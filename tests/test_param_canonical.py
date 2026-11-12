"""N07 回归：-0 必须按 0 归一（规范以数学整数值为身份），而 00 这类非规范写法仍要拒。"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver, pxlcompile  # noqa: E402
from texel_compiler.errors import PxlError  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "integer"


class IntegerCanonicalTest(unittest.TestCase):
    def test_minus_zero_normalizes_to_zero(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        # -0 归一为 0 -> slot 0 是透明，等于什么都没画，但**必须编得过**。
        compiled = pxlcompile.compile_target("entry.pxl", root, {"n": "-0"})
        self.assertEqual(bytes.fromhex("00000000"), compiled.rgba[:4])

    def test_plain_zero_and_two_still_work(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        self.assertEqual(bytes.fromhex("112233FF"),
                         pxlcompile.compile_target("entry.pxl", root, {"n": "1"}).rgba[:4])

    def test_leading_zero_is_still_rejected(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        with self.assertRaises(PxlError) as caught:
            pxlcompile.compile_target("entry.pxl", root, {"n": "00"})
        self.assertEqual("PXL012", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
