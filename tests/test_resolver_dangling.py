"""F-R7-03 回归：悬垂的越界链接必须判 ESCAPE（与 Java 对齐）。

此前 Python 的 FileSystemResolver 在 resolve(strict=True) 抛 OSError 时一律返回 MISSING，
于是同一个悬垂链接 Java 报 PXL008、Python 报 PXL009（产品级分叉）。这条同时钉住「指向 root 外」
与「指向 root 内但目标不存在」两种情形，后者仍应是 MISSING。
"""

import os
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver  # noqa: E402

WORK = REPO / "tests" / "_tmp" / "dangling"


class DanglingLinkTest(unittest.TestCase):
    def setUp(self):
        if WORK.exists():
            shutil.rmtree(WORK)
        (WORK / "root" / "parts").mkdir(parents=True)
        (WORK / "outside").mkdir(parents=True)

    def tearDown(self):
        if WORK.exists():
            shutil.rmtree(WORK, ignore_errors=True)

    def _resolver(self):
        return resolver.FileSystemResolver(WORK / "root")

    def test_dangling_link_outside_root_is_escape(self):
        link = WORK / "root" / "parts" / "gone.pxl"
        try:
            os.symlink(str(WORK / "outside" / "not-there.pxl"), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("该平台不允许创建符号链接（Windows 需开发者模式/特权）")
        status, _ = self._resolver().fetch("parts/gone.pxl")
        self.assertEqual(resolver.ESCAPE, status, "悬垂越界链接必须判 ESCAPE")

    def test_dangling_link_inside_root_is_missing(self):
        link = WORK / "root" / "parts" / "gone.pxl"
        try:
            os.symlink(str(WORK / "root" / "parts" / "not-there.pxl"), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("该平台不允许创建符号链接（Windows 需开发者模式/特权）")
        status, _ = self._resolver().fetch("parts/gone.pxl")
        self.assertEqual(resolver.MISSING, status, "root 内的悬垂链接仍是 MISSING")


class SourceLoopTest(unittest.TestCase):
    """读**源**路径时 resolve() 抛 RuntimeError（3.12 的 Windows 环）也不能漏 traceback。"""

    def test_resolve_runtime_error_is_controlled(self):
        root = WORK / "src-root"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        (root / "parts").mkdir(parents=True)
        (root / "parts" / "a.pxl").write_text("{}", encoding="utf-8")
        original = Path.resolve

        def boom(self, strict=False):
            raise RuntimeError("Symlink loop from 'a.pxl'")

        Path.resolve = boom
        try:
            status, _ = resolver.FileSystemResolver(root).fetch("parts/a.pxl")
        finally:
            Path.resolve = original
        self.assertIn(status, (resolver.MISSING, resolver.ESCAPE),
                      "解析异常必须变成受控状态，不能往上抛")

if __name__ == "__main__":
    unittest.main()
