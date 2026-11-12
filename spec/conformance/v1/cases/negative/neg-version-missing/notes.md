# v1/cases/negative/neg-version-missing

- 形态:**`negative` 负例**(PRD §18.2 / PXL.md §13.3)
- **受测侧**:版本分派(通用 JSON 对象检查之后的 `format`/`version` 读取与分派,PXL.md §2)
- 受测输入:`entry.pxl`
- 期望错误码:`PXL001`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(缺 `version` 这一处)

## 覆盖条款

- PXL.md §2 分派域表第一行:`version` **缺失** → `PXL001`;
- PXL.md §11.2 阶段表第 2 行(**v1 阶段 D**):通用 JSON 对象检查后读 `format` 与 `version` 并分派;
- PRD §6.2:`version` 的通用分派域固定为 `0..65535`,缺失/类型不符/非整数/负数/超 65535 一律 `PXL001`。

## 缺陷(只有这一处)

顶层**没有 `version` 成员**。它不是“某个版本的值不对”,而是**连分派都做不到**:§2 的三行分派表
只能覆盖“读到了 version”的三种情形,读不到就不再往下走,直接 `PXL001`(阶段 D)。
这条与 v0 的 `negative/neg-version-nonzero` 是**两件事**:那条是“读到 0 以外的值、由 v0 拒绝”,
这条是“根本读不到版本、由分派前置条件拒绝”。

文档其余部分与 `single-fill-4x4` 逐字节相同(只少了 `version` 这一行),因此本目录只违反一条规则:
凡 `version` 之外的检查(`format` 值、成员类型、token、数值域、palette、slot)在本条上**都成立**,
不会被误当成“这条其实测的是别的错”。

## 判定方式

按 `v1/README.md` §2.4:实现必须以 `PXL001` 失败,且**不产出任何输出文件**(不得留半份)。

## 待规范确认

1. **受测侧的声明方式**:v0 §13.1 靠“目录里是 `input.pxl` 还是 `input.png`”判定受测侧;v1 只有
   `entry.pxl` 一种入口名,而 v1 的错误可能来自 version gate、resolver(`PXL008`/`PXL009`)、
   PNG decoder(`PXL006`)或渲染阶段。**v1 索引是否需要一列显式的“受测侧/载体”**,PRD §18.2 与
   v1 §13.3 都未定义。本阶段把受测侧写进 `notes.md`(本行)与 `CASES.tsv` 的 `covers` 前缀,
   不改索引列 —— 若规范后续加列,索引与 runner 一并改。
