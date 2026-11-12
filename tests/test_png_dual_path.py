"""§13.5 永久双路径 PNG 门禁:第二个 compiler(Python)的 decoder 也要过同一批接受/拒绝集。

为什么单列:Python compiler 的语料 runner 只判 compiler 面,抽取侧 PNG 用例(neg-png-*)被跳过,
而它们的判据正落在解码器上。这里直接对 texel_compiler.pngdec 跑同一批语料,把
「三个实现看同一份字节必须给出同一结论」变成可执行判据(此前只有 Java 侧有)。
"""

import hashlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import pngdec  # noqa: E402

CASES = REPO / "spec/conformance/cases"


class PngDualPathTest(unittest.TestCase):
    def test_negative_png_cases_are_rejected(self):
        rejected = 0
        for directory in sorted((CASES / "negative").iterdir()):
            name = directory.name
            if not (name.startswith("neg-png-") or "iccp-crc" in name):
                continue
            source = directory / "input.png"
            self.assertTrue(source.is_file(), name + " 缺 input.png")
            with self.assertRaises(Exception, msg=name) as caught:
                pngdec.decode(source.read_bytes())
            self.assertEqual("PXL006", getattr(caught.exception, "code", None),
                             "%s 的拒绝码应为 PXL006" % name)
            rejected += 1
        self.assertGreaterEqual(rejected, 33, "拒绝集数量不足,语料可能被误改")

    def test_accept_set_matches_pixels(self):
        accepted = 0
        for source in sorted(CASES.rglob("input.png")):
            expected = source.parent / "expected.pixels.sha256"
            if not expected.is_file():
                continue
            image = pngdec.decode(source.read_bytes())
            label = str(source.parent.relative_to(CASES))
            self.assertEqual(4 * image.width * image.height, len(image.rgba),
                             "RGBA 长度与尺寸不符: " + label)
            self.assertEqual(expected.read_text(encoding="utf-8").strip(),
                             hashlib.sha256(image.rgba).hexdigest(), label)
            accepted += 1
        self.assertGreaterEqual(accepted, 600, "接受集数量不足,语料可能被误改")


if __name__ == "__main__":
    unittest.main()
