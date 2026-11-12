
# 快速开始

用 `python -m pip install texel-importer texel-compiler` 安装两个 Python 工具。执行 `texel-import image.png` 生成 `image.pxl`，再用 `python -m texel_compiler compile image.pxl --asset-root . -o image.png` 编译。Importer 只用于迁移和外部绘图工具回导；正常构建应直接编译已纳入版本控制的 PXL。

Java CLI 从 GitHub Releases 获取；解压后运行 `bin/texel-compiler capabilities --json` 查看能力档案。库使用 `io.github.gt6-modern:texel-core:0.1.0`，CLI artifact 为 `io.github.gt6-modern:texel-cli:0.1.0`。

PXL 逻辑路径只能在显式 asset root 内使用 `/` 分隔。通过 `--deps dependencies.tsv` 生成确定性依赖清单供增量构建使用。更多内容见[架构](architecture.zh-CN.md)和[中文权威规范](../spec/PXL.md)。
