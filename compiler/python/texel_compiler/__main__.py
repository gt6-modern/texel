"""python -m texel_compiler 的入口。"""

import sys

from .cli import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except MemoryError:
        # §10.2.1：驻留内存不足是宿主资源失败，不是 PXL 码。
        sys.stderr.write("texel: CLI001: I/O 失败: 宿主内存不足（工作集超出可用内存）\n")
        sys.exit(4)
    except (OSError, RuntimeError) as failure:
        # 宿主 I/O 与链接环（Windows 上 resolve 循环链接会抛 RuntimeError）必须
        # 变成受控错误：泄漏 traceback 是确证问题（发布态复核第二节）。
        sys.stderr.write("texel: 宿主 I/O 失败: " + str(failure) + "\n")
        sys.exit(2)
