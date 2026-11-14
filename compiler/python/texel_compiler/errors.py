"""PXL 错误码与异常(v0 §11 / v1 §11)。

错误码是规范的一部分:两个实现必须复用同一编号与同一触发条件,只有文本可以不同。
本模块只负责携带"码 + 一句话 + 引用链",不做任何判定。
"""

CODES = (
    "PXL001",  # 语法错 / 结构 / 枚举与字面量
    "PXL002",  # 尺寸 / 数值域 / 资源上界
    "PXL003",  # 未知或不符的 encoding
    "PXL004",  # 未知 op
    "PXL005",  # 调色板 / slot 引用错
    "PXL006",  # 输入位图不受支持
    "PXL007",  # 版本在分派域内但本实现不支持
    "PXL008",  # 逻辑路径非法 / 逃逸 / 大小写不匹配
    "PXL009",  # 依赖缺失 / 不可读 / 声明类型与实际资源不符
    "PXL010",  # 循环引用
    "PXL011",  # 会话预算超限
    "PXL012",  # 参数 / bindings 错
)


class PxlError(Exception):
    """一次 PXL 语义失败。code 必须是 PXL001..PXL012 之一。"""

    def __init__(self, code, message, chain=None):
        if code not in CODES:
            raise ValueError("未知错误码: " + str(code))
        Exception.__init__(self, code + ": " + message)
        self.code = code
        self.message = message
        #: v1 §9.3:入口 -> ... -> 实际失败文件的逻辑路径链。v0 场景下为空。
        self.chain = list(chain) if chain else []

    def describe(self):
        text = self.code + ": " + self.message
        if self.chain:
            text = text + " (引用链: " + " -> ".join(self.chain) + ")"
        return text


def fail(code, message):
    raise PxlError(code, message)
