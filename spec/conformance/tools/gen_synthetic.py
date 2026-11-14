#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""22 条 `syn-` 往返正例的 fixture 构造器与 CLI。

用法(本机 `python` = 3.12、`py -3` = 3.9;**不要**用 `python3`,那是 Store 占位别名)::

    <py> spec/conformance/tools/gen_synthetic.py --all        生成全部 22 条(已存在则覆盖)
    <py> spec/conformance/tools/gen_synthetic.py <case>       生成一条,<case> 形如 `syn-16x16-mono`
    <py> spec/conformance/tools/gen_synthetic.py --verify     只校验,不写任何文件

约定:

* 每条用例的**意图像素(RGBA8)**是唯一真源;`expected.pixels.sha256` 由它按 §13.4
  (逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充)直接算出,写成小写十六进制 + 单个 LF。
  **绝不**把 PNG 解回来再算。
* 本文件**不写** `expected.pxl`(它必须由抽取器按附录 A 产出),也**不 import** `importer/`。
* 确定性:无时钟、无随机、无 locale、无目录枚举顺序(§9);同一配方任意次运行输出逐字节相同。
* 只写 `spec/conformance/tools/` 与 `spec/conformance/cases/syn-*/` 下的文件。
"""

import hashlib
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import png_ref  # noqa: E402  独立的 PNG 读取器(fixture 侧)
import png_write  # noqa: E402  独立的 PNG 写出器(fixture 侧)

CASES_DIR = TOOLS_DIR.parent / "cases"
MAX_INPUT_BYTES = 4 * 1024 * 1024  # §13.6:超过 4 MiB 的输入不入库

ALPHA_NAMES = ("R", "G", "B", "A")


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------

def _spec(width, height, color_type, png_desc, intent, rows, clauses, derive,
          extra="", plte=None, trns=None, extra_chunks=()):
    """一条用例的完整构造结果。"""
    if len(intent) != 4 * width * height:
        raise ValueError("意图 RGBA 长度 %d != 4*%d*%d" % (len(intent), width, height))
    return {
        "width": width,
        "height": height,
        "color_type": color_type,
        "png_desc": png_desc,
        "intent": intent,
        "rows": rows,
        "plte": plte,
        "trns": trns,
        "extra_chunks": tuple(extra_chunks),
        "clauses": clauses,
        "derive": derive,
        "extra": extra,
    }


def _row_rgba(width, runs):
    """按 `[(RGBA bytes, 长度), ...]` 拼出一行意图像素。"""
    buf = bytearray()
    for pixel, length in runs:
        buf += pixel * length
    if len(buf) != width * 4:
        raise ValueError("行宽 %d 字节 != %d" % (len(buf), width * 4))
    return bytes(buf)


def _rows_ct6(intent, width, height):
    """色彩类型 6 的行 = 意图像素逐行切片(打包是恒等映射)。"""
    stride = 4 * width
    return [intent[y * stride:(y + 1) * stride] for y in range(height)]


def _rows_ct2(intent, width, height, trns_rgb=None):
    """色彩类型 2 的行:只留 RGB;alpha 必须能由 tRNS 解释。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            offset = (y * width + x) * 4
            r, g, b, a = intent[offset], intent[offset + 1], intent[offset + 2], intent[offset + 3]
            if a == 255:
                pass
            elif a == 0 and trns_rgb is not None and (r, g, b) == trns_rgb:
                pass
            else:
                raise ValueError("色彩类型 2 无法表达该 alpha: (r,g,b,a)=%r" % ((r, g, b, a),))
            row += bytes((r, g, b))
        rows.append(bytes(row))
    return rows


def _rows_ct0(intent, width, height, trns_gray=None):
    """色彩类型 0 的行:只留灰度样本;alpha 必须能由 tRNS 解释。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            offset = (y * width + x) * 4
            r, g, b, a = intent[offset], intent[offset + 1], intent[offset + 2], intent[offset + 3]
            if not (r == g == b):
                raise ValueError("色彩类型 0 要求 r=g=b: (r,g,b)=%r" % ((r, g, b),))
            if a == 255:
                pass
            elif a == 0 and trns_gray is not None and r == trns_gray:
                pass
            else:
                raise ValueError("色彩类型 0 无法表达该 alpha: (gray,a)=%r" % ((r, a),))
            row.append(r)
        rows.append(bytes(row))
    return rows


def _rows_ct4(intent, width, height):
    """色彩类型 4 的行:(灰度, alpha);要求 r=g=b。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            offset = (y * width + x) * 4
            r, g, b, a = intent[offset], intent[offset + 1], intent[offset + 2], intent[offset + 3]
            if not (r == g == b):
                raise ValueError("色彩类型 4 要求 r=g=b: (r,g,b)=%r" % ((r, g, b),))
            row += bytes((r, a))
        rows.append(bytes(row))
    return rows


def _palette_case(palette_rgb, index_rows, trns=None):
    """色彩类型 3:由「PLTE + 索引行 + 可选 tRNS」按 §8.2 展开出意图像素。"""
    plte = b"".join(bytes(color) for color in palette_rgb)
    table = []
    for index, (r, g, b) in enumerate(palette_rgb):
        alpha = trns[index] if trns is not None and index < len(trns) else 255
        table.append(bytes((r, g, b, alpha)))
    intent = b"".join(b"".join(table[i] for i in row) for row in index_rows)
    return plte, intent


def count_colors(intent):
    """非透明 texel 值的去重个数(§3.5:只有四通道全零才不算)。"""
    transparent = b"\x00\x00\x00\x00"
    seen = set()
    for offset in range(0, len(intent), 4):
        pixel = intent[offset:offset + 4]
        if pixel != transparent:
            seen.add(pixel)
    return len(seen)


# --------------------------------------------------------------------------
# 1x1 / 纯色 / 分块压缩类
# --------------------------------------------------------------------------

def case_1x1_transparent():
    intent = bytes((0x00, 0x00, 0x00, 0x00))
    return _spec(
        width=1, height=1, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、无 PLTE/tRNS/额外 chunk",
        intent=intent, rows=_rows_ct6(intent, 1, 1),
        clauses="`§4.5`(n = 0 的最小情形)、`§7`(只有四通道全零才是透明 slot)、"
                "附录 A 第 2 步(整图同一 slot → 单条 `fill`)。",
        derive="唯一像素 `00000000`:四通道全零 → slot 0。n = 0,留在 char 档;"
               "整图只有同一个 slot,故 `ops = [fill slot=0]`。RGBA8 流恰 4 字节。",
    )


def case_1x1_opaque():
    intent = bytes((0xFF, 0x00, 0x00, 0xFF))
    return _spec(
        width=1, height=1, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、无 PLTE/tRNS/额外 chunk",
        intent=intent, rows=_rows_ct6(intent, 1, 1),
        clauses="`§4.5`(n = 1)、`§4.2`(char 档首字符 `0` 即 slot 1)、`§5.1`(`fill`)、"
                "`§5.0.1`(最小画布)。",
        derive="唯一像素 `FF0000FF`:非全零且 A = 255 → 非透明,拿到 slot 1;"
               "palette = [\"FF0000\"],n = 1 → char;单条 `fill slot=1`。",
    )


def case_16x16_mono():
    width = height = 16
    pixel = bytes((0x33, 0x66, 0x99, 0xFF))
    intent = pixel * (width * height)
    return _spec(
        width=width, height=height, color_type=2,
        png_desc="色彩类型 2(RGB)、8 bit、非交错、行过滤器恒 0、每行 48 字节、无 tRNS",
        intent=intent, rows=_rows_ct2(intent, width, height),
        clauses="`§8.2` 第 3 行(色彩类型 2 → a = 255)、`§4.5`(n = 1)、"
                "`§5.1`/附录 A 第 2 步(整图纯色 → 单条 `fill`)、`AGENTS.md` 契约 5(必须压缩)。",
        derive="256 个像素全是 `336699` + a = 255;n = 1 → char 档;"
               "全图同 slot → `fill slot=1`,不得输出 256 条 `rect` 或一条 `grid`。",
    )


def case_16x16_tricolor():
    width = height = 16
    color_a = bytes((0xF0, 0x00, 0x00, 0xFF))
    color_b = bytes((0x00, 0xC0, 0x40, 0xFF))
    color_c = bytes((0x00, 0x40, 0xF0, 0xFF))
    cycle = (color_a, color_b, color_c)
    rows = []
    for y in range(height):
        if y < 4:                                    # 4 条整行色带:段长 16
            rows.append(_row_rgba(width, [(cycle[y % 3], width)]))
        elif y < 10:                                 # 6 行 1 px 交错:段长 1
            rows.append(_row_rgba(width, [((color_a if (x + y) % 2 == 0 else color_b), 1)
                                          for x in range(width)]))
        else:                                        # 6 条整行色带
            rows.append(_row_rgba(width, [(cycle[(y - 10) % 3], width)]))
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct6(intent, width, height),
        clauses="`§4.4`(调色板按行优先首次出现顺序:第 0 行 `F00000`、第 1 行 `00C040`、"
                "第 2 行 `0040F0`)、`§4.5`(n = 3 → char)、附录 A 第 3–6 步(RUNNER / GRID 块切分)。",
        derive="第 0–3 行各是一整条段(段长 16 ≥ MIN_RUN = 3)→ RUNNER 行,slot 逐行不同故不能"
               "垂直合并 → 4 条 `rect`(w = 16, h = 1);第 4–9 行是 1 px 交错(段长 1)→ GRID 行,"
               "一个块合成 1 条 `grid`(y = 4, 6 行);第 10–15 行同为色带 → 6 条 `rect`。"
               "ops 合计 4 + 1 + 6 = 11 条。",
    )


_ODD_COLORS = (
    bytes((0xE0, 0x10, 0x10, 0xFF)),
    bytes((0x10, 0xE0, 0x10, 0xFF)),
    bytes((0x10, 0x10, 0xE0, 0xFF)),
    bytes((0xE0, 0xE0, 0x10, 0xFF)),
)

#: 17 宽的 13 行,每行是 `[(调色板序号, 段长), ...]`;行宽恒 17。
_ODD_ROW_RUNS = (
    ((0, 4), (1, 5), (2, 8)),                                        # 段长 4/5/8 → RUNNER
    ((0, 2), (1, 2), (2, 2), (3, 2), (0, 2), (1, 2), (2, 2), (3, 2), (0, 1)),  # 段长 2 → GRID
    ((0, 3), (1, 3), (2, 3), (3, 3), (0, 3), (1, 2)),                # 段长 3 收尾 2 → GRID
    ((3, 17),),                                                      # 单段 17 → RUNNER
    ((2, 2), (3, 2), (0, 2), (1, 2), (2, 2), (3, 2), (0, 2), (1, 2), (2, 1)),  # GRID
    ((3, 5), (0, 5), (1, 7)),                                        # RUNNER
    ((1, 4), (2, 4), (3, 4), (0, 4), (1, 1)),                        # GRID
    ((2, 3), (3, 3), (0, 3), (1, 3), (2, 3), (3, 2)),                # GRID
    ((0, 2), (2, 2), (1, 2), (3, 2), (0, 2), (2, 2), (1, 2), (3, 2), (1, 1)),  # GRID
    ((0, 9), (2, 8)),                                                # RUNNER
    ((3, 4), (0, 4), (1, 4), (3, 4), (2, 1)),                        # GRID
    ((0, 2), (1, 3), (2, 3), (3, 3), (0, 3), (1, 3)),                # GRID
    ((2, 6), (3, 6), (0, 5)),                                        # RUNNER
)


def case_17x13_odd():
    width, height = 17, 13
    rows = [_row_rgba(width, [(_ODD_COLORS[index], length) for index, length in runs])
            for runs in _ODD_ROW_RUNS]
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、每行 68 字节",
        intent=intent, rows=_rows_ct6(intent, width, height),
        clauses="`§4.5`(n = 4 → char)、`§5.0`(rect 全在界内,无需裁剪)、"
                "附录 A 第 3–4 步(奇数宽度与奇数行数下的块切分)、`§5.0.2`(面积 221)。",
        derive="行配方见 `_ODD_ROW_RUNS`(每行段长之和恒为 17)。第 0/3/5/9/12 行的所有段长都"
               "≥ 3 → RUNNER 行;其余行含段长 1 或 2 的段(含段长 2 的行即此处)→ GRID 行。"
               "块切分:行 [0] [1,2] [3] [4] [5] [6,7,8] [9] [10,11] [12],共 9 个块,"
               "其中 **4** 个 GRID 块([1,2] [4] [6,7,8] [10,11])各合成 1 条 `grid`,"
               "y 分别是 1、4、6、10;其余 5 个 RUNNER 块产出 `rect`。色彩两两不同 → n = 4。",
    )


def case_64x32_nonsquare():
    width, height = 64, 32
    bands = (
        bytes((0xFF, 0x00, 0x00, 0xFF)),
        bytes((0x00, 0xFF, 0x00, 0xFF)),
        bytes((0x00, 0x00, 0xFF, 0xFF)),
        bytes((0xFF, 0xFF, 0x00, 0xFF)),
        bytes((0xFF, 0x00, 0xFF, 0xFF)),
        bytes((0x00, 0xFF, 0xFF, 0xFF)),
        bytes((0x80, 0x80, 0x80, 0xFF)),
        bytes((0x00, 0x00, 0x00, 0xFF)),
    )
    row = _row_rgba(width, [(bands[index], 8) for index in range(8)])
    intent = row * height
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、每行 256 字节",
        intent=intent, rows=_rows_ct6(intent, width, height),
        clauses="`§4.5`(n = 8 → char)、`§5.2`(`rect` 的右开区间与 `h`)、"
                "附录 A 第 5 步(同块内 `(x, w, slot)` 相同 → `h` 加 1)、`§5.0.2`(面积 2048)。",
        derive="8 条 8 px 竖带贯穿全高:每行 8 段、段长恒 8(≥ 3)→ 32 行全是 RUNNER 行、"
               "整图 1 个 RUNNER 块。同一带在每一行的 `(x, w, slot)` 完全相同 → 垂直合并成 "
               "8 条 `rect`(w = 8, h = 32)。",
    )


def case_16x512_strip():
    width, height = 16, 512
    colors = (
        bytes((0xFF, 0x00, 0x00, 0xFF)),
        bytes((0x00, 0xFF, 0x00, 0xFF)),
        bytes((0x00, 0x00, 0xFF, 0xFF)),
        bytes((0xFF, 0xFF, 0x00, 0xFF)),
        bytes((0xFF, 0x00, 0xFF, 0xFF)),
        bytes((0x00, 0xFF, 0xFF, 0xFF)),
    )

    def row_of(kind):
        if kind == 0:      # 4 段 × 4 px
            return _row_rgba(width, [(colors[0], 4), (colors[1], 4), (colors[2], 4), (colors[3], 4)])
        if kind == 1:      # 1 px 交错
            return _row_rgba(width, [((colors[4] if x % 2 == 0 else colors[5]), 1)
                                     for x in range(width)])
        if kind == 2:      # 2 段 × 8 px
            return _row_rgba(width, [(colors[0], 8), (colors[1], 8)])
        if kind == 3:      # 段长 2
            return _row_rgba(width, [(colors[(start // 2) % 6], 2) for start in range(0, width, 2)])
        if kind == 4:      # 5 / 5 / 6
            return _row_rgba(width, [(colors[1], 5), (colors[2], 5), (colors[3], 6)])
        # kind == 5:7 / 1 / 8,一个孤立像素造出段长 1
        return _row_rgba(width, [(colors[4], 7), (colors[5], 1), (colors[4], 8)])

    rows = []
    for y in range(height):
        frame = y // 16
        rows.append(row_of((y % 16 + frame) % 6))
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、每行 64 字节",
        intent=intent, rows=_rows_ct6(intent, width, height),
        clauses="`§4.5`(n = 6 → char)、`§5.0.2`(面积 16 × 512 = 8192 ≤ 2^22)、"
                "附录 A 第 4–6 步(纵向多块切分;`grid` 的 `y` 取块首行)、"
                "`§3.4`(`grid` 每行 token 数恒等于宽)。",
        derive="每 16 行是一帧,帧内按 `(y % 16 + 帧号) % 6` 轮转 6 种行型:行型 0/2/4 的段长"
               "全 ≥ 3(RUNNER),行型 1/3/5 含段长 1 或 2(GRID)。帧号参与轮转,好让相邻帧不"
               "总以同一行型开始,从而在同一竖列上既产生可垂直合并的 `rect`,也产生多段 GRID 块。"
               "6 个颜色全部出现 → n = 6。",
    )


# --------------------------------------------------------------------------
# 档位 / 大色数
# --------------------------------------------------------------------------

def case_300_gradient():
    width, height = 20, 15
    rows = []
    for y in range(height):
        rows.append(b"".join(bytes(((12 * x) & 0xFF, (17 * y) & 0xFF, (5 * (x + y)) & 0xFF, 0xFF))
                             for x in range(width)))
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、每行 80 字节",
        intent=intent, rows=_rows_ct6(intent, width, height),
        clauses="`§4.5`(n = 300 → 跳过 index 直接 index4)、`§4.3`(index4 token 宽 4、大写零填充、"
                "`0000` 保留给透明)、`§4.4`(行优先首次出现顺序即扫描顺序)、"
                "附录 A 第 6 步(全 GRID → 一条 `grid`)。",
        derive="像素 (x, y) 的颜色是 `(12x, 17y, 5(x+y))`,a = 255:x 取 0..19 使 r 互不相同,"
               "y 取 0..14 使 g 互不相同,于是 20 × 15 = 300 个像素两两不同 → n = 300。"
               "相邻列必不同 → 每行段长恒 1 → 全部 GRID 行且只有 1 个块 → 单条 `grid`"
               "(x = 0, y = 0, 15 行)。",
    )


def _tier_pixel(index):
    """第 index 个非透明色:r = index 保证互不相同。"""
    return bytes((index & 0xFF, (2 * index + 1) & 0xFF, (5 * index + 3) & 0xFF, 0xFF))


def _tier_case(total, clauses, derive):
    intent = b"".join(_tier_pixel(index) for index in range(total))
    return _spec(
        width=total, height=1, color_type=6,
        png_desc="色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、单行 %d 字节" % (4 * total),
        intent=intent, rows=_rows_ct6(intent, total, 1),
        clauses=clauses, derive=derive,
    )


def case_tier_61():
    return _tier_case(
        61,
        "`§4.5`(恰好 61 色必须留在 char 档)、`§4.2`(char 字符集 61 个,`z` 保留不用)、"
        "`§4.4`(palette 顺序 = 首次出现顺序)、附录 A 第 6 步。",
        "第 k 个像素(0 ≤ k ≤ 60)用第 k 个调色板颜色 `(k, 2k+1, 5k+3)`,故 palette 次序与像素"
        "次序一致、slot k+1 对应第 k 个像素。n = 61 ≤ 61 → char 档;slot 61 的 token 是字符集"
        "最后一个字符 `y`(字符集 `0-9A-Za-y` 共 61 个;`z` 保留不用)。整行段长恒 1 → 一条 `grid`。",
    )


def case_tier_62():
    return _tier_case(
        62,
        "`§4.5`(62 色必须升到 index 档)、`§4.1`(char 档容量 62 − 1 = 61)、`§4.3`(两位大写十六进制)。",
        "62 个像素 62 个互不相同的颜色;n = 62 > 61 → 必须升到 index:char 档 62 个值里有一个"
        "已被透明 token `.` 占用,物理上放不下第 62 个非透明色。整行段长恒 1 → 一条 `grid`,"
        "token 为 `01`..`3E`。",
    )


def case_tier_255():
    return _tier_case(
        255,
        "`§4.5`(255 色仍在 index 档)、`§4.1`(index 档容量 256 − 1 = 255)、"
        "`§4.3`(`00` 保留给透明,最后一个是 `FF`)。",
        "255 个像素 255 个互不相同的颜色;n = 255 = 256 − 1 → 仍是 index:透明 `00` 加 255 个"
        "非透明 slot 恰好用满两位十六进制的 256 个值。整行段长恒 1 → 一条 `grid`。",
    )


def case_tier_256():
    return _tier_case(
        256,
        "`§4.5`(256 色必须升到 index4)、`§4.1`(index 档放不下 256 个非透明色)、"
        "`§4.3`(四位大写十六进制、`0000` 保留给透明)。",
        "256 个像素 256 个互不相同的颜色;n = 256 > 255 → 必须升到 index4:index 档 256 个值里 "
        "`00` 已被透明占用。整行段长恒 1 → 一条 `grid`,token 为 `0001`..`0100`。",
    )


# --------------------------------------------------------------------------
# PNG 色彩类型覆盖
# --------------------------------------------------------------------------

def case_png_gray0():
    width = height = 16
    stops = (0x00, 0x55, 0xAA, 0xFF)
    rows = []
    for y in range(height):
        gray = stops[y // 4]
        rows.append(bytes((gray, gray, gray, 0xFF)) * width)
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=0,
        png_desc="色彩类型 0(灰度)、8 bit、非交错、行过滤器恒 0、无 PLTE/tRNS/额外 chunk",
        intent=intent, rows=_rows_ct0(intent, width, height),
        clauses="`§8.1`(色彩类型 0 属于接受集)、`§8.2` 第 1 行(无 tRNS:r = g = b = 样本、a = 255)、"
                "`§4.5`(n = 4)、`§7`(灰度 0 但 a = 255 不算透明)。",
        derive="第 y 行整行取 `stops[y // 4]`,stops = (0x00, 0x55, 0xAA, 0xFF),即 4 条 4 行高的横带。"
               "展开后 n = 4:第一行的 `(0,0,0,255)` 不是四通道全零,仍占自己的非透明 slot。"
               "每行段长 16 → RUNNER 行,slot 逐带不同 → 4 条 `rect`(w = 16, h = 4)。",
    )


def case_png_gray_trns():
    width = height = 16
    key = 0x80
    hit = bytes((key, key, key, 0x00))
    miss = bytes((0x00, 0x00, 0x00, 0xFF))
    row = _row_rgba(width, [(hit, 8), (miss, 8)])
    intent = row * height
    return _spec(
        width=width, height=height, color_type=0,
        png_desc="色彩类型 0(灰度)+ tRNS 2 字节 `0080`、8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct0(intent, width, height, trns_gray=key),
        trns=b"\x00\x80",
        clauses="`§8.5`(色彩类型 0 的 tRNS 必须恰 2 字节,值按大端与样本比较)、"
                "`§8.2` 第 2 行(命中 → a = 0,RGB 原样)、`§7` 与 `§3.5`(RGB 非零而 A = 0 的像素"
                "保留自己的 slot,不得并入透明 slot)。",
        derive="tRNS 按大端读出 = 0x0080 = 128。列 0–7 的样本是 128,命中 → `(128,128,128,0)`;"
               "列 8–15 的样本是 0,未命中 → `(0,0,0,255)`。n = 2:`(128,128,128,0)` 不是四通道"
               "全零,按 §3.5 占自己的 slot。每行两段、段长 8 → 2 条 `rect`(h = 16)。",
    )


def case_png_gray_trns_highbyte():
    """tRNS 的高字节非 0：按 PNG 标准掩掉高位后仍然命中（§8.5 已更正）。"""
    width = height = 16
    key = 0x80
    hit = bytes((key, key, key, 0x00))
    miss = bytes((0x00, 0x00, 0x00, 0xFF))
    row = _row_rgba(width, [(hit, 8), (miss, 8)])
    intent = row * height
    return _spec(
        width=width, height=height, color_type=0,
        png_desc="色彩类型 0(灰度)+ tRNS 2 字节 `0180`(高字节非 0)、8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct0(intent, width, height, trns_gray=key),
        trns=b"\x01\x80",
        clauses="`§8.5`(色彩类型 0 的 tRNS 必须**先按样本深度掩掉高位**再比较 —— PNG 第三版 "
                "§11.3.2.1)、`§8.2` 第 2 行(命中 → a = 0,RGB 原样)、`§7` 与 `§3.5`。",
        derive="tRNS = 0x0180,位深 8 → 有效比较值 `0x0180 & 0xFF = 0x80`。列 0–7 的样本是 128,"
               "**命中** → `(128,128,128,0)`;列 8–15 的样本是 0,未命中 → `(0,0,0,255)`。"
               "早前规范按 16 位整值比较(0x0180 ≠ 0x80,不命中),与 PNG 标准冲突,已更正。",
    )


_PALETTE8 = (
    (0xFF, 0x00, 0x00),
    (0x00, 0xFF, 0x00),
    (0x00, 0x00, 0xFF),
    (0xFF, 0xFF, 0x00),
    (0xFF, 0x00, 0xFF),
    (0x00, 0xFF, 0xFF),
    (0x12, 0x34, 0x56),
    (0xAB, 0xCD, 0xEF),
)


def _palette8_index_rows(width, height):
    """索引 = x // 2,于是 8 个索引各占 2 列、全部用上。"""
    return [bytes(x // 2 for x in range(width)) for _ in range(height)]


def case_png_palette():
    width = height = 16
    index_rows = _palette8_index_rows(width, height)
    plte, intent = _palette_case(_PALETTE8, index_rows)
    return _spec(
        width=width, height=height, color_type=3,
        png_desc="色彩类型 3(索引)+ PLTE 8 项(24 字节)、8 bit、非交错、行过滤器恒 0、无 tRNS",
        intent=intent, rows=index_rows, plte=plte,
        clauses="`§8.1`(色彩类型 3 必须有 PLTE)、`§8.2` 第 6 行(索引查 PLTE、a = 255)、"
                "`§4.5`(n = 8 → char)、`§8.5`(PLTE 长度必须是 3 的倍数、项数 1..256)。",
        derive="第 x 列的样本索引是 `x // 2`,8 个索引 0..7 各占 2 列、全部被用到;每像素展开为 "
               "`(PLTE[3i], PLTE[3i+1], PLTE[3i+2], 255)`。8 个颜色两两不同 → n = 8。"
               "每行段长恒 2(< MIN_RUN = 3)→ 全部 GRID 行 → 单条 `grid`。",
    )


def _palette5_index_rows(width, height):
    """索引 = `(x // 2) % 5`,只落到 0..4 这 5 个(PLTE 仍是 8 项,与 CASES.tsv 的 colors=5 对齐)。"""
    return [bytes((x // 2) % 5 for x in range(width)) for _ in range(height)]


def case_png_palette_trns():
    width = height = 16
    index_rows = _palette5_index_rows(width, height)
    # tRNS 只给 3 项:索引 0/1/2 落在范围内,索引 3/4 越界 → alpha 255
    trns = bytes((0x00, 0x80, 0xFF))
    plte, intent = _palette_case(_PALETTE8, index_rows, trns=trns)
    return _spec(
        width=width, height=height, color_type=3,
        png_desc="色彩类型 3(索引)+ PLTE 8 项 + tRNS 3 字节 `0080FF`、8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=index_rows, plte=plte, trns=trns,
        clauses="`§8.2` 第 5 行(tRNS 的**两条**分支:范围内取该项 alpha、`i >= tRNS 长度` 时 255)、"
                "`§8.5`(tRNS 长度不得超过 PLTE 项数)、`§3.5` 与 `§7`(RGB 非零而 A = 0 仍占 slot)、"
                "`§4.5`(n = 5 → char)。",
        derive="PLTE 保留 8 项,tRNS 只给 3 项(`00` / `80` / `FF`),像素只使用索引 0..4:第 x 列的"
               "索引是 `(x // 2) % 5`,每段 2 列、段长恒 2。两条 tRNS 分支各由不同的索引覆盖:"
               "索引 0 / 1 / 2 落在 tRNS 长度内 → alpha 取该项,依次是 0 / 128 / 255(索引 0 得到 "
               "`(255,0,0,0)`:RGB 非零而 A = 0,按 §3.5 保留自己的 slot,不是透明 slot);"
               "索引 3 / 4 的 `i = 3, 4 >= tRNS 长度 3` → alpha 按 255。"
               "5 个 texel 两两不同 → n = 5。每行段长 2 → 全部 GRID 行 → 单条 `grid`。",
    )


_GRAY_ALPHA_PAIRS = (
    (0x20, 0x00),
    (0x40, 0x40),
    (0x60, 0x80),
    (0x80, 0xC0),
    (0xA0, 0xFF),
    (0xFF, 0x7F),
)


def case_png_gray_alpha():
    width = height = 16
    rows = []
    for y in range(height):
        gray, alpha = _GRAY_ALPHA_PAIRS[y % 6]
        rows.append(bytes((gray, gray, gray, alpha)) * width)
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=4,
        png_desc="色彩类型 4(灰度 + alpha)、8 bit、非交错、行过滤器恒 0、无 PLTE/tRNS",
        intent=intent, rows=_rows_ct4(intent, width, height),
        clauses="`§8.1`(色彩类型 4:不得有 PLTE 或 tRNS)、`§8.2` 第 7 行(r = g = b = 样本 0、"
                "a = 样本 1)、`§3.5`(A = 0 但灰度非零 → 仍是非透明 slot)、`§4.5`(n = 6)。",
        derive="第 y 行整行取 `_GRAY_ALPHA_PAIRS[y % 6]`,组合为 (0x20,0x00) (0x40,0x40) "
               "(0x60,0x80) (0x80,0xC0) (0xA0,0xFF) (0xFF,0x7F)。展开成 `(g,g,g,a)` 后 6 个 "
               "texel 两两不同 → n = 6;第一行 `(32,32,32,0)` 的 A = 0 但灰度非零,按 §3.5 不并入"
               "透明 slot。每行段长 16 → 16 条 `rect`(相邻行 slot 不同,不能垂直合并)。",
    )


def case_png_rgb_trns():
    width = height = 16
    hit_rgb = (0x00, 0xFF, 0x00)
    color_a = bytes((0x11, 0x22, 0x33, 0xFF))
    color_b = bytes((hit_rgb[0], hit_rgb[1], hit_rgb[2], 0x00))
    color_c = bytes((0x44, 0x55, 0x66, 0xFF))
    row = _row_rgba(width, [(color_a, 6), (color_b, 5), (color_c, 5)])
    intent = row * height
    return _spec(
        width=width, height=height, color_type=2,
        png_desc="色彩类型 2(RGB)+ tRNS 6 字节 `0000 00FF 0000`、8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct2(intent, width, height, trns_rgb=hit_rgb),
        trns=struct.pack(">HHH", 0x0000, 0x00FF, 0x0000),
        clauses="`§8.5`(色彩类型 2 的 tRNS 必须恰 6 字节;命中三元组 → alpha = 0 且 RGB 原样)、"
                "`§8.2` 第 4 行、`§7` 与 `§3.5`(命中像素是非透明的\"不可见色\")、`§4.5`(n = 3)。",
        derive="三色竖带:列 0–5 是 `112233`,列 6–10 是 `00FF00`(与 tRNS 三元组相同 → a = 0),"
               "列 11–15 是 `445566`。展开后 3 个 texel 两两不同 → n = 3,其中 `(0,255,0,0)` 正是"
               "§7 关心的\"RGB 非零而 A = 0\"。每行三段、段长 6/5/5(全 ≥ 3)→ RUNNER 行,"
               "整图 1 个 RUNNER 块 → 3 条 `rect`(h = 16)。",
    )


def case_png_two_text_chunks():
    width = height = 16
    top = bytes((0x33, 0x66, 0x99, 0xFF))
    bottom = bytes((0xCC, 0x33, 0x00, 0xFF))
    rows = [top * width] * 8 + [bottom * width] * 8
    intent = b"".join(rows)
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)+ 两条 `tEXt`(关键词 `Comment` 与 `Software`,不参与像素)、"
                 "8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct6(intent, width, height),
        extra_chunks=(
            (b"tEXt", b"Comment\x00first ancillary text chunk"),
            (b"tEXt", b"Software\x00texel conformance fixture generator"),
        ),
        clauses="`§8.5`(重复约束按\"该 chunk 是否被读取\"分类:ancillary 出现任意次都接受、内容一律忽略)、"
                "`§8.1`(ancillary chunk 接受并忽略,不得因其存在而报错)。",
        derive="上半 8 行是 `336699`、下半 8 行是 `CC3300`,均 a = 255 → 2 个互不相同的非透明 texel "
               "值,n = 2;n ≤ 61 → char 档。每行段长 16 → 全部 RUNNER 行,上下两块 slot 不同 → "
               "2 条 `rect`(w = 16,h = 8)。两条 `tEXt` 的关键词不同(`Comment` / `Software`),"
               "按 §8.5 都属于\"不被读取\"的 ancillary,重复出现必须接受、内容一律忽略。",
        extra="**判据**:本用例的判据是\"两条 `tEXt` 必须被接受并忽略\",因此它的 `expected.pxl` "
              "必须与**像素完全相同、不带任何 `tEXt` 的版本**逐字节相同——两条关键词不同的 `tEXt` "
              "不得改变抽取结果的任何一个字节。(不带 `tEXt` 的那份不入库,判据靠这条文字表述与"
              "抽取器的实现共同保证。)它是 `neg-png-duplicate-chunk`(重复的**被读取** chunk 必须"
              "拒绝)的对照。",
    )


# --------------------------------------------------------------------------
# ops 预算回退 / 大 grid / 未知 ancillary
# --------------------------------------------------------------------------

def case_ops_fallback():
    width, height = 65535, 4
    # 65535 = 3 × 21845:每行恰好 21845 段、段长恒为 3
    segments = 21845
    low = b"\x00\x00\x00"
    high = b"\xff\xff\xff"
    even_row = (low + high) * (segments // 2) + low      # 21845 段(奇数段,以 low 起、low 收)
    odd_row = (high + low) * (segments // 2) + high      # 相邻行反色
    gray_rows = [even_row if (y % 2 == 0) else odd_row for y in range(height)]
    table = [bytes((value, value, value, 0xFF)) for value in range(256)]
    intent = b"".join(b"".join(table[sample] for sample in row) for row in gray_rows)
    return _spec(
        width=width, height=height, color_type=0,
        png_desc="色彩类型 0(灰度)、8 bit、非交错、行过滤器恒 0、无 tRNS;"
                 "每行解压后 65536 字节(1 过滤器字节 + 65535 样本)",
        intent=intent, rows=gray_rows,
        clauses="`§5.0.2`(接受的 PNG 一定能抽成合法 PXL:每层 ops ≤ `OPS_CAP`)、"
                "附录 A 第 8 步(ops 超预算 → 整层退化为一条覆盖全图的 `grid`)、"
                "`§8.1`(宽度 65535 ≤ 65536、面积 ≤ 2^22 故合法)。",
        derive="65535 = 3 × 21845,故每行恰好 21845 段、段长恒为 3(≥ MIN_RUN = 3)→ 每行都是 "
               "RUNNER 行。相邻行反色:第 0 行以 `00` 起、第 1 行以 `FF` 起,同一列的相邻两行 "
               "slot 不同 → 不能垂直合并。于是常规输出是 21845 × 4 = 87380 条 `rect`,"
               "超过 `OPS_CAP = 32768`,整层按附录 A 第 8 步回退为一条覆盖全图的 `grid`。"
               "灰度 0x00 → `(0,0,0,255)`、0xFF → `(255,255,255,255)` → n = 2。",
        extra="合法性:宽 65535 ≤ 65536,面积 65535 × 4 = 262140 ≤ 2^22(§5.0.2),"
              "所以这是**合法输入**,不能靠拒绝来绕开 ops 上界。",
    )


def case_grid_large_index4():
    width = height = 512
    palette = [((k & 0xFF), ((255 - k) & 0xFF), ((k * 7) & 0xFF)) for k in range(256)]
    index_rows = []
    for y in range(height):
        index_rows.append(bytes(((x * 37 + y * 91) & 0xFF) for x in range(width)))
    plte, intent = _palette_case(palette, index_rows)
    return _spec(
        width=width, height=height, color_type=3,
        png_desc="色彩类型 3(索引)+ PLTE 256 项(768 字节)、8 bit、非交错、行过滤器恒 0、无 tRNS",
        intent=intent, rows=index_rows, plte=plte,
        clauses="`§5.0.2`(大尺寸 + 4 位 token 的组合必须仍产出合法 PXL)、"
                "`§4.5`(n = 256 → index4)、附录 A 第 6 步(全 GRID → 一条 `grid`)、"
                "`§8.5`(索引不得越过 PLTE 项数)。",
        derive="样本索引 = `(37x + 91y) mod 256`。37 与 256 互素,故固定 y 时 x 取 0..511 会让 "
               "`37x mod 256` 取遍全部 256 个值;相邻两列差 37 ≢ 0 → 每行段长恒 1 → 全部 GRID 行、"
               "只有 1 个块 → 单条 `grid`(x = 0, y = 0, 512 行,每行 512 个 4 位 token)。"
               "PLTE 第 k 项是 `(k, 255-k, 7k mod 256)`:r 随 k 严格不同 → 256 个颜色两两不同 "
               "→ n = 256。面积 512 × 512 = 262144 ≤ 2^22。",
    )


def case_png_unknown_ancillary():
    width = height = 16
    left = bytes((0x12, 0x34, 0x56, 0xFF))
    right = bytes((0x65, 0x43, 0x21, 0xFF))
    row = _row_rgba(width, [(left, 8), (right, 8)])
    intent = row * height
    return _spec(
        width=width, height=height, color_type=6,
        png_desc="色彩类型 6(RGBA)+ 一个未知 ancillary chunk `prVt`(内容不参与像素)、"
                 "8 bit、非交错、行过滤器恒 0",
        intent=intent, rows=_rows_ct6(intent, width, height),
        extra_chunks=((b"prVt", b"unknown-ancillary\x00payload ignored"),),
        clauses="`§8.5`(未知 ancillary chunk:首字母小写 → 忽略内容、重复出现也接受)、"
                "`§8.5`(chunk 类型名必须恰为 4 个 ASCII 字母且第三字节保留位为 0)、"
                "`§8.1`(ancillary chunk 接受并忽略)。",
        derive="左 8 列 `123456`、右 8 列 `654321`,均 a = 255 → n = 2;每行两段、段长 8 → RUNNER 行,"
               "整图 1 个块 → 2 条 `rect`(h = 16)。`prVt` 的合法性:首字母 `p` 小写 = ancillary、"
               "第三字节 `V` 大写 = 保留位 0、四字节全是 ASCII 字母。该 chunk 不参与像素,"
               "抽取结果必须与不带它时完全相同。",
    )


# --------------------------------------------------------------------------
# 注册表(顺序与 CASES.tsv 的 22 条 syn- 行一致)
# --------------------------------------------------------------------------

BUILDERS = (
    ("syn-1x1-transparent", case_1x1_transparent),
    ("syn-1x1-opaque", case_1x1_opaque),
    ("syn-16x16-mono", case_16x16_mono),
    ("syn-16x16-tricolor", case_16x16_tricolor),
    ("syn-17x13-odd", case_17x13_odd),
    ("syn-64x32-nonsquare", case_64x32_nonsquare),
    ("syn-16x512-strip", case_16x512_strip),
    ("syn-300-gradient", case_300_gradient),
    ("syn-tier-61", case_tier_61),
    ("syn-tier-62", case_tier_62),
    ("syn-tier-255", case_tier_255),
    ("syn-tier-256", case_tier_256),
    ("syn-png-gray0", case_png_gray0),
    ("syn-png-gray-trns", case_png_gray_trns),
    ("syn-png-gray-trns-highbyte", case_png_gray_trns_highbyte),
    ("syn-png-palette", case_png_palette),
    ("syn-png-palette-trns", case_png_palette_trns),
    ("syn-png-gray-alpha", case_png_gray_alpha),
    ("syn-png-rgb-trns", case_png_rgb_trns),
    ("syn-png-two-text-chunks", case_png_two_text_chunks),
    ("syn-ops-fallback", case_ops_fallback),
    ("syn-grid-large-index4", case_grid_large_index4),
    ("syn-png-unknown-ancillary", case_png_unknown_ancillary),
)

CASES = tuple(name for name, _ in BUILDERS)
BUILDERS = dict(BUILDERS)


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def _notes(case, spec):
    lines = []
    lines.append("# %s" % case)
    lines.append("")
    lines.append("- 尺寸:%dx%d" % (spec["width"], spec["height"]))
    lines.append("- PNG:%s" % spec["png_desc"])
    lines.append("- 非透明色数(n):%d" % count_colors(spec["intent"]))
    lines.append("")
    lines.append("## 覆盖条款")
    lines.append("")
    lines.append(spec["clauses"])
    lines.append("")
    lines.append("## 期望值来源")
    lines.append("")
    lines.append(spec["derive"])
    lines.append("")
    lines.append("")
    lines.append("`expected.pixels.sha256` 按 §13.4 计算:逐行、行内自左向右、自上而下,每像素"
                 " R,G,B,A 四字节,无填充,共 %d 字节;由本文件持有的**意图像素**直接算出,"
                 "不经过任何 PNG 解码。" % (4 * spec["width"] * spec["height"]))
    if spec["extra"]:
        lines.append("")
        lines.append(spec["extra"])
    return "\n".join(lines) + "\n"


def encode_case(case):
    """构造并返回 `(png 字节, spec)`;同时用独立读取器自检像素往返。"""
    spec = BUILDERS[case]()
    data = png_write.encode_png(
        spec["width"], spec["height"], spec["color_type"], spec["rows"],
        plte=spec["plte"], trns=spec["trns"], extra_chunks=spec["extra_chunks"])
    width, height, rgba = png_ref.decode_rgba8(data)
    if (width, height) != (spec["width"], spec["height"]):
        raise RuntimeError("内部自检失败:%s 的尺寸解回 %dx%d" % (case, width, height))
    if rgba != spec["intent"]:
        raise RuntimeError("内部自检失败:%s 的 PNG 解码结果与意图像素不一致" % case)
    if len(data) > MAX_INPUT_BYTES:
        raise RuntimeError("%s 的 input.png 是 %d 字节,超过 §13.6 的 4 MiB 阈值"
                           % (case, len(data)))
    return data, spec


def write_case(case):
    data, spec = encode_case(case)
    directory = CASES_DIR / case
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "input.png").open("wb") as handle:
        handle.write(data)
    digest = hashlib.sha256(spec["intent"]).hexdigest()
    with (directory / "expected.pixels.sha256").open("wb") as handle:
        handle.write((digest + "\n").encode("ascii"))
    with (directory / "notes.md").open("wb") as handle:
        handle.write(_notes(case, spec).encode("utf-8"))
    return len(data), spec["width"], spec["height"]


def _describe_diff(width, expected, actual):
    total = min(len(expected), len(actual))
    for offset in range(total):
        if expected[offset] != actual[offset]:
            pixel = offset // 4
            channel = ALPHA_NAMES[offset % 4]
            return ("首个差异在字节偏移 %d(像素 #%d,%s 通道,坐标 x=%d y=%d):"
                    "意图 0x%02X,实际 0x%02X"
                    % (offset, pixel, channel, pixel % width, pixel // width,
                       expected[offset], actual[offset]))
    return "长度不同:意图 %d 字节,实际 %d 字节" % (len(expected), len(actual))


def verify_case(case):
    """只读校验一条用例,返回问题列表(空列表 = 通过)。"""
    spec = BUILDERS[case]()
    directory = CASES_DIR / case
    png_path = directory / "input.png"
    sha_path = directory / "expected.pixels.sha256"
    notes_path = directory / "notes.md"
    problems = []
    for path in (png_path, sha_path, notes_path):
        if not path.is_file():
            problems.append("缺少文件 %s" % path.name)
    if problems:
        return problems
    data = png_path.read_bytes()
    try:
        width, height, rgba = png_ref.decode_rgba8(data)
    except png_ref.PngRefError as exc:
        return ["解码 input.png 失败:%s" % exc]
    if (width, height) != (spec["width"], spec["height"]):
        problems.append("尺寸 %dx%d != 期望 %dx%d" % (width, height, spec["width"], spec["height"]))
    intent = spec["intent"]
    if rgba != intent:
        problems.append(_describe_diff(spec["width"], intent, rgba))
    expected_sha = (hashlib.sha256(intent).hexdigest() + "\n").encode("ascii")
    actual_sha = sha_path.read_bytes()
    if actual_sha != expected_sha:
        problems.append("expected.pixels.sha256 内容不符:文件=%r 期望=%r"
                        % (actual_sha[:72], expected_sha[:72]))
    if len(data) > MAX_INPUT_BYTES:
        problems.append("input.png %d 字节超过 4 MiB 阈值" % len(data))
    return problems


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run_all():
    print("生成 %d 条 syn- 用例 → %s" % (len(CASES), CASES_DIR))
    print("%-28s %-10s %10s" % ("case", "size", "bytes"))
    total = 0
    for case in CASES:
        size, width, height = write_case(case)
        total += size
        print("%-28s %-10s %10d" % (case, "%dx%d" % (width, height), size))
    print("合计:%d 字节(最大单文件阈值 %d)" % (total, MAX_INPUT_BYTES))
    return 0


def run_verify():
    print("校验 %d 条 syn- 用例(不写文件)" % len(CASES))
    failures = 0
    for case in CASES:
        problems = verify_case(case)
        if problems:
            failures += 1
            print("[FAIL] %s" % case)
            for problem in problems:
                print("       - %s" % problem)
        else:
            print("[ OK ] %s" % case)
    if failures:
        print("失败 %d / %d" % (failures, len(CASES)))
        return 1
    print("全部 %d 条通过" % len(CASES))
    return 0


def main(argv):
    args = argv[1:]
    if args == ["--all"]:
        return run_all()
    if args == ["--verify"]:
        return run_verify()
    if len(args) == 1 and not args[0].startswith("-"):
        case = args[0]
        if case not in BUILDERS:
            sys.stderr.write("未知用例:%s\n可用:%s\n" % (case, ", ".join(CASES)))
            return 2
        size, width, height = write_case(case)
        print("%s → %dx%d,%d 字节" % (case, width, height, size))
        return 0
    sys.stderr.write(
        "用法:\n"
        "  %s --all\n"
        "  %s <case-path>     (如 syn-16x16-mono)\n"
        "  %s --verify\n" % (argv[0], argv[0], argv[0]))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
