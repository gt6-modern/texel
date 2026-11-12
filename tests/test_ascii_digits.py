"""规范形式只认 ASCII 十进制：Unicode 数字必须被拒（发布态复核）。

`str.isdigit()` 会接受阿拉伯-印度数字（如 \u0663 = 3），`int()` 也接受；
§6.4 要求的是十进制字面量，所以必须显式限定 ASCII。
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import numlit  # noqa: E402


class AsciiDigitsTest(unittest.TestCase):
    def test_ascii_integer_is_accepted(self):
        self.assertEqual(3, numlit.integer_value("3"))

    def test_arabic_indic_digit_is_rejected(self):
        self.assertIsNone(numlit.integer_value("\u0663"))

    def test_negative_arabic_indic_is_rejected(self):
        self.assertIsNone(numlit.integer_value("-\u0663"))


if __name__ == "__main__":
    unittest.main()
