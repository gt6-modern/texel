#!/usr/bin/env python3
"""Check machine-synchronized facts in the Chinese and English PXL specs."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
START = "```json pxl-spec-sync\n"
END = "\n```"


def manifest(path):
    text = path.read_text(encoding="utf-8")
    start = text.find(START)
    if start < 0:
        raise AssertionError(str(path) + ": missing pxl-spec-sync block")
    start += len(START)
    end = text.find(END, start)
    if end < 0:
        raise AssertionError(str(path) + ": unterminated pxl-spec-sync block")
    return json.loads(text[start:end])



def section_ids(path):
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"(?:Core|Extension) §[0-9]+(?:\.[0-9]+)?|附录 [A-C]", text))

def main():
    zh = manifest(ROOT / "spec" / "PXL.md")
    en = manifest(ROOT / "spec" / "PXL.en.md")
    if zh != en:
        raise AssertionError("Chinese and English synchronization manifests differ")
    zh_sections = section_ids(ROOT / "spec" / "PXL.md")
    en_sections = section_ids(ROOT / "spec" / "PXL.en.md")
    if zh_sections != en_sections:
        missing = sorted(zh_sections - en_sections)
        extra = sorted(en_sections - zh_sections)
        raise AssertionError("section identifiers differ: missing=%r extra=%r" %
                             (missing, extra))
    required = set("PXL%03d" % number for number in range(1, 13))
    if set(zh["error_codes"]) != required:
        raise AssertionError("error-code manifest is incomplete")
    print("SPEC_SYNC_OK wire_version=%d error_codes=%d sections=%d" %
          (zh["wire_version"], len(zh["error_codes"]), len(zh_sections)))
    return 0

if __name__ == "__main__":
    sys.exit(main())

