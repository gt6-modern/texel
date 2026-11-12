# v1/cases/bindings-integer-slot

**pending:依赖 v1 oracle 的阶段 4 实现(参数声明 + 参引替换 + bindings);在它落地前本条没有可判定的期望像素。**

- 形态:`bindings` 正例(PXL.md §13.3 / §13.3.3)
- 受测输入:`entry.pxl` + `bindings.json`
- 期望像素:**暂缺**,索引 `expected` 列写 `-`

## 覆盖条款

- §6.2 替换位置白名单:`integer` 可替换已知 op 的 `x`/`y`/`x0`/`y0`/`x1`/`y1`/`w`/`h`/`slot`
  —— 本用例替换的是 `fill` 的 `slot`;
- §6.1 带 `default` 的参数是**可选**参数:本用例给了 `default: 1`,两个 target 覆写它;
- §6.4 `integer` 的身份是**数学整数值**,不是原始字符串(所以 `1` 与 `1.0` 同身份);
- §6.5 替换完成后由**目标字段**按原规则继续校验:替换出的 `slot` 仍受 palette 长度约束
  (越过就是 `PXL005`,见 `neg-priority-005-after-param-slot`);
- §13.3.3 `bindings.json` 形状与 `expected.<name>.pixels.sha256` 命名。

## 两个 target 钉什么

`red`(slot 1)与 `green`(slot 2)只差一个整数参数,像素必须整体不同 —— 于是“整数参数真的
替换进了 op 字段”成为可判定事实。若实现把 `"$tint_slot"` 原样当字符串或忽略绑定,两个
target 会渲染成同一张图 → 本条失败。

## 为什么现在没有期望像素文件

同 `bindings-color-palette`:渲染需要阶段 P(阶段 4),v1 oracle 尚未实现。本目录按 pending
约定处置(索引 `expected` 写 `-`、`notes` 以 `pending:` 开头、目录内无 `expected.*`),
并在 `v1/README.md` §6.3 的 pending 清单里登记。阶段 4 落地后必须补齐两份摘要并删掉标记。

## 落地后的判定

对每个 target:尺寸 4×4;摘要相等;两跑字节相同。

## 与 v0 的差异

v0 的 op 字段只能是字面量;v1 允许参引出现在 §6.2 白名单内的字段上,替换后语义完全不变。
