"""PXL 抽取器:位图(PNG)→ 文本(PXL)。

规范唯一真源是 ``spec/PXL.md``;本包只读 PNG,不读 PXL,也不依赖 ``texel-compiler``。

公开面(刻意保持最小):

    extract_png(data: bytes, raw: bool = False) -> str
    extract_file(path, raw: bool = False) -> str
    PxlError

``raw=True`` 输出"整图一条 grid"的逐像素基准形态(附录 A 第 8 步的全局回退
形态),用于与压缩版做逐像素等价性对照。

``extract_png`` / ``extract_file`` 是**惰性**暴露的(PEP 562):子模块之间的
依赖链 ``extract → png/palette/compress/serialize`` 较长,惰性导入让
``from importer.errors import PxlError`` 这类局部引用不必拉起整条链。
"""

from .errors import (
    ALL_CODES,
    PXL001,
    PXL002,
    PXL003,
    PXL004,
    PXL005,
    PXL006,
    PxlError,
)

__all__ = [
    "ALL_CODES",
    "PXL001",
    "PXL002",
    "PXL003",
    "PXL004",
    "PXL005",
    "PXL006",
    "PxlError",
    "extract_file",
    "extract_png",
]

_LAZY = ("extract_png", "extract_file")


def __getattr__(name):
    if name in _LAZY:
        from . import extract as _extract

        return getattr(_extract, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
