"""独立的附录 A 抽取器(测试专用 oracle)。

**为什么需要它:** ``expected.pxl`` 是由被测抽取器自己产出的("黄金自证")。解码侧
已有两道独立锚点(``png_ref`` 独立解码、``expected.pixels.sha256`` 由意图像素直算),
但"抽取策略 + 规范形式的排版"这两步此前只有实现与它自己的手写测试互证。本模块按
`spec/PXL.md` 的 §4.4 / §4.5 / §3.7 / 附录 A **另写一遍**,用一个完全不同的
数据表示(元组序列,不是 dict),再与黄金文本逐字节比对。

刻意不与 `importer` 共享任何代码,也不 import 它。
"""

#: §4.2:`char` 档 slot 1..61 的字符表。
CHAR_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy"

#: 附录 A 第 3 步:段长度阈值。
MIN_RUN = 3

#: 附录 A 第 8 步:每层 op 数上限。
OPS_CAP = 32768

CLEAR = b"\x00\x00\x00\x00"


# ---------------------------------------------------------------- token 与档位

def _token(encoding, slot):
    if slot == 0:
        return {"char": ".", "index": "00", "index4": "0000"}[encoding]
    if encoding == "char":
        return CHAR_ALPHABET[slot - 1]
    if encoding == "index":
        return "%02X" % slot
    return "%04X" % slot


def _encoding_for(count):
    """§4.5:档位由 palette 长度唯一决定。"""
    if count <= 61:
        return "char"
    if count <= 255:
        return "index"
    if count <= 65535:
        return "index4"
    raise ValueError("非透明色数 %d 超过 index4 容量 65535" % count)


# ---------------------------------------------------------------- §4.4 调色板

def reference_slots(rgba, width, height):
    """返回 ``(slots, palette)``:§4.4 的行优先首次出现顺序。"""
    slots = []
    palette = []
    order = {}
    for index in range(width * height):
        pixel = bytes(rgba[index * 4:index * 4 + 4])
        if pixel == CLEAR:          # §7:只有四通道全零是透明 slot
            slots.append(0)
            continue
        slot = order.get(pixel)
        if slot is None:
            slot = len(palette) + 1
            order[pixel] = slot
            palette.append(pixel.hex().upper())
        slots.append(slot)
    return slots, palette


# ---------------------------------------------------------------- 附录 A

def _segments(row):
    """行内极大的同色连续区间,``[(x, w, slot), ...]``。"""
    segments = []
    start = 0
    current = row[0]
    for x in range(1, len(row)):
        if row[x] != current:
            segments.append((start, x - start, current))
            start = x
            current = row[x]
    segments.append((start, len(row) - start, current))
    return segments


def _blocks(kinds):
    blocks = []
    start = 0
    current = kinds[0]
    for y in range(1, len(kinds)):
        if kinds[y] != current:
            blocks.append((current, start, y - 1))
            start = y
            current = kinds[y]
    blocks.append((current, start, len(kinds) - 1))
    return blocks


def _grid_of(rows, y0, y1, encoding):
    lines = []
    for y in range(y0, y1 + 1):
        parts = []
        for _x, width, slot in _segments(rows[y]):
            parts.append(_token(encoding, slot) * width)
        lines.append("".join(parts))
    return ("grid", 0, y0, tuple(lines))


def reference_ops(slots, width, height, encoding):
    """附录 A 的 op 序列,用元组表示(与实现的 dict 形状不同,便于交叉核对)。

    元组形状:``("fill", slot)`` / ``("rect", x, y, w, h, slot)`` /
    ``("grid", x, y, (row, ...))``。
    """
    if all(slot == slots[0] for slot in slots):
        return [("fill", slots[0])]

    rows = [list(slots[y * width:(y + 1) * width]) for y in range(height)]
    kinds = []
    for row in rows:
        runner = True
        for _x, run, _slot in _segments(row):
            if run < MIN_RUN:
                runner = False
                break
        kinds.append(runner)

    ops = []
    for runner, y0, y1 in _blocks(kinds):
        if not runner:
            ops.append(_grid_of(rows, y0, y1, encoding))
            if len(ops) > OPS_CAP:
                return _raw_ops(rows, height, encoding)
            continue

        previous = {}
        for y in range(y0, y1 + 1):
            current = {}
            for x, run, slot in _segments(rows[y]):
                key = (x, run, slot)
                if key in previous:
                    position = previous[key]
                    _op, ox, oy, ow, oh, oslot = ops[position]
                    ops[position] = ("rect", ox, oy, ow, oh + 1, oslot)
                    current[key] = position
                else:
                    ops.append(("rect", x, y, run, 1, slot))
                    current[key] = len(ops) - 1
            previous = current
        if len(ops) > OPS_CAP:
            return _raw_ops(rows, height, encoding)
    return ops


def _raw_ops(rows, height, encoding):
    return [_grid_of(rows, 0, height - 1, encoding)]


# ---------------------------------------------------------------- §3.7 序列化

def _render(width, height, encoding, palette, ops):
    lines = ["{",
             '  "format": "PXL",',
             '  "version": 1,',
             '  "encoding": "%s",' % encoding,
             '  "width": %d,' % width,
             '  "height": %d,' % height]
    if palette:
        lines.append('  "palette": [')
        for index, entry in enumerate(palette):
            comma = "" if index == len(palette) - 1 else ","
            lines.append('    "%s"%s' % (entry, comma))
        lines.append("  ],")
    else:
        lines.append('  "palette": [],')
    lines.append('  "layers": [')
    lines.append("    {")
    lines.append('      "name": "base",')
    lines.append('      "blend": "normal",')
    if ops:
        lines.append('      "ops": [')
        for index, op in enumerate(ops):
            lines.extend(_op_lines(op, index == len(ops) - 1))
        lines.append("      ]")
    else:
        lines.append('      "ops": []')
    lines.append("    }")
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _op_lines(op, is_last):
    """op 对象:`{` 与成员在数组元素缩进(8),成员再 +2,嵌套数组行 +4。"""
    tail = "" if is_last else ","
    lines = ["        {"]
    if op[0] == "fill":
        lines.append('          "op": "fill",')
        lines.append('          "slot": %d' % op[1])
    elif op[0] == "rect":
        _kind, x, y, w, h, slot = op
        lines.append('          "op": "rect",')
        lines.append('          "x": %d,' % x)
        lines.append('          "y": %d,' % y)
        lines.append('          "w": %d,' % w)
        lines.append('          "h": %d,' % h)
        lines.append('          "slot": %d' % slot)
    else:
        _kind, x, y, rows = op
        lines.append('          "op": "grid",')
        lines.append('          "x": %d,' % x)
        lines.append('          "y": %d,' % y)
        lines.append('          "grid": [')
        for index, row in enumerate(rows):
            comma = "" if index == len(rows) - 1 else ","
            lines.append('            "%s"%s' % (row, comma))
        lines.append("          ]")
    lines.append("        }" + tail)
    return lines


# ---------------------------------------------------------------- 入口

def reference_pxl(rgba, width, height):
    """从 RGBA8 像素直接产出规范形式 PXL 全文(含 §4.4/§4.5/附录 A/§3.7)。"""
    slots, palette = reference_slots(rgba, width, height)
    encoding = _encoding_for(len(palette))
    ops = reference_ops(slots, width, height, encoding)
    return _render(width, height, encoding, palette, ops)
