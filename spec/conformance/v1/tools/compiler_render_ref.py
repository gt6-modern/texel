"""Independent reference oracle for the current component suite.

The oracle accepts only wire version 1. It reuses deterministic raster, blend,
JSON, and digest helpers from the base reference oracle, while implementing
assets, parameters, bindings, logical paths, dependency graphs, and session
budgets here. It never imports compiler production code.

Use ``--selftest`` for version/error dispatch checks, or pass an entry document.
"""
import json
import sys
from pathlib import Path

_BASE_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(_BASE_TOOLS) not in sys.path:
    sys.path.insert(0, str(_BASE_TOOLS))

import pxl_render_ref as base  # noqa: E402  (base reference oracle;复用表见文件头)

# --------------------------------------------------------------------------
# 版本分派(§2)
# --------------------------------------------------------------------------

#: §2:version 的通用分派域,十进制整数 0..65535。
VERSION_MIN = 0
VERSION_MAX = 65535

#: 本 oracle **声明支持**的版本。PXL v1 的能力是一次性发布的(PXL.md §1.1),所以这里
#: 没有“部分支持”的中间态:实现了 version 1 的哪一部分都不算支持它。
#: 注意:能力档案 -> 语料组的映射**不在**这里,那是 profiles.tsv 的事(§13.2)。
SUPPORTED_VERSIONS = (1,)


def _fail(code, message):
    """按码失败。错误类型直接复用 base oracle 的 PxlRefError,避免出现第二个异常层次。"""
    raise base.PxlRefError(code, message)


def _generic_object_check(data):
    """阶段 D 前半(PXL.md §11.2 第 2 行):与版本无关的通用 JSON 对象检查。

    复用 base oracle 的两个函数,而不是重写:
    * '_decode_utf8' —— UTF-8 可解码,允许并忽略 BOM;
    * '_load_json'   —— 合法 JSON,且同一对象内成员名不得重复。
    两者失败都报 PXL001(与 v0 其余 PXL001 同码,但阶段不同:这里是阶段 D)。
    """
    if not isinstance(data, bytes):
        _fail("PXL001", "入口必须是原始字节(不接收已解码的字符串)")
    text = base._decode_utf8(data)
    document = base._load_json(text)
    if not isinstance(document, dict):
        _fail("PXL001", "顶层必须是 JSON 对象")
    return document


def _read_format(document):
    """阶段 D 后半:读出 format。

    **只读出、不判值。** 'format' 的取值校验属阶段表第 4 行(= base stage 3),
    而分派发生在它之前的阶段 D;在这里顺手判值会让“format 错 + version 不支持”的文档
    的报错码取决于实现把检查写在哪一步。返回值目前只用于调试输出。
    """
    value = document.get("format")
    return value if isinstance(value, str) else None


def _read_version(document):
    """阶段 D 后半:读 version 并校验它在通用分派域内(§2)。

    §2 的分派域表第一行:缺失、类型类别不符、非整数值、负数、超过 65535 一律 **PXL001**
    (不是 PXL002 —— PXL002 管的是“已分派之后”的数值域)。
    """
    if "version" not in document:
        _fail("PXL001", "缺少 version:无法分派")
    value = document["version"]
    if isinstance(value, bool) or not base._is_number(value):
        _fail("PXL001", "version 的类型不是数字")
    if not isinstance(value, int):
        # Decimal(JSON 带小数的字面量):整数值才合法,按原始词法精确判定(§12 / base §3.8)
        if value != value.to_integral_value():
            _fail("PXL001", "version 不是整数值: %s" % (value,))
        value = int(value)
    if value < VERSION_MIN or value > VERSION_MAX:
        _fail("PXL001", "version %d 越出通用分派域 %d..%d"
              % (value, VERSION_MIN, VERSION_MAX))
    return value


def dispatch(data):
    """阶段 D:通用 JSON 对象检查 -> 读 format 与 version -> 返回 (version, document)。

    本函数是 v1 oracle 唯一“新的判定逻辑”,也是**骨架的唯一实质内容**。
    """
    document = _generic_object_check(data)
    _read_format(document)
    return _read_version(document), document


# --------------------------------------------------------------------------
# 各版本的渲染入口(阶段 2/3/4 逐个补齐)
# --------------------------------------------------------------------------

#: 当前这次渲染的 resolver 与基准目录(单线程 oracle,调用 render_entry 时设置)。
_CURRENT = {"resolver": None, "base": None, "bindings": None}


def _render_version_1(data, document):
    """version 1:参数替换 + asset 解析 + 渲染。

    复用边界(规范 §13.4):光栅仍只经 base oracle —— 图层混合直接调 base._blend_layer,
    本文件在 base raster semantics 上增加 current component semantics:
    参数白名单替换、逻辑路径、依赖遍历(含循环)、asset stamp 的位图写入。
    未覆盖的 op 显式抛 NotImplementedError,绝不返回"近似结果"。
    """
    resolver = _CURRENT["resolver"]
    if resolver is None:
        raise NotImplementedError(
            "version 1 需要 resolver: 请用 render_entry(base_dir, entry, bindings);"
            " direct render_text requires a resolver.")
    return _render_pxl(data, resolver, _CURRENT["base"], [], _CURRENT["bindings"] or {})


_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_./")


def _normalize_path(text, where="path"):
    if not isinstance(text, str) or not text:
        _fail("PXL008", "%s 为空" % where)
    for ch in text:
        if ch not in _ALLOWED:
            _fail("PXL008", "%s 出现非法字符 %r(只允许 a-z 0-9 - _ . /)" % (where, ch))
    if text.startswith("/") or text.endswith("/"):
        _fail("PXL008", "%s 不得以 / 开头或结尾: %s" % (where, text))
    stack = []
    for segment in text.split("/"):
        if not segment:
            _fail("PXL008", "%s 含空段: %s" % (where, text))
        if len(segment) > 255:
            _fail("PXL008", "%s 的某段超过 255 字节: %s" % (where, text))
        if segment == ".":
            _fail("PXL008", "%s 不得出现 . 段: %s" % (where, text))
        if segment == "..":
            if not stack:
                _fail("PXL008", "%s 规范化时弹出根(字符串层逃逸): %s" % (where, text))
            stack.pop()
            continue
        stack.append(segment)
    if not stack:
        _fail("PXL008", "%s 规范化后为空: %s" % (where, text))
    out = "/".join(stack)
    if len(out) > 4096:
        _fail("PXL008", "%s 超过 4096 字节" % where)
    return out


def _resolve_path(base_document, reference):
    head = base_document.rsplit("/", 1)[0] + "/" if "/" in base_document else ""
    return _normalize_path(head + reference, "asset path")


class _DirResolver(object):
    """从目录读素材:逻辑路径 -> 文件字节。拼写不匹配与缺失分别报 PXL008 / PXL009。"""

    def __init__(self, base_dir):
        self.base = Path(base_dir)

    def read(self, logical):
        target = self.base.joinpath(*logical.split("/"))
        if target.is_file():
            return target.read_bytes()
        parent = target.parent
        if parent.is_dir():
            for item in sorted(parent.iterdir()):
                if item.name.lower() == target.name.lower() and item.is_file():
                    _fail("PXL008", "依赖的实际拼写与逻辑路径不符: %s" % logical)
        _fail("PXL009", "依赖不存在: %s" % logical)


_TYPE_NAMES = ("color", "integer", "boolean", "asset", "sprite")


def _params_parse(document):
    params = document.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        _fail("PXL001", "params 必须是对象")
    if len(params) > 256:
        _fail("PXL002", "params 声明数 %d 超过上限 256" % len(params))
    out = {}
    for name, decl in params.items():
        if not _is_name(name):
            _fail("PXL001", "参数名不匹配 [a-z][a-z0-9_]{0,63}: %r" % name)
        if not isinstance(decl, dict):
            _fail("PXL001", "参数 %r 的声明必须是对象" % name)
        for member in decl:
            if member not in ("type", "default"):
                _fail("PXL001", "参数 %r 出现未定义字段: %r" % (name, member))
        if "type" not in decl:
            _fail("PXL001", "参数 %r 缺少 type" % name)
        if decl["type"] not in _TYPE_NAMES:
            _fail("PXL001", "参数 %r 的 type 取值非法: %r" % (name, decl["type"]))
        out[name] = decl
    return out


def _is_name(text):
    if not isinstance(text, str) or not text or len(text) > 64:
        return False
    if not ("a" <= text[0] <= "z"):
        return False
    return all(("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_" for ch in text[1:])


def _params_bind(declarations, bindings):
    for name in bindings:
        if name not in declarations:
            _fail("PXL012", "绑定了未声明的参数: %r" % name)
    values = {}
    for name in sorted(declarations):
        decl = declarations[name]
        if name not in bindings:
            if "default" not in decl:
                _fail("PXL012", "缺少必需参数: %r" % name)
            values[name] = decl["default"]
            continue
        raw_value = bindings[name]
        # bindings.json 里的值可能是 JSON 数字/布尔;统一成规范字面量再按类型解析。
        if isinstance(raw_value, bool):
            raw_value = "true" if raw_value else "false"
        elif isinstance(raw_value, int):
            raw_value = str(raw_value)
        values[name] = _literal(name, decl["type"], raw_value)
    return values


def _literal(name, type_name, text):
    import re
    if type_name == "color":
        if not re.match(r"^[0-9A-F]{8}$", text):
            _fail("PXL012", "参数 %r 的颜色必须是八位大写 RGBA 十六进制: %s" % (name, text))
        return text
    if type_name == "integer":
        if not re.match(r"^-?(0|[1-9][0-9]{0,15})$", text):
            _fail("PXL012", "参数 %r 的整数形式非法: %s" % (name, text))
        return int(text)
    if type_name == "boolean":
        if text not in ("true", "false"):
            _fail("PXL012", "参数 %r 的布尔值只能是 true/false: %s" % (name, text))
        return text == "true"
    if not _is_name(text):
        _fail("PXL012", "参数 %r 的 %s 值不是合法名字: %s" % (name, type_name, text))
    return text


def _is_reference(value):
    return isinstance(value, str) and len(value) > 1 and value[0] == "$" and _is_name(value[1:])


def _require(values, reference, type_name, where):
    name = reference[1:]
    if name not in values:
        _fail("PXL012", "引用了未声明的参数: %r" % name)
    bound = values[name]
    if type_name == "boolean":
        ok = isinstance(bound, bool)
    elif type_name == "integer":
        ok = isinstance(bound, int) and not isinstance(bound, bool)
    else:
        ok = isinstance(bound, str)
    if not ok:
        _fail("PXL012", "参数 %r 的类型与该位置不符(需要 %s): %s" % (name, type_name, where))
    return bound


_OP_INTEGERS = {
    "fill": ["slot"], "rect": ["x", "y", "w", "h", "slot"],
    "pixel": ["x", "y", "slot"], "line": ["x0", "y0", "x1", "y1", "slot"],
    "grid": ["x", "y"], "erase": ["x", "y", "w", "h"], "stamp": ["x", "y"],
}


def _substitute(document, values):
    out = {}
    for name, value in document.items():
        if name == "params":
            continue
        if name == "palette" and isinstance(value, list):
            out[name] = [_require(values, item, "color", "palette") if _is_reference(item) else item
                         for item in value]
            continue
        if name == "layers" and isinstance(value, list):
            layers = []
            for layer in value:
                layer_out = {}
                for member, member_value in layer.items():
                    if member == "ops" and isinstance(member_value, list):
                        layer_out["ops"] = [_substitute_op(op, values) for op in member_value]
                    else:
                        _reject_reference(member_value, member)
                        layer_out[member] = member_value
                layers.append(layer_out)
            out[name] = layers
            continue
        _reject_reference(value, name)
        out[name] = value
    return out


def _substitute_op(op, values):
    if not isinstance(op, dict):
        return op
    kind = op.get("op")
    integers = _OP_INTEGERS.get(kind, [])
    out = {}
    for name, value in op.items():
        if name in integers and _is_reference(value):
            out[name] = _require(values, value, "integer", "%s.%s" % (kind, name))
            continue
        if kind == "stamp" and name in ("flipX", "flipY") and _is_reference(value):
            out[name] = _require(values, value, "boolean", "%s.%s" % (kind, name))
            continue
        if kind == "stamp" and name in ("asset", "sprite") and _is_reference(value):
            bound = values.get(value[1:])
            if not isinstance(bound, str):
                _fail("PXL012", "stamp 的 %s 绑定的参数类型不符: %s" % (name, value))
            out[name] = bound
            continue
        if kind == "mirror" and name in ("from", "to") and isinstance(value, dict):
            point = {}
            for member, member_value in value.items():
                if member in ("x", "y", "w", "h") and _is_reference(member_value):
                    point[member] = _require(values, member_value, "integer",
                                            "mirror.%s.%s" % (name, member))
                else:
                    _reject_reference(member_value, member)
                    point[member] = member_value
            out[name] = point
            continue
        _reject_reference(value, name)
        out[name] = value
    return out


def _reject_reference(value, where):
    if _is_reference(value):
        _fail("PXL012", "参引出现在白名单之外的字段: %r" % where)


def _slot_color(palette, slot):
    if slot == 0:
        return (0, 0, 0, 0)
    if slot < 1 or slot > len(palette):
        _fail("PXL005", "slot = %d 越过 palette 长度 %d" % (slot, len(palette)))
    return base._tuple(palette[slot - 1])


def _render_pxl(raw, resolver, logical, stack, bindings):
    if logical in stack:
        _fail("PXL010", "循环引用: %s" % " -> ".join(stack + [logical]))
    if len(stack) >= 64:
        _fail("PXL011", "PXL 引用深度超过 64: %s" % " -> ".join(stack + [logical]))
    version, document = dispatch(raw)
    if version != 1:
        _fail("PXL007", "不支持的文档版本 %d: %s" % (version, logical))
    values = _params_bind(_params_parse(document), bindings)
    concrete = _substitute(document, values)
    assets = concrete.pop("assets", {}) or {}
    width = concrete["width"]
    height = concrete["height"]
    palette = concrete.get("palette") or []
    accumulator = bytearray(4 * width * height)
    for layer in concrete["layers"]:
        buffer = bytearray(4 * width * height)
        for op in layer["ops"]:
            _apply_v1_op(buffer, width, height, op, palette, assets, resolver, logical,
                         stack + [logical])
        base._blend_layer(accumulator, buffer, layer.get("blend", "normal"))
    return width, height, bytes(accumulator)


def _apply_v1_op(buffer, width, height, op, palette, assets, resolver, logical, stack):
    kind = op["op"]
    if kind == "stamp" and "asset" in op:
        decl = assets.get(op["asset"])
        if decl is None:
            _fail("PXL005", "stamp 引用了未声明的 asset: %r" % op["asset"])
        if decl.get("kind") != "pxl":
            raise NotImplementedError(
                "PNG asset 的解码在 importer 侧的 decoder 上;本 oracle 目前只判 PXL asset")
        child_path = _resolve_path(logical, decl["path"])
        child_raw = resolver.read(child_path)
        child_bindings = dict((k, str(v)) for k, v in (decl.get("params") or {}).items())
        cw, ch, crgba = _render_pxl(child_raw, resolver, child_path, stack, child_bindings)
        _draw_bitmap(buffer, width, height, op, cw, ch, crgba)
        return
    if kind == "fill":
        slot = op["slot"]
        if not isinstance(slot, int) or isinstance(slot, bool):
            _fail("PXL002", "fill.slot 必须是整数值")
        pixel = bytes(_slot_color(palette, slot))
        for offset in range(0, len(buffer), 4):
            buffer[offset:offset + 4] = pixel
        return
    raise NotImplementedError(
        "v1 oracle 目前只实现 asset stamp 与 fill;遇到 op=%r 请用 compiler 侧判定" % (kind,))


def _draw_bitmap(buffer, width, height, op, cw, ch, rgba):
    for row in range(ch):
        destination_row = (ch - 1 - row) if op.get("flipY") else row
        y = op["y"] + destination_row
        if y < 0 or y >= height:
            continue
        for column in range(cw):
            destination_column = (cw - 1 - column) if op.get("flipX") else column
            x = op["x"] + destination_column
            if x < 0 or x >= width:
                continue
            source = (row * cw + column) * 4
            offset = (y * width + x) * 4
            buffer[offset:offset + 4] = rgba[source:source + 4]


def render_entry(base_dir, entry="entry.pxl", bindings=None):
    """按用例目录渲染一个入口(目录即 asset root,规范 §13.3.1)。"""
    resolver = _DirResolver(base_dir)
    _CURRENT["resolver"] = resolver
    _CURRENT["base"] = entry
    _CURRENT["bindings"] = bindings or {}
    try:
        raw = resolver.read(entry)
        return _render_pxl(raw, resolver, entry, [], bindings or {})
    finally:
        _CURRENT["resolver"] = None
        _CURRENT["base"] = None
        _CURRENT["bindings"] = {}


#: 版本 -> 渲染入口。**加一个版本就在这里加一行**,不要写 if/elif 链:
#: 分派表是 §2 那张表的可执行形式,一眼能看出“支持哪些版本”。
_RENDERERS = {
    1: _render_version_1,
}


def render_text(data):
    """把 PXL 文本(原始字节)渲染成 (width, height, RGBA8 bytes)。

    边界:version 在通用域内但本 oracle 不支持 -> PXL007(§2 / §11.1)。
    """
    if not isinstance(data, bytes):
        _fail("PXL001", "入口必须是原始字节")
    version, document = dispatch(data)
    renderer = _RENDERERS.get(version)
    if renderer is None:
        _fail("PXL007", "version %d 在通用分派域内,但本 oracle 不支持该版本" % (version,))
    return renderer(data, document)


def render_document(document):
    """渲染一个已解析的**规范文档**(dict)。

    v1 的 resolver / 依赖图需要“已通过阶段 D 的文档对象”而不是原始字节,故留此入口。
    当前实现:把它重新序列化成 JSON 再走 'render_text' —— 这只供内部调试,不是 runner 用的
    接口:重新序列化会丢掉 base §3.8 接受集里的非规范写法(CRLF、成员乱序、2.0 之类)。
    """
    return render_text(json.dumps(document).encode("utf-8"))


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------

def _document(version_line, extra=""):
    """构造一份最小文档(规范形式)。version 行由调用方给,便于构造各种非法版本。"""
    return (
        '{\n'
        '  "format": "PXL",\n'
        '%s'
        '  "encoding": "char",\n'
        '  "width": 4,\n'
        '  "height": 4,\n'
        '  "palette": [\n'
        '    "FF0000FF"\n'
        '  ],\n'
        '%s'
        '  "layers": [\n'
        '    {\n'
        '      "name": "base",\n'
        '      "blend": "normal",\n'
        '      "ops": []\n'
        '    }\n'
        '  ]\n'
        '}\n'
    ) % (version_line, extra)


def _expect_code(label, expected_code, callable_):
    """跑一次并比对错误码;返回 (标签, 是否通过, 实际值)。"""
    try:
        actual = callable_()
    except base.PxlRefError as exc:
        return label, exc.code == expected_code, exc.code
    return label, False, "未失败(实际返回 %r)" % (actual,)


def selftest():
    """验证版本分派骨架;返回进程退出码。"""
    cases = []

    # 1. version 0 is a valid version-domain integer but unsupported.
    cases.append(_expect_code(
        "version 0 -> PXL007", "PXL007",
        lambda: render_text(_document('  "version": %d,\n' % 0).encode("utf-8"))))

    # 2. version 1 在骨架阶段必须是 NotImplementedError,且消息里带阶段范围
    try:
        render_text(_document('  "version": 1,\n').encode("utf-8"))
        cases.append(("version 1 抛 NotImplementedError", False, "未抛出"))
    except NotImplementedError as exc:
        text = str(exc)
        # 阶段 2/3/4 已实现：version 1 走本文件的 v1 语义，缺 resolver 时显式报错（不返回近似结果）。
        ok = "resolver" in text
        cases.append(("version 1 缺 resolver 时显式 NotImplementedError", ok,
                      text.splitlines()[0]))

    # 3. 通用域内但不支持的版本 -> PXL007
    cases.append(_expect_code(
        "version 2 -> PXL007", "PXL007",
        lambda: render_text(_document('  "version": 2,\n').encode("utf-8"))))

    # 4. 缺失 version -> PXL001
    cases.append(_expect_code(
        "version 缺失 -> PXL001", "PXL001",
        lambda: render_text(_document("").encode("utf-8"))))

    # 5. 越出通用域 -> PXL001(含“整数但超 64 位”的写法)
    for literal in ("65536", "9223372036854775808", "-1", "1.5", '"1"', "null"):
        cases.append(_expect_code(
            "version %s -> PXL001" % (literal,), "PXL001",
            (lambda literal=literal: render_text(
                _document('  "version": %s,\n' % (literal,)).encode("utf-8")))))

    # 6. 等值小数字面量是整数(base §3.8 的接受集)-> 仍按该版本分派
    cases.append(_expect_code(
        "version 2.0 -> PXL007(等值小数是整数)", "PXL007",
        lambda: render_text(_document('  "version": 2.0,\n').encode("utf-8"))))

    # 7. 同对象内重复成员 -> PXL001(阶段 D 的通用检查,复用 v0 的 _load_json)
    duplicated = _document('  "version": 1,\n', '  "version": 1,\n').encode("utf-8")
    cases.append(_expect_code("重复成员 version -> PXL001", "PXL001",
                              lambda: render_text(duplicated)))

    # 8. 非法 JSON / 顶层不是对象 -> PXL001
    cases.append(_expect_code("非法 JSON -> PXL001", "PXL001",
                              lambda: render_text(b"{")))
    cases.append(_expect_code("顶层是数组 -> PXL001", "PXL001",
                              lambda: render_text(b"[]")))

    failures = 0
    for label, ok, actual in cases:
        if ok:
            print("[ OK ] %s" % label)
        else:
            failures += 1
            print("[FAIL] %s\n       实际: %r" % (label, actual))
    if failures:
        print("selftest 失败 %d 条" % failures)
        return 1
    print("selftest 全部通过(%d 条)" % len(cases))
    return 0


def main(argv):
    args = argv[1:]
    if args == ["--selftest"]:
        return selftest()
    if len(args) == 1 and not args[0].startswith("-"):
        path = Path(args[0])
        try:
            width, height, rgba = render_text(path.read_bytes())
        except NotImplementedError as exc:
            sys.stderr.write("%s: NotImplementedError: %s\n" % (path.as_posix(), exc))
            return 3
        except base.PxlRefError as exc:
            sys.stderr.write("%s: %s: %s\n" % (path.as_posix(), exc.code, exc.message))
            return 3
        print("%s: %dx%d  sha256=%s"
              % (path.as_posix(), width, height, base.pixels_sha256(width, height, rgba)))
        return 0
    sys.stderr.write("用法:\n  %s --selftest\n  %s <entry.pxl>\n" % (argv[0], argv[0]))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
