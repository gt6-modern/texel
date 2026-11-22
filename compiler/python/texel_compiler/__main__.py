"""``python -m texel_compiler`` 的入口。

顶层异常兜底在 ``cli.main`` 里（控制台脚本指向的也是它），这里只负责把退出码交给
解释器：两处各写一份兜底必然漂开，而用户实际执行的是 ``texel-compiler-python``。
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
