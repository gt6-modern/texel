"""测试:发布元数据门禁必须覆盖每一个发布面(审查 #22)。

`tools/check_release_metadata.py` 曾经不读 `texel_compiler.__version__`,于是把所有
检查到的面升到 0.2.0、只留包内 `__version__ = "0.1.0"` 时,门禁仍然打印
`RELEASE_METADATA_OK version=0.2.0`。这条门禁守的是"少一个面也红",所以测试用**合成
目录**跑真门禁,而不是只断言源码里出现过某个正则。

同一个合成目录还承载第二类发布面:**注册表上给人看的那段正文**(`PAGES`)与 Maven 侧的
POM 正文。0.1.0 的三个发布面曾经是 3,185 / 1,164 / 38 字符,版本号全对、资产全齐,三页
却像三个项目,而没有任何门禁读过这些文件。下面每条用例各钉一个判据:页存在、够长、
章节齐全、不含相对链接,以及每个 Gradle 子项目都有自己的 POM name 与够长的 description。

合成目录按本仓库惯例建在 `tests/_tmp/` 下(`tempfile.mkdtemp` 在本机沙箱里建不出可写
目录),每个用例自己清理。
"""

import re
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

def page(sections, minimum):
    """合成一页"合格正文":必备章节齐全,并撑到字符下限。"""
    body = ["# Synthetic release page"]
    per_section = minimum // max(1, len(sections)) + 1
    for section in sections:
        body += ["", "## " + section, "", "x" * per_section]
    return "\n".join(body) + "\n"


#: 合成目录里要写的最小文件集:每一行都是门禁真的会读的东西。
FILES = {
    "VERSION": "0.2.0\n",
    "pyproject.toml": '[project]\nname = "texel-importer"\nversion = "0.2.0"\nreadme = "README.md"\n',
    "compiler/python/pyproject.toml":
        '[project]\nname = "texel-compiler"\nversion = "0.2.0"\nreadme = "README.md"\n',
    "compiler/python/texel_compiler/__init__.py": '__version__ = "0.2.0"\n',
    "compiler/python/texel_compiler/capabilities.py": 'COMPILER_VERSION = "0.2.0"\n',
    "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java":
        'class Capabilities {\n'
        '    static final String TEXT = "  \\"compiler_version\\": \\"0.2.0\\",\\n";\n'
        '}\n',
    "spec/conformance/capabilities/pxl-v1.json":
        '{\n  "compiler_version": "0.2.0"\n}\n',
    "CHANGELOG.md": "# Changelog\n\n## [0.2.0] - 2026-11-21\n\n- 升版。\n",
    # Maven 侧的正文来源:每个 Gradle 子项目都要一行 name 和一段够长的 description。
    # description 按真实文件的样子写成多个字面量 + join:门禁只拼接字面量,不执行 Groovy。
    "compiler/java/settings.gradle":
        "rootProject.name = 'synthetic'\n\ninclude 'core'\ninclude 'cli'\n",
    "compiler/java/build.gradle":
        "ext.pomNames = ['core': 'Texel Core', 'cli': 'Texel CLI']\n"
        "ext.pomDescriptions = [\n"
        "        'core': [\n"
        "                'Deterministic, embeddable PXL compiler for Java 8,',\n"
        "                'renders PXL wire version 1 to RGBA8 pixels and PNG,',\n"
        "                'with no external dependency.',\n"
        "        ].join(' '),\n"
        "        'cli': [\n"
        "                'Command-line compiler for PXL pixel assets,',\n"
        "                'PXL wire version 1 to deterministic PNG on Java 8+,',\n"
        "                'with no external dependency at all.',\n"
        "        ].join(' '),\n"
        "]\n",
}

#: 三个发布页正文由门禁自己的表生成,章节与字符下限只有一处定义,夹具与判据不会各说一套。
#: PyPI 侧的文件名跟着夹具 pyproject 里那句 `readme = "README.md"` 走。
FILES.update((relative, page(sections, minimum))
             for _name, relative, sections, minimum in gate.MAVEN_PAGES)
FILES.update(((Path(project).parent / "README.md").as_posix(), page(sections, minimum))
             for _name, project, sections, minimum in gate.PYPI_PAGES)

#: 夹具从门禁的表反推,好处是不漂,代价是"表本身"没人守:把某一节或下限放宽,18 条用例照旧
#: 全绿。所以期望值在这里**另写一份字面量**——改表就得同时改这里,改不动就是有人真在放水。
EXPECTED_PAGES = {
    "importer page": ("Components", "Five-minute workflow", "Registries", "Guarantees",
                      "Community and security"),
    "python compiler page": ("Components", "Install and use", "Registries", "Guarantees",
                             "Community and security"),
    "java module page": ("Modules and artifacts", "Build", "Registries", "Guarantees",
                         "Community and security"),
}
EXPECTED_FLOOR = 2500
EXPECTED_POM_FLOOR = 120

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

    def test_capability_fixture_is_read(self):
        # fixture 不在 SURFACES 表里（它按 JSON 读），单独钉一次：只改它也必须变红。
        self.override("spec/conformance/capabilities/pxl-v1.json",
                      '{\n  "compiler_version": "0.1.0"\n}\n')
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("capability fixture", str(caught.exception))

    def test_version_itself_is_the_reference(self):
        # `VERSION` 是参照物：把它改掉，其余七处就都不同了，门禁必须红而不是自洽通过。
        self.override("VERSION", "0.1.0\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("release versions differ from 0.1.0", str(caught.exception))

    def test_the_bump_checklist_names_every_file_the_gate_reads(self):
        """`docs/releasing.md` 的升版清单必须点名门禁真正读的每一个文件。

        这条挡的是"清单少写一个面"——#22 的病根就是清单与门禁各说一套。文件清单从
        门禁自己的表里推导，所以新增一个发布面而忘了写文档会立刻变红。
        """
        read = [relative for _name, relative, _pattern in gate.SURFACES]
        read += ["VERSION", "spec/conformance/capabilities/pxl-v1.json"]
        text = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
        missing = [relative for relative in read if relative not in text]
        self.assertEqual([], missing, "docs/releasing.md 的升版清单漏了: " + repr(missing))
        self.assertIn("seven files", text, "清单口径要与实际数量一致")

    def page_of(self, relative):
        return (self.tree / relative).read_text(encoding="utf-8")

    def test_relative_link_in_a_release_page_is_rejected(self):
        # PyPI 不重写相对路径:`../../README.md` 在注册表上一定 404,所以正文里不许出现。
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) + "\n[repository](../../README.md)\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("python compiler page", str(caught.exception))
        self.assertIn("relative links", str(caught.exception))

    def test_release_page_that_shrinks_back_is_rejected(self):
        # `texel-compiler` 那页曾经就是 1,164 字符的子目录 README:够不上下限必须红。
        self.override("compiler/python/README.md",
                      "# Texel compiler for Python\n\n## Install and use\n\nx\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("floor", str(caught.exception))

    def test_missing_release_section_is_rejected(self):
        relative = "compiler/java/README.md"
        self.override(relative, re.sub(r"(?m)^## Registries$", "## Coordinates",
                                       self.page_of(relative)))
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("Registries", str(caught.exception))

    def test_pom_description_floor_is_enforced(self):
        self.override("compiler/java/build.gradle",
                      "ext.pomNames = ['core': 'Texel Core', 'cli': 'Texel CLI']\n"
                      "ext.pomDescriptions = [\n"
                      "        'core': ['Deterministic, embeddable PXL compiler'].join(' '),\n"
                      "        'cli': [\n"
                      "                'Command-line compiler for PXL pixel assets,',\n"
                      "                'PXL wire version 1 to deterministic PNG on Java 8+,',\n"
                      "                'with no external dependency at all.',\n"
                      "        ].join(' '),\n"
                      "]\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("POM description for 'core'", str(caught.exception))

    def test_every_gradle_module_needs_a_pom_entry(self):
        # 新加一个子项目却忘了写 POM name/description,Central 上那一页就是空的。
        self.override("compiler/java/settings.gradle",
                      "rootProject.name = 'synthetic'\n\ninclude 'core'\ninclude 'cli'\n"
                      "include 'extra'\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("extra", str(caught.exception))

    def test_reference_style_link_is_rejected(self):
        # `[repo]: ../../README.md` 不经过 `](url)` 的内联写法,是绕过相对链接检查的现成路子。
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) + "\n[repository]: ../../README.md\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("relative links", str(caught.exception))
        self.assertIn("../../README.md", str(caught.exception))

    def test_angle_bracketed_links_are_normalised(self):
        # Markdown 允许 `[x](<url>)` 与 `[x]: <url>`:包了尖括号的绝对链接必须放行,
        # 包了尖括号的相对链接仍必须拦下。
        relative = "compiler/python/README.md"
        absolute = "<https://github.com/gt6-modern/texel/blob/master/README.md>"
        self.override(relative, self.page_of(relative) + "\n[spec](%s)\n" % absolute)
        self.assertEqual("0.2.0", gate.check(self.tree))

        self.override(relative, self.page_of(relative) + "\n\n[repository]: <../../README.md>\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("../../README.md", str(caught.exception))

    def test_readme_table_form_is_accepted(self):
        # PEP 621 的另一种写法:`readme = {file = "…", content-type = "…"}`,不是字符串。
        self.override("compiler/python/pyproject.toml",
                      '[project]\nname = "texel-compiler"\nversion = "0.2.0"\n'
                      'readme = {file = "README.md", content-type = "text/markdown"}\n')
        self.assertEqual("0.2.0", gate.check(self.tree))

    def test_readme_may_not_escape_the_project(self):
        # 逃出项目目录的 `readme` 不是这个包会发布的那一页,门禁不该跟着它跑。
        self.override("compiler/python/pyproject.toml",
                      '[project]\nname = "texel-compiler"\nversion = "0.2.0"\n'
                      'readme = "../README.md"\n')
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("escapes", str(caught.exception))

    def test_the_tables_themselves_are_nailed_down(self):
        """门禁表与这里的字面量必须一致。

        夹具反向生成保证了"夹具不漂",却让"必备章节集合"和"字符下限"本身没有测试:
        把 `"Registries"` 从表里删掉,所有用例仍绿。这条是那两处的独立钉子。
        """
        observed = dict((name, tuple(sections))
                        for name, _project, sections, _min
                        in list(gate.PYPI_PAGES) + list(gate.MAVEN_PAGES))
        self.assertEqual(EXPECTED_PAGES, observed)
        for name, _project, _sections, minimum in list(gate.PYPI_PAGES) + list(gate.MAVEN_PAGES):
            self.assertEqual(EXPECTED_FLOOR, minimum, name)
        self.assertEqual(EXPECTED_POM_FLOOR, gate.POM_DESCRIPTION_MIN_CHARS)

    def test_inline_relative_link_with_title_is_rejected(self):
        # `[x](../../README.md "repo")` 是合法且常见的 Markdown:正则只要在空格处停下,
        # 这条就会静默放行——而它正是门禁存在的理由。
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) + '\n[r](../../README.md "repo")\n')
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("relative links", str(caught.exception))
        self.assertIn("../../README.md", str(caught.exception))

    def test_fenced_code_is_not_scanned_for_links(self):
        # 示例里出现 Markdown 片段不该让发布页门禁变红:围栏里的内容不是链接。
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) +
                      '\n```json\n{"example": "[r](../../README.md)"}\n```\n')
        self.assertEqual("0.2.0", gate.check(self.tree))

    def test_repository_link_to_a_missing_path_is_rejected(self):
        # 相对链接全改绝对之后,「发布页上的链接 404」换了个形式,不能就此无人守。
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) +
                      "\n[gone](https://github.com/gt6-modern/texel/blob/master/NO_SUCH_FILE.md)\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("do not exist", str(caught.exception))
        self.assertIn("NO_SUCH_FILE.md", str(caught.exception))

    def test_repository_link_to_a_real_path_is_accepted(self):
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) +
                      "\n[repo](https://github.com/gt6-modern/texel/blob/master/README.md)\n")
        self.assertEqual("0.2.0", gate.check(self.tree))

    def test_repository_link_on_another_branch_is_rejected(self):
        relative = "compiler/python/README.md"
        self.override(relative, self.page_of(relative) +
                      "\n[repo](https://github.com/gt6-modern/texel/blob/main/README.md)\n")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("branch other than", str(caught.exception))

    def test_readme_literal_string_is_accepted(self):
        # TOML 的 literal string(`readme = 'README.md'`)也是合法写法,不该被当成没声明。
        self.override("compiler/python/pyproject.toml",
                      "[project]\nname = \"texel-compiler\"\nversion = \"0.2.0\"\n"
                      "readme = 'README.md'\n")
        self.assertEqual("0.2.0", gate.check(self.tree))

    def test_generated_pom_mismatch_is_rejected(self):
        # 真正上网的是 Gradle 生成的那份 XML:key 拼错、`.get()` 返回 null,
        # 静态查 build.gradle 一律看不出来。
        pom = self.tree / "compiler/java/core/build/publications/mavenJava/pom-default.xml"
        pom.parent.mkdir(parents=True, exist_ok=True)
        pom.write_text("<project><name>Texel core</name><description>x</description></project>\n",
                       encoding="utf-8")
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("generated POM name", str(caught.exception))

    def test_generated_poms_can_be_required(self):
        # CI 的 Java 作业先跑生成任务再带这个开关:缺文件必须红,而不是悄悄跳过。
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree, require_generated_poms=True)
        self.assertIn("no generated POM for module", str(caught.exception))

    def test_declared_readme_must_exist(self):
        # 页面文件本身在,但 pyproject 把它指到别处:门禁查的必须是 `readme` 声明的那一份。
        self.override("compiler/python/pyproject.toml",
                      '[project]\nname = "texel-compiler"\nversion = "0.2.0"\n'
                      'readme = "MISSING.md"\n')
        with self.assertRaises(AssertionError) as caught:
            gate.check(self.tree)
        self.assertIn("declares readme", str(caught.exception))
        self.assertIn("MISSING.md", str(caught.exception))

    def test_the_real_repository_passes(self):
        version = gate.check(ROOT)
        self.assertEqual((ROOT / "VERSION").read_text(encoding="ascii").strip(), version)


if __name__ == "__main__":
    unittest.main()
