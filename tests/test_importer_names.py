"""审查 #25 / #26:importer 的文件名与诊断边界。

两条都是"合法输入把 CLI 变成 traceback 或静默漏文件":

* **#25** POSIX 文件名允许非 UTF-8 字节。Python 把这类名字解码成 surrogate 字符,
  排序时无条件 `encode("utf-8")` 会抛 `UnicodeEncodeError` —— 显式模式当场 traceback,
  目录模式则整个批次失败。排序必须走可逆的文件系统编码(`os.fsencode`)。
* **#26** 非空但超长的 `-o` 值在参数解析阶段调用 `is_dir()`,抛 `OSError(ENAMETOOLONG)`,
  那时还没进 `main()` 的兜底区,于是输出 traceback。探测失败必须按"不是目录"处理,
  由写入侧给出单行 `写出失败`。

非 UTF-8 文件名在 Windows 上造不出来(文件系统是 UTF-16),那两条按平台 skip;
`-o` 超长名在 Windows 上同样会失败,断言"不吐 traceback 且非零退出"仍然成立。
"""

import io
import os
import shutil
import struct
import sys
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "tests" / "_sandbox"

from importer.__main__ import main as importer_main  # noqa: E402

POSIX_ONLY = unittest.skipIf(os.name == "nt",
                             "非 UTF-8 文件名只在 POSIX 上可构造")


def tiny_png():
    """自己拼一张 1×1 红色 RGBA PNG:不借用编译器的编码器,也不依赖二进制夹具。"""
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    scanline = b"\x00\xff\x00\x00\xff"          # 过滤器 0 + 一个 RGBA 像素
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(scanline)) + chunk(b"IEND", b""))


class ImporterNameTest(unittest.TestCase):

    def setUp(self):
        self.work = SANDBOX / "importer-names"
        shutil.rmtree(str(self.work), ignore_errors=True)
        self.work.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(str(self.work), ignore_errors=True)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = importer_main(argv)
        return code, out.getvalue(), err.getvalue()

    def make_png(self, raw_name):
        """按**原始字节**文件名造一个合法 PNG(POSIX 专用)。"""
        source = tiny_png()
        target = os.path.join(os.fsencode(str(self.work)), raw_name)
        with open(target, "wb") as stream:
            stream.write(source)
        return target

    @POSIX_ONLY
    def test_non_utf8_name_is_imported_in_directory_mode(self):
        raw = b"bad\xff.png"
        self.make_png(raw)
        code, out, err = self.run_main([str(self.work)])
        self.assertNotIn("Traceback", err)
        self.assertEqual(0, code, err)
        produced = [name for name in os.listdir(os.fsencode(str(self.work)))
                    if name.endswith(b".pxl")]
        self.assertEqual([b"bad\xff.pxl"], produced, "非 UTF-8 名字也必须被导入")

    @POSIX_ONLY
    def test_non_utf8_name_is_imported_when_named_explicitly(self):
        raw = b"bad\xff.png"
        target = self.make_png(raw)
        out_pxl = os.path.join(os.fsencode(str(self.work)), b"out.pxl")
        code, out, err = self.run_main(["-o", os.fsdecode(out_pxl), os.fsdecode(target)])
        self.assertNotIn("Traceback", err)
        self.assertEqual(0, code, err)
        self.assertTrue(os.path.exists(out_pxl), "显式模式必须写出 out.pxl")

    def test_overlong_output_name_reports_one_line(self):
        entry = self.work / "entry.png"
        entry.write_bytes(tiny_png())
        overlong = str(self.work / ("a" * 300 + ".pxl"))
        code, out, err = self.run_main(["-o", overlong, str(entry)])
        self.assertNotIn("Traceback", err, "超长 -o 不得以 traceback 结束")
        self.assertNotEqual(0, code, "超长 -o 必须非零退出")
        self.assertEqual(1, len(err.splitlines()),
                         "诊断必须恰好一行: %r" % (err,))
        self.assertTrue(err.startswith("texel-import: "), err)


    def test_output_path_is_parsed_through_the_guard(self):
        """参数解析必须走 `_probe_is_dir`,不能直接 `out_path.is_dir()`。

        这一条是**结构判据**:上一条集成用例只在"`is_dir()` 真的会抛"的平台上咬人,
        而这条在任何平台上都能发现"有人把探测改回去"。
        """
        from importer import __main__ as importer_module
        from unittest import mock
        entry = self.work / "entry.png"
        entry.write_bytes(tiny_png())
        seen = []
        real = importer_module._probe_is_dir

        def spy(path):
            seen.append(str(path))
            return real(path)

        with mock.patch.object(importer_module, "_probe_is_dir", spy):
            code, out, err = self.run_main(["-o", str(self.work / "out.pxl"), str(entry)])
        self.assertEqual(0, code, err)
        self.assertIn(str(self.work / "out.pxl"), seen,
                      "参数解析没有经过 _probe_is_dir")


class OutputProbeTest(unittest.TestCase):
    """`_probe_is_dir` 的判据:探测本身失败不算"是目录",更不能把异常抛到解析阶段。

    上面那条集成用例只在"`Path.is_dir()` 真的会抛"的平台上有判据 —— CPython 3.8 在
    POSIX 上对 ENAMETOOLONG 会抛,较新的版本把它并进"忽略"名单,Windows 的文件系统
    根本造不出这种名字。所以这里直接注入那个异常,让门禁在任何平台上都咬人。
    """

    def test_probe_absorbs_oserror(self):
        from importer import __main__ as importer_module
        from unittest import mock
        with mock.patch.object(Path, "is_dir",
                               side_effect=OSError(36, "File name too long")):
            self.assertFalse(importer_module._probe_is_dir(Path("a" * 300 + ".pxl")))

    def test_probe_passes_real_answers_through(self):
        from importer import __main__ as importer_module
        self.assertTrue(importer_module._probe_is_dir(SANDBOX))
        self.assertFalse(importer_module._probe_is_dir(SANDBOX / "no-such-dir"))


class SortKeyTest(unittest.TestCase):

    def test_sort_key_orders_by_file_system_bytes(self):
        from importer.__main__ import _sort_key
        self.assertLess(_sort_key("a.png"), _sort_key("b.png"))
        self.assertEqual(b"a/b.png", _sort_key("a\\b.png"))

    @POSIX_ONLY
    def test_sort_key_accepts_surrogate_names(self):
        from importer.__main__ import _sort_key
        self.assertEqual(b"bad\xff.png", _sort_key("bad\udcff.png"))


if __name__ == "__main__":
    unittest.main()
