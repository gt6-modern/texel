"""产出/复核 `expected.pxl`(规范 §13.3 第 3 条、附录 A)。

`expected.pxl` **必须**由附录 A 的规则产出,不得手调空白(README §4.4),所以它的
唯一合法来源是 `importer` 的抽取器。本脚本就是那条产线,顺带提供一个 `--check`
模式,让"文本是否已被抽取器的当前行为固定"这件事可判定。

用法(在仓库根目录):

    <py> spec/conformance/tools/gen_expected_pxl.py            # 写出全部 syn- 用例
    <py> spec/conformance/tools/gen_expected_pxl.py --check     # 只比对,不写文件
    <py> spec/conformance/tools/gen_expected_pxl.py syn-tier-61 # 只做一条

`--check` 发现差异时以非零退出码结束——黄金文本与实现漂移必须被看见,不能静默
重写(那是"黄金迁就实现"的典型滑法)。
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from importer import extract_png  # noqa: E402  (需要先补 sys.path)

CONFORMANCE = ROOT / "spec" / "conformance"
CASES_TSV = CONFORMANCE / "CASES.tsv"
CASES_DIR = CONFORMANCE / "cases"

#: 只有往返正例(syn- / real-)才有 expected.pxl。
ROUND_TRIP_PREFIXES = ("syn-", "real-")


def load_cases():
    with open(str(CASES_TSV), "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def round_trip_cases():
    return [
        row["case"]
        for row in load_cases()
        if row["case"].startswith(ROUND_TRIP_PREFIXES)
    ]


def batch_cases():
    """`form=batch` 的套件：它们的 `input.png` 按设计不存在，成员各自核对。"""
    return set(row["case"] for row in load_cases() if row["form"] == "batch")


def render(case):
    """读该用例的 input.png,返回规范形式文本。"""
    source = CASES_DIR / case / "input.png"
    return extract_png(source.read_bytes())


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = False
    selected = []
    for item in args:
        if item == "--check":
            check_only = True
        elif item in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        elif item.startswith("-"):
            sys.stderr.write("未知选项: %s\n" % item)
            return 2
        else:
            selected.append(item)

    cases = selected or round_trip_cases()
    written = 0
    same = 0
    drift = []
    missing_input = []
    unknown = [case for case in cases
               if not (CASES_DIR / case).is_dir()]

    for case in cases:
        source = CASES_DIR / case / "input.png"
        if not source.is_file():
            missing_input.append(case)
            continue
        text = render(case)
        target = CASES_DIR / case / "expected.pxl"
        payload = text.encode("utf-8")
        if check_only:
            if not target.is_file():
                drift.append((case, "缺少 expected.pxl"))
                continue
            if target.read_bytes() != payload:
                drift.append((case, "与抽取器当前输出不一致"))
                continue
            same += 1
        else:
            with open(str(target), "wb") as handle:
                handle.write(payload)
            written += 1

    if unknown:
        sys.stderr.write("未知用例: %s\n" % ", ".join(unknown))
        return 2
    # 缺输入要分两类：批量套件没有 input.png 是**设计如此**（成员各自核对）；
    # 单例缺 input.png 是**语料坏了**，必须计入失败——不能被别的用例"核对成功"抵消。
    suites = batch_cases()
    broken = [case for case in missing_input if case not in suites]
    for case in missing_input:
        sys.stdout.write("[ 跳过 ] %s:没有 input.png%s\n"
                         % (case, "（批量套件，成员各自核对）" if case in suites else ""))
    for case in broken:
        sys.stderr.write("[ 缺输入 ] %s:单例往返正例必须有 input.png\n" % case)
    if check_only:
        sys.stdout.write("一致 %d 条,漂移 %d 条,缺输入 %d 条\n"
                         % (same, len(drift), len(broken)))
        for case, reason in drift:
            sys.stdout.write("[ 漂移 ] %s: %s\n" % (case, reason))
        # 零检查不是通过:索引里一条往返正例都没有时也会走到这里。
        if same == 0:
            sys.stderr.write("一条也没有核对成功(跳过 %d 条):这不是通过\n"
                             % len(missing_input))
            return 1
        if broken:
            sys.stderr.write("%d 条单例正例缺输入(见上):这不是通过\n" % len(broken))
            return 1
        return 1 if drift else 0
    sys.stdout.write("写出 %d 份 expected.pxl\n" % written)
    # 写出模式同样不能假成功：单例缺输入意味着**这份语料不完整**，"写出 0 份"更是
    # 什么都没干。`--check` 有判据、写出模式没有，等于让 `--all` 的调用方拿不到失败。
    if broken:
        sys.stderr.write("%d 条单例正例缺输入(见上):语料不完整\n" % len(broken))
        return 1
    if written == 0:
        sys.stderr.write("一份也没写出:这不是成功\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
