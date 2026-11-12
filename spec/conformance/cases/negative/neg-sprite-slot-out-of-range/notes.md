# negative/neg-sprite-slot-out-of-range

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL005`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§11.1` 阶段 9(**所有** token,含未被任何 `stamp` 引用的 sprite 定义)、`§3.7`。

## 缺陷(只有这一处)

`palette` 是 `[]`、`sprites` 是 `{"s": ["0"]}`、唯一的图层 `ops` 为空,且**没有任何 `stamp` 引用该 sprite**。字符 `0` 合法(阶段 5 通过),但它指向 slot 1,而 `palette` 长度为 0。未引用不等于不校验 → `PXL005`。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL005` 失败,且**不产出任何输出文件**(不得留半份)。
