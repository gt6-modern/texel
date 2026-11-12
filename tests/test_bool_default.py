"""P2 回归：文档里的 boolean 默认值必须是**真布尔**，不能是字符串 "false"。

发布态复核 P2：`_canonical_value` 同时服务 CLI 绑定与文档默认值校验，我修 CLI（接受文本
"true"/"false"）时顺手放宽了**文档接受集** —— `default:"false"` 被 Python 接受、Java 报 PXL012。
现在文本解析只在 canonical_bindings（绑定侧）做，文档侧仍是强类型。
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver, pxlcompile  # noqa: E402
from texel_compiler.errors import PxlError  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "bad_bool_default"


class BadBooleanDefaultTest(unittest.TestCase):
    def test_string_default_for_boolean_is_rejected(self):
        root = resolver.build_memory_resolver(str(FIXTURE))
        with self.assertRaises(PxlError) as caught:
            pxlcompile.compile_target("entry.pxl", root, {})
        self.assertEqual("PXL012", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
