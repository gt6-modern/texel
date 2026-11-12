"""F-R7-02 回归：Python 的别名判定必须按**文件身份**（硬链接），不只按路径。

Java 侧早有 v1-deps-hardlink-cannot-truncate-entry；Python 侧此前零用例，回退 samefile 全绿。
"""

import os
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli as cli_module  # noqa: E402

SRC = REPO / "tests" / "fixtures" / "asset_true"
WORK = REPO / "tests" / "_tmp" / "cli_alias"


class HardlinkAliasTest(unittest.TestCase):
    def setUp(self):
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(parents=True)
        shutil.copytree(str(SRC / "parts"), str(WORK / "parts"))
        shutil.copy(str(SRC / "entry.pxl"), str(WORK / "entry.pxl"))

    def tearDown(self):
        if WORK.exists():
            shutil.rmtree(WORK, ignore_errors=True)

    def test_output_hardlinked_to_dependency_is_rejected(self):
        dep = WORK / "parts" / "leaf.pxl"
        before = dep.read_bytes()
        alias = WORK / "alias.png"
        try:
            os.link(str(dep), str(alias))
        except OSError:
            self.skipTest("该文件系统不支持硬链接")
        cwd = os.getcwd()
        os.chdir(str(WORK))
        try:
            code = cli_module.main(["compile", "entry.pxl", "--asset-root", ".",
                                    "--bind", "tgt=true", "-o", "alias.png"])
        finally:
            os.chdir(cwd)
        self.assertNotEqual(0, code, "输出硬链接到依赖必须被拒")
        self.assertEqual(before, dep.read_bytes(), "依赖源文件不得被改写")


    def test_output_and_deps_same_path_is_rejected(self):
        """-o X --deps X 曾 exit 0：先写出的清单被 PNG 覆盖（发布态复核 P2）。"""
        cwd = os.getcwd()
        os.chdir(str(WORK))
        try:
            code = cli_module.main(["compile", "entry.pxl", "--asset-root", ".",
                                    "--bind", "tgt=true", "-o", "both.tsv",
                                    "--deps", "both.tsv"])
        finally:
            os.chdir(cwd)
        self.assertEqual(2, code, "输出与清单同路径必须按用法错退出")


if __name__ == "__main__":
    unittest.main()
