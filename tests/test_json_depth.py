"""N08 回归：JSON 嵌套上限两侧必须一致（Java 128 / Python 128）。"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import jsonx  # noqa: E402
from texel_compiler.errors import PxlError  # noqa: E402


def nested(levels):
    return "[" * levels + "1" + "]" * levels


class JsonDepthTest(unittest.TestCase):
    def test_100_levels_parse(self):
        # 100 层：Java 侧（128）能解析，Python 侧的 64 曾经在这里报 PXL001。
        self.assertIsNotNone(jsonx.parse(nested(100)))

    def test_over_limit_still_rejected(self):
        with self.assertRaises(PxlError) as caught:
            jsonx.parse(nested(130))
        self.assertEqual("PXL001", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
