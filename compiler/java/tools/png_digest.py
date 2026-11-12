#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打印一张 PNG 的 RGBA8 原始字节流 SHA-256(规范 §13.4)。

这是 Java 侧 conformance harness 的**跨语言**支点:harness 自己渲染出的像素摘要要与
"input.png 解码出来的 RGBA8"比较,而这个解码必须由**另一个实现**完成——否则编码器与
解码器共错时会自证成功。

复用 `importer` 的解码器是刻意的:规范 §13.2a 判据 3 说的正是"抽取器解出的 RGBA8 与
渲染器产出的 RGBA8 逐字节相同",所以这里的解码方就该是抽取器那一侧。

用法::

    <py> compiler/java/tools/png_digest.py <input.png>

输出一行:``<width>x<height> <sha256>``。失败写 stderr,退出码 1。
"""

import hashlib
import sys
from pathlib import Path

# compiler/java/tools → 上溯三级才是仓库根（重命名后多了一层）
REPO = Path(__file__).resolve().parents[3]

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from importer.errors import PxlError  # noqa: E402
from importer.png import decode_png  # noqa: E402
from importer.rgba import to_rgba8  # noqa: E402


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: png_digest.py <input.png>\n")
        return 2
    try:
        data = Path(argv[1]).read_bytes()
        image = decode_png(data)
        rgba = to_rgba8(image)
    except PxlError as error:
        sys.stderr.write("%s\n" % (error,))
        return 1
    except OSError as error:
        sys.stderr.write("读取失败: %s\n" % (error,))
        return 1
    sys.stdout.write(
        "%dx%d %s\n" % (image.width, image.height, hashlib.sha256(rgba).hexdigest())
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
