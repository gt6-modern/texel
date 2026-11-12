# v1/cases/bindings-color-palette

**pending:依赖 v1 oracle 的阶段 4 实现(参数声明 + 参引替换 + bindings);在它落地前本条没有可判定的期望像素。**

- 形态:`bindings` 正例(PXL.md §13.3 / §13.3.3):**一个入口、多组 bindings、多份期望像素**
- 受测输入:`entry.pxl` + `bindings.json`
- 期望像素:**暂缺**,索引 `expected` 列写 `-`

## 覆盖条款

- §6.1 `params` 声明:成员集合恰好 `type`/`default`;本用例声明一个**必需**的 `color` 参数
  (无 `default`,所以不提供绑定就是 `PXL012`,那条由 `neg-params-required-missing` 钉住);
- §6.2 替换位置白名单:`color` **只能**替换到 `palette` 数组的元素上 —— 本用例正是写在
  `palette[0] = "$base_color"`;
- §6.4 `color` 的规范形式是 `^[0-9A-F]{8}$`(八位大写,小写非法);
- §6.3 bindings 只能绑定**入口**声明的参数;通过校验后转为按参数名 ASCII 升序的规范映射;
- §13.3.3 `bindings.json` 的形状:`{"targets":[{"name":…,"bindings":{…}}]}`,`name` 匹配
  `[a-z][a-z0-9_-]*` 且文件内唯一;每个 target 的期望像素写 `expected.<name>.pixels.sha256`。

## 两个 target 钉什么

`copper` 与 `iron` 用**同一份模板**、只改 `base_color`:于是“同一个入口 + 不同 bindings →
不同像素”这件事成为可判定的事实,而同一条用例里两份期望像素互不相同。若实现把 bindings 忽略、
或把规范 bindings 混进了渲染节点身份以外的语义,两份摘要会相等 → 本条失败。

## 为什么现在没有 expected.`<name>`.pixels.sha256

§13.3.3 规定“缺少任一 target 的期望文件即用例自身非法”。本条**当前**正是缺的:
渲染它需要阶段 P 的参引替换与 bindings 校验(阶段 4),而 v1 oracle 骨架对 version 1 抛
`NotImplementedError`。为了不让它冒充“能过但没判据的绿”,本目录按约定:

- 索引 `expected` 列写 `-`;
- 索引 `notes` 列与本文件首行都以 `pending:` 开头;
- 目录里**没有**任何 `expected.*` 文件。

这是**已知的暂时状态**,登记在 `v1/README.md` §6.3 的 pending 清单里。阶段 4 落地时必须
用 v1 oracle 产出两份摘要并删掉 pending 标记 —— 在那之前本条**不得**被报告为通过。

## 落地后的判定

对每个 target 分别:尺寸 4×4;像素流 SHA-256 == `expected.<name>.pixels.sha256`;
两跑 PNG 字节相同。**不得**用“把参数手工替换后再交给 v0 oracle”凑摘要:那等于在语料里手写
一份 v1 的参引语义(§13.4 禁止的复制漂移)。

## 与 v0 的差异

v0 没有参数、没有 bindings:同一份文档只对应一份像素。v1 用 bindings 把“一个入口”变成多个
target,而文档语义(图层/blend/token)仍然完全沿用 v0。
