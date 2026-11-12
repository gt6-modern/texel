# negative/neg-sprite-tokens-over-limit

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL002`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§3.4`(上界适用于**凡出现 token 行数组的地方**:`grid` 字段与 `sprites` 的每个值)、`§11.1` 阶段 6(`行 token 数上界`)。

## 缺陷(只有这一处)

一个 sprite 的单行有 65537 个 token,超过 65536 上界。只查 `grid` 不查 `sprites` 就会放过它——同一份文档在另一个实现里会被拒,这是实质分叉。本例的 `ops` 为空,数据全在 `sprites` 里。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL002` 失败,且**不产出任何输出文件**(不得留半份)。
