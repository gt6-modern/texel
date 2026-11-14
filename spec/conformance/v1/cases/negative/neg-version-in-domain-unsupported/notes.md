version 2 落在通用分派域 0..65535 之内、但不是本 compiler 支持的版本，按 v1§2 报 PXL007。

这条与 `neg-version-negative` / `neg-version-over-domain` 成对：那两条证明**域外**是 `PXL001`，
这条证明**域内但不支持**是 `PXL007`。三者一起把分派的两个分支都钉住，任何一条缺失，
实现就可以悄悄把「不支持」归成「语法错」，或者反过来。

走**生产入口**（Java 的 PxlCompiler、Python 的 v1 路径），不经 v0 的 PxlParser。
文档里除了 `version` 这一处没有任何其它内容，因此本目录只违反一条规则：
不合法的版本必须在**读其余字段之前**就被拒绝，而不是先报别的错。
