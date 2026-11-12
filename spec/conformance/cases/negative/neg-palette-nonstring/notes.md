# negative/neg-palette-nonstring

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL001`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§3.2`(`palette` 是 array of string;项不是字符串属**类型类别**错误)、`§11` 的 `PXL001` / `PXL005` 分界、`§11.1` 阶段 3。

## 缺陷(只有这一处)

`palette` 里放了一个数字 `123` 而不是字符串。§3.2 把 `palette` 定义为 array of string,类型类别不符归 `PXL001`(阶段 3);`PXL005` 只管「是字符串但格式 / 容量不对」,两者不重叠。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL001` 失败,且**不产出任何输出文件**(不得留半份)。
