"""打包配置、引用路径与 Markdown 链接的可判定门禁。

为什么要有这个文件：阶段 0 的重命名把 `pyproject.toml` 与 `README.md` 做了全文件
单字符替换（`c`→`d`、`i`→`m`、`t`→`e`），包元数据整体消失、README 的
每条链接都指向不存在的路径，而当时 243 项单测、11,234 条语料断言、Java 四道门禁
**全部是绿的**——因为没有任何检查读过打包配置或文档链接。同一个提交还把
`spec/conformance/tools/png_write.py` 的注释改成指向一个不存在的计划文档。

三类判据各自对着一种真实事故：
  1. 打包元数据必须存在且与实际目录一致（PackagingTest）；
  2. 代码/构建脚本注释里引用的仓库内路径必须存在（ReferencedPathsTest）；
  3. 除 docs/ 之外，Markdown 里的相对链接必须解析得到（MarkdownLinksTest）——
     README 单字符污染正是被这一条咬住的（`[...](sped/PXL.md)`）。

docs/ 下是历史记录、参考实现说明与产品需求，可能引用尚未创建的产物（例如
下一版本的规范文件）；spec/conformance/cases/ 下是 800 多份语料 notes，属索引驱动的另一类
判据，正文里出现 Markdown 链接只是说明文字。两类都按目录排除，而不是逐文件列白名单。

本文件刻意不依赖 3.11+ 才有的 TOML 解析器（AGENTS.md §4.2 要求 3.8+），只做纯文本断言。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class PackagingTest(unittest.TestCase):
    """`pyproject.toml` 必须真的描述一个可安装的包，且与实际目录一致。"""

    def setUp(self):
        self.text = read("pyproject.toml")

    def test_required_tables_present(self):
        for table in ("[build-system]", "[project]", "[project.scripts]", "[tool.setuptools]"):
            self.assertIn(table, self.text)

    def test_metadata_minimum(self):
        self.assertRegex(self.text, r'(?m)^name = "texel-importer"$')
        self.assertRegex(self.text, r'(?m)^requires-python = ">=3\.8"$')
        #: 零运行时依赖是硬约束（AGENTS.md §4.2）。
        self.assertRegex(self.text, r'(?m)^dependencies = \[\]$')

    def test_declared_packages_exist(self):
        match = re.search(r"(?ms)^packages = \[(.*?)\]", self.text)
        self.assertIsNotNone(match, "找不到 packages 声明")
        names = re.findall(r'"([^"]+)"', match.group(1))
        self.assertTrue(names, "packages 为空")
        for name in names:
            self.assertTrue((ROOT / name / "__init__.py").is_file(),
                            "packages 声明的目录缺少 __init__.py: " + name)

    def test_console_script_name(self):
        # 程序名是对外契约（PRD §19 统一 CLI 文案）：改名必须显式改这一行，
        # 不能因为"目标模块仍然有效"就悄悄退化成一个没人记得的名字。
        self.assertRegex(self.text, r'(?m)^texel-import = "importer\.__main__:main"$')

    def test_console_script_target_exists(self):
        entries = re.findall(r'(?m)^([\w-]+) = "([\w.]+):(\w+)"$', self.text)
        self.assertTrue(entries, "没有 [project.scripts] 条目")
        for name, module, function in entries:
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(path.is_file(), "script " + name + " 指向不存在的模块 " + module)
            self.assertIn("def " + function, path.read_text(encoding="utf-8"),
                          "script " + name + " 指向的函数 " + function + " 不在 " + module)


class _Scanned(object):
    """扫面包：代码与构建脚本（含 markdown），排除 docs/ 与产物目录。"""

    #: 含 `*.yml` / `*.yaml`：CI workflow 里写着 spec/conformance/tools/*.py 的入口路径，
    #: 那些文件名一改就要有人变红——否则"扫 .github"只是名义上的。
    PATTERNS = ("*.py", "*.java", "*.gradle", "*.ps1", "*.md", "*.yml", "*.yaml")

    @staticmethod
    def iterate():
        for pattern in _Scanned.PATTERNS:
            for path in ROOT.rglob(pattern):
                if not path.is_file():
                    continue
                rel = path.relative_to(ROOT).as_posix()
                # 只排除明确的"非本仓库产物/语料"目录。不能写 startswith(".")：
                # 那会把 .github/ 一起挡掉，而 CI workflow 里的路径引用恰恰要查。
                if rel.startswith(("docs/", "tests/_sandbox/", "spec/conformance/cases/",
                                   ".git/", ".gradle-home/", ".review-",
                                   ".venv/", ".idea/", ".vscode/")):
                    continue
                if "/build/" in "/" + rel:
                    continue
                yield path, rel


class ReferencedPathsTest(unittest.TestCase):
    """代码/构建脚本注释里引用的仓库内文件必须真的存在。

    前缀白名单覆盖仓库的全部顶层目录；只认带扩展名的令牌——语料路径
    （如 `spec/conformance/cases/<case>`）没有扩展名，属索引驱动的另一类判据。
    """

    TOKEN = re.compile(
        r"(?<![\w/])((?:docs|spec|importer|tests|compiler|samples|\.github)"
        r"/[A-Za-z0-9_./-]*\.[A-Za-z0-9]+)")

    def test_referenced_paths_exist(self):
        missing = []
        for path, rel in _Scanned.iterate():
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                for token in self.TOKEN.findall(line):
                    token = token.rstrip(".")
                    if not (ROOT / token).exists():
                        missing.append(rel + ":" + str(number) + " -> " + token)
        self.assertEqual([], missing, "引用了不存在的路径:\n" + "\n".join(missing[:20]))


class MarkdownLinksTest(unittest.TestCase):
    """docs/ 之外的 Markdown 相对链接必须解析得到。"""

    LINK = re.compile(r"\]\(([^)\s]+)\)")

    def test_relative_links_resolve(self):
        broken = []
        for path, rel in _Scanned.iterate():
            if path.suffix != ".md":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                for target in self.LINK.findall(line):
                    if target.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    cleaned = target.split("#")[0].strip()
                    if not cleaned or cleaned.startswith("<"):
                        continue
                    if not (path.parent / cleaned).resolve().exists():
                        broken.append(rel + ":" + str(number) + " -> " + target)
        self.assertEqual([], broken, "Markdown 链接指向不存在的目标:\n" + "\n".join(broken[:20]))


class CodeDomainTest(unittest.TestCase):
    """两侧的错误码域必须一致：Java 的 Codes.ALL 与 Python 的 ALL_CODES。

    规范 §11.2 的码空间是**两个实现共享**的契约。任一侧增删码而在另一侧不同步，
    现有门禁都不会红（各自只对着自己那份表）。这条断言是唯一的交叉检查点。
    """

    JAVA = "compiler/java/core/src/main/java/io/github/texel/core/Codes.java"
    PYTHON = "importer/errors.py"

    @staticmethod
    def extract(text, pattern):
        match = re.search(pattern, text, re.S | re.M)
        if match is None:
            raise AssertionError("找不到码域声明: " + pattern)
        return set(re.findall(r"PXL\d{3}", match.group(0)))

    def test_code_space_matches_across_implementations(self):
        java = self.extract(read(self.JAVA), r"static final String\[\] ALL = \{[^}]*\}")
        py = self.extract(read(self.PYTHON), r"^ALL_CODES = \([^)]*\)")
        self.assertEqual(sorted(java), sorted(py),
                         "两侧错误码域不一致（Java Codes.ALL vs Python ALL_CODES）")
        self.assertEqual(["PXL001", "PXL002", "PXL003", "PXL004", "PXL005", "PXL006"],
                         sorted(java), "码域应与规范 §11.2 一致")


class IgnoreHygieneTest(unittest.TestCase):
    """没有任何 tracked 文件被 .gitignore 吃掉。

    @@build/@@、@@*.tmp@@ 这类按名字生效的规则一旦过宽，就会静默吞掉合法源码或语料。
    @@git ls-files -i -c --exclude-standard@@ 是这件事的唯一权威判定。
    """

    def test_no_tracked_file_is_ignored(self):
        import subprocess

        try:
            result = subprocess.run(["git", "ls-files", "-i", "-c", "--exclude-standard"],
                                    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        except (OSError, ValueError):
            self.skipTest("没有可用的 git")
        if result.returncode != 0:
            self.skipTest("git 不可用（" + str(result.returncode) + "）")
        swallowed = [line for line in result.stdout.split("\n") if line.strip()]
        self.assertEqual([], swallowed, "以下 tracked 文件被 .gitignore 吞掉了:\n" + "\n".join(swallowed[:20]))


    def test_repo_root_has_no_stray_binaries(self):
        """仓库根目录不得出现散落的二进制产物。

        事故来源（CodeX F-R7-05 / F-R8-02）：探针把 ab.png 与 a<ZWSP>b.png 写到了仓库根，
        又被 git add -A 扫进版本库。根目录本来只有配置与说明文件，出现 *.png 一定是误提交。
        """
        root = ROOT
        stray = sorted(entry.name.encode("unicode_escape").decode()
                       for entry in root.iterdir()
                       if entry.is_file() and entry.suffix in (".png", ".jpg", ".class"))
        self.assertEqual([], stray, "仓库根目录出现散落二进制文件: %r" % (stray,))


if __name__ == "__main__":
    unittest.main()
