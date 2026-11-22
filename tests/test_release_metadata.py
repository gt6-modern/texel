"""测试:发布元数据门禁必须覆盖每一个发布面(审查 #22)。

`tools/check_release_metadata.py` 曾经不读 `texel_compiler.__version__`,于是把所有
检查到的面升到 0.2.0、只留包内 `__version__ = "0.1.0"` 时,门禁仍然打印
`RELEASE_METADATA_OK version=0.2.0`。这条门禁守的是"少一个面也红",所以测试用**合成
目录**跑真门禁,而不是只断言源码里出现过某个正则。

合成目录按本仓库惯例建在 `tests/_tmp/` 下(`tempfile.mkdtemp` 在本机沙箱里建不出可写
目录),每个用例自己清理。
"""

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
WORK = ROOT / "tests" / "_tmp" / "release-metadata"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_release_metadata as gate

#: 合成目录里要写的最小文件集:每一行都是门禁真的会读的东西。
FILES = {
    "VERSION": "0.2.0\n",
    "pyproject.toml": '[project]\nname = "texel-importer"\nversion = "0.2.0"\n',
    "compiler/python/pyproject.toml": '[project]\nname = "texel-compiler"\nversion = "0.2.0"\n',
    "compiler/python/texel_compiler/__init__.py": '__version__ = "0.2.0"\n',
    "compiler/python/texel_compiler/capabilities.py": 'COMPILER_VERSION = "0.2.0"\n',
    "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java":
        'class Capabilities {\n'
        '    static final String TEXT = "  \\"compiler_version\\": \\"0.2.0\\",\\n";\n'
        '}\n',
    "spec/conformance/capabilities/pxl-v1.json":
        '{\n  "compiler_version": "0.2.0"\n}\n',
    "CHANGELOG.md": "# Changelog\n\n## [0.2.0] - 2026-11-21\n\n- 升版。\n",
}

#: 每个发布面的"陈旧版本"写法:改这里就能让门禁点名那个面。
STALE = {
    "pyproject.toml": '[project]\nversion = "0.1.0"\n',
    "compiler/python/pyproject.toml": '[project]\nversion = "0.1.0"\n',
    "compiler/python/texel_compiler/__init__.py": '__version__ = "0.1.0"\n',
    "compiler/python/texel_compiler/capabilities.py": 'COMPILER_VERSION = "0.1.0"\n',
    "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java":
        'class Capabilities {\n'
        '    static final String TEXT = "  \\"compiler_version\\": \\"0.1.0\\",\\n";\n'
        '}\n',
}


class ReleaseMetadataGateTest(unittest.TestCase):

    def setUp(self):
        self.tree = WORK / (self._testMethodName.replace("test_", "") or "case")
        shutil.rmtree(str(self.tree), ignore_errors=True)
        for relative, text in FILES.items():
            path = self.tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(str(self.tree), ignore_errors=True)

    def override(self, relative, text):
        (self.tree / relative).write_text(text, encoding="utf-8")

    def test_consistent_tree_passes(self):
        self.assertEqual("0.2.0", gate.check(self.tree))

    def test_each_surface_is_actually_read(self):
        # 逐个发布面单独改坏:门禁必须变红,而且报错要点名那个面。
        for name, relative, _pattern in gate.SURFACES:
            with self.subTest(surface=name):
                self.setUp()
                self.override(relative, STALE[relative])
                with self.assertRaises(AssertionError) as caught:
                    gate.check(self.tree)
                self.assertIn(name, str(caught.exception))

    def test_stale_package_version_is_rejected(self):
        # 审查 #22 的原始复现:除 `__version__` 外全部已升到 0.2.0。
        self.override("compiler/python/texel_compiler/__init__.py",
                      '__version__ = "0.1.0"\n')
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("python package __version__", str(caught.exception))

    def test_missing_changelog_section_is_rejected(self):
        self.override("CHANGELOG.md", "# Changelog\n")
        with self.assertRaises(AssertionError):
            gate.check(self.tree)

    def test_the_real_repository_passes(self):
        version = gate.check(ROOT)
        self.assertEqual((ROOT / "VERSION").read_text(encoding="ascii").strip(), version)


if __name__ == "__main__":
    unittest.main()
