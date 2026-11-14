# negative/neg-version-zero

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL007`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§12`(域内但不等于 1 的整数 `version` 报 `PXL007`)、Version dispatch 第 4 条(实现不得接受 version 0)、Extension §11.1。

## 缺陷(只有这一处)

`version` 写成 `0`:v0 曾是本文的旧版本号,current 实现必须**明确拒绝**而不是按 v0 语义渲染。词法上它是合法整数且落在分派域内,故是 `PXL007`;凡把「0」当作默认值或「缺省即 0」的实现都会在此失败。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL007` 失败,且**不产出任何输出文件**(不得留半份)。
