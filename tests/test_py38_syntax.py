"""Python 3.8 兼容性守卫(AGENTS.md §4.2)。

**本机有 3.8.10**:``py -0p`` 列出 ``-3.8-64``,验收时应当真跑
``py -3.8-64 -m unittest discover -s tests`` —— 那是唯一的执行级证据。

本文件是**另一道**静态守卫,负责挡住"在别人的机器/CI 上才炸"的写法,并明确它覆盖
不到什么:

* ``ast.parse(feature_version=8)`` 能挡下 3.9+ 才有的**语法**(``match``、``except*``
  等);
* 一份禁用 API 清单能挡下最常见的 3.9+ **标准库新增**(``str.removeprefix``、
  ``str.removesuffix``、``tomllib``、``zoneinfo``、``graphlib``),这类调用语法上是
  合法的 3.8 代码,只有运行到这里才会失败——这正是静态守卫要补的那一格。

**覆盖不到**:`list[int]` / `dict[str, int]` 这类"语法合法、3.8 运行时会抛
TypeError"的写法,以及任何新 API 的间接使用。所以本文件通过 ≠ "3.8 已验证";
两者都要,别拿静态守卫替代一次真跑。
"""

import ast
import os
import re
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_PARTS = ("__pycache__", "_sandbox", ".git")

#: 3.9+ 引入、在 3.8 上会 AttributeError / ImportError 的常见入口。
#: 用**正则**而不是子串:`functools.cache` 是 3.8 合法的 `functools.cached_property`
#: 的子串,子串匹配会把它误报成违规。
FORBIDDEN_APIS = (
    r"\.removeprefix\(",
    r"\.removesuffix\(",
    r"\btomllib\b",
    r"\bzoneinfo\b",
    r"\bgraphlib\b",
    r"\bfunctools\.cache\b",
    r"\bitertools\.pairwise\b",
    r"\bmath\.lcm\b",
    r"\bmath\.prod\b",
    r"\bmath\.nextafter\b",
)


#: 真正的 Python 源码根。整仓扫描量是这四个目录的 **300 倍**(0.69 s vs 0.0023 s/次),
#: 因为仓库里还有 3800+ 个语料文件与 samples/。守卫要的是"我们写的 Python",不是
#: "仓库里恰好以 .py 结尾的一切"。
SOURCE_ROOTS = (
    ROOT / "importer",
    ROOT / "tests",
    ROOT / "spec" / "conformance" / "tools",
    ROOT / "compiler" / "java" / "tools",
)


def python_files():
    """四个源码根下的全部 ``.py``,**先按名字剪枝再下降**。

    不能用 ``ROOT.rglob("*.py")``:它在剪枝之前就已经走进目录。仓库里只要出现一个
    junction / 符号链接环(例如 `tests/_sandbox` 下别的会话留下的环),``rglob`` 就会
    无止境地绕进去,而不是安静地跳过。这里自己走,遇到要跳过的名字**不进**,
    并且不跟进符号链接目录。
    """
    for root in SOURCE_ROOTS:
        if root.is_dir():
            for found in _walk_python(root):
                yield found


def _should_skip(name):
    """要整棵跳过的目录/文件名。

    `*_export_tmp` 也在这里:审查者会用 `git archive` 把整份仓库导出到仓库内做
    "干净检出"验证,那些副本里可能有被故意改坏的 `.py`(比如注入 BOM 的变异体)。
    它们不是本仓库的源码,扫进来只会制造假失败——但注意,**跳过的只是目录**,
    `.gitignore` 挡的是"别提交",这里挡的是"别扫描",两件事都要做。
    """
    return name in SKIP_PARTS or name.endswith("_export_tmp")


def _walk_python(directory):
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if _should_skip(entry.name):
            continue
        if entry.name == Path(__file__).name:
            # 本文件里就有那份禁用清单的字面量,扫自己必然误报。
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                for nested in _walk_python(entry):
                    yield nested
            elif entry.suffix == ".py":
                yield entry
        except OSError:
            continue


#: 3.10+ 才接受的**关键字参数**:语法完全合法、静态解析也过,只有 3.8 运行期才
#: TypeError。这道检查是被真事逼出来的——`Path.write_text(..., newline=...)` 曾在
#: 3.12 上全绿、在 3.8/3.9 上炸,靠的就是实跑 3.8 才发现。
FORBIDDEN_KEYWORDS = {
    ("write_text", "newline"): "Path.write_text(newline=...) 需要 3.10+",
    ("read_text", "newline"): "Path.read_text(newline=...) 需要 3.10+",
    ("findall", "flags"): "re.findall 的 flags 关键字是 3.11+ 的写法",
}


def keyword_problems(source, filename="<snippet>"):
    """返回该源码里用到的 3.10+ 关键字参数清单。"""
    problems = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = getattr(function, "attr", None) or getattr(function, "id", None)
        for keyword in node.keywords:
            hint = FORBIDDEN_KEYWORDS.get((name, keyword.arg))
            if hint is not None:
                problems.append("%s:%d: %s" % (filename, node.lineno, hint))
    return problems


class Python38SyntaxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 四条检查都要"路径列表 + 源码文本",扫一次、读一次就够:
        # 原来每条各自遍历整个仓库,四次加起来约 3 秒,且四次结果必然相同。
        cls.files = list(python_files())
        cls.sources = {}
        for path in cls.files:
            try:
                cls.sources[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    def test_every_source_parses_as_python_38(self):
        problems = []
        for path in self.files:
            source = self.sources.get(path, "")
            try:
                # feature_version 取次版本号(3.8 -> 8);3.9 / 3.12 都接受。
                ast.parse(source, filename=str(path), feature_version=8)
            except SyntaxError as error:
                problems.append(
                    "%s:%s: %s" % (path.relative_to(ROOT), error.lineno, error.msg)
                )
        self.assertEqual([], problems, "存在 3.8 下不合法的语法: " + "; ".join(problems))

    def test_no_python_39_plus_stdlib_entries(self):
        problems = []
        patterns = [(pattern, re.compile(pattern)) for pattern in FORBIDDEN_APIS]
        for path in self.files:
            text = self.sources.get(path, "")
            for pattern, compiled in patterns:
                if compiled.search(text):
                    problems.append("%s: %s" % (path.relative_to(ROOT), pattern))
        self.assertEqual([], problems, "用到 3.9+ API: " + "; ".join(problems))

    def test_api_guard_does_not_flag_python38_legal_names(self):
        """`functools.cached_property` 是 3.8 就有的,不许被 `functools.cache` 误伤。"""
        patterns = [re.compile(pattern) for pattern in FORBIDDEN_APIS]
        legal = "from functools import cached_property\n\nx = cached_property\ny = d.cache\n"
        self.assertEqual([], [p.pattern for p in patterns if p.search(legal)])
        illegal = "import functools\n\nok = functools.cache\n"
        self.assertEqual(
            [r"\bfunctools\.cache\b"],
            [p.pattern for p in patterns if p.search(illegal)],
        )

    def test_no_python_310_keyword_arguments(self):
        problems = []
        for path in self.files:
            problems.extend(keyword_problems(self.sources.get(path, ""),
                                             str(path.relative_to(ROOT))))
        self.assertEqual([], problems, "用到 3.10+ 关键字参数: " + "; ".join(problems))

    def test_keyword_guard_bites(self):
        # 给守卫自己喂一段已知会炸的写法,证明它不是空转。
        snippet = "import pathlib\npathlib.Path('x').write_text('a', newline='\\n')\n"
        self.assertEqual(1, len(keyword_problems(snippet)))
        self.assertEqual([], keyword_problems("import pathlib\npathlib.Path('x').read_text()\n"))

    def test_walker_skips_export_tmp_directories(self):
        """`*_export_tmp/` 里的 `.py` 不是本仓库源码,不许被扫进来。

        没有这条时真的踩过:审查者把仓库导出成 `_head_export_tmp/`,里面有一份被
        注入 BOM 的变异体,3.8 语法守卫把它当源码,全量测试直接红。

        注意这里用 `open(..., newline=...)` 而不是 `Path.write_text(newline=...)`
        ——后者的 `newline` 参数是 3.10 才有的。**本守卫文件自己被排除在扫描之外**
        (它含禁用清单的字面量),所以写在它里面的 3.9+/3.10+ 写法只有真跑 3.8 才抓得住。
        """
        root = ROOT / "tests" / "_sandbox" / ("walk-%d" % os.getpid())
        shutil.rmtree(str(root), ignore_errors=True)
        try:
            (root / "keep").mkdir(parents=True)
            with open(str(root / "keep" / "good.py"), "w", encoding="utf-8", newline="\n") as handle:
                handle.write("x = 1\n")
            (root / "some_export_tmp").mkdir()
            (root / "some_export_tmp" / "bad.py").write_bytes(b"\xef\xbb\xbfy = 2\n")
            found = sorted(path.name for path in _walk_python(root))
        finally:
            shutil.rmtree(str(root), ignore_errors=True)
        self.assertEqual(["good.py"], found)

    def test_guard_actually_scans_something(self):
        """防止这个守卫因为路径写错而"空集合通过"。

        这条还会在一种**真实**情况下报警:`python_files()` 会跳过路径里带
        `_sandbox` 的文件,所以若把仓库检出/复制到形如 `.../tests/_sandbox/x`
        的目录里,扫描集合会整个变空,本测试随即失败。那是设计如此(tripwire),
        不是仓库有问题——换个检出路径即可。
        """
        found = [path.name for path in self.files]
        self.assertIn("extract.py", found)
        self.assertIn("png.py", found)
        self.assertIn("test_conformance.py", found)


if __name__ == "__main__":
    unittest.main()
