# negative/neg-png-duplicate-chunk

- 形态:**负例**(§13.1)
- **受测侧**:抽取侧(目录内只有 `input.png`,抽取器根本不读 PXL)
- 受测输入:`input.png`
- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§8.5`(会被读取的 chunk —— `IHDR` / `PLTE` / `tRNS` —— 各自至多一个)。

## 缺陷(只有这一处)

出现两块 `PLTE`。`PLTE` 是会被读取的 chunk,多一个即拒绝。**不要**拿 `pHYs` / `tEXt` 之类构造此例:它们重复是合法的(对照 `syn-png-two-text-chunks`)。

`input.png` 实测 **205 字节**;本目录没有 `input.pxl`,也没有任何期望像素文件。

## 判据

§13.2c:实现必须以 `PXL006` 失败,且**不产出任何输出文件**(不得留半份)。
