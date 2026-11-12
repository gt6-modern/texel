# negative/neg-numeric-slot-out-of-palette

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL005`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§11.1` 阶段 9(已知 op 的**数值** `slot` 不得超过该档上限或 `palette` 长度)、`§5.0`(`slot: 0` 合法)。

## 缺陷(只有这一处)

`palette` 只有 1 项,而 `fill` 的 `slot` 写成 2。这个数**不经过 token 解码**,阶段 6 只判它是不是 0..65535 的整数(是),判不出它越过 `palette` 长度;阶段 9 必须单独接收「数值 slot」这一类 → `PXL005`。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL005` 失败,且**不产出任何输出文件**(不得留半份)。
