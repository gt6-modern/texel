# v1/cases/negative/neg-version1-unknown-field

- 形态:**`negative` 负例**(PRD §18.2 / PXL.md §13.3)
- **受测侧**:v1 文档结构校验(成员集合,PXL.md §11.2 阶段表第 4 行 = v0 阶段 3)
- 受测输入:`entry.pxl`
- 期望错误码:`PXL001`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(顶层多一个 `bogus` 成员)

## 覆盖条款

- PXL.md §3.2:v1 顶层成员集合**恰好**是 `format`/`version`/`width`/`height`/`encoding`/
  `palette`/`layers`/`sprites`/`assets`/`params`;**未列出的成员一律 `PXL001`**;
- PXL.md §11.2 阶段表第 4 行(v0 阶段 3):成员集合、类型类别、`format`/`version` 的具体值、
  图层名、`layers` 非空、已知 op 的参数集合 …… 全部 `PXL001`;
- **与 v0 同码**:v0 §3.2「顶层出现该表之外的字段即为 `PXL001`」,v1 不改这个码,只改那张表
  (v1 的表多了 `assets`/`params`,少了 v0 里保留的 `variant`/`bindings`)。

## 缺陷(只有这一处)

顶层多了一个 `bogus`。它不是 `params`/`assets`/`sprites` 中的任何一个,不属于 v1 §3.2 的十个成员,
所以是成员集合错 → `PXL001`。

文档其余部分与 `single-fill-4x4` 逐字节相同,只多了这一行;因此本条只违反一条规则。

## 这条钉住的是“位置”而不是“码”

`PXL001` 在 v1 阶段表里出现在两处(第 2 行阶段 D = 通用 JSON/分派、第 4 行 = v0 阶段 3),另有
v0 阶段 5(第 6 行)的 token 宽度检查也报 `PXL001`。本条**刻意不含参数**(无 `params`、无 `$name`
参引),因此它落在“**阶段 P 之后、其它一切值校验之前**”的那个 `PXL001` 上:
即使实现把阶段顺序写错(例如先查 `palette` 格式再查成员集合),本条的期望码也不会飘成 `PXL005`;
而若实现**完全不做成员集合检查**,它会接受这份文档并渲染成功 —— 于是本条失败。这正是它要抓的东西。

## 判定方式

同 `neg-version-missing`:实现必须以 `PXL001` 失败,且不产出任何输出文件。

## 待规范确认

1. 同 `neg-version-missing` 的待确认项 1(`negative` 用例的受测侧声明方式)。
2. 本条**不**顺带断言“阶段 P 与阶段 4 的相对顺序”(那需要一个同时含参数错与成员集合错的
   `neg-priority-*` 用例)。v1 是否需要 `neg-priority-*` 命名族(与 v0 一致)以标记“刻意违反多条
   规则”的用例,PRD §18.2 与 v1 §13.3 未写。→ 记入「待规范确认」,不在此处自行发明命名。
