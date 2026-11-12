"""Java harness 的 Python 批处理桥（P1-1）。

一个进程处理全部输入，一次输出 JSONL；取代"每个用例 spawn 两次 python"。

用法：
    python batch_probe.py <task-file> <out-dir>

task-file 每行 ``<id>\\t<PNG 绝对路径>``；对每个任务：
* 抽取：成功则把 `.pxl` 写到 ``<out-dir>/<id>.pxl``；
* 摘要：解码后的 RGBA SHA-256 与 ``宽x高``（与 ``png_digest.py`` 同口径）。

stdout 每行一条 JSON：``{"id":..,"ok":true,"sha256":"..","size":"WxH"}``
或 ``{"id":..,"ok":false,"code":"PXL00N","message":".."}``。
**失败也要继续处理后面的任务**：一条坏输入不该让整轮批处理作废。
"""

import json
import sys
from pathlib import Path

# compiler/java/tools → 上溯三级才是仓库根（重命名后多了一层）
ROOT = Path(__file__).resolve().parents[3]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hashlib  # noqa: E402

from importer import extract_png  # noqa: E402
from importer.errors import PxlError  # noqa: E402
from importer.png import decode_png  # noqa: E402
from importer.rgba import to_rgba8  # noqa: E402


def probe(identifier, source, out_dir):
    payload = source.read_bytes()
    # 抽取（这条路径就是被测实现；失败要带错误码回来）
    try:
        text = extract_png(payload)
    except PxlError as error:
        return {"id": identifier, "ok": False, "code": error.code,
                "message": error.message}
    # id 里带 `/`（suite 成员），文件名必须消毒；写回的 `pxl` 字段就是实际文件名。
    target = out_dir / (identifier.replace("/", "__") + ".pxl")
    target.write_bytes(text.encode("utf-8"))
    # 摘要：解码器 → RGBA8 → SHA-256（与 png_digest.py 一致）
    try:
        image = decode_png(payload)
        rgba = to_rgba8(image)
    except PxlError as error:
        return {"id": identifier, "ok": False, "code": error.code,
                "message": error.message}
    return {
        "id": identifier,
        "ok": True,
        "pxl": target.name,
        "sha256": hashlib.sha256(rgba).hexdigest(),
        "size": "%dx%d" % (image.width, image.height),
    }


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("用法: batch_probe.py <task-file> <out-dir>\n")
        return 2
    task_file = Path(argv[1])
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    for line in task_file.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        identifier, path = line.split("\t", 1)
        try:
            record = probe(identifier, Path(path), out_dir)
        except Exception as error:  # noqa: BLE001 - 一条坏输入不该炸掉整轮
            record = {"id": identifier, "ok": False, "code": "CRASH",
                      "message": "%s: %s" % (type(error).__name__, error)}
        sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
