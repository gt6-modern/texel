"""参数、bindings 与替换(v1 §4 / §6)。

三件事按固定顺序做,顺序本身是规范的一部分:

1. 校验 params 声明语法(§6.1)与 assets 声明结构(§5.1);
2. 校验 bindings 集合与类型(§6.3):只能绑入口声明的参数、缺必需参数即错、强类型;
3. 完成替换,得到一份不含参引的具体文档;之后的判定全部按目标字段的原码走(§6.5)。

参引只允许出现在 §6.2 的白名单位置,其它位置出现以 $ 开头的字符串一律 PXL012。
"""

from . import numlit
from .errors import PxlError
from .jsonx import JsonNumber

PARAM_TYPES = ("color", "integer", "boolean", "asset", "sprite")
ASSET_KINDS = ("pxl", "png")

HEX_UPPER = set("0123456789ABCDEF")

OP_INTEGER_MEMBERS = {
    "fill": ("slot",),
    "rect": ("x", "y", "w", "h", "slot"),
    "pixel": ("x", "y", "slot"),
    "line": ("x0", "y0", "x1", "y1", "slot"),
    "grid": ("x", "y"),
    "mirror": (),
    "erase": ("x", "y", "w", "h"),
    "stamp": ("x", "y"),
}

#: mirror 的嵌套坐标：from / to 的成员分别收口（§6.2）。
OP_NESTED_MEMBERS = {"from": ("x", "y", "w", "h"), "to": ("x", "y")}

_NUMERIC_MEMBERS = ("x", "y", "x0", "y0", "x1", "y1", "w", "h", "slot")


def name_is_valid(name):
    """§4.1:[a-z][a-z0-9_]{0,63}。"""
    if not isinstance(name, str) or not name or len(name) > 64:
        return False
    if not ("a" <= name[0] <= "z"):
        return False
    for char in name[1:]:
        if not ("a" <= char <= "z" or "0" <= char <= "9" or char == "_"):
            return False
    return True


class ParamDecl(object):
    __slots__ = ("name", "kind", "default", "has_default")

    def __init__(self, name, kind, default, has_default):
        self.name = name
        self.kind = kind
        self.default = default
        self.has_default = has_default


class AssetDecl(object):
    __slots__ = ("name", "kind", "path", "params")

    def __init__(self, name, kind, path, params):
        self.name = name
        self.kind = kind
        self.path = path
        self.params = params


def _is_object(value):
    return isinstance(value, dict)


def _is_string(value):
    return isinstance(value, str)


def parse_params(node):
    """§6.1:params 是对象,每项恰好 {type, default?};type 五个值之一。"""
    if node is None:
        return {}
    if not _is_object(node):
        raise PxlError("PXL001", "params 必须是对象")
    result = {}
    for name in node:
        if not name_is_valid(name):
            raise PxlError("PXL001", "参数名不合法: " + name)
        entry = node[name]
        if not _is_object(entry):
            raise PxlError("PXL001", "参数 " + name + " 的声明必须是对象")
        for member in entry:
            if member not in ("type", "default"):
                raise PxlError("PXL001", "参数 " + name + " 出现未定义成员: " + member)
        if "type" not in entry:
            raise PxlError("PXL001", "参数 " + name + " 缺少 type")
        kind = entry["type"]
        if not _is_string(kind) or kind not in PARAM_TYPES:
            raise PxlError("PXL001", "参数 " + name + " 的 type 不在五个允许值之内")
        if "default" in entry:
            # default 必须是**声明类型的 JSON 字面量**：字符串 "1" 不是整数
            # （发布态复核：Python 曾接受、Java 报 PXL012 —— 类型混用）。
            _check_typed(kind, entry["default"], "参数 " + name + " 的 default")
            value = _canonical_value(kind, entry["default"], None, None,
                                     "参数 " + name + " 的 default")
            result[name] = ParamDecl(name, kind, value, True)
        else:
            result[name] = ParamDecl(name, kind, None, False)
    return result


def _check_text(name):
    if not isinstance(name, str) or len(name) != 8:
        return False
    for char in name:
        if char not in HEX_UPPER:
            return False
    return True


#: §6.2 的 op 白名单：只有这些 op 的特定成员允许参引。
_KNOWN_OPS = ("fill", "rect", "pixel", "line", "grid", "mirror", "erase", "stamp")


def _reference_allowed(path, member, op_kind):
    # palette 的元素：父路径就是 ("palette",)（成员是下标）。
    if len(path) == 1 and path[0] == "palette":
        # palette 的元素必须是数组下标（W28）。
        return isinstance(member, int)
    if len(path) == 3 and path[0] == "assets" and path[2] == "params" \
            and not isinstance(path[1], int):
        # 子 bindings 必须按参数名索引（W29）。
        return not isinstance(member, int)
    # 只认 layers[<int>].ops[<int>] 这种**真实数组下标**路径；对象键不能冒充（W11）。
    if len(path) >= 4 and path[0] == "layers" and path[2] == "ops" \
            and isinstance(path[1], int) and isinstance(path[3], int):
        if op_kind not in _KNOWN_OPS:
            return False
        if len(path) == 4 and member in OP_INTEGER_MEMBERS.get(op_kind, ()):
            return True
        # 嵌套坐标只限 mirror 的 from/to，且必须是这个 op 对象下的直接子对象。
        if (len(path) == 5 and path[4] in ("from", "to") and op_kind == "mirror"
                and member in OP_NESTED_MEMBERS.get(path[4], ())):
            return True
        if len(path) == 4 and op_kind == "stamp" and member in ("flipX", "flipY"):
            return True
        if len(path) == 4 and op_kind == "stamp" and member in ("asset", "sprite"):
            return True
    return False


def scan_references(node, path=(), op_kind=None):
    """§11.2 行 3：参数阶段先于结构/op 阶段——非白名单位置的参引先报 PXL012。

    op 类型要**沿路径传下去**：ops[0].from 里没有 op 字段，靠当前对象取不到
    （发布态复核 W02/W03 的嵌套坐标就栽在这里）。
    """
    if _is_string(node):
        # 数组里的字符串也要判（E08）：否则会落到结构阶段报 PXL001。
        if node.startswith("$") and not _reference_allowed(path[:-1], path[-1] if path else "", op_kind):
            raise PxlError("PXL012", "参引 " + node + " 出现在不允许的位置: " + path_text(path))
        return
    if isinstance(node, dict):
        # 只有真正的 layers[i].ops[j] 对象才提供 op selector（W07 的 from.op 不能覆盖它）。
        is_op_object = (len(path) == 4 and path[0] == "layers" and path[2] == "ops")
        here = op_kind
        if is_op_object and _is_string(node.get("op")):
            here = node.get("op")
        for key in node:
            value = node[key]
            if _is_string(value) and value.startswith("$"):
                if not _reference_allowed(path, key, here):
                    raise PxlError("PXL012", "参引 " + value + " 出现在不允许的位置: "
                                    + path_text(path + (key,)))
            else:
                scan_references(value, path + (key,), here)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            scan_references(item, path + (index,), op_kind)

def validate_symbol_defaults(params, assets, sprites):
    """§6.4/§6.5：asset / sprite 的 default 必须是本文档已声明的名字，否则 PXL012。

    只在**使用位置**才发现（PXL005）是错的：默认值本身就是参数声明的一部分，
    发布态复核 D01–D04 指出两侧都漏了这一步。
    """
    for name in sorted(params):
        decl = params[name]
        if not decl.has_default:
            continue
        if decl.kind == "asset" and decl.default not in assets:
            raise PxlError("PXL012", "参数 " + name + " 的 default 引用了未声明的 asset: "
                            + str(decl.default))
        if decl.kind == "sprite" and decl.default not in sprites:
            raise PxlError("PXL012", "参数 " + name + " 的 default 引用了未声明的 sprite: "
                            + str(decl.default))


def _canonical_value(kind, value, sprites, assets, where):
    """把 JSON 载体里的值按声明类型转成规范值;类型不符即 PXL012(§6.3)。"""
    if kind == "color":
        if not _is_string(value) or not _check_text(value):
            raise PxlError("PXL012", where + ": color 必须是八位大写十六进制字符串")
        return value
    if kind == "integer":
        if isinstance(value, JsonNumber):
            number = numlit.integer_value(value.text)
            # HUGE 只表示「合法但肯定越域」：保留原字面量交给使用位置判范围（§6.5 → PXL002）。
            if number is None:
                raise PxlError("PXL012", where + ": integer 必须是整数值")
            if number is numlit.HUGE:
                return value
            return number
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if _is_string(value):
            number = numlit.integer_value(value)
            if number is None:
                raise PxlError("PXL012", where + ": integer 不是规范十进制字面量: " + value)
            if number is numlit.HUGE:
                # 先过规范拼写（1e41 / 前导零 / 尾随 .0 都不是规范整数），再保留字面量：
                # 巨大整数只是「合法但肯定越域」，拼写必须仍然规范（N05–N07、N09）。
                if not numlit.canonical_integer(value):
                    raise PxlError("PXL012", where + ": integer 不是规范十进制字面量: " + value)
                return JsonNumber(value)
            # N07（发布态复核 P2）：规范以"数学整数值"作为身份（§6.3），所以 -0 与 0 等价，
            # 允许 -0 归一化为 0。这里原先把 str(number) 与原文逐字比对，把 -0 误拒成
            # "不是规范十进制字面量"。
            if value != str(number) and not (number == 0 and value == "-0"):
                raise PxlError("PXL012", where + ": integer 不是规范十进制字面量: " + value)
            return number
        raise PxlError("PXL012", where + ": integer 的类型不匹配")
    if kind == "boolean":
        # 只收**真布尔**：这个函数同时校验文档里的 params.*.default，放宽会改变文档接受集
        # （发布态复核 P2：default:"false" 曾被 Python 接受、Java 报 PXL012）。
        # CLI 的文本解析在 canonical_bindings 里做，见 _bind_text。
        if value is True or value is False:
            return value
        raise PxlError("PXL012", where + ": boolean 的类型不匹配")
    if kind in ("asset", "sprite"):
        if not _is_string(value):
            raise PxlError("PXL012", where + ": " + kind + " 必须是字符串")
        if kind == "asset" and assets is not None and value not in assets:
            raise PxlError("PXL012", where + ": 引用了未声明的 asset " + value)
        if kind == "sprite" and sprites is not None and value not in sprites:
            raise PxlError("PXL012", where + ": 引用了未声明的 sprite " + value)
        return value
    raise PxlError("PXL012", where + ": 未知参数类型 " + str(kind))


def parse_assets(node):
    """§5.1:assets 是对象,每项恰好 {kind, path, params?}。"""
    if node is None:
        return {}
    if not _is_object(node):
        raise PxlError("PXL001", "assets 必须是对象")
    result = {}
    for name in node:
        if not name_is_valid(name):
            raise PxlError("PXL001", "asset 名不合法: " + name)
        entry = node[name]
        if not _is_object(entry):
            raise PxlError("PXL001", "asset " + name + " 的声明必须是对象")
        for member in entry:
            if member not in ("kind", "path", "params"):
                raise PxlError("PXL001", "asset " + name + " 出现未定义成员: " + member)
        for member in ("kind", "path"):
            if member not in entry:
                raise PxlError("PXL001", "asset " + name + " 缺少 " + member)
        kind = entry["kind"]
        if not _is_string(kind) or kind not in ASSET_KINDS:
            raise PxlError("PXL001", "asset " + name + " 的 kind 不是 pxl/png")
        if not _is_string(entry["path"]):
            raise PxlError("PXL001", "asset " + name + " 的 path 必须是字符串")
        if "params" in entry and entry["params"] is None:
            raise PxlError("PXL001", "asset " + name + " 的 params 必须是对象")
        params = entry.get("params")
        if params is not None:
            if not _is_object(params):
                raise PxlError("PXL001", "asset " + name + " 的 params 必须是对象")
            if kind == "png":
                raise PxlError("PXL001", "asset " + name + " 是 png,不得有 params")
        result[name] = AssetDecl(name, kind, entry["path"], params or {})
    return result


def _bind_text(kind, value):
    """把**CLI/JSON 绑定**里的文本拼写还原成强类型值。

    只对 boolean 生效：CLI 的 --bind 一律给字符串，而文档里的 default 是 JSON 字面量。
    把这两条路径分开，才不会「修 CLI 时顺手放宽了文档接受集」（发布态复核 P2）。
    """
    if kind == "boolean" and isinstance(value, str):
        if value == "true":
            return True
        if value == "false":
            return False
    return value


def _check_typed(kind, value, where):
    """文档里的子文档参数值必须是**声明类型的 JSON 字面量**（§6.3）。

    _canonical_value 为了 CLI 绑定要接受文本形式，所以这里单独判一次 JSON 类型：
    数字不能当颜色、字符串不能当整数。"""
    if kind == "integer":
        # JSON 解析器保留的是 JsonNumber（不是 int）：只认 int 会把**合法的 JSON 整数**
        # 子绑定拒掉（发布态复核 P1）。这里与 _canonical_value 用同一个判定。
        if isinstance(value, JsonNumber):
            # 规范拼写：1.0 / 1e0 数学上是整数，但不是 §6.4 的整数字面量（Java 报 PXL012）。
            if not numlit.canonical_integer(value.text):
                raise PxlError("PXL012", where + ": integer 参数必须是 JSON 整数")
            number = numlit.integer_value(value.text)
            # HUGE 只是「合法但肯定越域」的哨兵：拼写合法就算整数，
            # 范围由使用位置判（坐标越界是 PXL002，§6.5；发布态复核 R3）。
            if number is None:
                raise PxlError("PXL012", where + ": integer 参数必须是 JSON 整数")
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise PxlError("PXL012", where + ": integer 参数必须是 JSON 整数")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise PxlError("PXL012", where + ": boolean 参数必须是 JSON 布尔")
        return
    if kind in ("color", "asset", "sprite"):
        if not isinstance(value, str):
            raise PxlError("PXL012", where + ": " + kind + " 参数必须是 JSON 字符串")


def canonical_bindings(params, raw, sprites, assets, typed=False, source_kinds=None):
    """§6.3:只绑入口声明的参数;缺必需参数、强类型不符都是 PXL012。

    typed=True 表示值来自文档里的子文档 params（JSON 字面量）：按强类型校验，
    不做文本解析（§6.3：类型必须与声明一致）。"""
    bound = {}
    for name in sorted(raw):
        if name not in params:
            raise PxlError("PXL012", "bindings 绑定了未声明的参数: " + name)
        decl = params[name]
        given = raw[name] if typed else _bind_text(decl.kind, raw[name])
        if typed:
            _check_typed(decl.kind, given, "bindings." + name)
            # 转发来的值带着**来源参数的声明类型**：sprite 不能填进 asset 槽
            # （发布态复核 P2：父子转发时两侧都静默接受）。
            origin = None if source_kinds is None else source_kinds.get(name)
            if origin is not None and origin != decl.kind:
                raise PxlError("PXL012", "bindings." + name + ": 来源参数的类型 "
                                + origin + " 与声明的 " + decl.kind + " 不符")
        bound[name] = _canonical_value(decl.kind, given,
                                       sprites, assets, "bindings." + name)
    for name in sorted(params):
        decl = params[name]
        if name in bound:
            continue
        if decl.has_default:
            bound[name] = decl.default
            continue
        raise PxlError("PXL012", "缺少必需参数: " + name)
    return bound


def identity_key(bound):
    """规范 bindings 的稳定表示(用于渲染节点身份,§9.2)。"""
    parts = []
    for name in sorted(bound):
        value = bound[name]
        if value is True:
            text = "true"
        elif value is False:
            text = "false"
        elif isinstance(value, int):
            text = str(value)
        else:
            text = str(value)
        parts.append(name + "=" + text)
    return ";".join(parts)


def _allowed_kinds(path):
    """§6.2 的替换位置白名单。返回允许的参数类型集合,或 None(该位置不允许参引)。"""
    if len(path) == 2 and path[0] == "palette":
        return ("color",)
    # §6.2：from / to 里的同名坐标与直接成员同权（嵌套坐标）。
    if len(path) == 6 and path[0] == "layers" and path[2] == "ops" \
            and path[4] in ("from", "to") and path[5] in _NUMERIC_MEMBERS:
        return ("integer",)
    if len(path) == 5 and path[0] == "layers" and path[2] == "ops":
        member = path[4]
        if member in _NUMERIC_MEMBERS:
            return ("integer",)
        if member in ("flipX", "flipY"):
            return ("boolean",)
        if member == "asset":
            return ("asset",)
        if member == "sprite":
            return ("sprite",)
        return None
    if len(path) == 4 and path[0] == "assets" and path[2] == "params":
        # 子文档参数映射的值可以是父文档参引(§5.2);类型由子文档在 R2 阶段判定。
        return PARAM_TYPES
    return None


def substitute(root, params, bound, sprites, assets, forwarded=None):
    """把白名单位置上的参引替换成具体值;其它位置的 $ 字符串一律 PXL012。"""

    def walk(node, path):
        if _is_string(node):
            if not node.startswith("$"):
                return node
            allowed = _allowed_kinds(path)
            if allowed is None:
                raise PxlError("PXL012", "参引出现在不允许的位置: " + path_text(path))
            name = node[1:]
            if not name_is_valid(name):
                raise PxlError("PXL012", "参引不合法: " + node)
            if name not in params:
                raise PxlError("PXL012", "参引了未声明的参数: " + node)
            if params[name].kind not in allowed:
                raise PxlError("PXL012", "参引 " + node + " 的类型 " + params[name].kind
                               + " 不允许出现在 " + path_text(path))
            return bound[name]
        if isinstance(node, dict):
            # §6.2 只允许 boolean 参引进 **stamp** 的 flipX/flipY；mirror 上是白名单外
            # （发布态复核 W01：Python 曾接受）。
            if node.get("op") == "mirror":
                for member in ("flipX", "flipY"):
                    candidate = node.get(member)
                    if _is_string(candidate) and candidate.startswith("$"):
                        raise PxlError("PXL012", "参引 " + candidate
                                        + " 不允许出现在 mirror." + member)
            result = {}
            for key in node:
                if (forwarded is not None and len(path) == 3 and path[0] == "assets"
                        and path[2] == "params" and _is_string(node[key])
                        and node[key].startswith("$")):
                    source = node[key][1:]
                    if source in params:
                        forwarded.setdefault(path[1], {})[key] = params[source].kind
                result[key] = walk(node[key], path + (key,))
            return result
        if isinstance(node, list):
            return [walk(item, path + (index,)) for index, item in enumerate(node)]
        return node

    concrete = walk(root, ())
    return _reify_numbers(concrete)


def _reify_numbers(node):
    """替换出来的整数必须变回 JsonNumber,好让后面的阶段仍按"字面量"判定。"""
    if isinstance(node, int) and not isinstance(node, bool):
        return JsonNumber(str(node))
    if isinstance(node, dict):
        return dict((key, _reify_numbers(value)) for key, value in node.items())
    if isinstance(node, list):
        return [_reify_numbers(value) for value in node]
    return node


def path_text(path):
    if not path:
        return "<顶层>"
    parts = []
    for part in path:
        if isinstance(part, int):
            parts.append("[" + str(part) + "]")
        else:
            parts.append(("." if parts else "") + str(part))
    return "".join(parts)
