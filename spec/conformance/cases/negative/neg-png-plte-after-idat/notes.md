# negative/neg-png-plte-after-idat

- 形态:**负例**(§13.1)
- **受测侧**:抽取侧(目录内只有 `input.png`,抽取器根本不读 PXL)
- 受测输入:`input.png`
- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§8.5`(`PLTE` 若存在必须早于第一个 `IDAT`)。

## 缺陷(只有这一处)

chunk 顺序是 `IHDR → IDAT → PLTE → IEND`,CRC 全部正确、像素数据也可解码。§8.5 要求 `PLTE` 早于第一个 `IDAT`;不钉这条,「先收全文件再解码」与「流式解码」会分叉。

`input.png` 实测 **134 字节**;本目录没有 `input.pxl`,也没有任何期望像素文件。

## 判据

§13.2c:实现必须以 `PXL006` 失败,且**不产出任何输出文件**(不得留半份)。
