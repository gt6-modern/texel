"""逻辑路径(v1 §7)。

PXL 文档里的 path 是可移植逻辑路径,不是宿主文件系统字符串。这里的规则逐条对应 §7:
只允许 a-z / 0-9 / - / _ / . / /;禁绝对路径、盘符、UNC、URL、空段、尾随斜杠;. 段非法;
.. 段允许但只在规范化里消费;**弹出空栈即字符串层逃逸**;每段 <= 255 字节、结果 <= 4096 字节。
"""

from .errors import PxlError

ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_./")
MAX_SEGMENT_BYTES = 255
MAX_PATH_BYTES = 4096


def _fail(message):
    raise PxlError("PXL008", message)


def directory_of(logical_path):
    """逻辑路径所在目录(逻辑路径形式);根目录返回空串。"""
    index = logical_path.rfind("/")
    return "" if index < 0 else logical_path[:index]


def normalize(raw, base_directory=""):
    """校验并规范化,返回唯一名称。base_directory 是声明该引用的文档所在逻辑目录。"""
    if not isinstance(raw, str):
        _fail("逻辑路径必须是字符串")
    if not raw:
        _fail("逻辑路径为空")
    for char in raw:
        if char not in ALLOWED:
            _fail("逻辑路径含非法字符 " + repr(char) + ": " + raw)
    if raw.startswith("/"):
        _fail("逻辑路径不得是绝对路径: " + raw)
    segments = raw.split("/")
    for segment in segments:
        if not segment:
            _fail("逻辑路径含空段或尾随斜杠: " + raw)
        if segment == ".":
            _fail("逻辑路径含 . 段: " + raw)
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            _fail("逻辑路径的单个段超过 255 字节: " + raw)
    stack = [part for part in base_directory.split("/") if part]
    for segment in segments:
        if segment == "..":
            if not stack:
                _fail("逻辑路径规范化后逃出 asset root: " + raw)
            stack.pop()
        else:
            stack.append(segment)
    if not stack:
        _fail("逻辑路径规范化后为空: " + raw)
    result = "/".join(stack)
    if len(result.encode("utf-8")) > MAX_PATH_BYTES:
        _fail("规范化后的逻辑路径超过 4096 字节")
    return result