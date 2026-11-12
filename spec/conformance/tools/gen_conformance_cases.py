#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补齐 `CASES.tsv` 里缺的 op- / blend- / negative/ 用例目录
(real- 由 `gen_real_cases.py` 负责,因为它们的输入来自 `samples/`)。

具体条数以本脚本 `--check` 打印的「正例 N / 负例 N」为准,不要在这里写死——
曾经写死过,119 / 129 / 120 三个数字互相矛盾,而没人跑得动它去对账。

用法::

    <py> spec/conformance/tools/gen_conformance_cases.py            # 写出全部应生成的用例
    <py> spec/conformance/tools/gen_conformance_cases.py --check    # 只比对,不写文件
    <py> spec/conformance/tools/gen_conformance_cases.py op-pixel   # 只写一条

约定:

* 幂等、确定性:无时钟、无随机、无 locale、无目录枚举顺序(§9);同一输入任意次运行
  输出**逐字节**相同(所有文件都以 ``newline=""`` 的二进制方式写出,只用 LF)。
* `op-*` / `blend-*` 的 `expected.pixels.sha256` 由 `tools/pxl_render_ref.py`
  (**按规范另写的渲染 oracle**)对 `input.pxl` 渲染得出——不由被测实现、也不由
  `tests/` 里的任何模块计算。
* `negative/*` 只有 `input.pxl`(渲染侧)或 `input.png`(抽取侧)+ `expected.error`
  + `notes.md`,**绝不放** `expected.pxl`(§13.1);每个目录只放一个缺陷
  (`neg-priority-*` 是刻意的阶段序用例,例外)。
* 本文件只写 `spec/conformance/cases/{op-*,blend-*,negative/*}` 下的文件,不碰别的路径。
"""

import binascii
import json
import struct
import sys
import zlib
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import pxl_render_ref  # noqa: E402  独立渲染 oracle(用途见上)

CASES_DIR = TOOLS_DIR.parent / "cases"


# --------------------------------------------------------------------------
# 规范形式序列化(§3.7 的子集:只覆盖本文件写出的 op)
# --------------------------------------------------------------------------

def _scalar(value):
    """把一个 JSON 标量写成规范形式。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    raise TypeError("不支持的标量: %r" % (value,))


def _emit_member(lines, indent, key, value):
    """按 §3.7 写一个成员:标量同行,对象/数组的分隔符位置按上下文决定。"""
    pad = " " * indent
    if isinstance(value, dict):
        lines.append('%s"%s": {' % (pad, key))
        items = list(value.items())
        for index, (sub_key, sub_value) in enumerate(items):
            _emit_member(lines, indent + 2, sub_key, sub_value)
            if index != len(items) - 1:
                lines[-1] += ","
        lines.append("%s}" % pad)
    elif isinstance(value, list):
        if not value:
            lines.append('%s"%s": []' % (pad, key))
            return
        lines.append('%s"%s": [' % (pad, key))
        for index, item in enumerate(value):
            if isinstance(item, dict):
                _emit_value(lines, indent + 2, item)
            else:
                lines.append("%s%s" % (" " * (indent + 2), _scalar(item)))
            if index != len(value) - 1:
                lines[-1] += ","
        lines.append("%s]" % pad)
    else:
        lines.append('%s"%s": %s' % (pad, key, _scalar(value)))


def _emit_value(lines, indent, value):
    """数组元素:对象写 `{` 独占一行,标量直接写。"""
    pad = " " * indent
    if isinstance(value, dict):
        lines.append("%s{" % pad)
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            _emit_member(lines, indent + 2, key, item)
            if index != len(items) - 1:
                lines[-1] += ","
        lines.append("%s}" % pad)
    elif isinstance(value, str):
        lines.append("%s%s" % (pad, _scalar(value)))
    else:
        raise TypeError("数组元素类型不支持: %r" % (value,))


def serialize(width, height, encoding, palette, layers, sprites=None,
              version=1, format_value="PXL", top_extra=None):
    """按 §3.7 的成员顺序与花括号规则写出一份规范形式的 PXL 文档。"""
    lines = ["{", '  "format": %s,' % _scalar(format_value)]
    lines.append('  "version": %s,' % _scalar(version))
    lines.append('  "encoding": %s,' % _scalar(encoding))
    lines.append('  "width": %s,' % _scalar(width))
    lines.append('  "height": %s,' % _scalar(height))
    _emit_member(lines, 2, "palette", list(palette))
    lines[-1] += ","
    if sprites:
        _emit_member(lines, 2, "sprites", sprites)
        lines[-1] += ","
    for key, value in (top_extra or ()):
        _emit_member(lines, 2, key, value)
        lines[-1] += ","
    # layers 一定是最后一个成员
    lines.append('  "layers": [')
    for index, layer in enumerate(layers):
        _emit_value(lines, 4, layer)
        if index != len(layers) - 1:
            lines[-1] += ","
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def op(name, *members):
    """按给定顺序构造一个 op 对象(成员顺序由调用点决定,便于 control 缺陷)。"""
    pairs = [("op", name)]
    pairs.extend(members)
    return dict(pairs)


def layer(name="base", blend="normal", ops=()):
    return {"name": name, "blend": blend, "ops": list(ops)}


def fill(slot):
    return op("fill", ("slot", slot))


def rect(x, y, w, h, slot):
    return op("rect", ("x", x), ("y", y), ("w", w), ("h", h), ("slot", slot))


def pixel(x, y, slot):
    return op("pixel", ("x", x), ("y", y), ("slot", slot))


def line(x0, y0, x1, y1, slot):
    return op("line", ("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1),
              ("slot", slot))


def grid(x, y, rows):
    return op("grid", ("x", x), ("y", y), ("grid", list(rows)))


def mirror(from_box, to_point, flip_x=False, flip_y=False):
    members = [("from", {"x": from_box[0], "y": from_box[1],
                         "w": from_box[2], "h": from_box[3]}),
               ("to", {"x": to_point[0], "y": to_point[1]})]
    if flip_x:
        members.append(("flipX", True))
    if flip_y:
        members.append(("flipY", True))
    return op("mirror", *members)


def erase(x, y, w, h):
    return op("erase", ("x", x), ("y", y), ("w", w), ("h", h))


def stamp(name, x, y, flip_x=False, flip_y=False):
    members = [("sprite", name), ("x", x), ("y", y)]
    if flip_x:
        members.append(("flipX", True))
    if flip_y:
        members.append(("flipY", True))
    return op("stamp", *members)


def tier_palette(count):
    """``count`` 个互不相同的非透明色(确定性配方,便于造档位边界 fixture)。"""
    items = []
    for index in range(count):
        items.append("%02X%02X%02XFF"
                     % (index & 0xFF, (index >> 8) & 0xFF, (index * 7 + 3) & 0xFF))
    if len(set(items)) != count:
        raise AssertionError("tier_palette(%d) 出现了重复颜色" % count)
    return items


def encoding_for(count):
    return pxl_render_ref._derive_encoding(count)


# --------------------------------------------------------------------------
# 用例注册表
# --------------------------------------------------------------------------

CASES = []


def render_case(case, clauses, derive, text, extra_notes=""):
    CASES.append({"case": case, "kind": "render", "clauses": clauses,
                  "derive": derive, "text": text, "extra": extra_notes})


def negative_render(case, code, clauses, reason, text):
    CASES.append({"case": case, "kind": "negative-render", "code": code,
                  "clauses": clauses, "reason": reason, "text": text})


def negative_extract(case, code, clauses, reason, data):
    CASES.append({"case": case, "kind": "negative-extract", "code": code,
                  "clauses": clauses, "reason": reason, "png": data})


# ==========================================================================
# 一、op-*(9 条仅渲染正例,§13.2b)
# ==========================================================================

def build_op_cases():
    # ---- op-pixel(§5.3)----
    ops = [
        pixel(0, 0, 1),
        pixel(0, 0, 2),        # 同一格被后继 pixel 覆盖
        pixel(15, 15, 1),
        pixel(-1, 5, 2),       # 越界 → 空操作
        pixel(16, 5, 2),
        pixel(5, -1, 2),
        pixel(5, 16, 2),
    ]
    render_case(
        "op-pixel",
        "`§5.3`(`pixel` 只写一个像素,等价于 `rect(x,y,1,1)`)、`§5.0`"
        "(越界一律裁剪、不报错)、`§5.0`(后写的 op 整体替换同格 texel 值)`。",
        "7 条 `pixel` 依序执行:第 2 条把 `(0,0)` 从 slot 1 覆盖成 slot 2;"
        "末尾 4 条落在画布之外(或负坐标)被丢弃,不产生任何像素。"
        "最终 `(0,0)` = slot 2 = `00FF00FF`、`(15,15)` = slot 1 = `FF0000FF`,"
        "其余 254 个像素仍是图层的初始透明 `00000000`。",
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF"],
                  [layer(ops=ops)]))

    # ---- op-line-bresenham(§5.4)----
    ops = [
        line(7, 7, 7, 0, 1),      # 上
        line(7, 7, 15, 7, 1),     # 右
        line(7, 7, 7, 15, 1),     # 下
        line(7, 7, 0, 7, 1),      # 左
        line(7, 7, 15, 0, 1),     # 右上
        line(7, 7, 15, 15, 1),    # 右下
        line(7, 7, 0, 15, 1),     # 左下
        line(7, 7, 0, 0, 1),      # 左上
        line(2, 6, 10, 5, 1),     # 非 45 度斜率:正向
        line(10, 5, 2, 6, 2),     # 同一对端点反向:点集不同(§5.4 明写不归一)
        line(1, 14, 1, 14, 2),    # 退化:只写 1 个像素
        line(4, 12, 11, 12, 1),   # 水平线不得有锯齿偏移
    ]
    forward = list(pxl_render_ref.line_points(2, 6, 10, 5))
    backward = list(pxl_render_ref.line_points(10, 5, 2, 6))
    only_forward = sorted(set(forward) - set(backward))
    only_backward = sorted(set(backward) - set(forward))
    render_case(
        "op-line-bresenham",
        "`§5.4`(逐字符固定的 Bresenham,含两端点)、`§5.4`(两个 `若` 各自独立判断,"
        "同一轮可能同时走 x 与 y)、`§5.4`(**不做端点交换归一**:`line(a,b)` 与 "
        "`line(b,a)` 的像素集可以不同)、`§5.4`(退化单点、水平线无锯齿)、`§5.0`(逐点裁剪)。",
        "八条从 `(7,7)` 出发的单向线覆盖八个八分圆方向;第 9、10 条是**同一对端点"
        "按相反方向**画出的两条线(正向 slot 1、反向 slot 2),用来钉住 §5.4 明确禁止的"
        "「交换端点归一」。按本文件 oracle 的逐点计算:\n\n"
        "```\n正向 (2,6)->(10,5) 共 %d 点:%s\n反向 (10,5)->(2,6) 共 %d 点:%s\n"
        "只在正向出现:%s\n只在反向出现:%s\n```\n\n"
        "两者点数相同(%d = %d)但点集不同,所以把两条都画出来之后,两个方向的差异"
        "各自留下自己的 slot 颜色。第 11 条是退化单点(`x0==x1 && y0==y1`),"
        "第 12 条是水平线(y 恒 12,无锯齿偏移)。"
        % (len(forward), forward, len(backward), backward,
           only_forward, only_backward, len(forward), len(backward)),
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF"],
                  [layer(ops=ops)]))

    # ---- op-mirror-overlap(§5.6)----
    ops = [
        rect(0, 0, 8, 4, 1),
        rect(0, 1, 4, 1, 2),         # 制造上下/左右都不对称的内容
        mirror((0, 0, 8, 4), (2, 0)),             # 与源区重叠:快照语义
        mirror((0, 0, 8, 4), (8, 0), flip_x=True),
        mirror((0, 0, 8, 4), (0, 4), flip_y=True),
        mirror((0, 0, 8, 4), (8, 4), flip_x=True, flip_y=True),
        rect(0, 12, 4, 4, 1),
        mirror((-4, 12, 8, 4), (8, 12)),          # 读出侧越界:源按透明 slot 0
    ]
    render_case(
        "op-mirror-overlap",
        "`§5.6`(快照语义:源区必须在任何写入发生前整体读出)、`§5.6`(`flipX` 水平、"
        "`flipY` 垂直,两者同真等于 180 度旋转)、`§5.6`(**读取侧不裁剪**,越界源像素按"
        "透明 slot 0 参与快照)、`§5.0`(写入侧裁剪)。",
        "第 1–2 条在 `0..7 × 0..3` 画出一块上下、左右都不对称的内容"
        "(`rect(0,1,4,1)` 的 slot 2 横条)。第 3 条 `mirror from (0,0,8,4) to (2,0)`:"
        "目标区 `2..9 × 0..3` 与源区重叠,若实现按「边读边写」就会读到刚写下的内容;"
        "按 §5.6 必须先整体快照,结果唯一。第 4–6 条分别是 flipX、flipY、两者同真,"
        "各自的源都是**当时的**图层内容(前一条 mirror 的输出也算在内,因为快照只在"
        "本条 op 内成立)。第 8 条 `from.x = -4`:左半 4 列落在图层之外,按透明 slot 0 "
        "参与,于是目标 `8..11` 列被写成 `00000000`,而 `12..15` 列拿到源 `0..3` 列"
        "(slot 1)——这条钉的正是「越界源全零」,不是「越界源跳过」。",
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF"],
                  [layer(ops=ops)]))

    # ---- op-stamp-flip(§5.8)----
    sprites = {
        # token `0` = slot 1、`1` = slot 2、`2` = slot 3(§4.2 的字符集从 `0` 起)
        "lshape": ["0..", "0..", "011"],
        "unused": ["222", "222"],
    }
    ops = [
        stamp("lshape", 0, 0),
        stamp("lshape", 4, 0, flip_x=True),
        stamp("lshape", 8, 0, flip_y=True),
        stamp("lshape", 12, 0, flip_x=True, flip_y=True),
        stamp("lshape", -1, 6),                    # 逐点裁剪
        stamp("lshape", 14, 6),                    # 右侧裁剪
    ]
    render_case(
        "op-stamp-flip",
        "`§5.8`(`stamp` 把命名 sprite 写到 `(x,y)`,先 X 后 Y 翻转,两者同真等于 180 度"
        "旋转)、`§3.7`(`sprites` 成员按名字的 ASCII 字节序排列)、`§5.8`"
        "(定义但从未被引用的 sprite 对输出没有影响)、`§5.0`(逐点裁剪)。",
        "`sprites` 有两条:`lshape`(3×3,含 slot 1 与 slot 2,左右上下都不对称,"
        "所以三种翻转互不相同)与 `unused`(从未被 `stamp` 引用,按 §5.8 必须对输出"
        "零影响——它若被误画进来,摘要必然不同)。四个 `stamp` 分别是原样、flipX、"
        "flipY、flipX+flipY;后两条放在 x=-1 与 x=14 处,验证逐点裁剪不报错。"
        "按 §3.7,`sprites` 的成员顺序是 `lshape`、`unused`(ASCII 字节序)。",
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF", "0000FFFF"],
                  [layer(ops=ops)], sprites=sprites))

    # ---- op-erase-clip(§5.7)----
    ops = [
        fill(1),
        erase(2, 2, 4, 4),
        rect(0, 8, 16, 8, 2),
        erase(-4, 10, 8, 3),        # 负坐标:只清 x 0..3
        erase(12, 14, 100, 100),    # 超右下:只清到画布边缘
        erase(0, 0, 0, 4),          # w = 0 → 空操作
        erase(0, 5, 4, 0),          # h = 0 → 空操作
        erase(16, 0, 4, 4),         # 完全越界 → 空操作
        erase(-100, -100, 10, 10),  # 完全越界 → 空操作
    ]
    render_case(
        "op-erase-clip",
        "`§5.7`(`erase` 把本层矩形清成透明 slot 0,等价于 `rect(..., slot=0)`)、"
        "`§5.0`(矩形右开下开、`w`/`h` 为 0 等于空操作、越界一律裁剪不报错)。",
        "先 `fill slot 1` 铺满,再挖三个洞、清一条带:第 2 条清 `2..5 × 2..5`;"
        "第 3 条把下半 8 行刷成 slot 2;第 4 条负坐标 `x=-4,w=8` 只覆盖 `x 0..3`;"
        "第 5 条 `x=12,w=100` 只覆盖 `x 12..15`、`y 14..15`;第 6–9 条分别因 `w=0`、"
        "`h=0`、完全越界而是空操作(它们一条都不改像素,正是「不得因越界报错」的部分)。",
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF"],
                  [layer(ops=ops)]))

    # ---- op-coord-extreme(§5.0.1)----
    ops = [
        rect(-65536, 0, 65536, 16, 1),       # 右边界恰好 0:一个像素都不覆盖
        rect(0, -65536, 16, 65536, 1),
        rect(0, 0, 65536, 65536, 1),
        line(-65536, -65536, 65536, 65536, 2),
        line(65536, 0, 0, 65536, 2),
        pixel(-65536, -65536, 2),
        pixel(65536, 65536, 2),
    ]
    render_case(
        "op-coord-extreme",
        "`§5.0.1`(坐标取值域 `-65536..65536`、`w`/`h` 取值域 `0..65536`)、"
        "`§5.0.1`(终止性:步进轮数不超过 `|dx| + |dy| + 1 <= 262145`)、"
        "`§5.0`(逐点裁剪,不得先做解析裁剪)。",
        "第 1 条 `x=-65536,w=65536` 的右边界恰为 0,覆盖集为空(不是「夹到边界」);"
        "第 2 条同理;第 3 条 `(0,0,65536,65536)` 裁剪后等于整张 16×16 铺满 slot 1。"
        "第 4、5 条是从极端端点出发的两条线:按 §5.4 的逐点算法,每轮步进 1,"
        "`|dx|+|dy|+1 = 262145` 轮后必然终止,其中落在画布内的点被写入、其余丢弃;"
        "由于 `dx == |dy|`,两条线在画布内都是对角步进。第 6、7 条落在四角之外,是空操作。",
        serialize(16, 16, "char", ["FF0000FF", "00FF00FF"],
                  [layer(ops=ops)]))

    # ---- op-numeric-literal-forms(§3.8)----
    text = (
        '{\n'
        '  "format": "PXL",\n'
        '  "version": 1,\n'
        '  "encoding": "char",\n'
        '  "width": 2.0,\n'
        '  "height": 2.0,\n'
        '  "palette": [\n'
        '    "FF0000FF",\n'
        '    "00FF00FF"\n'
        '  ],\n'
        '  "layers": [\n'
        '    {\n'
        '      "name": "base",\n'
        '      "blend": "normal",\n'
        '      "ops": [\n'
        '        {\n'
        '          "op": "rect",\n'
        '          "x": 0.0,\n'
        '          "y": 0.0,\n'
        '          "w": 2.0,\n'
        '          "h": 1.0,\n'
        '          "slot": 1.0\n'
        '        },\n'
        '        {\n'
        '          "op": "rect",\n'
        '          "x": 0.0,\n'
        '          "y": 1.0,\n'
        '          "w": 2.0,\n'
        '          "h": 1.0,\n'
        '          "slot": 2.0\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        '}\n')
    render_case(
        "op-numeric-literal-forms",
        "`§3.8`(`width` / `height` / `x` / `y` / `w` / `h` / `slot` 写成等值的小数字面量"
        "必须被接受)、`§3.8`(整数值判定必须按原始词法精确做,不得先转二进制浮点)。",
        "全部整数成员都写成 `<n>.0` 形式:`width`/`height` 是 `2.0`,`rect` 的 `x`/`y`/`w`/`h` "
        "与 `slot` 也都是 `n.0`。按 §3.8 它们分别等于 2 与 1 等整数值,渲染器必须接受:"
        "上半天是 slot 1(`FF0000FF`)、下半天是 slot 2(`00FF00FF`)。"
        "与 `negative/neg-width-not-integer-precise` 成对:那一例的 `1.0000000000000001` "
        "按精确十进制**不等于** 1,必须是 `PXL002`。",
        text)

    # ---- op-lenient-bom-crlf(§3.8)----
    body = (
        '{\n'
        '  "format": "PXL",\n'
        '  "version": 1,\n'
        '  "encoding": "char",\n'
        '  "width": 2,\n'
        '  "height": 2,\n'
        '  "palette": [\n'
        '    "FF0000FF",\n'
        '    "00FF00FF"\n'
        '  ],\n'
        '  "layers": [\n'
        '    {\n'
        '      "name": "base",\n'
        '      "blend": "normal",\n'
        '      "ops": [\n'
        '        {\n'
        '          "op": "rect",\n'
        '          "x": 0,\n'
        '          "y": 0,\n'
        '          "w": 2,\n'
        '          "h": 1,\n'
        '          "slot": 1\n'
        '        },\n'
        '        {\n'
        '          "op": "rect",\n'
        '          "x": 0,\n'
        '          "y": 1,\n'
        '          "w": 2,\n'
        '          "h": 1,\n'
        '          "slot": 2\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        '}')
    # 全 CRLF、无结尾换行、带 UTF-8 BOM(字节层面在写文件时加)
    text = body.replace("\n", "\r\n")
    render_case(
        "op-lenient-bom-crlf",
        "`§3.8`(渲染器必须接受带 UTF-8 BOM 的文本)、`§3.8`(任意换行风格,含 CRLF)、"
        "`§3.8`(有无结尾换行均可)、`§9`(确定性)。",
        "这份 `input.pxl` **故意**不是规范形式:它以 UTF-8 BOM(`EF BB BF`)开头、"
        "所有换行都是 CRLF、结尾没有换行符。像素意图与 `op-numeric-literal-forms` "
        "同构(2×2,上半 slot 1、下半 slot 2),因此摘要可与那一例互相参照。"
        "只接受「无 BOM + LF + 有结尾换行」的渲染器必须在本用例失败(§3.8)。"
        "注意本文件由 `.gitattributes` 的 `-text -eol` 例外保护,否则 checkout 时 "
        "CRLF 会被翻成 LF、BOM 会被剥掉。",
        "\ufeff" + text)

    # ---- op-lenient-structure(§3.8)----
    text = (
        '{\n'
        '\t"layers": [\n'
        '        {\n'
        ' \t "ops": [\n'
        '                {\n'
        '                    "op": "fill",\n'
        '                    "slot": 1\n'
        '                },\n'
        '            {\n'
        '              "op": "pixel",\n'
        '              "x": 0.0,\n'
        '              "y": 0.0,\n'
        '              "slot": 2.0\n'
        '            },   \n'
        '            {\n'
        '              "op": "mirror",\n'
        '              "from": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},\n'
        '              "to": {"x": 1.0, "y": 1.0},\n'
        '              "flipX": false,\n'
        '              "flipY":   false\n'
        '            }\t\n'
        ' \t ],\n'
        '   "blend": "normal",\n'
        '\t "name": "base"\n'
        '        }\n'
        '    ],\n'
        '  "palette": ["FF0000FF", "00FF00FF"],\n'
        '  "height": 2.0,\n'
        '  "width":   2.0,\n'
        ' "encoding": "char",\n'
        '   "version": 1,\n'
        '"format": "PXL"\n'
        '}\n')
    render_case(
        "op-lenient-structure",
        "`§3.8`(任意缩进、任意行尾空白、任意成员顺序、冒号后多余空格全部必须接受)、"
        "`§3.8`(可选布尔成员写成 `false` 与整体省略等价)、`§3.8`(`2.0` 这类等值字面量"
        "表示整数)、`§3.7`(`flipX: false` 不是规范形式但仍然合法)。",
        "这份 `input.pxl` 把 §3.8 的接受集逐条用上:缩进用了 4 空格、2 个 tab、"
        "空格与 tab 混用三种;至少两行带**行尾空白**(第 `},   ` 行是空格,"
        "第 `},\\t` 行是 tab);冒号后有的是多个空格(`\"y\":   2.0` 那类);"
        "顶层与图层内的成员顺序都被打乱;`width` / `height` 写成 `2.0`;"
        "`mirror` 的 `flipX` / `flipY` 显式写 `false`。像素意图:`fill slot 1` 铺满 2×2,"
        "`pixel(0,0)` 改 slot 2,再把 `(0,0)` 镜到 `(1,1)`(flipX/flipY 皆 false,纯拷贝),"
        "最终 `[[2,1],[1,2]]`。只接受「4 空格缩进、拒绝 tab 与行尾空白」的解析器必须失败。",
        text)

    # ---- blend-*(14 条)----
    blend_cases()


def blend_cases():
    def two_layer(width, height, bottom_slot, top_ops, blend, palette,
                  bottom_blend="normal"):
        ops_bottom = [fill(bottom_slot)]
        return serialize(width, height, encoding_for(len(palette)), palette,
                         [layer("bottom", bottom_blend, ops_bottom),
                          layer("top", blend, top_ops)])

    # blend-normal-a128:§6.6(a) → 80007FFF
    palette = ["0000FFFF", "FF000080"]
    render_case(
        "blend-normal-a128",
        "`§6.6(a)`(`normal` 源 `FF000080` over `0000FFFF` 必须得 `80007FFF`)、"
        "`§6.3`(用**未取整**的 `aNum` 当分母)、`§6.7` 第 1 条(单图层 normal 恒等性"
        "由下层 `0000FFFF` 的自渲染保证)。",
        "2×2 全同像素。下层 `fill slot 1` = `0000FFFF`(不透明蓝,单层 normal 下恒等);"
        "上层 `blend: normal`、`fill slot 2` = `FF000080`。逐步算式(§6.6a):\n\n"
        "```\nsa=128  t=127  da=255\naNum = 128*255 + 255*127 = 65025\n"
        "ao   = (65025 + 127) / 255 = 255\nhalf = 65025 / 2 = 32512\n"
        "r: (255*128*255 +   0*255*127 + 32512) / 65025 = 8355712 / 65025 = 128\n"
        "g: (  0*128*255 +   0*255*127 + 32512) / 65025 =   32512 / 65025 =   0\n"
        "b: (  0*128*255 + 255*255*127 + 32512) / 65025 = 8290687 / 65025 = 127\n```\n\n"
        "结果 `80007FFF`,四个像素相同。注意 r + b = 255 不是「各半取整」,"
        "而是上面这条唯一确定的整数结果。",
        two_layer(2, 2, 1, [fill(2)], "normal", palette))

    # blend-multiply-a128:§6.6(b) → 0D2219FF
    palette = ["112233FF", "80FF0080"]
    render_case(
        "blend-multiply-a128",
        "`§6.6(b)`(`multiply` 源 `80FF0080` over `112233FF` 必须得 `0D2219FF`)、"
        "`§6.4`(先 `mc = div255(sc*dc)`,再用与 `normal` 完全相同的公式)。",
        "2×2 全同像素。下层 `112233FF`,上层 `blend: multiply`、`fill slot 2` = `80FF0080`。"
        "逐步算式(§6.6b):\n\n"
        "```\nmr = div255(128*17 = 2176) = 9\nmg = div255(255*34 = 8670) = 34\n"
        "mb = div255(  0*51 =    0) = 0\nsa=128  t=127  da=255  aNum=65025  ao=255  half=32512\n"
        "r: (  9*128*255 + 17*255*127 + 32512) / 65025 =  876817 / 65025 = 13\n"
        "g: ( 34*128*255 + 34*255*127 + 32512) / 65025 = 2243362 / 65025 = 34\n"
        "b: (  0*128*255 + 51*255*127 + 32512) / 65025 = 1684147 / 65025 = 25\n```\n\n"
        "结果 `0D2219FF`。",
        two_layer(2, 2, 1, [fill(2)], "multiply", palette))

    # blend-mask-a128:§6.6(c) 11223380 与 §6.6(d) 11223300
    palette = ["112233FF", "00FF0080", "00FF0000"]
    text = serialize(
        2, 2, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("mask128", "mask", [rect(0, 0, 2, 1, 2)]),
         layer("mask0", "mask", [rect(0, 1, 2, 1, 3)])])
    render_case(
        "blend-mask-a128",
        "`§6.6(c)`(`mask` 源 `00FF0080` over `112233FF` 得 `11223380`)、"
        "`§6.6(d)`(`mask` 源 `00FF0000` over `112233FF` 得 `11223300`,**不得**折叠成 "
        "`00000000`)、`§6.5`(`mask` 只乘 alpha,RGB 恒取下层)、`§6.7` 第 5 条。",
        "三层:底层 `fill slot 1` = `112233FF`;`mask128` 层只覆盖**上**半两格,"
        "源 `00FF0080`;`mask0` 层只覆盖**下**半两格,源 `00FF0000`。"
        "逐步算式:\n\n"
        "```\n上排: out = (0x11, 0x22, 0x33, div255(255*128 = 32640) = 128) = 11223380\n"
        "下排: out = (0x11, 0x22, 0x33, div255(255*0   =     0) =   0) = 11223300\n```\n\n"
        "下排的 `11223300` 是「RGB 非零而 A = 0」的**普通像素**(§7 第 2 条),"
        "不得被当作透明 slot 丢弃。\n\n"
        "**索引与实际的差异(如实记录)**:`CASES.tsv` 该行的 `colors` 列写 2,"
        "而本条要同时钉住 §6.6(c) 与 §6.6(d) 两个不同的源 alpha(128 与 0),"
        "因此 `palette` 实为 3 项(`112233FF` / `00FF0080` / `00FF0000`)。"
        "1×1 之外的任何做法都无法用 2 项同时表达这两个源;"
        "索引的 `colors` 列只对有 `input.png` 的往返正例做逐条交叉校验"
        "(`tests/test_conformance.py::test_index_columns_match_the_landed_cases`),"
        "故此处按实际生成并在 notes 里登记差异,不改索引。",
        text)

    # blend-bound-overflow:§6.6(g) → FFFFFF80(normal 与 multiply 各一例)
    palette = ["FFFFFF01", "FFFFFF80"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(2)]),
         layer("over-normal", "normal", [fill(1)]),
         layer("over-multiply", "multiply", [fill(1)])])
    render_case(
        "blend-bound-overflow",
        "`§6.6(g)`(源 `FFFFFF01` over `FFFFFF80` 必须得 `FFFFFF80`;先取整 `ao` 的旧写法"
        "会算出通道 **256**)、`§6.3`(通道上界证明依赖未取整的 `aNum`)、`§6.4`"
        "(`multiply` 的 `mc` 代入同一条证明)。",
        "1×1,三层:底层 `FFFFFF80`;`over-normal` 层 `FFFFFF01`;"
        "`over-multiply` 层 `FFFFFF01`(源色全 255,`mc = div255(255*255) = 255`,"
        "于是退化为与 `normal` 相同的算术)。逐步算式(§6.6g):\n\n"
        "```\nsa=1  t=254  da=128\naNum = 1*255 + 128*254 = 32767\nao   = (32767 + 127) / 255 = 128\n"
        "half = 16383\nr = (255*1*255 + 255*128*254 + 16383) / 32767 = 8371968 / 32767 = 255\n```\n\n"
        "结果 `FFFFFF80`。**注意**:这一例单独看不出钳位——旧公式把 256 钳到 255 "
        "恰好等于正确值;真正的钳位识别由 `blend-formula-818181-normal` / `-multiply` "
        "承担(那一对即使不越界也与正确值不同)。",
        text)

    # blend-mask-alters-alpha
    palette = ["445566FF", "11223300"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("mask", "mask", [fill(2)])])
    render_case(
        "blend-mask-alters-alpha",
        "`§6.7` 第 6 条(`mask` 必须改下层 alpha:`11223300` mask 到 `445566FF` 得 "
        "`44556600`)、`§6.5`(`mask` 只乘 alpha、RGB 取下层)、`§7` 第 3 条(`mask` 不得"
        "清除下层 RGB)。",
        "1×1,两层:底层 `fill slot 1` = `445566FF`;上层 `blend: mask`、`fill slot 2` = "
        "`11223300`(RGB 非零、A = 0 的普通像素,不是透明 slot)。算式:\n\n"
        "```\nout = (dr, dg, db, div255(da * sa)) = (0x44, 0x55, 0x66, div255(255*0) = 0)\n```\n\n"
        "结果 `44556600`:下层 alpha 被改掉了,RGB 原样保留。若把 `mask` 误实现成 "
        "`normal`,结果会是 `11223300`(源作为不可见颜色在下层为空时不适用;"
        "此处下层可见,`normal` 的 `aNum == 0` 分支要求保留下层 RGB 与 alpha,"
        "得到 `445566FF`),两种错法都与 `44556600` 不同,所以摘要能区分。\n\n"
        "§6.7 第 6 条里另半句「`mask` 作底层得 `00000000` 且不保留源 RGB」无法在本例"
        "同一个 1×1 摘要里同时钉住(最终值只能有一个);它在本文具的 "
        "`pxl_render_ref.py --selftest` 里作为公式级断言独立覆盖"
        "(`mask 11223300 over 00000000 → 00000000`)。",
        text)

    # blend-invisible-stack-upper-wins
    palette = ["FFFFFF00", "FF000000"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("top", "normal", [fill(2)])])
    render_case(
        "blend-invisible-stack-upper-wins",
        "`§7` 第 3 条(两层都是「RGB 非零而 A = 0」时**取上层**)、`§6.3`"
        "(`aNum == 0` 分支:源不是透明 slot → 保留 `sr/sg/sb`)、`§6.6(f)`。",
        "1×1,两层都是不可见颜色:下层 `FFFFFF00`、上层 `FF000000`。"
        "`sa = 0`、`da = 0` → `aNum = 0`,源不是透明 slot,故走"
        "「保留上层 RGB」分支:\n\n"
        "```\nout = (sr, sg, sb, 0) = (0xFF, 0x00, 0x00, 0) = FF000000\n```\n\n"
        "结果 `FF000000`,**不是** `FFFFFF00`。这条钉住 §7 的散文与公式不矛盾:"
        "「有颜色者胜;两者都有颜色时上层胜」。",
        text)

    # blend-invisible-stack-transparent-top
    palette = ["FFFFFF00"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("top", "normal", [fill(0)])])
    render_case(
        "blend-invisible-stack-transparent-top",
        "`§6.6(e)`(透明 slot 不吞掉下层的不可见颜色)、`§7` 第 3 条"
        "(上层是透明 slot → 保留**下层** RGB)、`§6.3` 的 `aNum == 0` 分支。",
        "1×1,下层 `FFFFFF00`,上层是显式 `fill slot 0`(即透明 slot `00000000`)。"
        "`aNum = 0 + div255(0*255) = 0`,源是透明 slot,故保留下层 RGB:\n\n"
        "```\nout = (dr, dg, db, 0) = (0xFF, 0xFF, 0xFF, 0) = FFFFFF00\n```\n\n"
        "结果 `FFFFFF00`。与 `blend-invisible-stack-upper-wins` 配对,"
        "钉住「有颜色者胜」的两个分支。",
        text)

    # blend-transparent-identity-{normal,multiply,mask}
    for blend_name, clause in (
        ("normal", "`§6.3` 的透明源分支(`t = 255 - 0`,通道恒等)"),
        ("multiply", "`§6.4` 的透明源分支(`mc = div255(0*dc) = 0`,与 `normal` 同构)"),
        ("mask", "`§6.5` 的第一个分支(`源是透明 slot` → 直接返回下层四通道)"),
    ):
        case = "blend-transparent-identity-%s" % blend_name
        palette = ["FFFFFF00"]
        text = serialize(
            1, 1, "char", palette,
            [layer("bottom", "normal", [fill(1)]),
             layer("top", blend_name, [fill(0)])])
        render_case(
            case,
            "`§6.7` 第 2 条(透明 slot 像素在三种 `blend` 下都**完全恒等**,"
            "下层四通道一个字节都不变)、%s。" % clause,
            "1×1,下层 `FFFFFF00`,上层 `blend: %s` 且内容为透明 slot。"
            "把 `(sr,sg,sb,sa) = (0,0,0,0)` 代入 §6.3 / §6.4 的 `aNum == 0` 分支或 "
            "§6.5 的第一个分支,三者都直接返回 `(dr,dg,db,da)`:\n\n"
            "```\nout = FFFFFF00\n```\n\n"
            "结果恒为 `FFFFFF00`(下层像素逐字节不变)。" % blend_name,
            text)

    # blend-normal-opaque-wins
    palette = ["11223380", "FF0000FF"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("top", "normal", [fill(2)])])
    render_case(
        "blend-normal-opaque-wins",
        "`§6.7` 第 3 条(`normal` 下 `sa = 255` 时结果恒等于上层源值且 `ao = 255`)、`§6.3`。",
        "1×1,下层 `11223380`(alpha 非 255,确保不是「下层本来就被覆盖」的巧合),"
        "上层 `FF0000FF`。`sa = 255` → `t = 0`、`aNum = 255*255 = 65025`、`ao = 255`:\n\n"
        "```\nr = (255*255*255 + 0 + 32512) / 65025 = 16613887 / 65025 = 255\n"
        "g = (0*...    + 0 + 32512) / 65025 = 0\nb = 0\n```\n\n"
        "结果 `FF0000FF`,与上层源值逐字节相同。",
        text)

    # blend-multiply-bottom-black
    palette = ["FF0000FF", "000000FF"]
    text = serialize(
        1, 1, "char", palette,
        [layer("multiply-base", "multiply", [fill(1)]),
         layer("alpha-veil", "mask", [fill(2)])])
    render_case(
        "blend-multiply-bottom-black",
        "`§6.7` 第 4 条(`multiply` 不是「覆盖」:下层为空时任何不透明源都乘成黑)、`§6.4`。",
        "1×1。第一层就是 `blend: multiply` 的底层(下层是空累加器,`dc = 0`):\n\n"
        "```\nmc = div255(255*0) = 0\naNum = 255*255 + 0*0 = 65025   ao = 255\n"
        "r = (0*255*255 + 0*0*255 + 32512) / 65025 = 0\n```\n\n"
        "得到 `000000FF`。第二层 `blend: mask` 源 `000000FF`:"
        "`out = (dr,dg,db, div255(255*255)) = (0,0,0,255)`,不改变上一步结果。"
        "若实现给 `multiply` 加了「全不透明即覆盖」的特例,第一层会得到 `FF0000FF`,"
        "摘要必然不同——这正是本条要挡的。",
        text)

    # blend-formula-818181-normal
    palette = ["81818180", "81818101"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("top", "normal", [fill(2)])])
    render_case(
        "blend-formula-818181-normal",
        "`§6.6(g)` 的补充例(钳位识别)、`§6.3`(必须用未取整的 `aNum` 归一化)。",
        "1×1,下层 `81818180`、上层 `81818101`(`normal`)。逐步算式:\n\n"
        "```\nsa=1  t=254  da=128\naNum = 1*255 + 128*254 = 32767\nao   = (32767 + 127) / 255 = 128\n"
        "half = 16383\n每通道: (0x81*1*255 + 0x81*128*254 + 16383) / 32767\n"
        "      = (32913 + 4193280 + 16383) / 32767 = 4242576 / 32767 = 129 (0x81)\n```\n\n"
        "正确结果 `81818180`。**旧公式**(先取整 `ao = 128`,再用 `den = ao*255 = 32640` "
        "做分母)得到 `82828280`(通道 130)——它没有越界,只是错。"
        "所以本条钉的是「即使不越界也必须用 `aNum` 归一」,"
        "与 `blend-bound-overflow` 的「旧公式会产出域外值 256」互补。",
        text)

    # blend-formula-818181-multiply
    palette = ["81818180", "FFFFFF01"]
    text = serialize(
        1, 1, "char", palette,
        [layer("bottom", "normal", [fill(1)]),
         layer("top", "multiply", [fill(2)])])
    render_case(
        "blend-formula-818181-multiply",
        "`§6.6(g)` 的补充例(multiply 侧的钳位识别)、`§6.4`(`mc` 代入同一条归一化证明)。",
        "1×1,下层 `81818180`、上层 `FFFFFF01`(`multiply`):\n\n"
        "```\nmc = div255(255*0x81) = div255(32895) = 129\n"
        "sa=1  t=254  da=128  aNum=32767  ao=128  half=16383\n"
        "每通道: (129*1*255 + 0x81*128*254 + 16383) / 32767\n"
        "      = (32895 + 4193280 + 16383) / 32767 = 4242558 / 32767 = 129 (0x81)\n```\n\n"
        "正确结果 `81818180`;旧公式(先取整 `ao`)给 `82828280`。"
        "`mc` 恰好等于上一条里用的 `sc`(两者都是 129),于是两者退化为同一算术——"
        "这正是 `CASES.tsv` 说「与上一条相同的算术」的意思。",
        text)


# ==========================================================================
# 二、negative/*(渲染侧 + 抽取侧,96 条)
# ==========================================================================

def negative_cases():
    """96 条负例:每个目录只放一个缺陷(`neg-priority-*` 例外,是阶段序用例)。"""

    def simple(**kwargs):
        return serialize(
            kwargs.get("width", 1), kwargs.get("height", 1),
            kwargs.get("encoding", "char"),
            kwargs.get("palette", ["FF0000FF"]),
            kwargs.get("layers", [layer(ops=[fill(1)])]),
            sprites=kwargs.get("sprites"),
            version=kwargs.get("version", 0),
            format_value=kwargs.get("format_value", "PXL"),
            top_extra=kwargs.get("top_extra"))

    # ---- 阶段 3:PXL001(结构与枚举)----
    negative_render(
        "negative/neg-json-invalid", "PXL001",
        "`§11.1` 阶段 2/3(`不是合法 JSON 属 `PXL001`)、`§3.1`。",
        "括号被截断:文本末尾少了一个 `}` 与 `]`,`json` 解析必然失败。"
        "期望 `PXL001`(阶段 3)。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": [\n    {\n      "name": "base",\n      "blend": "normal",\n'
        '      "ops": [\n        {\n          "op": "fill",\n          "slot": 1\n'
        '        }\n      ]\n    }\n')

    negative_render(
        "negative/neg-duplicate-key", "PXL001",
        "`§3.2`(同一对象内成员名不得重复)、`§11.1` 阶段 3。",
        "顶层对象里出现了两个 `\"width\"`(1 与 2)。不同 JSON 解析器对重复键有的取首值、"
        "有的取末值、有的直接报错;§3.2 因此把「成员名重复」定为 `PXL001`,"
        "阶段 3 就拒绝,不进入后续任何检查。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "width": 2,\n  "height": 1,\n  "palette": [\n'
        '    "FF0000FF"\n  ],\n  "layers": [\n    {\n      "name": "base",\n'
        '      "blend": "normal",\n      "ops": []\n    }\n  ]\n}\n')

    negative_render(
        "negative/neg-unknown-field", "PXL001",
        "`§3.2`(顶层出现该表之外的字段即 `PXL001`)、`§12`(`variant` / `bindings` "
        "同为 fail-closed)、`§11.1` 阶段 3。",
        "顶层多了一个 `\"extra\"` 成员。§3.2 把顶层成员集合钉死为 8 个字段,"
        "多一个即 `PXL001`(阶段 3)——`variant` / `bindings` 这两个 reserved 字段"
        "走的也是这条规则。",
        simple(top_extra=[("extra", 1)]))

    negative_render(
        "negative/neg-version-out-of-long-range", "PXL001",
        "`§12`(非法、非整数或越界的 `version` 一律 `PXL001`)、`§3.8`(按原始词法精确判定,"
        "不经二进制浮点)、`§11.1` 阶段 3。",
        "`version` 写成 2^63(`9223372036854775808`):词法是整数,但**超出 64 位可表示"
        "范围**。实现若只依赖宿主整数转换,会把无法表示的值读成 0 从而**误接受**"
        "(解析层用一个「整数但不可表示」的哨兵时尤其容易);正确做法是先判能不能表示,"
        "再判值。这条钉住 §12 的 fail-closed。",
        serialize(1, 1, "char", ["FF0000FF"], [layer(ops=[])]).replace(
            '"version": 1', '"version": 9223372036854775808'))

    negative_render(
        "negative/neg-blend-invalid", "PXL001",
        "`§3.3`(`blend` 只能是 `normal` / `multiply` / `mask`)、`§11.1` 阶段 3。",
        "图层 `blend` 写成 `\"screen\"`,不在三值之内。`blend` 的取值属**枚举合法性**"
        "(阶段 3),不是数值域,故是 `PXL001`。",
        simple(layers=[layer("base", "screen", [])]))

    negative_render(
        "negative/neg-layer-name-invalid", "PXL001",
        "`§3.3`(图层名必须匹配 `[a-z][a-z0-9_-]*`)、`§11.1` 阶段 3。",
        "图层名写成 `\"Base\"`(首字母大写),不匹配 `[a-z][a-z0-9_-]*`。"
        "注意 `\"base2\"`、`\"base_2\"`、`\"base-2\"` 都合法,只有首字母大写非法。",
        simple(layers=[layer("Base", "normal", [])]))

    negative_render(
        "negative/neg-layer-name-duplicate", "PXL001",
        "`§3.3`(图层名在文档内唯一)、`§11.1` 阶段 3。",
        "两个图层都叫 `base`。§3.3 要求图层名在文档内唯一——重名会让 diff 定位失去意义,"
        "也无法唯一引用某一层,故 `PXL001`(阶段 3)。",
        simple(layers=[layer("base", "normal", []), layer("base", "normal", [])]))

    negative_render(
        "negative/neg-fill-extra-member", "PXL001",
        "`§3.7`(`fill` 的成员集合恰为 `op`、`slot`,不得有多余成员)、`§5.1`、"
        "`§11.1` 阶段 3(已知 op 的参数集合)。",
        "`fill` 对象里多了一个 `\"x\": 0`。`fill` 没有坐标参数(§5.1),"
        "已知 op 的成员集合与 §5 不一致即 `PXL001`(阶段 3,而非 `PXL004`)。",
        simple(layers=[layer(ops=[op("fill", ("slot", 1), ("x", 0))])]))

    negative_render(
        "negative/neg-grid-row-unequal", "PXL001",
        "`§3.4`(同一数组内所有行长度必须相等)、`§11.1` 阶段 3"
        "(这一项不依赖 token 宽度)。",
        "`grid` 的两行分别是 `\"01\"` 与 `\"0\"`(长度 2 与 1)。各行等长检查不需要"
        "知道 token 宽度,因此归阶段 3:`PXL001`。",
        simple(width=2, height=2,
               layers=[layer(ops=[grid(0, 0, ["01", "0"])])]))

    negative_render(
        "negative/neg-grid-row-not-multiple", "PXL001",
        "`§3.4`(`index` 档下行长必须是 token 宽度 2 的正整数倍)、`§4.6`"
        "(按固定宽度逐段切分,不存在「这个 token 有多宽」的判定空间)、`§11.1` 阶段 5。",
        "`index` 档(`palette` 62 项)下 `grid` 只有一行 `\"000\"`:长度 3 不是 2 的"
        "整数倍,最后一段只剩 1 个字符。这条检查依赖 token 宽度,故归阶段 5:`PXL001`。",
        simple(encoding="index", palette=tier_palette(62),
               layers=[layer(ops=[grid(0, 0, ["000"])])]))

    negative_render(
        "negative/neg-char-z", "PXL001",
        "`§4.2`(`char` 档字符集是 `0-9` + `A-Z` + `a-y`,共 61 个;`z` **保留未用**)、"
        "`§11.1` 阶段 5。",
        "`char` 档 `grid` 的第 2 个字符是保留字符 `z`。§4.2 的枚举集里没有 `z`,"
        "它与 `.` 一起被留出(61 = 62 − 1)。token 字符集检查依赖档位,归阶段 5:`PXL001`。",
        simple(width=2, layers=[layer(ops=[grid(0, 0, ["0z"])])]))

    negative_render(
        "negative/neg-lowercase-hex", "PXL001",
        "`§4.3`(`index` / `index4` 档的 token 是**大写**十六进制)、`§11.1` 阶段 5。",
        "`index` 档下 token 写成 `\"0a\"`(小写)。§4.3 要求大写、零填充;"
        "小写字母不在 `.hex` 的枚举集(`0-9A-F`)里,故 `PXL001`(阶段 5)。"
        "`palette` 用了 62 项,好让档位确实是 `index`。",
        simple(encoding="index", palette=tier_palette(62),
               layers=[layer(ops=[grid(0, 0, ["0a"])])]))

    negative_render(
        "negative/neg-layers-empty", "PXL001",
        "`§3.2`(`layers` 数组至少 1 个图层,空数组即 `PXL001`)、`§11.1` 阶段 3。",
        "`layers` 是 `[]`。它不是「没有图层」而是缺了必需内容:§3.2 要求至少 1 层,"
        "阶段 3 报 `PXL001`。注意与 `neg-layers-over-limit`(257 层 → `PXL002` 阶段 6)"
        "区分:那是数量超限,这里是结构缺失。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": []\n}\n')

    negative_render(
        "negative/neg-grid-empty-array", "PXL001",
        "`§3.4`(行数组至少 1 行;空数组即 `PXL001`,不得当成 no-op)、`§11.1` 阶段 3。",
        "`grid` op 的 `grid` 字段是 `[]`。§3.4 明确:没有「0 行矩形」这种东西,"
        "空数组必须在阶段 3 被拒绝,而不是当成空操作。",
        simple(layers=[layer(ops=[grid(0, 0, [])])]))

    negative_render(
        "negative/neg-grid-empty-row", "PXL001",
        "`§3.4`(每行至少 1 个 token;空串行即 `PXL001`)、`§11.1` 阶段 3。",
        "`grid` 的单行是空串 `\"\"`。与上一条同源的规则(§3.4),但覆盖的是"
        "「0 列矩形」而不是「0 行」——同样不是 no-op,阶段 3 报 `PXL001`。",
        simple(layers=[layer(ops=[grid(0, 0, [""])])]))

    negative_render(
        "negative/neg-sprite-empty-array", "PXL001",
        "`§3.4`(行数组至少 1 行;`sprites` 的每个值同受此约束)、`§3.7`、"
        "`§11.1` 阶段 3。",
        "`sprites` 里是 `{\"s\": []}`,即某个 sprite 的行数组为空。它走的是与 `grid` "
        "不同的读取路径(§3.7 / §5.8),因此单独立项;规则本身与 "
        "`neg-grid-empty-array` 同一条,阶段 3 报 `PXL001`。",
        simple(sprites={"s": []}, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-sprite-name-invalid", "PXL001",
        "`§3.7`(`sprites` 的成员名必须匹配 `[a-z][a-z0-9_-]*`)、`§3.3`(与图层名同一条"
        "语法)、`§11.1` 阶段 3。",
        "`sprites` 的成员名写成 `Bad_Name`:不匹配 `[a-z][a-z0-9_-]*`(首字母大写)。"
        "§11 的 `PXL001` 条目里专门补了 sprite 名这一条;阶段 3 报 `PXL001`。",
        simple(sprites={"Bad_Name": ["0"]}, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-format-invalid", "PXL001",
        "`§3.2`(`format` 恒为 `\"PXL\"`;值不等于它就是 `PXL001`,阶段 3)、`§11`。",
        "`\"format\": \"OTHER\"`:字段存在、类型是字符串,但值不等于 `PXL`。"
        "只看「字段在不在、类型对不对」会漏掉它,所以 §3.2 明写值不等于 `\"PXL\"` "
        "也是 `PXL001`(阶段 3)。",
        simple(format_value="OTHER"))

    negative_render(
        "negative/neg-palette-nonstring", "PXL001",
        "`§3.2`(`palette` 是 array of string;项不是字符串属**类型类别**错误)、"
        "`§11` 的 `PXL001` / `PXL005` 分界、`§11.1` 阶段 3。",
        "`palette` 里放了一个数字 `123` 而不是字符串。§3.2 把 `palette` 定义为 "
        "array of string,类型类别不符归 `PXL001`(阶段 3);`PXL005` 只管"
        "「是字符串但格式 / 容量不对」,两者不重叠。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    123\n  ],\n'
        '  "layers": [\n    {\n      "name": "base",\n      "blend": "normal",\n'
        '      "ops": []\n    }\n  ]\n}\n')

    # ---- 阶段 4:PXL003 ----
    negative_render(
        "negative/neg-encoding-unknown", "PXL003",
        "`§11.1` 阶段 4(`encoding` 取值是否在 {`char`,`index`,`index4`})、`§4.5`。",
        "`encoding` 写成 `\"hex\"`。阶段 4 只判取值,先于任何依赖 token 宽度的检查,"
        "故必须是 `PXL003`。这一条也钉住「未知 `encoding` 搭配 `grid` 只可能得到 "
        "`PXL003`」——阶段 4 失败即返回,阶段 5 根本不执行。",
        simple(encoding="hex", layers=[layer(ops=[grid(0, 0, ["0"])])]))

    negative_render(
        "negative/neg-encoding-tier-mismatch", "PXL003",
        "`§4.5`(档位由 `palette` 长度唯一派生;声明值必须等于派生值)、"
        "`§11.1` 阶段 7。",
        "`palette` 有 100 项(派生档 = `index`),却声明 `\"encoding\": \"char\"`。"
        "阶段 7 比对声明值与派生值,不符即 `PXL003`。注意本轮 `palette` 长度 100 "
        "**没有**超过声明档的上限(61)?它超过了——但那条不单列规则,因为派生档必然"
        "高于声明档,阶段 7 的比对会先命中,报的仍是 `PXL003`。",
        simple(encoding="char", palette=tier_palette(100),
               layers=[layer(ops=[grid(0, 0, ["0"])])]))

    # ---- 阶段 6:PXL002(数值域与资源上界)----
    negative_render(
        "negative/neg-size-width-zero", "PXL002",
        "`§5.0.1`(`width` / `height` 取值 1..65536)、`§11.1` 阶段 6。",
        "`width` 写成 0。8.1 与 §5.0.1 的下界都是 1(不是 0),故越界属数值域 → `PXL002`。"
        "(类型是整数,所以不是 `PXL001`。)",
        simple(width=0, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-size-too-large", "PXL002",
        "`§5.0.1`(`width` 上界 65536)、`§11.1` 阶段 6。",
        "`width` 写成 70000,超过 65536 上界。文档侧越界一律 `PXL002`"
        "(输入 PNG 侧的同类越界才报 `PXL006`)。",
        simple(width=70000, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-width-not-integer", "PXL002",
        "`§5.0.1` / `§11`(`width` 不是整数值的数 → `PXL002`;类型类别本身错才是 "
        "`PXL001`)、`§11.1` 阶段 6。",
        "`width` 写成 `1.5`:JSON 类型是 number(类型类别没问题),但值不是整数,"
        "属数值域 → `PXL002`。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1.5,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": [\n    {\n      "name": "base",\n      "blend": "normal",\n'
        '      "ops": []\n    }\n  ]\n}\n')

    negative_render(
        "negative/neg-width-not-integer-precise", "PXL002",
        "`§3.8`(整数性与区间必须按数字的原始词法做精确十进制判断,不得先转二进制浮点)、"
        "`§11.1` 阶段 6。",
        "`width` 写成 `1.0000000000000001`。它与 1 **不相等**,是 `PXL002`;"
        "常见的双精度解析会把它舍成 `1.0` 从而误接受——这正是 §3.8 要钉的分叉点。"
        "与 `op-numeric-literal-forms` 成对(那一例的 `2.0` 等值,必须接受)。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1.0000000000000001,\n  "height": 1,\n  "palette": [\n'
        '    "FF0000FF"\n  ],\n  "layers": [\n    {\n      "name": "base",\n'
        '      "blend": "normal",\n      "ops": []\n    }\n  ]\n}\n')

    negative_render(
        "negative/neg-coord-out-of-range", "PXL002",
        "`§5.0.1`(`x` / `y` / `x0` / `y0` / `x1` / `y1` 取值 -65536..65536)、`§11.1` 阶段 6。",
        "`rect` 的 `x` 写成 99999,越出 §5.0.1 的坐标闭区间。已知 op 的坐标成员"
        "属数值域检查 → `PXL002`(阶段 6)。",
        simple(layers=[layer(ops=[rect(99999, 0, 1, 1, 1)])]))

    negative_render(
        "negative/neg-rect-negative-w", "PXL002",
        "`§5.0`(`w` / `h` 为负即 `PXL002`,为 0 等于空操作)、`§5.0.1`(`w` 取值 0..65536)。",
        "`rect` 的 `w` 写成 -1。矩形右开下开、`w`/`h` 必须非负;负数不是空操作,"
        "而是数值越界 → `PXL002`。",
        simple(layers=[layer(ops=[rect(0, 0, -1, 1, 1)])]))

    negative_render(
        "negative/neg-slot-non-integer", "PXL002",
        "`§5.0`(`slot` 是整数,取值 0..65535)、`§5.0.1`、`§11.1` 阶段 6。",
        "`fill` 的 `slot` 写成 `1.5`。它是整数值吗?不是 → `PXL002`"
        "(阶段 6 只判整数性与区间,判不出它是否越过 `palette` 长度——那是阶段 9)。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": [\n    {\n      "name": "base",\n      "blend": "normal",\n'
        '      "ops": [\n        {\n          "op": "fill",\n'
        '          "slot": 1.5\n        }\n      ]\n    }\n  ]\n}\n')

    negative_render(
        "negative/neg-grid-tokens-over-limit", "PXL002",
        "`§3.4`(行 token 数上界 65536,**是 token 数不是字符数**)、`§11.1` 阶段 6。",
        "`char` 档下一行有 65537 个 token(65537 个字符),超过 65536 上界。"
        "上界按 token 数计:`index4` 档下一行最多 65536 × 4 = 262144 个字符。",
        serialize(65537, 1, "char", ["FF0000FF"],
                  [layer(ops=[grid(0, 0, ["0" * 65537])])]))

    negative_render(
        "negative/neg-sprite-tokens-over-limit", "PXL002",
        "`§3.4`(上界适用于**凡出现 token 行数组的地方**:`grid` 字段与 `sprites` 的"
        "每个值)、`§11.1` 阶段 6(`行 token 数上界`)。",
        "一个 sprite 的单行有 65537 个 token,超过 65536 上界。只查 `grid` 不查 "
        "`sprites` 就会放过它——同一份文档在另一个实现里会被拒,这是实质分叉。"
        "本例的 `ops` 为空,数据全在 `sprites` 里。",
        serialize(1, 1, "char", ["FF0000FF"], [layer(ops=[])],
                  sprites={"s": ["0" * 65537]}))

    negative_render(
        "negative/neg-size-area-over-limit", "PXL002",
        "`§5.0.2`(`width × height <= 2^22`)、`§11.1` 阶段 6(文档侧面积检查)。",
        "`width` = 2049、`height` = 2048,各自都合法,乘积 4,196,352 超过 2^22 = 4,194,304。"
        "文档本身极小(每层 `ops` 为空),专测**文档侧**面积上界 —— 它必须与输入侧"
        "(`neg-png-area-over-limit`)分开,因为越界报的码不同(`PXL002` vs `PXL006`)。",
        simple(width=2049, height=2048, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-size-area-overflow-32bit", "PXL002",
        "`§5.0.1`(资源校验必须用 64 位或防溢出比较,不得依赖 32 位乘法)、`§5.0.2`。",
        "`width` 与 `height` 都取合法上界 65536,乘积 4,294,967,296 **超过 2^31−1**。"
        "用 32 位算会回绕成 `0`,从而「通过」面积检查。fixture 极小(只有两个数字),"
        "不必真画 43 亿像素——测的是乘法是否溢出,不是内存。",
        simple(width=65536, height=65536, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-layers-over-limit", "PXL002",
        "`§5.0.2`(图层数上界 256)、`§11.1` 阶段 6。",
        "257 个图层,超过 256 上限。每层 `ops` 为空,文件很小;专测图层数上界,"
        "与 `neg-layers-empty`(结构缺失 → `PXL001`)区分:这里是数量超限 → `PXL002`。",
        simple(width=1, height=1,
               layers=[layer("l%03d" % index, "normal", []) for index in range(257)]))

    negative_render(
        "negative/neg-resource-layers-times-area", "PXL002",
        "`§5.0.2`(图层数 × `width × height` <= 2^24)、`§11.1` 阶段 6。",
        "256 个图层 × 2048×2048 = 2^30 像素,超过 2^24 上界。**每一项单独都合规**:"
        "面积恰为 2^22 上限、图层数恰为 256 上限——只有乘积超限。文档本身很小"
        "(每层 `ops` 为空),专测这条乘积。",
        simple(width=2048, height=2048,
               layers=[layer("l%03d" % index, "normal", []) for index in range(256)]))

    ops_text = _ops_over_limit()
    negative_render(
        "negative/neg-resource-raster-budget", "PXL002",
        "`§5.0.2` 的光栅工作预算(所有 op 裁剪后的像素写入总数不得超过 2^22)。",
        "画布 2048×2048(面积恰好等于上界,合法)、两条全画布 `rect`、每层 ops 数 2(远低于 32768)"
        "——三个旧上限各自都合法,但累计像素写入是 8,388,608,超过 2^22。"
        "只按面积/图层/ops 分别设界,挡不住这种组合。",
        serialize(2048, 2048, "char", ["FF0000FF"],
                  [layer(ops=[rect(0, 0, 2048, 2048, 1), rect(0, 0, 2048, 2048, 1)])]))

    negative_render(
        "negative/neg-resource-ops-over-limit", "PXL002",
        "`§5.0.2`(每层 `ops` 数上界 32,768)、`§11.1` 阶段 6。",
        "单层 32,769 条 `fill slot 0`(每条都是一次合法的透明填充),超过每层 32,768 "
        "上限。生成后实测 **%d 字节**(%.2f MiB),低于 §13.6 的 4 MiB 阈值,"
        "因此**必须入库**,不走生成路径。"
        "(README §5.1 与该行的 `covers` 记的是 2,130,193 字节,与本文件相差 16 字节——"
        "fixture 的构造细节不同,以本处的实测值为准。)"
        % (len(ops_text.encode("utf-8")), len(ops_text.encode("utf-8")) / 1048576.0),
        ops_text)

    negative_render(
        "negative/neg-resource-sprites-over-limit", "PXL002",
        "`§5.0.2`(`sprites` 个数上界 4096)、`§11.1` 阶段 6。",
        "`sprites` 有 4097 项,每项只有一行 `\"0\"`,超过 4096 上限。"
        "所有 sprite 都未被 `stamp` 引用——这不要紧:§5.0.2 的计数与引用无关,"
        "而 §11.1 阶段 6 的数量检查先于阶段 9 的引用检查。",
        simple(sprites=dict(("s%04d" % index, ["0"]) for index in range(4097)),
               layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-resource-sprite-rows-over-limit", "PXL002",
        "`§5.0.2`(每个 sprite 的行数上界 65536)、`§11.1` 阶段 6。",
        "单个 sprite 有 65537 行(每行 1 个 token,与其它行等长),超过 65536 行上限。"
        "行数上界与 `height` 上界是两条独立规则,这里钉的是前者。",
        simple(sprites={"s": ["0"] * 65537}, layers=[layer(ops=[])]))

    # ---- 阶段 8:PXL004 ----
    negative_render(
        "negative/neg-unknown-op", "PXL004",
        "`§11`(`op` 值不在八条图元之内 → `PXL004`)、`§11.1` 阶段 8。",
        "`op` 写成 `\"circle\"`。`op` 字段存在且是字符串(阶段 3 通过),但不在八条之内,"
        "阶段 8 报 `PXL004`。未知 op 的 op 专属成员不检查,因此这一例只有 `op` 一个成员"
        "也能通过阶段 3。",
        simple(layers=[layer(ops=[op("circle", ("x", 0))])]))

    # ---- 阶段 9:PXL005 ----
    negative_render(
        "negative/neg-palette-missing-slot", "PXL005",
        "`§11.1` 阶段 9(token 解码出的 slot 不得超过 `palette` 长度)、`§4.4`。",
        "`palette` 只有 2 项,而 `grid` 的 token 是 `\"5\"`(slot 5)。"
        "token 解码路径的越界归阶段 9 → `PXL005`。",
        simple(width=1, palette=["FF0000FF", "00FF00FF"],
               layers=[layer(ops=[grid(0, 0, ["5"])])]))

    negative_render(
        "negative/neg-palette-lowercase", "PXL005",
        "`§3.2`(`palette` 项必须匹配 `^[0-9A-F]{8}$`)、`§11.1` 阶段 9。",
        "`palette` 项写成小写 `ff0000ff`。它是字符串(阶段 3 类型类别通过),"
        "但不匹配大写十六进制 → `PXL005`。与 `negative/neg-palette-nonstring` "
        "(非字符串 → `PXL001`)配对,钉住 §3.2 的三层判据。",
        simple(palette=["ff0000ff"], layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-palette-contains-transparent", "PXL005",
        "`§2`(slot 0 保留给透明,不占 `palette`)、`§3.2`、`§11.1` 阶段 9。",
        "`palette` 里出现 `\"00000000\"`。透明 slot 是保留值,`palette` 只存非透明色,"
        "出现即 `PXL005`。",
        simple(palette=["00000000"], layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-stamp-missing-sprite", "PXL005",
        "`§5.8`(`stamp` 引用不存在的 sprite → `PXL005`)、`§11.1` 阶段 9。",
        "`stamp` 引用了 `\"missing\"`,而文档里根本没有 `sprites` 成员。"
        "「名字是否存在」不是 slot 检查:没有 `sprites` 时没有任何 token 可供检查,"
        "所以 §11.1 阶段 9 必须单独接收这一类。",
        simple(layers=[layer(ops=[stamp("missing", 0, 0)])]))

    negative_render(
        "negative/neg-sprite-slot-out-of-range", "PXL005",
        "`§11.1` 阶段 9(**所有** token,含未被任何 `stamp` 引用的 sprite 定义)、`§3.7`。",
        "`palette` 是 `[]`、`sprites` 是 `{\"s\": [\"0\"]}`、唯一的图层 `ops` 为空,"
        "且**没有任何 `stamp` 引用该 sprite**。字符 `0` 合法(阶段 5 通过),"
        "但它指向 slot 1,而 `palette` 长度为 0。未引用不等于不校验 → `PXL005`。",
        simple(palette=[], sprites={"s": ["0"]}, layers=[layer(ops=[])]))

    negative_render(
        "negative/neg-numeric-slot-out-of-palette", "PXL005",
        "`§11.1` 阶段 9(已知 op 的**数值** `slot` 不得超过该档上限或 `palette` 长度)、"
        "`§5.0`(`slot: 0` 合法)。",
        "`palette` 只有 1 项,而 `fill` 的 `slot` 写成 2。这个数**不经过 token 解码**,"
        "阶段 6 只判它是不是 0..65535 的整数(是),判不出它越过 `palette` 长度;"
        "阶段 9 必须单独接收「数值 slot」这一类 → `PXL005`。",
        simple(palette=["FF0000FF"], layers=[layer(ops=[fill(2)])]))

    negative_render(
        "negative/neg-palette-over-capacity", "PXL005",
        "`§4.5`(`n >= 65536` 时没有可派生的档位,阶段 7 被跳过)、`§11.1` 阶段 9、`§3.2`。",
        "`palette` 恰好 65536 项(= `index4` 值域总数,含透明值)。`n >= 65536` 时"
        "「声明值是否等于派生值」这句话无从判断,阶段 7 因此**跳过**,直接由阶段 9 "
        "报 `PXL005`。若改成 `PXL003`,就会与 `neg-priority-005-over-003-palette-capacity` "
        "打架。fixture 约 1.1 MB,低于 4 MiB 阈值,入库。",
        simple(encoding="index4", palette=tier_palette(65536),
               layers=[layer(ops=[])]))

    # ---- 阶段序(neg-priority-*,刻意同时违反两条)----
    negative_render(
        "negative/neg-priority-002-over-003", "PXL002",
        "`§11.1` 阶段 6 先于阶段 7(数值域 → 档位比对)。",
        "**刻意的阶段序用例**:同一文档既 `width = 0`(阶段 6 的 `PXL002`)"
        "又 `encoding` 与由 `palette` 长度派生的档位不符(阶段 7 的 `PXL003`)。"
        "必须报阶段更早的 `PXL002`。",
        simple(width=0, encoding="char", palette=tier_palette(100),
               layers=[layer(ops=[grid(0, 0, ["0"])])]))

    negative_render(
        "negative/neg-priority-002-over-003-layers", "PXL002",
        "`§11.1` 阶段 6(资源上界)先于阶段 7(档位比对)。",
        "**刻意的阶段序用例**:257 个图层(阶段 6 的 `PXL002`)与档位不符"
        "(阶段 7 的 `PXL003`)同时存在,必须报 `PXL002`。"
        "它钉住「图层数上界确实归在阶段 6」,而不是被更晚的 `PXL003` 抢走。",
        simple(width=1, encoding="char", palette=tier_palette(100),
               layers=[layer("l%03d" % index, "normal", [])
                       for index in range(257)]))

    negative_render(
        "negative/neg-priority-001-over-003-empty-grid", "PXL001",
        "`§11.1` 阶段 3(行数组非空)先于阶段 4(`encoding` 取值)。",
        "**刻意的阶段序用例**:`grid: []`(阶段 3 的 `PXL001`)与未知 `encoding`"
        "(阶段 4 的 `PXL003`)同时存在。行数组非空判定不需要 token 宽度,归阶段 3,"
        "必须报 `PXL001`。",
        simple(encoding="hex", layers=[layer(ops=[grid(0, 0, [])])]))

    negative_render(
        "negative/neg-priority-001-over-003-uneven-grid", "PXL001",
        "`§11.1` 阶段 3(各行等长)先于阶段 4(`encoding` 取值)。",
        "**刻意的阶段序用例**:两行长度不等(阶段 3)与未知 `encoding`(阶段 4)"
        "同时存在。各行等长同样不需要 token 宽度,归阶段 3:`PXL001`。",
        simple(encoding="hex", width=2, height=2,
               layers=[layer(ops=[grid(0, 0, ["01", "0"])])]))

    negative_render(
        "negative/neg-priority-001-over-003-empty-layers", "PXL001",
        "`§11.1` 阶段 3(`layers` 非空)先于阶段 4(`encoding` 取值)。",
        "**刻意的阶段序用例**:`layers: []`(阶段 3)与未知 `encoding`(阶段 4)同时存在,"
        "必须报 `PXL001`。",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "hex",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": []\n}\n')

    negative_render(
        "negative/neg-priority-004-over-002-unknown-op", "PXL004",
        "`§11.1` 阶段 8 与「op 专属检查只对已知 op 生效」、`§11.1` 的跳过范围说明。",
        "**刻意的阶段序用例**:`{\"op\": \"circle\", \"x\": 99999}` —— 未知 op 附带越界坐标。"
        "按 §11.1,op 级数值检查只适用于八条已知图元,未知 op 的 `x` 不被检查,"
        "故阶段 6 不报 `PXL002`,统一在阶段 8 报 `PXL004`。",
        simple(layers=[layer(ops=[op("circle", ("x", 99999))])]))

    negative_render(
        "negative/neg-priority-004-over-001-unknown-op-grid", "PXL004",
        "`§3.4`(行数组规则只作用于已知 `grid` op 的 `grid` 字段与 `sprites` 的值)、"
        "`§11.1` 阶段 8。",
        "**刻意的阶段序用例**:未知 op `circle` 带一个形似 `grid` 的成员,且其各行长度不等。"
        "那个成员不是「token 行数组」(§3.4 只约束已知 `grid` 与 `sprites`),"
        "故阶段 3 不报 `PXL001`,统一报 `PXL004`。",
        simple(width=2, height=2,
               layers=[layer(ops=[op("circle", ("grid", ["01", "0"]))])]))

    negative_render(
        "negative/neg-priority-001-over-004-unknown-op-duplicate-key", "PXL001",
        "`§3.2`(成员名重复)、`§11.1` 阶段 3 与「通用对象检查对任何对象都执行」。",
        "**刻意的阶段序用例**:`{\"op\": \"circle\", \"x\": 1, \"x\": 2}` —— 未知 op 且同一对象内 "
        "`x` 重复。按 §11.1,成员名重复是**通用对象检查**,阶段 3 就报 `PXL001`;"
        "未知 op 只跳过 op 专属检查,不影响这一条。"
        "(这一份 `input.pxl` 因此不是规范形式——它必须保留重复键才能测到该规则。)",
        '{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",\n'
        '  "width": 1,\n  "height": 1,\n  "palette": [\n    "FF0000FF"\n  ],\n'
        '  "layers": [\n    {\n      "name": "base",\n      "blend": "normal",\n'
        '      "ops": [\n        {\n          "op": "circle",\n'
        '          "x": 1,\n          "x": 2\n        }\n      ]\n    }\n  ]\n}\n')

    negative_render(
        "negative/neg-priority-005-over-003-palette-capacity", "PXL005",
        "`§4.5`(阶段 7 的前置条件:仅当 `n <= 65535` 才比对档位)、`§11.1` 阶段 9。",
        "**刻意的阶段序用例**:`palette` 长度 65536 且 `encoding` 声明为 `char`。"
        "若阶段 7 照常执行会因档位不符报 `PXL003`;但 `n >= 65536` 时阶段 7 "
        "**被跳过**,必须由阶段 9 报 `PXL005`。",
        simple(encoding="char", palette=tier_palette(65536),
               layers=[layer(ops=[])]))

    # ---- 阶段 5:U+00A0 ----
    negative_render(
        "negative/neg-token-nbsp", "PXL001",
        "`§3.4`(行内只允许 §4.2 / §4.3 枚举出的 token 字符;不得用宿主语言的"
        "「是否为空白」分类)、`§11.1` 阶段 5。",
        "`grid` 行内含一个 U+00A0(非断行空格)。它在很多语言的「空白」分类里是空白,"
        "但 §3.4 只认枚举字符集 → 阶段 5 报 `PXL001`。"
        "本文件把这个字符写成 JSON 转义 `\\u00a0`(§3.8 要求渲染器接受转义):"
        "解码后的码点必须真的是 U+00A0,而它不在 §4.2 的枚举字符集里 → 阶段 5 报 `PXL001`。",
        serialize(1, 1, "char", ["FF0000FF"],
                  [layer(ops=[grid(0, 0, ["\u00a0"])])]))

    negative_render(
        "negative/neg-priority-003-over-001-nbsp", "PXL003",
        "`§11.1` 阶段 4 先于阶段 5(token 字符集依赖档位)、`§3.4`。",
        "**刻意的阶段序用例**:`encoding` 未知**且** `grid` 行内含 U+00A0。"
        "字符是否合法取决于档位(阶段 5),而 `encoding` 取值在阶段 4;"
        "阶段 4 先命中,必须报 `PXL003` 而不是 `PXL001`。",
        serialize(1, 1, "hex", ["FF0000FF"],
                  [layer(ops=[grid(0, 0, ["\u00a0"])])]))

    negative_render(
        "negative/neg-priority-001-over-003-codepoint-length", "PXL001",
        "`§3.4`(长度一律按 JSON 解码后的 **Unicode 码点**计数,不按字节、不按 UTF-16 "
        "代码单元)、`§11.1` 阶段 3(各行等长)先于阶段 4(`encoding` 取值)。",
        "**刻意的阶段序用例**:`grid` 是 `[\"\\uD83D\\uDE00\", \"00\"]`,`encoding` 未知。"
        "那个转义代理对解码后是**一个**码点,所以两行长度是 1 与 2——不等长,属阶段 3 的 "
        "`PXL001`。若按 UTF-16 代码单元计,两行是 2 与 2(等长),就会滑到阶段 4 报 "
        "`PXL003`。§3.4 把计数单位写死为码点,就是为了同一份文件不给两个答案。"
        "(本文件的字节里,那一行是 `json.dumps` 写出的 `\\uD83D\\uDE00` 转义;"
        "渲染器按 JSON 解码后必须只见到一个码点。)",
        serialize(1, 1, "hex", ["FF0000FF"],
                  [layer(ops=[grid(0, 0, ["\U0001F600", "00"])])]))


def _ops_over_limit():
    """`neg-resource-ops-over-limit` 的输入(32769 条 `fill slot 0`)。

    体积约 2 MB:低于 §13.6 的 4 MiB 阈值,**必须入库**。
    """
    ops = [fill(0)] * 32769
    return serialize(1, 1, "char", ["FF0000FF"], [layer(ops=ops)])


# ==========================================================================
# 三、negative/*(抽取侧):PNG 字节级构造
#
# 这些用例的输入是**非法或不受支持的 PNG**,被测的是抽取器(§8.1 / §8.3 / §8.5)。
# 每个文件都从一份最小合法 PNG 出发,只破坏一处,便于阅读与 review。
# ==========================================================================

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _crc(ctype, data):
    return binascii.crc32(ctype + data) & 0xFFFFFFFF


def _chunk(ctype, data, crc=None):
    """一个标准 chunk;``crc`` 显式给出时用它替代计算值(用于破坏 CRC 的用例)。"""
    value = _crc(ctype, data) if crc is None else crc
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", value)


def _png(chunks):
    return PNG_SIGNATURE + b"".join(chunks)


def _ihdr(width, height, depth=8, color_type=6, compression=0, filter_method=0,
          interlace=0):
    return _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth,
                                       color_type, compression, filter_method,
                                       interlace))


def _scanlines(rows):
    """把行数组拼成 PNG 的原始扫描线(每行前置过滤器字节 0)。"""
    return b"".join(b"\x00" + bytes(row) for row in rows)


def _zlib(rows):
    """行过滤器恒为 0(None)的一整条 zlib 流。"""
    return zlib.compress(_scanlines(rows), 9)


def _ct6_rows(width, height):
    """RGBA 的两色棋盘——只用于「像素本身无关」的结构类负例。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row += b"\xff\x00\x00\xff" if (x + y) % 2 == 0 else b"\x00\xff\x00\xff"
        rows.append(bytes(row))
    return rows


def extract_cases():
    """抽取侧负例(PXL006):每个用例只有 `input.png` + `expected.error`。"""
    width = height = 16
    rows = _ct6_rows(width, height)
    payload = _zlib(rows)

    def ct6(chunks):
        return _png([_ihdr(width, height)] + list(chunks) + [_chunk(b"IEND", b"")])

    negative_extract(
        "negative/neg-bitdepth16", "PXL006",
        "`§8.1`(样本深度仅接受 8)、`§8.3`(16 bit 必须显式拒绝,不得静默降位深)、`§3.6`。",
        "IHDR 的样本深度写成 16(色彩类型 6,每像素 8 字节)。v0 的接受集只含 8 bit;"
        "任何降位深都是有损的,违反 §1 的无损前提,因此必须在**输入侧**拒绝。",
        _png([_ihdr(width, height, depth=16),
              _chunk(b"IDAT", _zlib([bytes(8 * width)] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-bitdepth4", "PXL006",
        "`§8.1`(样本深度仅接受 8)、`§8.3`(深度 1 / 2 / 4 属**范围收窄**,v0 不展开)。",
        "色彩类型 3、样本深度 4(每字节两个样本)。4 bit 索引 PNG 本可无损展开,"
        "但 v0 有意不展开以换取「接受集小而封闭」,按 §8.3 必须拒绝。",
        _png([_ihdr(width, height, depth=4, color_type=3),
              _chunk(b"PLTE", bytes(range(16)) * 3),
              _chunk(b"IDAT", _zlib([bytes(range(8))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-bitdepth1", "PXL006",
        "`§8.1`(样本深度仅接受 8)、`§8.3`(深度 1 属范围收窄)。",
        "色彩类型 0、样本深度 1(每字节 8 个样本)。同上:v0 只接受 8 bit。",
        _png([_ihdr(width, height, depth=1, color_type=0),
              _chunk(b"IDAT", _zlib([b"\xaa\x55"] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-bitdepth2", "PXL006",
        "`§8.1`(样本深度仅接受 8)、`§8.3`(深度 2 属范围收窄)。",
        "色彩类型 0、样本深度 2(每字节 4 个样本)。同上。",
        _png([_ihdr(width, height, depth=2, color_type=0),
              _chunk(b"IDAT", _zlib([b"\x1b\xe4\x1b\xe4"] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-adam7", "PXL006",
        "`§8.1`(交错仅接受 0)、`§8.3`(Adam7 必须拒绝)。",
        "IHDR 的 `interlace` 字段写成 1(Adam7)。v0 的接受集只含非交错,"
        "交错解码是另一套行/通道映射,不实现也不静默当作非交错。",
        _png([_ihdr(width, height, interlace=1),
              _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-ct5", "PXL006",
        "`§8.1`(色彩类型 0 / 2 / 3 / 4 / 6 之外一律拒绝)、`§8.3`。",
        "IHDR 的色彩类型写成 5 —— 一个未定义的取值。§8.1 的接受集是封闭且穷举的,"
        "表外的一律拒绝。",
        _png([_ihdr(width, height, color_type=5),
              _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-index-beyond-plte", "PXL006",
        "`§8.5`(色彩类型 3 的样本索引 `>= PLTE` 项数即拒绝;不钳位、不环绕)。",
        "PLTE 只有 2 项,而索引图里出现了样本 5。按 §8.5 必须拒绝;"
        "钳位或环绕都会静默改变像素。",
        _png([_ihdr(width, height, color_type=3),
              _chunk(b"PLTE", b"\x10\x20\x30\x40\x50\x60"),
              _chunk(b"IDAT", _zlib([bytes((x % 5) for x in range(width))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-crc", "PXL006",
        "`§8.5`(**每一个 chunk 的 CRC 都必须校验**,不逐类型列举)。",
        "IDAT 的 CRC 字段被改成 `DEADBEEF`(数据本身仍可解压)。§8.5 要求校验"
        "每个 chunk 的 CRC;按「能不能读出像素」放行会让两个实现在这里分叉。"
        "IEND 的同类缺陷由 `neg-png-iend-crc` 单独覆盖。",
        ct6([_chunk(b"IDAT", payload, crc=0xDEADBEEF)]))

    negative_extract(
        "negative/neg-png-iend-crc", "PXL006",
        "`§8.5`(CRC 校验**包括 IEND**)。",
        "IEND 的 CRC 字段被改坏。§8.5 特别点名「包括 IEND」——`neg-png-crc` 只破坏了 "
        "IDAT,不能替它作证。",
        _png([_ihdr(width, height), _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"", crc=0x12345678)]))

    negative_extract(
        "negative/neg-png-inflated-too-long", "PXL006",
        "`§8.5`(解压后字节数必须**恰好**等于 `height × (1 + stride)`;不得只取前若干行)。",
        "IDAT 解压后比 `16 × (1 + 64)` 多出 1 个字节。多一个也要拒绝——"
        "否则「多出来的那一段是什么」就成了实现自由裁量。",
        # 必须是**一条完整的 zlib 流**、只是解压出来的字节多 1 个。早先写成
        # `payload + b"\x00"`(流后追加)测的是「流后有剩余字节」，与 zlib-trailing 同构，
        # 真正的「解压长度必须精确」一条用例都没碰到。
        ct6([_chunk(b"IDAT", zlib.compress(_scanlines(rows) + b"\x00", 9))]))

    negative_extract(
        "negative/neg-png-inflated-too-short", "PXL006",
        "`§8.5`(解压后字节数必须恰好;不得补零、不得截断)。",
        "IDAT 解压后比 `16 × (1 + 64)` 少 1 个字节。补零会静默造出一个不存在的像素。",
        # 同理:完整 zlib 流、解压出来少 1 个字节。早先的 `payload[:-1]` 截断的是
        # **流本身**,测的是「zlib 流提前结束」。
        ct6([_chunk(b"IDAT", zlib.compress(_scanlines(rows)[:-1], 9))]))

    negative_extract(
        "negative/neg-png-trns-bad-length", "PXL006",
        "`§8.5`(色彩类型 0 的 `tRNS` 必须恰为 2 字节)。",
        "色彩类型 0 的 `tRNS` 写了 3 个字节。长度不符即拒绝——多出来的字节没有定义。",
        _png([_ihdr(width, height, color_type=0),
              _chunk(b"tRNS", b"\x00\x80\x00"),
              _chunk(b"IDAT", _zlib([bytes(range(16))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-size-over-limit", "PXL006",
        "`§8.3`(`width` / `height` 超过 65536 必须在**输入侧**拒绝)、`§5.0.1`。",
        "IHDR 声明 `width = 70000`,`height = 1`。这样的 PNG 无论怎么抽都写不出合法 PXL"
        "(画布上界 65536),必须在输入侧拒绝,而不是等抽取完再撞上 `PXL002`。"
        "文件本身很小:拒绝发生在读 IHDR 之后,不需要真的解压出 70000 像素。",
        _png([_ihdr(70000, 1, color_type=0),
              _chunk(b"IDAT", zlib.compress(b"\x00\x01", 9)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-area-over-limit", "PXL006",
        "`§8.3`(`width × height` 超过 §5.0.2 的面积上界即拒)、`§5.0.2`(`<= 2^22`)。",
        "IHDR 声明 `2049 × 2048 = 4,196,352 > 2^22 = 4,194,304`。宽高各自都在 1..65536 内,"
        "只有乘积越界 —— 这条与文档侧的 `neg-size-area-over-limit` 成对,"
        "但报的是输入侧的 `PXL006`。",
        _png([_ihdr(2049, 2048, color_type=0),
              _chunk(b"IDAT", zlib.compress(b"\x00\x01", 9)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-filter-illegal", "PXL006",
        "`§8.5`(每行的过滤器字节必须是 0..4;其它值拒绝)。",
        "第 3 行的过滤器字节写成 5。§8.5 只接受 0..4,其余一律拒绝——"
        "把未知过滤器当 0 处理会静默改变像素。",
        ct6([_chunk(b"IDAT", zlib.compress(
            b"".join(b"\x00" + bytes(row) for row in rows[:2])
            + b"\x05" + bytes(rows[2])
            + b"".join(b"\x00" + bytes(row) for row in rows[3:]), 9))]))

    negative_extract(
        "negative/neg-png-idat-noncontiguous", "PXL006",
        "`§8.5`(`IDAT` 必须连续出现,中间不得夹其它 chunk)。",
        "两块 IDAT 之间夹了一条 `tEXt`。§8.5 要求 IDAT 连续出现、按顺序拼接;"
        "夹了别的 chunk 之后「先收全文件再解码」与「流式解码」会得到不同结果。",
        ct6([_chunk(b"IDAT", payload[:len(payload) // 2]),
              _chunk(b"tEXt", b"Comment\x00between idat chunks"),
              _chunk(b"IDAT", payload[len(payload) // 2:])]))

    negative_extract(
        "negative/neg-png-duplicate-chunk", "PXL006",
        "`§8.5`(会被读取的 chunk —— `IHDR` / `PLTE` / `tRNS` —— 各自至多一个)。",
        "出现两块 `PLTE`。`PLTE` 是会被读取的 chunk,多一个即拒绝。"
        "**不要**拿 `pHYs` / `tEXt` 之类构造此例:它们重复是合法的"
        "(对照 `syn-png-two-text-chunks`)。",
        _png([_ihdr(width, height, color_type=3),
              _chunk(b"PLTE", bytes(range(48))),
              _chunk(b"PLTE", bytes(range(48, 96))),
              _chunk(b"IDAT", _zlib([bytes(x % 16 for x in range(width))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-unknown-critical", "PXL006",
        "`§8.5`(未知 **critical** chunk —— 首字母大写 —— 即拒绝;未知 ancillary 才忽略)。",
        "IHDR 之后插入一个类型名为 `PrVt` 的 chunk:四个字节全是字母、第三个字节大写"
        "(保留位 0)、首字母 `P` 大写 → critical,且是未知类型。必须拒绝,"
        "不能当成 ancillary 跳过。",
        ct6([_chunk(b"PrVt", b"critical-unknown"), _chunk(b"IDAT", payload)]))

    negative_extract(
        "negative/neg-png-zlib-adler", "PXL006",
        "`§8.5`(拼接后的 IDAT 必须是恰好一个完整 zlib 流,Adler-32 必须校验)。",
        "zlib 流的 Adler-32 尾 4 字节被改坏(deflate 数据本身仍是好的)。"
        "§8.5 要求校验 Adler-32 —— 只看「能不能 inflate 出来」会让静默损坏过关。",
        ct6([_chunk(b"IDAT", payload[:-1] + bytes((payload[-1] ^ 0xFF,)))]))

    negative_extract(
        "negative/neg-png-zlib-trailing", "PXL006",
        "`§8.5`(流后有剩余字节即拒绝;不得静默忽略)。",
        "zlib 流正常结束后多出 1 个字节。§8.5 明写「流后有剩余字节」要拒绝。",
        ct6([_chunk(b"IDAT", payload + b"\x00")]))

    negative_extract(
        "negative/neg-png-chunk-length-high-bit", "PXL006",
        "`§8.5`(chunk 长度按 32 位无符号读,必须 `len <= 2,147,483,647`,"
        "且 `8 + len + 4 <= 文件剩余字节数`)。",
        "IHDR 之后紧跟一个长度字段为 `0x80000000` 的 chunk(最高位为 1)。"
        "按无符号读是一个荒谬的大长度、按有符号读是负数——两种错法不同,"
        "但都必须被同一条规则挡住。",
        _png([_ihdr(width, height),
              struct.pack(">I", 0x80000000) + b"tEXt",
              _chunk(b"IDAT", payload), _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-chunk-truncated", "PXL006",
        "`§8.5`(不得把「长度超出文件末尾」当成「读到文件结束」)。",
        "IHDR 之后紧跟一个声明长度 100、实际只有 5 字节数据的 chunk,文件到此结束。"
        "把截断的文件静默当成合法,是 §8.5 明确禁止的。",
        _png([_ihdr(width, height),
              struct.pack(">I", 100) + b"tEXt" + b"short"]))

    negative_extract(
        "negative/neg-png-chunk-type-invalid", "PXL006",
        "`§8.5`(chunk 类型名必须恰为 4 个 ASCII 字母)。",
        "IHDR 之后插入类型名为 `teX1` 的 chunk:含数字 `1`。类型名含数字后,"
        "「首字母大小写区分 critical / ancillary」这个判据就没有定义了,故必须拒绝。"
        "与 `neg-png-chunk-reserved-bit`(四个字母但第三字节小写)区分。",
        _png([_ihdr(width, height),
              _chunk(b"teX1", b"payload"),
              _chunk(b"IDAT", payload), _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-bad-signature", "PXL006",
        "`§8.5`(文件前 8 字节必须**恰好**是 PNG 签名)。",
        "签名最后一个字节从 `0A` 改成 `0B`,其余全部合法。签名不符即拒绝——"
        "不靠「后面的 chunk 看起来对」放行。",
        b"\x89PNG\r\n\x1a\x0b" + _png([_ihdr(width, height),
                                       _chunk(b"IDAT", payload),
                                       _chunk(b"IEND", b"")])[8:])

    negative_extract(
        "negative/neg-png-missing-iend", "PXL006",
        "`§8.5`(`IEND` 必须恰好一个且排在最后;缺失即拒绝)。",
        "文件在 IDAT 之后直接结束,没有 `IEND`。截断的文件不能静默当成合法。",
        _png([_ihdr(width, height), _chunk(b"IDAT", payload)]))

    negative_extract(
        "negative/neg-png-data-after-iend", "PXL006",
        "`§8.5`(`IEND` 之后不得有任何字节)。",
        "`IEND` 之后多了一个字节 `00`。§8.5 明写多余数据不得静默忽略。",
        ct6([_chunk(b"IDAT", payload)]) + b"\x00")

    negative_extract(
        "negative/neg-png-missing-idat", "PXL006",
        "`§8.5`(IDAT 至少一块;缺失即拒绝)。",
        "只有 `IHDR` 与 `IEND`,没有任何 `IDAT`。没有像素数据的 PNG 无法抽出画面。",
        _png([_ihdr(width, height), _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-ihdr-compression-method", "PXL006",
        "`§8.5`(IHDR 的压缩方法必须为 0)。",
        "IHDR 的压缩方法字段写成 1。该字段只定义过 0,其它值必须拒绝。",
        _png([_ihdr(width, height, compression=1), _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-ihdr-filter-method", "PXL006",
        "`§8.5`(IHDR 的过滤方法必须为 0)。",
        "IHDR 的过滤方法字段写成 1。同上:只定义过 0。",
        _png([_ihdr(width, height, filter_method=1), _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-palette-missing-plte", "PXL006",
        "`§8.5`(色彩类型 3 **必须**有 `PLTE`)。",
        "色彩类型是 3(索引)却没有 `PLTE`。索引无从查表,必须拒绝。",
        _png([_ihdr(width, height, color_type=3),
              _chunk(b"IDAT", _zlib([bytes(x % 4 for x in range(width))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-plte-forbidden-gray", "PXL006",
        "`§8.5`(色彩类型 0 / 4 **不得**有 `PLTE`)。",
        "色彩类型 0(灰度)却带了 `PLTE`。灰度图没有索引,`PLTE` 的存在意味着"
        "两种读法(按灰度 / 按调色板)都能自圆其说,故必须拒绝。",
        _png([_ihdr(width, height, color_type=0),
              _chunk(b"PLTE", bytes(range(48))),
              _chunk(b"IDAT", _zlib([bytes(range(16))] * height)),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-trns-forbidden-rgba", "PXL006",
        "`§8.5`(色彩类型 4 / 6 出现 `tRNS` 即拒绝)。",
        "色彩类型 6(RGBA)带了 `tRNS`。RGBA 已有逐像素 alpha,`tRNS` 与它冲突,"
        "故「出现即拒绝」而不是「忽略」。",
        ct6([_chunk(b"tRNS", b"\x00\x00\x00\x00\x00\x00"),
              _chunk(b"IDAT", payload)]))

    negative_extract(
        "negative/neg-png-chunk-reserved-bit", "PXL006",
        "`§8.5`(chunk 类型名第三个字节的保留位必须为 0,即必须是大写字母)。",
        "IHDR 之后插入类型名 `tExt`:四个字节全是 ASCII 字母,但**第三字节 `x` 小写**"
        "→ 保留位为 1。必须拒绝。注意与 `neg-png-chunk-type-invalid`(类型名含数字)"
        "区分:那条只测「非字母」,测不到保留位。",
        _png([_ihdr(width, height),
              _chunk(b"tExt", b"payload"),
              _chunk(b"IDAT", payload), _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-plte-after-idat", "PXL006",
        "`§8.5`(`PLTE` 若存在必须早于第一个 `IDAT`)。",
        "chunk 顺序是 `IHDR → IDAT → PLTE → IEND`,CRC 全部正确、像素数据也可解码。"
        "§8.5 要求 `PLTE` 早于第一个 `IDAT`;不钉这条,「先收全文件再解码」与"
        "「流式解码」会分叉。",
        _png([_ihdr(width, height, color_type=3),
              _chunk(b"IDAT", _zlib([bytes(x % 4 for x in range(width))] * height)),
              _chunk(b"PLTE", bytes(range(48))),
              _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-ihdr-length", "PXL006",
        "`§8.5`(IHDR 的 chunk 数据长度必须恰为 13 字节)。",
        "IHDR 的数据部分只有 12 字节(最后一个字段 `interlace` 被截掉),长度字段也照实写 12。"
        "CRC 自洽,但长度不是 13 → 拒绝。",
        _png([_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)[:12]),
              _chunk(b"IDAT", payload), _chunk(b"IEND", b"")]))

    negative_extract(
        "negative/neg-png-iend-length", "PXL006",
        "`§8.5`(IEND 的 chunk 数据长度必须为 0)。",
        "IEND 带了 1 个字节的数据。§8.5 要求 IEND 的数据长度恰为 0。",
        _png([_ihdr(width, height), _chunk(b"IDAT", payload),
              _chunk(b"IEND", b"\x00")]))

    negative_extract(
        "negative/neg-png-trns-before-plte", "PXL006",
        "`§8.5`(存在 `PLTE` 时 `tRNS` 必须晚于 `PLTE`、早于首个 `IDAT`)。",
        "chunk 顺序是 `IHDR → tRNS → PLTE → IDAT → IEND`,CRC 与像素数据全对。"
        "这种 `IHDR → tRNS → PLTE` 的顺序非法,而不写这条规则就会出现"
        "「CRC 全对却没人拒绝」的索引图。",
        _png([_ihdr(width, height, color_type=3),
              _chunk(b"tRNS", b"\x00\x80"),
              _chunk(b"PLTE", bytes(range(48))),
              _chunk(b"IDAT", _zlib([bytes(x % 4 for x in range(width))] * height)),
              _chunk(b"IEND", b"")]))

    # 65536 个互不相同的非透明色 → 抽取侧 PXL005(色数超容量)
    overflow_rows = []
    for y in range(256):
        row = bytearray()
        for x in range(256):
            index = y * 256 + x
            row += bytes((index & 0xFF, (index >> 8) & 0xFF, (x * 5 + y) & 0xFF, 255))
        overflow_rows.append(bytes(row))
    negative_extract(
        "negative/neg-palette-overflow", "PXL005",
        "`§4.5`(`n >= 65536` 无档位可派生)、`§13.1`(抽取侧的 `PXL005`:色数超出该档容量)、"
        "`§3.5`(全部像素 alpha = 255,故没有任何 `00000000` 可并入透明 slot)。",
        "256×256 共 65536 个像素,每个都是**不同的非透明 RGBA 值**(alpha 恒 255,"
        "所以一个透明 slot 也没有)。尺寸 256×256 与面积 65536 都合规,"
        "去重后 `n = 65536` 大于 `index4` 容量 65535,只能由抽取器报 `PXL005`——"
        "这时**根本没有 PXL 文档可读**,因此本目录只有 `input.png`,没有 `input.pxl`"
        "(§13.1 的侧别规则;渲染侧的 `PXL005` 由 `neg-palette-lowercase` 等条目覆盖)。",
        _png([_ihdr(256, 256), _chunk(b"IDAT", _zlib(overflow_rows)),
              _chunk(b"IEND", b"")]))


# ==========================================================================
# 四、写出与 CLI
# ==========================================================================

def _render_notes(entry, width, height, digest):
    lines = [
        "# %s" % entry["case"],
        "",
        "- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)",
        "- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)",
        "- 尺寸:%dx%d" % (width, height),
        "- `expected.pixels.sha256`:`%s`" % digest,
        "",
        "## 覆盖条款",
        "",
        entry["clauses"],
        "",
        "## 期望值来源",
        "",
        entry["derive"],
        "",
        "## 判据",
        "",
        "§13.2b 的五条同时成立:",
        "",
        "1. **回渲像素一致**:渲染器读 `input.pxl`,产出的 RGBA8 字节流 SHA-256 等于",
        "   `expected.pixels.sha256`;",
        "2. **确定性**:同一文档两跑,PNG **文件**逐字节相同(§9);",
        "3. **不拘形式**:该文档本就不是规范抽取器能产出的形态(含 `pixel` / `line` /",
        "   `mirror` / `erase` / `stamp` 之一,或含非 `normal` 的 `blend`),渲染器必须照样接受"
        "(§3.8);",
        "4. **尺寸一致**:输出 PNG 的宽高等于 `input.pxl` 声明的值(摘要不含尺寸,必须单独断言);",
        "5. **输出结构合规**:8 bit / 色彩类型 6 / 非交错 / 所有行过滤器字节恒 0 /",
        "   chunk 类型序列 `IHDR` → `IDAT`⁺ → `IEND`(§10)。",
        "",
        "## 摘要怎么来的",
        "",
        "`expected.pixels.sha256` 由 `spec/conformance/tools/pxl_render_ref.py` —— "
        "**按规范另写**的",
        "渲染 oracle(不 import `importer`、不 import `tests/`)—— 对 `input.pxl` 渲染后",
        "按 §13.4 的字节流定义(逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充)",
        "计算,并经被测实现之外的第二条路径复核。生成命令:",
        "",
        "```",
        "<py> spec/conformance/tools/gen_conformance_cases.py %s" % entry["case"],
        "```",
    ]
    return "\n".join(lines) + "\n"


def _negative_notes(entry, side, subject, extra_lines=()):
    lines = [
        "# %s" % entry["case"],
        "",
        "- 形态:**负例**(§13.1)",
        "- **受测侧**:%s" % side,
        "- 受测输入:`%s`" % subject,
        "- 期望错误码:`%s`(见 `expected.error`,一行 + LF)" % entry["code"],
        "- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)",
        "",
        "## 覆盖条款",
        "",
        entry["clauses"],
        "",
        "## 缺陷(只有这一处)",
        "",
        entry["reason"],
    ]
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    lines.extend([
        "",
        "## 判据",
        "",
        "§13.2c:实现必须以 `%s` 失败,且**不产出任何输出文件**(不得留半份)。" % entry["code"],
    ])
    return "\n".join(lines) + "\n"


def build_case(entry):
    """构造一条用例的全部文件,返回 ``{文件名: 字节}``。

    正例的摘要由 oracle 现算:它既写进 `expected.pixels.sha256`,也写进 `notes.md`,
    因此两者永远一致。
    """
    case = entry["case"]
    if entry["kind"] == "render":
        payload = entry["text"].encode("utf-8")
        width, height, rgba = pxl_render_ref.render_text(payload)
        digest = pxl_render_ref.pixels_sha256(width, height, rgba)
        notes = _render_notes(entry, width, height, digest)
        return {
            "input.pxl": payload,
            "expected.pixels.sha256": (digest + "\n").encode("ascii"),
            "notes.md": notes.encode("utf-8"),
        }, width, height, rgba

    if entry["kind"] == "negative-render":
        payload = entry["text"].encode("utf-8")
        notes = _negative_notes(
            entry, "渲染侧(目录内只有 `input.pxl`)", "input.pxl",
            ["本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出"
             "期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。"])
        return {
            "input.pxl": payload,
            "expected.error": (entry["code"] + "\n").encode("ascii"),
            "notes.md": notes.encode("utf-8"),
        }, None, None, None

    if entry["kind"] == "negative-extract":
        payload = entry["png"]
        notes = _negative_notes(
            entry, "抽取侧(目录内只有 `input.png`,抽取器根本不读 PXL)", "input.png",
            ["`input.png` 实测 **%d 字节**;本目录没有 `input.pxl`,也没有任何期望像素文件。"
             % len(payload)])
        return {
            "input.png": payload,
            "expected.error": (entry["code"] + "\n").encode("ascii"),
            "notes.md": notes.encode("utf-8"),
        }, None, None, None

    raise AssertionError("未知形态: %r" % (entry["kind"],))


def _case_dir(case):
    parts = case.split("/")
    directory = CASES_DIR
    for part in parts:
        directory = directory / part
    return directory


def write_case(entry, check_only=False):
    """写(或校验)一条用例;返回 ``(状态, 说明)``。"""
    files, width, height, rgba = build_case(entry)
    directory = _case_dir(entry["case"])
    if check_only:
        problems = []
        for name in sorted(files):
            path = directory / name
            if not path.is_file():
                problems.append("缺少 %s" % name)
            elif path.read_bytes() != files[name]:
                problems.append("%s 与生成结果不一致" % name)
        return ("OK" if not problems else "FAIL", "; ".join(problems))
    directory.mkdir(parents=True, exist_ok=True)
    for name in sorted(files):
        with (directory / name).open("wb") as handle:
            handle.write(files[name])
    return ("OK", "%d 个文件" % len(files))


def all_entries():
    if not CASES:
        build_op_cases()
        negative_cases()
        extract_cases()
    return CASES


def _oracle_disagreement(entry):
    """用 oracle 复核负例的期望码(只作交叉核对,不影响判定)。"""
    if entry["kind"] != "negative-render":
        return None
    try:
        pxl_render_ref.render_text(entry["text"].encode("utf-8"))
    except pxl_render_ref.PxlRefError as error:
        if error.code != entry["code"]:
            return "oracle 报 %s(期望 %s):%s" % (error.code, entry["code"],
                                                error.message)
        return None
    return "oracle 未拒绝该文档(期望 %s)" % (entry["code"],)


def main(argv):
    args = argv[1:]
    check_only = False
    selected = []
    for item in args:
        if item == "--check":
            check_only = True
        elif item in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        elif item.startswith("-"):
            sys.stderr.write("未知选项: %s\n" % item)
            return 2
        else:
            selected.append(item)

    entries = all_entries()
    if selected:
        known = {entry["case"]: entry for entry in entries}
        missing = [name for name in selected if name not in known]
        if missing:
            sys.stderr.write("未知用例: %s\n可用: %s\n"
                             % (", ".join(missing),
                                ", ".join(entry["case"] for entry in entries)))
            return 2
        entries = [known[name] for name in selected]

    failures = 0
    disagreements = 0
    render_count = 0
    negative_count = 0
    print("用例 %d 条 → %s" % (len(entries), CASES_DIR.as_posix()))
    print("%-52s %-6s %s" % ("case", "状态", "说明"))
    for entry in entries:
        status, detail = write_case(entry, check_only=check_only)
        if status != "OK":
            failures += 1
        if entry["kind"] == "render":
            render_count += 1
        else:
            negative_count += 1
        note = ""
        disagreement = _oracle_disagreement(entry)
        if disagreement:
            disagreements += 1
            note = "  ← " + disagreement
        print("%-52s %-6s %s%s" % (entry["case"], status, detail, note))

    print("正例 %d 条 / 负例 %d 条" % (render_count, negative_count))
    if disagreements:
        print("注意:oracle 与 %d 条负例的期望码不一致(见上面的 ← 标注;"
              "判定以 expected.error 为准)" % disagreements)
    if check_only:
        print("校验完成:%s" % ("全部一致" if failures == 0 else "%d 条不一致" % failures))
        return 1 if failures else 0
    print("写出完成:%d 条" % len(entries))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))


