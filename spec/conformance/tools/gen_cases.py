#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§13.6 生成型用例的固定入口(`CASES.tsv` 里 `input = generated` 的行)。

接口按规范钉死(§13.6「生成器入口」)::

    <py> spec/conformance/tools/gen_cases.py <case-path>     # 在该用例目录下写出输入,已存在则覆盖
    <py> spec/conformance/tools/gen_cases.py --all           # 遍历 input=generated 的所有行
    <py> spec/conformance/tools/gen_cases.py --check         # 不写文件,逐条校验配方

`<case-path>` 是 `CASES.tsv` 首列的**完整相对路径**(如 `negative/neg-doc-too-large`),
不接受只写叶名,也不接受绝对路径。

`--check` 的失败条件(§13.6,任一命中即以非零退出码结束):

1. 输入文件**不存在**(不得因为「没有可检查的文件」而当成通过——空集合通过是这里最容易
   踩的坑);
2. 字节数不等于 `expectedInputBytes`;
3. SHA-256 不等于 `expectedInputSha256`;
4. `input = generated` 的行没有 `## recipe`;
5. `input = committed` 的行却有 `## recipe`(索引自相矛盾)。

当前唯一一条生成型用例是 `negative/neg-doc-too-large`:它要测的是**文档字节上限**
(64 MiB),输入必须超过 64 MiB 才测得到,因此按 §13.6 不入库、按配方生成。
不超过 4 MiB 的用例必须入库,没有例外(§13.6 判定)——
`--check` 会核对 `expectedInputBytes` 是否真的越过了 4 MiB 阈值。

确定性:同一配方任意次运行输出**逐字节**相同;不使用时钟、随机、locale、文件系统遍历
顺序(§9)。只用标准库,可被任意 Python 3.8+ 解释器以文件路径直接运行。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CASES_DIR = TOOLS_DIR.parent / "cases"
CASES_TSV = TOOLS_DIR.parent / "CASES.tsv"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from gen_conformance_cases import layer, serialize  # noqa: E402  §3.7 序列化(同一份实现)

#: §13.6 的阈值:超过 4 MiB 才允许走生成路径。
FOUR_MIB = 4 * 1024 * 1024

#: 文档字节上限(§5.0.2)。
DOC_BYTES_LIMIT = 67108864


# --------------------------------------------------------------------------
# 配方:一个生成型用例一段可执行的构造
# --------------------------------------------------------------------------

def _iter_doc_too_large(params):
    """把 4096 个 sprite(每个 1488 行)按 §3.7 规范形式流式写出来。

    为什么这样构造:`sprites` 项数 4096 与每 sprite 行数 1488 **都合规**
    (§5.0.2 的上界分别是 4096 与 65536),只有**总字节**越界——这正是
    「字节上限必须独立存在」的理由。行数再多就会被 `sprite 行数 <= 65536`
    那条接住,那样测到的就不是字节上限了。

    逐块产出而不是先拼成一个大字符串,是为了把峰值内存压到一块的量级。
    """
    sprite_count = params["sprites"]
    rows_per_sprite = params["rowsPerSprite"]
    head = serialize(params["width"], params["height"], "char",
                     params["palette"], [layer(ops=[])], sprites=None)
    # 含 `sprites` 的头部:把 serialize 产出里 `"layers"` 之前的位置切开,插入 sprites。
    marker = '  "layers": [\n'
    cut = head.index(marker)
    prefix = head[:cut] + '  "sprites": {\n'
    suffix = "  },\n" + head[cut:]

    yield prefix.encode("utf-8")
    rows_comma = ("      \"%s\",\n" % params["rowToken"])
    rows_last = ("      \"%s\"\n" % params["rowToken"])
    for index in range(sprite_count):
        block = ['    "%s": [\n' % (params["namePrefix"] + _pad(index, params["nameDigits"]),)]
        block.extend([rows_comma] * (rows_per_sprite - 1))
        block.append(rows_last)
        block.append("    ]%s\n" % ("," if index != sprite_count - 1 else ""))
        yield "".join(block).encode("utf-8")
    yield suffix.encode("utf-8")


def _pad(index, digits):
    text = str(index)
    return text.rjust(digits, "0")


RECIPES = {
    "negative/neg-doc-too-large": {
        "generator": "sprites-stuffing",
        "params": {
            "width": 1,
            "height": 1,
            "palette": ["FF0000FF"],
            "sprites": 4096,
            "rowsPerSprite": 1488,
            "rowToken": "0",
            "namePrefix": "s",
            "nameDigits": 4,
        },
        "build": _iter_doc_too_large,
    },
}


# --------------------------------------------------------------------------
# 索引与配方读取
# --------------------------------------------------------------------------

COLUMNS = ("case", "source", "form", "size", "colors", "encoding", "covers", "input")


def load_index():
    """读 `CASES.tsv`(八列,制表符分隔)。列数不对就失败,不静默截断。"""
    text = CASES_TSV.read_text(encoding="utf-8")
    lines = [line for line in text.split("\n") if line != ""]
    header = tuple(lines[0].split("\t"))
    if header != COLUMNS:
        raise SystemExit("CASES.tsv 表头与 §3 的八列不一致: %r" % (header,))
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise SystemExit("CASES.tsv 第 %d 行有 %d 列" % (number, len(cells)))
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def read_recipe(case_path):
    """从该用例的 `notes.md` 里取出 `## recipe` 的 JSON 对象;没有则 ``None``。"""
    notes = _case_dir(case_path) / "notes.md"
    if not notes.is_file():
        return None
    text = notes.read_text(encoding="utf-8")
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
    return json.loads(section[start:stop + 1])


def _case_dir(case_path):
    directory = CASES_DIR
    for part in case_path.split("/"):
        directory = directory / part
    return directory


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

def iter_input(case_path, recipe):
    """按配方产出输入文件的字节块(确定性:纯函数,无时钟/随机/locale)。"""
    name = recipe.get("generator")
    for case, spec in RECIPES.items():
        if spec["generator"] != name:
            continue
        params = dict(spec["params"])
        params.update(recipe.get("params") or {})
        return spec["build"](params)
    raise SystemExit("%s 的配方引用了未知生成器: %r" % (case_path, name))


def write_input(case_path, recipe):
    cases_dir = _case_dir(case_path)
    cases_dir.mkdir(parents=True, exist_ok=True)
    target = cases_dir / "input.pxl"
    # 原子写：先写同目录的临时文件再 rename。直接就地写的话，并发跑两个
    # conformance 时另一个会读到半写的文件，`--check` 于是报字节数不符——
    # 那是个与实现无关的假红（审查第八轮实测过）。
    temporary = cases_dir / "input.pxl.tmp"
    size = 0
    with temporary.open("wb") as handle:
        for chunk in iter_input(case_path, recipe):
            handle.write(chunk)
            size += len(chunk)
    os.replace(str(temporary), str(target))
    return size


# --------------------------------------------------------------------------
# 三个入口
# --------------------------------------------------------------------------

def _validate_case_path(case_path):
    if case_path.startswith("/") or ":" in case_path or "\\" in case_path:
        raise SystemExit("不接受绝对路径: %r" % (case_path,))
    if "/" not in case_path:
        raise SystemExit("不接受只写叶名(要写 CASES.tsv 首列的完整相对路径): %r"
                         % (case_path,))


def run_one(case_path):
    _validate_case_path(case_path)
    rows = [row for row in load_index() if row["case"] == case_path]
    if not rows:
        raise SystemExit("%r 不在 CASES.tsv 里" % (case_path,))
    if rows[0]["input"] != "generated":
        raise SystemExit("%r 的 input 列不是 generated(§13.6 判定)" % (case_path,))
    recipe = read_recipe(case_path)
    if recipe is None:
        raise SystemExit("%r 没有 `## recipe`(§13.6 要求必须有)" % (case_path,))
    size = write_input(case_path, recipe)
    print("%s → input.pxl,%d 字节" % (case_path, size))
    return 0


def run_all():
    rows = [row for row in load_index() if row["input"] == "generated"]
    if not rows:
        print("没有 input=generated 的行")
        return 0
    failures = 0
    for row in rows:
        case_path = row["case"]
        recipe = read_recipe(case_path)
        if recipe is None:
            sys.stderr.write("[FAIL] %s:`## recipe` 缺失\n" % case_path)
            failures += 1
            continue
        size = write_input(case_path, recipe)
        print("[ OK ] %s → input.pxl,%d 字节" % (case_path, size))
    if failures:
        sys.stderr.write("失败 %d 条\n" % failures)
        return 1
    print("生成完成:%d 条" % len(rows))
    return 0


def run_check():
    rows = load_index()
    problems = []
    generated = [row for row in rows if row["input"] == "generated"]
    if not generated:
        problems.append((None, "CASES.tsv 里没有 input=generated 的行——"
                               "空集合通过是 §13.6 明确点名的坑"))
    checked = 0
    for row in rows:
        case_path = row["case"]
        recipe = read_recipe(case_path)
        if row["input"] == "generated":
            if recipe is None:
                problems.append((case_path, "input=generated 却没有 `## recipe`"))
                continue
            checked += 1
            expected_bytes = recipe.get("expectedInputBytes")
            expected_sha = recipe.get("expectedInputSha256")
            if not isinstance(expected_bytes, int) or expected_bytes <= FOUR_MIB:
                problems.append((case_path, "expectedInputBytes 未超过 4 MiB(%r);"
                                            "该用例必须入库" % (expected_bytes,)))
            if not expected_sha:
                problems.append((case_path, "缺少 expectedInputSha256 指纹"))
            target = _case_dir(case_path) / "input.pxl"
            if not target.is_file():
                problems.append((case_path, "输入文件不存在(先跑 --all)"))
                continue
            size = target.stat().st_size
            if size != expected_bytes:
                problems.append((case_path, "字节数 %d != expectedInputBytes %r"
                                 % (size, expected_bytes)))
            actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                problems.append((case_path, "SHA-256 %s != expectedInputSha256 %r"
                                 % (actual_sha, expected_sha)))
            if size <= DOC_BYTES_LIMIT:
                problems.append((case_path, "输入只有 %d 字节,没有越过 64 MiB 文档上限,"
                                            "测不到该用例的覆盖点" % size))
        elif recipe is not None:
            problems.append((case_path, "input=committed 却带 `## recipe`(索引自相矛盾)"))

    if problems:
        for case_path, message in problems:
            sys.stderr.write("[FAIL] %s:%s\n" % (case_path or "-", message))
        sys.stderr.write("校验失败 %d 条\n" % len(problems))
        return 1
    print("校验通过:%d 条生成型用例,配方与输入指纹一致" % checked)
    return 0


def main(argv):
    args = argv[1:]
    if args == ["--all"]:
        return run_all()
    if args == ["--check"]:
        return run_check()
    if args in (["-h"], ["--help"]):
        sys.stdout.write(__doc__)
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        return run_one(args[0])
    sys.stderr.write(
        "用法:\n"
        "  %s <case-path>     如 negative/neg-doc-too-large\n"
        "  %s --all\n"
        "  %s --check\n" % (argv[0], argv[0], argv[0]))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
