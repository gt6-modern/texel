# negative/neg-unknown-field

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL001`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§3.2`(顶层出现该表之外的字段即 `PXL001`)、`§12`(`variant` 为 reserved,出现即 fail-closed;`bindings` 是调用期输入、不是文档字段)、`§11.1` 阶段 3。

## 缺陷(只有这一处)

顶层多了一个 `"extra"` 成员。§3.2 把顶层成员集合钉死为 8 个字段,多一个即 `PXL001`(阶段 3)——`variant` 这个 reserved 字段走的是同一条规则;`bindings` 属 Extension §6.3 的调用期输入,写成顶层成员同样按未定义字段拒绝。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL001` 失败,且**不产出任何输出文件**(不得留半份)。
