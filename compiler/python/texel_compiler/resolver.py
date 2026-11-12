"""Resolver 合同与两种实现(v1 §8.2 / §8.3)。

core 只依赖抽象 resolver,不直接搜索文件系统。resolver 对逻辑路径返回四种结果之一:
OK / MISSING / CASE_MISMATCH / ESCAPE。

* MemoryResolver:conformance 用。以逻辑路径为键存字节,**同时保存每个路径段的原始拼写**;
  完全一致才 OK,仅大小写折叠后相等即 CASE_MISMATCH。它让大小写规则在平台无关的语料里
  可判定(§13.3.2),不依赖宿主文件系统是否大小写敏感。
* FileSystemResolver:CLI 用。逐段枚举真实目录并按 ASCII 精确比较拼写,用路径段关系判断
  是否落在 root 内(不做字符串前缀判断),拒绝经 symlink/junction 逃出 root 的目标。
"""

import os
from pathlib import Path

OK = "OK"
MISSING = "MISSING"
CASE_MISMATCH = "CASE_MISMATCH"
ESCAPE = "ESCAPE"


class MemoryResolver(object):
    """以逻辑路径为键的内存 resolver。"""

    def __init__(self, files, spelling=None):
        #: logical_path -> bytes
        self.files = dict(files)
        #: logical_path -> 磁盘上的实际拼写(§13.3.2 的 spelling.tsv)
        self.spelling = dict(spelling or {})
        folded = {}
        for key in sorted(self.files):
            folded.setdefault(key.lower(), []).append(key)
        self._folded = folded

    def fetch(self, logical_path):
        actual = self.spelling.get(logical_path, logical_path)
        if actual in self.files:
            if actual == logical_path:
                return OK, self.files[actual]
            return CASE_MISMATCH, None
        if self._folded.get(logical_path.lower()):
            return CASE_MISMATCH, None
        return MISSING, None


def _escapes(path, root):
    """该路径的真实落点是否已经离开 asset root。"""
    try:
        resolved = path.resolve()
        base = Path(root).resolve()
    except (FileNotFoundError, NotADirectoryError, RuntimeError):
        return False
    # 不能按字符串前缀判：root-sibling 会以 root 的字符串开头（发布态复核 F4）。
    try:
        resolved.relative_to(base)
        return False
    except ValueError:
        return True

def _file_type(path, follow_symlinks=True):
    """Read type bits without predicates that can suppress host I/O failures."""
    try:
        info = path.stat() if follow_symlinks else path.lstat()
    except (FileNotFoundError, NotADirectoryError, RuntimeError):
        return 0
    # POSIX type bits are also used by Python on Windows; no extra module needed.
    return info.st_mode & 0o170000


class FileSystemResolver(object):
    """标准文件系统 resolver(平台测试用,不进语言语料)。

    权限、锁和其它宿主 I/O 故障按 §8.4 原样抛 OSError，由 CLI 分类。
    """

    def __init__(self, root):
        self.root = Path(root)

    def fetch(self, logical_path):
        segments = [part for part in logical_path.split("/") if part]
        if not segments:
            return ESCAPE, None
        current = self.root
        for segment in segments:
            if _file_type(current) != 0o040000:
                return MISSING, None
            try:
                names = sorted(entry.name for entry in current.iterdir())
            except (FileNotFoundError, NotADirectoryError, RuntimeError):
                return MISSING, None
            if segment not in names:
                folded = segment.lower()
                for name in names:
                    if name.lower() == folded:
                        return CASE_MISMATCH, None
                return MISSING, None
            current = current / segment
            # 越界必须在枚举下一段之前判掉（A2）：否则外部目录项的存在性会泄露成 PXL009。
            if _escapes(current, self.root):
                return ESCAPE, None
        try:
            real_root = self.root.resolve(strict=True)
            real_target = current.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, RuntimeError):
            # 悬垂链接：目标不存在。若它**指向 root 之外**，判 ESCAPE —— 否则错误码取决于
            # 「root 外那个文件恰好存不存在」，而且会与 Java 分叉（发布态复核 F-R7-03）。
            try:
                if _file_type(current, follow_symlinks=False) == 0o120000:
                    # Path.readlink() 是 3.9+；3.8 上会抛 AttributeError（发布态复核 P2）。
                    link = Path(os.readlink(str(current)))
                    resolved = link if link.is_absolute() else (current.parent / link)
                    root_now = self.root.resolve(strict=False)
                    if not _is_inside(resolved.resolve(strict=False), root_now):
                        return ESCAPE, None
            except (FileNotFoundError, NotADirectoryError, RuntimeError):
                pass
            return MISSING, None
        if not _is_inside(real_target, real_root):
            return ESCAPE, None
        if _file_type(real_target) != 0o100000:
            return MISSING, None
        try:
            return OK, real_target.read_bytes()
        except (FileNotFoundError, NotADirectoryError, RuntimeError):
            return MISSING, None

#: Windows 扩展路径前缀：两个反斜杠 + 问号 + 反斜杠。
EXTENDED_PREFIX = chr(92) * 2 + chr(63) + chr(92)

def _plain(path):
    """把路径规范化成可比较的字符串。

    Windows 上 resolve() 会给出带扩展前缀的形式（两个反斜杠 + 问号 + 反斜杠），
    普通路径则不带；两者直接 relative_to 会抛 ValueError，于是 root 内的路径被误判
    成 ESCAPE（发布态复核 P2：Windows 3.12/3.14 复现）。这里去掉 4 字符前缀并做
    大小写归一。
    """
    text = os.path.abspath(str(path))
    if text.startswith(EXTENDED_PREFIX):
        text = text[4:]
    return os.path.normcase(text)

def _is_inside(target, root):
    """按路径段关系判断,而不是字符串前缀(§8.4)。"""
    target_text = _plain(target)
    root_text = _plain(root)
    if target_text == root_text:
        return True
    # 文件系统根（/ 或 E:\）本身已带分隔符，不能再拼一个，否则 startswith 恒为假，
    # 合法文件被误判成越界（发布态复核 P2）。
    prefix = root_text if root_text.endswith(os.sep) else root_text + os.sep
    return target_text.startswith(prefix)


def build_memory_resolver(root_directory, spelling=None):
    """把磁盘上的一个目录树读成内存 resolver(conformance 的 asset root 即用例目录)。"""
    root = Path(root_directory)
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        files[relative] = path.read_bytes()
    return MemoryResolver(files, spelling)