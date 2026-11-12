# block-fluid-source-thoriumsalt

- 形态:**已知缺陷成员**(§13.7),按负例判据(§13.2c)判定
- **受测侧**:抽取侧(目录内只有 `input.png`)
- 受测输入:`input.png`(来源 `block/fluid/source/thoriumsalt.png`)
- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)
- 所属用例:`real-fluid-source-255`(批量形态,§13.7)
- `manifest.tsv` 的 `input` 列:`sample:block/fluid/source/thoriumsalt.png`;该行 `colors`/`encoding` 写 `-`

## 覆盖条款

`§8.5`(**每一个 chunk 的 CRC 都必须校验**,不逐类型列举)、`§11`(`PXL006` 的范围包含 §8.5 列出的全部结构与校验拒绝项)、`§13.7`(已知缺陷成员)。

## 为什么这是素材缺陷而不是实现缺陷

该文件的 `iCCP` chunk **CRC 损坏**:

```
chunk 类型  : iCCP
存储 CRC    : EEF9409D
计算 CRC    : D7FE4025   (CRC-32 over 类型名 + 数据)
```

其余 chunk(含 IDAT、IEND)的 CRC 全部正确,pixels 完好,IDAT 解压字节数也与
`height × (1 + width × 4)` 严格吻合——**像素与容器合法性是两件事**。
§8.5 规定 CRC 一律校验,所以 v0 只能拒绝它:`PXL006`。把它写成往返正例,要么迫使实现放宽 CRC,要么让用例永远失败,两者都不可接受。

## 判据

§13.2c:实现必须以 `PXL006` 失败,且**不产出任何输出文件**。
本目录**没有** `expected.pxl`、**没有** `expected.pixels.sha256`——缺陷成员不可能有期望像素。
