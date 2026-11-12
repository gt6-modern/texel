"""差分矩阵：把同一组输入分别喂给 Java 与 Python 两个 compiler，逐条比对退出码与错误码。

用法（在仓库根目录、已构建 :cli:classes 的前提下）：
    python tests/tools/param_type_matrix.py

需要 java 在 PATH 上；容器内路径按 /work 相对，宿主机上请按需改 ROOT。
"""

import itertools, json, os, shutil, subprocess, sys, sys
from pathlib import Path

ROOT = Path("/work")
WORK = Path("/tmp/difffuzz")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

LEAF = {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
        "palette": ["FFFFFFFF"],
        "layers": [{"name": "b", "blend": "normal", "ops": [{"op": "fill", "slot": 1}]}]}
(WORK / "leaf.pxl").write_text(json.dumps(LEAF), encoding="utf-8")

TYPES = ["integer", "color", "boolean"]
VALUES = ['1', '1.5', '"1"', '"abc"', 'true', 'false', 'null', '[]', '{}', '"B87333FF"', '0', '-0']

def build(t, raw, mode):
    if mode == "default":
        head = '"params": {"p": {"type": "%s", "default": %s}}' % (t, raw)
        ops = '[{"op": "fill", "slot": 1}]'
    else:
        head = '"assets": {"a": {"kind": "pxl", "path": "leaf.pxl", "params": {"k": %s}}}' % raw
        ops = '[{"op": "stamp", "asset": "a", "x": 0, "y": 0}]'
    # 被绑定文档需要声明 k
    if mode == "bind":
        sub = {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
               "params": {"k": {"type": t}},
               "palette": ["FFFFFFFF"],
               "layers": [{"name": "b", "blend": "normal",
                           "ops": [{"op": "rect", "x": 0, "y": 0, "w": "$k", "h": 1, "slot": 1}
                                   if t == "integer" else {"op": "fill", "slot": 1}]}]}
        (WORK / "sub.pxl").write_text(json.dumps(sub), encoding="utf-8")
        head = head.replace('"leaf.pxl"', '"sub.pxl"')
    body = ('{"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1, %s, '
            '"palette": ["FFFFFFFF"], "layers": [{"name": "b", "blend": "normal", "ops": %s}]}')
    return body % (head, ops)

CP = "/work/compiler/java/cli/build/classes/java/main:/work/compiler/java/core/build/classes/java/main"
env = dict(os.environ, PYTHONPATH=str(ROOT / "compiler" / "python"))
mismatches = []
for t, raw, mode in itertools.product(TYPES, VALUES, ["default", "bind"]):
    (WORK / "entry.pxl").write_text(build(t, raw, mode), encoding="utf-8")
    j = subprocess.run(["java", "-cp", CP, "io.github.texel.cli.Main", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/j.png"], cwd=str(WORK),
                       capture_output=True, text=True)
    p = subprocess.run(["python3", "-m", "texel_compiler", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/p.png"], cwd=str(WORK),
                       capture_output=True, text=True, env=env)
    def code(r):
        for line in (r.stdout + r.stderr).splitlines():
            if "PXL0" in line:
                return line.split("PXL", 1)[1][:3].strip()
        return "ok" if r.returncode == 0 else "rc%d" % r.returncode
    jc, pc = code(j), code(p)
    if (j.returncode == 0) != (p.returncode == 0) or (jc != pc):
        mismatches.append((t, raw, mode, jc, pc))

print("total cases:", len(TYPES) * len(VALUES) * 2, "mismatches:", len(mismatches))
for m in mismatches[:20]:
    print("MISMATCH", m)

sys.exit(1 if mismatches else 0)  # 门禁语义：有分歧必须非零退出
