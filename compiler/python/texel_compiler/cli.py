"""命令行入口(PRD §17.1)。

统一概念(帮助文本见 `usage_text()`):

    texel-compiler-python capabilities --json
    texel-compiler-python compile ENTRY --asset-root ROOT -o OUT [--bind name=value]... [--deps FILE]
    texel-compiler-python check ENTRY --asset-root ROOT [--bind name=value]... [--deps FILE]
    texel-compiler-python [-o OUT] PATH...   v0 批量渲染(与 Java CLI 的旧入口同形)

退出码:0 成功、1 批量模式下至少一个文件失败、2 用法错、3 PXL 语义错、4 宿主 I/O 失败。
失败绝不留下半份输出:先完整编译到内存,再写同目录独占临时文件并原子改名。
"""

import os
import sys
from pathlib import Path

from . import capabilities, pngenc, pxlcompile
from .errors import PxlError
from .resolver import FileSystemResolver

EXIT_OK = 0
EXIT_BATCH_FAILED = 1
EXIT_USAGE = 2
EXIT_PXL = 3
EXIT_IO = 4

#: 每条 stderr 都以**真实命令名**开头。三个 CLI(``texel-compiler-python``、
#: ``texel-compiler``、``texel-import``)统一这一条:日志混流时能分清这行是谁说的。
PROGRAM = "texel-compiler-python"


def main(argv=None):
    """CLI 入口：连宿主级失败也要变成受控的单行诊断与退出码。

    ``pyproject.toml`` 的控制台脚本直接指向**这里**，不经过 ``__main__.py``：把兜底留在
    ``__main__`` 里等于发布命令照样把 ``MemoryError`` 的 traceback 甩给使用者，退出码还
    由包装器决定（2026-11-20 审查 P2）。``python -m texel_compiler`` 走的是同一个函数。
    """
    try:
        return _main(argv)
    except MemoryError:
        # §10.2.1：驻留内存不足是宿主资源失败，不是 PXL 码。
        _note("CLI001: I/O 失败: 宿主内存不足（工作集超出可用内存）")
        return EXIT_IO
    except BrokenPipeError:
        return EXIT_IO
    except (OSError, RuntimeError) as failure:
        # 宿主 I/O 与链接环（Windows 上 resolve 循环链接会抛 RuntimeError）必须变成
        # 受控错误：泄漏 traceback 是确证问题（发布态复核第二节）。
        _note("宿主 I/O 失败: " + str(failure))
        return EXIT_USAGE


def _main(argv=None):
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


#: ``splitlines()`` 会当成换行的码点（都是**合法文件名**的一部分）：Java 侧
#: ``Main.escape`` 转义的正是这一组（再加全部控制字符）。不转义的话，一条诊断会被
#: 拆成 4 行，而后续行没有命令名前缀——日志混流时无从归属。
_SPLITLINES = (0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)


def _escape(text):
    r"""把一段文字渲染成**单行**：控制字符与"会被当成换行"的码点写成 ``\uXXXX``。

    与 Java 侧的 ``Main.escape`` 用同一种转义形式，所以两边对同一个文件名吐出同一行。
    转义的是整条消息而不只是路径：``PxlError.describe`` 里也会带上文档里的逻辑路径，
    那里同样可能含 U+2028。
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
    """打一行诊断：``texel-compiler-python: <message>``，整行已转义。"""
    sys.stderr.write(PROGRAM + ": " + _escape(message) + "\n")


def _same_output(left, right):
    """两个产物路径是否其实是**同一份内容**：路径相同、符号链接归一后相同，或硬链接。

    ``resolve()`` 只能认出前两种。两个路径指向同一个 inode 时它看不出来，于是
    ``-o out.png --deps deps.tsv``（deps.tsv 是 out.png 的硬链接）会被当成两个产物：
    清单写进同一份文件后又被 PNG 覆盖，命令还报成功。Java 侧同一处判据用的是
    ``Files.isSameFile``，两边必须一致。
    """
    resolved_left = _safe_resolve(left)
    resolved_right = _safe_resolve(right)
    if resolved_left is not None and resolved_left == resolved_right:
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(str(left), str(right))
    except OSError:
        return False


def _identity(path):
    """路径的**文件身份**：存在的文件用 (设备号, inode)，否则用解析后的路径。

    硬链接是另一个目录项指向同一个 inode：``resolve()`` 只能归一同名路径与符号链接，
    对硬链接完全无感——``-o entry.png entry.pxl``（entry.png 是 entry.pxl 的硬链接）
    会把入口源文件静默改写成 PNG。Java 侧用 ``BasicFileAttributes.fileKey()`` 问同一件事，
    两边对同一组参数给出同一个结论。解析失败（符号链接环）返回 None，由调用方给 I/O 失败。
    """
    resolved = _safe_resolve(path)
    if resolved is None:
        return None
    try:
        info = os.stat(str(path))
    except OSError:
        # 还不存在的产物：路径身份就是"解析后的绝对路径"。
        return ("path", str(resolved))
    return ("file", info.st_dev, info.st_ino)


def _usage(message, code="CLI004"):
    """用法类错误。默认 CLI004；输出冲突类由调用方指定 CLI002（与单 target 路径一致）。"""
    _note(code + ": " + message)
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
            _note("CLI001: I/O 失败: asset root 不是目录: " + str(root))
            return EXIT_IO
        resolver = FileSystemResolver(root_path)
        compiled = pxlcompile.compile_target(entry, resolver, bindings)
        png = pngenc.encode(compiled.width, compiled.height, compiled.rgba)
    except PxlError as failure:
        _note(failure.describe())
        return EXIT_PXL
    except OSError as failure:
        _note("CLI001: I/O 失败: " + str(failure))
        return EXIT_IO
    # 输出与清单指向同一路径时，先写出的清单会被 PNG 覆盖，命令还报成功（发布态复核 P2）。
    if not check and output is not None and deps is not None and deps != "-":
        # `--deps -` 是 stdout 哨兵，不是文件路径：把它当路径比较只会得出"输出=清单"
        # 的假冲突（`-o - --deps -` 就会撞上），而 `-o` 的 `-` 是普通文件名。
        if _same_output(Path(output), Path(deps)):
            _note("CLI002: 输出与清单指向同一路径: " + output)
            return EXIT_USAGE

    # PRD §13.2 CLI002:输出与清单都不得落到**入口或任何依赖**上。判定用的是本次编译的依赖
    # 清单,所以入口与全部被读到的素材一起被挡住;比较按真实路径。判在写任何文件之前。
    if deps is not None and deps != "-":
        clash = pxlcompile.conflicting_source(deps, root, compiled.dependencies)
        if clash is not None:
            _note("CLI002: --deps 路径与源文件相同: " + deps + "(" + clash + ")")
            return EXIT_USAGE
    if not check:
        clash = pxlcompile.conflicting_source(output, root, compiled.dependencies)
        if clash is not None:
            _note("CLI002: 输出路径与源文件相同: " + output + "(" + clash + ")")
            return EXIT_USAGE
    # 输出路径上出现符号链接环时一律拒绝：环的解析依赖宿主，允许跟随会让同一
    # 输入在不同平台写到不同位置（复核第二节 + §5.4）。
    for target in ((deps if deps not in (None, "-") else None),
                   (output if not check else None)):
        if target is not None and _symlink_cycle(target):
            _note("CLI002: 输出路径包含符号链接环，拒绝写出: " + target)
            return EXIT_USAGE
    # 目标已经存在却**不是普通文件**（目录、设备 …）必须先挡住：走到改名那一步才发现的话，
    # 两份产物里先改名的那份已经替换成功，命令失败却留下了新产物（2026-11-20 审查 P2）。
    for label, candidate in (("输出路径", None if check else output),
                             ("清单路径", None if deps in (None, "-") else deps)):
        if candidate is None:
            continue
        existing = Path(candidate)
        if existing.is_dir():
            return _usage(label + "是一个目录: " + candidate)
        if existing.exists() and not existing.is_file():
            return _usage(label + "不是普通文件: " + candidate)

    staged = []
    try:
        # 要写的产物**全部**先落到各自目录的独占临时文件里，两份都写成功了才改名。
        # 只保证"顺序"是不够的：先写 PNG 再写清单那版，清单写失败会留下「新 PNG +
        # 旧清单」，下游会把一份过期清单当成这次构建的结果。
        if not check:
            staged.append((_stage_bytes(Path(output), png, "png"), Path(output)))
        if deps is not None and deps != "-":
            staged.append((_stage_bytes(Path(deps), compiled.manifest_text().encode("utf-8"),
                                       "deps"), Path(deps)))
        _publish(staged)
        staged = []
        if deps == "-":
            # PRD 要求「依赖清单可写 stdout」;旗标未规定,这里取约定的 '-'，并且**不能**
            # 把 '-' 当成文件名写出去。放到改名之后才打：否则 PNG 写失败时清单已经在
            # stdout 上了，而产物一个都没更新（2026-11-20 审查 P2）。
            sys.stdout.write(compiled.manifest_text())
            sys.stdout.flush()
    except OSError as failure:
        for temporary, _target in staged:
            _discard(temporary)
        _note("CLI003: I/O 失败: " + str(failure))
        return EXIT_IO
    except BaseException:
        # MemoryError 之类：main 会把它兜成受控的 CLI001，但暂存文件必须在这里清掉，
        # 否则一条失败的编译会在目录里留下 .texel-*.tmp（2026-11-20 审查 P2）。
        for temporary, _target in staged:
            _discard(temporary)
        raise
    return EXIT_OK


#: POSIX 的 ELOOP。不引 errno：核心只允许 pathlib 等白名单模块。
_ELOOP = 40

#: 临时文件名的进程内序号。见 `_stage_bytes`：并发调用必须拿到不同的名字，而唯一性由
#: `O_EXCL` 仲裁，这里只提供一个单调递增的候选。用列表而不是模块级 int，是为了让
#: `+=` 写法在 3.8 上也合法（`global` 与闭包两种写法都能过，列表最少歧义）。
_TEMP_SEQUENCE = [0]

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

def _stage_bytes(target, payload, tag="out"):
    """把 payload 写进 target 同目录的独占临时文件并刷盘；返回临时路径，**不改名**。

    单独一层的理由：一次命令可能有两份产物（PNG + 依赖清单），必须两份都写成功了才改名，
    否则一份都不动（见 `_run_target` 里 staged 的注释）。

    ``tag`` 进临时文件名：同目录下的两份产物（``-o out.png --deps deps.tsv``）会撞同一个
    临时名，独占创建直接 EEXIST。

    名字里还必须带一个**每次调用都不同**的序号：只按 PID 起名时，同一个进程里的两个线程
    编译到同一目录会抢同一个 ``.texel-<pid>-out.tmp``，其中一个报 ``File exists`` ——
    两个输出本来毫无冲突，失败却来自宿主 I/O（审查 #13）。序号不要求原子：真正的仲裁是
    ``O_EXCL``，序号撞了就换下一个再试。
    """
    parent = target.absolute().parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    while True:
        _TEMP_SEQUENCE[0] += 1
        temporary = parent / (".texel-" + str(os.getpid()) + "-" + tag + "-"
                              + str(_TEMP_SEQUENCE[0]) + ".tmp")
        try:
            handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY
                             if os.name == "nt" else os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # 同进程并发撞号，或上一个进程崩在同一 PID 上留下了同名残留。
            continue
        break
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _discard(temporary)
        raise
    return temporary


def _discard(temporary):
    """删掉暂存文件/备份的**目录项**；已经不在了或删不掉都不掩盖原始错误。

    不能先用 ``Path.exists()`` 判断：它跟随符号链接，指向已不存在目标的 ``.prev``
    备份 ``exists()`` 是 False，于是这种备份永远清不掉，会在输出目录里越积越多
    （CodeRabbit 复审）。直接 unlink，容忍"已经不在了"。
    """
    try:
        os.unlink(str(temporary))
    except OSError:
        pass


#: 备份的三种形态。见 ``_backup_target``。
_BACKUP_LINK = "link"
_BACKUP_COPY = "copy"
_BACKUP_SYMLINK = "symlink"

#: 流式复制的块大小：旧产物可能很大，不能整份读进内存。
_COPY_CHUNK = 1 << 20


def _backup_candidates(base):
    """备份名的候选序列：先 ``base``，再 ``base.1``、``base.2``……

    **绝不复用已存在的名字**：上次回滚失败时留下的 ``.prev`` 可能是旧产物的唯一副本，
    而创建备份失败后去"清理"一个自己没建出来的文件就会把它删掉。所以撞名只换下一个
    候选，不删除、不覆盖（CodeRabbit 复审）。
    """
    yield base
    index = 1
    while True:
        yield base.with_name(base.name + "." + str(index))
        index += 1


def _backup_target(target, base):
    """给既有产物建一份**保身份**的备份，返回 ``(kind, payload)``。

    内容备份只能恢复字节，恢复不了目录项：目标原本是符号链接或硬链接时，回滚会把链接
    换成普通文件，链接关系就永久没了（2026-11-20 审查 P2）。所以分三种形态：

    * **符号链接**：记下 ``os.readlink`` 的原始文本，回滚时按原文本重建链接；
    * **普通文件**：建一个**硬链接**做备份——同一个 inode，回滚就是把它换回来，硬链接数、
      其它名字指向的内容都不变；顺带也不用把旧产物整份读进内存；
    * **硬链接不可用但目标没有别的名字**（FAT/exFAT、权限）：退化成流式复制，内容一样、
      身份丢了——但 ``st_nlink == 1`` 说明没有别处还指着这个 inode，丢的只是 inode 号；
    * **硬链接不可用且目标还有别的名字**（``st_nlink > 1``）：**让这次发布失败**。复制出来的
      备份回滚后是新 inode，那些名字之间的链接关系会永久丢掉，而失败的命令不该改变文件系统。

    每个候选名都独占创建（``EXCL`` 语义），撞名就换下一个候选（见 ``_backup_candidates``）。
    """
    if target.is_symlink():
        # 备份必须是**磁盘上的对象**。只把 readlink 的文本记在内存里是不够的：回滚要先
        # 删掉目标再重建链接，重建一旦失败（权限、链接数上限）目标路径就没了，而那份
        # "备份"只活在进程里，谁也拿不回来（2026-11-20 审查 P1）。所以备份就是另一个
        # 指向同一目标的符号链接，回滚是一次原子改名。
        for backup in _backup_candidates(base):
            try:
                os.symlink(os.readlink(str(target)), str(backup),
                           target_is_directory=target.is_dir())
            except FileExistsError:
                continue          # 名字被占：换下一个，**不删别人的备份**
            except (OSError, NotImplementedError) as failure:
                # 没建出任何东西，无需清理（清理反而可能删掉别人的文件）。
                raise OSError("无法为符号链接产物建立备份（建立链接失败: " + str(failure)
                              + "）: " + str(target))
            return (_BACKUP_SYMLINK, backup)
        raise AssertionError("备份候选名用尽")  # pragma: no cover - 序列无穷
    link_failure = None
    for backup in _backup_candidates(base):
        try:
            os.link(str(target), str(backup))
            return (_BACKUP_LINK, backup)
        except FileExistsError:
            continue              # 同上：撞名不覆盖
        except (OSError, NotImplementedError) as failure:
            link_failure = failure
            break
    # 把"文件系统不支持硬链接"和"权限/配额/链接数上限"分开：后者发生时目标很可能还与
    # 别的名字共享 inode，复制备份等于承诺一份恢复不了的路径。nlink 是唯一能证明"没别的
    # 名字"的依据，拿不到就按"有别的名字"处理。
    links = 0
    try:
        links = os.lstat(str(target)).st_nlink
    except (OSError, AttributeError):
        links = 0
    if links != 1:
        raise OSError("无法为有硬链接的产物建立保身份备份（nlink=" + str(links)
                      + "，与其它路径共享 inode）: " + str(target)
                      + "（建硬链接失败: " + str(link_failure) + "）")
    for backup in _backup_candidates(base):
        try:
            _copy_stream(target, backup)      # O_EXCL：撞名抛 FileExistsError
        except FileExistsError:
            continue
        return (_BACKUP_COPY, backup)
    raise AssertionError("备份候选名用尽")  # pragma: no cover - 序列无穷


def _copy_stream(source, destination):
    """把 source 的内容**流式**写进 destination（独占创建），失败删掉半份文件。

    不用 ``read_bytes()``：此刻内存里已经有新 PNG、清单和编译产物，再要一份和旧产物
    等大的连续内存没有意义（2026-11-20 审查 P2）。

    两个细节都是回滚的正确性要求（CodeRabbit 复审）：

    * **先开源再建备份**：源打不开（权限、句柄额度）时不该先创建一个待清理的备份，
      更不该把已经拿到的写描述符漏在进程里；
    * **复制保留旧产物的权限**：备份是"回滚后要顶替旧产物"的东西，权限降成 0600 就等于
      回滚后别人读不到那张 PNG。保留不了权限就让发布在替换旧产物**之前**失败。
    """
    with open(str(source), "rb") as reader:
        mode = None
        try:
            mode = os.stat(str(source)).st_mode & 0o777
        except OSError:
            mode = None
        handle = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY
                         if os.name == "nt" else os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handed_over = False
        try:
            if mode is not None:
                os.chmod(str(destination), mode)
            writer = os.fdopen(handle, "wb")
            handed_over = True
            with writer:
                while True:
                    chunk = reader.read(_COPY_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        except BaseException:
            if not handed_over:
                # fdopen 还没接管这个描述符，必须自己关掉。
                try:
                    os.close(handle)
                except OSError:
                    pass
            _discard(destination)
            raise


def _restore_target(kind, payload, target):
    """把目标的**目录项**恢复原状：三种形态的备份都是磁盘对象，一次改名就够。

    不需要"先删目标再重建"：那条路在重建失败时会留下一个空路径，而符号链接的备份
    如果只存在内存里就连退路都没有（2026-11-20 审查 P1）。
    """
    os.replace(str(payload), str(target))


def _drop_backup(kind, payload):
    """丢掉没用上的备份：三种形态都是磁盘对象，删掉即可。"""
    _discard(payload)


def _publish(staged):
    """把 staging 好的产物改名到各自目标；多份产物时要么全成、要么一份都不变。

    **单份产物就是一次 ``os.replace``**：目标路径在任何时刻都存在，进程被打断也只会
    看到"旧内容"或"新内容"——那是老的原子替换语义，不能退化成"先把旧产物移走、再改名"
    （中间那一刻目标不存在，被终止就只剩一份隐藏备份）。

    多份产物时先给既有目标建一份**不移走目标**的备份（见 ``_backup_target``），逐份原子
    替换；任一份失败就用备份**把目录项恢复原状**。读者任何时刻看到的都是旧内容或新内容，
    不会看到一个不存在的路径；被强行终止时最坏留下"新 PNG + 旧清单"和一份备份，不会只剩
    隐藏备份。

    失败清理对**所有**异常生效（不只是 OSError）：``MemoryError`` 这类由 ``__main__`` 兜成
    受控 CLI001 的失败，同样不能把 ``.texel-*.tmp`` 留在目录里。
    """
    if len(staged) == 1:
        os.replace(str(staged[0][0]), str(staged[0][1]))
        return
    backups = {}
    committed = []
    try:
        # PNG 先改名、清单后改名：万一回滚本身也失败，「新 PNG + 旧清单」是保守的一侧
        # （看到旧清单下游会重建），反过来「旧 PNG + 新清单」等于让构建系统相信一份
        # 过期产物。
        for temporary, target in staged:
            if target.is_symlink() or target.exists():
                backups[target] = _backup_target(target, temporary.with_suffix(".prev"))
            os.replace(str(temporary), str(target))
            committed.append(target)
    except BaseException:
        kept = []
        #: 本次新产物没能删掉的路径。删除失败以前被 `_discard` 静默吞掉，于是命令报
        #: "清单提交失败"、磁盘上却留着新 PNG，操作者无从知道（CodeRabbit 复审）。
        stray = []
        try:
            for target in reversed(committed):
                record = backups.pop(target, None)
                try:
                    if record is None:
                        # 这个目标原来不存在：回滚就是删掉它。只容忍"已经不在了"，
                        # 其它错误（权限、占用）必须记账并继续处理剩下的目标。
                        try:
                            os.unlink(str(target))
                        except FileNotFoundError:
                            pass
                        except BaseException:
                            stray.append(target)
                    else:
                        _restore_target(record[0], record[1], target)
                except BaseException:
                    # 退不回去就**留着**备份：那份旧内容可能只在这里，删掉等于把退路也毁了。
                    # 它必须脱离下面那个"没用上的备份"清理集合——双重故障（提交失败 + 恢复失败）
                    # 曾经把旧产物连备份一起删掉，那是不可恢复的丢失。备份现在是磁盘对象，
                    # 所以这里给出的路径确实能被人捡回来。
                    # 恢复本身也可能因资源耗尽抛出 MemoryError：同样只记账、**继续**处理剩下
                    # 的目标，不能在这里中断（2026-11-20 审查 P2）。
                    if record is not None:
                        kept.append(record[1])
        finally:
            # 清理与报告放在 finally：上面的循环中途抛出任何东西，未使用的备份也必须清掉、
            # 保留下来的路径也必须说出来。
            for kind, payload in backups.values():
                try:
                    _drop_backup(kind, payload)
                except BaseException:
                    pass
            if kept:
                try:
                    _note("回滚失败，旧产物备份保留在: "
                          + ", ".join(str(item) for item in kept))
                except BaseException:
                    pass
            if stray:
                try:
                    _note("回滚未能删除本次新产物（它仍留在磁盘上）: "
                          + ", ".join(str(item) for item in stray))
                except BaseException:
                    pass
        raise
    for kind, payload in backups.values():
        _drop_backup(kind, payload)


def _write_bytes(target, payload):
    """同目录独占临时文件 + 原子替换;失败删临时文件,不破坏既有输出。"""
    temporary = _stage_bytes(target, payload)
    try:
        os.replace(str(temporary), str(target))
    except BaseException:
        _discard(temporary)
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
            # 输入**根**本身是链接目录时也要挡：递归里的剪枝只看子项，根是 junction 时
            # 整棵树都会走到链接目标上去，"目录模式处理哪些文件"取决于本机链接
            # （2026-11-20 审查 P2）。要求使用者给出真实路径。
            if _is_link_like(path):
                _note(item + ": 输入目录是符号链接/junction，批量入口不跟随（请给出真实路径）")
                return EXIT_BATCH_FAILED
            found = []
            skipped = []
            failures = []
            _collect_pxl(path, found, skipped, failures)
            # 跳过的候选必须说出来：目录模式"少产出一个 PNG"否则无从发现。
            for note in skipped:
                _note("跳过 " + note)
            if failures:
                _directory, failure = failures[0]
                _note(item + ": 目录读取失败: " + str(failure))
                return EXIT_BATCH_FAILED
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
    # -o 的值必须先按用途判一次再用：空串会被 Path("") 当成当前目录，指向一个
    # **已存在的目录**则会一路走到「临时文件 + 改名」，把内部临时文件名和改名目标
    # 一起印给使用者，退出码还是 1（"某个文件处理失败"）。这两条在 Java 侧的批量
    # 入口（Main.java 的 output 准入）都是用法错 exit 2。
    if output is not None:
        if not output:
            return _usage("输出路径不能为空")
        if Path(output).is_dir():
            return _usage("输出路径是一个目录: " + output)
    # 旧式入口同样要挡别名：-o entry.pxl 会把源文件静默写成 PNG（新式 compile/check 已有闸门，
    # 但两条入口是各自解析参数的，只保护新式分支等于没保护）。
    planned = []
    for source in sources:
        target = Path(output) if output is not None else source.with_suffix(".png")
        planned.append((source, target))
    sources_resolved = set()
    source_ids = set()
    for path in sources:
        resolved = _safe_resolve(path)
        if resolved is not None:
            sources_resolved.add(resolved)
        ident = _identity(path)
        if ident is not None:
            source_ids.add(ident)
    targets_seen = set()
    target_ids = set()
    for source, target in planned:
        key = _safe_resolve(target)
        if key is None:
            # 符号链接环等：resolve() 会抛 OSError/RuntimeError。此前直接漏出 traceback
            # （发布态复核 M04），这里按 I/O 失败处理并保持单行输出。
            _note("I/O 失败: 无法解析输出路径: " + str(target))
            return EXIT_IO
        ident = _identity(target)
        if key in sources_resolved:
            # 输出冲突是 CLI002，不是 CLI004：单 target 路径（compile/check）对同一类错误
            # 用的就是 CLI002，Java 侧的批量入口也已统一。裸 CLI004 会让下游按码分流时
            # 把"会踩坏源文件"当成普通用法错（2026-11-14 审查 P2）。
            return _usage("输出路径与输入相同: " + str(target), code="CLI002")
        if ident is not None and ident in source_ids:
            # 路径不同、inode 相同：写入会把入口（或某个素材）截断成 PNG。
            return _usage("输出与输入是同一个文件（硬链接）: " + str(target), code="CLI002")
        if key in targets_seen or (ident is not None and ident in target_ids):
            return _usage("两个输入映射到同一个输出: " + str(target), code="CLI002")
        targets_seen.add(key)
        if ident is not None:
            target_ids.add(ident)

    # 批次预扫（相位 A：只解析与记账，不产 PNG）：把整批入口的依赖清单先收齐。
    # 某个入口的输出可能落在**另一个**入口读过的素材上（例如 `tile.pxl` 的默认输出
    # `tile.png` 正是 `entry.pxl` stamp 的那张 PNG 素材），而依赖清单只有解析完才知道；
    # 逐个入口"编译完再判自己"已经太晚——前面几份产物可能已经落盘。
    broken = {}
    manifests = []
    for source, target in planned:
        parent = source.absolute().parent
        try:
            manifest = pxlcompile.dependency_manifest(source.name, FileSystemResolver(parent))
        except PxlError as failure:
            broken[source] = str(source) + ": " + failure.describe()
            continue
        manifests.append((source, target, parent, manifest))

    # 每个输出都要对**所有**入口的依赖清单比一遍，然后整批拒绝——与"输出撞输入"同一类
    # 冲突（CLI002，写任何文件之前中止），而不是只跳过撞上的那一个。
    conflicts = []
    for source, target, _parent, _manifest in manifests:
        for other_source, _other_target, other_parent, other_manifest in manifests:
            clash = pxlcompile.conflicting_source(target, other_parent, other_manifest)
            if clash is not None:
                conflicts.append(str(source) + ": CLI002: 输出 " + str(target)
                                 + " 会覆盖 " + str(other_source) + " 的编译依赖 "
                                 + clash + "（未写入）")
                break
    if conflicts:
        for message in conflicts:
            _note(message)
        _note("检测到 %d 处输出覆盖编译依赖，整批未写入任何文件" % len(conflicts))
        return EXIT_USAGE

    failures = 0
    for source, target in planned:
        if source in broken:
            failures += 1
            _note(broken[source])
            continue
        try:
            # 旧式批量入口同样走**完整的 v1 编译管线**，asset root = 源文件所在目录。
            # 只在内存里渲染入口字节（api.render_document）拿不到 asset root，于是任何
            # stamp 了外部 asset 的文档都报 PXL009「依赖缺失」，而 Java 侧同一个文件能
            # 编译成功——两条入口自称"同形"却给出不同结果。文档字节上限不受影响：
            # pxlcompile 载入每份文档时自己判 64 MiB。
            parent = source.absolute().parent
            compiled = pxlcompile.compile_target(source.name, FileSystemResolver(parent), {})
            png = pngenc.encode(compiled.width, compiled.height, compiled.rgba)
            _write_bytes(target, png)
        except PxlError as failure:
            failures += 1
            _note(str(source) + ": " + failure.describe())
        except OSError as failure:
            failures += 1
            _note(str(source) + ": I/O 失败: " + str(failure))
    return EXIT_OK if failures == 0 else EXIT_BATCH_FAILED


#: Windows 的 FILE_ATTRIBUTE_REPARSE_POINT。junction 在 Windows 上**不算**符号链接
#: （os.path.islink 返回 False），但同样是重解析点，跟进去就走出了给定目录。
_REPARSE_POINT = 0x400


def _is_link_like(path):
    """是否是符号链接，或 Windows 的 junction / 挂载点。

    只看 `Path.is_symlink()` 是不够的：`os.walk(followlinks=False)` 靠的正是 islink，
    于是 junction 一路被跟随，同一棵树导出的文件集合取决于这台机器上谁建过链接
    （实测 Windows + Python 3.12：junction 下的文件被当成树内文件）。lstat 的文件属性
    位在 Windows 上可用，且不需要额外模块。
    """
    if path.is_symlink():
        return True
    try:
        info = os.lstat(str(path))
    except OSError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _collect_pxl(root, into, skipped, failures=None):
    """递归收集 ``*.pxl``；跳过的候选写进 ``skipped``，读不动的目录写进 ``failures``。

    语义与 Java 侧 ``Main.collect`` 逐条对齐（2026-11-14 审查：两边曾经一个跳过符号链接、
    另一个靠 ``rglob`` 跟着走，同一棵树两个 CLI 会产出不同的 PNG 集合）：

    * **不跟随符号链接与 junction**：`os.walk(followlinks=False)` 只认 `islink`，Windows 的
      junction 不算 islink，原来那版会走进去，把给定目录**之外**的文件也收进来。这里改成
      自己展开目录，链接/junction 一律剪枝（也顺手挡掉 junction 自引用造成的死循环）；
    * 只认字面 ``.pxl``；``X.PXL`` 这类大小写不符的候选报出来而不是静默忽略；
    * 结果按路径的 UTF-8 字节序排序（§9：不得依赖文件系统遍历顺序）。
    """
    found = []
    pending = [Path(root)]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=_sort_key)
        except OSError as failure:
            # 不可读目录必须报出来：静默跳过会让"目录模式少产出一批 PNG"无从发现，
            # 而且命令在其余文件成功时还会返回 0，产出一个不完整的集合
            # （2026-11-20 审查 P2）。Java 侧 collect() 同样把这里当成 I/O 失败。
            if failures is not None:
                failures.append((current, failure))
            continue
        for path in entries:
            name = path.name
            linked = _is_link_like(path)
            # **先认目录**：名字叫 `group.pxl` 的普通目录也是目录，必须继续往下走。
            # 先按扩展名分支会把它当成"不是普通文件的候选"报成跳过，里面的
            # `group.pxl/entry.pxl` 就再也不会被编译，而整批还可能返回 0
            # （CodeRabbit 复审）。
            if not linked and path.is_dir():
                pending.append(path)
            elif name.endswith(".pxl"):
                if not linked and path.is_file():
                    found.append(path)
                else:
                    reason = ("（不是普通文件：符号链接不被跟随）" if linked
                              else "（不是普通文件）")
                    skipped.append(str(path) + reason)
            elif name.lower().endswith(".pxl"):
                skipped.append(str(path) + "（扩展名大小写不符，只认 .pxl）")
    into.extend(sorted(found, key=_sort_key))


def _sort_key(path):
    # §9:处理顺序必须由显式排序确定,不得依赖目录枚举顺序。
    # 用 `os.fsencode` 而不是 `str.encode("utf-8")`:POSIX 文件名允许非 UTF-8 字节,
    # 解释器把这类名字解码成 surrogate 字符交给批量入口,直接按 UTF-8 编码会抛
    # `UnicodeEncodeError` 并以 traceback 结束(审查 #25)。`fsencode` 走的是解释器解码
    # 这些名字时用的文件系统编码,对原始字节名同样可逆、可排序。
    #
    # 只归一**本平台的分隔符**:在 POSIX 上反斜杠是合法文件名字符,`a/b.pxl` 与
    # `a\b.pxl` 是两个文件,全局替换会让它们拿到同一个排序键(CodeRabbit 复审)。
    text = str(path)
    if os.sep != "/":
        text = text.replace(os.sep, "/")
    return os.fsencode(text)


def usage_text():
    """帮助文本。

    三个 CLI(Java 的 ``texel-compiler``、本模块的 ``texel-compiler-python``、
    importer 的 ``texel-import``)共用一套版式,程序名用**真实命令名**而不是
    ``python -m ...``:``usage:`` 首行 + 对齐的多行 synopsis、缩进选项表(说明列
    统一从第 22 列起)、最后一段中文说明。改这里时另外两份要一起改,``tests/test_cli_identity.py``
    会钉住版式。
    """
    return ("usage: texel-compiler-python capabilities --json\n"
            "       texel-compiler-python compile ENTRY --asset-root ROOT -o OUT\n"
            "       texel-compiler-python check ENTRY --asset-root ROOT\n"
            "       texel-compiler-python [-o OUT] PATH...\n"
            "\n"
            "  ENTRY              入口逻辑路径(相对 --asset-root),compile/check 用\n"
            "  --asset-root ROOT  资产根目录,compile/check 用\n"
            "  --bind name=value  给入口参数绑一个值,可重复\n"
            "  --deps FILE        写出依赖清单,FILE 为 - 时写到标准输出\n"
            "  -o, --out OUT      输出路径(批量模式只在恰好一个输入时允许)\n"
            "  PATH               输入 .pxl 文件或目录(目录递归收集 *.pxl)\n"
            "  --                 其后的参数一律当路径(文件名以 - 开头时用)\n"
            "  --json             只与 capabilities 合用:打印能力档案\n"
            "\n"
            "省略 -o 时,批量模式把每个输入写到同目录、同名的 .png。\n"
            "compile 必须显式给 -o;check 不接受输出路径。\n")


if __name__ == "__main__":
    sys.exit(main())
