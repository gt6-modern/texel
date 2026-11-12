"""Run the current PXL compiler conformance corpus.

The ``base`` suite covers PNG mirroring, operations, blending, validation and
determinism. The ``components`` suite covers assets, parameters, bindings,
resolvers and dependency graphs. Expectations are read from the corpus.

    python compiler/python/tools/conformance.py
    python compiler/python/tools/conformance.py --suite components
    python compiler/python/tools/conformance.py --raw base
    python compiler/python/tools/conformance.py --fast
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "compiler" / "python"))

from texel_compiler import PxlError, capabilities_json, compile_target, encode_png, render_document
from texel_compiler.capabilities import CONFORMANCE_PROFILE
from texel_compiler.pngenc import chunk_types, row_filter_bytes
from texel_compiler.resolver import build_memory_resolver

CONF = ROOT / "spec" / "conformance"
BASE_CASES = CONF / "cases"
COMPONENT_CASES = CONF / "v1" / "cases"
PROFILES = CONF / "profiles.tsv"

SUITES = ("base", "components")


class Skip(Exception):
    """不是失败,只是本侧不判(并给出可读理由)。"""

    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason


class Report(object):
    def __init__(self):
        self.passed = []
        self.failed = []
        self.skipped = []

    def ok(self, name):
        self.passed.append(name)

    def bad(self, name, message):
        self.failed.append((name, message))

    def skip(self, name, reason):
        self.skipped.append((name, reason))


# ---------------------------------------------------------------- 语料索引


def read_tsv(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    lines = [line for line in lines if line != ""]
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) != len(header):
            raise SystemExit("索引格式错: " + str(path) + " 行列数与表头不符: " + line[:80])
        rows.append(dict(zip(header, cells)))
    return rows


def base_index():
    return read_tsv(CONF / "CASES.tsv")


def components_index():
    return read_tsv(CONF / "v1" / "CASES.tsv")


def select_profile(profile):
    """按 profiles.tsv 做集合代数:并集减精确排除。规则见 PRD §6.3。"""
    if not PROFILES.exists():
        raise SystemExit("缺少 " + str(PROFILES))
    selection = {}
    seen_actions = {}
    for line in PROFILES.read_text(encoding="utf-8").split("\n"):
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != 4:
            raise SystemExit("profiles.tsv 不是四列: " + line)
        name, suite, action, case = cells
        if suite not in SUITES:
            raise SystemExit("profiles.tsv 引用了不存在的 suite: " + suite)
        if action not in ("include_all", "include", "exclude"):
            raise SystemExit("profiles.tsv 的 action 非法: " + action)
        if action == "include_all" and case != "-":
            raise SystemExit("include_all 的 case 列必须写 -")
        if action != "include_all" and case == "-":
            raise SystemExit("include/exclude 必须给出精确 case")
        if name != profile:
            continue
        bucket = selection.setdefault(suite, {"include_all": False, "include": set(),
                                              "exclude": set()})
        key = (suite, case)
        if action in ("include_all", "include"):
            if seen_actions.get(key) == "exclude":
                raise SystemExit("同一档案下动作冲突: " + suite + "/" + case)
            seen_actions[key] = "include"
            if action == "include_all":
                bucket["include_all"] = True
            else:
                bucket["include"].add(case)
        else:
            if seen_actions.get(key) == "include":
                raise SystemExit("同一档案下动作冲突: " + suite + "/" + case)
            seen_actions[key] = "exclude"
            bucket["exclude"].add(case)
    if not any(bucket["include_all"] or bucket["include"] for bucket in selection.values()):
        raise SystemExit("档案 " + profile + " 的选择集为空")
    return selection


def cases_for_suite(suite, selection, raw):
    """返回该 suite 下要跑的 case 名列表(按索引顺序)。"""
    if suite == "base":
        names = [row["case"] for row in base_index()]
    else:
        names = [row["case"] for row in components_index()]
    known = set(names)
    if raw:
        return sorted(names)
    bucket = selection.get(suite)
    if bucket is None:
        return []
    chosen = set()
    if bucket["include_all"]:
        chosen.update(names)
    for case in bucket["include"]:
        if case not in known:
            raise SystemExit("profiles.tsv 引用了不存在的 case: " + suite + "/" + case)
        chosen.add(case)
    for case in bucket["exclude"]:
        if case not in known:
            raise SystemExit("profiles.tsv 引用了不存在的 case: " + suite + "/" + case)
        chosen.discard(case)
    return sorted(chosen)


# ---------------------------------------------------------------- base suite


def read_expected_pixels(path):
    if not path.exists():
        raise Skip("缺少 expected.pixels.sha256")
    return path.read_text(encoding="utf-8").strip()


def check_png_structure(png, width, height):
    types = chunk_types(png)
    if types[0] != b"IHDR" or types[-1] != b"IEND":
        return "PNG chunk 类型序列不是 IHDR...IEND: " + repr(types)
    for kind in types[1:-1]:
        if kind != b"IDAT":
            return "PNG 出现非 IDAT 的中间 chunk: " + repr(kind)
    if not types[1:-1]:
        return "PNG 没有 IDAT"
    filters = row_filter_bytes(png, width, height)
    if any(value != 0 for value in filters):
        return "PNG 行过滤器不恒为 0"
    return None


def _crash(failure):
    """未预期的异常也是一条失败,不能让整轮 runner 崩在第一条上。

    变异自检时这条路径真的被用到过:把 §6.3 的分母换成"先取整的 ao * 255"会让通道算出
    256,于是 bytearray 赋值抛 ValueError。那是实现错,不是 runner 的错——runner 必须把这
    一条判红并继续,否则一次变异就会伪装成"跑不起来"。
    """
    return "未预期异常 " + type(failure).__name__ + ": " + str(failure)


def render_and_compare(doc_path, expected_path, report, name, fast):
    data = doc_path.read_bytes()
    rendered = render_document(data)
    want = read_expected_pixels(expected_path)
    got = hashlib.sha256(rendered.rgba).hexdigest()
    if got != want:
        report.bad(name, "像素摘要不等: 得到 " + got + ",期望 " + want)
        return
    png = encode_png(rendered.width, rendered.height, rendered.rgba)
    problem = check_png_structure(png, rendered.width, rendered.height)
    if problem is not None:
        report.bad(name, problem)
        return
    if not fast:
        again = render_document(data)
        if again.rgba != rendered.rgba:
            report.bad(name, "两次渲染的像素不同(§9 确定性)")
            return
        if encode_png(again.width, again.height, again.rgba) != png:
            report.bad(name, "两次渲染的 PNG 字节不同(§9 确定性)")
            return
    report.ok(name)


def run_base_case(case, report, fast):
    directory = BASE_CASES / case
    if not directory.is_dir():
        raise AssertionError("用例目录不存在（索引与目录不一致）")
    if case.startswith("real-item-material-family"):
        fast = True  # 400 个成员的批数组:两跑确定性检查留给单例,免得整轮变成分钟级
    expected_error = directory / "expected.error"
    input_pxl = directory / "input.pxl"
    expected_pxl = directory / "expected.pxl"
    if (directory / "manifest.tsv").exists():
        run_base_batch(case, directory, report)
        return
    if input_pxl.exists():
        if expected_error.exists():
            want = expected_error.read_text(encoding="utf-8").strip()
            try:
                render_document(input_pxl.read_bytes())
            except PxlError as failure:
                if failure.code != want:
                    raise AssertionError("期望 " + want + ",实际 " + failure.describe())
                report.ok(case)
                return
            raise AssertionError("期望 " + want + ",但渲染成功")
        render_and_compare(input_pxl, directory / "expected.pixels.sha256", report, case, fast)
        return
    if expected_pxl.exists():
        render_and_compare(expected_pxl, directory / "expected.pixels.sha256", report, case, fast)
        return
    if (directory / "input.png").exists():
        raise Skip("抽取侧用例(只有 input.png):本 runner 只判 compiler")
    raise Skip("输入不存在(§13.6 生成型用例需先跑 gen_cases.py)")


def run_base_batch(case, directory, report):
    manifest = read_tsv(directory / "manifest.tsv")
    members = directory / "members"
    actual = sorted(path.name for path in members.iterdir() if path.is_dir())
    listed = sorted(row["member"] for row in manifest)
    if actual != listed:
        report.bad(case, "manifest 与实际成员目录不一致(§13.7)")
        return
    positives = 0
    defects = 0
    for row in manifest:
        member_dir = members / row["member"]
        name = case + "/" + row["member"]
        if (member_dir / "expected.error").exists():
            defects += 1
            report.skip(name, "已知缺陷成员(只有 input.png + expected.error):抽取侧")
            continue
        positives += 1
        try:
            render_and_compare(member_dir / "expected.pxl",
                               member_dir / "expected.pixels.sha256", report, name, True)
        except AssertionError as failure:
            report.bad(name, str(failure))
    report.ok(case + " [aggregate " + str(positives) + "+" + str(defects)
              + "=" + str(len(manifest)) + "]")


# ---------------------------------------------------------------- v1


def _chain_equals(chain_part, want):
    """从「引用链: a -> b」取出段序列，与期望逐段比较。"""
    prefix = "引用链: "
    if not chain_part.startswith(prefix):
        return False
    body = chain_part[len(prefix):].strip()
    while body.endswith('）') or body.endswith(')'):
        body = body[:-1].strip()
    return body.split(" -> ") == want.strip().split(" -> ")

def component_targets(directory):
    bindings_file = directory / "bindings.json"
    if not bindings_file.exists():
        return [("", {})]
    data = json.loads(bindings_file.read_text(encoding="utf-8"))
    targets = [(target["name"], target["bindings"]) for target in data["targets"]]
    if not targets:
        # 声明了 bindings.json 却没有任何 target：不能"跑 0 个 target"就算通过
        # （发布态复核 P2：这一格此前只跑正常用例就退 0，Java 会退 1）。
        raise AssertionError("bindings.json 声明了 0 个 target")
    return targets


def component_expected_pixels(directory, name):
    if name:
        candidate = directory / ("expected." + name + ".pixels.sha256")
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    candidate = directory / "expected.pixels.sha256"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8").strip()
    return None


def component_spelling(directory):
    path = directory / "spelling.tsv"
    if not path.exists():
        return None
    spelling = {}
    for row in read_tsv(path):
        spelling[row["logical"]] = row["actual"]
    return spelling


def component_resolver(directory):
    """§13.3.1:用例目录就是 asset root,入口逻辑路径恒为 entry.pxl。"""
    resolver = build_memory_resolver(directory)
    spelling = component_spelling(directory)
    if spelling:
        resolver.spelling.update(spelling)
    return resolver


def run_component_case(case, row, report, fast):
    directory = COMPONENT_CASES / case
    if not directory.is_dir():
        raise AssertionError("用例目录不存在（索引与目录不一致）")
    expected_column = row.get("expected", "").strip()
    if expected_column == "-":
        # §5.1 要求 pending 清单为 0：正式 profile 遇到 pending 必须判红，
        # 不能"跳过并退出 0"（发布态复核：Java 已拒，两侧不一致）。
        raise AssertionError("pending 用例（§5.1 要求 0 条）: " + case)
    resolver = component_resolver(directory)
    expected_error = directory / "expected.error"
    expected_deps = directory / "expected.dependencies.tsv"
    if "dependencies" in expected_column and not expected_deps.exists():
        raise AssertionError("索引声明了 dependencies 但文件缺失: " + case)
    for name, bindings in component_targets(directory):
        label = case if not name else case + "#" + name
        try:
            compiled = compile_target("entry.pxl", resolver, bindings)
        except PxlError as failure:
            chain_file = directory / "expected.chain"
            if chain_file.exists():
                want_chain = chain_file.read_text(encoding="utf-8").strip()
                described = failure.describe()
                marker = described.find("引用链: ")
                chain_part = described[marker:] if marker >= 0 else ""
                # 精确比较：子串匹配时把期望改成严格前缀也能通过（C2）。
                if want_chain and not _chain_equals(chain_part, want_chain):
                    raise AssertionError("引用链不符「" + want_chain + "」（实际: " + chain_part + "）")
            if not expected_error.exists():
                raise AssertionError("不应失败: " + failure.describe())
            want = expected_error.read_text(encoding="utf-8").strip()
            if failure.code != want:
                raise AssertionError("期望 " + want + ",实际 " + failure.describe())
            report.ok(label)
            continue
        if expected_error.exists():
            want = expected_error.read_text(encoding="utf-8").strip()
            raise AssertionError("期望 " + want + ",但编译成功")
        want = component_expected_pixels(directory, name)
        if want is None:
            raise AssertionError("索引声称有期望像素,但目录里没有 expected.*.pixels.sha256")
        got = hashlib.sha256(compiled.rgba).hexdigest()
        if got != want:
            raise AssertionError("像素摘要不等: 得到 " + got + ",期望 " + want)
        png = encode_png(compiled.width, compiled.height, compiled.rgba)
        problem = check_png_structure(png, compiled.width, compiled.height)
        if problem is not None:
            raise AssertionError(problem)
        if expected_deps.exists():
            want_deps = expected_deps.read_text(encoding="utf-8")
            if compiled.manifest_text() != want_deps:
                raise AssertionError("依赖清单不等: 得到 " + repr(compiled.manifest_text())
                                     + ",期望 " + repr(want_deps))
        if not fast:
            again = compile_target("entry.pxl", resolver, bindings)
            if again.rgba != compiled.rgba:
                raise AssertionError("两次编译的像素不同(§12 确定性)")
        report.ok(label)


# ---------------------------------------------------------------- 主流程


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description="PXL conformance runner(compiler 侧)")
    parser.add_argument("--profile", default=None,
                        help="能力档案名;默认取本实现声明的那个")
    parser.add_argument("--suite", action="append", choices=list(SUITES), default=None,
                        help="只跑指定 suite(可重复)")
    parser.add_argument("--raw", default=None, choices=list(SUITES),
                        help="不套 profiles.tsv,直接跑某个 suite 的全量用例")
    parser.add_argument("--fast", action="store_true", help="跳过两跑确定性检查")
    parser.add_argument("--verbose", action="store_true", help="逐条打印通过项")
    options = parser.parse_args(argv)

    profile = options.profile or CONFORMANCE_PROFILE
    report = Report()
    selection = {} if options.raw else select_profile(profile)
    suites = [options.raw] if options.raw else (options.suite or list(SUITES))
    if options.raw and options.suite:
        raise SystemExit("--raw 与 --suite 不能同时用")

    print("profile: " + ("<raw:" + options.raw + ">" if options.raw else profile))
    print("capabilities_profile: " + CONFORMANCE_PROFILE)

    for suite in suites:
        names = cases_for_suite(suite, selection, options.raw is not None)
        if suite == "base":
            for case in names:
                try:
                    run_base_case(case, report, options.fast)
                except Skip as skipped:
                    report.skip(case, skipped.reason)
                except AssertionError as failure:
                    report.bad(case, str(failure))
                except Exception as failure:  # noqa: BLE001 - 见下
                    report.bad(case, _crash(failure))
        else:
            index = dict((row["case"], row) for row in components_index())
            for case in names:
                try:
                    run_component_case(case, index[case], report, options.fast)
                except Skip as skipped:
                    report.skip(case, skipped.reason)
                except AssertionError as failure:
                    report.bad(case, str(failure))
                except Exception as failure:  # noqa: BLE001
                    report.bad(case, _crash(failure))

    if options.verbose:
        for name in report.passed:
            print("PASS " + name)
    for name, message in report.failed:
        print("FAIL " + name + ": " + message)
    reasons = {}
    for name, reason in report.skipped:
        reasons.setdefault(reason, []).append(name)
    for reason in sorted(reasons):
        print("SKIP " + str(len(reasons[reason])) + " 条: " + reason)
    print("通过 " + str(len(report.passed)) + " / 失败 " + str(len(report.failed))
          + " / 跳过 " + str(len(report.skipped)))
    # 一条都没跑也退 0 是假绿：索引或目录不对时必须响（发布态复核 P2）。
    if not report.passed:
        print("FAIL <none>: 没有任何用例被判定通过（索引/目录不对？）")
        return 1
    return 0 if not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
