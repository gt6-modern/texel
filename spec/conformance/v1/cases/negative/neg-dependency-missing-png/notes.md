# v1/cases/negative/neg-dependency-missing-png

- 形态:`negative` 负例(PXL.md §13.3);`defect: dependency`
- **受测侧**:阶段 R2(§11.2 第 12 行:按固定顺序解析**被选中**的依赖,resolver 结果映射)
- 受测输入:`entry.pxl`
- 期望错误码:`PXL009`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(声明的 PNG 依赖不存在)

## 覆盖条款

- §8.2:`MISSING` → `PXL009`(与 `CASE_MISMATCH`/`ESCAPE` 的 `PXL008` 不是同一个码);
- §9.1:**只有被当前 target 实际选中的声明才会被读取**;未选中的声明不触发存在性检查;
- §5.1:`kind: "png"` 的声明不得出现 `params`(本用例没给);
- §5.3:PNG asset 作为 `stamp` 来源与 PXL asset 同形;
- §7:`legacy/x.png` 语法合法(全小写、单层目录、无 `..`),所以缺陷不可能被归到路径类。

## 缺陷(只有这一处)

`assets.legacy_tile.path = "legacy/x.png"`,而用例目录里**没有** `legacy/` 这一层。
它与 §9.1 的两半配合才使本条成为可能:路径语法合法(过 R1)、被 `stamp` 选中(真的要读)、
读不到(→ `MISSING` → `PXL009`)。

**与 `neg-path-escape-root` 的对照**:那条的 asset 没有被选中,永远走不到解析;本条的 asset
必须被选中,否则 `PXL009` 根本不会发生。两条合起来钉住 §9.1 的两半。

## 判定方式

实现必须以 `PXL009` 失败,且不产出任何输出文件。

## 待规范确认

1. 本条钉 `kind: "png"` 的缺失。PXL asset 缺失是否要独立一条(`neg-dependency-missing-pxl`)?
   按“一个负例一个缺陷”会是另一条用例;本阶段未添加,请规范指定。语义上两者同码同阶段
   (§8.2 / §9.1 不区分 kind)。

## 与 v0 的差异

v0 的依赖全是文档内数据(sprite/palette),不存在“读不到”的情形;v0 的 `PXL006` 管的是
**读到了但 PNG 非法**,v1 的 `PXL009` 管**读不到**,两码不许混(§8.3 / §11.1)。
