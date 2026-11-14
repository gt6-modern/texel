"""v0 文档模型:阶段 3..9 的校验 + 渲染计划(v0 §3 / §4 / §5 / §11.1)。

阶段序是规范的一部分,不能重排:

  阶段 3 结构 / 枚举 / 字面量(PXL001) —— 不依赖 token 宽度的那部分
  阶段 4 encoding 取值(PXL003)
  阶段 5 依赖 token 宽度的检查(PXL001)
  阶段 6 数值域与资源上界(PXL002)
  阶段 7 encoding 与 palette 长度派生一致(PXL003)
  阶段 8 op 名是否在八条之内(PXL004)
  阶段 9 引用与容量(PXL005)

校验完成后产出一份渲染计划(plan);plan 不含任何校验逻辑,只含已解出的整数与 slot,
这样渲染路径里不会再出现"这里其实该报错"的分支。
"""

from . import numlit
from .errors import PxlError
from .jsonx import JsonNumber
from .raster import Surface, ZERO, blend_layer, clip_area, execute_ops, raster_work, to_texel

TIER_WIDTH = {"char": 1, "index": 2, "index4": 4}
TIER_MAX_SLOT = {"char": 61, "index": 255, "index4": 65535}
CHAR_TOKENS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy"
HEX_TOKENS = "0123456789ABCDEF"

MAX_SIDE = 65536
MAX_AREA = 1 << 22
MAX_LAYERS = 256
MAX_LAYER_PIXELS = 1 << 24
MAX_OPS_PER_LAYER = 32768
MAX_SPRITES = 4096
MAX_SPRITE_ROWS = 65536
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_RASTER_WORK = 1 << 22
MAX_ROW_TOKENS = 65536
MAX_PARAMS = 256
MAX_ASSET_DECLS = 4096

COORD = (-65536, 65536)
SIZE_ARG = (0, 65536)
SLOT = (0, 65535)
SIZE = (1, 65536)

BLENDS = ("normal", "multiply", "mask")

#: 八条已知图元的成员集合:必需成员与可选成员(除 op 自身)。
OP_REQUIRED = {
    "fill": ("slot",),
    "rect": ("x", "y", "w", "h", "slot"),
    "pixel": ("x", "y", "slot"),
    "line": ("x0", "y0", "x1", "y1", "slot"),
    "grid": ("x", "y", "grid"),
    "mirror": ("from", "to"),
    "erase": ("x", "y", "w", "h"),
    "stamp": ("x", "y"),
}
OP_OPTIONAL = {
    "fill": (),
    "rect": (),
    "pixel": (),
    "line": (),
    "grid": (),
    "mirror": ("flipX", "flipY"),
    "erase": (),
    "stamp": ("flipX", "flipY"),
}

#: op 级数值成员与区间(§5.0.1)。mirror 的 from/to 与 stamp 的 sprite/asset 单独处理。
OP_NUMERIC = {
    "fill": (("slot", SLOT),),
    "rect": (("x", COORD), ("y", COORD), ("w", SIZE_ARG), ("h", SIZE_ARG), ("slot", SLOT)),
    "pixel": (("x", COORD), ("y", COORD), ("slot", SLOT)),
    "line": (("x0", COORD), ("y0", COORD), ("x1", COORD), ("y1", COORD), ("slot", SLOT)),
    "grid": (("x", COORD), ("y", COORD)),
    "mirror": (("from.x", COORD), ("from.y", COORD), ("from.w", SIZE_ARG),
               ("from.h", SIZE_ARG), ("to.x", COORD), ("to.y", COORD)),
    "erase": (("x", COORD), ("y", COORD), ("w", SIZE_ARG), ("h", SIZE_ARG)),
    "stamp": (("x", COORD), ("y", COORD)),
}


def _is_object(value):
    return isinstance(value, dict)


def _is_array(value):
    return isinstance(value, list)


def _is_string(value):
    return isinstance(value, str)


def _is_number(value):
    return isinstance(value, JsonNumber)


def _is_boolean(value):
    return value is True or value is False


def _type_name(value):
    if _is_object(value):
        return "object"
    if _is_array(value):
        return "array"
    if _is_string(value):
        return "string"
    if _is_number(value):
        return "number"
    if _is_boolean(value):
        return "boolean"
    if value is None:
        return "null"
    return "unknown"


def name_is_valid(name):
    """§3.3 / §3.7 的图层名与 sprite 名语法:[a-z][a-z0-9_-]*。"""
    if not name:
        return False
    first = name[0]
    if not ("a" <= first <= "z"):
        return False
    for char in name[1:]:
        if not ("a" <= char <= "z" or "0" <= char <= "9" or char in "-_"):
            return False
    return True


class ResolvedAsset(object):
    """一个已解析的 asset:声明信息 + 解码后的位图(v1 §5.1 / §5.3)。"""

    __slots__ = ("name", "kind", "path", "bindings", "width", "height", "rgba", "node")

    def __init__(self, name, kind, path):
        self.name = name
        self.kind = kind
        self.path = path
        self.bindings = {}
        self.width = None
        self.height = None
        #: kind == "png" 时在相位 A 就有;kind == "pxl" 时要等相位 C 光栅化子节点才有。
        self.rgba = None
        #: kind == "pxl" 时的子节点(相位 C 用它递归光栅化,并回填 rgba)。
        self.node = None


class AssetStamp(object):
    """asset stamp 的**唯一**形状:几何计划与渲染计划共用同一个对象。

    为什么要有这个类(审查 codex/DSv4 都点过):同一个 kind "stamp_asset" 曾经在
    _geometry_plan 里是 (kind, x, y, w, h)、在 _plan_op 里是 (kind, x, y, flipX, flipY, asset)
    —— 两种形状同名不同长。两个消费者各按自己的下标取值,一旦计划串了就是 IndexError /
    AttributeError;更糟的是在较长的形状上取到相邻字段(把 height 当 flipY),静默画错。
    元组形状没有名字,门禁也没有理由红。改成显式类之后形状只有一种、字段有名字。
    """

    __slots__ = ("x", "y", "flip_x", "flip_y", "asset")

    def __init__(self, x, y, flip_x, flip_y, asset):
        self.x = x
        self.y = y
        self.flip_x = flip_x
        self.flip_y = flip_y
        self.asset = asset


class LayerPlan(object):
    __slots__ = ("name", "blend", "ops")

    def __init__(self, name, blend, ops):
        self.name = name
        self.blend = blend
        self.ops = ops


class Doc(object):
    """一份通过全部阶段的具体文档 + 可直接执行的渲染计划。"""

    __slots__ = ("version", "width", "height", "encoding", "palette", "layers",
                 "sprite_names", "assets", "params", "work")

    def __init__(self, version, width, height, encoding, palette, layers,
                 sprite_names, assets, params, work):
        self.version = version
        self.width = width
        self.height = height
        self.encoding = encoding
        self.palette = palette
        self.layers = layers
        self.sprite_names = sprite_names
        self.assets = assets
        self.params = params
        self.work = work

    def render_rgba(self):
        accumulator = bytearray(self.width * self.height * 4)
        for index, layer in enumerate(self.layers):
            surface = Surface(self.width, self.height)
            execute_ops(surface, self.palette, layer.ops)
            if index == 0 and layer.blend == "normal":
                # §6.7 第 1 条的算术推论:normal 层叠在空累加器上逐像素恒等。
                accumulator[:] = surface.data
            else:
                blend_layer(accumulator, surface, layer.blend, self.width, self.height)
        return bytes(accumulator)


class _OpDraft(object):
    __slots__ = ("name", "raw", "known", "ints", "rows")

    def __init__(self, name, raw, known):
        self.name = name
        self.raw = raw
        self.known = known
        self.ints = {}
        self.rows = None


class _LayerDraft(object):
    __slots__ = ("name", "blend", "ops")

    def __init__(self, name, blend, ops):
        self.name = name
        self.blend = blend
        self.ops = ops


class Builder(object):
    """按阶段序校验一份具体文档(不含参引),产出 Doc。"""

    def __init__(self, root, byte_length, version, asset_decls=None, assets=None, params=None):
        self.root = root
        self.byte_length = byte_length
        self.version = version
        self.is_v1 = version == 1
        #: v1 的 asset 声明(名字 -> AssetDecl);v0 恒为空。声明名在阶段 9 用于存在性检查。
        self.asset_decls = asset_decls or {}
        #: 已解析的 asset(名字 -> ResolvedAsset);阶段 R2 之前为空。
        self.assets = assets or {}
        self.params = params or {}
        self.work = 0
        self.width = 0
        self.height = 0
        self.encoding = ""
        self.token_width = 1
        self.palette_text = []
        self.draft_layers = []
        self.draft_sprites = {}

    # ------------------------------------------------------------ 阶段 3

    def stage3(self):
        root = self.root
        if not _is_object(root):
            raise PxlError("PXL001", "顶层必须是 JSON 对象,而不是 " + _type_name(root))
        allowed = set(["format", "version", "encoding", "width", "height", "palette",
                       "sprites", "layers"])
        if self.is_v1:
            allowed.update(("assets", "params"))
        for name in root:
            if name not in allowed:
                raise PxlError("PXL001", "顶层出现未定义成员: " + name)
        required = ["format", "version", "encoding", "width", "height", "layers"]
        if not self.is_v1:
            required.append("palette")
        for name in required:
            if name not in root:
                raise PxlError("PXL001", "顶层缺少必需成员: " + name)
        if not _is_string(root["format"]) or root["format"] != "PXL":
            raise PxlError("PXL001", "format 必须是字符串 PXL")
        if not _is_number(root["version"]):
            raise PxlError("PXL001", "version 必须是数字")
        if not _is_string(root["encoding"]):
            raise PxlError("PXL001", "encoding 必须是字符串")
        for name in ("width", "height"):
            if not _is_number(root[name]):
                raise PxlError("PXL001", name + " 必须是数字,而不是 " + _type_name(root[name]))
        if "palette" in root:
            if not _is_array(root["palette"]):
                raise PxlError("PXL001", "palette 必须是数组")
            for index, item in enumerate(root["palette"]):
                if not _is_string(item):
                    raise PxlError("PXL001", "palette[" + str(index) + "] 不是字符串,而是 "
                                   + _type_name(item))
        if "sprites" in root:
            sprites = root["sprites"]
            if not _is_object(sprites):
                raise PxlError("PXL001", "sprites 必须是对象")
            for name in sprites:
                if not name_is_valid(name):
                    raise PxlError("PXL001", "sprite 名不合法: " + name)
                self._check_row_array(sprites[name], "sprites." + name)
        layers = root["layers"]
        if not _is_array(layers):
            raise PxlError("PXL001", "layers 必须是数组")
        if not layers:
            raise PxlError("PXL001", "layers 至少要有 1 个图层")
        seen = set()
        for index, layer in enumerate(layers):
            where = "layers[" + str(index) + "]"
            if not _is_object(layer):
                raise PxlError("PXL001", where + " 必须是对象")
            for name in layer:
                if name not in ("name", "blend", "ops"):
                    raise PxlError("PXL001", where + " 出现未定义成员: " + name)
            for name in ("name", "blend", "ops"):
                if name not in layer:
                    raise PxlError("PXL001", where + " 缺少必需成员: " + name)
            if not _is_string(layer["name"]) or not name_is_valid(layer["name"]):
                raise PxlError("PXL001", where + ".name 不合法")
            if layer["name"] in seen:
                raise PxlError("PXL001", "图层名重复: " + layer["name"])
            seen.add(layer["name"])
            if not _is_string(layer["blend"]) or layer["blend"] not in BLENDS:
                raise PxlError("PXL001", where + ".blend 不在 normal/multiply/mask 之内")
            if not _is_array(layer["ops"]):
                raise PxlError("PXL001", where + ".ops 必须是数组")
            drafts = []
            for op_index, op in enumerate(layer["ops"]):
                drafts.append(self._check_op(op, where + ".ops[" + str(op_index) + "]"))
            self.draft_layers.append(_LayerDraft(layer["name"], layer["blend"], drafts))
        self.draft_sprites = dict(root.get("sprites", {}))

    def _check_row_array(self, value, where):
        if not _is_array(value):
            raise PxlError("PXL001", where + " 必须是行数组")
        if not value:
            raise PxlError("PXL001", where + " 是空数组(没有 0 行矩形)")
        for index, row in enumerate(value):
            if not _is_string(row):
                raise PxlError("PXL001", where + "[" + str(index) + "] 不是字符串")
            if not row:
                raise PxlError("PXL001", where + "[" + str(index) + "] 是空串(没有 0 列矩形)")
        expected = len(value[0])
        for row in value:
            if len(row) != expected:
                raise PxlError("PXL001", where + " 内各行长度不相等")

    def _check_op(self, op, where):
        if not _is_object(op):
            raise PxlError("PXL001", where + " 必须是对象")
        if "op" not in op:
            raise PxlError("PXL001", where + " 缺少 op 成员")
        name = op["op"]
        if not _is_string(name):
            raise PxlError("PXL001", where + ".op 必须是字符串")
        if name not in OP_REQUIRED:
            # 未知 op:op 专属检查一律跳过(§11.1),留给阶段 8 报 PXL004。
            return _OpDraft(name, op, False)
        required = list(OP_REQUIRED[name])
        optional = list(OP_OPTIONAL[name])
        if name == "stamp":
            origins = [member for member in ("sprite", "asset") if member in op]
            if len(origins) != 1:
                raise PxlError("PXL001", where + ": stamp 必须恰好给出 sprite 或 asset 之一")
            if origins[0] == "asset" and not self.is_v1:
                raise PxlError("PXL001", where + ": v0 的 stamp 只接受 sprite")
            required.append(origins[0])
            for other in ("sprite", "asset"):
                if other != origins[0] and other in op:
                    raise PxlError("PXL001", where + " 出现多余成员: " + other)
        for member in required:
            if member not in op:
                raise PxlError("PXL001", where + " 缺少必需成员: " + member)
        allowed_members = set(["op"]) | set(required) | set(optional)
        for member in op:
            if member not in allowed_members:
                raise PxlError("PXL001", where + " 出现未定义成员: " + member)
        for member in optional:
            if member in op and not _is_boolean(op[member]):
                raise PxlError("PXL001", where + "." + member + " 必须是布尔值")
        for member in ("sprite", "asset"):
            if member in op and not _is_string(op[member]):
                raise PxlError("PXL001", where + "." + member + " 必须是字符串")
        if name == "grid":
            self._check_row_array(op["grid"], where + ".grid")
        if name == "mirror":
            for key in ("from", "to"):
                if not _is_object(op[key]):
                    raise PxlError("PXL001", where + "." + key + " 必须是对象")
            for member in op["from"]:
                if member not in ("x", "y", "w", "h"):
                    raise PxlError("PXL001", where + ".from 出现未定义成员: " + member)
            for member in ("x", "y", "w", "h"):
                if member not in op["from"]:
                    raise PxlError("PXL001", where + ".from 缺少必需成员: " + member)
            for member in op["to"]:
                if member not in ("x", "y"):
                    raise PxlError("PXL001", where + ".to 出现未定义成员: " + member)
            for member in ("x", "y"):
                if member not in op["to"]:
                    raise PxlError("PXL001", where + ".to 缺少必需成员: " + member)
        # 数值成员的类型类别(是否为数字)在这一阶段判;整数值与区间留到阶段 6。
        for member, _ in OP_NUMERIC[name]:
            if "." in member:
                owner, leaf = member.split(".")
                value = op[owner][leaf]
            else:
                value = op[member]
            if not _is_number(value):
                raise PxlError("PXL001", where + "." + member + " 必须是数字,而不是 "
                               + _type_name(value))
        return _OpDraft(name, op, True)

    # ------------------------------------------------------------ 阶段 4

    def stage4(self):
        encoding = self.root["encoding"]
        if encoding not in TIER_WIDTH:
            raise PxlError("PXL003", "encoding 不在 char/index/index4 之内: " + encoding)
        self.encoding = encoding
        self.token_width = TIER_WIDTH[encoding]

    # ------------------------------------------------------------ 阶段 5

    def stage5(self):
        allowed = self._allowed_tokens()
        for where, rows in self._all_row_arrays():
            for index, row in enumerate(rows):
                if len(row) % self.token_width != 0:
                    raise PxlError("PXL001", where + "[" + str(index) + "] 的字符数 "
                                   + str(len(row)) + " 不是 token 宽度 "
                                   + str(self.token_width) + " 的正整数倍")
                for char in row:
                    if char not in allowed:
                        raise PxlError("PXL001", where + "[" + str(index) + "] 含非法 token 字符 "
                                       + _escape_char(char))

    def _allowed_tokens(self):
        if self.encoding == "char":
            return set(CHAR_TOKENS) | set(".")
        return set(HEX_TOKENS)

    def _all_row_arrays(self):
        items = []
        for layer in self.draft_layers:
            for op in layer.ops:
                if op.known and op.name == "grid":
                    items.append(("grid", op.raw["grid"]))
        for name, rows in sorted(self.draft_sprites.items()):
            items.append(("sprites." + name, rows))
        return items

    # ------------------------------------------------------------ 阶段 6

    def stage6(self):
        root = self.root
        self.width = self._int(root["width"], SIZE[0], SIZE[1], "width")
        self.height = self._int(root["height"], SIZE[0], SIZE[1], "height")
        area = self.width * self.height
        if area > MAX_AREA:
            raise PxlError("PXL002", "面积 " + str(area) + " 超过 2^22")
        if len(self.draft_layers) > MAX_LAYERS:
            raise PxlError("PXL002", "图层数 " + str(len(self.draft_layers)) + " 超过 256")
        if len(self.draft_layers) * area > MAX_LAYER_PIXELS:
            raise PxlError("PXL002", "图层数 x 面积超过 2^24")
        for layer in self.draft_layers:
            if len(layer.ops) > MAX_OPS_PER_LAYER:
                raise PxlError("PXL002", "图层 " + layer.name + " 的 ops 数 "
                               + str(len(layer.ops)) + " 超过 32768")
        if len(self.draft_sprites) > MAX_SPRITES:
            raise PxlError("PXL002", "sprites 个数超过 4096")
        for name in sorted(self.draft_sprites):
            rows = self.draft_sprites[name]
            if len(rows) > MAX_SPRITE_ROWS:
                raise PxlError("PXL002", "sprite " + name + " 的行数超过 65536")
            tokens = len(rows[0]) // self.token_width
            if tokens > MAX_ROW_TOKENS:
                raise PxlError("PXL002", "sprite " + name + " 每行 token 数超过 65536")
        for layer in self.draft_layers:
            for op in layer.ops:
                if not op.known:
                    continue
                for member, bounds in OP_NUMERIC[op.name]:
                    if "." in member:
                        owner, leaf = member.split(".")
                        node = op.raw[owner][leaf]
                    else:
                        node = op.raw[member]
                    op.ints[member] = self._int(node, bounds[0], bounds[1],
                                                op.name + "." + member)
                if op.name == "grid":
                    tokens = len(op.raw["grid"][0]) // self.token_width
                    if tokens > MAX_ROW_TOKENS:
                        raise PxlError("PXL002", "grid 每行 token 数 " + str(tokens)
                                       + " 超过 65536")
        if len(self.params) > MAX_PARAMS:
            raise PxlError("PXL002", "params 声明数 " + str(len(self.params)) + " 超过 256")
        if len(self.asset_decls) > MAX_ASSET_DECLS:
            raise PxlError("PXL002", "assets 声明数 " + str(len(self.asset_decls)) + " 超过 4096")
        self.recheck_raster()

    def recheck_raster(self):
        """按当前已知的 asset 尺寸重算本文档的光栅工作量(§5.0.2 的 2^22)。"""
        work = 0
        for layer in self.draft_layers:
            work += raster_work(self.width, self.height, self._geometry_plan(layer))
        if work > MAX_RASTER_WORK:
            raise PxlError("PXL002", "光栅工作 " + str(work) + " 超过 2^22")
        self.work = work

    def _int(self, node, low, high, what):
        value = numlit.integer_value(node.text)
        if value is None:
            raise PxlError("PXL002", what + " 不是整数值: " + node.text)
        if value is numlit.HUGE:
            raise PxlError("PXL002", what + " 超出可表示范围: " + node.text)
        if value < low or value > high:
            raise PxlError("PXL002", what + " = " + str(value) + " 不在 " + str(low) + ".."
                           + str(high) + " 之内")
        return value

    def _geometry_plan(self, layer):
        """只为光栅计数服务的几何计划:不含 slot,不含 token 解码。"""
        plan = []
        for op in layer.ops:
            if not op.known:
                continue
            if op.name == "fill":
                plan.append(("fill", 0))
            elif op.name == "rect":
                plan.append(("rect", op.ints["x"], op.ints["y"], op.ints["w"], op.ints["h"], 0))
            elif op.name == "pixel":
                plan.append(("pixel", op.ints["x"], op.ints["y"], 0))
            elif op.name == "erase":
                plan.append(("erase", op.ints["x"], op.ints["y"], op.ints["w"], op.ints["h"]))
            elif op.name == "line":
                plan.append(("line", op.ints["x0"], op.ints["y0"], op.ints["x1"],
                             op.ints["y1"], 0))
            elif op.name == "grid":
                rows = op.raw["grid"]
                # 光栅计数按 token 列数,不按字符数(§3.4:上界限制的是 token 数)。
                columns = len(rows[0]) // self.token_width
                plan.append(("grid", op.ints["x"], op.ints["y"], [["0"] * columns] * len(rows)))
            elif op.name == "mirror":
                plan.append(("mirror",
                             (op.ints["from.x"], op.ints["from.y"], op.ints["from.w"],
                              op.ints["from.h"]),
                             (op.ints["to.x"], op.ints["to.y"]), False, False))
            elif op.name == "stamp":
                plan.append(self._geometry_stamp(op))
        return plan

    def _geometry_stamp(self, op):
        origin = "sprite" if "sprite" in op.raw else "asset"
        name = op.raw[origin]
        if origin == "sprite":
            rows = self.draft_sprites.get(name)
            if rows is None:
                return ("pixel", 1 << 30, 1 << 30, 0)  # 引用不存在:阶段 9 会报,这里记 0 工作
            cols = len(rows[0]) // self.token_width
            height = len(rows)
        else:
            asset = self.assets.get(name)
            if asset is None or asset.width is None:
                return ("pixel", 1 << 30, 1 << 30, 0)
            # asset stamp **不计入单文档** 2^22:面积取决于被引用资源尺寸,而那要等 R2 才拿到,
            # 单文档预算却属阶段 6。它按 §10.3 计入会话全局光栅(见 asset_stamp_work)。
            return ("stamp_asset", AssetStamp(op.ints["x"], op.ints["y"],
                                             False, False, asset))
        return ("stamp", [b"\x00" * (cols * 4)] * height, op.ints["x"], op.ints["y"],
                bool(op.raw.get("flipX", False)), bool(op.raw.get("flipY", False)), cols)

    def asset_stamp_work(self):
        """asset stamp 的写入面积之和(§10.3):按实际裁剪后的面积、每次 stamp 各计一次。

        与 Java 侧 PxlCompiler.resolveAssets 逐句对应:同一口径,只是那边的累加发生在
        R2 里、这里在 R2 之后统一算。
        """
        total = 0
        for layer in self.draft_layers:
            for op in layer.ops:
                if not op.known or op.name != "stamp" or "asset" not in op.raw:
                    continue
                asset = self.assets.get(op.raw["asset"])
                if asset is None or asset.width is None:
                    continue
                total += clip_area(self.width, self.height, op.ints["x"], op.ints["y"],
                                   asset.width, asset.height)
        return total

    # ------------------------------------------------------------ 阶段 7

    def stage7(self):
        palette = self.root.get("palette", [])
        count = len(palette)
        if count >= 65536:
            return  # 无档位可派生,跳过本阶段,留给阶段 9 报 PXL005
        derived = ("char" if count <= TIER_MAX_SLOT["char"]
                   else ("index" if count <= TIER_MAX_SLOT["index"] else "index4"))
        if self.encoding != derived:
            raise PxlError("PXL003", "palette 长度 " + str(count) + " 派生出的档位是 "
                           + derived + ",与声明的 " + self.encoding + " 不一致")

    # ------------------------------------------------------------ 阶段 8

    def stage8(self):
        for layer in self.draft_layers:
            for op in layer.ops:
                if not op.known:
                    raise PxlError("PXL004", "未知 op: " + op.name)

    # ------------------------------------------------------------ 阶段 9

    def stage9(self):
        palette = self.root.get("palette", [])
        count = len(palette)
        if count >= 65536:
            raise PxlError("PXL005", "palette 长度 " + str(count) + " 超过 65535")
        for index, item in enumerate(palette):
            if len(item) != 8 or any(char not in HEX_TOKENS for char in item):
                raise PxlError("PXL005", "palette[" + str(index) + "] 不是八位大写十六进制: "
                               + item)
            if item == "00000000":
                raise PxlError("PXL005", "palette[" + str(index) + "] 是透明值 00000000")
        self.palette_text = list(palette)
        limit = min(TIER_MAX_SLOT[self.encoding], count)
        for layer in self.draft_layers:
            for op in layer.ops:
                if "slot" in op.ints and op.ints["slot"] > limit:
                    raise PxlError("PXL005", "slot " + str(op.ints["slot"])
                                   + " 超过可用上限 " + str(limit))
        for where, rows in self._all_row_arrays():
            for index, row in enumerate(rows):
                for slot in self._decode_row(row, count, where + "[" + str(index) + "]"):
                    pass
        for layer in self.draft_layers:
            for op in layer.ops:
                if op.name != "stamp":
                    continue
                if "sprite" in op.raw:
                    if op.raw["sprite"] not in self.draft_sprites:
                        raise PxlError("PXL005", "stamp 引用了不存在的 sprite: "
                                       + op.raw["sprite"])
                elif self.is_v1 and op.raw["asset"] not in self.asset_decls:
                    raise PxlError("PXL005", "stamp 引用了未声明的 asset: " + op.raw["asset"])

    def _decode_row(self, row, palette_size, where):
        slots = []
        width = self.token_width
        if self.encoding == "char":
            for char in row:
                if char == ".":
                    slots.append(0)
                else:
                    slots.append(CHAR_TOKENS.index(char) + 1)
        else:
            for start in range(0, len(row), width):
                slots.append(int(row[start:start + width], 16))
        for slot in slots:
            if slot > palette_size:
                raise PxlError("PXL005", where + " 的 token 指向 slot " + str(slot)
                               + ",超过 palette 长度 " + str(palette_size))
        return slots

    # ------------------------------------------------------------ 计划

    def build(self):
        palette = [ZERO]
        for text in self.palette_text:
            palette.append(to_texel(text))
        sprite_rows = {}
        for name in self.draft_sprites:
            rows = self.draft_sprites[name]
            decoded = []
            for row in rows:
                slots = self._decode_row(row, len(self.palette_text), "sprites." + name)
                decoded.append(b"".join([palette[slot] for slot in slots]))
            sprite_rows[name] = decoded
        layers = []
        for layer in self.draft_layers:
            plan = []
            for op in layer.ops:
                plan.append(self._plan_op(op, palette, sprite_rows))
            layers.append(LayerPlan(layer.name, layer.blend, plan))
        return Doc(self.version, self.width, self.height, self.encoding, palette, layers,
                   sprite_rows, self.assets, self.params, getattr(self, "work", 0))

    def _plan_op(self, op, palette, sprite_rows):
        name = op.name
        if name == "fill":
            return ("fill", op.ints["slot"])
        if name == "rect":
            return ("rect", op.ints["x"], op.ints["y"], op.ints["w"], op.ints["h"],
                    op.ints["slot"])
        if name == "pixel":
            return ("pixel", op.ints["x"], op.ints["y"], op.ints["slot"])
        if name == "erase":
            return ("erase", op.ints["x"], op.ints["y"], op.ints["w"], op.ints["h"])
        if name == "line":
            return ("line", op.ints["x0"], op.ints["y0"], op.ints["x1"], op.ints["y1"],
                    op.ints["slot"])
        if name == "grid":
            rows = [self._decode_row(row, len(palette) - 1, "grid") for row in op.raw["grid"]]
            return ("grid", op.ints["x"], op.ints["y"], rows)
        if name == "mirror":
            return ("mirror",
                    (op.ints["from.x"], op.ints["from.y"], op.ints["from.w"],
                     op.ints["from.h"]),
                    (op.ints["to.x"], op.ints["to.y"]),
                    bool(op.raw.get("flipX", False)), bool(op.raw.get("flipY", False)))
        if name == "stamp":
            flipx = bool(op.raw.get("flipX", False))
            flipy = bool(op.raw.get("flipY", False))
            if "sprite" in op.raw:
                rows = sprite_rows[op.raw["sprite"]]
                cols = len(rows[0]) // 4
            else:
                # asset 的行必须等**相位 C** 才有(rgba 要等子节点光栅化完),所以计划里只放
                # asset 对象本身;切行由 raster 在执行时做。
                return ("stamp_asset", AssetStamp(op.ints["x"], op.ints["y"], flipx,
                                                  flipy, self.assets[op.raw["asset"]]))
            return ("stamp", rows, op.ints["x"], op.ints["y"], flipx, flipy, cols)
        raise AssertionError("未处理的 op: " + name)


def _escape_char(char):
    code = ord(char)
    if code < 0x20 or code == 0x7F:
        return "U+" + format(code, "04X")
    return repr(char)
