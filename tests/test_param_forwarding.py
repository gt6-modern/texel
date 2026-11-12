"""F04 回归：父参引必须能转发到子 PXL 的参数（发布态复核 P1）。

此前两侧都不支持：子文档拿到的是替换前的字面 "$c"，于是把合法转发拒成 PXL012。
这条同时钉两件事——**能编过**，以及**转发值真的生效**（首像素等于默认色 B87333FF）。
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver, pxlcompile  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "forward"


class ParentParamForwardingTest(unittest.TestCase):
    def test_parent_color_forwarded_to_child(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        compiled = pxlcompile.compile_target("entry.pxl", root, {})
        self.assertEqual(2, compiled.width)
        self.assertEqual(2, compiled.height)
        # 子文档 palette 是 ["$tint"]，转发成功后应等于父参数默认色 B87333FF。
        self.assertEqual(bytes.fromhex("B87333FF"), compiled.rgba[:4])

    def test_forwarding_uses_bound_value_not_default(self):
        """绑定覆盖时转发的是**绑定值**，不是默认值——否则"转发"只是把默认值抄了一遍。"""
        root = resolver.build_memory_resolver(str(FIXTURE))
        compiled = pxlcompile.compile_target("entry.pxl", root, {"c": "112233FF"})
        self.assertEqual(bytes.fromhex("112233FF"), compiled.rgba[:4])


if __name__ == "__main__":
    unittest.main()
