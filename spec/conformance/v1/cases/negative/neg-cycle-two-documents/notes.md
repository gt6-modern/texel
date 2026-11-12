# v1/cases/negative/neg-cycle-two-documents

- 形态:`negative` 负例(PXL.md §13.3);`defect: cycle`
- **受测侧**:阶段 R3(循环检测)
- 受测输入:`entry.pxl`、`parts/b.pxl`、`parts/back.pxl`
- 期望错误码:`PXL010`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(两节点环)

## 目录与引用链

```text
entry.pxl         assets.b     -> parts/b.pxl
parts/b.pxl       assets.back  -> back.pxl     (相对它自己所在目录 parts/)
parts/back.pxl    assets.again -> b.pxl        (同样相对 parts/)
```

即 `entry → b → back → b`:环由 **b 与 back 两个节点**构成,入口只是进入环的那条边。
三个文档自身都合法(画布 1×1 / 2×2、单图层、单 stamp),**唯一**缺陷是环。

## 覆盖条款

- §9.2:循环检测身份 = 规范化逻辑路径,不考虑 bindings;同一路径再次出现在当前递归栈中即为循环;
- §7 **相对解析**:`parts/b.pxl` 里写的 `back.pxl` 是相对 `parts/` 解析的。这一条顺带钉住
  实现别把“相对声明者所在目录”写成“相对 root”:写成相对 root 就会去找 `back.pxl` → 不存在 →
  `PXL009`,而本条的期望码是 `PXL010`;
- §11.2:阶段 R3 先于 R4,所以不是 `PXL011`。

## 与 `neg-cycle-self` 的分工

那条是**一个**节点自环(栈里第二次出现同一路径);本条是**两个**节点互引 —— 需要栈保留多个
路径才能发现。只实现“当前路径 == 上一条路径”的实现能过前者、过不了后者。

## 判定方式

实现必须以 `PXL010` 失败,且不产出任何输出文件。`expected.error` 只写码,不含引用链
(理由见 `neg-cycle-self`)。

## 与 v0 的差异

同 `neg-cycle-self`:v0 无外部引用,不可能有循环。
