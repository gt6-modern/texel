#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立 PXL 渲染 oracle(规范 §3–§7 的第二实现,仅标准库)。

**为什么要有第三份实现。** `texel-compiler`(Java)是被测渲染器,`tests/pxl_oracle.py` 是测试侧
的独立实现,而 `op-*` / `blend-*` 用例的 `expected.pixels.sha256` 必须有一个**与两者都
无关**的来源。本文件就是那个来源:它按 `spec/PXL.md` 逐条另写一遍,不 import
`importer`、不 import `tests/`、不 import 本目录下任何别的模块。

覆盖范围:

* 三档 token 解码(§4.2 / §4.3):`char` 档 61 字符集(含保留的 `z` 与 `.`),
  `index` / `index4` 档的大写十六进制;
* 八条 op 全部(§5.1–§5.8):`fill` / `rect` / `pixel` / `line` / `grid` / `mirror` /
  `erase` / `stamp`。`line` 用 §5.4 逐字符固定的 Bresenham,**不做端点交换归一**;
  `mirror` 是快照语义、越界源按全零;`stamp` 先 X 后 Y;
* 三种 blend 的整数公式(§6.2–§6.5):用**未取整**的 `aNum` 归一,含 `aNum == 0` 分支;
* 输出 `(width, height, RGBA8 bytes)`;SHA-256 按 §13.4 定义(逐行、行内自左向右、
  自上而下,每像素 R,G,B,A,无填充)。

错误码按 §11.1 的阶段表实现(阶段 2 起;阶段 1 的输入位图规则属抽取侧,不在本文件)。
它只用来**拒绝**明显非法的文档,以及在 `python_ref_verify.py` 里交叉核对负例的期望码;
它不是被测实现,判定仍以 `expected.error` 与规范为准。

用法::

    <py> spec/conformance/tools/pxl_render_ref.py --selftest
    <py> spec/conformance/tools/pxl_render_ref.py <input.pxl>   # 打印尺寸与像素摘要

`--selftest` 逐条比对 §6.6(a)(b)(c)(d)(e)(f)(g) 的已算值,外加两条"钳位识别"用例
(`81818101 over 81818180` 与 `FFFFFF01 multiply over 81818180`,正确值都是 `81818180`),
以及 §6.5 / §6.7 的几条分支性质。任何一条不符即以非零退出码结束。
"""

import decimal
import hashlib
import json
import re
import sys

# --------------------------------------------------------------------------
# §4 三档编码
# --------------------------------------------------------------------------

#: §4.2:slot 1..61 的 token,即 `0-9` + `A-Z` + `a-y`。`z` 保留未用,`.` 归透明。
CHAR_TOKENS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy"

#: 档位 → (token 宽度, 非透明 slot 上限)
TIERS = {
    "char": (1, 61),
    "index": (2, 255),
    "index4": (4, 65535),
}

TRANSPARENT = b"\x00\x00\x00\x00"

#: §5.0.2 资源上界
AREA_LIMIT = 1 << 22
LAYER_LIMIT = 256
LAYERS_TIMES_AREA_LIMIT = 1 << 24
OPS_LIMIT = 32768
SPRITE_LIMIT = 4096
SPRITE_ROWS_LIMIT = 65536
DOC_BYTES_LIMIT = 67108864

#: §5.0.1 整数域
DIM_MIN, DIM_MAX = 1, 65536
COORD_MIN, COORD_MAX = -65536, 65536
SIZE_MIN, SIZE_MAX = 0, 65536
SLOT_MIN, SLOT_MAX = 0, 65535
TOKENS_PER_ROW_MAX = 65536

#: 光栅工作预算：所有 op 裁剪后的像素写入总数（§5.0.2）。
RASTER_WORK_MAX = 4194304


NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]*\Z")
PALETTE_ITEM_PATTERN = re.compile(r"[0-9A-F]{8}\Z")

TOP_MEMBERS = ("format", "version", "encoding", "width", "height", "palette",
               "sprites", "layers")
LAYER_MEMBERS = ("name", "blend", "ops")
BLEND_NAMES = ("normal", "multiply", "mask")

#: §3.7 成员顺序表(这里只用它的**成员集合**;顺序只影响序列化,不影响渲染)。
OP_MEMBERS = {
    "fill": ("op", "slot"),
    "rect": ("op", "x", "y", "w", "h", "slot"),
    "pixel": ("op", "x", "y", "slot"),
    "line": ("op", "x0", "y0", "x1", "y1", "slot"),
    "grid": ("op", "x", "y", "grid"),
    "mirror": ("op", "from", "to", "flipX", "flipY"),
    "erase": ("op", "x", "y", "w", "h"),
    "stamp": ("op", "sprite", "x", "y", "flipX", "flipY"),
}

#: 每个 op 的整数成员 → §5.0.1 的闭区间
OP_INTEGER_RANGES = {
    "fill": {"slot": (SLOT_MIN, SLOT_MAX)},
    "rect": {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX),
             "w": (SIZE_MIN, SIZE_MAX), "h": (SIZE_MIN, SIZE_MAX),
             "slot": (SLOT_MIN, SLOT_MAX)},
    "pixel": {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX),
              "slot": (SLOT_MIN, SLOT_MAX)},
    "line": {"x0": (COORD_MIN, COORD_MAX), "y0": (COORD_MIN, COORD_MAX),
             "x1": (COORD_MIN, COORD_MAX), "y1": (COORD_MIN, COORD_MAX),
             "slot": (SLOT_MIN, SLOT_MAX)},
    "grid": {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX)},
    "mirror": {},
    "erase": {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX),
              "w": (SIZE_MIN, SIZE_MAX), "h": (SIZE_MIN, SIZE_MAX)},
    "stamp": {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX)},
}

FROM_INTEGER_RANGES = {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX),
                       "w": (SIZE_MIN, SIZE_MAX), "h": (SIZE_MIN, SIZE_MAX)}
TO_INTEGER_RANGES = {"x": (COORD_MIN, COORD_MAX), "y": (COORD_MIN, COORD_MAX)}


class PxlRefError(Exception):
    """按 §11 的错误码失败。"""

    __slots__ = ("code", "message")

    def __init__(self, code, message):
        self.code = code
        self.message = message
        Exception.__init__(self, "%s: %s" % (code, message))


def _fail(code, message):
    raise PxlRefError(code, message)


# --------------------------------------------------------------------------
# §13.4 像素摘要
# --------------------------------------------------------------------------

def pixels_sha256(width, height, rgba):
    """§13.4:逐行、行内自左向右、自上而下;每像素 R,G,B,A;无填充。"""
    if len(rgba) != 4 * width * height:
        raise ValueError("RGBA 流长度 %d != 4*%d*%d" % (len(rgba), width, height))
    return hashlib.sha256(rgba).hexdigest()


# --------------------------------------------------------------------------
# §3 文档解析与校验(阶段 2..9)
# --------------------------------------------------------------------------

def _reject_duplicate_keys(pairs):
    """§3.2:同一对象内成员名不得重复(不同解析器取值不同,不钉死必分叉)。"""
    seen = set()
    ordered = []
    for key, value in pairs:
        if key in seen:
            _fail("PXL001", "同一对象内成员名重复: %r" % (key,))
        seen.add(key)
        ordered.append((key, value))
    return dict(ordered)


def _decode_utf8(data):
    """阶段 2:能按 UTF-8 解码,允许并忽略 BOM。"""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("PXL001", "不是合法 UTF-8: %s" % (exc,))


def _load_json(text):
    """阶段 3 的前半:合法 JSON。用 Decimal 解析小数字面量,以保留精确十进制。"""
    try:
        return json.loads(text, parse_float=decimal.Decimal,
                          object_pairs_hook=_reject_duplicate_keys)
    except PxlRefError:
        raise
    except ValueError as exc:
        _fail("PXL001", "不是合法 JSON: %s" % (exc,))


def _is_number(value):
    return isinstance(value, (int, decimal.Decimal)) and not isinstance(value, bool)


def _as_int(value, where):
    """把 JSON 数转成整数;非整数值 → PXL002(§3.8 要求按原始词法精确判定)。"""
    if isinstance(value, bool) or not _is_number(value):
        _fail("PXL001", "%s 的类型不是数字" % (where,))
    if isinstance(value, int):
        return value
    if value != value.to_integral_value():
        _fail("PXL002", "%s 不是整数值: %s" % (where, value))
    return int(value)


def _require_number_type(mapping, key, where):
    """阶段 3:成员的类型类别必须是 JSON 的 number(整数值与否归阶段 6)。"""
    value = mapping[key]
    if isinstance(value, bool) or not _is_number(value):
        _fail("PXL001", "%s.%s 的类型不是数字" % (where, key))


def _require_boolean_type(mapping, key, where):
    """阶段 3:可选布尔成员;缺省合法,写了就必须是布尔值。"""
    if key not in mapping:
        return
    if not isinstance(mapping[key], bool):
        _fail("PXL001", "%s.%s 的类型不是布尔值" % (where, key))


def _check_op_value_types(layers):
    """阶段 3:已知 op 的成员类型类别。未知 op 的专属成员一律不碰(阶段 8 报 PXL004)。"""
    for layer_index, layer in enumerate(layers):
        for op_index, op in enumerate(layer["ops"]):
            name = op["op"]
            if name not in OP_MEMBERS:
                continue
            where = "layers[%d].ops[%d]" % (layer_index, op_index)
            for key in OP_INTEGER_RANGES[name]:
                if key not in op:
                    _fail("PXL001", "%s 缺少字段 %r" % (where, key))
                _require_number_type(op, key, where)
            if name == "mirror":
                for group, table in (("from", FROM_INTEGER_RANGES),
                                     ("to", TO_INTEGER_RANGES)):
                    if group not in op:
                        _fail("PXL001", "%s 缺少字段 %r" % (where, group))
                    holder = op[group]
                    if not isinstance(holder, dict):
                        _fail("PXL001", "%s.%s 的类型不是对象" % (where, group))
                    for key in table:
                        if key not in holder:
                            _fail("PXL001", "%s.%s 缺少字段 %r" % (where, group, key))
                        _require_number_type(holder, key, "%s.%s" % (where, group))
                for key in ("flipX", "flipY"):
                    _require_boolean_type(op, key, where)
            elif name == "stamp":
                for key in ("flipX", "flipY"):
                    _require_boolean_type(op, key, where)
                if not isinstance(op.get("sprite"), str):
                    _fail("PXL001", "%s.sprite 的类型不是字符串" % where)


def _clipped(width, height, x, y, w, h):
    """与画布求交后的面积(§5.0.2 的计数口径)。"""
    if w <= 0 or h <= 0:
        return 0
    left = max(0, x)
    top = max(0, y)
    right = min(width, x + w)
    bottom = min(height, y + h)
    if left >= right or top >= bottom:
        return 0
    return (right - left) * (bottom - top)


def _check_version(value):
    """Version dispatch：`version` 必须是**整数值的 1**。

    分派规则(本规范开头 Version dispatch 与 §12)：非整数 / 不可表示 / 负数 / 超域
    一律 PXL001；落在分派域 `0..65535` 内但不等于 1 的整数报 PXL007。

    这里**不能**走 `_require_int` + `%d`：`1.5` 会得到 PXL002(那属于数值域，
    而 version 的取值归版本分派)，`1e99999` 会先被格式化成 10 万位的整数串而抛
    ValueError(连错误码都没有)。两种都与实现不符。
    """
    if isinstance(value, bool) or not _is_number(value):
        _fail("PXL001", "version 的类型不是数字")
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            _fail("PXL001", "version 不是有限数值(§12)")
        if value != value.to_integral_value():
            _fail("PXL001", "version 不是整数值(§12 fail-closed)")
        # 超过 64 位可表示范围就没有"值"可言，直接按 fail-closed 拒绝；
        # 这样既不用把巨大指数展开成整数，也不用格式化它。
        if abs(value) > decimal.Decimal(2) ** 63:
            _fail("PXL001", "version 超出 64 位可表示范围(§12 fail-closed)")
        integral = int(value)
    else:
        integral = int(value)
        if abs(integral) > 2 ** 63:
            _fail("PXL001", "version 超出 64 位可表示范围(§12 fail-closed)")
    if not 0 <= integral <= 65535:
        _fail("PXL001", "version 落在分派域 0..65535 之外(负数或超域)")
    if integral != 1:
        _fail("PXL007", "version = %d 在分派域内但本 compiler 不支持" % integral)


def _require_int(mapping, key, where):
    """取一个必须存在的整数成员(类型类别错 → PXL001,非整数值 → PXL002)。"""
    value = mapping[key]
    if isinstance(value, bool) or not _is_number(value):
        _fail("PXL001", "%s.%s 的类型不是数字" % (where, key))
    return _as_int(value, "%s.%s" % (where, key))


def _check_range(value, bounds, where):
    low, high = bounds
    if not low <= value <= high:
        _fail("PXL002", "%s = %d 越出 [%d, %d]" % (where, value, low, high))


def _check_rows(rows, where):
    """阶段 3:行数组非空、每行非空、各行等长(都不依赖 token 宽度)。"""
    if not isinstance(rows, list):
        _fail("PXL001", "%s 的行数组不是数组" % (where,))
    if not rows:
        _fail("PXL001", "%s 的行数组为空(§3.4 要求至少 1 行)" % (where,))
    width = None
    for index, row in enumerate(rows):
        if not isinstance(row, str):
            _fail("PXL001", "%s 的第 %d 行不是字符串" % (where, index))
        if row == "":
            _fail("PXL001", "%s 的第 %d 行是空串(§3.4 要求至少 1 个 token)"
                  % (where, index))
        if width is None:
            width = len(row)          # 按码点计数(§3.4)
        elif len(row) != width:
            _fail("PXL001", "%s 的第 %d 行长度 %d != 第 0 行的 %d"
                  % (where, index, len(row), width))


def _check_op_members(op, name):
    """阶段 3:已知 op 的成员集合必须与 §3.7 一致(多一个少一个都 PXL001)。

    `flipX` / `flipY` 是可选成员(§3.7:当且仅当为 `false` 时省略),因此不算缺。
    """
    allowed = OP_MEMBERS[name]
    extra = [key for key in op if key not in allowed]
    if extra:
        _fail("PXL001", "%s 有未定义成员: %r" % (name, sorted(extra)))
    missing = [key for key in allowed
               if key not in op and key not in ("flipX", "flipY")]
    if missing:
        _fail("PXL001", "%s 缺少成员: %r" % (name, missing))


def _check_nested_point(obj, keys, where):
    if not isinstance(obj, dict):
        _fail("PXL001", "%s 不是对象" % (where,))
    extra = [key for key in obj if key not in keys]
    if extra:
        _fail("PXL001", "%s 有未定义成员: %r" % (where, sorted(extra)))
    missing = [key for key in keys if key not in obj]
    if missing:
        _fail("PXL001", "%s 缺少成员: %r" % (where, missing))


def _tokenize(row, encoding):
    """把一行切成 slot 序列(§4.6:按固定 token 宽度逐段位置切分)。"""
    width = TIERS[encoding][0]
    if encoding != "char" and not all(ch in "0123456789ABCDEF" for ch in row):
        bad = sorted({ch for ch in row if ch not in "0123456789ABCDEF"})
        _fail("PXL001", "token 含枚举集之外的字符: %r" % (bad,))
    if encoding == "char":
        bad = sorted({ch for ch in row if ch != "." and ch not in CHAR_TOKENS})
        if bad:
            _fail("PXL001", "token 含枚举集之外的字符: %r" % (bad,))
    slots = []
    for start in range(0, len(row), width):
        piece = row[start:start + width]
        if encoding == "char":
            slots.append(0 if piece == "." else CHAR_TOKENS.index(piece) + 1)
        else:
            slots.append(int(piece, 16))
    return slots


def _derive_encoding(count):
    """§4.5:档位由 `palette` 长度唯一派生;`n >= 65536` 时无档位可派生(None)。"""
    if count <= 61:
        return "char"
    if count <= 255:
        return "index"
    if count <= 65535:
        return "index4"
    return None


def parse_document(data):
    """解析并校验一份 PXL 文档,返回规范化结构(供 :func:`render_document` 使用)。

    阶段严格按 §11.1:2 → 3 → 4 → 5 → 6 → 7 → 8 → 9。
    """
    text = _decode_utf8(data)
    raw = _load_json(text)
    # ---- 阶段 3:结构、成员集合、类型类别、枚举值、行数组形状 ----
    if not isinstance(raw, dict):
        _fail("PXL001", "顶层不是对象")
    extra = [key for key in raw if key not in TOP_MEMBERS]
    if extra:
        _fail("PXL001", "顶层出现 §3.2 之外的字段: %r" % (sorted(extra),))
    for key in ("format", "version", "encoding", "width", "height", "palette",
                "layers"):
        if key not in raw:
            _fail("PXL001", "缺少必需字段 %r" % (key,))

    if not isinstance(raw["format"], str):
        _fail("PXL001", "format 的类型不是字符串")
    if raw["format"] != "PXL":
        _fail("PXL001", "format 不等于 \"PXL\": %r" % (raw["format"],))

    _check_version(raw["version"])

    if not isinstance(raw["encoding"], str):
        _fail("PXL001", "encoding 的类型不是字符串")

    width = _require_int(raw, "width", "document")
    height = _require_int(raw, "height", "document")

    if not isinstance(raw["palette"], list):
        _fail("PXL001", "palette 的类型不是数组")
    palette = []
    for index, item in enumerate(raw["palette"]):
        if not isinstance(item, str):
            _fail("PXL001", "palette[%d] 不是字符串(§3.2 array of string)" % index)
        palette.append(item)

    sprites = {}
    if "sprites" in raw:
        if not isinstance(raw["sprites"], dict):
            _fail("PXL001", "sprites 的类型不是对象")
        for name, rows in raw["sprites"].items():
            if not NAME_PATTERN.match(name):
                _fail("PXL001", "sprites 成员名不匹配 [a-z][a-z0-9_-]*: %r" % (name,))
            _check_rows(rows, "sprites[%r]" % (name,))
            sprites[name] = list(rows)

    if not isinstance(raw["layers"], list):
        _fail("PXL001", "layers 的类型不是数组")
    if not raw["layers"]:
        _fail("PXL001", "layers 数组为空(§3.2 要求至少 1 个图层)")

    layers = []
    for layer_index, layer in enumerate(raw["layers"]):
        where = "layers[%d]" % layer_index
        if not isinstance(layer, dict):
            _fail("PXL001", "%s 不是对象" % where)
        extra = [key for key in layer if key not in LAYER_MEMBERS]
        if extra:
            _fail("PXL001", "%s 有未定义成员: %r" % (where, sorted(extra)))
        for key in LAYER_MEMBERS:
            if key not in layer:
                _fail("PXL001", "%s 缺少成员 %r" % (where, key))
        if not isinstance(layer["name"], str):
            _fail("PXL001", "%s.name 的类型不是字符串" % where)
        if not NAME_PATTERN.match(layer["name"]):
            _fail("PXL001", "%s.name 不匹配 [a-z][a-z0-9_-]*: %r"
                  % (where, layer["name"]))
        if not isinstance(layer["blend"], str):
            _fail("PXL001", "%s.blend 的类型不是字符串" % where)
        if layer["blend"] not in BLEND_NAMES:
            _fail("PXL001", "%s.blend 不在三值内: %r" % (where, layer["blend"]))
        if not isinstance(layer["ops"], list):
            _fail("PXL001", "%s.ops 的类型不是数组" % where)
        ops = []
        for op_index, op in enumerate(layer["ops"]):
            op_where = "%s.ops[%d]" % (where, op_index)
            if not isinstance(op, dict):
                _fail("PXL001", "%s 不是对象" % op_where)
            if "op" not in op:
                _fail("PXL001", "%s 缺少 op 字段" % op_where)
            if not isinstance(op["op"], str):
                _fail("PXL001", "%s.op 的类型不是字符串" % op_where)
            name = op["op"]
            if name in OP_MEMBERS:
                _check_op_members(op, name)
                if name == "grid":
                    _check_rows(op["grid"], "%s.grid" % op_where)
                elif name == "mirror":
                    _check_nested_point(op["from"], ("x", "y", "w", "h"),
                                        "%s.from" % op_where)
                    _check_nested_point(op["to"], ("x", "y"), "%s.to" % op_where)
                elif name == "stamp":
                    if not isinstance(op["sprite"], str):
                        _fail("PXL001", "%s.sprite 的类型不是字符串" % op_where)
                for key in ("flipX", "flipY"):
                    if key in op and not isinstance(op[key], bool):
                        _fail("PXL001", "%s.%s 的类型不是布尔" % (op_where, key))
            ops.append(op)
        layers.append({"name": layer["name"], "blend": layer["blend"], "ops": ops})

    layer_names = [layer["name"] for layer in layers]
    if len(set(layer_names)) != len(layer_names):
        _fail("PXL001", "图层名在文档内重名: %r" % (sorted(layer_names),))

    # §11.1 把「类型类别不符」放在**阶段 3**，把「不是整数值 / 越区间」放在阶段 6。
    # 两者必须发生在各自的阶段：混在阶段 6 里，`encoding=index9 + pixel.x=[0]`
    # 会被判成 PXL003（阶段 4）而不是 PXL001（阶段 3），与实现分叉。
    _check_op_value_types(layers)

    # ---- 阶段 4:encoding 取值 ----
    encoding = raw["encoding"]
    if encoding not in TIERS:
        _fail("PXL003", "encoding 不在 {char, index, index4}: %r" % (encoding,))
    token_width, tier_cap = TIERS[encoding]

    # ---- 阶段 5:依赖 token 宽度的两项检查 ----
    for name, rows in sprites.items():
        _check_token_rows(rows, encoding, "sprites[%r]" % (name,))
    for layer_index, layer in enumerate(layers):
        for op_index, op in enumerate(layer["ops"]):
            if op["op"] == "grid":
                _check_token_rows(op["grid"], encoding,
                                  "layers[%d].ops[%d].grid" % (layer_index, op_index))

    # ---- 阶段 6:数值域(文档级 + op 级) ----
    _check_range(width, (DIM_MIN, DIM_MAX), "width")
    _check_range(height, (DIM_MIN, DIM_MAX), "height")
    area = width * height                      # 任意精度,不依赖 32 位乘法
    if area > AREA_LIMIT:
        _fail("PXL002", "width × height = %d 超过 2^22" % area)
    if len(layers) > LAYER_LIMIT:
        _fail("PXL002", "图层数 %d 超过 256" % len(layers))
    if len(layers) * area > LAYERS_TIMES_AREA_LIMIT:
        _fail("PXL002", "图层数 × 面积 = %d 超过 2^24" % (len(layers) * area))
    if len(sprites) > SPRITE_LIMIT:
        _fail("PXL002", "sprites 项数 %d 超过 4096" % len(sprites))
    for name, rows in sprites.items():
        if len(rows) > SPRITE_ROWS_LIMIT:
            _fail("PXL002", "sprite %r 的行数 %d 超过 65536" % (name, len(rows)))
        tokens = len(rows[0]) // token_width
        if tokens > TOKENS_PER_ROW_MAX:
            _fail("PXL002", "sprite %r 的行 token 数 %d 超过 65536" % (name, tokens))
    if len(data) > DOC_BYTES_LIMIT:
        _fail("PXL002", "文档 UTF-8 字节数 %d 超过 64 MiB" % len(data))

    normalized_layers = []
    for layer_index, layer in enumerate(layers):
        where = "layers[%d]" % layer_index
        if len(layer["ops"]) > OPS_LIMIT:
            _fail("PXL002", "%s 的 ops 数 %d 超过 32768" % (where, len(layer["ops"])))
        normalized_ops = []
        for op_index, op in enumerate(layer["ops"]):
            op_where = "%s.ops[%d]" % (where, op_index)
            name = op["op"]
            if name not in OP_MEMBERS:
                normalized_ops.append(op)      # 阶段 8 才拒绝;这里不碰 op 专属成员
                continue
            values = {}
            for key, bounds in OP_INTEGER_RANGES[name].items():
                values[key] = _require_int(op, key, op_where)
                _check_range(values[key], bounds, "%s.%s" % (op_where, key))
            if name == "grid":
                tokens = len(op["grid"][0]) // token_width
                if tokens > TOKENS_PER_ROW_MAX:
                    _fail("PXL002", "%s 行 token 数 %d 超过 65536"
                          % (op_where, tokens))
                values["grid"] = list(op["grid"])
            elif name == "mirror":
                values["from"] = {}
                for key, bounds in FROM_INTEGER_RANGES.items():
                    values["from"][key] = _require_int(op["from"], key,
                                                      "%s.from" % op_where)
                    _check_range(values["from"][key], bounds,
                                 "%s.from.%s" % (op_where, key))
                values["to"] = {}
                for key, bounds in TO_INTEGER_RANGES.items():
                    values["to"][key] = _require_int(op["to"], key,
                                                     "%s.to" % op_where)
                    _check_range(values["to"][key], bounds,
                                 "%s.to.%s" % (op_where, key))
                values["flipX"] = bool(op.get("flipX", False))
                values["flipY"] = bool(op.get("flipY", False))
            elif name == "stamp":
                values["sprite"] = op["sprite"]
                values["flipX"] = bool(op.get("flipX", False))
                values["flipY"] = bool(op.get("flipY", False))
            normalized = {"op": name}
            normalized.update(values)
            normalized_ops.append(normalized)
        normalized_layers.append({"name": layer["name"], "blend": layer["blend"],
                                  "ops": normalized_ops})

    # ---- 阶段 6 补充:光栅工作预算(§5.0.2) ----
    total_work = 0
    for layer in layers:
        for op in layer["ops"]:
            name = op["op"]
            if name not in OP_MEMBERS:
                continue
            if name == "fill":
                total_work += _clipped(width, height, 0, 0, width, height)
            elif name in ("rect", "erase"):
                total_work += _clipped(width, height, op["x"], op["y"], op["w"], op["h"])
            elif name == "pixel":
                total_work += (1 if 0 <= op["x"] < width and 0 <= op["y"] < height else 0)
            elif name == "line":
                total_work += max(abs(op["x1"] - op["x0"]), abs(op["y1"] - op["y0"])) + 1
            elif name == "grid":
                rows_ = list(op["grid"])
                if rows_:
                    total_work += _clipped(width, height, op["x"], op["y"],
                                           len(rows_[0]) // token_width, len(rows_))
            elif name == "mirror":
                total_work += _clipped(width, height, op["to"]["x"], op["to"]["y"],
                                       op["from"]["w"], op["from"]["h"])
            elif name == "stamp":
                sprite = sprites.get(op["sprite"])
                if sprite:
                    total_work += _clipped(width, height, op["x"], op["y"],
                                           len(sprite[0]) // token_width, len(sprite))
            if total_work > RASTER_WORK_MAX:
                _fail("PXL002", "光栅工作 %d 超过 2^22 的预算(裁剪后的像素写入总数)"
                      % total_work)


    # ---- 阶段 7:encoding 是否等于由 palette 长度派生的档位(仅 n <= 65535)----
    count = len(palette)
    if count <= 65535:
        derived = _derive_encoding(count)
        if encoding != derived:
            _fail("PXL003", "encoding %r 与 palette 长度 %d 派生的 %r 不一致"
                  % (encoding, count, derived))

    # ---- 阶段 8:op 名是否在八条之内 ----
    for layer in normalized_layers:
        for op in layer["ops"]:
            if op["op"] not in OP_MEMBERS:
                _fail("PXL004", "未知 op: %r" % (op["op"],))

    # ---- 阶段 9:引用与容量 ----
    for index, item in enumerate(palette):
        if not PALETTE_ITEM_PATTERN.match(item):
            _fail("PXL005", "palette[%d] 不匹配 ^[0-9A-F]{8}$: %r" % (index, item))
        if item == "00000000":
            _fail("PXL005", "palette[%d] 含 00000000(与透明 slot 冲突)" % index)
    if count >= 65536:
        _fail("PXL005", "palette 长度 %d >= 65536,无档位可派生" % count)

    for layer in normalized_layers:
        for op in layer["ops"]:
            name = op["op"]
            if "slot" in op:
                slot = op["slot"]
                if slot > tier_cap or slot > count:
                    _fail("PXL005", "%s 的 slot %d 越过档上限 %d 或 palette 长度 %d"
                          % (name, slot, tier_cap, count))
            if name == "stamp" and op["sprite"] not in sprites:
                _fail("PXL005", "stamp 引用了不存在的 sprite: %r" % (op["sprite"],))

    for name in sorted(sprites):
        _check_sprite_slots(sprites[name], encoding, count, name)
    for layer in normalized_layers:
        for op in layer["ops"]:
            if op["op"] == "grid":
                _check_sprite_slots(op["grid"], encoding, count, "grid")
            elif op["op"] == "stamp":
                _check_sprite_slots(sprites[op["sprite"]], encoding, count,
                                    op["sprite"])

    return {
        "width": width,
        "height": height,
        "encoding": encoding,
        "palette": palette,
        "sprites": sprites,
        "layers": normalized_layers,
    }


def _check_token_rows(rows, encoding, where):
    """阶段 5:行长必须是 token 宽度的正整数倍,且字符必须属于该档枚举集。"""
    token_width = TIERS[encoding][0]
    for index, row in enumerate(rows):
        if len(row) % token_width != 0:
            _fail("PXL001", "%s 的第 %d 行长度 %d 不是 token 宽度 %d 的整数倍"
                  % (where, index, len(row), token_width))
        _tokenize(row, encoding)


def _check_sprite_slots(rows, encoding, palette_count, where):
    """阶段 9:所有 token 解码出的 slot 都不得越过档上限或 palette 长度。"""
    tier_cap = TIERS[encoding][1]
    for row in rows:
        for slot in _tokenize(row, encoding):
            if slot > tier_cap or slot > palette_count:
                _fail("PXL005", "%s 的 token 指向 slot %d,越过档上限 %d 或 palette 长度 %d"
                      % (where, slot, tier_cap, palette_count))


# --------------------------------------------------------------------------
# §5 八条图元 + §6 三种 blend
# --------------------------------------------------------------------------

def div255(value):
    """§6.2:`(v + 127) / 255`,整数除法,要求 0 <= v <= 65025。"""
    return (value + 127) // 255


def composite_pixel(bottom, source, blend):
    """把单个源像素按 ``blend`` 合到下层像素上(§6.3 / §6.4 / §6.5)。"""
    dr, dg, db, da = bottom
    sr, sg, sb, sa = source

    if blend == "mask":
        if source == (0, 0, 0, 0):
            return (dr, dg, db, da)
        return (dr, dg, db, div255(da * sa))

    t = 255 - sa
    a_num = sa * 255 + da * t
    if a_num == 0:
        if source == (0, 0, 0, 0):
            return (dr, dg, db, 0)
        return (sr, sg, sb, 0)

    alpha_out = (a_num + 127) // 255

    def channel(sc, dc):
        numerator = sc * sa * 255 + dc * da * t + a_num // 2
        return numerator // a_num

    if blend == "normal":
        return (channel(sr, dr), channel(sg, dg), channel(sb, db), alpha_out)

    # multiply: 先把源色乘进下层色,再用与 normal 完全相同的公式
    mc_r = div255(sr * dr)
    mc_g = div255(sg * dg)
    mc_b = div255(sb * db)
    return (channel(mc_r, dr), channel(mc_g, dg), channel(mc_b, db), alpha_out)


def line_points(x0, y0, x1, y1):
    """§5.4 逐字符固定的 Bresenham:含两端点,**不做端点交换归一**。"""
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        yield x, y
        if x == x1 and y == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def render_document(document):
    """按 §5 / §6 渲染,返回 ``(width, height, RGBA8 bytes)``。"""
    width = document["width"]
    height = document["height"]
    palette = document["palette"]
    sprites = document["sprites"]
    encoding = document["encoding"]

    values = [TRANSPARENT]
    for item in palette:
        values.append(bytes.fromhex(item))

    accumulator = bytearray(width * height * 4)
    for layer in document["layers"]:
        buffer = bytearray(width * height * 4)
        for op in layer["ops"]:
            _apply_op(buffer, width, height, op, values, sprites, encoding)
        _blend_layer(accumulator, buffer, layer["blend"])
    return width, height, bytes(accumulator)


def _write(buffer, width, height, x, y, value):
    """§5.0:越界一律裁剪,不报错。"""
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 4
        buffer[offset:offset + 4] = value


def _fill_rect(buffer, width, height, x, y, w, h, value):
    if w <= 0 or h <= 0:
        return
    x0 = max(x, 0)
    y0 = max(y, 0)
    x1 = min(x + w, width)
    y1 = min(y + h, height)
    if x0 >= x1 or y0 >= y1:
        return
    row = value * (x1 - x0)
    for py in range(y0, y1):
        offset = (py * width + x0) * 4
        buffer[offset:offset + len(row)] = row


def _apply_op(buffer, width, height, op, values, sprites, encoding):
    name = op["op"]
    if name == "fill":
        buffer[:] = values[op["slot"]] * (width * height)
    elif name == "rect":
        _fill_rect(buffer, width, height, op["x"], op["y"], op["w"], op["h"],
                   values[op["slot"]])
    elif name == "pixel":
        _write(buffer, width, height, op["x"], op["y"], values[op["slot"]])
    elif name == "line":
        value = values[op["slot"]]
        for x, y in line_points(op["x0"], op["y0"], op["x1"], op["y1"]):
            _write(buffer, width, height, x, y, value)
    elif name == "grid":
        _apply_grid(buffer, width, height, op["x"], op["y"], op["grid"],
                    encoding, values)
    elif name == "mirror":
        _apply_mirror(buffer, width, height, op)
    elif name == "erase":
        _fill_rect(buffer, width, height, op["x"], op["y"], op["w"], op["h"],
                   TRANSPARENT)
    elif name == "stamp":
        _apply_stamp(buffer, width, height, op, values, sprites, encoding)
    else:                                            # pragma: no cover
        raise AssertionError("未知 op: %r" % (name,))


def _decode_row(row, encoding):
    """把一行切成 token 并解码成 slot 序列(§4.6:按固定宽度逐段切分)。"""
    token_width = TIERS[encoding][0]
    slots = []
    for start in range(0, len(row), token_width):
        piece = row[start:start + token_width]
        if encoding == "char":
            slots.append(0 if piece == "." else CHAR_TOKENS.index(piece) + 1)
        else:
            slots.append(int(piece, 16))
    return slots


def _decode_grid(rows, encoding):
    return [_decode_row(row, encoding) for row in rows]


def _apply_grid(buffer, width, height, x, y, rows, encoding, values,
                flip_x=False, flip_y=False):
    """§5.5 / §5.8:逐点覆盖;`stamp` 复用本函数并先做翻转(先 X 后 Y)。"""
    slots = _decode_grid(rows, encoding)
    grid_height = len(slots)
    grid_width = len(slots[0]) if slots else 0
    for j in range(grid_height):
        source_j = grid_height - 1 - j if flip_y else j
        for i in range(grid_width):
            source_i = grid_width - 1 - i if flip_x else i
            _write(buffer, width, height, x + i, y + j,
                   values[slots[source_j][source_i]])


def _apply_mirror(buffer, width, height, op):
    """§5.6:快照语义;读出侧不裁剪,越界源像素按透明 slot 0 参与。"""
    fx, fy = op["from"]["x"], op["from"]["y"]
    w, h = op["from"]["w"], op["from"]["h"]
    tx, ty = op["to"]["x"], op["to"]["y"]
    flip_x, flip_y = op["flipX"], op["flipY"]

    # 只保存**会落到画布上**的那部分目标像素。合法文档里 `from` 可以是
    # 65536 × 65536（4G 项），全量快照会申请几十 GB；按目标侧裁剪后不超过 2^22 项。
    left = max(0, tx)
    top = max(0, ty)
    right = min(width, tx + w)
    bottom = min(height, ty + h)
    if left >= right or top >= bottom:
        return

    dest_width = right - left
    dest_height = bottom - top
    snapshot = []
    for dy in range(top, bottom):
        j = dy - ty
        sy = fy + (h - 1 - j if flip_y else j)
        row = []
        for dx in range(left, right):
            i = dx - tx
            sx = fx + (w - 1 - i if flip_x else i)
            if 0 <= sx < width and 0 <= sy < height:
                offset = (sy * width + sx) * 4
                row.append(bytes(buffer[offset:offset + 4]))
            else:
                row.append(TRANSPARENT)      # §5.6:越界源按透明 slot 0
        snapshot.append(row)

    for row_index, dy in enumerate(range(top, bottom)):
        row = snapshot[row_index]
        base = (dy * width + left) * 4
        for column in range(dest_width):
            offset = base + column * 4
            buffer[offset:offset + 4] = row[column]


def _apply_stamp(buffer, width, height, op, values, sprites, encoding):
    rows = sprites[op["sprite"]]
    _apply_grid(buffer, width, height, op["x"], op["y"], rows, encoding,
                values, flip_x=op["flipX"], flip_y=op["flipY"])


def _blend_layer(accumulator, buffer, blend):
    """§6.1:逐像素把这一层合到累加器上。"""
    length = len(accumulator)
    for offset in range(0, length, 4):
        source = (buffer[offset], buffer[offset + 1], buffer[offset + 2],
                  buffer[offset + 3])
        if source == (0, 0, 0, 0):
            # 全零源:normal / multiply 的 aNum 分支与 mask 的第一分支都恒等,
            # 逐像素公式见 §6.3–§6.5;这里只是同一结论的快路径。
            continue
        bottom = (accumulator[offset], accumulator[offset + 1],
                  accumulator[offset + 2], accumulator[offset + 3])
        r, g, b, a = composite_pixel(bottom, source, blend)
        accumulator[offset] = r
        accumulator[offset + 1] = g
        accumulator[offset + 2] = b
        accumulator[offset + 3] = a


def render_text(data):
    """把 PXL 文本(``bytes``)渲染成 ``(width, height, RGBA8 bytes)``。"""
    return render_document(parse_document(data))


def _tuple(hex_value):
    raw = bytes.fromhex(hex_value)
    return (raw[0], raw[1], raw[2], raw[3])


def _document(width, height, palette, layers):
    """构造一个最小合法文档(只用于自测;走真实解析路径以保证端到端)。"""
    parts = []
    parts.append('{\n  "format": "PXL",\n  "version": 1,\n  "encoding": "char",')
    parts.append('  "width": %d,\n  "height": %d,' % (width, height))
    parts.append('  "palette": [')
    parts.append(",\n".join('    "%s"' % item for item in palette))
    parts.append('  ],\n  "layers": [')
    rendered_layers = []
    for name, blend, ops in layers:
        rendered_ops = []
        for op in ops:
            body = ",\n".join('            "%s": %s' % (key, json.dumps(value))
                             for key, value in op)
            rendered_ops.append("          {\n%s\n          }" % body)
        rendered_layers.append(
            '    {\n      "name": "%s",\n      "blend": "%s",\n      "ops": [\n'
            "%s\n      ]\n    }" % (name, blend, ",\n".join(rendered_ops)))
    parts.append(",\n".join(rendered_layers))
    parts.append("  ]\n}\n")
    return "\n".join(parts).encode("utf-8")


def _selftest_cases():
    """(说明, 期望值, 实际值) 三元组列表;实际值全部由本文件的公式算出。"""
    results = []

    def check(label, expected, actual):
        results.append((label, expected, actual))

    # §6.6(a):normal 源 FF000080 over 0000FFFF → 80007FFF
    check("§6.6(a) normal FF000080 over 0000FFFF",
          (0x80, 0x00, 0x7F, 0xFF),
          composite_pixel(_tuple("0000FFFF"), _tuple("FF000080"), "normal"))
    # §6.6(b):multiply 源 80FF0080 over 112233FF → 0D2219FF
    check("§6.6(b) multiply 80FF0080 over 112233FF",
          (0x0D, 0x22, 0x19, 0xFF),
          composite_pixel(_tuple("112233FF"), _tuple("80FF0080"), "multiply"))
    # §6.6(c):mask 源 00FF0080 over 112233FF → 11223380
    check("§6.6(c) mask 00FF0080 over 112233FF",
          (0x11, 0x22, 0x33, 0x80),
          composite_pixel(_tuple("112233FF"), _tuple("00FF0080"), "mask"))
    # §6.6(d):mask 源 00FF0000 over 112233FF → 11223300(不得折叠成 00000000)
    check("§6.6(d) mask 00FF0000 over 112233FF",
          (0x11, 0x22, 0x33, 0x00),
          composite_pixel(_tuple("112233FF"), _tuple("00FF0000"), "mask"))
    # §6.6(e):透明 slot 源不吞掉下层的不可见颜色 → FFFFFF00
    check("§6.6(e) normal 00000000 over FFFFFF00",
          (0xFF, 0xFF, 0xFF, 0x00),
          composite_pixel(_tuple("FFFFFF00"), _tuple("00000000"), "normal"))
    # §6.6(f):不可见颜色只在底下什么都没有时透过 → FFFFFF00
    check("§6.6(f) normal FFFFFF00 over 00000000",
          (0xFF, 0xFF, 0xFF, 0x00),
          composite_pixel(_tuple("00000000"), _tuple("FFFFFF00"), "normal"))
    # §6.6(g):上界反例 → FFFFFF80(先取整 ao 的旧写法会算出通道 256)
    check("§6.6(g) normal FFFFFF01 over FFFFFF80",
          (0xFF, 0xFF, 0xFF, 0x80),
          composite_pixel(_tuple("FFFFFF80"), _tuple("FFFFFF01"), "normal"))
    # 钳位识别:正确值 81818180,旧公式 82828280
    check("normal 81818101 over 81818180(旧公式给 82828280)",
          (0x81, 0x81, 0x81, 0x80),
          composite_pixel(_tuple("81818180"), _tuple("81818101"), "normal"))
    check("multiply FFFFFF01 over 81818180(mc = div255(255*129) = 129)",
          (0x81, 0x81, 0x81, 0x80),
          composite_pixel(_tuple("81818180"), _tuple("FFFFFF01"), "multiply"))
    # §6.5 / §6.7 第 6 条:mask 作底层不保留源 RGB(11223300 mask 到空 → 00000000)
    check("mask 11223300 over 00000000(不保留源 RGB)",
          (0x00, 0x00, 0x00, 0x00),
          composite_pixel(_tuple("00000000"), _tuple("11223300"), "mask"))
    # §6.7 第 6 条:mask 改下层 alpha 而 RGB 不变 → 44556600
    check("mask 11223300 over 445566FF(改 alpha 不改 RGB)",
          (0x44, 0x55, 0x66, 0x00),
          composite_pixel(_tuple("445566FF"), _tuple("11223300"), "mask"))
    # §6.7 第 5 条:mask 从不改 RGB
    check("mask 00FF0080 over 112233FF(RGB 恒取下层)",
          (0x11, 0x22, 0x33),
          composite_pixel(_tuple("112233FF"), _tuple("00FF0080"), "mask")[:3])
    # §6.7 第 3 条:normal 下全不透明覆盖恒等于源
    check("normal sa=255 覆盖",
          (0x12, 0x34, 0x56, 0xFF),
          composite_pixel(_tuple("FFFFFF80"), _tuple("123456FF"), "normal"))
    # §6.7 第 4 条:multiply 不是覆盖,下层为空时乘成黑
    check("multiply FF0000FF over 00000000(乘成黑)",
          (0x00, 0x00, 0x00, 0xFF),
          composite_pixel(_tuple("00000000"), _tuple("FF0000FF"), "multiply"))
    # §5.6:两者都是不可见颜色时取上层
    check("normal FF000000 over FFFFFF00(上层胜)",
          (0xFF, 0x00, 0x00, 0x00),
          composite_pixel(_tuple("FFFFFF00"), _tuple("FF000000"), "normal"))

    # §4.2 / §4.3 token 解码:三档的最值
    check("char 档 slot 1 的 token 是 '0'", 1,
          _decode_row("0", "char")[0])
    check("char 档 '.' 是透明 slot 0", 0, _decode_row(".", "char")[0])
    check("char 档 slot 61 的 token 是 'y'", 61, _decode_row("y", "char")[0])
    check("index 档 'FF' = 255", 255, _decode_row("FF", "index")[0])
    check("index4 档 'FFFF' = 65535", 65535, _decode_row("FFFF", "index4")[0])

    # 端到端:走真实解析 + 渲染路径,1x1 char 档 fill
    document = parse_document(_document(1, 1, ["FF0000FF"], [
        ("base", "normal", [[("op", "fill"), ("slot", 1)]])]))
    check("端到端 char 1x1 fill 渲染", "FF0000FF",
          render_document(document)[2].hex().upper())

    # §5.4:line 不做端点交换归一 —— (2,6)->(10,5) 与反向的点集不同
    forward = list(line_points(2, 6, 10, 5))
    backward = list(line_points(10, 5, 2, 6))
    check("line (2,6)->(10,5) 与反向点数相同", len(forward), len(backward))
    check("line (2,6)->(10,5) 与反向点集不同", True,
          set(forward) != set(backward))
    check("line 退化单点", [(3, 4)], list(line_points(3, 4, 3, 4)))
    check("line 端点必被写入", (2, 6), forward[0])
    check("line 另一端点在末位", (10, 5), forward[-1])
    adjacent_ok = True
    for (x0, y0), (x1, y1) in zip(forward, forward[1:]):
        if abs(x1 - x0) > 1 or abs(y1 - y0) > 1:
            adjacent_ok = False
    check("line 是 8-连通路径", True, adjacent_ok)
    return results


def selftest():
    """逐条比对已算值;返回进程退出码。"""
    failures = 0
    for label, expected, actual in _selftest_cases():
        if expected == actual:
            print("[ OK ] %s" % label)
        else:
            failures += 1
            print("[FAIL] %s\n       期望: %r\n       实际: %r"
                  % (label, expected, actual))
    if failures:
        print("selftest 失败 %d 条" % failures)
        return 1
    print("selftest 全部通过")
    return 0


def main(argv):
    args = argv[1:]
    if args == ["--selftest"]:
        return selftest()
    if len(args) == 1 and not args[0].startswith("-"):
        from pathlib import Path
        path = Path(args[0])
        width, height, rgba = render_text(path.read_bytes())
        print("%s: %dx%d  sha256=%s"
              % (path.as_posix(), width, height, pixels_sha256(width, height, rgba)))
        return 0
    sys.stderr.write("用法:\n  %s --selftest\n  %s <input.pxl>\n"
                     % (argv[0], argv[0]))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
