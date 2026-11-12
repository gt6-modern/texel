"""严格 JSON 解析器(v0 §3.1 / §3.2 / §3.8)。

为什么不用标准库 json:规范对 JSON 的要求比标准库的默认行为严,而每一条差异都会让两个实现
分叉。这里需要的能力是:

* 拒绝重复成员名(§3.2 明确「任何对象的成员名都不得重复」),而不是默默取末值;
* 拒绝 NaN / Infinity / -Infinity —— 它们不是合法 JSON;
* 保留数字字面量原文,交给 numlit 做精确十进制判定,绝不经过二进制浮点;
* 按码点计数(转义代理对 😀 解码后是 1 个码点,§3.4);
* 拒绝裸控制字符、孤立代理、以及可能把递归爆掉的深嵌套。

解析结果里的数字是 JsonNumber(保留原文),不是 float/int。
"""

import re

from .errors import PxlError

#: 嵌套深度上限。文档模型最深处是 layers[i].ops[j].from.x 一级,64 层远超任何合法文档;
#: 它只挡住 "[" * 60000000 这种把递归爆栈的输入。
# N08（发布态复核 P2）：Java 侧是 128，这里是 64 —— 同一份 100 层嵌套的文档，Java 能解析、
# Python 报 PXL001，属于「错误码跨实现分叉」。两边对齐到 128，并在规范里写明这个边界。
MAX_DEPTH = 128

_WHITESPACE = " \t\n\r"
_HEX_DIGITS = "0123456789abcdefABCDEF"
#: 空白跳过用 C 层的正则而不是逐字符循环:64 MiB 的合法文档(§5.0.2 的上界)尾部可能全是
#: 空白,逐字符循环在 Python 里要几十秒,而"判合法"不该以时间为前提。
_WHITESPACE_RUN = re.compile(r"[ \t\n\r]*")


class JsonNumber(object):
    """一个 JSON 数字字面量,保留原文用于精确十进制判定。"""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "JsonNumber(" + self.text + ")"


class _Parser(object):
    __slots__ = ("text", "pos", "length")

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def error(self, message):
        raise PxlError("PXL001", "JSON: " + message + " (位于第 " + str(self.pos) + " 个码点)")

    def skip(self):
        if self.pos < self.length and self.text[self.pos] in _WHITESPACE:
            self.pos = _WHITESPACE_RUN.match(self.text, self.pos).end()

    def parse_document(self):
        self.skip()
        if self.pos >= self.length:
            self.error("文档为空")
        value = self.parse_value(0)
        self.skip()
        if self.pos != self.length:
            self.error("顶层值之后还有多余内容")
        return value

    def parse_value(self, depth):
        if depth > MAX_DEPTH:
            self.error("嵌套层数超过 " + str(MAX_DEPTH))
        if self.pos >= self.length:
            self.error("值不完整")
        char = self.text[self.pos]
        if char == "{":
            return self.parse_object(depth)
        if char == "[":
            return self.parse_array(depth)
        if char == "\"":
            return self.parse_string()
        if char == "t":
            return self.expect_word("true", True)
        if char == "f":
            return self.expect_word("false", False)
        if char == "n":
            return self.expect_word("null", None)
        if char == "-" or char.isdigit():
            return self.parse_number()
        self.error("无法识别的值起始字符")

    def expect_word(self, word, value):
        if self.text[self.pos:self.pos + len(word)] != word:
            self.error("期望字面量 " + word)
        self.pos += len(word)
        return value

    def parse_object(self, depth):
        self.pos += 1
        result = {}
        self.skip()
        if self.pos < self.length and self.text[self.pos] == "}":
            self.pos += 1
            return result
        while True:
            self.skip()
            if self.pos >= self.length or self.text[self.pos] != "\"":
                self.error("对象的成员名必须是字符串")
            name = self.parse_string()
            if name in result:
                self.error("同一对象内成员名重复: " + name)
            self.skip()
            if self.pos >= self.length or self.text[self.pos] != ":":
                self.error("成员名之后缺少冒号")
            self.pos += 1
            self.skip()
            result[name] = self.parse_value(depth + 1)
            self.skip()
            if self.pos >= self.length:
                self.error("对象未闭合")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                continue
            if char == "}":
                self.pos += 1
                return result
            self.error("对象成员之间缺少逗号")

    def parse_array(self, depth):
        self.pos += 1
        result = []
        self.skip()
        if self.pos < self.length and self.text[self.pos] == "]":
            self.pos += 1
            return result
        while True:
            self.skip()
            result.append(self.parse_value(depth + 1))
            self.skip()
            if self.pos >= self.length:
                self.error("数组未闭合")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                continue
            if char == "]":
                self.pos += 1
                return result
            self.error("数组元素之间缺少逗号")

    def parse_string(self):
        self.pos += 1
        pieces = []
        while True:
            if self.pos >= self.length:
                self.error("字符串未闭合")
            char = self.text[self.pos]
            if char == "\"":
                self.pos += 1
                return "".join(pieces)
            if char == "\\":
                self.pos += 1
                pieces.append(self.parse_escape())
                continue
            if char < " " or char == "\x7f":
                # 裸控制字符不是合法 JSON;DEL 一并拒绝(规范只允许 U+0020..U+007E 的可打印 ASCII)。
                if char < " ":
                    self.error("字符串里有裸控制字符")
            pieces.append(char)
            self.pos += 1

    def parse_escape(self):
        if self.pos >= self.length:
            self.error("转义序列不完整")
        char = self.text[self.pos]
        simple = {"\"": "\"", "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                  "n": "\n", "r": "\r", "t": "\t"}
        if char in simple:
            self.pos += 1
            return simple[char]
        if char != "u":
            self.error("非法的转义字符")
        self.pos += 1
        code = self.read_hex4()
        if 0xD800 <= code <= 0xDBFF:
            if self.text[self.pos:self.pos + 2] != "\\u":
                self.error("高代理项之后缺少低代理项")
            self.pos += 2
            low = self.read_hex4()
            if not 0xDC00 <= low <= 0xDFFF:
                self.error("代理对不完整")
            combined = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
            return chr(combined)
        if 0xDC00 <= code <= 0xDFFF:
            self.error("孤立的低代理项")
        return chr(code)

    def read_hex4(self):
        chunk = self.text[self.pos:self.pos + 4]
        if len(chunk) != 4:
            self.error("\\u 转义缺少 4 位十六进制")
        for char in chunk:
            if char not in _HEX_DIGITS:
                self.error("\\u 转义含非十六进制字符")
        self.pos += 4
        return int(chunk, 16)

    @staticmethod
    def _digit(ch):
        # 只认 ASCII 0-9：str.isdigit() 还把阿拉伯-印度数字等当成数字，
        # 而 Java 在 JSON 语法层就拒绝（PXL001）。
        return "0" <= ch <= "9"

    def parse_number(self):
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        if self.pos >= self.length:
            self.error("数字不完整")
        if self.text[self.pos] == "0":
            self.pos += 1
        elif self._digit(self.text[self.pos]):
            while self.pos < self.length and self._digit(self.text[self.pos]):
                self.pos += 1
        else:
            self.error("数字缺少整数部分")
        if self.pos < self.length and self.text[self.pos] == ".":
            self.pos += 1
            if self.pos >= self.length or not self._digit(self.text[self.pos]):
                self.error("小数点之后缺少数字")
            while self.pos < self.length and self._digit(self.text[self.pos]):
                self.pos += 1
        if self.pos < self.length and self.text[self.pos] in "eE":
            self.pos += 1
            if self.pos < self.length and self.text[self.pos] in "+-":
                self.pos += 1
            if self.pos >= self.length or not self._digit(self.text[self.pos]):
                self.error("指数部分缺少数字")
            while self.pos < self.length and self._digit(self.text[self.pos]):
                self.pos += 1
        return JsonNumber(self.text[start:self.pos])


def parse(text):
    """把文本解析成 JSON 值树;任何不合法的输入都抛 PXL001。"""
    return _Parser(text).parse_document()


def decode_utf8(data):
    """按 UTF-8 解码并忽略开头 BOM(v0 §3.8 的接受集)。失败即 PXL001。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as failure:
        raise PxlError("PXL001", "文档不是合法 UTF-8: " + str(failure))
    if text.startswith("\ufeff"):
        text = text[1:]
    return text
