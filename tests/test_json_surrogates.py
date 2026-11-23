"""测试:JSON 阶段的 Unicode 字符串校验(规范 §3.4 / §11.1 阶段 3)。

孤立代理项(``\\uD800`` 单独出现、``\\uDC00`` 单独出现)不是合法 Unicode 文本,
必须在 JSON 解析阶段以 ``PXL001`` 拒绝 —— 若放过去,后面的未知 op / 参数检查会先报
``PXL004`` 之类,同一份文档在 Python 与 Java 两条实现上得到不同错误码(审查 #11)。

Java 侧的同一组用例在 `CompilerTestMain.jsonStrings()` 里,诊断为逐字相同的中文短语。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / "compiler" / "python"

if str(COMPILER) not in sys.path:
    sys.path.insert(0, str(COMPILER))

from texel_compiler import PxlError
from texel_compiler import jsonx


def parse(text):
    return jsonx.parse(text)


class SurrogateTest(unittest.TestCase):

    def assert_pxl001(self, text, what):
        with self.assertRaises(PxlError) as caught:
            parse(text)
        self.assertEqual("PXL001", caught.exception.code, what)

    def test_isolated_low_surrogate_is_rejected(self):
        self.assert_pxl001('{"k":"\\uDC00"}', "孤立的低代理项")
        self.assert_pxl001('{"k":"a\\uDFFFb"}', "孤立的低代理项")

    def test_isolated_high_surrogate_is_rejected(self):
        self.assert_pxl001('{"k":"\\uD800"}', "高代理项之后缺少低代理项")
        self.assert_pxl001('{"k":"\\uD800x"}', "高代理项之后缺少低代理项")
        self.assert_pxl001('{"k":"\\uD800\\u0041"}', "代理对不完整")

    def test_valid_surrogate_pair_is_one_code_point(self):
        value = parse('{"k":"\\uD83D\\uDE00"}')
        self.assertEqual(1, len(value["k"]), "代理对按一个码点计数（§3.4）")
        self.assertEqual("\U0001F600", value["k"])

    def test_surrogate_rejection_precedes_op_checks(self):
        # 缺陷是"孤立低代理 + 未知 op"同时成立:JSON 阶段先失败,所以是 PXL001 而不是
        # PXL004。这正是审查 #11 里 Python 与 Java 分叉的位置。
        document = (
            '{"format":"PXL","version":1,"encoding":"char","width":1,"height":1,'
            '"palette":["FFFFFFFF"],"layers":[{"name":"base","blend":"normal",'
            '"ops":[{"op":"bogus","value":"\\uDC00"}]}]}'
        )
        self.assert_pxl001(document, "JSON/Unicode 阶段先于 op 检查")


if __name__ == "__main__":
    unittest.main()
