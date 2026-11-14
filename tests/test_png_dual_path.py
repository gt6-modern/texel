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
        """凡是"输入是 PNG、期望 PXL006"的负例，解码器都必须拒绝。

        以前这里靠名字前缀 `neg-png-` 划集合：改个名就静默退出集合，命名约定一漂移
        判据就消失（2026-11-14 审查 P2）。现在按**目录结构**判定：负例目录里有
        `input.png`，且 `expected.error` 是 `PXL006`。
        """
        names = []
        for directory in sorted((CASES / "negative").iterdir()):
            if not directory.is_dir():
                continue
            source = directory / "input.png"
            error = directory / "expected.error"
            if not (source.is_file() and error.is_file()):
                continue
            if error.read_text(encoding="utf-8").strip() != "PXL006":
                continue
            names.append(directory.name)
        self.assertGreaterEqual(len(names), 38, "PNG 拒绝用例数量不足,语料可能被误改")
        for name in names:
            source = CASES / "negative" / name / "input.png"
            with self.subTest(case=name):
                with self.assertRaises(Exception, msg=name) as caught:
                    pngdec.decode(source.read_bytes())
                self.assertEqual("PXL006", getattr(caught.exception, "code", None),
                                 "%s 的拒绝码应为 PXL006" % name)

    def test_accept_set_matches_pixels(self):
        # 先扫出应有的单元集合，再逐个校验；循环里不再 continue，
        # 于是"某个单元悄悄掉队"会被下面的计数断言抓住。
        units = [source for source in sorted(CASES.rglob("input.png"))
                 if (source.parent / "expected.pixels.sha256").is_file()]
        self.assertGreaterEqual(len(units), 600, "接受集数量不足,语料可能被误改")
        accepted = 0
        for source in units:
            expected = source.parent / "expected.pixels.sha256"
            image = pngdec.decode(source.read_bytes())
            label = str(source.parent.relative_to(CASES))
            self.assertEqual(4 * image.width * image.height, len(image.rgba),
                             "RGBA 长度与尺寸不符: " + label)
            self.assertEqual(expected.read_text(encoding="utf-8").strip(),
                             hashlib.sha256(image.rgba).hexdigest(), label)
            accepted += 1
        self.assertEqual(len(units), accepted, "有单元没被检查")


if __name__ == "__main__":
    unittest.main()
