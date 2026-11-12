# negative/neg-priority-004-over-001-unknown-op-grid

- 形态:**负例**(§13.1)
- **受测侧**:渲染侧(目录内只有 `input.pxl`)
- 受测输入:`input.pxl`
- 期望错误码:`PXL004`(见 `expected.error`,一行 + LF)
- 缺陷数量:1(§13.1 / README §2.2 要求一个目录只放一个缺陷)

## 覆盖条款

`§3.4`(行数组规则只作用于已知 `grid` op 的 `grid` 字段与 `sprites` 的值)、`§11.1` 阶段 8。

## 缺陷(只有这一处)

**刻意的阶段序用例**:未知 op `circle` 带一个形似 `grid` 的成员,且其各行长度不等。那个成员不是「token 行数组」(§3.4 只约束已知 `grid` 与 `sprites`),故阶段 3 不报 `PXL001`,统一报 `PXL004`。

本目录**没有** `input.png`,`expected.pixels.sha256` 也不存在——负例不产出期望像素,只钉错误码与「不留输出文件」。**没有** `expected.pxl`:负例绝不放它。

## 判据

§13.2c:实现必须以 `PXL004` 失败,且**不产出任何输出文件**(不得留半份)。
