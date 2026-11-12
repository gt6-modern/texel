"""旧式 CLI 的别名闸门回归（发布态复核 P1）。

`texel -o entry.pxl entry.pxl` 曾把入口 PXL 静默写成 PNG 并返回 0。新式 compile/check
有闸门，但旧式入口独立解析参数——只保护一边等于没保护。

夹具放在仓库里（tests/fixtures/legacy），因为宿主沙箱下 tempfile 目录不可写；
输出写到 tests/_tmp（本目录由测试自己创建并清理），不碰夹具本身。
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli as cli_module  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "legacy" / "entry.pxl"
WORK = REPO / "tests" / "_tmp"


class LegacyAliasTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.before = FIXTURE.read_bytes()

    def tearDown(self):
        self.assertEqual(self.before, FIXTURE.read_bytes(), "夹具源文件不得被改写")
        for leftover in WORK.glob("legacy-*.png"):
            leftover.unlink()

    def test_output_equal_to_input_is_rejected(self):
        code = cli_module.main(["-o", str(FIXTURE), str(FIXTURE)])
        self.assertEqual(2, code, "输出等于输入必须按用法错退出")

    def test_two_inputs_one_output_is_rejected(self):
        second = WORK / "legacy-second.pxl"
        second.write_bytes(self.before)
        try:
            target = WORK / "legacy-same.png"
            code = cli_module.main(["-o", str(target), str(FIXTURE), str(second)])
            self.assertEqual(2, code, "-o 只允许一个输入")
        finally:
            if second.exists():
                second.unlink()

    def test_normal_legacy_render_still_works(self):
        target = WORK / "legacy-ok.png"
        code = cli_module.main(["-o", str(target), str(FIXTURE)])
        self.assertEqual(0, code)
        self.assertTrue(target.is_file(), "正常路径仍要产物")


if __name__ == "__main__":
    unittest.main()
