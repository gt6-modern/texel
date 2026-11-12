
# Texel

[English](README.md) · [PXL 中文规范（权威文本）](spec/PXL.md) · [English translation](spec/PXL.en.md)

Texel 是与引擎无关的位图 ⇄ 文本镜像工具链：它把 RGBA8 位图无损写成可读、可审查的 PXL 源文件，再把 PXL 确定性地编译成 PNG。

PXL 让一次像素改动表现为“某个坐标上的 token”，而不是不可读的二进制差异。它既可以作为现有 PNG 的文本镜像，也可以作为可组合像素素材的源格式；最终部署物仍然是 PNG。

> **版本状态：** 0.1.0 是首个公开的 pre-1.0 版本；只支持 PXL wire version 1。

## 组件

| 组件 | 用途 | 运行环境 |
| --- | --- | --- |
| `texel-importer` | PNG → PXL 迁移与外部工具回导 | Python 3.8+，仅标准库 |
| `texel-compiler` | 独立 Python PXL compiler | Python 3.8+，仅标准库 |
| `texel-core` | 可嵌入的 Java compiler | Java 8 字节码，仅 `java.base` |
| `texel-cli` | Java 命令行 compiler | Java 8+ |

## 五分钟上手

```console
python -m pip install texel-importer texel-compiler
texel-import texture.png
python -m texel_compiler compile texture.pxl --asset-root . -o rebuilt.png
```

Importer 会在 PNG 旁生成自包含的 `version: 1` PXL。构建集成和 Java CLI 用法见[快速开始](docs/getting-started.zh-CN.md)。

## 核心保证

- 往返等价按逐行 RGBA8 原始像素判定，不要求 PNG 文件字节相同。
- 调色板、渲染、依赖清单和 PNG 输出均确定。
- 颜色运算只用整数；任何操作都不产生半覆盖像素。
- 只有精确的 `00000000` 使用透明 slot；RGB 非零但 alpha 为 0 的颜色仍保留。
- 16-bit 与 Adam7 PNG 会被明确拒绝。

## 构建与测试

```console
python spec/conformance/tools/gen_cases.py --all
python -m unittest discover -s tests
python compiler/python/tools/conformance.py
cd compiler/java
./gradlew check -Ppython=python
```

完整门禁见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。语言唯一裁决文本是 [`spec/PXL.md`](spec/PXL.md)；两个 compiler 只共享规范和 conformance 语料，不共享生产代码。

代码使用 [Apache License 2.0](LICENSE)。样本素材采用单独的 CC0 来源记录，见 [`samples/PROVENANCE.md`](samples/PROVENANCE.md) 与 [`samples/MANIFEST.tsv`](samples/MANIFEST.tsv)。
