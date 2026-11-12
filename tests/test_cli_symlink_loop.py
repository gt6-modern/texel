"""M04 的单元级判据：`_safe_resolve` 必须把符号链接环的异常变成 None。

发布态复核 M04 报的是「旧式 CLI 别名预检遇到输出 symlink 环泄漏 traceback」。
我在本机/容器都构造不出能让 `Path.resolve()` 真抛异常的环（3.10+ 对环返回路径而不是抛），
所以端到端复现没有成功；但这层保护本身可以直接用桩验证：
`_safe_resolve` 只要在 `resolve()` 抛 OSError/RuntimeError 时返回 None，elsewhere 正常返回。
"""

import contextlib
import io
import shutil
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli  # noqa: E402


class _Loop(object):
    """假路径：resolve() 抛 RuntimeError（老版本 pathlib 对符号链接环的报法）。"""

    def resolve(self):
        raise RuntimeError("Symlink loop from %r" % ("loop-a.png",))


class _IoLoop(object):
    def resolve(self):
        raise OSError(17, "File exists")


class SafeResolveTest(unittest.TestCase):
    def test_runtime_error_becomes_none(self):
        self.assertIsNone(cli._safe_resolve(_Loop()))

    def test_os_error_becomes_none(self):
        self.assertIsNone(cli._safe_resolve(_IoLoop()))

    def test_normal_path_resolves(self):
        self.assertEqual(Path(".").resolve(), cli._safe_resolve(Path(".")))


class SymlinkCycleTest(unittest.TestCase):
    """同一 OS 边界异常在所有 CI 平台上都必须在写出前识别。"""

    def test_windows_cant_resolve_filename_is_a_cycle(self):
        # Windows Python 3.8/3.9/3.14 实测：errno=22、winerror=1921。
        failure = OSError(22, "cannot resolve filename")
        failure.winerror = 1921
        with patch.object(Path, "resolve", side_effect=failure):
            self.assertTrue(cli._symlink_cycle("loop-a.png"))

    def test_missing_parent_is_not_a_cycle(self):
        with patch.object(Path, "resolve", side_effect=FileNotFoundError(2, "missing")):
            self.assertFalse(cli._symlink_cycle("new/out.png"))

    def test_other_windows_io_error_is_not_a_cycle(self):
        failure = OSError(22, "invalid name")
        failure.winerror = 123
        with patch.object(Path, "resolve", side_effect=failure):
            self.assertFalse(cli._symlink_cycle("out.png"))


class LegacyCliLoopTest(unittest.TestCase):
    """下游行为：output 解析失败时必须单行退出，不吐 traceback（M04 的端到端部分）。"""

    def test_legacy_cli_reports_io_failure(self):
        tmp = REPO / "tests" / "_tmp" / "cli_loop"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        if True:
            entry = tmp / "entry.pxl"
            entry.write_text(
                chr(123) + chr(34) + "format" + chr(34) + ": " + chr(34) + "PXL" + chr(34) +
                chr(44) + " " + chr(34) + "version" + chr(34) + ": 0, " + chr(34) + "encoding" + chr(34) + ": " + chr(34) + "char" + chr(34) +
                chr(44) + " " + chr(34) + "width" + chr(34) + ": 1, " + chr(34) + "height" + chr(34) + ": 1, " + chr(34) + "palette" + chr(34) + ": [" + chr(34) + "FFFFFFFF" + chr(34) + "], " +
                chr(34) + "layers" + chr(34) + ": [{ " + chr(34) + "name" + chr(34) + ": " + chr(34) + "b" + chr(34) + chr(44) + " " + chr(34) + "blend" + chr(34) + ": " + chr(34) + "normal" + chr(34) + chr(44) + 
                " " + chr(34) + "ops" + chr(34) + ": [{ " + chr(34) + "op" + chr(34) + ": " + chr(34) + "fill" + chr(34) + chr(44) + " " + chr(34) + "slot" + chr(34) + ": 1 }] }]}",
                encoding="utf-8")
            original = cli._safe_resolve
            cli._safe_resolve = lambda path: None  # 模拟"解析输出路径时抛环异常"
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    code = cli.main([str(entry), "-o", str(tmp / "out.png")])
            finally:
                cli._safe_resolve = original
            text = stderr.getvalue()
            self.assertEqual(4, code, "解析失败必须按 I/O 失败退出")
            self.assertEqual(1, len([l for l in text.split(chr(10)) if l.strip()]), "必须只有一行诊断")
            self.assertNotIn("Traceback", text)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
