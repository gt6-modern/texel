"""`tools/release_notes.py` 的回归测试：发布页正文必须真的来自 CHANGELOG。

这个脚本只在发布时运行一次，如果它悄悄取错段落（比如把链接定义 `[0.1.0]: url`
一起带进正文，或者取到上一节的尾巴），发布之后才会有人发现——那时版本已经发出去了。
所以这里钉住三件事：段落边界、缺节即失败、以及真实仓库上取出的正文非空。
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "release_notes.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_notes", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## [Unreleased]

### Fixed
- 未发布的修复。

## [1.2.3] - 2026-01-02

### Added
- 新增 A。
- 新增 B。

### Fixed
- 修了 C。

## [1.2.2] - 2025-12-01

### Fixed
- 更早的一版。

[Unreleased]: https://example.invalid/compare/v1.2.3...HEAD
[1.2.3]: https://example.invalid/releases/tag/v1.2.3
[1.2.2]: https://example.invalid/releases/tag/v1.2.2
"""


class ReleaseNotesTest(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_section_stops_at_the_next_heading(self):
        body = self.module.section(CHANGELOG, "1.2.3")
        self.assertEqual(body[0], "")
        self.assertIn("### Added", body)
        self.assertIn("- 新增 B。", body)
        self.assertIn("- 修了 C。", body)
        # 下一节的内容一个字都不能带进来。
        self.assertNotIn("- 更早的一版。", body)

    def test_section_drops_the_link_reference_block(self):
        body = self.module.section(CHANGELOG, "1.2.2")
        self.assertEqual(body, ["", "### Fixed", "- 更早的一版。"])

    def test_section_ignores_the_unreleased_heading(self):
        # `## [Unreleased]` 不能被当成 `## [1.2.3]` 的正文来源。
        body = self.module.section(CHANGELOG, "1.2.3")
        self.assertNotIn("- 未发布的修复。", body)

    def test_missing_version_returns_none(self):
        self.assertIsNone(self.module.section(CHANGELOG, "9.9.9"))

    def test_real_repository_section_is_not_empty(self):
        # 真实仓库上必须能取出当前版本的正文：`check_release_metadata.py` 已要求该节存在,
        # 两者一起保证发布页不会是空的。
        version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        body = self.module.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
                                   version)
        self.assertIsNotNone(body, "CHANGELOG 缺少 " + version + " 小节")
        self.assertTrue([line for line in body if line.strip()],
                        "CHANGELOG 的 " + version + " 小节是空的")

    def test_missing_section_exits_non_zero(self):
        self.assertEqual(self.module.main(["release_notes.py", "9.9.9"]), 1)


if __name__ == "__main__":
    unittest.main()
