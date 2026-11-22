"""机器可读能力档案(PRD §6.3 / v1 §13.2)。

schema version 1 只允许 PRD 列出的七个键。手写常量字符串而不是 json.dumps:成员顺序与空白
都必须确定(同一实现的输出字节必须相同),而且嵌套的 error_code_ranges 在 fixture 里是
单行写法,让序列化器决定排版就会和 PRD 的样例形状分叉。
"""

CAPABILITIES_VERSION = 1
#: 与 Java CLI 的 ``Capabilities.java`` 和 ``spec/conformance/capabilities/pxl-v1.json``
#: **同一个值**：能力档案是跨实现的对外契约,不是"本实现自报家门",按实现分叉就没有
#: 消费者能依赖它(审查 #12)。
COMPILER_ID = "io.github.texel.compiler"
COMPILER_VERSION = "0.1.0"
CONFORMANCE_PROFILE = "pxl-v1"
LANGUAGE_VERSIONS = (1,)
PNG_INPUT_PROFILE = "pxl-png"

#: 逐字节等于共享 fixture:嵌套数组/对象按 fixture 的展开式排版(不是压缩成一行),
#: 末尾恰好一个 LF。让序列化器决定排版就会和 Java 输出分叉,所以这里全手写。
_TEXT = (
    "{\n"
    '  "capabilities_version": ' + str(CAPABILITIES_VERSION) + ',\n'
    '  "compiler_id": "' + COMPILER_ID + '",\n'
    '  "compiler_version": "' + COMPILER_VERSION + '",\n'
    '  "conformance_profile": "' + CONFORMANCE_PROFILE + '",\n'
    '  "language_versions": [\n'
    + ",\n".join('    ' + str(v) for v in LANGUAGE_VERSIONS) + "\n"
    "  ],\n"
    '  "error_code_ranges": {\n'
    '    "1": {\n'
    '      "min": "PXL001",\n'
    '      "max": "PXL012"\n'
    "    }\n"
    "  },\n"
    '  "png_input_profile": "' + PNG_INPUT_PROFILE + '"\n'
    "}\n"
)


def json_text():
    """能力档案的规范字节:UTF-8、无 BOM、单个 JSON 对象、末尾恰好一个 LF。"""
    return "".join(_TEXT)