"""审查 #18:完全离屏的 `stamp` 不该复制素材。

Python 曾在 asset stamp 执行前，先按素材的**整高**切出每一行的字节切片，再做纵向裁剪。
画布 1×1、素材 2048×2048 时，一次完全不可见的 stamp 就复制 16 MiB；规范计费的裁剪写入
面积仍是 0，于是"写 0 字节"的 op 可以不受预算约束地把编译拖垮。

判据与 `tests/test_mirror_memory.py` 一致：用 tracemalloc 量峰值，不靠读代码判断；再用
可运行性判据证明裁剪**没有改变像素结果**（可见部分与逐行裁剪前的语义逐字节相同）。
"""

import sys
import tracemalloc
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import raster  # noqa: E402


class FakeAsset(object):
    """`raster` 只读这三个字段，不必造一整套 asset 解析。"""

    def __init__(self, width, height, rgba=None):
        self.width = width
        self.height = height
        self.rgba = rgba if rgba is not None else bytes(width * height * 4)


class FakeStamp(object):
    def __init__(self, asset, x, y, flip_x=False, flip_y=False):
        self.asset = asset
        self.x = x
        self.y = y
        self.flip_x = flip_x
        self.flip_y = flip_y


def stamped_peak(surface, stamp):
    plan = [("stamp_asset", stamp)]
    tracemalloc.start()
    try:
        raster.execute_ops(surface, [bytes((0, 0, 0, 0))], plan)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def pattern(width, height):
    """可区分的素材像素：逐像素编码坐标，翻转/错位一目了然。"""
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data += bytes((x * 17 & 0xFF, y * 29 & 0xFF, 0x40, 0xFF))
    return bytes(data)


class OffscreenStampMemoryTest(unittest.TestCase):

    def test_fully_offscreen_stamp_copies_nothing(self):
        asset = FakeAsset(2048, 2048)
        surface = raster.Surface(1, 1)
        peak = stamped_peak(surface, FakeStamp(asset, -65536, -65536))
        # 旧实现每次复制 16 MiB（2048 × 2048 × 4），这里必须只有"没有复制"的量级。
        self.assertLess(peak, 1 << 20, "离屏 stamp 的峰值内存 %d 字节过高" % peak)
        self.assertEqual(bytes(4), bytes(surface.data), "离屏 stamp 不得改动画布")

    def test_offscreen_to_the_right_copies_nothing(self):
        asset = FakeAsset(1024, 1024)
        surface = raster.Surface(4, 4)
        before = bytes(surface.data)
        peak = stamped_peak(surface, FakeStamp(asset, 64, 0))
        self.assertLess(peak, 1 << 20, "右侧离屏 stamp 的峰值内存 %d 字节过高" % peak)
        self.assertEqual(before, bytes(surface.data))

    def test_vertical_offscreen_copies_nothing(self):
        asset = FakeAsset(1024, 1024)
        surface = raster.Surface(4, 4)
        before = bytes(surface.data)
        peak = stamped_peak(surface, FakeStamp(asset, 0, -4096))
        self.assertLess(peak, 1 << 20, "上侧离屏 stamp 的峰值内存 %d 字节过高" % peak)
        self.assertEqual(before, bytes(surface.data))


class ClippedStampEquivalenceTest(unittest.TestCase):
    """裁剪只许少复制，不许改变像素：与"整行切开后再裁剪"的语义逐字节相同。"""

    def reference(self, surface, stamp):
        """旧语义的参考实现：先按素材整高切行，再交给 `_draw_rows` 裁剪。"""
        reference = raster.Surface(surface.width, surface.height)
        reference.data[:] = surface.data
        asset = stamp.asset
        stride = asset.width * 4
        rows = [asset.rgba[row * stride:(row + 1) * stride] for row in range(asset.height)]
        raster._draw_rows(reference, stamp.x, stamp.y, rows, asset.width,
                          stamp.flip_x, stamp.flip_y)
        return bytes(reference.data)

    def check(self, surface, stamp):
        expected = self.reference(surface, stamp)
        raster.execute_ops(surface, [bytes((0, 0, 0, 0))], [("stamp_asset", stamp)])
        self.assertEqual(expected, bytes(surface.data),
                         "裁剪后的像素必须与整行切开的语义一致 (x=%d y=%d flipx=%s flipy=%s)"
                         % (stamp.x, stamp.y, stamp.flip_x, stamp.flip_y))

    def test_partial_overlaps_match_row_slicing_semantics(self):
        asset = FakeAsset(3, 4, pattern(3, 4))
        for x, y in ((0, 0), (-1, -2), (-3, 1), (2, -3), (4, 4), (-3, -4), (1, 2)):
            for flip_x in (False, True):
                for flip_y in (False, True):
                    surface = raster.Surface(5, 5)
                    surface.data[:] = pattern(5, 5)
                    self.check(surface, FakeStamp(asset, x, y, flip_x, flip_y))


if __name__ == "__main__":
    unittest.main()
