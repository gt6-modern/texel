#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全量自检:把 `CASES.tsv` 的每一条用例按它自己的形态跑一遍,报告实测结果。

这不是被测实现(Java 的 `texel-compiler` 不在本文件的范围里)。它是**语料自身的体检**:
目录与索引是否一一对应、每种形态的文件是否齐、`expected.*` 是否与两条独立路径
(现有抽取器 + `pxl_render_ref` 的渲染 oracle)一致、批量用例的计数与聚合断言是否成立。

用法::

    <py> spec/conformance/tools/python_ref_verify.py            # 全量
    <py> spec/conformance/tools/python_ref_verify.py --quiet    # 只打印汇总与失败项
    <py> spec/conformance/tools/python_ref_verify.py real-oil   # 只做指定的几条

退出码:发现任何问题即非零。**`--verbose` 不省略通过项。**

检查项(逐条对应规范条款):

* §13.1 一个用例一个目录、形态由索引的 `form` 列与目录前缀共同确定、`input.pxl` 与 `input.png` 互斥;
* §13.2a 往返正例六条里抽取侧能判定的四条 + 摘要一致 + 渲染回放一致;
* §13.2b 仅渲染正例的像素摘要与尺寸;
* §13.2c 负例:实现必须以 `expected.error` 的码失败,且不留输出;
* §13.3 用例形态与验证方式一致(负例绝不放 `expected.pxl`);
* §13.4 摘要的字节流定义;
* §13.6 生成型用例的配方(输入由 `gen_cases.py --check` 判定,这里只查配方存在);
* §13.7 批量用例的 `manifest.tsv` 行数 = 成员目录数 = 宣称的文件数,
  `input` 列必须是 `sample:` 前缀且已登记在 `samples/MANIFEST.tsv`,去重组逐字节相同。
"""

import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parents[2]
CONFORMANCE = TOOLS_DIR.parent
CASES_DIR = CONFORMANCE / "cases"
CASES_TSV = CONFORMANCE / "CASES.tsv"
SAMPLES_DIR = ROOT / "samples"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import png_ref  # noqa: E402
import pxl_render_ref  # noqa: E402
from importer import extract_png  # noqa: E402
from importer.errors import PxlError  # noqa: E402

COLUMNS = ("case", "source", "form", "size", "colors", "encoding", "covers", "input")

#: 批量用例(§13.7)与它们宣称的成员数
SUITE_SIZES = {
    "real-chain-overlay-dedupe": 2,
    "real-fluid-source-255": 255,
    "real-item-material-family-400": 400,
}

ROUND_TRIP_PREFIXES = ("syn-", "real-")
RENDER_PREFIXES = ("op-", "blend-")

#: 故意不写成规范形式的仅渲染正例(§3.8 的接受集用例)
NON_CANONICAL = ("op-lenient-bom-crlf", "op-lenient-structure")


class Report(object):
    def __init__(self):
        self.checks = 0
        self.problems = []

    def check(self, condition, case, message):
        self.checks += 1
        if not condition:
            self.problems.append((case, message))
        return condition

    def fail(self, case, message):
        self.checks += 1
        self.problems.append((case, message))


# --------------------------------------------------------------------------
# 通用判定
# --------------------------------------------------------------------------

def load_index():
    text = CASES_TSV.read_text(encoding="utf-8")
    lines = [line for line in text.split("\n") if line != ""]
    header = tuple(lines[0].split("\t"))
    if header != COLUMNS:
        raise SystemExit("CASES.tsv 表头不是 %d 列: %r" % (len(COLUMNS), header))
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise SystemExit("CASES.tsv 第 %d 行有 %d 列，应为 %d 列"
                             % (number, len(cells), len(COLUMNS)))
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


SIZE_PLAIN = re.compile(r"^\d+x\d+$")
SIZE_BATCH = re.compile(r"^\d+x\(\d+\.\.\d+\)$")
COLORS_PLAIN = re.compile(r"^\d+$")
TIER_ORDER = ("char", "index", "index4")
ENCODING_RANGE = re.compile(r"^(char|index|index4)\.\.(char|index|index4)$")
CASE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$")


def check_index_semantics(report):
    """§13.3 的索引语义：取值域 + 负例/正例/批量三类形态的用法约束。

    这些规则此前只写在 Java harness 与 pytest 里，**本工具自己不看**——于是
    「负例 size 写成实测值」「正例三列写 `-`」「批量单值与成员不符」在这里全绿。
    语料体检是独立的第二双眼睛，缺了这些就只是"能读表"。
    """
    rows = load_index()
    names = set()
    for row in rows:
        case = row["case"]
        if case in names:
            report.fail(case, "用例名重复")
        names.add(case)
        if not CASE_NAME.match(case):
            report.fail(case, "用例名不合规")
        if row["source"] not in ("synthetic", "real"):
            report.fail(case, "source 取值非法: %r" % (row["source"],))
        if row["form"] not in ("single", "batch"):
            report.fail(case, "form 取值非法: %r" % (row["form"],))
        if row["input"] not in ("committed", "generated"):
            report.fail(case, "input 取值非法: %r" % (row["input"],))

        size, colors, encoding = row["size"], row["colors"], row["encoding"]
        if not (size == "-" or SIZE_PLAIN.match(size) or SIZE_BATCH.match(size)):
            report.fail(case, "size 取值非法: %r" % (size,))
        if not (colors == "-" or colors == "varying" or COLORS_PLAIN.match(colors)):
            report.fail(case, "colors 取值非法: %r" % (colors,))
        if not (encoding == "-" or encoding in TIER_ORDER or ENCODING_RANGE.match(encoding)):
            report.fail(case, "encoding 取值非法: %r" % (encoding,))
        ranged = ENCODING_RANGE.match(encoding)
        if ranged and TIER_ORDER.index(ranged.group(1)) > TIER_ORDER.index(ranged.group(2)):
            report.fail(case, "encoding 区间左端比右端宽: %r" % (encoding,))

        if case.startswith("negative/"):
            if row["form"] != "single":
                report.fail(case, "负例的 form 必须是 single")
            for column, value in (("size", size), ("colors", colors)):
                if value != "-":
                    report.fail(case, "负例的 %s 必须写 `-`，实际 %r" % (column, value))
        else:
            for column, value in (("size", size), ("colors", colors),
                                  ("encoding", encoding)):
                if value == "-":
                    report.fail(case, "正例的 %s 不得写 `-`" % column)
            if row["form"] != "batch":
                if colors == "varying":
                    report.fail(case, "只有 form=batch 的行才能写 colors=varying")
                if ranged:
                    report.fail(case, "只有 form=batch 的行才能写 encoding 区间")
                if "(" in size:
                    report.fail(case, "只有 form=batch 的行才能写 `Wx(...)` 形式的 size")
    return rows


def check_batch_columns(report, rows):
    """§13.3：批量行的 size / colors / encoding 必须与成员实际内容相容。"""
    for row in rows:
        if row["form"] != "batch":
            continue
        case = row["case"]
        members = case_dir(case) / "members"
        if not members.is_dir():
            report.fail(case, "form=batch 却没有 members/")
            continue
        sizes = []
        encodings = []
        palettes = []
        for member in sorted(members.iterdir()):
            golden = member / "expected.pxl"
            if not member.is_dir() or not golden.is_file():
                continue
            document = json.loads(golden.read_text(encoding="utf-8"))
            sizes.append((int(document["width"]), int(document["height"])))
            encodings.append(document["encoding"])
            palettes.append(len(document["palette"]))
        if not sizes:
            report.fail(case, "没有一个成员带 expected.pxl")
            continue
        size = row["size"]
        ranged = SIZE_BATCH.match(size)
        plain = SIZE_PLAIN.match(size)
        if ranged:
            width = int(re.match(r"^(\d+)x", size).group(1))
            low = int(re.search(r"\((\d+)\.\.", size).group(1))
            high = int(re.search(r"\.\.(\d+)\)$", size).group(1))
            for actual in sizes:
                if actual[0] != width:
                    report.fail(case, "成员宽 %d 与 size 列的 %d 不符" % (actual[0], width))
                    break
                if not low <= actual[1] <= high:
                    report.fail(case, "成员高 %d 不在 size 列的 %d..%d 内"
                                % (actual[1], low, high))
                    break
        elif plain:
            expected = (int(size.split("x")[0]), int(size.split("x")[1]))
            for actual in sizes:
                if actual != expected:
                    report.fail(case, "成员尺寸 %dx%d 与 size 列的 %s 不符"
                                % (actual[0], actual[1], size))
                    break
        else:
            report.fail(case, "批量行的 size 形式非法: %r" % (size,))
        colors = row["colors"]
        if colors != "varying" and colors != "-":
            if any(str(value) != colors for value in palettes):
                report.fail(case, "colors 列写单值 %s，但成员 palette 长度不一致" % colors)
        encoding = row["encoding"]
        ranged_enc = ENCODING_RANGE.match(encoding)
        if ranged_enc:
            low = TIER_ORDER.index(ranged_enc.group(1))
            high = TIER_ORDER.index(ranged_enc.group(2))
            for value in encodings:
                if not low <= TIER_ORDER.index(value) <= high:
                    report.fail(case, "成员档位 %s 不在 encoding 列的 %s 内"
                                % (value, encoding))
                    break
        elif encoding in TIER_ORDER:
            if any(value != encoding for value in encodings):
                report.fail(case, "encoding 列写单值 %s，但成员档位不一致" % encoding)
        else:
            report.fail(case, "批量行的 encoding 形式非法: %r" % (encoding,))


def case_dir(case):
    directory = CASES_DIR
    for part in case.split("/"):
        directory = directory / part
    return directory


def canonical_problems(payload):
    """§3.1 / §3.7 的字节级要求(逐字节判定的文件必须守)。"""
    problems = []
    if payload.startswith(b"\xef\xbb\xbf"):
        problems.append("带 BOM")
    if b"\r" in payload:
        problems.append("含 CR")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        problems.append("末尾不是恰一个 LF")
    for number, line in enumerate(payload.split(b"\n")[:-1], start=1):
        if line != line.rstrip():
            problems.append("第 %d 行有行尾空白" % number)
        if b"\t" in line:
            problems.append("第 %d 行含制表符" % number)
        indent = len(line) - len(line.lstrip(b" "))
        if indent % 2 != 0:
            problems.append("第 %d 行缩进不是 2 的倍数" % number)
    return problems


def provenance_paths():
    """Read registered sample paths from the machine-readable manifest."""
    path = SAMPLES_DIR / "MANIFEST.tsv"
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return set(line.split("\t", 1)[0] for line in lines[1:] if line.strip())


# --------------------------------------------------------------------------
# 形态:往返正例
# --------------------------------------------------------------------------

def verify_round_trip(report, case, notes):
    directory = case_dir(case)
    files = ("input.png", "expected.pxl", "expected.pixels.sha256", "notes.md")
    missing = [name for name in files if not (directory / name).is_file()]
    if not report.check(not missing, case, "缺文件: %s" % ", ".join(missing)):
        return
    payload = (directory / "input.png").read_bytes()
    golden = (directory / "expected.pxl").read_bytes()
    recorded = (directory / "expected.pixels.sha256").read_bytes()

    problems = canonical_problems(golden)
    report.check(not problems, case, "expected.pxl 非规范字节: %s" % "; ".join(problems))

    try:
        width, height, rgba = png_ref.decode_rgba8(payload)
    except png_ref.PngRefError as error:
        report.fail(case, "独立解码器读不了 input.png: %s" % (error,))
        return
    digest = pxl_render_ref.pixels_sha256(width, height, rgba)
    report.check(recorded == (digest + "\n").encode("ascii"), case,
                 "expected.pixels.sha256 与 input.png 解码出的 RGBA8 不一致")

    try:
        extracted = extract_png(payload)
    except PxlError as error:
        report.fail(case, "抽取器拒绝了合法往返正例: %s" % (error,))
        return
    report.check(extracted.encode("utf-8") == golden, case,
                 "抽取结果与 expected.pxl 不零 diff")

    try:
        out_w, out_h, out_rgba = pxl_render_ref.render_text(golden)
    except pxl_render_ref.PxlRefError as error:
        report.fail(case, "oracle 拒绝了 expected.pxl: %s" % (error,))
        return
    report.check((out_w, out_h) == (width, height), case,
                 "回渲尺寸 %dx%d != input.png 的 %dx%d"
                 % (out_w, out_h, width, height))
    report.check(out_rgba == rgba, case, "expected.pxl 回渲像素与 input.png 不同")
    report.check(pxl_render_ref.pixels_sha256(out_w, out_h, out_rgba) == digest, case,
                 "回渲摘要与 expected.pixels.sha256 不一致")

    if notes["source"] == "real" and notes["case"] not in SUITE_SIZES:
        relative = notes.get("samples_path")
        if not report.check(bool(relative), case,
                            "该 real- 用例没有登记素材相对路径"):
            return
        sample = SAMPLES_DIR.joinpath(*relative.split("/"))
        report.check(sample.is_file(), case, "samples 里缺少 %s" % notes["samples_path"])
        if sample.is_file():
            report.check(sample.read_bytes() == payload, case,
                         "input.png 与 samples 里的副本不是逐字节相同")

    if notes["colors"] not in ("-", "varying"):
        document = json.loads(golden.decode("utf-8"))
        # 这里的期望值来自 **manifest.tsv 的那一行**（不是 CASES.tsv），文案别再指错文件。
        report.check(str(len(document["palette"])) == notes["colors"], case,
                     "manifest 的 colors 与 palette 长度不符")
        report.check(document["encoding"] == notes["encoding"], case,
                     "manifest 的 encoding 与文档不符")
        size = notes["size"]
        if size and size[0].isdigit() and "(" not in size:
            report.check("%dx%d" % (document["width"], document["height"]) == size,
                         case, "CASES.tsv 的 size 列与文档不符")


# --------------------------------------------------------------------------
# 形态:仅渲染正例
# --------------------------------------------------------------------------

def verify_render_only(report, case):
    directory = case_dir(case)
    files = ("input.pxl", "expected.pixels.sha256", "notes.md")
    missing = [name for name in files if not (directory / name).is_file()]
    if not report.check(not missing, case, "缺文件: %s" % ", ".join(missing)):
        return
    report.check(not (directory / "input.png").is_file(), case,
                 "仅渲染正例不得有 input.png")
    report.check(not (directory / "expected.pxl").is_file(), case,
                 "仅渲染正例不得有 expected.pxl(没有「抽取零 diff」判据)")
    payload = (directory / "input.pxl").read_bytes()
    recorded = (directory / "expected.pixels.sha256").read_bytes()
    if case not in NON_CANONICAL:
        problems = canonical_problems(payload)
        report.check(not problems, case,
                     "input.pxl 不是规范形式: %s" % "; ".join(problems))
    try:
        width, height, rgba = pxl_render_ref.render_text(payload)
    except pxl_render_ref.PxlRefError as error:
        report.fail(case, "oracle 拒绝了 input.pxl: %s" % (error,))
        return
    digest = pxl_render_ref.pixels_sha256(width, height, rgba)
    report.check(recorded == (digest + "\n").encode("ascii"), case,
                 "expected.pixels.sha256 与回渲结果不一致")
    document = json.loads(payload.decode("utf-8").lstrip("\ufeff"))
    report.check((document["width"], document["height"]) == (width, height), case,
                 "文档声明的尺寸与渲染尺寸不符")


# --------------------------------------------------------------------------
# 形态:负例
# --------------------------------------------------------------------------

def verify_negative(report, case, row):
    directory = case_dir(case)
    has_pxl = (directory / "input.pxl").is_file()
    has_png = (directory / "input.png").is_file()
    error_file = directory / "expected.error"
    notes_file = directory / "notes.md"
    exclusive = report.check(has_pxl != has_png, case,
                             "input.pxl 与 input.png 必须恰好存在一个(§13.1)")
    if row.get("input") == "generated" and not has_pxl and not has_png:
        report.fail(case, "生成型用例的输入还没生成:先跑 "
                          "`python spec/conformance/tools/gen_cases.py --all`")
    if not exclusive:
        # 输入互斥已破，往下的"哪一侧"就没有定义；继续猜只会得到一条回溯。
        return
    for name in ("expected.pxl", "expected.pixels.sha256"):
        report.check(not (directory / name).is_file(), case,
                     "负例不得有 %s" % name)
    if not report.check(error_file.is_file() and notes_file.is_file(), case,
                        "缺 expected.error 或 notes.md"):
        return
    code = error_file.read_bytes()
    # current 的错误码止于 `PXL012`(Extension §11.1):PXL001..PXL006 属 core,
    # PXL007..PXL012 属 Extension。语料里两类都出现,故这里不能只放行前六码。
    report.check(code in [(("PXL%03d" % index) + "\n").encode("ascii")
                         for index in range(1, 13)], case,
                 "expected.error 不是「PXL001..PXL012 + LF」: %r" % (code,))
    expected = code.decode("ascii").strip()
    side = "渲染侧" if has_pxl else "抽取侧"
    report.check(side in notes_file.read_text(encoding="utf-8"), case,
                 "notes.md 没有写明受测侧(%s)" % side)
    report.check(row["covers"].startswith("%s(%s):" % (expected, side)), case,
                 "CASES.tsv 的 covers 列没有以 `%s(%s):` 开头" % (expected, side))

    if has_pxl:
        payload = (directory / "input.pxl").read_bytes()
        try:
            pxl_render_ref.render_text(payload)
        except pxl_render_ref.PxlRefError as error:
            report.check(error.code == expected, case,
                         "oracle 报 %s,期望 %s(%s)" % (error.code, expected,
                                                      error.message))
        else:
            report.fail(case, "oracle 未拒绝该文档(期望 %s)" % expected)
        return

    payload = (directory / "input.png").read_bytes()
    try:
        extract_png(payload)
    except PxlError as error:
        report.check(error.code == expected, case,
                     "抽取器报 %s,期望 %s" % (error.code, expected))
    else:
        report.fail(case, "抽取器未拒绝该输入(期望 %s)" % expected)


# --------------------------------------------------------------------------
# 形态:批量用例(§13.7)
# --------------------------------------------------------------------------

def _manifest_row_count(directory):
    """manifest.tsv 的数据行数；读不到返回 0。"""
    manifest = directory / "manifest.tsv"
    if not manifest.is_file():
        return 0
    lines = [line for line in manifest.read_text(encoding="utf-8").split("\n") if line]
    return max(0, len(lines) - 1)


def _section_lines(section):
    """`## aggregate` 段去掉行首列表序号后的各行。"""
    for line in section.split("\n"):
        yield re.sub(r"^\s*\d+[.)]\s*", "", line)


def _states_member_count(section, count):
    """总成员数是否写死：必须在**含「行数」/「成员目录数」的行**里、紧跟 `=`。

    不能整段搜数字：同一节里的散文（如「对 255 个文件逐个跑」）会让判据恒真。
    """
    for line in _section_lines(section):
        if "行数" not in line and "成员目录数" not in line:
            continue
        if re.search(r"=\s*\*{0,2}%d\*{0,2}(?![0-9])" % count, line):
            return True
    return False


def _states_split_count(section, label, count):
    """「正例 N / 缺陷 N」是否在同**一行**里写死。"""
    for line in _section_lines(section):
        if label not in line:
            continue
        if re.search(r"%s[^0-9\n]{0,12}\*{0,2}%d\*{0,2}\s*个" % (label, count), line):
            return True
    return False


def _aggregate_section(text):
    """取出 `## aggregate` 这一节（到下一个 `## ` 为止）。"""
    index = text.find("## aggregate")
    if index < 0:
        return ""
    section = text[index:]
    following = section.find("\n## ", 1)
    return section if following < 0 else section[:following]


def verify_suite(report, case, declared, registered):
    directory = case_dir(case)
    manifest = directory / "manifest.tsv"
    if not report.check(manifest.is_file(), case, "缺 manifest.tsv"):
        return
    if not report.check((directory / "notes.md").is_file(), case, "缺 notes.md"):
        return
    text = (directory / "notes.md").read_text(encoding="utf-8")
    report.check("## aggregate" in text, case, "notes.md 缺 `## aggregate` 一节")
    # 计数必须在 `## aggregate` 段里、且紧挨计数词。早先的实现扫整份 notes 的
    # `\d+`，于是有序列表序号「2.」与 SHA-256 里的数字串都能满足它——把 `= **2**`
    # 改成 `= **若干**` 仍然"全部通过"（审查第十轮实测过）。
    section = _aggregate_section(text)
    report.check(section, case, "notes.md 里找不到 `## aggregate` 段")
    if section:
        # 必须**紧贴**等号或「成员 N 个」这种计数短语。放宽成"计数词后 24 个字符内出现
        # 该数字"仍然会被同节里的其他数字（如「两份」「245 个」）满足——审查实测过。
        # `= N` 这个备选不能裸用：`**245 + 10 = 255**` 这种算术式同样满足它，
        # 于是 fluid 那条把 `= **255**` 改坏也照样绿（审查第十一轮实测）。必须把等号
        # 锚到**计数短语**上。
        report.check(_states_member_count(section, declared), case,
                     "## aggregate 里没有把成员数 %d 作为计数写死" % declared)

    lines = manifest.read_text(encoding="utf-8").split("\n")
    report.check(lines[0] == "member\tinput\tencoding\tcolors", case,
                 "manifest.tsv 表头不符: %r" % (lines[0],))
    rows = [line.split("\t") for line in lines[1:] if line != ""]
    for row in rows:
        report.check(len(row) == 4, case, "manifest 行不是 4 列: %r" % (row,))
    report.check(len(rows) == declared, case,
                 "manifest 行数 %d != 宣称的 %d" % (len(rows), declared))

    members_dir = directory / "members"
    on_disk = sorted(item.name for item in members_dir.iterdir() if item.is_dir()) \
        if members_dir.is_dir() else []
    report.check(len(on_disk) == declared, case,
                 "成员目录数 %d != 宣称的 %d" % (len(on_disk), declared))
    names = [row[0] for row in rows]
    report.check(sorted(names) == on_disk, case, "manifest 成员名与成员目录不一致")

    defect_members = 0
    positive_members = 0
    for row in rows:
        member, source, encoding, colors = row
        sub = members_dir / member
        report.check(sub.is_dir(), case, "缺少成员目录 %s" % member)
        if not sub.is_dir():
            continue
        if not source.startswith("sample:"):
            report.fail(case, "成员 %s 的 input 列不是 sample: 形式: %r"
                        % (member, source))
            continue
        relative = source[len("sample:"):]
        report.check(relative in registered, case,
                     "成员 %s 的来源 %s 没有登记在 samples/MANIFEST.tsv"
                     % (member, relative))
        report.check((SAMPLES_DIR.joinpath(*relative.split("/"))).is_file(), case,
                     "samples 里缺少 %s" % relative)
        member_notes = sub / "notes.md"
        report.check(member_notes.is_file(), case, "成员 %s 缺 notes.md" % member)
        if member_notes.is_file():
            report.check(("`%s`" % relative) in member_notes.read_text(encoding="utf-8"),
                         case, "成员 %s 的 notes.md 没有声明来源 %s" % (member, relative))
        if (sub / "expected.error").is_file():
            defect_members += 1
            report.check(encoding == "-" and colors == "-", case,
                         "缺陷成员 %s 的 colors/encoding 必须写 `-`" % member)
            report.check(not (sub / "expected.pxl").is_file(), case,
                         "缺陷成员 %s 不得有 expected.pxl" % member)
            report.check(not (sub / "expected.pixels.sha256").is_file(), case,
                         "缺陷成员 %s 不得有 expected.pixels.sha256" % member)
        else:
            positive_members += 1
            verify_round_trip(report, "%s/members/%s" % (case, member),
                              {"source": "real", "case": case,
                               "samples_path": relative, "colors": colors,
                               "encoding": encoding, "size": "-"})
    if section and defect_members > 0:
        # §13.7「正例成员数 + 缺陷成员数 = 写死的成员数,两者都要写死具体数字」。
        # 只在**存在缺陷成员**时才要求拆分——全是正例的套件正例数就等于总数，
        # 已经由上面那条总计数判据覆盖了，再要求一遍只是逼人写废话。
        for label, value in (("正例", positive_members), ("缺陷", defect_members)):
            report.check(_states_split_count(section, label, value), case,
                         "## aggregate 里没有把「%s成员数 %d」写死" % (label, value))
    report.check(positive_members + defect_members == declared, case,
                 "正例 %d + 缺陷 %d != %d" % (positive_members, defect_members,
                                              declared))
    if case == "real-chain-overlay-dedupe":
        goldens = [members_dir / name / "expected.pxl" for name in names]
        payloads = [path.read_bytes() for path in goldens if path.is_file()]
        report.check(len(payloads) == 2 and payloads[0] == payloads[1], case,
                     "去重组的两个 expected.pxl 不是逐字节相同")
    return positive_members, defect_members


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv):
    args = argv[1:]
    quiet = "--quiet" in args
    selected = [item for item in args if not item.startswith("-")]
    unknown = [item for item in args if item.startswith("-")
               and item not in ("--quiet", "-h", "--help")]
    if unknown:
        sys.stderr.write("未知选项: %s\n" % ", ".join(unknown))
        return 2
    if args in (["-h"], ["--help"]):
        sys.stdout.write(__doc__)
        return 0

    report = Report()
    # 索引语义（§13.3）与批量列一致性：本工具自己的第一道闸。
    rows = check_index_semantics(report)
    check_batch_columns(report, rows)
    registered = provenance_paths()
    indexed = [row["case"] for row in rows]
    # 单例 real- 用例 → 素材相对路径(与 gen_real_cases.py 共用同一张表)
    from gen_real_cases import DEDUPE, SINGLES  # noqa: E402  只在 main 里用

    single_sources = dict(SINGLES)
    single_sources.update(dict(DEDUPE))

    # 1) 索引 ↔ 目录:两个方向都查
    for row in rows:
        report.check(case_dir(row["case"]).is_dir(), row["case"], "用例目录不存在")
    top = sorted(item.name for item in CASES_DIR.iterdir() if item.is_dir())
    negative = sorted(item.name for item in (CASES_DIR / "negative").iterdir()
                      if item.is_dir()) if (CASES_DIR / "negative").is_dir() else []
    on_disk = set(name for name in top if name != "negative")
    on_disk.update("negative/" + name for name in negative)
    extra = sorted(on_disk - set(indexed))
    report.check(not extra, "-", "cases/ 下有索引里没有的目录: %r" % (extra,))

    if selected:
        rows = [row for row in rows if row["case"] in selected]
        if not rows:
            sys.stderr.write("没有匹配的用例\n")
            return 2

    # 2) 逐条按形态判定
    counters = {"round-trip": 0, "render-only": 0, "negative": 0,
                "suite": 0, "members": 0, "defect-members": 0}
    for row in rows:
        case = row["case"]
        if not case_dir(case).is_dir():
            continue
        if row.get("form") == "batch":
            counters["suite"] += 1
            # 成员数优先用硬编码表（独立期望），表里没有就从 manifest 行数推出来
            # ——否则新增一条批量用例会直接 KeyError，而"空套件"反而不报错。
            declared = SUITE_SIZES.get(case) or _manifest_row_count(case_dir(case))
            result = verify_suite(report, case, declared, registered)
            if result:
                counters["members"] += result[0] + result[1]
                counters["defect-members"] += result[1]
        elif case.startswith(ROUND_TRIP_PREFIXES):
            counters["round-trip"] += 1
            verify_round_trip(report, case, {
                "source": row["source"], "case": case, "colors": row["colors"],
                "encoding": row["encoding"], "size": row["size"],
                "samples_path": single_sources.get(case),
            })
        elif case.startswith(RENDER_PREFIXES):
            counters["render-only"] += 1
            verify_render_only(report, case)
        elif case.startswith("negative/"):
            counters["negative"] += 1
            verify_negative(report, case, row)

    # 3) 生成型用例:配方是否存在(内容与指纹由 gen_cases.py --check 判定)
    for row in rows:
        if row["input"] != "generated":
            continue
        notes = case_dir(row["case"]) / "notes.md"
        has = notes.is_file() and "## recipe" in notes.read_text(encoding="utf-8")
        report.check(has, row["case"], "生成型用例缺 `## recipe`")

    print("用例索引 %d 条" % len(rows))
    print("  往返正例单例 %d 条" % counters["round-trip"])
    print("  仅渲染正例   %d 条" % counters["render-only"])
    print("  负例         %d 条" % counters["negative"])
    print("  批量用例     %d 条,成员 %d 个(其中已知缺陷成员 %d 个)"
          % (counters["suite"], counters["members"], counters["defect-members"]))
    print("断言 %d 条,问题 %d 条" % (report.checks, len(report.problems)))
    if report.problems:
        for case, message in report.problems[:60]:
            print("[FAIL] %s: %s" % (case, message))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
