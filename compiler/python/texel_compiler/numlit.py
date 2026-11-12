"""JSON 数字字面量的精确十进制判定(v0 §3.8 / §11)。

规范要求「整数性与区间必须按数字的原始词法做精确十进制判断,不得先转成二进制浮点」:
1.0000000000000001 与 1 不相等,而常见双精度解析会把它舍成 1.0 从而误接受。
宿主语言自带的浮点解析全部不可用,因此这里保留字面量原文并自己做十进制算术。

对外只暴露一个函数 integer_value,返回:

  int   —— 该字面量恰好是整数值,且在 40 位有效数字以内;
  None  —— 不是整数值(带非零小数部分);
  HUGE  —— 是整数值,但量级远超规范任何上界(避免把 1e99999 真的展开成十万位整数)。
"""


class _Huge(object):
    """整数值但量级越界。与 int 比较时一律「大于任何上界」。"""

    __slots__ = ()

    def __repr__(self):
        return "<huge>"


HUGE = _Huge()

#: 有效数字位数上限:规范里最大的数字是 65536 与 2^63,40 位足够,再大就不必展开。
_MAX_SIGNIFICANT = 40


def _exponent_value(text):
    """解析指数部分的十进制整数值;超过 6 位有效数字时按其符号返回哨兵(±10^7)。"""
    if not text:
        return None
    negative = text[0] == "-"
    if text[0] in "+-":
        text = text[1:]
    if not _ascii_digits(text):
        return None
    stripped = text.lstrip("0")
    if not stripped:
        return 0
    if len(stripped) > 6:
        return -10 ** 7 if negative else 10 ** 7
    value = int(stripped, 10)
    return -value if negative else value


def _ascii_digits(text):
    """只认 ASCII 0-9：str.isdigit() 还会接受阿拉伯-印度数字等 Unicode 数字，
    而 §6.4 的规范形式是十进制字面量（发布态复核：非法 Unicode 数字当时被接受）。"""
    return bool(text) and all('0' <= ch <= '9' for ch in text)

def canonical_integer(text):
    """§6.4 的规范整数拼写：可选负号 + 十进制数字，无小数点、无指数、无前导零。

    integer_value 判的是**数学值**（1.0、1e0 都算 1），而文档里的整数参数值必须是这个
    规范拼写，否则 Java 报 PXL012 而 Python 接受（发布态复核 P2）。
    """
    if not isinstance(text, str) or not text:
        return False
    body = text[1:] if text.startswith("-") else text
    if not _ascii_digits(body):
        return False
    return body == "0" or not body.startswith("0")


def integer_value(text):
    """按字面量判定整数值。见模块文档。"""
    if not isinstance(text, str) or not text:
        return None
    negative = False
    if text[0] == "-":
        negative = True
        text = text[1:]
    exponent = 0
    for marker in ("e", "E"):
        index = text.find(marker)
        if index >= 0:
            exponent = _exponent_value(text[index + 1:])
            if exponent is None:
                return None
            text = text[:index]
            break
    if "." in text:
        int_part, _, frac_part = text.partition(".")
    else:
        int_part, frac_part = text, ""
    digits = int_part + frac_part
    if not _ascii_digits(digits):
        return None
    scale = exponent - len(frac_part)
    significant = digits.lstrip("0")
    if not significant:
        return 0
    if scale < 0:
        need = -scale
        if need >= len(significant):
            # 非零数字全部落进小数部分 -> 不是整数(例如 0.5、0.0001)
            return None
        if significant[len(significant) - need:].strip("0"):
            return None
        significant = significant[:len(significant) - need]
        if not significant:
            return 0
    elif scale > 0:
        if len(significant) + scale > _MAX_SIGNIFICANT:
            return HUGE
        significant = significant + "0" * scale
    if len(significant) > _MAX_SIGNIFICANT:
        return HUGE
    value = int(significant, 10)
    return -value if negative else value
