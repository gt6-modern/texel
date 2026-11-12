# negative/neg-palette-lowercase

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL005`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§3.2`(`palette` 项必须匹配 `^[0-9A-F]{8}$`)、`§11.1` 阶段 9。

## 缺陷(只有这一处)

`palette` 项写成小写 `ff0000ff`。它是字符串(阶段 3 类型类别通过),但不匹配大写十六进制 → `PXL005`。与 `negative/neg-palette-nonstring` (非字符串 → `PXL001`)配对,钉住 §3.2 的三层判据。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL005` 失败,且**不产出任何输出文件**(不得留半份)。
