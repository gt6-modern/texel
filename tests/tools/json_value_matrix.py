
"""差分矩阵：把**裸 JSON 值**（不带引号）分别放到 width / 参数默认 / 子绑定三个位置，
   逐条比对 Java 与 Python 的退出码与错误码。有分歧即非零退出。

用法（仓库根目录、已构建 :cli:classes）：
    python tests/tools/json_value_matrix.py
"""
import itertools, json, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path("/work") if Path("/work").exists() else Path(__file__).resolve().parents[2]
WORK = Path("/tmp/jsonmatrix")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

LEAF = {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
        "palette": ["FFFFFFFF"],
        "layers": [{"name": "b", "blend": "normal", "ops": [{"op": "fill", "slot": 1}]}]}
(WORK / "leaf.pxl").write_text(json.dumps(LEAF), encoding="utf-8")

RAW = ["1", "1.0", "1e0", "01", "-0", "+1", ".5", "1.", "\u0661", "1\u0661", "\u00b2",
       "0x1", "1_0", " 1", "1 ", "true", "null", "\"1\"", "[]", "{}"]

def build(raw, where):
    if where == "width":
        return ('{"format":"PXL","version":1,"encoding":"char","width":%s,"height":1,'
                '"palette":["FFFFFFFF"],"layers":[{"name":"b","blend":"normal",'
                '"ops":[{"op":"fill","slot":1}]}]}' % raw)
    if where == "default":
        return ('{"format":"PXL","version":1,"encoding":"char","width":"1","height":1,'
                '"params":{"p":{"type":"integer","default":%s}},"palette":["FFFFFFFF"],'
                '"layers":[{"name":"b","blend":"normal","ops":[{"op":"fill","slot":1}]}]}'.replace('"width":"1"', '"width":1') % raw)
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
for raw, where in itertools.product(RAW, ["width", "default", "bind"]):
    total += 1
    (WORK / "entry.pxl").write_text(build(raw, where), encoding="utf-8")
    j = subprocess.run(["java", "-cp", CP, "io.github.texel.cli.Main", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/j.png"], cwd=str(WORK), capture_output=True, text=True)
    p = subprocess.run(["python3", "-m", "texel_compiler", "compile", "entry.pxl",
                        "--asset-root", ".", "-o", "/tmp/p.png"], cwd=str(WORK), capture_output=True, text=True, env=env)
    if (j.returncode == 0) != (p.returncode == 0) or code(j) != code(p):
        mismatches.append((raw, where, code(j), code(p)))

print("total:", total, "mismatches:", len(mismatches))
for m in mismatches[:20]:
    print("MISMATCH", m)
sys.exit(1 if mismatches else 0)
