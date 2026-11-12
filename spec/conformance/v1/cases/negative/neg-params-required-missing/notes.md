# v1/cases/negative/neg-params-required-missing

- 形态:`negative` 负例(PXL.md §13.3);`defect: params`
- **受测侧**:阶段 P(bindings 集合完整性)
- 受测输入:`entry.pxl` + `bindings.json`
- 期望错误码:`PXL012`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(必需参数没有绑定)

## 覆盖条款

- §6.1:`缺 `default` 的参数是**必需参数**``;
- §6.3:`必需参数缺失 → `PXL012``;
- §11.2 阶段 P(在 v0 阶段 3 之前)。

## 缺陷(只有这一处)

`params.base_color` 是 `color` 且**没有 `default`** → 必需参数;`bindings.json` 的 target `main`
给的是**空对象** `{}`。于是替换无从下手 —— 而 `palette[0]` 正引用着它。

注意缺陷**不是**“palette 项格式非法”:替换根本没发生,阶段 P 就该失败,不会走到阶段 9 的
palette 校验(§6.5 第 2 步“本阶段失败就结束”)。这正是 §11.2 第 3 行排在第 4 行之前的意义。

与 `neg-params-undeclared` 的分工:那条是“多给了一个没声明的”,本条是“少给了一个必需的”。
两条都报 `PXL012`,但打的是绑定的两端:集合的上界与下界。

## 判定方式

实现必须以 `PXL012` 失败,且不产出任何输出文件。

## 待规范确认

1. 同 `neg-params-undeclared` 的待确认项 1(`negative` 用例借用 `bindings.json` 是否合规)。

## 与 v0 的差异

v0 没有参数,不存在“必需参数缺失”;v0 文档里 palette 项缺失只可能是格式错(`PXL005`)。
