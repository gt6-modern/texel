"""独立的 PXL 渲染 oracle(测试专用)。

**刻意不与 `importer` 共享任何代码**:字符表、十六进制解析、矩形裁剪都按
`spec/PXL.md` 另写一遍。它承担两件事:

1. 判定"压缩版与逐像素版渲染结果逐像素相同"(AGENTS.md §3.3);
2. 判定 `expected.pxl` 回渲后等于 `input.png` 的像素(§13.2a 判据 2/3)。

只支持**单图层**与 `blend: "normal"`:单图层 `normal` 按 §6.7 第 1 条即恒等,
本任务(抽取器只产单图层 normal 文档)用不到 §6.3–§6.5 的合成公式。遇到多图层或
其它 blend 直接报错——那属于 `texel-compiler` 的职责,不该在这里被"顺便"实现。
"""

import json

#: §4.2 的 61 字符表,独立抄写,不 import importer.tokens。
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy"
WIDTHS = {"char": 1, "index": 2, "index4": 4}
CLEAR = {"char": ".", "index": "00", "index4": "0000"}
CLEAR_PIXEL = b"\x00\x00\x00\x00"


def render_pxl_text(text):
    """把 PXL 文本渲染成 ``(width, height, RGBA8 字节流)``。"""
    doc = json.loads(text)
    width = int(doc["width"])
    height = int(doc["height"])
    encoding = doc["encoding"]
    palette = [bytes.fromhex(entry) for entry in doc["palette"]]

    layers = doc["layers"]
    if len(layers) != 1:
        raise ValueError("oracle 只支持单图层,收到 %d 个" % len(layers))
    layer = layers[0]
    if layer.get("blend", "normal") != "normal":
        raise ValueError("oracle 只支持 blend=normal,收到 %r" % (layer.get("blend"),))

    buffer = bytearray(width * height * 4)
    for op in layer["ops"]:
        _apply(buffer, width, height, encoding, palette, op)
    return width, height, bytes(buffer)


def _slots_of(encoding, row):
    """把一行 token 串切成分列 slot。"""
    step = WIDTHS.get(encoding)
    if step is None:
        raise ValueError("未知 encoding: %r" % (encoding,))
    if not row or len(row) % step != 0:
        raise ValueError("行字符数 %d 不是 token 宽度的正整数倍" % len(row))
    slots = []
    for start in range(0, len(row), step):
        token = row[start : start + step]
        if token == CLEAR[encoding]:
            slots.append(0)
        elif encoding == "char":
            position = CHARS.find(token)
            if position < 0:
                raise ValueError("char 档非法 token: %r" % (token,))
            slots.append(position + 1)
        else:
            slots.append(int(token, 16))
    return slots


def _pixel_of(palette, slot):
    if slot == 0:
        return CLEAR_PIXEL
    if slot > len(palette):
        raise ValueError("slot %d 越过 palette 长度 %d" % (slot, len(palette)))
    return palette[slot - 1]


def _write(buffer, width, height, x, y, pixel):
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 4
        buffer[offset : offset + 4] = pixel


def _fill_rect(buffer, width, height, x, y, w, h, pixel):
    """右开、下开的矩形,越界一律裁剪(§5.0)。"""
    for py in range(y, y + h):
        if py < 0 or py >= height:
            continue
        for px in range(x, x + w):
            if 0 <= px < width:
                offset = (py * width + px) * 4
                buffer[offset : offset + 4] = pixel


def _apply(buffer, width, height, encoding, palette, op):
    kind = op["op"]
    if kind == "fill":
        _fill_rect(
            buffer, width, height, 0, 0, width, height, _pixel_of(palette, op["slot"])
        )
    elif kind == "rect":
        _fill_rect(
            buffer,
            width,
            height,
            op["x"],
            op["y"],
            op["w"],
            op["h"],
            _pixel_of(palette, op["slot"]),
        )
    elif kind == "pixel":
        _write(
            buffer, width, height, op["x"], op["y"], _pixel_of(palette, op["slot"])
        )
    elif kind == "erase":
        _fill_rect(buffer, width, height, op["x"], op["y"], op["w"], op["h"], CLEAR_PIXEL)
    elif kind == "grid":
        origin_x = op["x"]
        origin_y = op["y"]
        for offset_y, row in enumerate(op["grid"]):
            for offset_x, slot in enumerate(_slots_of(encoding, row)):
                _write(
                    buffer,
                    width,
                    height,
                    origin_x + offset_x,
                    origin_y + offset_y,
                    _pixel_of(palette, slot),
                )
    else:
        raise ValueError("oracle 不支持的 op: %r" % (kind,))
