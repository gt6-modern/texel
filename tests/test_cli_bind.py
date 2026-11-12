"""GATE-2b 回归：CLI 路径上的 --bind 必须真的被测到（F-R7-01）。

上一版 tests/test_bind.py 走的是库 API，与文件头声称的「CLI」相反，而且回退 N06 仍全绿。
真实形状：asset 参数绑 "true" —— N06 会把 true 预转换成布尔，于是类型不符报 PXL012；
修好后应正常编译。这条**真走 cli.main**，因此也能钉住 cli.py 的 _bind_value。
"""

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli as cli_module  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "asset_true"
WORK = REPO / "tests" / "_tmp" / "cli_bind"


class CliBindTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)

    def _run(self, *args):
        cwd = os.getcwd()
        os.chdir(str(FIXTURE))
        try:
            return cli_module.main(list(args))
        finally:
            os.chdir(cwd)

    def test_asset_param_bound_to_symbol_true(self):
        target = WORK / "asset-true.png"
        code = self._run("compile", "entry.pxl", "--asset-root", ".",
                         "--bind", "tgt=true", "-o", str(target))
        self.assertEqual(0, code, "asset 参数绑 true 必须成功（N06 会把它当布尔）")
        self.assertTrue(target.is_file(), "必须真的产出 PNG")
        if target.exists():
            target.unlink()

    def test_asset_param_bound_to_unknown_symbol_is_pxl012(self):
        target = WORK / "asset-bad.png"
        code = self._run("compile", "entry.pxl", "--asset-root", ".",
                         "--bind", "tgt=nope", "-o", str(target))
        self.assertNotEqual(0, code, "未声明的 asset 符号必须失败")


if __name__ == "__main__":
    unittest.main()
