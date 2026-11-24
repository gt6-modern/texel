
# Texel

[English](https://github.com/gt6-modern/texel/blob/master/README.md) · [PXL 中文规范（权威文本）](https://github.com/gt6-modern/texel/blob/master/spec/PXL.md) · [English translation](https://github.com/gt6-modern/texel/blob/master/spec/PXL.en.md)

Texel 是引擎无关的位图 ⇄ 文本镜像工具链：它把 RGBA8 位图无损写成可读、可审查的 PXL 源文件，再把 PXL 确定性地编译成 PNG。

PXL 让一次像素改动表现为「某个坐标上的 token」，而不是不可读的二进制差异。它既可以作为现有 PNG 的文本镜像，也可以作为可组合像素素材的源格式；最终部署物仍然是 PNG。

> **版本状态：** 0.1.0 是首个公开的 pre-1.0 版本；只支持 PXL wire version 1。

## 组件

| 组件 | 用途 | 运行环境 |
| --- | --- | --- |
| [`texel-importer`](https://pypi.org/project/texel-importer/) | PNG → PXL 迁移与外部工具回导 | Python 3.8+，仅标准库 |
| [`texel-compiler`](https://pypi.org/project/texel-compiler/) | 独立 Python PXL compiler | Python 3.8+，仅标准库 |
| [`texel-core`](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-core) | 可嵌入的 Java compiler | Java 8 字节码，仅 `java.base` |
| [`texel-cli`](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-cli) | Java 命令行 compiler | Java 8+ |

importer 与 compiler 是同一个镜像的两半，各自独立发布：importer 写出 PXL，compiler 把它读回 PNG。两套独立的 compiler（Python 与 Java）并存，是为了让「对规范的误读」无法悄悄过关；它们只共享规范和 conformance 语料，不共享生产代码。

## 五分钟上手

```console
python -m pip install texel-importer texel-compiler
texel-import texture.png
python -m texel_compiler compile texture.pxl --asset-root . -o rebuilt.png
```

importer 会在 PNG 旁生成自包含的 `version: 1` PXL。构建集成和 Java CLI 用法见[快速开始](https://github.com/gt6-modern/texel/blob/master/docs/getting-started.zh-CN.md)。

## 注册表

| 坐标 | 注册表 |
| --- | --- |
| `texel-importer` | [pypi.org/project/texel-importer](https://pypi.org/project/texel-importer/) |
| `texel-compiler` | [pypi.org/project/texel-compiler](https://pypi.org/project/texel-compiler/) |
| `io.github.gt6-modern:texel-core` | [central.sonatype.com](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-core) |
| `io.github.gt6-modern:texel-cli` | [central.sonatype.com](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-cli) |

四者由同一个 tag 发布同一个版本，与 GitHub Release 上的资产同源。

最小 PXL 文档：

```json
{
  "format": "PXL",
  "version": 1,
  "encoding": "char",
  "width": 1,
  "height": 1,
  "palette": ["FF0000FF"],
  "layers": [{
    "name": "base",
    "blend": "normal",
    "ops": [{"op": "fill", "slot": 1}]
  }]
}
```

## 核心保证

- 往返等价按逐行 RGBA8 原始像素判定，不要求 PNG 文件字节相同。
- 调色板、渲染、依赖清单和 PNG 输出均确定。
- 颜色运算只用整数；任何操作都不产生半覆盖像素。
- 只有精确的 `00000000` 使用透明 slot；RGB 非零但 alpha 为 0 的颜色仍保留。
- 16-bit 与 Adam7 PNG 会被明确拒绝。

## 构建与测试

```console
python spec/conformance/tools/gen_cases.py --all
python spec/conformance/tools/check_spec_sync.py
python tools/check_release_metadata.py
python -m unittest discover -s tests
python spec/conformance/tools/python_ref_verify.py
python compiler/python/tools/conformance.py
cd compiler/java
./gradlew --no-daemon clean check -Ppython=python
```

完整门禁见 [CONTRIBUTING.zh-CN.md](https://github.com/gt6-modern/texel/blob/master/CONTRIBUTING.zh-CN.md)。语言唯一裁决文本是 [`spec/PXL.md`](https://github.com/gt6-modern/texel/blob/master/spec/PXL.md)；两个 compiler 只共享规范和 conformance 语料，不共享生产代码。

## 社区与安全

- 提问：[SUPPORT.zh-CN.md](https://github.com/gt6-modern/texel/blob/master/SUPPORT.zh-CN.md)
- 贡献：[CONTRIBUTING.zh-CN.md](https://github.com/gt6-modern/texel/blob/master/CONTRIBUTING.zh-CN.md)
- 漏洞：[SECURITY.zh-CN.md](https://github.com/gt6-modern/texel/blob/master/SECURITY.zh-CN.md)
- 行为准则：[CODE_OF_CONDUCT.zh-CN.md](https://github.com/gt6-modern/texel/blob/master/CODE_OF_CONDUCT.zh-CN.md)

代码使用 [Apache License 2.0](https://github.com/gt6-modern/texel/blob/master/LICENSE)。样本素材采用单独的 CC0 来源记录，见 [`samples/PROVENANCE.md`](https://github.com/gt6-modern/texel/blob/master/samples/PROVENANCE.md) 与 [`samples/MANIFEST.tsv`](https://github.com/gt6-modern/texel/blob/master/samples/MANIFEST.tsv)。
