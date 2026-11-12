"""token 编码(规范 §4.1–§4.3、§4.6)。

一个文件只用一种 token 宽度,解析按固定宽度逐段位置切分,不存在"这个 token 有多宽"
的判定空间。slot 0 恒为透明,三档分别记作 ``.`` / ``00`` / ``0000``。
"""

from .errors import PXL003, PXL005, PxlError

#: ``char`` 档 slot 1..61 的字符表(§4.2):``0-9`` + ``A-Z`` + ``a-y``。
#: ``.`` 留给透明 slot,``z`` 保留未用。
CHAR_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy"

#: 每档的透明 token(§4.1)。
TRANSPARENT_TOKEN = {"char": ".", "index": "00", "index4": "0000"}

#: 每档的非透明 slot 上限 = 该 token 宽度可表示的值域总数 − 1 个透明值(§4.1)。
TIER_LIMIT = {"char": 61, "index": 255, "index4": 65535}


def encode_token(encoding, slot):
    """把 slot 编成该档的 token 字符串。"""
    if encoding == "char":
        if slot == 0:
            return TRANSPARENT_TOKEN["char"]
        if slot < 0 or slot > TIER_LIMIT["char"]:
            raise PxlError(PXL005, "char 档 slot %d 越界(上限 61)" % slot)
        return CHAR_ALPHABET[slot - 1]
    if encoding == "index":
        if slot == 0:
            return TRANSPARENT_TOKEN["index"]
        if slot < 0 or slot > TIER_LIMIT["index"]:
            raise PxlError(PXL005, "index 档 slot %d 越界(上限 255)" % slot)
        return "%02X" % slot
    if encoding == "index4":
        if slot == 0:
            return TRANSPARENT_TOKEN["index4"]
        if slot < 0 or slot > TIER_LIMIT["index4"]:
            raise PxlError(PXL005, "index4 档 slot %d 越界(上限 65535)" % slot)
        return "%04X" % slot
    raise PxlError(PXL003, "未知 encoding: %r" % (encoding,))
