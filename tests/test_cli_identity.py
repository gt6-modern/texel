"""三个 CLI 的"自述"必须一致:帮助版式 + 每条 stderr 的前缀(2026-11-19 统一)。

交付物就是"统一"本身,所以断言不能只写"打印了点什么",也不该只测一个工具:

* 帮助文本:``usage:`` 首行 + 7 空格对齐的多行 synopsis、缩进选项表(名字字段宽
  19 列,说明列从第 22 列起)、最后一段中文说明;程序名一律是真实命令名,不出现
  ``python -m ...``。Java 的 ``usage()`` 直接从源码里抽出来与 Python 版逐字节比对。
* 诊断前缀:每条 stderr 都以真实命令名开头(``texel-compiler`` /
  ``texel-compiler-python`` / ``texel-import``)。三个实现各自只有**一个**出口:
  importer 只剩 ``_note()`` 里那一处 ``sys.stderr.write``,Python 编译器的每一处
  写都带 ``PROGRAM``,Java 的每一处 ``ERR.println`` 都带程序名。数量对不上就是
  有人绕过统一出口新写了一条。
"""

import contextlib
import io
import re
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))
sys.path.insert(0, str(REPO))

from importer import __main__ as importer_main  # noqa: E402
from texel_compiler import cli as compiler_cli  # noqa: E402

JAVA_MAIN = (REPO / "compiler" / "java" / "cli" / "src" / "main" / "java"
             / "io" / "github" / "texel" / "cli" / "Main.java")
COMPILER_CLI = REPO / "compiler" / "python" / "texel_compiler" / "cli.py"
COMPILER_MAIN = REPO / "compiler" / "python" / "texel_compiler" / "__main__.py"
IMPORTER_MAIN = REPO / "importer" / "__main__.py"

#: 名字字段宽度(从第 3 列起算)。
LABEL_WIDTH = 19
#: 说明列,0 起算:2 空格缩进 + 19 列名字段。
DESCRIPTION_INDEX = 2 + LABEL_WIDTH


class _Stream:
    """同时接住两种写法:importer 走 ``sys.stdout.write``,编译器走 ``sys.stdout.buffer.write``。"""

    def __init__(self):
        self.buffer = self
        self._chunks = []

    def write(self, chunk):
        if isinstance(chunk, bytes):
            self._chunks.append(chunk)
        else:
            self._chunks.append(chunk.encode("utf-8"))
        return len(chunk)

    def flush(self):
        pass

    def text(self):
        return b"".join(self._chunks).decode("utf-8")


def _capture_stdout(run):
    stream = _Stream()
    with contextlib.redirect_stdout(stream):
        code = run()
    return code, stream.text()


def _capture_stderr(run):
    stream = io.StringIO()
    with contextlib.redirect_stderr(stream):
        code = run()
    return code, stream.getvalue()


def _compiler_usage():
    return _capture_stdout(lambda: compiler_cli.main(["--help"]))[1]


def _importer_usage():
    return _capture_stdout(lambda: importer_main.main(["--help"]))[1]


def _java_usage():
    """从源码里抽出 Java ``usage()`` 返回的字符串,拼成完整文本。"""
    source = JAVA_MAIN.read_text(encoding="utf-8")
    start = source.index("static String usage()")
    end = source.index("\n    }", start)
    body = source[start:end]
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    return "".join(literal.replace("\\n", "\n") for literal in literals)


def _neutralize(program, text):
    """把程序名抽掉,好让三份帮助文本逐字节互比。"""
    return text.replace(program, "<prog>")


def _count(text, needle):
    return text.count(needle)


class HelpTextFormatTest(unittest.TestCase):
    def _check_layout(self, program, text):
        """按统一版式拆帮助,返回 (synopsis 行数, 选项名字列表)。"""
        self.assertTrue(text.endswith("\n"), "帮助文本必须以换行结束:%r" % text[-20:])
        self.assertFalse(text.endswith("\n\n"), "帮助文本不该以空行结束")
        lines = text[:-1].split("\n")

        self.assertTrue(lines[0].startswith("usage: " + program + " "),
                        "首行不是 usage: %s:%r" % (program, lines[0]))

        index = 1
        synopsis = 1
        while index < len(lines) and lines[index].strip():
            self.assertTrue(
                lines[index].startswith("       " + program + " "),
                "synopsis 续行必须用 7 空格对齐并带真实命令名:%r" % lines[index])
            synopsis += 1
            index += 1
        self.assertEqual("", lines[index], "synopsis 与选项表之间必须有一个空行")

        index += 1
        labels = []
        while index < len(lines) and lines[index].strip():
            row = lines[index]
            self.assertTrue(row.startswith("  ") and not row.startswith("   "),
                            "选项行必须从第 3 列起:%r" % row)
            label = row[2:DESCRIPTION_INDEX]
            self.assertTrue(label.strip(), "选项行没有名字:%r" % row)
            self.assertNotIn("  ", label.strip(),
                             "名字字段内部夹了空格(说明列会跟着偏):%r" % row)
            self.assertNotEqual(" ", row[DESCRIPTION_INDEX],
                                "说明没有从第 %d 列起:%r"
                                % (DESCRIPTION_INDEX + 1, row))
            labels.append(label.rstrip())
            index += 1
        self.assertTrue(labels, "帮助里没有选项表")
        self.assertEqual("", lines[index], "选项表与尾部说明之间必须有一个空行")

        notes = lines[index + 1:]
        self.assertTrue(notes, "帮助里没有尾部说明")
        for note in notes:
            self.assertTrue(note.strip(), "尾部说明不该夹空行")
            self.assertFalse(note.startswith(" "), "尾部说明不该缩进:%r" % note)
        return synopsis, labels

    def test_compiler_python_help(self):
        code, text = _capture_stdout(lambda: compiler_cli.main(["--help"]))
        self.assertEqual(0, code)
        synopsis, labels = self._check_layout("texel-compiler-python", text)
        self.assertEqual(4, synopsis, "capabilities / compile / check / 批量 各占一行 synopsis")
        self.assertEqual(
            ["ENTRY", "--asset-root ROOT", "--bind name=value", "--deps FILE",
             "-o, --out OUT", "PATH", "--", "--json"],
            labels)
        self.assertNotIn("python -m", text, "帮助里不该出现 python -m:程序名要用真实命令名")

    def test_importer_help(self):
        code, text = _capture_stdout(lambda: importer_main.main(["--help"]))
        self.assertEqual(0, code)
        _synopsis, labels = self._check_layout("texel-import", text)
        self.assertEqual(["PATH", "--raw", "-o, --out OUT"], labels)
        self.assertNotIn("python -m", text, "帮助里不该出现 python -m:程序名要用真实命令名")
        # 这个 CLI 不支持 `--` 转义,帮助里就不许列出来(列了等于承诺一个没实现的入口)。
        self.assertNotIn("--", labels)

    def test_java_usage_matches_python_compiler(self):
        """Java 与 Python 编译器的帮助只允许程序名不同。

        两份文本分居两个语言、两套构建,靠人肉对齐必然漂开;这里直接从 Java 源码
        抽出字符串比对,漂了就红。
        """
        self.assertEqual(
            _neutralize("texel-compiler-python", _compiler_usage()),
            _neutralize("texel-compiler", _java_usage()),
            "Java 与 Python 编译器的帮助文本已漂开")


class DiagnosticPrefixTest(unittest.TestCase):
    """每条 stderr 都要自报家门,而且三个实现只有一条出口。"""

    def _assert_prefixed(self, program, stderr):
        lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertTrue(lines, "这条用例本该报错,stderr 却是空的")
        for line in lines:
            self.assertTrue(line.startswith(program + ": "),
                            "stderr 行没有以 %s: 开头:%r" % (program, line))

    def test_compiler_python_errors_are_prefixed(self):
        code, stderr = _capture_stderr(lambda: compiler_cli.main(["compile", "entry.pxl"]))
        self.assertEqual(2, code)
        self._assert_prefixed("texel-compiler-python", stderr)

    def test_compiler_python_batch_errors_are_prefixed(self):
        """批量模式的每条失败都带前缀——这正是"日志混流"最容易露馅的地方。"""
        code, stderr = _capture_stderr(
            lambda: compiler_cli.main(["_no_such_input_.pxl"]))
        self.assertEqual(2, code)
        self._assert_prefixed("texel-compiler-python", stderr)

    def test_importer_errors_are_prefixed(self):
        code, stderr = _capture_stderr(lambda: importer_main.main(["--nope"]))
        self.assertEqual(2, code)
        self._assert_prefixed("texel-import", stderr)

    def test_importer_per_file_errors_are_prefixed(self):
        """逐文件的失败行也要带前缀:它们是批量输出里行数最多的一类。"""
        code, stderr = _capture_stderr(
            lambda: importer_main.main(["_no_such_input_.png"]))
        self.assertEqual(1, code)
        self._assert_prefixed("texel-import", stderr)

    def test_compiler_python_escapes_unicode_line_separators(self):
        """Windows 允许文件名里带 U+2028；原样写进 stderr 会把一条诊断拆成 4 行，
        而后续行没有命令名前缀。Java 侧早有 escape()，Python 侧现在也是同一套转义。"""
        directory = REPO / "tests" / "_tmp" / "identity-escape"
        shutil.rmtree(str(directory), ignore_errors=True)
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(directory), True)
        target = directory / ("bad" + chr(0x2028) + "name.pxl")
        target.write_bytes(b"{ not json }")
        code, stderr = _capture_stderr(lambda: compiler_cli.main([str(target)]))
        self.assertEqual(1, code)
        self.assertEqual(1, len(stderr.splitlines()),
                         "一条诊断只能占一行，实际 %d 行:%r" % (len(stderr.splitlines()), stderr))
        self.assertTrue(stderr.startswith("texel-compiler-python: "),
                        "整条诊断都必须带命令名前缀:%r" % stderr[:40])
        self.assertNotIn(chr(0x2028), stderr, "U+2028 必须被转义")
        self.assertIn("\\u2028", stderr)

    def test_compiler_python_has_a_single_stderr_exit(self):
        """编译器与 importer 一样只有一个 stderr 出口，转义才不会漏掉某一条。"""
        source = COMPILER_CLI.read_text(encoding="utf-8")
        self.assertEqual(1, _count(source, "sys.stderr.write("),
                         "cli.py 里出现了绕过 _note() 的 stderr 直写")
        self.assertEqual(0, _count(COMPILER_MAIN.read_text(encoding="utf-8"), "sys.stderr.write("),
                         "__main__.py 必须走 cli._note()")

    def test_importer_has_a_single_stderr_exit(self):
        """importer 的全部 stderr 都走 ``_note()``,所以源码里只应有**一处**直写。"""
        source = IMPORTER_MAIN.read_text(encoding="utf-8")
        self.assertEqual(1, _count(source, "sys.stderr.write("),
                         "importer 里出现了绕过 _note() 的 stderr 直写")
        self.assertEqual(1, _count(source, 'PROGRAM = "texel-import"'))

    def test_compiler_python_every_write_carries_program(self):
        for path in (COMPILER_CLI, COMPILER_MAIN):
            source = path.read_text(encoding="utf-8")
            writes = _count(source, "sys.stderr.write(")
            prefixed = _count(source, 'PROGRAM + ": ')
            self.assertEqual(writes, prefixed,
                             "%s: %d 处 stderr 写里只有 %d 处带 PROGRAM 前缀"
                             % (path.name, writes, prefixed))
            self.assertNotIn('"texel: ', source, "%s 还有旧的 texel: 前缀" % path.name)

    def test_java_check_publishes_the_manifest_after_encoding(self):
        """`check --deps FILE` 的清单必须在 PNG 编码**之后**才发布。

        Java 侧没有可注入的运行时缝隙（本机实测 2048x2048 + 小堆时 OOM 落在清单写入之前，
        分不出两种顺序），所以这里用源码顺序钉住：编码先、清单后。把写清单挪到编码之前，
        这条会红。
        """
        source = JAVA_MAIN.read_text(encoding="utf-8")
        # 只看 compile/check 这一条路径：旧式批量入口里也有一次 PngEncoder.encode，
        # 从文件头 index() 会抓到那一次，断言就变成永真（实测：把写清单挪到编码之前它照样绿）。
        check_branch = source.index('            if (check) {\n                if (deps != null && !"-".equals(deps)) {')
        encode_at = source.rindex("PngEncoder.encode(compiled.width(),", 0, check_branch)
        manifest_at = source.index("writeDependencies(deps, compiled.dependencies())")
        self.assertLess(encode_at, manifest_at,
                        "check 的依赖清单必须在 PNG 编码之后才发布（失败时不得留下新清单）")

    def test_java_every_println_carries_program(self):
        source = JAVA_MAIN.read_text(encoding="utf-8")
        self.assertEqual(_count(source, "ERR.println("), _count(source, "texel-compiler: "),
                         "Main.java 里出现了不带程序名的 ERR.println")
        self.assertNotIn('"texel: ', source, "Main.java 还有旧的 texel: 前缀")

    def test_no_legacy_prefixes_survive_anywhere(self):
        for path in (COMPILER_CLI, COMPILER_MAIN, IMPORTER_MAIN, JAVA_MAIN):
            source = path.read_text(encoding="utf-8")
            for stale in ('"importer: ', '"texel: '):
                self.assertNotIn(stale, source,
                                 "%s 还在用旧前缀 %r" % (path.name, stale))


class BatchDiagnosticParityTest(unittest.TestCase):
    """批量入口的"输出覆盖编译依赖"诊断在两个实现里必须同形。

    这条判据是**文本级**的:Java 侧只能读源码(跑它要 jar),所以断言两边都带着同一句
    汇总、同一条冲突消息形状、同一个"码在最前"的写法。任何一侧改了措辞或把码挪走,
    这里就会红——下游按 `CLI00x` 前缀分流,措辞分叉等于契约分叉。
    """

    PYTHON = COMPILER_CLI.read_text(encoding="utf-8")
    JAVA = JAVA_MAIN.read_text(encoding="utf-8")

    #: 两个实现共用的字面片段。
    SUMMARY = "处输出覆盖编译依赖，整批未写入任何文件"
    CLAIM = " 会覆盖 "
    DEPENDENCY = " 的编译依赖 "
    NOT_WRITTEN = "（未写入）"

    def test_both_emit_the_same_summary_sentence(self):
        for name, text in (("Python", self.PYTHON), ("Java", self.JAVA)):
            self.assertIn(self.SUMMARY, text, name + " 少了批量冲突的汇总句")

    def test_both_spell_the_conflict_the_same_way(self):
        for name, text in (("Python", self.PYTHON), ("Java", self.JAVA)):
            self.assertIn(self.CLAIM, text, name)
            self.assertIn(self.DEPENDENCY, text, name)
            self.assertIn(self.NOT_WRITTEN, text, name)

    def test_the_code_comes_first_on_both_sides(self):
        self.assertIn('"CLI002: " + str(source) + ": 输出 "', self.PYTHON,
                      "Python 批量冲突必须把 CLI002 放在最前")
        self.assertIn('ERR.println("texel-compiler: CLI002: " + conflict)', self.JAVA,
                      "Java 批量冲突必须把 CLI002 放在最前")


if __name__ == "__main__":
    unittest.main()
