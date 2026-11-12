# negative/neg-encoding-unknown

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL003`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§11.1` 阶段 4(`encoding` 取值是否在 {`char`,`index`,`index4`})、`§4.5`。

## 缺陷(只有这一处)

`encoding` 写成 `"hex"`。阶段 4 只判取值,先于任何依赖 token 宽度的检查,故必须是 `PXL003`。这一条也钉住「未知 `encoding` 搭配 `grid` 只可能得到 `PXL003`」——阶段 4 失败即返回,阶段 5 根本不执行。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL003` 失败,且**不产出任何输出文件**(不得留半份)。
