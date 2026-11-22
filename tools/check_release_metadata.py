#!/usr/bin/env python3
"""Cross-check every public release version and capability surface.

每个"发布面"都在 ``SURFACES`` 里登记一次:路径 + 取值正则 + 人读名字。新增一个要跟
``VERSION`` 同步的值时,必须同时加进这张表,否则它就是一个**不在门禁里**的版本面 ——
``texel_compiler.__version__`` 曾经就是这样漏掉的(审查 #22)。
"""
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


#: (人读名字, 相对路径, 取值正则)。顺序按"最该先改的"排,报错时容易定位。
SURFACES = (
    ("importer pyproject", "pyproject.toml", r'^version = "([^"]+)"$'),
    ("compiler pyproject", "compiler/python/pyproject.toml", r'^version = "([^"]+)"$'),
    ("python package __version__", "compiler/python/texel_compiler/__init__.py",
     r'^__version__ = "([^"]+)"$'),
    ("python capability", "compiler/python/texel_compiler/capabilities.py",
     r'^COMPILER_VERSION = "([^"]+)"$'),
    ("java capability", "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java",
     r'compiler_version\\": \\"([^\\]+)\\"'),
)


def observe(root):
    """读出所有发布面的版本值;``capability fixture`` 按 JSON 读,其余按正则。"""
    root = Path(root)
    version = (root / "VERSION").read_text(encoding="ascii").strip()
    if not re.match(r"^0\.[0-9]+\.[0-9]+$", version):
        raise AssertionError("VERSION must be a pre-1.0 semantic version")
    observed = {}
    for name, relative, pattern in SURFACES:
        observed[name] = must(pattern, root / relative)
    fixture = json.loads((root / "spec/conformance/capabilities/pxl-v1.json")
                         .read_text(encoding="utf-8"))
    observed["capability fixture"] = fixture["compiler_version"]
    return version, observed


def check(root):
    """返回版本号;任何发布面不一致就 AssertionError。"""
    root = Path(root)
    version, observed = observe(root)
    wrong = dict((name, value) for name, value in observed.items() if value != version)
    if wrong:
        raise AssertionError("release versions differ from %s: %r" % (version, wrong))
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if ("## [" + version + "]") not in changelog:
        raise AssertionError("CHANGELOG has no release section for " + version)
    return version


def main():
    print("RELEASE_METADATA_OK version=" + check(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
