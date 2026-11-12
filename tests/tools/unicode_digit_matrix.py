"""差分矩阵：把同一组输入分别喂给 Java 与 Python 两个 compiler，逐条比对退出码与错误码。

用法（在仓库根目录、已构建 :cli:classes 的前提下）：
    python tests/tools/unicode_digit_matrix.py

需要 java 在 PATH 上；容器内路径按 /work 相对，宿主机上请按需改 ROOT。
"""

import itertools, json, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path("/work")
WORK = Path("/tmp/uni")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

LEAF = {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
        "palette": ["FFFFFFFF"],
        "layers": [{"name": "b", "blend": "normal", "ops": [{"op": "fill", "slot": 1}]}]}
(WORK / "leaf.pxl").write_text(json.dumps(LEAF), encoding="utf-8")

STRINGS = ["\u0663", "\u06f3", "\uff13", "\u0969", "\u00b3", "\u2163", "\uff11\uff12\uff13",
           "-\u0663", "+1", " 1", "1 ", "0x10", "1_000", "1e3", "\u0661\u0662\u0663",
           "\u00b2", "\u2081", "1\u0663"]

def build(raw, mode):
    if mode == "default":
        return ('{"format":"PXL","version":1,"encoding":"char","width":1,"height":1,'
                '"params":{"p":{"type":"integer","default":%s}},"palette":["FFFFFFFF"],'
                '"layers":[{"name":"b","blend":"normal","ops":[{"op":"fill","slot":1}]}]}' % raw)
    return ('{"format":"PXL","version":1,"encoding":"char","width":1,"height":1,'
            '"assets":{"a":{"kind":"pxl","path":"sub.pxl","params":{"k":%s}}},"palette":["FFFFFFFF"],'
            '"layers":[{"name":"b","blend":"normal","ops":[{"op":"stamp","asset":"a","x":0,"y":0}]}]}' % raw)

sub = {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
       "params": {"k": {"type": "integer"}}, "palette": ["FFFFFFFF"],
       "layers": [{"name": "b", "blend": "normal",
                   "ops": [{"op": "rect", "x": 0, "y": 0, "w": "$k", "h": 1, "slot": 1}]}]}
(WORK / "sub.pxl").write_text(json.dumps(sub), encoding="utf-8")

CP = "/work/compiler/java/cli/build/classes/java/main:/work/compiler/java/core/build/classes/java/main"
env = dict(os.environ, PYTHONPATH=str(ROOT / "compiler" / "python"))

def code(r):
    for line in (r.stdout + r.stderr).splitlines():
        if "PXL0" in line:
            return "PXL" + line.split("PXL", 1)[1][:3].strip()
    return "ok" if r.returncode == 0 else "rc%d" % r.returncode

mismatches, total = [], 0
for raw, mode in itertools.product([json.dumps(s) for s in STRINGS], ["default", "bind"]):
    total += 1
    (WORK / "entry.pxl").write_text(build(raw, mode), encoding="utf-8")
    j = subprocess.run(["java", "-cp", CP, "io.github.texel.cli.Main", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/j.png"], cwd=str(WORK), capture_output=True, text=True)
    p = subprocess.run(["python3", "-m", "texel_compiler", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/p.png"], cwd=str(WORK), capture_output=True, text=True, env=env)
    if (j.returncode == 0) != (p.returncode == 0) or code(j) != code(p):
        mismatches.append((raw, mode, code(j), code(p)))

sys.exit(1 if mismatches else 0)  # 门禁语义
print("total:", total, "mismatches:", len(mismatches))
for m in mismatches[:15]:
    print("MISMATCH", m)
