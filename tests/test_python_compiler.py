"""compiler/python(第二个 PXL compiler)的门禁。

本文件只守四件事,而且每件都必须可执行,不靠"看起来对":

1. 能力档案符合 PRD §6.3 的 schema(键集合、类型、profile 与语言版本、错误码范围);
2. 一次最小 PXL 的编译能端到端跑通(API 与 CLI 两条路),像素由本文件独立算出来;
3. profiles.tsv 只选择当前 pxl-v1 语料;
4. 源码里不得出现 "import importer" / "pxl_render_ref" / "v1_render_ref" /
   "spec/conformance/tools" —— 阶段 6 要的是独立实现,这条是它的机械判据。

另加两条静态守卫:3.8 语法可解析(ast.parse(feature_version=8)),以及 3.8 没有的
标准库入口不被调用(只查最容易被顺手写下的那几个)。
"""

import ast
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import unittest
import zlib
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / "compiler" / "python"
TOOLS = COMPILER / "tools"

if str(COMPILER) not in sys.path:
    sys.path.insert(0, str(COMPILER))

from texel_compiler import PxlError, capabilities_json, compile_target, encode_png, render_document
from texel_compiler import pngdec, pxlcompile
from texel_compiler.resolver import MemoryResolver, build_memory_resolver

#: PRD §6.3:schema version 1 的最小且完整键集合,不允许额外键。
ALLOWED_KEYS = {
    "capabilities_version",
    "compiler_id",
    "compiler_version",
    "conformance_profile",
    "language_versions",
    "error_code_ranges",
    "png_input_profile",
}

#: PRD §6.3 固定的档位映射。
FIXED_PROFILES = {
    "pxl-v1": [1],
}

FIXED_RANGES = {1: ("PXL001", "PXL012")}

#: 临时工作区放在仓库内:tests/_sandbox/ 已被 .gitignore 挡住,而系统 temp 在本机沙箱下
#: 删不掉(与 tests/test_cli.py 的做法一致)。
SANDBOX = ROOT / "tests" / "_sandbox"


_WORKSPACE_COUNTER = [0]


@contextmanager
def workspace():
    """仓库内的临时工作区。目录名手工拼:本机沙箱下 tempfile.mkdtemp 造出来的目录写不进去。"""
    SANDBOX.mkdir(parents=True, exist_ok=True)
    _WORKSPACE_COUNTER[0] += 1
    path = SANDBOX / ("python-compiler-" + str(os.getpid()) + "-"
                      + str(_WORKSPACE_COUNTER[0]))
    if path.exists():
        shutil.rmtree(str(path), ignore_errors=True)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(str(path), ignore_errors=True)


FORBIDDEN_IN_SOURCE = (
    "import importer",
    "from importer",
    "pxl_render_ref",
    "v1_render_ref",
    "png_ref",
    "spec/conformance/tools",
)


def python_sources():
    skip = ("__pycache__", "_sandbox")
    for path in sorted(COMPILER.rglob("*.py")):
        if any(part in skip for part in path.parts):
            continue
        yield path


def tree_snapshot(directory):
    """目录树的逐项快照（相对路径，目录以 / 结尾）：任何新增/删除都会让两份快照不相等。"""
    return sorted(str(path.relative_to(directory)).replace("\\", "/")
                  + ("/" if path.is_dir() else "")
                  for path in directory.rglob("*"))


def run_cli(args, cwd=COMPILER, extra_env=None):
    environment = None
    if extra_env is not None:
        environment = dict(os.environ)
        environment.update(extra_env)
    process = subprocess.run([sys.executable, "-m", "texel_compiler"] + args,
                             cwd=str(cwd), env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process.returncode, process.stdout, process.stderr


class CapabilitiesTest(unittest.TestCase):

    def caps(self):
        code, out, err = run_cli(["capabilities", "--json"])
        self.assertEqual(0, code, err.decode("utf-8", "replace"))
        return out

    def test_bytes_contract(self):
        raw = self.caps()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "能力档案不得带 BOM")
        self.assertTrue(raw.endswith(b"\n"), "能力档案必须以 LF 结尾")
        self.assertFalse(raw.endswith(b"\n\n"), "只允许一个结尾 LF")
        self.assertEqual(raw, capabilities_json().encode("utf-8"),
                         "CLI 输出与库内档案必须逐字节相同")
        self.assertEqual(1, raw.decode("utf-8").count('"capabilities_version"'))

    def test_schema(self):
        data = json.loads(self.caps().decode("utf-8"))
        self.assertEqual(ALLOWED_KEYS, set(data), "键集合与 PRD §6.3 不一致")
        self.assertEqual(1, data["capabilities_version"])
        self.assertIsInstance(data["capabilities_version"], int)
        for key in ("compiler_id", "compiler_version", "conformance_profile",
                    "png_input_profile"):
            self.assertIsInstance(data[key], str)
            self.assertTrue(data[key].strip(), key + " 不能为空")
        versions = data["language_versions"]
        self.assertIsInstance(versions, list)
        self.assertEqual(sorted(set(versions)), versions, "language_versions 必须升序去重")
        self.assertIn(data["conformance_profile"], FIXED_PROFILES)
        self.assertEqual(FIXED_PROFILES[data["conformance_profile"]], versions,
                         "profile 与 language_versions 的配对必须是 PRD 固定的那一种")
        self.assertEqual({1}, set(versions), "compiler must declare only wire version 1")

    def test_error_code_ranges(self):
        data = json.loads(self.caps().decode("utf-8"))
        ranges = data["error_code_ranges"]
        expected_keys = set(str(version) for version in data["language_versions"])
        self.assertEqual(expected_keys, set(ranges))
        for version in data["language_versions"]:
            key = str(version)
            self.assertEqual(key, str(int(key)), "版本键不得有前导零")
            entry = ranges[key]
            self.assertEqual({"min", "max"}, set(entry))
            self.assertEqual(FIXED_RANGES[version], (entry["min"], entry["max"]),
                             "version " + key + " 的码范围与 PRD §6.3 不一致")


class MiniDocumentTest(unittest.TestCase):
    """最小的 v0 文档端到端:像素期望值由本文件独立算出。"""

    #: v0 §3.5 的 4x4 两色示例。slot 1 = 红,slot 2 = 绿。
    TOKENS = ("0..0", ".11.", ".11.", "0..0")
    PALETTE = ("FF0000FF", "00FF00FF")

    def document(self, version=1):
        lines = [
            "{",
            '  "format": "PXL",',
            '  "version": ' + str(version) + ",",
            '  "encoding": "char",',
            '  "width": 4,',
            '  "height": 4,',
            '  "palette": [',
        ]
        for index, item in enumerate(self.PALETTE):
            comma = "," if index + 1 < len(self.PALETTE) else ""
            lines.append('    "' + item + '"' + comma)
        lines.extend([
            "  ],",
            '  "layers": [',
            "    {",
            '      "name": "base",',
            '      "blend": "normal",',
            '      "ops": [',
            "        {",
            '          "op": "grid",',
            '          "x": 0,',
            '          "y": 0,',
            '          "grid": [',
        ])
        for index, row in enumerate(self.TOKENS):
            comma = "," if index + 1 < len(self.TOKENS) else ""
            lines.append('            "' + row + '"' + comma)
        lines.extend([
            "          ]",
            "        }",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
        ])
        return "\n".join(lines).encode("utf-8")

    def expected_rgba(self):
        """按 §4.2 的 char 字符集与 §4.4 的 slot 顺序,手工算出期望像素。"""
        transparent = (0, 0, 0, 0)
        red = (0xFF, 0x00, 0x00, 0xFF)
        green = (0x00, 0xFF, 0x00, 0xFF)
        table = {".": transparent, "0": red, "1": green}
        out = bytearray()
        for row in self.TOKENS:
            for char in row:
                out.extend(table[char])
        return bytes(out)

    def test_render_matches_hand_computed_pixels(self):
        rendered = render_document(self.document())
        self.assertEqual(4, rendered.width)
        self.assertEqual(4, rendered.height)
        self.assertEqual(self.expected_rgba(), rendered.rgba)

    def test_png_roundtrip_through_own_decoder(self):
        rendered = render_document(self.document())
        png = encode_png(rendered.width, rendered.height, rendered.rgba)
        self.assertEqual(b"\x89PNG\r\n\x1a\n", png[:8])
        image = pngdec.decode(png)
        self.assertEqual((rendered.width, rendered.height), (image.width, image.height))
        self.assertEqual(rendered.rgba, image.rgba)

    def test_deterministic_bytes(self):
        first = render_document(self.document())
        second = render_document(self.document())
        self.assertEqual(first.rgba, second.rgba)
        self.assertEqual(encode_png(4, 4, first.rgba), encode_png(4, 4, second.rgba))

    def test_error_codes(self):
        broken = self.document().replace(b'"width": 4', b'"width": 0')
        with self.assertRaises(PxlError) as caught:
            render_document(broken)
        self.assertEqual("PXL002", caught.exception.code)

        unknown = self.document().replace(b'"encoding": "char"', b'"encoding": "hex"')
        with self.assertRaises(PxlError) as caught:
            render_document(unknown)
        self.assertEqual("PXL003", caught.exception.code)


class CommandLineTest(unittest.TestCase):

    def test_compile_and_check(self):
        with workspace() as directory:
            (directory / "entry.pxl").write_bytes(MiniDocumentTest().document())
            code, out, err = run_cli(["compile", "entry.pxl", "--asset-root", str(directory),
                                      "-o", str(directory / "out.png"),
                                      "--deps", str(directory / "deps.tsv")])
            self.assertEqual(0, code, err.decode("utf-8", "replace"))
            png = (directory / "out.png").read_bytes()
            image = pngdec.decode(png)
            self.assertEqual(MiniDocumentTest().expected_rgba(), image.rgba)
            manifest = (directory / "deps.tsv").read_text(encoding="utf-8").split("\n")
            self.assertEqual(2, len(manifest))
            columns = manifest[0].split("\t")
            self.assertEqual("entry", columns[0])
            self.assertEqual("pxl", columns[1])
            self.assertEqual("entry.pxl", columns[2])
            self.assertEqual(hashlib.sha256(
                (directory / "entry.pxl").read_bytes()).hexdigest(), columns[3])

            # 与 Java 侧 v1-check-writes-nothing 同侧要求（审查 R4-09）：check 只判不写。
            # 原先只断言 *.png 的**数量**，挡不住 .tmp / 清单文件之类的写入；改成整棵目录树
            # 逐项快照比较，多一个少一个都红。cwd 也放在目录里，顺带覆盖"在 cwd 里落盘"。
            before = tree_snapshot(directory)
            code, out, err = run_cli(["check", "entry.pxl", "--asset-root", str(directory)],
                                     cwd=directory,
                                     extra_env={"PYTHONPATH": str(COMPILER)})
            self.assertEqual(0, code, err.decode("utf-8", "replace"))
            self.assertEqual(before, tree_snapshot(directory), "check 不得产出或删除任何文件")

    def test_failure_leaves_no_output(self):
        with workspace() as directory:
            (directory / "entry.pxl").write_bytes(b"{ this is not json }")
            target = directory / "out.png"
            code, out, err = run_cli(["compile", "entry.pxl", "--asset-root", str(directory),
                                      "-o", str(target)])
            self.assertEqual(3, code)
            self.assertIn(b"PXL001", err)
            self.assertFalse(target.exists(), "失败不得留下半份输出")
            self.assertEqual([], sorted(directory.glob(".texel-*")), "临时文件必须清掉")


class V1BoundaryCliTest(unittest.TestCase):
    """v1 CLI boundary checks shared with the Java implementation."""

    def test_large_png_payload_declared_as_pxl_is_pxl009(self):
        # The 64 MiB limit belongs to PXL documents; check kind first.
        entry = v1_doc(1, 1, '{"a":{"kind":"pxl","path":"art/tile.pxl"}}',
                       '[{"op":"stamp","asset":"a","x":0,"y":0}]')
        payload = b"\x89PNG\r\n\x1a\n" + b"x" * (64 * 1024 * 1024 + 1 - 8)
        failure = expect_failure({"entry.pxl": entry, "art/tile.pxl": payload},
                                 "PXL009", "oversized PNG declared as PXL")
        self.assertEqual(["entry.pxl", "art/tile.pxl"], failure.chain)

    def test_output_symlink_loop_reports_cli002(self):
        for scenario in ("output", "deps", "parent"):
            with self.subTest(scenario=scenario), workspace() as directory:
                entry = directory / "entry.pxl"
                entry.write_bytes(MiniDocumentTest().document())
                first = directory / "loop-a"
                second = directory / "loop-b"
                try:
                    first.symlink_to(second.name, target_is_directory=scenario == "parent")
                    second.symlink_to(first.name, target_is_directory=scenario == "parent")
                except (OSError, NotImplementedError, AttributeError):
                    self.skipTest("symlinks are not available")
                safe_manifest = directory / "deps.tsv"
                if scenario == "deps":
                    args = ["check", "entry.pxl", "--asset-root", str(directory),
                            "--deps", str(first)]
                else:
                    output = first / "out.png" if scenario == "parent" else first
                    args = ["compile", "entry.pxl", "--asset-root", str(directory),
                            "-o", str(output), "--deps", str(safe_manifest)]
                code, out, err = run_cli(
                    args, cwd=directory, extra_env={"PYTHONPATH": str(COMPILER)},
                )
                self.assertEqual(2, code, err.decode("utf-8", "replace"))
                self.assertIn(b"CLI002", err)
                self.assertNotIn(b"Traceback", err)
                self.assertTrue(first.is_symlink())
                self.assertTrue(second.is_symlink())
                self.assertFalse(safe_manifest.exists(), "拒绝前不得写出清单")


class GraphTest(unittest.TestCase):
    """v1 的 png asset:§5.3 的 asset stamp 与 §9.4 的依赖清单。"""

    ENTRY = (
        b"{\n"
        b'  "format": "PXL",\n'
        b'  "version": 1,\n'
        b'  "encoding": "char",\n'
        b'  "width": 4,\n'
        b'  "height": 4,\n'
        b'  "palette": [\n'
        b'    "FF0000FF"\n'
        b"  ],\n"
        b'  "layers": [\n'
        b"    {\n"
        b'      "name": "base",\n'
        b'      "blend": "normal",\n'
        b'      "ops": [\n'
        b"        {\n"
        b'          "op": "stamp",\n'
        b'          "asset": "tile",\n'
        b'          "x": 1,\n'
        b'          "y": 1\n'
        b"        }\n"
        b"      ]\n"
        b"    }\n"
        b"  ],\n"
        b'  "assets": {\n'
        b'    "tile": {\n'
        b'      "kind": "png",\n'
        b'      "path": "parts/tile.png"\n'
        b"    }\n"
        b"  }\n"
        b"}\n")

    TILE = bytes((
        1, 2, 3, 255, 4, 5, 6, 255,
        7, 8, 9, 255, 10, 11, 12, 255,
    ))

    def test_png_asset_stamp_and_manifest(self):
        with workspace() as directory:
            (directory / "parts").mkdir()
            (directory / "entry.pxl").write_bytes(self.ENTRY)
            png = encode_png(2, 2, self.TILE)
            (directory / "parts" / "tile.png").write_bytes(png)

            compiled = compile_target("entry.pxl", build_memory_resolver(directory), {})
            self.assertEqual((4, 4), (compiled.width, compiled.height))
            expected = bytearray(4 * 4 * 4)
            rows = [self.TILE[0:8], self.TILE[8:16]]
            for j in range(2):
                offset = ((1 + j) * 4 + 1) * 4
                expected[offset:offset + 8] = rows[j]
            self.assertEqual(bytes(expected), compiled.rgba)

            lines = compiled.manifest_text().split("\n")
            self.assertEqual(3, len(lines))
            entry = lines[0].split("\t")
            self.assertEqual("entry", entry[0])
            self.assertEqual("entry.pxl", entry[2])
            dependency = lines[1].split("\t")
            self.assertEqual("dependency", dependency[0])
            self.assertEqual("png", dependency[1])
            self.assertEqual("parts/tile.png", dependency[2])
            self.assertEqual(hashlib.sha256(png).hexdigest(), dependency[3])

    def test_missing_png_asset_is_pxl009(self):
        with workspace() as directory:
            (directory / "entry.pxl").write_bytes(self.ENTRY)
            with self.assertRaises(PxlError) as caught:
                compile_target("entry.pxl", build_memory_resolver(directory), {})
            self.assertEqual("PXL009", caught.exception.code)


class ProfileSelectionTest(unittest.TestCase):
    """The current capability profile selects both current corpus suites."""

    def runner(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("texel_conformance",
                                                      str(TOOLS / "conformance.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_current_profile_selects_base_and_components(self):
        runner = self.runner()
        profile = runner.select_profile("pxl-v1")
        self.assertEqual(len(runner.base_index()),
                         len(runner.cases_for_suite("base", profile, False)))
        self.assertEqual(len(runner.components_index()),
                         len(runner.cases_for_suite("components", profile, False)))

    def test_pending_rows_never_count_as_passed(self):
        runner = self.runner()
        pending = [row["case"] for row in runner.components_index()
                   if row["expected"].strip() == "-"]
        for case in pending:
            directory = runner.COMPONENT_CASES / case
            if directory.is_dir():
                self.assertEqual([], sorted(directory.glob("expected.*")),
                                 "pending cases must not contain expected.* files")

class IndependenceTest(unittest.TestCase):
    """阶段 6 的核心判据:实现必须独立,不能借用 importer 或参考 oracle。"""

    def test_no_forbidden_references(self):
        hits = []
        for path in python_sources():
            text = path.read_text(encoding="utf-8")
            for needle in FORBIDDEN_IN_SOURCE:
                if needle in text:
                    hits.append(str(path.relative_to(ROOT)) + ": " + needle)
        self.assertEqual([], hits, "源码里出现了被禁止的依赖: " + repr(hits))

    def test_only_stdlib_imports(self):
        allowed = {"argparse", "hashlib", "json", "os", "re", "struct", "sys", "zlib",
                   "pathlib", "unittest"}
        hits = []
        for path in python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root not in allowed and root != "texel_compiler":
                            hits.append(str(path.relative_to(ROOT)) + ": " + root)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    root = (node.module or "").split(".")[0]
                    if root and root not in allowed and root != "texel_compiler":
                        hits.append(str(path.relative_to(ROOT)) + ": " + root)
        self.assertEqual([], hits, "出现了计划外的第三方依赖: " + repr(hits))


class Python38SyntaxTest(unittest.TestCase):

    def test_every_source_parses_as_38(self):
        for path in python_sources():
            text = path.read_text(encoding="utf-8")
            try:
                ast.parse(text, filename=str(path), feature_version=8)
            except SyntaxError as failure:
                self.fail(path.name + " 不能在 3.8 语法下解析: " + str(failure))

    def test_no_39_plus_stdlib_calls(self):
        # 只查最容易顺手写下的几个;真正的证据是跑一次 3.8 解释器。
        # 字面量拼起来写:全仓的 tests/test_py38_syntax.py 会正则扫 tests/,直接写出这些
        # 名字会让它把本文件报成违规(它只按文件名跳过自己)。
        forbidden = (".remove" + "prefix(", ".remove" + "suffix(",
                     "toml" + "lib", "zone" + "info", "graph" + "lib")
        hits = []
        for path in python_sources():
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    hits.append(str(path.relative_to(ROOT)) + ": " + needle)
        self.assertEqual([], hits)


# ---------------------------------------------------------------- current component fixtures


def pxl_doc(width, height, palette, ops):
    """Return a minimal self-contained current PXL document."""
    return ('{"format":"PXL","version":1,"width":%d,"height":%d,"encoding":"char",'
            '"palette":%s,"layers":[{"name":"base","blend":"normal","ops":%s}]}'
            % (width, height, palette, ops)).encode("utf-8")


def v1_doc(width, height, assets, ops):
    """一份最小 v1 文档(与 Java 侧 V1TestMain.v1Document 同形)。"""
    return ('{"format":"PXL","version":1,"width":%d,"height":%d,"encoding":"char",'
            '"palette":["FFFFFFFF"],"assets":%s,"layers":[{"name":"base",'
            '"blend":"normal","ops":%s}]}' % (width, height, assets, ops)).encode("utf-8")


def compile_files(files, bindings=None):
    return compile_target("entry.pxl", MemoryResolver(files), bindings or {})


def expect_failure(files, code, what):
    try:
        compile_files(files)
    except PxlError as failure:
        if failure.code != code:
            raise AssertionError(what + ":期望 " + code + ",实际 " + failure.describe())
        return failure
    raise AssertionError(what + ":期望 " + code + ",实际编译成功")


def node_fan_out(children, with_missing):
    """入口 + children 个互不相同的 1x1 子文档(与 Java 侧同形)。

    缺失依赖放在**最后一个子文档**里:每文档 assets 声明数上限是 4096(§10.1),
    往入口再塞一个就变成 PXL002,测不到预算;放在最深处还能构造出"计数器先越过上限、
    随后才撞上缺失依赖"这条最刁钻的顺序。
    """
    leaf = pxl_doc(1, 1, '["112233FF"]', '[{"op":"fill","slot":1}]')
    last = v1_doc(1, 1, '{"zz":{"kind":"pxl","path":"gone.pxl"}}',
                  '[{"op":"fill","slot":1},{"op":"stamp","asset":"zz","x":0,"y":0}]')
    files = {}
    assets = []
    ops = []
    for index in range(children):
        name = "d%04d" % index
        files[name + ".pxl"] = last if (index == children - 1 and with_missing) else leaf
        assets.append('"a%s":{"kind":"pxl","path":"%s.pxl"}' % (name, name))
        ops.append('{"op":"stamp","asset":"a%s","x":0,"y":0}' % name)
    files["entry.pxl"] = v1_doc(1, 1, "{" + ",".join(assets) + "}", "[" + ",".join(ops) + "]")
    return files


def png_fan_out(with_missing):
    """唯一 PNG 叶节点 8190 个 + 3 份 PXL = 8193 个唯一源文件 > 8192(§10.2)。

    PNG 只计文件、不建渲染节点(§10.4 第 1 条),所以节点数仍是 3,测到的确实是**文件数**。
    每文档 assets 声明数 <= 4096(§10.1),因此拆成 entry -> mid0 -> mid1 三份。
    """
    png = encode_png(1, 1, bytes((0xFF, 0, 0, 0xFF)))
    files = {}
    counter = [0]
    names = ["mid0.pxl", "mid1.pxl"]
    for document in range(len(names) + 1):
        if document < len(names):
            assets = []
            ops = []
            for _ in range(4095):
                name = "p%04d.png" % counter[0]
                counter[0] += 1
                files[name] = png
                assets.append('"a%s":{"kind":"png","path":"%s"}' % (name[:-4], name))
                ops.append('{"op":"stamp","asset":"a%s","x":0,"y":0}' % name[:-4])
            assets.append('"next":{"kind":"pxl","path":"%s"}' % names[document])
            ops.append('{"op":"stamp","asset":"next","x":0,"y":0}')
            target = "entry.pxl" if document == 0 else names[document - 1]
            files[target] = v1_doc(1, 1, "{" + ",".join(assets) + "}", "[" + ",".join(ops) + "]")
        else:
            files[names[-1]] = (
                v1_doc(1, 1, '{"zz":{"kind":"png","path":"gone.png"}}',
                       '[{"op":"fill","slot":1},{"op":"stamp","asset":"zz","x":0,"y":0}]')
                if with_missing else pxl_doc(1, 1, '["FFFFFFFF"]', '[{"op":"fill","slot":1}]'))
    return files


def byte_chain(with_missing):
    """一份**恰好 64 MiB**(合法上限)的子文档 + 入口,累计字节 > 64 MiB(§10.2)。

    尾部空白是合法 JSON,于是"文档很小、字节很大"可以精确构造。用两份而不是几十份小文档:
    累计字节越界与读的字节总量无关,但少几十次解析。
    """
    unit = pxl_doc(1, 1, '["FFFFFFFF"]', '[{"op":"fill","slot":1}]')
    padded = unit + b" " * (pxlcompile.MAX_PXL_BYTES - len(unit))
    assets = '"big":{"kind":"pxl","path":"big.pxl"}'
    ops = ['{"op":"stamp","asset":"big","x":0,"y":0}']
    if with_missing:
        assets += ',"zz":{"kind":"pxl","path":"gone.pxl"}'
        ops.append('{"op":"stamp","asset":"zz","x":0,"y":0}')
    return {"entry.pxl": v1_doc(1, 1, "{" + assets + "}", "[" + ",".join(ops) + "]"),
            "big.pxl": padded}


def raster_heavy():
    """4 份 2048x2048 文档的链,每份一条全幅 mirror(flipX)+ 一条 stamp。

    全局光栅 = (4 mirror + 3 stamp) x 2^22 = 29360128 > 2^24;每份文档恰好 2^22(合法),
    所以命中必须是**会话**预算 PXL011,而不是单文档的 PXL002。

    <p>为什么用 mirror(flipX) 而不是 fill/stamp:判据要能区分"预算先于光栅化"与"画完再判"。
    纯 stamp 的写入在 Python 里是整行切片(C 层),几千万次只要几十毫秒,与"不写"拉不开量级;
    flipX 的 mirror 是**逐像素**循环(每条 4.2M 次 ≈ 0.9s),四份就把"先画后判"推到 ~3.6s,
    与相位 A 的毫秒级相差三个数量级。
    """
    ops = ('[{"op":"mirror","from":{"x":0,"y":0,"w":2048,"h":2048},'
           '"to":{"x":0,"y":0},"flipX":true},'
           '{"op":"stamp","asset":"next","x":0,"y":0}]')
    files = {}
    names = ["entry.pxl", "p1.pxl", "p2.pxl", "p3.pxl"]
    for index, name in enumerate(names):
        if index == len(names) - 1:
            files[name] = v1_doc(2048, 2048, "{}",
                                 '[{"op":"mirror","from":{"x":0,"y":0,"w":2048,"h":2048},'
                                 '"to":{"x":0,"y":0},"flipX":true}]')
            continue
        files[name] = v1_doc(2048, 2048,
                             '{"next":{"kind":"pxl","path":"%s"}}' % names[index + 1], ops)
    return files


class BudgetParityTest(unittest.TestCase):
    """§11.2:R2(依赖可解析)先于 R4(会话预算),R4 先于光栅化。

    与 Java 侧 V1TestMain.twoPhaseBudgets() 用**同一批夹具形状**,两侧结论逐格一致。
    """

    def test_node_limit_is_pxl011(self):
        failure = expect_failure(node_fan_out(pxlcompile.MAX_RENDER_NODES, False), "PXL011",
                                 "节点数超限")
        self.assertIn("唯一渲染节点数", failure.message)
        self.assertIn("引用链:", failure.message)
        self.assertEqual("entry.pxl", failure.chain[0])

    def test_node_limit_with_missing_dependency_is_pxl009(self):
        expect_failure(node_fan_out(pxlcompile.MAX_RENDER_NODES, True), "PXL009",
                       "缺失依赖必须压过节点数超限")

    def test_file_limit_is_pxl011(self):
        failure = expect_failure(png_fan_out(False), "PXL011", "文件数超限")
        self.assertIn("唯一实际源文件数", failure.message)
        self.assertIn("引用链:", failure.message)

    def test_file_limit_with_missing_dependency_is_pxl009(self):
        expect_failure(png_fan_out(True), "PXL009", "缺失依赖必须压过文件数超限")

    def test_byte_limit_is_pxl011(self):
        failure = expect_failure(byte_chain(False), "PXL011", "累计字节超限")
        self.assertIn("累计 PXL UTF-8 字节数", failure.message)
        self.assertIn("引用链:", failure.message)

    def test_byte_limit_with_missing_dependency_is_pxl009(self):
        expect_failure(byte_chain(True), "PXL009", "缺失依赖必须压过累计字节超限")

    def test_pixel_limit_with_missing_dependency_is_pxl009(self):
        # §11.2（本轮补的边界）：像素预算超限后**不再解码**后续 PNG，但 R2 的「依赖缺失」仍优先。
        # 缺的 asset 名 "zz" 在 ASCII 序上排在 32 张 p0000..p0031 之后，所以它是在像素预算
        # 已经超限之后才被解析到的 —— 这时报的必须是 PXL009，而不是 PXL011。
        expect_failure(many_distinct_pngs(32, 4, with_missing=True), "PXL009",
                       "像素预算超限不得压过依赖缺失")

    def test_single_document_65mib_is_pxl002(self):
        # §5.0.2:单文档 64 MiB 是**格式边界** -> PXL002;PXL011 只用于会话累计。
        unit = pxl_doc(1, 1, '["FFFFFFFF"]', '[{"op":"fill","slot":1}]')
        huge = unit + b" " * (65 * 1024 * 1024 - len(unit))
        expect_failure({"entry.pxl": huge}, "PXL002", "单文档 65 MiB 是 PXL002")

    def test_global_raster_limit_is_pxl011_before_rasterizing(self):
        # 判据两条:码是 PXL011,且**在光栅化之前**拒绝(耗时)。
        started = time.time()
        failure = expect_failure(raster_heavy(), "PXL011", "全局光栅超限")
        elapsed_ms = (time.time() - started) * 1000.0
        sys.stdout.write("[GATE] global-raster 判定耗时=%.0fms\n" % elapsed_ms)
        self.assertIn("29360128", failure.message)
        self.assertIn("引用链:", failure.message)
        # 相位 A 只做算术(实测 2ms);若先把 29360128 个像素画出来再判,Python 侧约 3.6s。
        self.assertLess(elapsed_ms, 500.0,
                        "预算判定用了 %.0fms:光栅化发生在预算之前(§11.2)" % elapsed_ms)


class ReferenceChainTest(unittest.TestCase):
    """§9.3:每条错误都要有「入口 -> ... -> 实际失败文件」的链与失败文件名。"""

    def test_nested_missing_dependency_carries_chain(self):
        entry = v1_doc(1, 1, '{"a":{"kind":"pxl","path":"parts/a.pxl"}}',
                       '[{"op":"stamp","asset":"a","x":0,"y":0}]')
        mid = v1_doc(1, 1, '{"gone":{"kind":"pxl","path":"gone.pxl"}}',
                     '[{"op":"stamp","asset":"gone","x":0,"y":0}]')
        failure = expect_failure({"entry.pxl": entry, "parts/a.pxl": mid}, "PXL009",
                                 "嵌套缺依赖")
        self.assertEqual(["entry.pxl", "parts/a.pxl", "parts/gone.pxl"], failure.chain)
        self.assertIn("parts/gone.pxl", failure.message)
        self.assertIn("引用链", failure.describe())

    def test_nested_document_error_carries_chain(self):
        entry = v1_doc(1, 1, '{"a":{"kind":"pxl","path":"parts/a.pxl"}}',
                       '[{"op":"stamp","asset":"a","x":0,"y":0}]')
        mid = v1_doc(1, 1, "{}", '[{"op":"fill","slot":1}]').replace(b'"char"', b'"hex"')
        failure = expect_failure({"entry.pxl": entry, "parts/a.pxl": mid}, "PXL003",
                                 "子文档 encoding 非法")
        self.assertEqual(["entry.pxl", "parts/a.pxl"], failure.chain)

    def test_root_error_chain_starts_at_entry(self):
        bad = v1_doc(1, 1, "{}", '[{"op":"fill","slot":1}]').replace(b'"char"', b'"hex"')
        failure = expect_failure({"entry.pxl": bad}, "PXL003", "入口 encoding 非法")
        self.assertEqual(["entry.pxl"], failure.chain)

    def test_png_decode_error_carries_chain(self):
        entry = v1_doc(1, 1, '{"img":{"kind":"png","path":"art/tile.png"}}',
                       '[{"op":"stamp","asset":"img","x":0,"y":0}]')
        failure = expect_failure({"entry.pxl": entry, "art/tile.png": b"\x89PNG\r\n\x1a\nxyz"},
                                 "PXL006", "坏 PNG 是 PXL006")
        self.assertEqual(["entry.pxl", "art/tile.png"], failure.chain)


class NestedRelativePathTest(unittest.TestCase):
    """§7 / F07:引用相对**声明它的文档**解析。

    与 Java 侧 V1TestMain.nestedRelativePaths() 用同一份夹具:两侧必须都接受,且像素与
    依赖清单逐项相同。
    """

    def files(self):
        leaf = pxl_doc(2, 2, '["FF0000FF","00FF00FF"]',
                      '[{"op":"grid","x":0,"y":0,"grid":["01","10"]}]')
        mid = v1_doc(2, 2, '{"b":{"kind":"pxl","path":"../shared/b.pxl"}}',
                     '[{"op":"stamp","asset":"b","x":0,"y":0}]')
        entry = v1_doc(2, 2, '{"a":{"kind":"pxl","path":"parts/a.pxl"}}',
                       '[{"op":"stamp","asset":"a","x":0,"y":0}]')
        return {"entry.pxl": entry, "parts/a.pxl": mid, "shared/b.pxl": leaf}

    def test_parent_reference_is_legal(self):
        compiled = compile_files(self.files())
        self.assertEqual([0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF,
                          0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF],
                         list(compiled.rgba))
        self.assertEqual(["entry.pxl", "parts/a.pxl", "shared/b.pxl"],
                         [item.logical_path for item in compiled.dependencies])

    def test_nested_escape_is_pxl008(self):
        files = self.files()
        files["parts/a.pxl"] = v1_doc(2, 2, '{"b":{"kind":"pxl","path":"../../outside/b.pxl"}}',
                                      '[{"op":"fill","slot":1}]')
        failure = expect_failure(files, "PXL008", "相对声明文档的逃逸仍是 PXL008")
        self.assertIn("逃出 asset root", failure.message)
        self.assertEqual(["entry.pxl", "parts/a.pxl"], failure.chain)


# ---------------------------------------------------------------- 第四轮夹具


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def same_pxl_many_names(children=2, names=4096):
    """两份子文档各 names 个 asset 名，全部指向同一个 tile.pxl：唯一文件 4、读取 8195。

    为什么 target 是 .pxl 而不是 .png：PNG 有解码缓存，同一路径在一次会话里只会被 read 一次，
    于是"按读取次数计"与"按唯一文件计"在 PNG 上恰好相等 —— 用 PNG 做 target 时这条门禁对计数
    口径**不敏感**（审查 R4-01）。PXL 没有读取缓存（load_graph 的 read 发生在节点缓存检查之前），
    换成 .pxl 后按读取次数计会立刻误报 PXL011，门禁才咬得住。
    """
    tile = pxl_doc(1, 1, '["FF0000FF"]', '[{"op":"fill","slot":1}]')
    files = {"tile.pxl": tile}
    entry_assets = []
    entry_ops = []
    for child in range(1, children + 1):
        assets = []
        ops = []
        for index in range(names):
            name = "a%04d" % index
            assets.append('"%s":{"kind":"pxl","path":"tile.pxl"}' % name)
            ops.append('{"op":"stamp","asset":"%s","x":0,"y":0}' % name)
        files["c%d.pxl" % child] = v1_doc(1, 1, "{" + ",".join(assets) + "}",
                                          "[" + ",".join(ops) + "]")
        entry_assets.append('"c%d":{"kind":"pxl","path":"c%d.pxl"}' % (child, child))
        entry_ops.append('{"op":"stamp","asset":"c%d","x":0,"y":0}' % child)
    files["entry.pxl"] = v1_doc(1, 1, "{" + ",".join(entry_assets) + "}",
                                "[" + ",".join(entry_ops) + "]")
    return files


def nodes_and_files_over_limit(nodes=4096):
    """节点数 4097 > 4096 且唯一文件 8193 > 8192：用来钉 R4 的表内顺序。"""
    png = encode_png(1, 1, bytes((0xFF, 0, 0, 0xFF)))
    files = {}
    entry_assets = []
    entry_ops = []
    for index in range(nodes):
        name = "d%04d" % index
        left = name + "x.png"
        right = name + "y.png"
        files[left] = png
        files[right] = png
        assets = ('"p":{"kind":"png","path":"%s"},"q":{"kind":"png","path":"%s"}'
                  % (left, right))
        files[name + ".pxl"] = v1_doc(1, 1, "{" + assets + "}",
                                      '[{"op":"stamp","asset":"p","x":0,"y":0},'
                                      '{"op":"stamp","asset":"q","x":0,"y":0}]')
        entry_assets.append('"a%s":{"kind":"pxl","path":"%s.pxl"}' % (name, name))
        entry_ops.append('{"op":"stamp","asset":"a%s","x":0,"y":0}' % name)
    files["entry.pxl"] = v1_doc(1, 1, "{" + ",".join(entry_assets) + "}",
                                "[" + ",".join(entry_ops) + "]")
    return files


def many_nodes_one_png(nodes=60):
    """nodes 个节点各 stamp 同一张 2048x2048 的 PNG；唯一像素 2^22，预算全合法。"""
    png = encode_png(2048, 2048, bytes(2048 * 2048 * 4))
    files = {"big.png": png}
    entry_assets = []
    entry_ops = []
    for index in range(nodes):
        name = "n%04d" % index
        files[name + ".pxl"] = v1_doc(1, 1, '{"big":{"kind":"png","path":"big.png"}}',
                                      '[{"op":"stamp","asset":"big","x":0,"y":0}]')
        entry_assets.append('"a%s":{"kind":"pxl","path":"%s.pxl"}' % (name, name))
        entry_ops.append('{"op":"stamp","asset":"a%s","x":0,"y":0}' % name)
    files["entry.pxl"] = v1_doc(1, 1, "{" + ",".join(entry_assets) + "}",
                                "[" + ",".join(entry_ops) + "]")
    return files


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF))


def ct2_png(chunks):
    """1x1、色彩类型 2(RGB) 的 PNG；chunks 是 [(类型, 载荷)] 的顺序清单。"""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes((0x11, 0x22, 0x33))
    out = bytearray(PNG_SIGNATURE)
    out += png_chunk(b"IHDR", ihdr)
    for kind, payload in chunks:
        if kind == b"IDAT":
            payload = zlib.compress(row)
        out += png_chunk(kind, payload)
    out += png_chunk(b"IEND", b"")
    return bytes(out)


class BudgetCountingTest(unittest.TestCase):
    """R3-01 / R3-03：唯一文件口径与解码缓存。"""

    def test_same_pxl_many_asset_names_is_legal(self):
        # 读取 8195 次、唯一文件 4 个：按读取次数计会误报 PXL011（R3-01 的守卫）。
        # 用 .pxl 做 target 才咬得住（见 same_pxl_many_names 的 docstring / 审查 R4-01）。
        compiled = compile_files(same_pxl_many_names())
        self.assertEqual(4, len(compiled.dependencies))
        self.assertEqual([0xFF, 0x00, 0x00, 0xFF], list(compiled.rgba[:4]))

    def test_budget_table_order_nodes_before_files(self):
        # §11.2 阶段 R4 的表内顺序：节点数在文件数之前。两项同时越界时报第一项。
        failure = expect_failure(nodes_and_files_over_limit(), "PXL011", "节点数与文件数同时越界")
        self.assertIn("唯一渲染节点数", failure.message)

    def test_png_decoded_once_per_unique_file(self):
        # 同一张 2048x2048 的 PNG 被 60 个节点引用：唯一像素 2^22 合法，但解码必须只有一次
        # （没有缓存时峰值 60 x 16 MiB，能把进程打 OOM）。
        calls = []
        original = pngdec.decode

        def spy(payload):
            calls.append(1)
            return original(payload)

        pngdec.decode = spy
        try:
            compiled = compile_files(many_nodes_one_png(60))
        finally:
            pngdec.decode = original
        self.assertEqual(1, len(calls), "同一 PNG 必须只解码一次")
        self.assertEqual(1, compiled.width)


class OutputAliasTest(unittest.TestCase):
    """R3-04：输出目标不得与**任何被读的源文件**别名冲突（PRD §13.2 CLI002）。"""

    def files(self):
        return {
            "entry.pxl": v1_doc(1, 1, '{"a":{"kind":"pxl","path":"parts/blade.pxl"}}',
                                '[{"op":"stamp","asset":"a","x":0,"y":0}]'),
            "parts/blade.pxl": pxl_doc(1, 1, '["112233FF"]', '[{"op":"fill","slot":1}]'),
        }

    def test_core_helper_blocks_entry_and_dependencies(self):
        with workspace() as root:
            for name, payload in self.files().items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            compiled = compile_target("entry.pxl", build_memory_resolver(root), {})
            self.assertEqual("parts/blade.pxl",
                             pxlcompile.conflicting_source(str(root / "parts" / "blade.pxl"),
                                                          str(root), compiled.dependencies))
            self.assertEqual("entry.pxl",
                             pxlcompile.conflicting_source(str(root / "entry.pxl"),
                                                          str(root), compiled.dependencies))
            self.assertIsNone(pxlcompile.conflicting_source(str(root / "out.png"), str(root),
                                                           compiled.dependencies))

    def test_cli_output_pointing_at_dependency_is_refused(self):
        with workspace() as root:
            for name, payload in self.files().items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            before = (root / "parts" / "blade.pxl").read_bytes()
            code, out, err = run_cli(["compile", "entry.pxl", "--asset-root", str(root),
                                      "-o", str(root / "parts" / "blade.pxl")])
            self.assertEqual(2, code, err.decode("utf-8", "replace"))
            self.assertEqual(before, (root / "parts" / "blade.pxl").read_bytes(),
                             "依赖不得被改写")

    def test_cli_deps_pointing_at_dependency_is_refused(self):
        with workspace() as root:
            for name, payload in self.files().items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            before = (root / "parts" / "blade.pxl").read_bytes()
            code, out, err = run_cli(["check", "entry.pxl", "--asset-root", str(root),
                                      "--deps", str(root / "parts" / "blade.pxl")])
            self.assertEqual(2, code, err.decode("utf-8", "replace"))
            self.assertEqual(before, (root / "parts" / "blade.pxl").read_bytes(),
                             "依赖不得被改写")


def mirror_chain(docs):
    """docs 份 2048x2048 文档的链，每份只有一条全幅 mirror(flipX) + 一次子文档 stamp。

    与 Java 侧 V1TestMain.mirrorChain 同一份形状：mirror 的写入面积在 fromW/fromH，
    漏计它会让"只由 mirror 组成的超预算图"在会话光栅里变成 0。
    """
    mirror = ('{"op":"mirror","flipX":true,"from":{"x":0,"y":0,"w":2048,"h":2048},'
              '"to":{"x":0,"y":0}}')
    names = ["entry.pxl"] + ["p%d.pxl" % index for index in range(1, docs)]
    files = {}
    for index, name in enumerate(names):
        if index == docs - 1:
            files[name] = v1_doc(2048, 2048, "{}", "[" + mirror + "]")
            continue
        files[name] = v1_doc(2048, 2048,
                             '{"next":{"kind":"pxl","path":"%s"}}' % names[index + 1],
                             "[" + mirror + ',{"op":"stamp","asset":"next","x":0,"y":0}]')
    return files


def deep_chain(depth):
    """depth 篇文档的链（每篇 stamp 下一篇，最后一篇是 1x1 的 v0 叶子）。"""
    names = ["entry.pxl"] + ["p%d.pxl" % index for index in range(1, depth)]
    files = {}
    for index, name in enumerate(names):
        if index == depth - 1:
            files[name] = pxl_doc(1, 1, '["112233FF"]', '[{"op":"fill","slot":1}]')
            continue
        files[name] = v1_doc(1, 1, '{"c":{"kind":"pxl","path":"%s"}}' % names[index + 1],
                             '[{"op":"stamp","asset":"c","x":0,"y":0}]')
    return files


class MirrorRasterAndDepthTest(unittest.TestCase):
    """G-02 / 假门禁：mirror 的会话光栅面积，以及深度边界 64/65。"""

    def test_mirror_only_session_raster_is_pxl011(self):
        # 5 份 2048² 纯 mirror：mirror 5 x 2^22 = 20,971,520，加 4 次 stamp 16,777,216
        # 合计 37,748,736 > 2^24。把 mirror 算成 0 时恰好是 16,777,216（== 上限，不超），
        # 于是编译成功 —— 这条会红。
        failure = expect_failure(mirror_chain(5), "PXL011", "mirror-only 的会话超限")
        self.assertIn("37748736", failure.message)

    def test_mirror_raster_matches_java_fixture(self):
        # 与 Java 侧 v1.mirror-raster-matches-python-fixture 同一个数：29,360,128。
        failure = expect_failure(mirror_chain(4), "PXL011", "4 份 mirror 链")
        self.assertIn("29360128", failure.message)

    def test_depth_64_is_legal(self):
        # §10.2：64 层合法（Gate 的另一半：65 层越界）。
        compiled = compile_files(deep_chain(64))
        self.assertEqual(1, compiled.width)

    def test_depth_65_is_pxl011_and_reports_actual_depth(self):
        # 深度消息印的是**实际走到的层数**（进入第 65 篇之前停），并明写"更深的层未被读取"。
        # 深度必须 fail-fast（§10.4：进入更深一层之前判），所以 66 / 70 篇的链同样停在 65 ——
        # 这条同时钉住措辞不冒充链长（审查 R4-04）。
        for depth in (65, 66):
            failure = expect_failure(deep_chain(depth), "PXL011", "%d 层越界" % depth)
            self.assertIn("深度 65", failure.message)
            self.assertIn("拒绝再进入第 65 篇", failure.message)
            self.assertIn("更深的层未被读取", failure.message)


def ihdr_only_png(width, height):
    """只有 签名 + IHDR 的 PNG（其后没有 IDAT/IEND）：读头部合法，解码必报 PXL006。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return PNG_SIGNATURE + png_chunk(b"IHDR", ihdr)


def many_distinct_pngs(total=32, complete=4, with_missing=False):
    """total 张 2048x2048 PNG：前 complete 张完整，其余只到 IHDR。

    路径各不相同即为不同源文件（像素预算按**唯一逻辑路径**计，与内容是否相同无关）；
    把尾部做成"只有头部"是为了让"有没有解码"变成可判定的结构差异。

    with_missing=True 时再追加一条**不存在**的 png asset（名字 "zz"，ASCII 排序在最后），
    用来钉 §11.2 的另一半：像素预算超限之后不再解码，但 R2 的依赖缺失仍优先。
    """
    rgba = bytes(2048 * 2048 * 4)
    full = encode_png(2048, 2048, rgba)
    header_only = ihdr_only_png(2048, 2048)
    assets = []
    ops = []
    files = {}
    for index in range(total):
        name = "p%04d" % index
        path = "art/p%04d.png" % index
        files[path] = full if index < complete else header_only
        assets.append('"%s":{"kind":"png","path":"%s"}' % (name, path))
        ops.append('{"op":"stamp","asset":"%s","x":0,"y":0}' % name)
    if with_missing:
        assets.append('"zz":{"kind":"png","path":"art/gone.png"}')
        ops.append('{"op":"stamp","asset":"zz","x":0,"y":0}')
    files["entry.pxl"] = v1_doc(1, 1, "{" + ",".join(assets) + "}", "[" + ",".join(ops) + "]")
    return files


def png_entry(path="art/tile.png"):
    return {"entry.pxl": v1_doc(1, 1, '{"img":{"kind":"png","path":"%s"}}' % path,
                               '[{"op":"stamp","asset":"img","x":0,"y":0}]')}


class PngAssetKindAndBudgetTest(unittest.TestCase):
    """R4-02 / R4-03：像素预算约束解码工作量；kind 与实际字节不符的码边界。"""

    def test_png_pixel_budget_stops_decoding(self):
        # §10.2「累计 PNG 解码像素数」按名字就该约束**解码工作量**：32 张 2048^2，预算 2^24
        # 只容得下 4 张，第 5 张起必须**不解码**。判据是结构性的：第 5 张起只有签名 + IHDR
        # （没有 IDAT/IEND），一旦被解码就必报 PXL006，正确实现只读头部、报 PXL011。
        calls = []
        original = pngdec.decode

        def spy(payload):
            calls.append(1)
            return original(payload)

        pngdec.decode = spy
        started = time.monotonic()
        try:
            failure = expect_failure(many_distinct_pngs(32, 4), "PXL011", "32 张 2048^2")
        finally:
            pngdec.decode = original
        elapsed = time.monotonic() - started
        self.assertIn("累计 PNG 解码像素数", failure.message)
        self.assertIn("134217728", failure.message)
        # 墙钟只打印、**不判定**：共享 CI 上"只解 4 张"也可能超过任何固定阈值，那种门禁不可判定。
        # 判据是上面两条确定性断言：解码次数 <= 4（spy 计数），且第 5 张起只有 IHDR。
        sys.stdout.write("\n[INFO] 像素预算判定耗时=%.3fs（解码 %d 次）\n" % (elapsed, len(calls)))
        self.assertLessEqual(len(calls), 4, "超限之后不得再解码")

    def test_png_asset_not_a_png_is_pxl009(self):
        # §11.1：声明的 kind 与实际字节不符（连 PNG 签名都没有）→ PXL009；
        # 有签名但结构非法 → PXL006。边界是「是不是 PNG」与「PNG 是否合法」。
        files = png_entry()
        files["art/tile.png"] = "这不是 PNG".encode("utf-8")
        failure = expect_failure(files, "PXL009", "声明为 png 但字节不是 PNG")
        self.assertEqual(["entry.pxl", "art/tile.png"], failure.chain)
        files["art/tile.png"] = b""
        expect_failure(files, "PXL009", "零字节的 png asset")
        files["art/tile.png"] = ihdr_only_png(1, 1)
        expect_failure(files, "PXL006", "有签名但缺 IDAT/IEND")


class AssetStampPlanTest(unittest.TestCase):
    """asset stamp：形状统一（审查 codex/DSv4）+ 面积只进会话预算（规范 §10.1 / §10.3）。"""

    def test_stamp_area_counts_only_in_session_budget(self):
        # 两个**不同尺寸**的 asset 的 stamp 进同一份几何计划（过去同名 kind 有两种元组形状），
        # 且它们的写入面积不计入单文档 2^22、只计入会话全局光栅：
        #   2048^2 x 4 次 = 16,777,216；1024^2 x 1 次 = 1,048,576；合计 17,825,792 > 2^24。
        # 画布 2048^2 恰好等于单文档面积上限、本文档自身 op 工作为 0（asset stamp 不计），
        # 所以这条**只可能**报 PXL011（会话光栅）；把面积算进单文档就会先报 PXL002。
        big = encode_png(2048, 2048, bytes(2048 * 2048 * 4))
        small = encode_png(1024, 1024, bytes(1024 * 1024 * 4))
        ops = ['{"op":"stamp","asset":"big","x":0,"y":0}'] * 4
        ops.append('{"op":"stamp","asset":"small","x":0,"y":0}')
        files = {"entry.pxl": v1_doc(2048, 2048,
                                     '{"big":{"kind":"png","path":"big.png"},'
                                     '"small":{"kind":"png","path":"small.png"}}',
                                     "[" + ",".join(ops) + "]"),
                 "big.png": big, "small.png": small}
        failure = expect_failure(files, "PXL011", "asset stamp 的会话光栅超限")
        self.assertIn("全局光栅工作", failure.message)
        self.assertIn("17825792", failure.message)

    def test_two_asset_sizes_share_one_geometry_plan(self):
        # 两个尺寸不同的 asset 各自 stamp 一次：渲染计划里必须出现**两个** AssetStamp，
        # 各自带着自己的 x/y/尺寸 —— 形状串了这里会 AttributeError 或画错位置。
        red = encode_png(3, 2, bytes((0xFF, 0x00, 0x00, 0xFF)) * 6)
        green = encode_png(5, 1, bytes((0x00, 0xFF, 0x00, 0xFF)) * 5)
        files = {"entry.pxl": v1_doc(8, 8,
                                     '{"red":{"kind":"png","path":"red.png"},'
                                     '"green":{"kind":"png","path":"green.png"}}',
                                     '[{"op":"stamp","asset":"red","x":1,"y":1},'
                                     '{"op":"stamp","asset":"green","x":2,"y":3}]'),
                 "red.png": red, "green.png": green}
        compiled = compile_files(files)

        def pixel(x, y):
            offset = (y * 8 + x) * 4
            return list(compiled.rgba[offset:offset + 4])

        self.assertEqual([0xFF, 0x00, 0x00, 0xFF], pixel(1, 1), "红块左上")
        self.assertEqual([0xFF, 0x00, 0x00, 0xFF], pixel(3, 2), "红块右下（3x2）")
        self.assertEqual([0x00, 0xFF, 0x00, 0xFF], pixel(2, 3), "绿条左端")
        self.assertEqual([0x00, 0xFF, 0x00, 0xFF], pixel(6, 3), "绿条右端（5x1）")
        self.assertEqual([0, 0, 0, 0], pixel(7, 3), "绿条右侧必须仍是透明")
        self.assertEqual([0, 0, 0, 0], pixel(0, 0), "画布左上必须仍是透明")


class PngTrnsOrderTest(unittest.TestCase):
    """PM6：tRNS 早于 PLTE 这条要在**ct2** 上也可判定（语料那条是 ct3，被更早的检查抓住）。"""

    def entry(self):
        return {"entry.pxl": v1_doc(1, 1,
                                    '{"img":{"kind":"png","path":"art/tile.png"}}',
                                    '[{"op":"stamp","asset":"img","x":0,"y":0}]')}

    def test_ct2_trns_before_plte_is_pxl006(self):
        files = self.entry()
        files["art/tile.png"] = ct2_png([(b"tRNS", bytes(6)), (b"PLTE", bytes(3)),
                                         (b"IDAT", b"")])
        failure = expect_failure(files, "PXL006", "ct2 的 tRNS 早于 PLTE")
        self.assertEqual(["entry.pxl", "art/tile.png"], failure.chain)

    def test_ct2_trns_without_plte_is_accepted(self):
        # 反向：ct2 的 PLTE 是可选的，tRNS 单独出现完全合法（键色像素 alpha=0）。
        files = self.entry()
        files["art/tile.png"] = ct2_png([(b"tRNS", bytes((0x11, 0x22, 0x33)) * 2 and
                                          bytes((0, 0x11, 0, 0x22, 0, 0x33))),
                                         (b"IDAT", b"")])
        compiled = compile_files(files)
        self.assertEqual([0x11, 0x22, 0x33, 0x00], list(compiled.rgba[:4]))


if __name__ == "__main__":
    unittest.main()
