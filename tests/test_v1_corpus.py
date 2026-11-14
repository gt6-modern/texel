"""Mechanical checks for the current PXL conformance corpus."""
import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "spec" / "conformance"
PROFILES = CONF / "profiles.tsv"
SUITES = {"base": CONF, "components": CONF / "v1"}
CODE = re.compile(r"^PXL[0-9]{3}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    result = []
    for number, line in enumerate(lines[1:], 2):
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != len(header):
            raise AssertionError("%s:%d has %d columns, expected %d" %
                                 (path, number, len(cells), len(header)))
        result.append(dict(zip(header, cells)))
    return result


def case_dirs(root):
    found = set()
    for item in (root / "cases").iterdir():
        if not item.is_dir():
            continue
        if item.name == "negative":
            found.update("negative/" + child.name for child in item.iterdir() if child.is_dir())
        else:
            found.add(item.name)
    return found


class ProfilesTest(unittest.TestCase):
    def test_only_current_profile_and_suites(self):
        actual = []
        for line in PROFILES.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                actual.append(tuple(line.split("\t")))
        self.assertEqual([
            ("pxl-v1", "base", "include_all", "-"),
            ("pxl-v1", "components", "include_all", "-"),
        ], actual)

    def test_profile_selects_every_indexed_case(self):
        selected = set()
        for suite, root in SUITES.items():
            indexed = {row["case"] for row in rows(root / "CASES.tsv")}
            self.assertTrue(indexed, suite)
            selected.update((suite, case) for case in indexed)
        self.assertEqual(sum(len(rows(root / "CASES.tsv")) for root in SUITES.values()),
                         len(selected))


class CorpusIndexTest(unittest.TestCase):
    def test_indexes_and_directories_are_bidirectional(self):
        for suite, root in SUITES.items():
            indexed = {row["case"] for row in rows(root / "CASES.tsv")}
            self.assertEqual(indexed, case_dirs(root), suite)

    def test_no_wire_version_zero_documents(self):
        """语料里不得出现 `"version": 0`。

        唯一例外是**显式钉住 v0 必须被拒**的负例:目录在 `negative/` 下,
        且 `expected.error` 为 `PXL007`(域内但不支持的版本)。其余任何位置出现
        version 0,都说明有一份语料在按旧版本号写出。
        """
        pattern = re.compile(rb'"version"\s*:\s*0(?:\D|$)')
        offenders = []
        allowed = []
        for path in CONF.rglob("*.pxl"):
            if not pattern.search(path.read_bytes()):
                continue
            case_dir = path.parent
            error = case_dir / "expected.error"
            in_negative = "negative" in case_dir.parts
            rejected = (in_negative and error.is_file()
                        and error.read_text(encoding="utf-8").strip() == "PXL007")
            if rejected:
                allowed.append(path.relative_to(ROOT).as_posix())
            else:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)
        # 空集合守卫:这个例外必须真的被至少一条用例用上,不能退化成"谁都能过"。
        self.assertTrue(allowed, "没有任何负例覆盖 v0 必须被拒绝的情形")

    def test_negative_cases_have_single_error_code(self):
        for suite, root in SUITES.items():
            for row in rows(root / "CASES.tsv"):
                if not row["case"].startswith("negative/"):
                    continue
                error = root / "cases" / row["case"] / "expected.error"
                if error.is_file():
                    text = error.read_text(encoding="utf-8")
                    self.assertTrue(text.endswith("\n"), str(error))
                    self.assertRegex(text[:-1], CODE, str(error))

    def test_expected_pixel_digests_are_lowercase_sha256(self):
        scanned = 0
        for path in CONF.rglob("expected*.pixels.sha256"):
            scanned += 1
            self.assertRegex(path.read_text(encoding="utf-8").strip(), SHA256, str(path))
        # 空集合守卫：路径/目录改名后判据会静默变成"零条通过"。
        self.assertGreater(scanned, 0, "一份像素摘要都没扫到")

    def test_json_carriers_are_objects(self):
        scanned = 0
        for path in CONF.rglob("bindings.json"):
            scanned += 1
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict, str(path))
        self.assertGreater(scanned, 0, "一份 bindings.json 都没扫到")

    def test_every_error_code_has_a_negative_case(self):
        """每个 PXL001..PXL012 至少要有一条负例（Core §11.2 + Extension §11.1）。

        只写"正则允许 PXL001..PXL012"等于允许反而不要求：加一个码而没有人钉它，
        门禁不会红。这条把"每个码都被钉住"变成可执行断言。
        """
        covered = set()
        for suite, root in SUITES.items():
            for row in rows(root / "CASES.tsv"):
                if not row["case"].startswith("negative/"):
                    continue
                error = root / "cases" / row["case"] / "expected.error"
                if error.is_file():
                    covered.add(error.read_text(encoding="utf-8").strip())
        expected = set("PXL%03d" % number for number in range(1, 13))
        self.assertEqual(sorted(expected - covered), [],
                         "以下错误码没有任何负例")

    def test_component_oracle_is_python38_syntax(self):
        path = CONF / "v1" / "tools" / "compiler_render_ref.py"
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
