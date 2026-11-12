# negative/neg-png-unknown-critical

- 形态:**负例**(§13.1)
- **受测侧**:抽取侧(目录内只有 `input.png`,抽取器根本不读 PXL)
- 受测输入:`input.png`
- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§8.5`(未知 **critical** chunk —— 首字母大写 —— 即拒绝;未知 ancillary 才忽略)。

## 缺陷(只有这一处)

IHDR 之后插入一个类型名为 `PrVt` 的 chunk:四个字节全是字母、第三个字节大写(保留位 0)、首字母 `P` 大写 → critical,且是未知类型。必须拒绝,不能当成 ancillary 跳过。

`input.png` 实测 **117 字节**;本目录没有 `input.pxl`,也没有任何期望像素文件。

## 判据

§13.2c:实现必须以 `PXL006` 失败,且**不产出任何输出文件**(不得留半份)。
