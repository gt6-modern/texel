#!/usr/bin/env python3
"""Cross-check every public release version and capability surface."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def must(pattern, path):
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise AssertionError("missing release metadata in " + str(path))
    return match.group(1)


def main():
    version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
    if not re.match(r"^0\.[0-9]+\.[0-9]+$", version):
        raise AssertionError("VERSION must be a pre-1.0 semantic version")
    observed = {
        "importer pyproject": must(r'^version = "([^"]+)"$', ROOT / "pyproject.toml"),
        "compiler pyproject": must(r'^version = "([^"]+)"$', ROOT / "compiler/python/pyproject.toml"),
        "python capability": must(r'^COMPILER_VERSION = "([^"]+)"$', ROOT / "compiler/python/texel_compiler/capabilities.py"),
        "java capability": must(r'compiler_version\\\": \\\"([^\\]+)\\\"', ROOT / "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java"),
    }
    fixture = json.loads((ROOT / "spec/conformance/capabilities/pxl-v1.json").read_text(encoding="utf-8"))
    observed["capability fixture"] = fixture["compiler_version"]
    wrong = dict((name, value) for name, value in observed.items() if value != version)
    if wrong:
        raise AssertionError("release versions differ from %s: %r" % (version, wrong))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if ("## [" + version + "]") not in changelog:
        raise AssertionError("CHANGELOG has no release section for " + version)
    print("RELEASE_METADATA_OK version=" + version)
    return 0

if __name__ == "__main__":
    sys.exit(main())
