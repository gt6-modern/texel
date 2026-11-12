"""F03 回归：mirror 的快照必须只为**可能被覆盖**的源像素分配（发布态复核 P1）。

原实现把整个源矩形复制成快照：目标完全离屏时也要 fw*fh*4 字节（4096² = 64 MiB，
按允许的最大源宽高推算可达 ~16 GiB）。这条用 tracemalloc 量真实峰值，不靠读代码判断。
"""

import sys
import tracemalloc
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import raster  # noqa: E402


class MirrorMemoryTest(unittest.TestCase):
    def test_offscreen_mirror_allocates_bounded_memory(self):
        surface = raster.Surface(1, 1)
        tracemalloc.start()
        try:
            raster._mirror(surface, (0, 0, 4096, 4096), (64, 64), False, False)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        # 完全离屏时一个源像素都不该被读，更不该被快照；64 MiB 是旧实现的下限。
        self.assertLess(peak, 1 << 20, "离屏 mirror 的峰值内存 %d 字节过高" % peak)

    def test_overlapping_mirror_matches_snapshot_semantics(self):
        """源与目标重叠时必须是**读前快照**语义：先取全部源像素，再写。"""
        surface = raster.Surface(4, 4)
        for y in range(4):
            for x in range(4):
                off = (y * 4 + x) * 4
                surface.data[off:off + 4] = bytes([x * 16, y * 16, 0, 255])
        before = bytes(surface.data)

        # 参考实现：完整快照 + 逐像素写（旧语义，作为判据）。
        fx = fy = 0
        expected = bytearray(before)
        fw = fh = 3
        snapshot = [[before[((fy + j) * 4 + (fx + i)) * 4:
                     ((fy + j) * 4 + (fx + i)) * 4 + 4]
                     for i in range(fw)] for j in range(fh)]
        for j in range(fh):
            for i in range(fw):
                dx, dy = 1 + i, 1 + j
                off = (dy * 4 + dx) * 4
                expected[off:off + 4] = snapshot[fh - 1 - j][fw - 1 - i]

        raster._mirror(surface, (0, 0, 3, 3), (1, 1), True, True)
        self.assertEqual(bytes(expected), bytes(surface.data))


if __name__ == "__main__":
    unittest.main()
