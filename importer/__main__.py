"""命令行入口:``texel-import [--raw] [-o OUT] PATH...``(``python -m importer`` 等价)。

PATH 可以是文件或目录;目录按 ``*.png`` 递归收集。处理顺序按路径的 UTF-8
字节序显式排序(规范 §9:不得依赖文件系统遍历顺序)。

退出码:0 全成功;1 至少一个文件失败(失败原因打到 stderr,该文件不产出任何
输出);2 用法错误。**每条错误恰好一行**,且都以 ``texel-import: `` 开头(与
``texel-compiler`` / ``texel-compiler-python`` 统一:日志混流时能分清谁说的):
输入不存在、无权限、输出目录不可写
这类 OS 级失败按 1 处理;用法错误(含"选项值非法")按 2 处理——完整用法用
``--help`` 看,不往错误后面接整段 USAGE。批量模式下"每条错误一行"是**逐条**的:
每个出问题的文件、每个冲突各占一行,末尾最多再加一行汇总。CLI 不是调试器,
不该把 traceback 甩给使用者。

输出路径与输入是同一个文件时**拒绝执行**并整批退出 2:读完之后以写模式打开会
把源位图截断成 PXL 文本,原图无法恢复。批量模式下"输出撞上**同批另一个**输入"
(例如 `a.png` 的输出去覆盖同批的 `a.pxl`)同样在**任何写入之前**整批判为用法错误
—— 否则先写的那份已经把后面的输入毁了,而后者的失败会让整批看起来只是"某个文件
读取失败"。

输出路径是**另一张**已存在的 PNG 时不拒绝(那是使用者明确指定的路径,`cp` 同样会
覆盖),但会往 stderr 打一行警告——绝不静默把位图换成文本。成功写出走"同目录
**独占创建**的临时文件 + ``Path.replace``",所以中途失败也不会留下半份 PXL,
也不会覆盖别人的同名 ``.tmp``。
"""

import os
import sys
from pathlib import Path

from .errors import PxlError
from .extract import extract_file

#: PNG 文件签名,用于"这个输出路径其实是一张位图"的判断。
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: 每条 stderr 都以**真实命令名**开头。三个 CLI(``texel-import``、
#: ``texel-compiler``、``texel-compiler-python``)统一这一条:批量跑的时候几个工具的
#: 输出往往接进同一份日志,不带头就分不清这行是谁说的。
PROGRAM = "texel-import"


#: "会被当成换行"的码点（都是**合法文件名**的一部分）：与 ``texel-compiler-python`` 的
#: ``_escape`` 用同一组、同一种转义形式。
_SPLITLINES = (0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)


def _escape(text):
    r"""把一段文字渲染成**单行**：控制字符与"会被当成换行"的码点写成 ``\uXXXX``。

    只转义路径是不够的：``--bad\noption`` 这种参数、以及 OS 异常消息里带的路径，
    都会让"一条诊断一行"断成多行，后续行还没有 ``texel-import: `` 前缀
    （2026-11-20 审查 P2）。
    """
    out = []
    for ch in str(text):
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code in _SPLITLINES:
            out.append("\\u%04X" % code)
        else:
            out.append(ch)
    return "".join(out)


def _note(message):
    """往 stderr 打**一行**:``texel-import: <message>``（整行已转义）。"""
    sys.stderr.write("%s: %s\n" % (PROGRAM, _escape(message)))

USAGE = (
    # 与 texel-compiler / texel-compiler-python 共用一套版式(2026-11-19 统一):
    # `usage:` 首行 + 缩进选项表(说明列从第 22 列起) + 最后一段中文说明,
    # 程序名用真实命令名 `texel-import` 而不是 `python -m importer`。
    "usage: texel-import [--raw] [-o OUT] PATH...\n"
    "\n"
    "  PATH               输入 .png 文件或目录(目录递归收集 *.png)\n"
    "  --raw              输出逐像素基准形态(整图一条 grid),不压缩\n"
    "  -o, --out OUT      输出路径(只在恰好一个输入时允许)\n"
    "\n"
    "省略 -o 时,输出写到输入文件同目录、同名的 .pxl。\n"
)


def collect_inputs(paths, skipped=None):
    """把文件/目录混排的参数展开成按路径字节序排序的文件列表。

    目录展开的规则与两个 compiler 的批量入口逐条一致（原实现用 ``Path.rglob``）：

    * **不跟随符号链接**（``os.walk(followlinks=False)``，与 Java ``walkFileTree``
      默认一致）。``rglob`` 在 Python 3.12 及以前会跟着 junction/符号链接走出给定
      目录，把树外的 PNG 也导入；3.13 起默认又不跟——同一棵树在不同解释器上导入的
      文件集合不同，那是与 §9 的确定性要求直接冲突。
    * 只认字面 ``.png``；``X.PNG`` 这类大小写不符的候选报进 ``skipped`` 而不是静默
      忽略（Windows 的文件名匹配不区分大小写、POSIX 区分，不写死就与宿主绑定）。
    * 跳过的候选逐条写进 ``skipped``：目录模式"少产出一个 PXL"否则无从发现。
    """
    collected = []
    for raw in paths:
        entry = Path(raw)
        if entry.is_dir():
            _collect_png(entry, collected, skipped)
        else:
            collected.append(entry)
    collected.sort(key=_sort_key)
    return collected


#: Windows 的 FILE_ATTRIBUTE_REPARSE_POINT。junction 在 Windows 上**不算**符号链接
#: （os.path.islink 返回 False），但同样是重解析点，跟进去就走出了给定目录。
_REPARSE_POINT = 0x400


def _is_link_like(path):
    """是否是符号链接，或 Windows 的 junction / 挂载点。

    `Path.is_symlink()` 对 junction 返回 False，而 `os.walk(followlinks=False)` 靠的正是
    islink——只看它，junction 会被一路跟随，树外的 PNG 也会被导入。lstat 的文件属性位
    在 Windows 上可用，且不需要额外模块。
    """
    if path.is_symlink():
        return True
    try:
        info = os.lstat(str(path))
    except OSError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _collect_png(root, into, skipped):
    """递归收集 ``*.png``；跳过的候选逐条写进 ``skipped``（可为 None）。

    过滤规则与 ``texel_compiler.cli._collect_pxl`` 对齐：不跟随符号链接与 junction、
    只认字面扩展名、显式排序。
    """
    found = []
    pending = [Path(root)]
    while pending:
        current = pending.pop()
        # 不可读目录**不吞掉**：静默跳过会让"少产出一批 PXL"无从发现，而且其余文件成功时
        # 命令还会返回 0，产出一个不完整的集合（2026-11-20 审查 P2）。异常交给 main 里的
        # 宿主管兜底，报成单行说明 + 退出 1。
        entries = sorted(current.iterdir(), key=_sort_key)
        for path in entries:
            name = path.name
            linked = _is_link_like(path)
            # **先认目录**：名字叫 `assets.png` 的普通目录也是目录，必须继续往下走。
            # 先按扩展名分支会把它报成"不是普通文件的候选"跳过，里面的 PNG 就再也不会
            # 被导入，而整批还可能返回 0（CodeRabbit 复审）。
            if not linked and path.is_dir():
                pending.append(path)
            elif name.endswith(".png"):
                if not linked and path.is_file():
                    found.append(path)
                elif skipped is not None:
                    reason = ("（不是普通文件：符号链接不被跟随）" if linked
                              else "（不是普通文件）")
                    skipped.append(str(path) + reason)
            elif name.lower().endswith(".png"):
                if skipped is not None:
                    skipped.append(str(path) + "（扩展名大小写不符，只认 .png）")
    into.extend(sorted(found, key=_sort_key))


def _sort_key(path):
    """§9:处理顺序按路径字节序,不得依赖目录枚举顺序。

    用 ``os.fsencode`` 而不是 ``str.encode("utf-8")``:POSIX 文件名允许非 UTF-8 字节,
    Python 把它们解码成 surrogate 字符交给我们,直接 ``encode("utf-8")`` 会抛
    ``UnicodeEncodeError`` —— 目录模式下这会让整批以 traceback 结束,显式传入同一路径时
    更是当场炸掉(审查 #25)。``fsencode`` 用的正是解释器解出这些名字时用的文件系统编码,
    因此对原始字节名同样可逆、可排序。只归一**本平台的分隔符**:POSIX 上反斜杠是合法
    文件名字符,`a/b.png` 与 `a\b.png` 是两个文件,全局替换会让它们拿到同一个排序键
    (CodeRabbit 复审)。
    """
    text = str(path)
    if os.sep != "/":
        text = text.replace(os.sep, "/")
    return os.fsencode(text)


#: 这些码点不是"控制字符",但 ``str.splitlines()`` 会把它们当换行——于是原样打印
#: 之后,"一行说明"在按行解析时又会变成两行(`U+0085` 尤其阴:Windows 允许它进文件名)。
_SPLITLINES_ONLY = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def _show(path):
    """把路径渲染成**单行**:控制字符与"会被当成换行"的码点一律转义。

    Windows 允许文件名里带换行符,直接把 `as_posix()` 塞进 stderr 会让"一行说明"
    在原样打印时断成两行——而测试是按行数断言的。非 ASCII 的可打印字符(中文等)
    保持原样,不做 unicode_escape 那种把中文也转掉的粗暴处理。
    """
    rendered = []
    for ch in path.as_posix():
        if ch < " " or ch == "\x7f" or ch in _SPLITLINES_ONLY:
            code = ord(ch)
            rendered.append("\\x%02x" % code if code < 0x100 else "\\u%04x" % code)
        else:
            rendered.append(ch)
    return "".join(rendered)


def _fail_usage(message):
    """用法错误:只打**一行**说明。

    不在这里补 `USAGE` 全文——文件头对使用者的承诺是"单行说明",而把整段用法
    铺在错误后面既让它读起来像半个帮助文档,也让"错误行数"无法被测试断言。
    需要完整用法请用 `--help`。
    """
    _note(message)


def _same_file(left, right):
    """两个路径是否指向同一个文件(不存在的路径退化为规范化绝对路径比较)。

    这一层允许在"解析失败"时退化成词法比较:它只用于**判断冲突**,不用于放开写入。
    真正阻止写坏符号链接的是 `_identity()`——那里解析失败会让整批在写入前中止。
    """
    try:
        return left.samefile(right)
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return left.resolve() == right.resolve()
    except (OSError, RuntimeError):
        return left == right


def _identity(path):
    """去重/比对用的路径标识;**解析不了就让它抛**。

    不能在这里把 `resolve()` 的失败吞掉降级成"原样路径":输出目标若是符号链接环,
    降级后它看起来就是个正常目标,随后 `Path.replace` 会把那个链接目录项直接换掉
    (退出码 0、无任何提示)。解析失败必须冒到 `main` 的前置兜底里,变成单行说明 +
    退出 1,并且**一个文件都不写**。
    """
    # resolve() 默认 strict=False：**符号链接环不会抛**，而是"尽量解析"后返回——
    # 于是输出目标是环时它看起来像个正常目标，Path.replace 会把那个链接目录项换掉，
    # 退出码 0、无任何提示（Linux 实测）。这里用 strict=True 让 ELOOP 冒到 main 的前置兜底；
    # 只有"目标尚不存在"（FileNotFoundError）才退回非严格解析——那是正常的新建输出。
    try:
        return ("resolved", path.resolve(strict=True))
    except FileNotFoundError:
        return ("resolved", path.resolve())


def _dedupe(inputs):
    """同一个文件被重复给多次时只保留一次(否则两份输出会互相踩)。

    按 ``resolve()`` 的路径去重,所以 `./a.png` 与 `a.png` 只算一份;**普通硬链接
    不会被合并**——它们是不同的路径、各自有不同输出名,分别处理是对的。
    """
    seen = set()
    unique = []
    for source in inputs:
        key = _identity(source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return unique


def _plan_writes(inputs, out):
    """为每个输入算出输出路径;`out` 只允许单输入(调用方已校验)。"""
    if out is not None:
        return [(inputs[0], Path(out))]
    return [(source, source.with_suffix(".pxl")) for source in inputs]


def _write_conflicts(plan):
    """输出与**任一输入**或**另一个输出**撞车的清单,在写入前一次性算清。

    为什么不能只比"输出 vs 自己这个输入":批量模式下 `a.png` 的输出 `a.pxl`
    可能正是**同批另一个待处理的输入**——先写它就把那份源位图换成了文本,随后
    那份输入按 PNG 读失败,而原图已经没了。这类损坏发生在"另一个文件失败"的
    掩护下,最容易不被发现。
    """
    problems = []
    inputs = [source for source, _target in plan]
    owners = {}
    for source, target in plan:
        # 输入本身不存在时让它按"读取失败"报(退出 1 + 明确原因),不要因为
        # 词法比较撞上自己就误报成"输出冲突"(退出 2)。
        if not source.exists():
            continue
        reasons = []
        for other in inputs:
            if _same_file(target, other):
                if other is source:
                    reasons.append("输出路径与输入是同一个文件,拒绝覆盖源位图")
                else:
                    reasons.append("输出会覆盖同批的输入文件 %s" % _show(other))
                break
        key = _identity(target)
        if key in owners:
            reasons.append("与 %s 的输出路径相同" % _show(owners[key]))
        else:
            owners[key] = source
        if reasons:
            problems.append((target, reasons))
    return problems


def _open_exclusive_temp(target):
    """在目标同目录**独占**创建一个临时文件,返回 ``(路径, 已打开的文件对象)``。

    名字里带 pid 与序号(`xb` 独占打开),不能固定成 ``<target>.tmp``:固定名会静默
    覆盖用户已有的同名文件;更坏的情况下那份文件恰好是输入,于是绕过同文件检查、
    把源位图删掉。清理时也只删本次创建的这一个。
    """
    for attempt in range(64):
        candidate = target.with_name(
            "%s.%d.%d.tmp" % (target.name, os.getpid(), attempt))
        try:
            return candidate, candidate.open("xb")
        except FileExistsError:
            continue
    raise OSError("无法在 %s 旁创建临时文件" % _show(target.parent))


def _looks_like_png(path):
    """文件头是不是 PNG 签名(读不到就当不是)。"""
    try:
        with path.open("rb") as stream:
            return stream.read(8) == PNG_SIGNATURE
    except OSError:
        return False


def _write_atomic(target, payload):
    """写完再原子替换:不留下半份 PXL,也不截断已有文件。

    清理分支接 `(OSError, ValueError)`:今天 `ValueError` 只可能从 `with_name()`
    冒出来(在 `_open_exclusive_temp` 里、早于 try),但一旦将来 try 内部多了会抛
    `ValueError` 的调用,只接 `OSError` 就会把临时文件留在目录里。清理本身再失败
    也吞掉——它只影响"干不干净",不该盖住原始错误。
    """
    temp, stream = _open_exclusive_temp(target)
    try:
        with stream:
            stream.write(payload)
        temp.replace(target)
    except (OSError, ValueError):
        try:
            temp.unlink()
        except (OSError, ValueError):
            # 清理失败只影响"干不干净",绝不能盖住原始错误。
            pass
        raise


def _probe_is_dir(path):
    """`path` 是否是一个**已存在的目录**;探测本身失败时按"不是目录"回答。

    参数解析阶段不能碰会抛异常的 I/O:非空但超过文件名上限的 `-o` 会在 `is_dir()` 里抛
    `OSError(ENAMETOOLONG)`,而这里还在 `main()` 的规划期兜底**之前**,于是 CLI 吐
    traceback(审查 #26)。探测失败就说明它不可能是"已存在的目录",让它继续走到写入侧,
    由那里报成一行 `写出失败` + 退出码 1。
    """
    try:
        return path.is_dir()
    except (OSError, ValueError):
        # Windows 对超长路径抛 ValueError（"path too long"），不是 OSError。
        return False


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    raw = False
    out = None
    paths = []
    index = 0
    while index < len(args):
        item = args[index]
        if item in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        if item == "--raw":
            raw = True
        elif item in ("-o", "--out"):
            index += 1
            if index >= len(args):
                _fail_usage("%s 需要一个值 (见 --help)" % item)
                return 2
            out = args[index]
            # 输出必须是**文件**路径。`-o .` / `-o ""` / `-o <已存在目录>` 都属于
            # 用法错误:再往下走会在 `Path.with_name()` 里抛 ValueError,而写入侧
            # 只接 OSError —— 结果就是 CLI 吐 traceback,违反文件头的承诺。
            # 以 `-` 开头的值同样拒掉:那几乎总是"选项当成了值"(`-o -o` 会写出一个
            # 名为 `-o` 的文件),而本 CLI 不支持 `--` 转义。
            out_path = Path(out)
            if not out_path.name or _probe_is_dir(out_path):
                _fail_usage("%s 需要一个文件路径,收到 %r (见 --help)"
                            % (item, out))
                return 2
            if out.startswith("-"):
                _fail_usage("%s 的值不能以 '-' 开头(是选项被当成路径了吗?):%r"
                            " (见 --help)" % (item, out))
                return 2
        elif item.startswith("-"):
            _fail_usage("未知选项 %r (见 --help)" % item)
            return 2
        else:
            paths.append(item)
        index += 1

    if not paths:
        _fail_usage("没有输入 (见 --help)")
        return 2

    # 输入**根**本身是链接目录时也要挡：递归里的剪枝只看子项，根是 junction/符号链接时
    # 整棵树都会走到链接目标上去，"目录模式处理哪些文件"取决于本机链接（2026-11-20
    # 审查 P2）。要求使用者给出真实路径，而不是悄悄跟随。
    for item in paths:
        entry = Path(item)
        # 探测本身失败（ENAMETOOLONG 之类）不是"是链接目录"，而是这次输入不可用：
        # 直接 `entry.is_dir()` 会在 main 的兜底 try 之前抛出去，变成 traceback
        # （CodeRabbit 复审）。探测失败按"不是目录"处理，交给规划阶段报一行诊断。
        if _probe_is_dir(entry) and _is_link_like(entry):
            _note("%s: 输入目录是符号链接/junction，批量入口不跟随（请给出真实路径）"
                  % _show(entry))
            return 1

    # 建计划这一步要碰文件系统(目录枚举、符号链接解析),自引用符号链接之类
    # 会直接抛 OSError。按文件头的承诺:OS 级失败给单行说明,不吐 traceback。
    try:
        skipped = []
        inputs = _dedupe(collect_inputs(paths, skipped))
        # 跳过的候选必须说出来：目录模式"少产出一个 PXL"否则无从发现。
        for note in skipped:
            _note("跳过 " + note)
        if not inputs:
            _note("给出的路径下没有 *.png")
            return 2
        if out is not None and len(inputs) != 1:
            _note("-o 只接受恰好一个输入(实际 %d 个)" % len(inputs))
            return 2
        plan = _plan_writes(inputs, out)
        # 冲突必须在**任何写入之前**判完:否则"先写的那份"已经把后面的输入毁了,
        # 而后面的失败又会让整批看起来只是"某个文件读取失败"。
        conflicts = _write_conflicts(plan)
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        # ValueError = 宿主不接受这个路径（NUL、超长名）：同样是"这条路径用不了"，
        # 不是 traceback 的理由（审查 P2）。
        _note("无法解析输入/输出路径: %s" % error)
        return 1
    if conflicts:
        for target, reasons in conflicts:
            _note("%s: %s" % (_show(target), ", ".join(reasons)))
        _note("检测到 %d 处输出冲突,整批未写入任何文件" % len(conflicts))
        return 2

    failures = 0
    for source, target in plan:
        try:
            text = extract_file(source, raw=raw)
        except PxlError as error:
            _note("%s: %s" % (_show(source), error))
            failures += 1
            continue
        except OSError as error:
            _note("%s: 读取失败: %s" % (_show(source), error))
            failures += 1
            continue
        # 覆盖输入 / 撞上同批输入的判断已在 `_write_conflicts` 里对**整批**做过,
        # 走到这里说明 plan 内部没有冲突,不必再逐个比对。
        # `-o` 指向**另一张**已存在的 PNG 时,那张位图会被换成 PXL 文本。这是使用者
        # 明确指定的路径(`cp` 同样会覆盖),所以不拒绝;但绝不静默——否则原图丢了
        # 都不知道是哪一步干的。
        if target.exists() and _looks_like_png(target):
            _note("%s: 警告:该路径已是一张 PNG,将被改写成 PXL 文本" % _show(target))
        # §3.1:UTF-8 无 BOM、LF 行尾。按字节写,避免平台默认换行翻译。
        try:
            _write_atomic(target, text.encode("utf-8"))
        except (OSError, ValueError) as error:
            # ValueError 也要接:非法文件名(空名、超长名)不是 OSError,
            # 但它同样是"写不出去",不该以 traceback 的形式出现。
            _note("%s: 写出失败: %s" % (_show(target), error))
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
