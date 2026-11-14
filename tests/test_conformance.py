"""一致性语料的回归测试(规范 §13.1–§13.4、README §2.1a)。

本文件只做**抽取侧**能判定的部分:

1. 用例目录完整(缺文件必须失败,不许静默跳过——空的用例目录会让"跑全部用例"
   假性通过,比没有用例更危险);
2. ``expected.pixels.sha256`` 与解码出的 RGBA8 流一致;
3. 抽取器输出与 ``expected.pxl`` **逐字节相同**;
4. ``expected.pxl`` 自身合规(UTF-8 无 BOM、LF、末尾恰一个换行、无行尾空白);
5. **压缩版与逐像素版渲染后逐像素相同**(`AGENTS.md` 契约 5 的判定方式);
6. 抽取器两跑零 diff(§9)。

回渲判定用 ``tests/pxl_oracle.py``——那是按规范另写的独立实现,不与抽取器共享代码。
"""

import binascii
import functools
import hashlib
import json
import os
import shutil
import random
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

from importer import extract_png
from importer.compress import build_ops
from importer.png import decode_png
from importer.rgba import to_rgba8
from pxl_oracle import render_pxl_text

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = ROOT / "spec" / "conformance"
CASES_TSV = CONFORMANCE / "CASES.tsv"
CASES_DIR = CONFORMANCE / "cases"
GEN_SYNTHETIC = CONFORMANCE / "tools" / "gen_synthetic.py"

# `png_ref` / `pxl_extract_ref` 是与 `importer` 零共享代码的独立实现,用来给黄金
# 文本提供第二条判定路径。`png_ref` 在 tools/ 下,需要先把该目录塞进 sys.path。
TOOLS_DIR = CONFORMANCE / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import png_ref  # noqa: E402  (需要先补 sys.path)
import pxl_extract_ref  # noqa: E402

#: 往返正例的必需文件(§13.1)。
ROUND_TRIP_FILES = (
    "input.png",
    "expected.pxl",
    "expected.pixels.sha256",
    "notes.md",
)


#: `CASES.tsv` 的列定义(README §3:八列,不得留空)。
COLUMNS = ("case", "source", "form", "size", "colors", "encoding", "covers", "input")

#: 依赖逐字节 LF 的语料文件。``input.pxl`` **故意排除**:`op-lenient-bom-crlf`
#: 要求 CRLF + BOM,那是 §3.8 接受集的一部分(见 `.gitattributes` 的 `-text` 例外)。
LF_PATTERNS = ("expected.pxl", "expected.error", "manifest.tsv")

#: §13.6 的生成型阈值:输入超过 4 MiB 才允许不入库。
FOUR_MIB = 4 * 1024 * 1024


def _crlf_fixture_problems(payload):
    """`op-lenient-bom-crlf/input.pxl` 的形态判定,返回问题清单(空 = 合规)。

    `CASES.tsv` 对该用例的要求是「带 UTF-8 BOM、**所有**换行为 CRLF、结尾无换行符」。
    抽成函数是为了能在用例未落地时也直接喂样本验证规则本身。
    """
    problems = []
    if not payload.startswith(b"\xef\xbb\xbf"):
        problems.append("缺少 UTF-8 BOM")
    if b"\r\n" not in payload:
        problems.append("没有 CRLF(检查 .gitattributes 的 -text 例外)")
    remainder = payload.replace(b"\r\n", b"")
    if b"\n" in remainder:
        problems.append("混入了裸 LF")
    if b"\r" in remainder:
        problems.append("有孤立的 CR")
    if payload.endswith((b"\n", b"\r")):
        problems.append("结尾还有换行符")
    return problems


def _recipe_problems(case, recipe):
    """§13.6:配方必须声明**超过 4 MiB** 的输入,并带 SHA-256 指纹。"""
    if not isinstance(recipe, dict):
        return [(case, "配方不是 JSON 对象")]
    problems = []
    if recipe.get("expectedInputBytes", 0) <= FOUR_MIB:
        problems.append((case, "expectedInputBytes 未超过 4 MiB,该用例必须入库"))
    if not recipe.get("expectedInputSha256"):
        problems.append((case, "缺少 expectedInputSha256 指纹"))
    return problems


def _read_recipe(notes_path):
    """从 `notes.md` 的 `## recipe` 段落里取出那个 JSON 对象;取不到返回 ``None``。"""
    text = notes_path.read_text(encoding="utf-8")
    index = text.find("## recipe")
    if index < 0:
        return None
    section = text[index:]
    following = section.find("\n## ", 1)
    if following >= 0:
        section = section[:following]
    start = section.find("{")
    stop = section.rfind("}")
    if start < 0 or stop <= start:
        return None
    try:
        return json.loads(section[start : stop + 1])
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def load_cases():
    """读 ``CASES.tsv`` 并做**结构性**校验,返回列名到值的 dict 列表。

    列数不是 8 就直接失败:``dict(zip(header, cells))`` 会把多出来的列静默截断、
    把缺的列静默补空,于是整表左移一列仍然"读得动"——那是索引最危险的一种坏法。
    """
    text = CASES_TSV.read_text(encoding="utf-8")
    lines = [line for line in text.split("\n") if line != ""]
    header = tuple(lines[0].split("\t"))
    if header != COLUMNS:
        raise AssertionError("CASES.tsv 表头与 §3 的八列不一致: %r" % (header,))
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise AssertionError(
                "CASES.tsv 第 %d 行有 %d 列,应为 %d 列"
                % (number, len(cells), len(COLUMNS))
            )
        rows.append(dict(zip(COLUMNS, cells)))
    return tuple(rows)


#: 顶层的往返正例前缀。语料已全量落地(`syn-` + `real-`),这里保留两个前缀是
#: 为了让"目录结构与索引一一对应"那条判据有明确的顶层范围;内容判据走
#: :func:`round_trip_units`,它连 suite 成员一起算。
ROUND_TRIP_PREFIXES = ("syn-", "real-")


def round_trip_cases():
    """**顶层**往返正例目录(按索引顺序)。

    只用于"目录结构与索引一一对应"这类比对顶层名字的判据。
    内容判据请用 :func:`round_trip_units`,它还把 suite 成员算进来。
    """
    return [
        row["case"]
        for row in load_cases()
        if row["case"].startswith(ROUND_TRIP_PREFIXES)
    ]


def round_trip_units():
    """所有需要"抽取零 diff + 像素闭环"的目录,含 `suite/members/<member>`。

    语料全量落地后仍只跑 `syn-` 等于让 10 条 real- 单例与 657 个成员完全绕过 §13.2a
    判据 1/3/4——那不是"本步范围",而是漏测。
    """
    units = []
    for row in load_cases():
        case = row["case"]
        if not case.startswith(("syn-", "real-")):
            continue
        directory = CASES_DIR / case
        if (directory / "input.png").is_file() and (directory / "expected.pxl").is_file():
            units.append(case)
            continue
        members = directory / "members"
        if not members.is_dir():
            continue
        for member in sorted(members.iterdir(), key=lambda item: item.name):
            if (member / "input.png").is_file() and (member / "expected.pxl").is_file():
                units.append("%s/members/%s" % (case, member.name))
    return units


@functools.lru_cache(maxsize=None)
def unit_input(case):
    """一个往返单元的 `input.png` 字节（缓存：同一份文件被多条判据反复读）。"""
    return (CASES_DIR / case / "input.png").read_bytes()


@functools.lru_cache(maxsize=1)
def unique_units():
    """按 **PNG 字节**去重后的往返单元代表（保留索引顺序里第一个出现的名字）。

    只适用于"同字节 ⇒ 同结论"的**算法级**判据（压缩等价、确定性、ancillary 忽略）。
    黄金文本比对必须用全量 :func:`round_trip_units`：每个成员都有自己的 golden 要对。
    """
    seen = {}
    for case in round_trip_units():
        digest = hashlib.sha256(unit_input(case)).hexdigest()
        seen.setdefault(digest, case)
    return tuple(sorted(seen.values()))


#: 三档编码各一个代表 + 一个全局回退（确定性判据只需要这四种形态）。
#:
#: 早先这里写的是 `real-oil`（注释说是 index）与 `syn-ops-fallback`（注释说是 index4），
#: 实际两者都是 **char**——于是"精简"之后再没有一条判据覆盖 index / index4 的重复抽取。
#: 现在按 `expected.pxl` 的实测档位选，并由 :meth:`...test_determinism_reps_cover_all_tiers`
#: 断言三档齐全，注释再写错就会被测试抓住。
DETERMINISM_REPRESENTATIVES = (
    "syn-16x16-mono",             # char 档 + 整图纯色 → fill
    "syn-tier-62",                # index 档（62 色）
    "syn-tier-256",               # index4 档（256 色）
    "syn-ops-fallback",           # ops 超预算 → 全局回退（char，但形态特殊，必须留）
)


def sha256_hex(payload):
    return hashlib.sha256(payload).hexdigest()


class ConformanceSuiteTest(unittest.TestCase):
    """语料自身的完整性与合规性。"""

    def test_index_and_cases_use_lf_only(self):
        """语料是逐字节判定的输入:索引与 golden 文本都不许混进 CR。

        CRLF 不只难看:`csv` 以 ``newline=""`` 读时会把 ``\\r`` 留在最后一列,
        于是 `input` 列的值变成 ``committed\\r``、取值域校验全部失配。
        """
        offenders = []
        scanned = 0
        targets = [CASES_TSV]
        for pattern in LF_PATTERNS:
            targets.extend(sorted(CASES_DIR.rglob(pattern)))
        for path in targets:
            scanned += 1
            if b"\r" in path.read_bytes():
                offenders.append(str(path.relative_to(ROOT)))
        # 防止"扫了个空集合"式通过:索引 + 22 份黄金至少 23 个文件。
        self.assertGreaterEqual(scanned, 23, "扫描文件数异常: %d" % scanned)
        self.assertEqual([], offenders, "含 CR 的文件: %s" % ", ".join(offenders))

    def test_gitattributes_exempts_the_crlf_fixture(self):
        """`.gitattributes` 的 `* text=auto eol=lf` 会抹掉 §3.8 的 CRLF 用例。

        用例目录还没落地,但这条例外是"落地后还剩不剩覆盖"的前提,现在就能查。
        """
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").split("\n")
        rules = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        target = "spec/conformance/cases/op-lenient-bom-crlf/input.pxl"
        exemption = [
            rule
            for rule in rules
            if rule.split(" ")[0] == target and "-text" in rule.split(" ")[1:]
        ]
        self.assertEqual(1, len(exemption), "缺少 %s 的 -text 例外" % target)
        self.assertIn("-eol", exemption[0].split(" ")[1:], "-text 之外还要按掉全局 eol=lf")

    def test_every_indexed_case_is_landed(self):
        """索引里的每一条都必须有目录——"未落地"不再是一个合法状态。

        这条以前把 22/129 写死,用来防止未落地清单静默扩大。语料补齐之后它的角色
        反了过来:任何一条缺目录都直接失败。**空集合守卫也在里面**——索引本身为空
        时这条必须红,而不是因为"没有反例"而绿。
        """
        indexed = [row["case"] for row in load_cases()]
        self.assertGreater(len(indexed), 0, "索引是空的")
        landed = [case for case in indexed if (CASES_DIR / case).is_dir()]
        landed_set = set(landed)
        unlanded = [case for case in indexed if case not in landed_set]
        self.assertEqual([], unlanded, "索引里有条目缺目录: %r" % (unlanded[:8],))
        self.assertEqual(len(indexed), len(landed))
        # §13.3：form 只能是 single / batch；负例永远是 single（§13.1）。
        bad_form = [row["case"] for row in load_cases()
                    if row["form"] not in ("single", "batch")]
        self.assertEqual([], bad_form, "form 取值必须是 single / batch: %r" % (bad_form[:5],))
        negative_batch = [row["case"] for row in load_cases()
                          if row["case"].startswith("negative/") and row["form"] == "batch"]
        self.assertEqual([], negative_batch, "负例的 form 必须是 single（§13.1）")
        # 目录反过来也不能多。判据必须是**结构**的，不能靠"这个目录里有没有 notes.md"
        # 这种启发式：`op-line-bresenham/members/member-one/notes.md` 这种真实嵌套
        # 会被启发式整体漏掉（`members/` 那层没有 notes.md 就不成候选，成员目录那层
        # 又被 members 分支判掉）——审查实测过，Python 侧仍绿而 Java 侧会红。
        #
        # 规则：case 级只允许 `cases/<case>` 与 `cases/negative/<case>`；
        # 用例目录里唯一的合法子目录是 `members/`，且仅当**索引声明**它是批量用例；
        # 成员目录是叶子，底下再出现子目录同样失败。
        rows = {row["case"]: row for row in load_cases()}
        problems = []
        case_entries = []
        for item in sorted(CASES_DIR.iterdir()):
            if not item.is_dir():
                continue
            if item.name == "negative":
                for child in sorted(item.iterdir()):
                    if child.is_dir():
                        case_entries.append(("negative/" + child.name, child))
            else:
                case_entries.append((item.name, item))
        for case, directory in case_entries:
            if case not in landed_set:
                problems.append("%s 不在索引中" % case)
                continue
            row = rows[case]
            # 只看索引的 form 列（规范 §13.3）——不看目录形状、也不看 covers 文字。
            declared_batch = row["form"] == "batch"
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                if child.name == "members" and declared_batch:
                    for member in sorted(child.iterdir()):
                        if not member.is_dir():
                            continue
                        for grand in member.iterdir():
                            if grand.is_dir():
                                problems.append("%s/members/%s 里有多余子目录 %s"
                                                % (case, member.name, grand.name))
                    continue
                problems.append("%s 里有多余子目录 %s" % (case, child.name))
        self.assertEqual([], problems, "用例目录结构不合 §13.1/§13.7: %r" % (problems[:8],))

    def test_crlf_fixture_rules_bite(self):
        """`op-lenient-bom-crlf` 的形态判定必须自己也测得动。

        用例目录尚未落地,`test_deliberate_crlf_fixture_is_exempt` 会 skip —— 如果不
        给判定逻辑单独喂样本,"断言写对了但永不执行"就会被当成覆盖(这正是审查指出
        的问题)。这里直接喂四种形态,证明规则真的能分辨。
        """
        good = b"\xef\xbb\xbf{\r\n\"a\": 1\r\n}"
        self.assertEqual([], _crlf_fixture_problems(good))
        self.assertEqual(["缺少 UTF-8 BOM"], _crlf_fixture_problems(good[len(b"\xef\xbb\xbf"):]))
        self.assertEqual(["混入了裸 LF"], _crlf_fixture_problems(b"\xef\xbb\xbf{\r\n\"a\": 1\n}"))
        self.assertEqual(["结尾还有换行符"], _crlf_fixture_problems(good + b"\r\n"))
        self.assertEqual(
            ["没有 CRLF(检查 .gitattributes 的 -text 例外)", "混入了裸 LF"],
            _crlf_fixture_problems(b"\xef\xbb\xbf{\n\"a\": 1}"),
        )

    def test_deliberate_crlf_fixture_is_exempt(self):
        """唯一允许 CRLF 的地方是 §3.8 的接受集用例;用例落地后按同一规则判定。"""
        lenient = CASES_DIR / "op-lenient-bom-crlf" / "input.pxl"
        if not lenient.is_file():
            self.skipTest("op-lenient-bom-crlf 尚未落地(规则本身由 test_crlf_fixture_rules_bite 覆盖)")
        self.assertEqual([], _crlf_fixture_problems(lenient.read_bytes()))

    def test_landed_dirs_match_the_index(self):
        """索引与已落地目录必须一一对应,两个方向都查。

        只校验"已落地的都在索引里"会让"索引里有、目录却没有"永远静默通过——
        而 `CASES.tsv` 现在就把尚未落地的 `real-` / `op-` / `blend-` / `negative/`
        写成了 `committed`。本步的判定范围是 `syn-`,故在此范围内做双向相等。
        """
        indexed = sorted(round_trip_cases())
        landed = sorted(
            item.name
            for item in CASES_DIR.iterdir()
            if item.is_dir() and item.name.startswith(ROUND_TRIP_PREFIXES)
        )
        # 双向相等在两边同时为空时也成立,所以"非空"必须单独断言。
        self.assertTrue(indexed, "CASES.tsv 里没有任何 syn- 行")
        self.assertTrue(landed, "cases/ 下没有任何 syn- 目录")
        self.assertEqual(indexed, landed)

    def test_every_round_trip_case_has_all_files(self):
        missing = []
        for case in round_trip_units():
            for name in ROUND_TRIP_FILES:
                if not (CASES_DIR / case / name).is_file():
                    missing.append("%s/%s" % (case, name))
        self.assertEqual([], missing, "往返正例缺文件: %s" % ", ".join(missing))

    def test_expected_pxl_is_canonical_bytes(self):
        """§3.1 / §3.7:UTF-8 无 BOM、LF 行尾、末尾恰一个换行、无行尾空白、无制表符缩进。"""
        problems = []
        for case in round_trip_units():
            path = CASES_DIR / case / "expected.pxl"
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if payload.startswith(b"\xef\xbb\xbf"):
                problems.append("%s: 带 BOM" % case)
            if b"\r" in payload:
                problems.append("%s: 含 CR" % case)
            if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
                problems.append("%s: 末尾不是恰一个 LF" % case)
            for number, line in enumerate(payload.split(b"\n")[:-1], start=1):
                if line != line.rstrip():
                    problems.append("%s:%d: 行尾有空白" % (case, number))
                if b"\t" in line:
                    problems.append("%s:%d: 含制表符" % (case, number))
                indent = len(line) - len(line.lstrip(b" "))
                if indent % 2 != 0:
                    problems.append("%s:%d: 缩进不是 2 的倍数" % (case, number))
        self.assertEqual([], problems, "; ".join(problems))


class IndexSchemaTest(unittest.TestCase):
    """`CASES.tsv` 的取值域校验(README §4 第 7 条:不只看列数,要验取值域)。"""

    ENCODING_ORDER = {"char": 0, "index": 1, "index4": 2}

    def _cases(self):
        return load_cases()

    def test_source_domain(self):
        bad = [r["case"] for r in self._cases() if r["source"] not in ("synthetic", "real")]
        self.assertEqual([], bad, "source 取值域非法")

    def test_input_domain(self):
        bad = [
            r["case"]
            for r in self._cases()
            if r["input"] not in ("committed", "generated")
        ]
        self.assertEqual([], bad, "input 取值域非法")

    def test_size_shape(self):
        # §13.3：`WxH` / `Wx(Hlo..Hhi)` / `-`。括号里**不允许**自由说明。
        allowed = re.compile(r"^(\d+x\d+|\d+x\(\d+\.\.\d+\)|-)$")
        bad = [r["case"] for r in self._cases() if not allowed.match(r["size"])]
        self.assertEqual([], bad, "size 形状非法")

    def test_colors_shape(self):
        allowed = re.compile(r"^(\d+|varying|-)$")
        bad = [r["case"] for r in self._cases() if not allowed.match(r["colors"])]
        self.assertEqual([], bad, "colors 形状非法")

    def test_encoding_shape(self):
        single = re.compile(r"^(char|index|index4|-)$")
        span = re.compile(r"^(char|index|index4)\.\.(char|index|index4)$")
        problems = []
        for row in self._cases():
            value = row["encoding"]
            if single.match(value):
                continue
            matched = span.match(value)
            if not matched:
                problems.append((row["case"], value, "形状非法"))
            elif self.ENCODING_ORDER[matched.group(1)] > self.ENCODING_ORDER[matched.group(2)]:
                problems.append((row["case"], value, "区间左端比右端宽"))
        self.assertEqual([], problems)

    def test_case_names_are_unique_and_shaped(self):
        positive = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
        negative = re.compile(r"^negative/neg-[a-z0-9]+(-[a-z0-9]+)*$")
        problems = []
        seen = set()
        for row in self._cases():
            case = row["case"]
            if case in seen:
                problems.append((case, "重名"))
            seen.add(case)
            pattern = negative if case.startswith("negative/") else positive
            if not pattern.match(case):
                problems.append((case, "命名不合规"))
        self.assertEqual([], problems)

    def test_index_columns_match_the_landed_cases(self):
        """索引的 `colors` / `encoding` / `size` 必须与落地用例的实际内容一致。

        只做正则校验等于"格式对就算对":把 `syn-16x16-tricolor` 的 colors 改成 300、
        encoding 改成 index4、size 改成 32x64,`IndexSchemaTest` 全都照样通过。
        """
        checked = 0
        for row in load_cases():
            case = row["case"]
            golden = CASES_DIR / case / "expected.pxl"
            source = CASES_DIR / case / "input.png"
            hand_written = CASES_DIR / case / "input.pxl"
            if case.startswith("negative/"):
                continue   # 负例的 input.pxl 本来就不是合法 JSON
            if golden.is_file():
                document = json.loads(golden.read_text(encoding="utf-8"))
            elif hand_written.is_file():
                # 仅渲染正例没有 expected.pxl，但索引三列同样是"事实"，一样要核对——
                # 否则把 op-pixel 的 size 改成 99x99，两侧门禁都不会红。
                # 用 utf-8-sig 读：`op-lenient-bom-crlf` 的 input.pxl 故意带 BOM（§3.8）。
                document = json.loads(hand_written.read_text(encoding="utf-8-sig"))
            else:
                continue
            checked += 1
            image = decode_png(source.read_bytes()) if source.is_file() else None
            with self.subTest(case=case):
                self.assertEqual(str(len(document["palette"])), row["colors"],
                                 "%s: colors 列与 palette 长度不符" % case)
                self.assertEqual(document["encoding"], row["encoding"],
                                 "%s: encoding 列与文档不符" % case)
                document_size = "%dx%d" % (document["width"], document["height"])
                self.assertEqual(document_size, row["size"],
                                 "%s: size 列与文档尺寸不符" % case)
                if image is not None:
                    self.assertEqual("%dx%d" % (image.width, image.height), row["size"],
                                     "%s: size 列与 input.png 尺寸不符" % case)
        # 有 expected.pxl + input.png 的用例必须**全部**参与:漏掉一条就等于少验一份黄金。
        # 期望值是算出来的,不是写死的数字——写死过一次,语料一补齐它就变成了噪音。
        # 期望值**独立于循环取**:直接从文件系统数"有 input.png + expected.pxl 的顶层用例目录"。
        # 若两边用同一个过滤器推导,这条断言就永远是恒真式(踩过:第一版就是这样)。
        # 独立于循环取：从文件系统数"有 expected.pxl 或 input.pxl、且不在 negative/ 下"
        # 的正例目录（往返 29 + 仅渲染 23）。用同一个过滤器推导会变成恒真式。
        on_disk = sorted(
            item.name
            for item in CASES_DIR.iterdir()
            if item.is_dir()
            and ((item / "expected.pxl").is_file() or (item / "input.pxl").is_file())
        )
        self.assertGreater(len(on_disk), 0, "一条正例都没被检查")
        self.assertEqual(len(on_disk), checked, "有 %d 条往返正例,只检查了 %d 条"
                         % (len(on_disk), checked))

    def test_negative_rows_declare_code_and_side(self):
        # current 的错误码止于 `PXL012`(Extension §11.1):PXL001..PXL006 属 core,
        # PXL007..PXL012 属 Extension,负例行两类都会出现。
        allowed = re.compile(r"^PXL(?:00[1-9]|01[0-2])\((渲染侧|抽取侧)\):")
        problems = [
            (r["case"], r["covers"])
            for r in self._cases()
            if r["case"].startswith("negative/") and not allowed.match(r["covers"])
        ]
        self.assertEqual([], problems, "负例行必须以 PXL00N(渲染侧|抽取侧): 开头")

    def test_positive_rows_do_not_use_dash(self):
        """§13.3：`-` 只留给负例。正例行三列都必须给出可校验的值。

        否则批量行三列一起写 `-` 就能同时免责（审查实测过）。Java 侧已有同一条。
        """
        problems = []
        for row in load_cases():
            if row["case"].startswith("negative/"):
                continue
            for column in ("size", "colors", "encoding"):
                if row[column] == "-":
                    problems.append((row["case"], column))
        self.assertEqual([], problems, "正例的 size/colors/encoding 不得写 `-`: %r"
                         % (problems[:5],))

    def test_negative_rows_use_dash_size_and_colors(self):
        """§13.3：负例没有 expected.pxl，`size` / `colors` 在那里无法校验，一律写 `-`。"""
        problems = [
            (row["case"], row["size"], row["colors"])
            for row in load_cases()
            if row["case"].startswith("negative/")
            and (row["size"] != "-" or row["colors"] != "-")
        ]
        self.assertEqual([], problems, "负例的 size/colors 必须写 -: %r" % (problems[:5],))

    def test_negative_rows_use_dash_encoding(self):
        bad = [
            r["case"]
            for r in self._cases()
            if r["case"].startswith("negative/") and r["encoding"] != "-"
        ]
        self.assertEqual([], bad, "负例行的 encoding 必须写 -")

    def test_landed_negative_rows_match_their_declared_side(self):
        """§13.1:负例的受测侧由**目录里实际存在哪个输入文件**决定。"""
        problems = []
        checked = []
        for row in self._cases():
            case = row["case"]
            if not case.startswith("negative/"):
                continue
            directory = CASES_DIR / case
            if not directory.is_dir():
                continue
            checked.append(case)
            side = re.match(r"^PXL(?:00[1-9]|01[0-2])\((渲染侧|抽取侧)\):", row["covers"]).group(1)
            has_pxl = (directory / "input.pxl").is_file()
            has_png = (directory / "input.png").is_file()
            if has_pxl and has_png:
                problems.append((case, "input.pxl 与 input.png 互斥"))
            elif side == "渲染侧" and not has_pxl:
                problems.append((case, "声明渲染侧却没有 input.pxl"))
            elif side == "抽取侧" and not has_png:
                problems.append((case, "声明抽取侧却没有 input.png"))
        self.assertEqual([], problems)
        # 空集合守卫:负例必须真的被检查过,不能"一条都没落地所以通过"。
        self.assertGreater(len(checked), 0, "一条已落地的负例都没被检查")

    def test_suite_members_are_leaves(self):
        """§13.7:成员目录是叶子。底下再套子目录，两侧门禁都不会去看它。"""
        problems = []
        for row in load_cases():
            case = row["case"]
            members = CASES_DIR / case / "members"
            if not members.is_dir():
                continue
            for member in members.iterdir():
                if not member.is_dir():
                    continue
                for child in member.iterdir():
                    if child.is_dir():
                        problems.append(
                            "%s/members/%s/%s" % (case, member.name, child.name)
                        )
        self.assertEqual([], problems, "成员目录下出现了子目录: %r" % (problems[:5],))

    def test_batch_form_needs_index_declaration(self):
        """`members/` 与 `manifest.tsv` 的准入必须由**索引声明**决定，不由文件存在决定.

        否则往任意用例目录放一个空 `manifest.tsv`，就能把整棵 `members/` 子树
        变成无人校验区(审查第五轮实测:全量门禁仍然 FAILURES=0)。
        """
        problems = []
        for row in load_cases():
            case = row["case"]
            directory = CASES_DIR / case
            # 判据落在索引上（投一个空 manifest.tsv 就能绕过文件存在性检查），
            # 但负例永远是单缺陷用例（§13.1），它们引用 §13.7 只是引用。
            declared = row["form"] == "batch"
            has_manifest = (directory / "manifest.tsv").is_file()
            if declared != has_manifest:
                problems.append((case, declared, has_manifest))
        self.assertEqual([], problems, "索引声明与 manifest.tsv 不一致: %r" % (problems[:5],))
    def test_batch_suites_match_their_manifest(self):
        """§13.7：批量用例的 manifest、成员目录、成员文件必须逐项对上。

        此前 Python 门禁对批量用例**一条判据都没有**：删掉
        `real-fluid-source-255` 的一个成员目录，`-p test_conformance.py` 仍然
        38 tests OK（Java harness 会红）。单跑 `AGENTS.md` 契约 2(确定性)列的那条 python
        命令时这就是假绿。
        """
        problems = []
        checked = 0
        for row in load_cases():
            case = row["case"]
            if row["form"] != "batch":
                continue
            suite = CASES_DIR / case
            manifest = suite / "manifest.tsv"
            members = suite / "members"
            if not manifest.is_file():
                problems.append("%s：缺 manifest.tsv" % case)
                continue
            if not members.is_dir():
                problems.append("%s：缺 members/" % case)
                continue
            lines = [
                line for line in manifest.read_text(encoding="utf-8").split("\n") if line
            ]
            if not lines or lines[0] != "member\tinput\tencoding\tcolors":
                problems.append("%s：manifest 表头不对" % case)
                continue
            rows = [line.split("\t") for line in lines[1:]]
            if not rows:
                problems.append("%s：批量用例没有任何成员（空集合不算通过）" % case)
                continue
            names = [cells[0] for cells in rows]
            if len(set(names)) != len(names):
                problems.append("%s：manifest 里成员名重复" % case)
            on_disk = sorted(item.name for item in members.iterdir() if item.is_dir())
            if sorted(names) != on_disk:
                problems.append("%s：manifest 成员 %r 与目录 %r 不一致"
                                % (case, sorted(names)[:4], on_disk[:4]))
            for cells in rows:
                if len(cells) != 4:
                    problems.append("%s：manifest 有一行不是 4 列" % case)
                    continue
                member = cells[0]
                if not cells[1].startswith("sample:"):
                    problems.append("%s/%s：input 列缺 sample: 前缀" % (case, member))
                    continue
                member_dir = members / member
                if not (member_dir / "input.png").is_file():
                    problems.append("%s/%s：缺 input.png" % (case, member))
                    continue
                if not (member_dir / "expected.pxl").is_file() and \
                        not (member_dir / "expected.error").is_file():
                    problems.append("%s/%s：既无 expected.pxl 也无 expected.error"
                                    % (case, member))
                if not (member_dir / "notes.md").is_file():
                    problems.append("%s/%s：缺 notes.md" % (case, member))
                checked += 1
        self.assertGreater(checked, 0, "一条批量成员都没被检查（空集合）")
        self.assertEqual([], problems, "批量用例与 manifest 不一致: %r" % (problems[:6],))

    def test_index_columns_match_batch_members(self):
        """§13.3：批量行的 `size` / `colors` / `encoding` 必须与成员实际内容相容。

        Java 侧早就有这条（`checkBatchColumns`），Python 侧完全没有：把
        `real-fluid-source-255` 的 size 改成 `16x(16..100)`（成员实为 16..512），
        `python -m unittest` 与 `python_ref_verify` 都是绿的。规则与 Java 一致：
        宽度必须相等；高度落在闭区间（或与单值相等）；`colors=varying` 与
        `encoding=区间` 是放宽写法，写单值时要求**全部成员一致**。
        """
        problems = []
        checked = 0
        order = ["char", "index", "index4"]
        for row in load_cases():
            if row["form"] != "batch":
                continue
            case = row["case"]
            members = CASES_DIR / case / "members"
            if not members.is_dir():
                problems.append("%s：form=batch 却没有 members/" % case)
                continue
            sizes = []
            encodings = []
            palettes = []
            for member in sorted(members.iterdir()):
                if not member.is_dir():
                    continue
                golden = member / "expected.pxl"
                if not golden.is_file():
                    continue
                document = json.loads(golden.read_text(encoding="utf-8"))
                sizes.append((int(document["width"]), int(document["height"])))
                encodings.append(document["encoding"])
                palettes.append(len(document["palette"]))
            if not sizes:
                problems.append("%s：没有一个成员带 expected.pxl" % case)
                continue
            checked += 1
            size_column = row["size"]
            ranged = re.match(r"^(\d+)x\((\d+)\.\.(\d+)\)$", size_column)
            plain = re.match(r"^(\d+)x(\d+)$", size_column)
            if ranged:
                width, low, high = (int(ranged.group(1)), int(ranged.group(2)),
                                    int(ranged.group(3)))
                for actual in sizes:
                    if actual[0] != width:
                        problems.append("%s：成员宽 %d 与 size 列的 %d 不符"
                                        % (case, actual[0], width))
                        break
                    if not low <= actual[1] <= high:
                        problems.append("%s：成员高 %d 不在 size 列的 %d..%d 内"
                                        % (case, actual[1], low, high))
                        break
            elif plain:
                expected_size = (int(plain.group(1)), int(plain.group(2)))
                for actual in sizes:
                    if actual != expected_size:
                        problems.append("%s：成员尺寸 %dx%d 与 size 列的 %s 不符"
                                        % (case, actual[0], actual[1], size_column))
                        break
            else:
                problems.append("%s：批量行的 size 形式非法: %r" % (case, size_column))
            colors_column = row["colors"]
            if colors_column == "-":
                problems.append("%s：批量行的 colors 不得写 `-`（`-` 只留给负例）" % case)
            elif colors_column != "varying":
                if any(str(value) != colors_column for value in palettes):
                    problems.append("%s：colors 列写单值 %s，但成员 palette 长度不一致"
                                    % (case, colors_column))
            encoding_column = row["encoding"]
            ranged_enc = re.match(r"^(char|index|index4)\.\.(char|index|index4)$",
                                  encoding_column)
            if ranged_enc:
                low = order.index(ranged_enc.group(1))
                high = order.index(ranged_enc.group(2))
                for value in encodings:
                    if not low <= order.index(value) <= high:
                        problems.append("%s：成员档位 %s 不在 encoding 列的 %s 内"
                                        % (case, value, encoding_column))
                        break
            elif encoding_column in order:
                if any(value != encoding_column for value in encodings):
                    problems.append("%s：encoding 列写单值 %s，但成员档位不一致"
                                    % (case, encoding_column))
            else:
                problems.append("%s：批量行的 encoding 形式非法: %r"
                                % (case, encoding_column))
        self.assertGreater(checked, 0, "一条批量行都没被检查")
        self.assertEqual([], problems, "批量列与成员不一致: %r" % (problems[:6],))

    def test_no_empty_cells(self):
        """README §3:八列**不得留空单元格**。"""
        problems = []
        for row in self._cases():
            for column in COLUMNS:
                if row[column].strip() == "":
                    problems.append((row["case"], column))
        self.assertEqual([], problems)

    def test_batch_only_forms_are_confined_to_batch_rows(self):
        """`varying` / `encoding` 区间 / `Wx(...)` 只允许出现在 `form=batch` 的行上。

        规范 §13.3 对这三者的措辞都是"批量行的写法",没有限定 `source=real` 或名字以
        `real-` 开头。早先那条「仅 `real-` 批量用例可用」只写在 README 里(已删),门禁
        却还在执行它,而且用**精确集合**断言——新增一条合法批量用例就会红。
        """
        problems = []
        for row in self._cases():
            forms = []
            if row["colors"] == "varying":
                forms.append("colors=varying")
            if ".." in row["encoding"]:
                forms.append("encoding 区间")
            if "(" in row["size"]:
                forms.append("括号尺寸")
            if not forms:
                continue
            if row["form"] != "batch":
                problems.append((row["case"], "、".join(forms)))
        self.assertEqual([], problems,
                         "批量写法只能用于 form=batch 的行: %r" % (problems[:5],))

    def test_recipe_presence_matches_the_input_column(self):
        """§13.6:`generated` 的行必须有 `## recipe`,`committed` 的行不得有。"""
        problems = []
        missing_dirs = []
        for row in self._cases():
            notes = CASES_DIR / row["case"] / "notes.md"
            has_recipe = notes.is_file() and "## recipe" in notes.read_text(encoding="utf-8")
            if row["input"] == "generated":
                if not notes.is_file():
                    missing_dirs.append(row["case"])
                elif not has_recipe:
                    problems.append((row["case"], "generated 却缺 ## recipe"))
            elif has_recipe:
                problems.append((row["case"], "committed 却带 ## recipe"))
        self.assertEqual([], problems)
        # 生成型输入尚未落地是已知状态——允许缺目录,但必须数得出是哪一条,
        # 否则"新加一条 generated 却忘了写配方"会被静默放过。
        self.assertEqual([], missing_dirs, "generated 行的目录必须存在(目录可以没有输入文件)")

    def test_generated_rows_must_declare_over_four_mib_input(self):
        """§13.6:只有输入**超过 4 MiB** 的用例才允许走生成路径。

        "不超过 4 MiB 的必须入库,没有例外"——所以配方里的 `expectedInputBytes`
        必须真的越过阈值,否则这条行把一份本该入库的输入藏进了生成路径。
        """
        unlanded = []
        for row in self._cases():
            if row["input"] != "generated":
                continue
            notes = CASES_DIR / row["case"] / "notes.md"
            if not notes.is_file():
                unlanded.append(row["case"])
                continue
            recipe = _read_recipe(notes)
            self.assertIsNotNone(recipe, "%s 的 ## recipe 解析不出 JSON" % row["case"])
            self.assertEqual([], _recipe_problems(row["case"], recipe))
        # 唯一尚未落地的一条必须被显式登记;新增 generated 行会立刻在这里暴露。
        self.assertEqual([], unlanded, "所有 generated 行都必须有配方")

    def test_recipe_parser_and_threshold_rules_bite(self):
        """`_read_recipe` 与 4 MiB 判据必须自己也测得动。

        当前唯一一条 `generated` 行未落地,上面那个"遍历真实配方"的循环一次都不执行
        —— 于是这两段逻辑在交付状态下零覆盖。这里直接喂人造 notes.md 与配方。
        """
        self.assertIsNone(_read_recipe(CASES_TSV))          # 没有 ## recipe 段落
        scratch = CASES_DIR.parent / "tools" / "_recipe_probe.md"
        try:
            # 注意:`Path.write_text` 的 `newline` 参数是 3.10 才有的,3.8/3.9 上会
            # TypeError。这里用 open(...) 显式指定,别为了少两行把 3.8 弄炸。
            with open(str(scratch), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    "# 用例\n\n文字。\n\n## recipe\n\n```json\n"
                    '{"generator": "x", "params": {}, "expectedInputBytes": 4194305,'
                    ' "expectedInputSha256": "abc"}\n```\n\n## aggregate\n\n无关内容。\n'
                )
            recipe = _read_recipe(scratch)
        finally:
            scratch.unlink()
        self.assertEqual(4194305, recipe["expectedInputBytes"])
        self.assertEqual([], _recipe_problems("case", recipe))

        # 恰好 4 MiB 不算"超过";缺指纹不合格;非对象不合格。
        self.assertEqual(
            [("case", "expectedInputBytes 未超过 4 MiB,该用例必须入库")],
            _recipe_problems("case", {"expectedInputBytes": FOUR_MIB, "expectedInputSha256": "abc"}),
        )
        self.assertEqual(
            [("case", "缺少 expectedInputSha256 指纹")],
            _recipe_problems("case", {"expectedInputBytes": FOUR_MIB + 1, "expectedInputSha256": ""}),
        )
        self.assertEqual([("case", "配方不是 JSON 对象")], _recipe_problems("case", None))


class RoundTripTest(unittest.TestCase):
    """§13.2a 里抽取侧能判定的四条:像素摘要、抽取一致、压缩⇄逐像素、确定性。"""

    def _paths(self, case):
        directory = CASES_DIR / case
        return (
            directory / "input.png",
            directory / "expected.pxl",
            directory / "expected.pixels.sha256",
        )

    def test_pixels_sha256_matches_decoder(self):
        for case in round_trip_units():
            source, _expected, digest_file = self._paths(case)
            if not (source.is_file() and digest_file.is_file()):
                continue
            with self.subTest(case=case):
                image = decode_png(source.read_bytes())
                actual = sha256_hex(to_rgba8(image))
                expected = digest_file.read_text(encoding="utf-8").strip()
                self.assertEqual(expected, actual, "%s 的像素摘要不符" % case)

    def test_extract_matches_expected_pxl(self):
        for case in round_trip_units():
            source, expected_file, _digest = self._paths(case)
            if not (source.is_file() and expected_file.is_file()):
                continue
            with self.subTest(case=case):
                actual = extract_png(source.read_bytes())
                expected = expected_file.read_bytes().decode("utf-8")
                if actual != expected:
                    self.fail(
                        "%s 抽取结果与 expected.pxl 不零 diff:\n--- 首个差异 ---\n%s"
                        % (case, _first_difference(expected, actual))
                    )

    def test_compressed_and_raw_render_identically(self):
        """压缩版与逐像素版渲染结果必须逐像素相同(`AGENTS.md` 契约 5)。"""
        # 同字节必然同结论：按 PNG 字节去重后跑代表；黄金比对另由全量判据覆盖。
        for case in unique_units():
            with self.subTest(case=case):
                payload = unit_input(case)
                compressed = render_pxl_text(extract_png(payload))
                per_pixel = render_pxl_text(extract_png(payload, raw=True))
                self.assertEqual(
                    compressed[0], per_pixel[0], "%s: 宽度不同" % case
                )
                self.assertEqual(
                    compressed[1], per_pixel[1], "%s: 高度不同" % case
                )
                self.assertEqual(
                    compressed[2],
                    per_pixel[2],
                    "%s: 压缩版与逐像素版像素不同" % case,
                )

    def test_expected_pxl_renders_to_input_pixels(self):
        """§13.2a 判据 2/3:golden 文本回渲后必须等于输入 PNG 的像素。"""
        for case in round_trip_units():
            source, expected_file, _digest = self._paths(case)
            if not (source.is_file() and expected_file.is_file()):
                continue
            with self.subTest(case=case):
                image = decode_png(source.read_bytes())
                rendered = render_pxl_text(
                    expected_file.read_bytes().decode("utf-8")
                )
                self.assertEqual(image.width, rendered[0], "%s: 宽度不同" % case)
                self.assertEqual(image.height, rendered[1], "%s: 高度不同" % case)
                self.assertEqual(
                    to_rgba8(image),
                    rendered[2],
                    "%s: expected.pxl 回渲结果与 input.png 像素不同" % case,
                )

    def test_determinism_reps_cover_all_tiers(self):
        """代表集必须真的覆盖 char / index / index4 三档。

        这条是对"注释写错了没人发现"的直接防护：代表集里任一用例改档、或有人把某一档
        换成另一档，这里立刻红。
        """
        tiers = {}
        for case in DETERMINISM_REPRESENTATIVES:
            golden = CASES_DIR / case / "expected.pxl"
            self.assertTrue(golden.is_file(), "%s 没有 expected.pxl" % case)
            document = json.loads(golden.read_text(encoding="utf-8"))
            tiers.setdefault(document["encoding"], []).append(case)
        self.assertEqual({"char", "index", "index4"}, set(tiers),
                         "确定性代表集必须覆盖三档: %r" % (tiers,))

    def test_extraction_is_deterministic(self):
        """§9:同一输入两跑,`.pxl` 逐字节相同。"""
        units = list(DETERMINISM_REPRESENTATIVES)
        units.extend(["real-chain-overlay-dedupe/members/copper-chain-overlay",
                      "real-chain-overlay-dedupe/members/wood-chain-overlay"])
        for case in units:
            with self.subTest(case=case):
                payload = unit_input(case)
                first = extract_png(payload)
                second = extract_png(payload)
                self.assertEqual(first, second, "%s 两跑不一致" % case)


class IndependentExtractorTest(unittest.TestCase):
    """黄金文本的独立复核:换一个解码器 + 换一个抽取器,仍然逐字节相同。

    这是唯一能证伪"抽取器错了、黄金跟着错"的手段——`expected.pxl` 由被测实现
    产出,所以必须有一条**不经过它**的路径也能写出同一份规范形式。
    """

    def test_reference_extractor_reproduces_every_golden(self):
        checked = 0
        for case in round_trip_units():
            source = CASES_DIR / case / "input.png"
            golden = CASES_DIR / case / "expected.pxl"
            if not (source.is_file() and golden.is_file()):
                continue
            with self.subTest(case=case):
                width, height, rgba = png_ref.decode_rgba8(source.read_bytes())
                actual = pxl_extract_ref.reference_pxl(rgba, width, height)
                expected = golden.read_bytes().decode("utf-8")
                if actual != expected:
                    self.fail(
                        "%s: 独立抽取器与黄金不一致\n%s"
                        % (case, _first_difference(expected, actual))
                    )
            checked += 1
        # 期望值从**文件系统**独立数出来(递归,含 suite 成员),不与索引推导共用过滤器,
        # 否则这条断言永远是恒真式。
        on_disk = [
            item
            for item in CASES_DIR.rglob("*")
            if item.is_dir()
            and (item / "input.png").is_file()
            and (item / "expected.pxl").is_file()
        ]
        self.assertGreater(len(on_disk), 0, "一条黄金都没被独立复核")
        self.assertEqual(len(on_disk), checked, "独立抽取器漏查了用例")

    def test_reference_ops_agrees_with_implementation_on_random_grids(self):
        """随机 slot 网格上,两套附录 A 实现的 op 序列必须完全一致。

        刻意覆盖 `index` / `index4` 两档:已落地用例里的 `rect` 全都落在 `char`
        档,这个空洞只能靠随机网格补。
        """
        generator = random.Random(20261101)
        compared = 0
        for _ in range(200):
            width = generator.randint(1, 10)
            height = generator.randint(1, 6)
            slots = [generator.choice((0, 1, 2, 3)) for _ in range(width * height)]
            for encoding in ("char", "index", "index4"):
                self.assertEqual(
                    pxl_extract_ref.reference_ops(slots, width, height, encoding),
                    _as_tuples(build_ops(slots, width, height, encoding)),
                    "op 序列不一致: %dx%d %s %r" % (width, height, encoding, slots),
                )
                compared += 1
        self.assertGreaterEqual(compared, 600)

    def test_ops_cap_boundary_is_exact(self):
        """附录 A 第 8 步判的是「**超过** 32768」——恰好 32768 必须保留。

        边界输入:段长恒 3、相邻行反色,于是每行 `W/3` 条 rect、上下不可合并。
        2 行 × 49152 宽 = **32768** 条;3 行 × 32769 宽 = **32769** 条。

        没有这条用例时,把 `len(ops) > OPS_CAP` 写成 `>=` 全套测试仍然全绿
        ——边界方向整个没被钉住。
        """
        def slots_for(width, height):
            return [1 if ((x // 3) + y) % 2 == 0 else 2
                    for y in range(height) for x in range(width)]

        exact = slots_for(49152, 2)
        ops = build_ops(exact, 49152, 2, "char")
        self.assertEqual(32768, len(ops), "恰好 32768 条 rect 不得回退")
        self.assertEqual({"rect"}, {op["op"] for op in ops})
        self.assertEqual(
            pxl_extract_ref.reference_ops(exact, 49152, 2, "char"), _as_tuples(ops)
        )

        over = slots_for(32769, 3)
        ops = build_ops(over, 32769, 3, "char")
        self.assertEqual(1, len(ops), "32769 条必须整层退化")
        self.assertEqual("grid", ops[0]["op"])
        self.assertEqual(
            pxl_extract_ref.reference_ops(over, 32769, 3, "char"), _as_tuples(ops)
        )

    def test_both_implementations_agree_on_the_global_fallback(self):
        """远远超预算(87,380 条 rect)的输入:两套实现都退化为同一条 grid。

        真正的**边界**由 `test_ops_cap_boundary_is_exact` 负责(32768 / 32769);
        这条只钉"回退路径上两套实现逐字段一致"。
        """
        width, height = 65535, 4
        slots = [1 if ((x // 3) + y) % 2 == 0 else 2
                 for y in range(height) for x in range(width)]
        expected = pxl_extract_ref.reference_ops(slots, width, height, "char")
        self.assertEqual([("grid", 0, 0, expected[0][3])], expected)
        self.assertEqual(expected, _as_tuples(build_ops(slots, width, height, "char")))


class CrossProcessDeterminismTest(unittest.TestCase):
    """§9:输出不得依赖哈希表迭代顺序——同进程两跑测不出这一点。"""

    #: 必须写 bytes 到 ``stdout.buffer``:走文本层的话 Windows 会把 "\n" 翻成 "\r\n",
    #: 于是三次比较的差异来自平台的换行翻译,而不是哈希顺序。
    SNIPPET = (
        "import sys;"
        "from importer import extract_png;"
        "sys.stdout.buffer.write(extract_png(sys.stdin.buffer.read()).encode('utf-8'))"
    )

    def test_extraction_is_stable_across_hash_seeds(self):
        source = CASES_DIR / "syn-16x16-tricolor" / "input.png"
        payload = source.read_bytes()
        outputs = set()
        for seed in ("0", "1", "12345"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [sys.executable, "-c", self.SNIPPET],
                input=payload,
                cwd=str(ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(
                0,
                completed.returncode,
                "seed=%s 失败:\n%s" % (seed, completed.stderr.decode("utf-8", "replace")),
            )
            outputs.add(completed.stdout)
        self.assertEqual(
            1, len(outputs), "抽取结果随 PYTHONHASHSEED 变化,说明依赖了哈希迭代顺序"
        )
        self.assertEqual(
            extract_png(payload).encode("utf-8"),
            outputs.pop(),
            "子进程结果与进程内结果不同",
        )


def _as_tuples(ops):
    """把实现的 dict 形状转成 `pxl_extract_ref` 的元组形状,便于直接比较。"""
    converted = []
    for op in ops:
        if op["op"] == "fill":
            converted.append(("fill", op["slot"]))
        elif op["op"] == "rect":
            converted.append(
                ("rect", op["x"], op["y"], op["w"], op["h"], op["slot"])
            )
        elif op["op"] == "grid":
            converted.append(("grid", op["x"], op["y"], tuple(op["grid"])))
        else:
            raise AssertionError("未知 op: %r" % (op["op"],))
    return converted


class AncillaryChunkTest(unittest.TestCase):
    """§8.5:ancillary chunk 一律忽略其内容,存在与否不影响像素与抽取结果。"""

    KEEP = (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND")

    #: ancillary 判据要的是"一张带 ancillary 的真实素材 + 一张合成的"，不是 677 次。
    ANCILLARY_SAMPLES = ("real-oil", "syn-16x16-tricolor")

    def test_stripping_all_ancillary_keeps_extraction_identical(self):
        checked = 0
        for case in self.ANCILLARY_SAMPLES:
            source = CASES_DIR / case / "input.png"
            if not source.is_file():
                continue
            checked += 1
            with self.subTest(case=case):
                payload = source.read_bytes()
                stripped = _join_chunks(
                    [
                        (ctype, body)
                        for ctype, body in _split_chunks(payload)
                        if ctype in self.KEEP
                    ]
                )
                self.assertEqual(
                    extract_png(payload),
                    extract_png(stripped),
                    "%s:剔除 ancillary 后抽取结果变了" % case,
                )
        # 空集合守卫：样本全缺时循环体一次都不执行，判据会静默消失。
        self.assertGreater(checked, 0, "一条 ancillary 样本都没找到")

    def test_garbage_ancillary_payload_is_ignored(self):
        # CRC 正确但内容不是合法 deflate 的 zTXt:§8.5 要求忽略内容,不得试图解析。
        for case in round_trip_units():
            source = CASES_DIR / case / "input.png"
            if not source.is_file():
                continue
            with self.subTest(case=case):
                payload = source.read_bytes()
                chunks = _split_chunks(payload)
                for index, (ctype, _body) in enumerate(chunks):
                    if ctype == b"IDAT":
                        chunks.insert(
                            index, (b"zTXt", b"Comment\x00\x00not-a-deflate-stream")
                        )
                        break
                self.assertEqual(
                    extract_png(payload),
                    extract_png(_join_chunks(chunks)),
                    "%s:垃圾 ancillary 改变了抽取结果" % case,
                )


def _split_chunks(payload):
    """把 PNG 切成 ``[(类型, 数据), ...]``(不校验 CRC,只做搬运)。"""
    if payload[:8] != PNG_SIGNATURE:
        raise AssertionError("不是 PNG")
    chunks = []
    position = 8
    while position < len(payload):
        length = struct.unpack(">I", payload[position : position + 4])[0]
        ctype = payload[position + 4 : position + 8]
        body = payload[position + 8 : position + 8 + length]
        chunks.append((ctype, body))
        position += 12 + length
    return chunks


def _join_chunks(chunks):
    """按 PNG 规则重新拼装并补算 CRC。"""
    out = bytearray(PNG_SIGNATURE)
    for ctype, body in chunks:
        out.extend(struct.pack(">I", len(body)))
        out.extend(ctype)
        out.extend(body)
        out.extend(struct.pack(">I", binascii.crc32(ctype + body) & 0xFFFFFFFF))
    return bytes(out)


class FixtureReproducibleTest(unittest.TestCase):
    """§13.5:合成用例自包含,输入必须能由生成器确定性复现。"""

    def test_expected_pxl_checker_fails_on_missing_input(self):
        """黄金检查器：**好项不能抵消缺输入项**。

        审查实测过：同时检查完好的 `syn-1x1-opaque` 与缺 `input.png` 的用例，旧实现
        打印「一致 1 条，漂移 0 条」并 exit 0。零成功那一条只挡住了"全缺"，挡不住
        "一条好的一条坏的"。
        """
        import importlib.util

        tool = ROOT / "spec" / "conformance" / "tools" / "gen_expected_pxl.py"
        spec = importlib.util.spec_from_file_location("gen_expected_pxl_under_test", str(tool))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        good_dir = CASES_DIR / "syn-1x1-opaque"
        # 系统 temp 在本机沙箱下删不掉（PermissionError），临时树一律放 tests/_sandbox/，
        # 与 test_cli 的做法一致；该目录已 gitignore。
        base = ROOT / "tests" / "_sandbox" / ("expected-pxl-%d" % os.getpid())
        shutil.rmtree(str(base), ignore_errors=True)
        self.addCleanup(shutil.rmtree, str(base), True)

        cases = base / "cases"
        (cases / "syn-good").mkdir(parents=True)
        (cases / "syn-good" / "input.png").write_bytes((good_dir / "input.png").read_bytes())
        (cases / "syn-good" / "expected.pxl").write_bytes(
            (good_dir / "expected.pxl").read_bytes())
        (cases / "syn-broken").mkdir(parents=True)      # 故意没有 input.png
        # 用 write_bytes：`Path.write_text(newline=...)` 是 3.10+ 的，3.8 守卫会（正确地）拦下。
        (base / "CASES.tsv").write_bytes(
            ("case\tsource\tform\tsize\tcolors\tencoding\tcovers\tinput\n"
             "syn-good\tsynthetic\tsingle\t1x1\t1\tchar\tc\tcommitted\n"
             "syn-broken\tsynthetic\tsingle\t1x1\t1\tchar\tc\tcommitted\n"
             ).encode("utf-8"))

        module.CASES_DIR = cases
        module.CASES_TSV = base / "CASES.tsv"
        self.assertNotEqual(0, module.main(["--check", "syn-good", "syn-broken"]),
                            "好项 + 缺输入项 必须失败")
        # **写出模式**同样不得假成功：它也会撞上缺输入，而旧实现照样 exit 0
        # （连"写出 0 份"都算成功）。
        self.assertNotEqual(0, module.main(["syn-good", "syn-broken"]),
                            "写出模式遇到缺输入也必须失败")
        # 对照：只有好项时两种模式都通过。
        self.assertEqual(0, module.main(["--check", "syn-good"]))
        self.assertEqual(0, module.main(["syn-good"]))

    def test_gen_synthetic_verify(self):
        if not GEN_SYNTHETIC.is_file():
            self.fail("缺少生成器 %s(§13.6 固定了入口路径)" % GEN_SYNTHETIC)
        completed = subprocess.run(
            [sys.executable, str(GEN_SYNTHETIC), "--verify"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = completed.stdout.decode("utf-8", "replace")
        self.assertEqual(0, completed.returncode, "生成器 --verify 失败:\n%s" % output)


def _first_difference(expected, actual):
    """定位首个差异,便于读失败信息。"""
    limit = min(len(expected), len(actual))
    for index in range(limit):
        if expected[index] != actual[index]:
            start = max(0, index - 60)
            return "偏移 %d\n期望: %r\n实际: %r" % (
                index,
                expected[start : index + 60],
                actual[start : index + 60],
            )
    if len(expected) != len(actual):
        return "长度不同:期望 %d,实际 %d\n尾部期望: %r\n尾部实际: %r" % (
            len(expected),
            len(actual),
            expected[limit:],
            actual[limit:],
        )
    return "(无差异)"


if __name__ == "__main__":
    unittest.main()
