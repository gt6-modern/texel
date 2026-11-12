"""PXL 错误码(规范 §11)。

两个实现必须复用同一编号与同一触发条件;错误文本可以不同。

抽取侧(importer)只会产生两个码:
  * ``PXL006`` —— 输入位图不受支持(规范 §8.1 / §8.3 / §8.5),先于任何输出;
  * ``PXL005`` —— 色数超出该档容量(去重后 n >= 65536,§4.5),此时根本没有 PXL 文档可读。
"""

PXL001 = "PXL001"
PXL002 = "PXL002"
PXL003 = "PXL003"
PXL004 = "PXL004"
PXL005 = "PXL005"
PXL006 = "PXL006"

#: v0 固定的码空间(规范 §11.2),PXL007..PXL099 保留。
ALL_CODES = (PXL001, PXL002, PXL003, PXL004, PXL005, PXL006)

_CODES = frozenset(ALL_CODES)


class PxlError(Exception):
    """带稳定错误码的失败。

    ``code`` 必须是 :data:`ALL_CODES` 之一;任何其它值都是实现 bug,直接
    抛 ``ValueError`` 而不是悄悄生成一个不存在的码。
    """

    __slots__ = ("code", "message")

    def __init__(self, code, message):
        if code not in _CODES:
            raise ValueError("unknown PXL code: %r" % (code,))
        self.code = code
        self.message = message
        Exception.__init__(self, "%s: %s" % (code, message))

    def __str__(self):
        return "%s: %s" % (self.code, self.message)
