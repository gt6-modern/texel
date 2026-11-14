"""命令行入口(PRD §17.1)。

统一概念:

    texel capabilities --json
    texel compile ENTRY --asset-root ROOT -o OUT [--bind name=value]... [--deps FILE]
    texel check ENTRY --asset-root ROOT [--bind name=value]... [--deps FILE]
    texel [-o OUT] PATH...            v0 批量渲染(与 Java CLI 的旧入口同形)

退出码:0 成功、1 批量模式下至少一个文件失败、2 用法错、3 PXL 语义错、4 宿主 I/O 失败。
失败绝不留下半份输出:先完整编译到内存,再写同目录独占临时文件并原子改名。
"""

import os
import sys
from pathlib import Path

from . import api, capabilities, pngenc, pxlcompile
from .errors import PxlError
from .resolver import FileSystemResolver

EXIT_OK = 0
EXIT_BATCH_FAILED = 1
EXIT_USAGE = 2
EXIT_PXL = 3
EXIT_IO = 4


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0] in ("compile", "check"):
            return _run_target(args)
        if args and args[0] == "capabilities":
            if args[1:] == ["--json"]:
                _write_stdout(capabilities.json_text())
                return EXIT_OK
            return _usage("capabilities 只接受 --json")
        return _run_legacy(args)
    except BrokenPipeError:
        return EXIT_IO


def _write_stdout(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _safe_resolve(path):
    """解析路径；遇到符号链接环返回 None，调用方据此给单行 I/O 失败。

    Python 3.13 起 `Path.resolve()` 对环**不再抛异常**（发布态复核：3.14 下旧式 CLI 因此 exit 0，
    还把环里的一个输出链接替换成了普通文件）。所以先用 os.stat 让内核判定：
    stat 会跟随链接，环上必然抛 ELOOP，与解释器版本无关。
    """
    try:
        os.stat(str(path))
    except FileNotFoundError:
        pass  # 产物还不存在是正常情况
    except OSError:
        # ELOOP（环）等：stat 会跟随链接，环上内核必然报错，与解释器版本无关。
        # 不用 errno 常量：本仓库的独立性测试只允许白名单模块。
        return None
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _usage(message, code="CLI004"):
    """用法类错误。默认 CLI004；输出冲突类由调用方指定 CLI002（与单 target 路径一致）。"""
    sys.stderr.write("texel: " + code + ": " + message + "\n")
    return EXIT_USAGE


def _bind_value(text):
    """CLI 的 --bind 一律给字符串，**不做**任何预转换。

    N06（发布态复核 P2）：这里原先把 true/false 提前还原成布尔，可是 CLI 此刻并不知道
    参数的声明类型——对一个 color/asset/sprite 参数来说 "true" 是完全合法的符号名，
    被提前转成 bool 就会报 PXL012（类型不符）。布尔值的还原交给按声明类型解析的那一层
    （paramlib 的 canonical_bindings），Java 侧也是这么分的。
    """
    return text


def _run_target(args):
    mode = args[0]
    check = mode == "check"
    entry = None
    root = None
    output = None
    deps = None
    bindings = {}
    index = 1
    while index < len(args):
        argument = args[index]
        if argument == "--asset-root":
            if index + 1 >= len(args):
                return _usage("--asset-root 缺少参数")
            root = args[index + 1]
            index += 2
            continue
        if argument == "--bind":
            if index + 1 >= len(args):
                return _usage("--bind 缺少参数")
            pair = args[index + 1]
            if "=" not in pair or pair.index("=") == 0:
                return _usage("--bind 需要 name=value,收到 " + pair)
            name, _, value = pair.partition("=")
            bindings[name] = _bind_value(value)
            index += 2
            continue
        if argument in ("-o", "--out"):
            # 恰好一个显式输出：重复给出必须拒绝，不能静默采用后者（PRD 合同）。
            if output is not None:
                return _usage("只能给出一个输出参数（-o / --out）")
            if index + 1 >= len(args):
                return _usage(argument + " 缺少参数")
            output = args[index + 1]
            index += 2
            continue
        if argument == "--deps":
            if index + 1 >= len(args):
                return _usage("--deps 缺少参数")
            deps = args[index + 1]
            index += 2
            continue
        if argument.startswith("-"):
            return _usage("未知选项 " + argument)
        if entry is not None:
            return _usage("一次调用只接受一个入口(PRD §17.1)")
        entry = argument
        index += 1
    if entry is None:
        return _usage(mode + " 需要入口的逻辑路径")
    if root is None:
        return _usage(mode + " 需要 --asset-root")
    if check and output is not None:
        return _usage("check 不接受输出路径")
    if not check and output is None:
        return _usage("compile 需要一个显式 -o 输出路径")
    # asset root 无效是**宿主 I/O 错误**，不是「依赖缺失」：否则无效 root 与
    # 「有效 root 下入口不存在」会给出同一个码（复核第二节；后者必须保持 PXL009）。
    root_path = Path(root)
    try:
        if not root_path.is_dir():
            sys.stderr.write("texel: CLI001: I/O 失败: asset root 不是目录: " + str(root) + "\n")
            return EXIT_IO
        resolver = FileSystemResolver(root_path)
        compiled = pxlcompile.compile_target(entry, resolver, bindings)
        png = pngenc.encode(compiled.width, compiled.height, compiled.rgba)
    except PxlError as failure:
        sys.stderr.write("texel: " + failure.describe() + "\n")
        return EXIT_PXL
    except OSError as failure:
        sys.stderr.write("texel: CLI001: I/O 失败: " + str(failure) + "\n")
        return EXIT_IO
    # 输出与清单指向同一路径时，先写出的清单会被 PNG 覆盖，命令还报成功（发布态复核 P2）。
    if not check and output is not None and deps is not None:
        if _safe_resolve(Path(output)) is not None and _safe_resolve(Path(output)) == _safe_resolve(Path(deps)):
            sys.stderr.write("texel: CLI002: 输出与清单指向同一路径: " + output + chr(10))
            return EXIT_USAGE

    # PRD §13.2 CLI002:输出与清单都不得落到**入口或任何依赖**上。判定用的是本次编译的依赖
    # 清单,所以入口与全部被读到的素材一起被挡住;比较按真实路径。判在写任何文件之前。
    if deps is not None and deps != "-":
        clash = pxlcompile.conflicting_source(deps, root, compiled.dependencies)
        if clash is not None:
            sys.stderr.write("texel: CLI002: --deps 路径与源文件相同: " + deps + "(" + clash + ")\n")
            return EXIT_USAGE
    if not check:
        clash = pxlcompile.conflicting_source(output, root, compiled.dependencies)
        if clash is not None:
            sys.stderr.write("texel: CLI002: 输出路径与源文件相同: " + output + "(" + clash + ")\n")
            return EXIT_USAGE
    # 输出路径上出现符号链接环时一律拒绝：环的解析依赖宿主，允许跟随会让同一
    # 输入在不同平台写到不同位置（复核第二节 + §5.4）。
    for target in ((deps if deps not in (None, "-") else None),
                   (output if not check else None)):
        if target is not None and _symlink_cycle(target):
            sys.stderr.write("texel: CLI002: 输出路径包含符号链接环，拒绝写出: " + target + "\n")
            return EXIT_USAGE
    try:
        if deps == "-":
            # PRD 要求「依赖清单可写 stdout」;旗标未规定,这里取约定的 '-'，
            # 并且**不能**把 '-' 当成文件名写出去。
            sys.stdout.write(compiled.manifest_text())
            sys.stdout.flush()
        elif deps is not None:
            _write_bytes(Path(deps), compiled.manifest_text().encode("utf-8"))
        if not check:
            _write_bytes(Path(output), png)
    except OSError as failure:
        sys.stderr.write("texel: CLI003: I/O 失败: " + str(failure) + "\n")
        return EXIT_IO
    return EXIT_OK


#: POSIX 的 ELOOP。不引 errno：核心只允许 pathlib 等白名单模块。
_ELOOP = 40

def _symlink_cycle(target):
    """目标路径上是否存在符号链接环（§5.4 确定性：环一律拒绝）。

    resolve(strict=False) 会**吞掉** ELOOP 并原样返回，所以必须用 strict=True；
    父目录尚不存在时抛的是 ENOENT，那不是环，继续往上找。
    """
    # 从目标本身开始：叶子自己是链接环时只看父目录会漏（发布态复核 F5）。
    probe = Path(target).absolute()
    while True:
        try:
            probe.resolve(strict=True)
            return False
        except OSError as failure:
            # Windows 3.8/3.9/3.14 把循环链接报告为 errno=22、winerror=1921；
            # 不能只识别 POSIX ELOOP，否则会继续到父目录并允许替换链接。
            if failure.errno == _ELOOP or getattr(failure, "winerror", None) == 1921:
                return True
            if probe == probe.parent:
                return False
            probe = probe.parent
        except RuntimeError:
            # Windows raises RuntimeError for a symlink loop instead of ELOOP.
            return True

def _write_bytes(target, payload):
    """同目录独占临时文件 + 原子替换;失败删临时文件,不破坏既有输出。"""
    parent = target.absolute().parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / (".texel-" + str(os.getpid()) + ".tmp")
    handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY
                     if os.name == "nt" else os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    except BaseException:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise


def _run_legacy(args):
    inputs = []
    output = None
    literal = False
    index = 0
    while index < len(args):
        argument = args[index]
        if not literal and argument == "--":
            literal = True
            index += 1
            continue
        if not literal and argument in ("--help", "-h"):
            _write_stdout(usage_text())
            return EXIT_OK
        if not literal and argument in ("-o", "--out"):
            if index + 1 >= len(args):
                return _usage("选项 " + argument + " 缺少参数")
            if output is not None:
                return _usage("只允许一个输出路径")
            output = args[index + 1]
            index += 2
            continue
        inputs.append(argument)
        index += 1
    if not inputs:
        return _usage("缺少输入路径")
    sources = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            found = sorted(path.rglob("*.pxl"), key=_sort_key)
            if not found:
                return _usage("目录里没有 .pxl 文件: " + item)
            sources.extend(found)
        elif path.exists():
            sources.append(path)
        else:
            return _usage("输入不存在: " + item)
    sources = sorted(set(sources), key=_sort_key)
    if output is not None and len(sources) > 1:
        return _usage("-o 只允许在恰好一个输入时使用")
    # 旧式入口同样要挡别名：-o entry.pxl 会把源文件静默写成 PNG（新式 compile/check 已有闸门，
    # 但两条入口是各自解析参数的，只保护新式分支等于没保护）。
    planned = []
    for source in sources:
        target = Path(output) if output is not None else source.with_suffix(".png")
        planned.append((source, target))
    sources_resolved = set()
    for path in sources:
        resolved = _safe_resolve(path)
        if resolved is not None:
            sources_resolved.add(resolved)
    targets_seen = {}
    for source, target in planned:
        key = _safe_resolve(target)
        if key is None:
            # 符号链接环等：resolve() 会抛 OSError/RuntimeError。此前直接漏出 traceback
            # （发布态复核 M04），这里按 I/O 失败处理并保持单行输出。
            sys.stderr.write("texel: I/O 失败: 无法解析输出路径: " + str(target) + chr(10))
            return EXIT_IO
        if key in sources_resolved:
            # 输出冲突是 CLI002，不是 CLI004：单 target 路径（compile/check）对同一类错误
            # 用的就是 CLI002，Java 侧的批量入口也已统一。裸 CLI004 会让下游按码分流时
            # 把"会踩坏源文件"当成普通用法错（2026-11-14 审查 P2）。
            return _usage("输出路径与输入相同: " + str(target), code="CLI002")
        if key in targets_seen:
            return _usage("两个输入映射到同一个输出: " + str(target), code="CLI002")
        targets_seen[key] = source

    failures = 0
    for source, target in planned:
        try:
            data = source.read_bytes()
            rendered = api.render_document(data)
            png = pngenc.encode(rendered.width, rendered.height, rendered.rgba)
            _write_bytes(target, png)
        except PxlError as failure:
            failures += 1
            sys.stderr.write("texel: " + str(source) + ": " + failure.describe() + "\n")
        except OSError as failure:
            failures += 1
            sys.stderr.write("texel: " + str(source) + ": I/O 失败: " + str(failure) + "\n")
    return EXIT_OK if failures == 0 else EXIT_BATCH_FAILED


def _sort_key(path):
    # §9:处理顺序必须由显式排序确定,不得依赖目录枚举顺序。按路径的 UTF-8 字节序。
    return str(path).replace("\\", "/").encode("utf-8")


def usage_text():
    return ("usage: texel capabilities --json\n"
            "       texel compile ENTRY --asset-root ROOT -o OUT\n"
            "             [--bind name=value]... [--deps FILE]\n"
            "       texel check ENTRY --asset-root ROOT\n"
            "             [--bind name=value]... [--deps FILE]\n"
            "       texel [-o OUT] PATH...\n"
            "\n"
            "PATH 是 .pxl 文件或目录(目录递归收集 *.pxl)。\n"
            "省略 -o 时输出写到同目录同名 .png。\n")


if __name__ == "__main__":
    sys.exit(main())
