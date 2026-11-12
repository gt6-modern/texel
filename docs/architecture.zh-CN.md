
# 架构

Texel 有三条独立产品路径：Python PNG importer、Java compiler 和 Python compiler。Importer 读取外部 PNG 并输出规范形式 PXL；两个 compiler 读取 PXL version 1、解析静态依赖图、替换有类型 bindings、渲染整数 RGBA8 像素并编码确定性 PNG。compiler 之间只共享权威规范与 conformance 语料。

`texel-core` 的运行时模块只有 `java.base`；两个 Python 包仅用标准库。构建集成只枚举 target、映射输入输出，不重实现语言语义。
