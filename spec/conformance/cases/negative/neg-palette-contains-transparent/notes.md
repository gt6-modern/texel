# negative/neg-palette-contains-transparent

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL005`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§2`(slot 0 保留给透明,不占 `palette`)、`§3.2`、`§11.1` 阶段 9。

## 缺陷(只有这一处)

`palette` 里出现 `"00000000"`。透明 slot 是保留值,`palette` 只存非透明色,出现即 `PXL005`。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL005` 失败,且**不产出任何输出文件**(不得留半份)。
