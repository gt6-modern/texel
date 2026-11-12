"""Current single-profile conformance selection."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "spec/conformance/tools/profile_select.py"
SPEC = importlib.util.spec_from_file_location("profile_select", str(TOOL))
profile_select = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_select)


class CurrentProfileTest(unittest.TestCase):
    def rows(self):
        return profile_select._read_rows(ROOT / "spec/conformance/profiles.tsv")

    def test_current_profile_selects_both_corpus_partitions(self):
        selected = profile_select.select(self.rows(), "pxl-v1")
        suites = set(suite for suite, _ in selected)
        self.assertEqual(set(["base", "components"]), suites)
        self.assertTrue(selected)

    def test_capability_fixture_selects_current_profile(self):
        fixture = ROOT / "spec/conformance/capabilities/pxl-v1.json"
        self.assertEqual("pxl-v1", profile_select.profile_from_capabilities(fixture))

    def test_cli_json_is_deterministic(self):
        command = [sys.executable, str(TOOL), "--profile", "pxl-v1", "--json"]
        first = subprocess.check_output(command, cwd=str(ROOT))
        second = subprocess.check_output(command, cwd=str(ROOT))
        self.assertEqual(first, second)
        payload = json.loads(first.decode("utf-8"))
        self.assertEqual("pxl-v1", payload["profile"])
        self.assertTrue(payload["cases"])

    def test_unknown_profile_fails(self):
        with self.assertRaises(profile_select.ProfileError):
            profile_select.select(self.rows(), "missing")


if __name__ == "__main__":
    unittest.main()
