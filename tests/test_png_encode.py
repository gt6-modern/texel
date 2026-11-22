"""测试:公开 PNG 编码器的尺寸契约(规范 §8.1 / §8.3)。

公开 API ``encode_png`` 可以产出 PNG 给外部用,所以它必须守住与 ``pngdec`` 和规范
完全相同的输入上限:单边 ``1..65536``、面积 ``≤ 2^22``。守不住就会出现"编码成功、
Texel 自己读不回来"的结果,把错误推迟到下游导入/解析阶段(审查 #9/#10)。

这里只断言"越界必须在写字节前被拒绝",以及两个边界本身仍然合法。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / "compiler" / "python"

if str(COMPILER) not in sys.path:
    sys.path.insert(0, str(COMPILER))

from texel_compiler import encode_png
from texel_compiler import pngdec

#: 规范 §8.1 的两个上限。测试里写死数值:从被测模块读常量的话,常量改错了也一致。
MAX_SIDE = 65536
MAX_AREA = 1 << 22


class EncodeSizeTest(unittest.TestCase):

    def assert_rejected(self, width, height, what):
        with self.assertRaises(ValueError) as caught:
            encode_png(width, height, bytes(width * height * 4))
        self.assertIn(what, str(caught.exception))

    def test_rejects_zero_dimensions(self):
        self.assert_rejected(0, 0, "宽或高为 0")
        self.assert_rejected(0, 4, "宽或高为 0")
        self.assert_rejected(4, 0, "宽或高为 0")

    def test_rejects_side_over_65536(self):
        self.assert_rejected(MAX_SIDE + 1, 1, "宽或高超过 65536")
        self.assert_rejected(1, MAX_SIDE + 1, "宽或高超过 65536")

    def test_rejects_area_over_2_22(self):
        # 2049 × 2049 = 4,198,401 只比 2^22 多 4097,单边却远小于 65536:
        # 只校验单边的编码器会放过它(审查 #10)。
        self.assertGreater(2049 * 2049, MAX_AREA)
        self.assert_rejected(2049, 2049, "面积超过 2^22")
        # 4096 × 1025 = 4,198,400:两边都远小于 65536,只有面积越界。
        self.assertGreater(4096 * 1025, MAX_AREA)
        self.assert_rejected(4096, 1025, "面积超过 2^22")

    def test_accepts_the_boundaries(self):
        # 单边 65536 与整块 2^22 面积都是合法输入:拒绝越界不能顺手拒绝边界。
        for width, height in ((1, 1), (MAX_SIDE, 1), (1, MAX_SIDE), (2048, 2048)):
            self.assertLessEqual(width * height, MAX_AREA)
            try:
                png = encode_png(width, height, bytes(width * height * 4))
            except ValueError as failure:
                raise AssertionError("边界尺寸 %dx%d 不应被拒绝: %s"
                                     % (width, height, failure))
            decoded = pngdec.decode(png)
            self.assertEqual(width, decoded.width)
            self.assertEqual(height, decoded.height)


if __name__ == "__main__":
    unittest.main()
