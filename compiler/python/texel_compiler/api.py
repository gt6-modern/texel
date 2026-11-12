"""对外 API:渲染一份 PXL 文档 / 编译一个 target / 编码 PNG。

lib 层的形状可以按语言习惯不同(PRD §6.1),但语义与错误码必须与 Java 实现一致。
"""

from . import jsonx, numlit, pngenc, pxlcompile
from .errors import PxlError
from .jsonx import JsonNumber
from .resolver import MemoryResolver

encode_png = pngenc.encode
compile_target = pxlcompile.compile_target
Compiled = pxlcompile.Compiled

#: 内存渲染时伪造的入口逻辑路径;纯字节渲染没有真实身份(v1 §8.1)。
_ANONYMOUS_ENTRY = "entry.pxl"


class Rendered(object):
    __slots__ = ("width", "height", "rgba")

    def __init__(self, width, height, rgba):
        self.width = width
        self.height = height
        self.rgba = rgba


def dispatch_version(root):
    """按 v1 §2 读出并分派 version。只返回 1；不支持抛 PXL007,无法表示抛 PXL001。"""
    return pxlcompile._dispatch_version(root)


def render_document(data):
    """渲染一份 version 1 文档字节。v1 若声明了 asset,会因为拿不到 asset root 而报 PXL009。"""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("render_document 需要字节")
    payload = bytes(data)
    root = _parse(payload)
    dispatch_version(root)
    resolver = MemoryResolver({_ANONYMOUS_ENTRY: payload})
    compiled = pxlcompile.compile_target(_ANONYMOUS_ENTRY, resolver, {})
    return Rendered(compiled.width, compiled.height, compiled.rgba)


def _parse(data):
    from .doc import MAX_DOCUMENT_BYTES
    if len(data) > MAX_DOCUMENT_BYTES:
        raise PxlError("PXL002", "文档 UTF-8 字节数 " + str(len(data)) + " 超过 64 MiB")
    text = jsonx.decode_utf8(data)
    root = jsonx.parse(text)
    if not isinstance(root, dict):
        raise PxlError("PXL001", "顶层必须是 JSON 对象")
    return root