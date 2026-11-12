"""Release-surface gates for the public Texel 0.1.0 repository."""

import json
import os
import re
import unittest
from pathlib import Path

from importer.serialize import serialize

ROOT = Path(__file__).resolve().parent.parent


class PublicReleaseTest(unittest.TestCase):
    def test_importer_emits_only_current_wire_version(self):
        text = serialize(1, 1, "char", [], [{"op": "fill", "slot": 0}])
        self.assertIn('"version": 1', text)
        self.assertNotIn('"version": ' + str(0), text)

    def test_only_current_specs_are_published(self):
        self.assertTrue((ROOT / "spec" / "PXL.md").is_file())
        self.assertTrue((ROOT / "spec" / "PXL.en.md").is_file())
        self.assertFalse((ROOT / "spec" / ("PXL-v" + str(0) + ".md")).exists())
        self.assertFalse((ROOT / "spec" / "PXL-v1.md").exists())

    def test_release_metadata_is_consistent(self):
        importer = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        compiler = (ROOT / "compiler" / "python" / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(importer, r'(?m)^version = "0\.1\.0"$')
        self.assertRegex(compiler, r'(?m)^version = "0\.1\.0"$')
        fixture = json.loads((ROOT / "spec" / "conformance" / "capabilities" /
                              "pxl-v1.json").read_text(encoding="utf-8"))
        self.assertEqual("0.1.0", fixture["compiler_version"])
        self.assertEqual("pxl-v1", fixture["conformance_profile"])
        self.assertEqual([1], fixture["language_versions"])
        self.assertEqual(set(["1"]), set(fixture["error_code_ranges"]))
        self.assertEqual("pxl-png", fixture["png_input_profile"])

    def test_community_health_files_exist(self):
        required = (
            "README.md", "README.zh-CN.md", "CONTRIBUTING.md",
            "CONTRIBUTING.zh-CN.md", "SECURITY.md", "SECURITY.zh-CN.md",
            "SUPPORT.md", "SUPPORT.zh-CN.md", "CODE_OF_CONDUCT.md",
            "CODE_OF_CONDUCT.zh-CN.md", "GOVERNANCE.md", "CHANGELOG.md",
            "NOTICE", ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/documentation.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".github/dependabot.yml", ".github/workflows/release.yml",
        )
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_internal_process_documents_are_not_published(self):
        self.assertFalse((ROOT / "docs" / "reviews").exists())
        self.assertFalse((ROOT / "docs" / "findings").exists())
        self.assertFalse((ROOT / "docs" / "superpowers").exists())

    def test_public_text_has_no_private_machine_paths(self):
        forbidden = (re.compile(r"[A-Za-z]:\\\\src\\\\"),
                     re.compile(r"C:\\\\Users\\\\", re.IGNORECASE),
                     re.compile(r"Program Files\\\\BellSoft", re.IGNORECASE))
        pruned = {".git", ".gradle-home", "build", "_sandbox", "_tmp"}
        for directory, names, files in os.walk(str(ROOT), topdown=True):
            names[:] = [name for name in names
                        if name not in pruned and not name.startswith(".review-")]
            base = Path(directory)
            for name in files:
                path = base / name
                rel = path.relative_to(ROOT).as_posix()
                if path.suffix.lower() in (".png", ".jar", ".class", ".zip", ".gz"):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for pattern in forbidden:
                    self.assertIsNone(pattern.search(text),
                                      rel + " contains a private machine path")


if __name__ == "__main__":
    unittest.main()
