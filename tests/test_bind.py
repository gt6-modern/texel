"""GATE-2 回归：CLI 的 --bind 必须真的被测到（此前全仓一条 --bind 用例都没有，
于是一条 boolean 绑定的回归能一路绿灯跑到复审）。

同时钉住两件事：
  - F-R6-03：boolean 参数绑 "true"/"false" 必须成功（CLI 不再预转换，靠声明类型解析）；
  - N06：color 参数绑 "true" 必须是类型不符（而不是被提前转成布尔后报别的码）。
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver, pxlcompile  # noqa: E402
from texel_compiler.errors import PxlError  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "bools"


class BindTest(unittest.TestCase):
    def test_boolean_binding_true_flips(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        compiled = pxlcompile.compile_target("entry.pxl", root, {"flag": "true"})
        # flipX 生效：源 tile 是 [112233FF, 445566FF]，翻转后首像素是 445566FF。
        self.assertEqual(bytes.fromhex("445566FF"), compiled.rgba[:4])

    def test_boolean_binding_false_keeps_order(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        compiled = pxlcompile.compile_target("entry.pxl", root, {"flag": "false"})
        self.assertEqual(bytes.fromhex("112233FF"), compiled.rgba[:4])
        self.assertEqual(2, compiled.width)

    def test_boolean_binding_rejects_non_boolean(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        with self.assertRaises(PxlError) as caught:
            pxlcompile.compile_target("entry.pxl", root, {"flag": "maybe"})
        self.assertEqual("PXL012", caught.exception.code)

    def test_color_binding_rejects_symbol_true(self):
        """N06：color 参数拿到 "true" 必须是类型不符，不能被当成布尔。"""
        root = resolver.build_memory_resolver(str(FIXTURE))
        with self.assertRaises(PxlError) as caught:
            pxlcompile.compile_target("entry.pxl", root, {"c": "true"})
        self.assertEqual("PXL012", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
