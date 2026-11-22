"""旧式 CLI 的别名闸门回归（发布态复核 P1）。

`texel -o entry.pxl entry.pxl` 曾把入口 PXL 静默写成 PNG 并返回 0。新式 compile/check
有闸门，但旧式入口独立解析参数——只保护一边等于没保护。

夹具放在仓库里（tests/fixtures/legacy），因为宿主沙箱下 tempfile 目录不可写；
输出写到 tests/_tmp（本目录由测试自己创建并清理），不碰夹具本身。
"""

import os
import shutil
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli as cli_module  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "legacy" / "entry.pxl"
WORK = REPO / "tests" / "_tmp"
#: 一份 `stamp` 了同目录 `parts/blade.pxl` 的 v1 文档：旧式批量入口曾经解析不了它。
ASSET_CASE = REPO / "spec" / "conformance" / "v1" / "cases" / "graph-stamp-pxl"
BATCH = WORK / "legacy_batch"


def tiny_png():
    """自己拼一张 1×1 红色 RGBA PNG，给"PNG 素材"那条用例做输入。"""
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


MINI_DOCUMENT = ('{"format":"PXL","version":1,"encoding":"char","width":1,"height":1,'
                 '"palette":["FF0000FF"],"layers":[{"name":"base","blend":"normal",'
                 '"ops":[{"op":"fill","slot":1}]}]}')

#: `stamp` 一张同目录的 PNG 素材：它的依赖清单里会出现 `sprite.png`。
PNG_ASSET_DOCUMENT = (
    '{"format":"PXL","version":1,"encoding":"char","width":1,"height":1,'
    '"palette":["FF0000FF"],"assets":{"sprite":{"kind":"png","path":"sprite.png"}},'
    '"layers":[{"name":"base","blend":"normal",'
    '"ops":[{"op":"stamp","asset":"sprite","x":0,"y":0}]}]}'
)


def make_junction(link, target):
    """建一个指向目录的链接（Windows 用 junction，不需要管理员）。建不出来返回 False。"""
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0 and Path(link).exists()
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    return True


class LegacyAliasTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.before = FIXTURE.read_bytes()

    def tearDown(self):
        self.assertEqual(self.before, FIXTURE.read_bytes(), "夹具源文件不得被改写")
        for leftover in list(WORK.glob("legacy-*.png")) + list(WORK.glob("legacy-*.pxl")):
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

    def test_output_hardlinked_to_the_input_is_refused(self):
        """`-o entry.png entry.pxl`（entry.png 是入口的硬链接）曾退出 0 并把入口改写成 PNG。

        两个路径不同、inode 相同：`resolve()` 归一不了这一类，必须问文件身份（Java 侧
        用的是 `BasicFileAttributes.fileKey()`）。
        """
        target = WORK / "legacy-hardlink.png"
        try:
            os.link(str(FIXTURE), str(target))
        except OSError:
            self.skipTest("该文件系统不支持硬链接")
        code = cli_module.main(["-o", str(target), str(FIXTURE)])
        self.assertEqual(2, code, "输出与输入是同一个 inode，必须按用法错拒绝")
        self.assertEqual(self.before, FIXTURE.read_bytes(), "入口源文件不得被改写")

    def test_two_outputs_hardlinked_to_each_other_are_refused(self):
        """同批两个输出互为硬链接时，后写的那份会盖掉前一份。"""
        first = WORK / "legacy-a.pxl"
        second = WORK / "legacy-b.pxl"
        first.write_bytes(self.before)
        second.write_bytes(self.before)
        first_target = WORK / "legacy-a.png"
        first_target.write_bytes(b"sentinel")
        try:
            os.link(str(first_target), str(WORK / "legacy-b.png"))
        except OSError:
            self.skipTest("该文件系统不支持硬链接")
        code = cli_module.main([str(first), str(second)])
        self.assertEqual(2, code, "两个输出是同一个 inode，必须按用法错拒绝")
        self.assertEqual(b"sentinel", first_target.read_bytes(), "既有产物不得被改写")

    def test_normal_legacy_render_still_works(self):
        target = WORK / "legacy-ok.png"
        code = cli_module.main(["-o", str(target), str(FIXTURE)])
        self.assertEqual(0, code)
        self.assertTrue(target.is_file(), "正常路径仍要产物")


class LegacyBatchTest(unittest.TestCase):
    """旧式批量入口必须与 Java 的**同形**入口给出同样结果。

    2026-11-20 审查的三条实测缺陷都在这条入口上：asset 解析、`-o` 准入分类、目录展开。
    """

    def setUp(self):
        if BATCH.exists():
            shutil.rmtree(BATCH)
        BATCH.mkdir(parents=True)
        shutil.copytree(str(ASSET_CASE), str(BATCH / "case"))
        self.entry = BATCH / "case" / "entry.pxl"

    def tearDown(self):
        if BATCH.exists():
            shutil.rmtree(BATCH, ignore_errors=True)

    def test_batch_resolves_assets_relative_to_the_source_directory(self):
        """带 asset 的 v1 文档曾报 PXL009「依赖缺失」，而 Java 侧同一个文件能编译。

        旧实现走 `api.render_document`（只有入口字节的内存 resolver），拿不到 asset
        root；Java 的批量入口用的是「源文件所在目录」。
        """
        code = cli_module.main([str(self.entry)])
        self.assertEqual(0, code, "旧式批量入口必须能解析源文件同目录的 asset")
        self.assertTrue((self.entry.parent / "entry.png").is_file(), "要产出 PNG")

    def test_output_pointing_at_a_directory_is_a_usage_error(self):
        """`-o <已存在目录>` 曾 exit 1，并把内部临时文件名印给使用者（Java 侧 exit 2）。"""
        code = cli_module.main(["-o", str(self.entry.parent), str(self.entry)])
        self.assertEqual(2, code, "输出路径是目录属于用法错，不是某个文件处理失败")

    def test_output_must_not_overwrite_a_compiled_dependency(self):
        """`-o parts/blade.pxl` 会把被引用的子文档截断成 PNG —— 必须在写之前拒绝。

        批量预检只知道**输入**，而依赖要编译完才拿得到；所以先做一次"只解析不渲染"的
        预扫，再按整批的依赖清单在写之前判。原来没有这一格：命令成功、依赖被就地替换。
        """
        dependency = self.entry.parent / "parts" / "blade.pxl"
        before = dependency.read_bytes()
        code = cli_module.main(["-o", str(dependency), str(self.entry)])
        self.assertNotEqual(0, code, "覆盖依赖必须失败")
        self.assertEqual(before, dependency.read_bytes(), "依赖不得被截断成 PNG")
        self.assertFalse((self.entry.parent / "entry.png").exists(), "被拒的批次不得产出其它产物")

    def test_batch_refuses_an_output_that_overwrites_another_entry_asset(self):
        """跨入口：`sprite.pxl` 的默认输出 `sprite.png` 正是 `entry.pxl` stamp 的素材。

        预检只比"输出 vs 输入"（`sprite.png` 不是输入，所以过得去），依赖清单只有解析完
        才知道，而逐个入口"编译完再判自己"时前面几份产物已经落盘。所以整批先做一次只解析
        的预扫，把每个输出对**所有**入口的依赖比一遍，整批拒绝（CLI002，写任何文件之前）。
        """
        case = BATCH / "cross"
        case.mkdir(parents=True)
        (case / "sprite.png").write_bytes(tiny_png())
        (case / "sprite.pxl").write_text(MINI_DOCUMENT, encoding="utf-8")
        (case / "entry.pxl").write_text(PNG_ASSET_DOCUMENT, encoding="utf-8")
        before = (case / "sprite.png").read_bytes()

        code = cli_module.main([str(case)])

        self.assertEqual(2, code, "输出覆盖依赖属于用法错（CLI002），不是逐文件失败")
        self.assertEqual(before, (case / "sprite.png").read_bytes(),
                         "被引用的 PNG 素材不得被改写成编译产物")
        self.assertEqual([], sorted(p.name for p in case.glob("*.png") if p.name != "sprite.png"),
                         "整批拒绝时一个新产物都不许留下")

    def test_empty_output_is_a_usage_error(self):
        """`-o ""` 曾被 `Path("")` 当成当前目录，然后整批失败 exit 1。"""
        code = cli_module.main(["-o", "", str(self.entry)])
        self.assertEqual(2, code, "空输出路径属于用法错")

    def test_root_path_with_parent_segments_is_still_a_directory(self):
        """`dir/..` 是普通父目录引用，不是链接：判据不能只看词法路径。"""
        inner = BATCH / "case" / "inner"
        inner.mkdir()
        code = cli_module.main([str(inner / "..")])
        self.assertEqual(0, code, "dir/.. 是普通目录，必须照常编译")
        self.assertTrue((self.entry.parent / "entry.png").is_file(), "应产出 PNG")

    def test_link_like_directory_root_is_refused(self):
        """输入**根**本身是链接目录时也要挡。

        递归里的剪枝只看子项：根是 junction 时整棵树都会走到链接目标上，"目录模式处理
        哪些文件"取决于本机链接（2026-11-20 审查 P2）。
        """
        outside = BATCH / "outside"
        outside.mkdir()
        shutil.copy(str(self.entry), str(outside / "z.pxl"))
        if not make_junction(BATCH / "rootlink", outside):
            self.skipTest("本机无法创建 junction/目录符号链接")
        code = cli_module.main([str(BATCH / "rootlink")])
        self.assertNotEqual(0, code, "链接目录根必须被拒绝")
        self.assertFalse((outside / "z.png").exists(), "不得编译链接目标下的 .pxl")

    def test_batch_does_not_follow_a_junction(self):
        """junction 在 Windows 上不算 islink，`os.walk(followlinks=False)` 照样走进去。

        走进去就会把**给定目录之外**的 .pxl 也编译出 PNG（写回树外），而且"哪些文件被
        导出"取决于这台机器上谁建过链接。
        """
        outside = BATCH / "outside"
        outside.mkdir()
        shutil.copy(str(self.entry), str(outside / "z.pxl"))
        if not make_junction(BATCH / "case" / "link", outside):
            self.skipTest("本机无法创建 junction/目录符号链接")
        code = cli_module.main([str(BATCH / "case")])
        self.assertEqual(0, code)
        self.assertTrue((self.entry.parent / "entry.png").is_file())
        self.assertFalse((outside / "z.png").exists(), "不得编译给定目录之外的 .pxl")


if __name__ == "__main__":
    unittest.main()
