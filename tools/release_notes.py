#!/usr/bin/env python3
"""Print one CHANGELOG section, for use as the GitHub Release body.

Usage::

    python tools/release_notes.py           # the version in VERSION
    python tools/release_notes.py 0.1.1     # an explicit version

Why this exists: the release workflow used to pass ``generate_release_notes: true``
alone. With no previous release to diff against, GitHub produces a single line
("**Full Changelog**: ..."), so the release page said nothing about what changed.
The curated, human-maintained section in ``CHANGELOG.md`` is the right body.

The section starts at ``## [<version>]`` and ends at the next ``## [`` heading, at
the Markdown link-reference block (``[Unreleased]: https://...``), or at the end of
the file. A missing section is exit code 1: ``check_release_metadata.py`` already
requires the section to exist, so failing here means the two have drifted.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(text, version):
    """Return the body lines of ``## [version]`` (heading excluded)."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^## \[" + re.escape(version) + r"\]", line):
            start = index + 1
            break
    if start is None:
        return None
    body = []
    for line in lines[start:]:
        if line.startswith("## ["):
            break
        # 链接定义块（[Unreleased]: url / [0.1.0]: url）不属于这一节。
        if re.match(r"^\[[^\]]+\]:\s", line):
            break
        body.append(line)
    while body and not body[-1].strip():
        body.pop()
    return body


def main(argv):
    version = argv[1].strip() if len(argv) > 1 else (ROOT / "VERSION").read_text(encoding="ascii").strip()
    body = section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if body is None:
        sys.stderr.write("CHANGELOG.md 没有 `## [" + version + "]` 小节\n")
        return 1
    if not body:
        sys.stderr.write("CHANGELOG.md 的 `## [" + version + "]` 小节是空的\n")
        return 1
    sys.stdout.write("\n".join(body).strip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
