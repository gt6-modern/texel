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
        pattern = re.compile(rb'"version"\s*:\s*0(?:\D|$)')
        offenders = []
        for path in CONF.rglob("*.pxl"):
            if pattern.search(path.read_bytes()):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

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
        for path in CONF.rglob("expected*.pixels.sha256"):
            self.assertRegex(path.read_text(encoding="utf-8").strip(), SHA256, str(path))

    def test_json_carriers_are_objects(self):
        for path in CONF.rglob("bindings.json"):
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict, str(path))

    def test_component_oracle_is_python38_syntax(self):
        path = CONF / "v1" / "tools" / "compiler_render_ref.py"
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
