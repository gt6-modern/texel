# v1/cases/negative/neg-params-undeclared

- 形态:`negative` 负例(PXL.md §13.3);`defect: params`
- **受测侧**:阶段 P(§11.2 阶段表第 3 行:参数声明语法、参引语法、bindings 集合与类型)
- 受测输入:`entry.pxl` + `bindings.json`
- 期望错误码:`PXL012`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(绑定了一个未声明的参数)

## 覆盖条款

- §6.3:``bindings 只能绑定**入口**文档声明的参数;未声明 → `PXL012```;
- §11.2 阶段 P 位于**版本分派之后、具体字段值校验之前**:本条在进入 v0 阶段 3 之前就该失败;
- §6.5 第 2 步:“任何一条不满足即 `PXL012`,**本阶段失败就结束**,不继续后面的阶段”。

## 缺陷(只有这一处)

文档声明了 `known_color`(`color`,`default: "FF0000FF"` → 可选参数);
`bindings.json` 的 target `main` 却绑定 `unknown_color`。类型也对不上,但**类型错误不是这里的
判定依据**:集合关系(这个参数根本没声明)先成立,码同为 `PXL012`。这一点刻意写清,以免
实现误以为本条在钉“类型”而漏掉“集合”。

文档其余部分合法:`palette[0] = "$known_color"` 是 §6.2 白名单内的替换位置;`$known_color` 有
`default`,所以**没有**“缺必需参数”的问题(那条由 `neg-params-required-missing` 钉)。若实现
忽略未声明绑定,替换照常完成、渲染成功 → 本条失败。

## bindings 的载体(负例借用 §13.3.3 的形状)

`bindings.json` 用的是与 `bindings` 形态**逐字符同构**的
`{"targets":[{"name":"main","bindings":{…}}]}`,只放一个 target。这样错误码不依赖“哪个 target
先跑”这种实现细节。

## 判定方式

实现必须以 `PXL012` 失败,且不产出任何输出文件。

## 待规范确认

1. §13.3.3 只写了“`bindings` 形态的用例在目录里放 `bindings.json`”。**`negative` 形态的用例
   同样需要一组绑定**才能表达参数类缺陷(本条及 `neg-params-*`),而规范没有明说这种用例是否
   可以复用它。本阶段按同一形状落地(只放一个 target),请规范确认或另定载体。

## 与 v0 的差异

v0 没有参数与 bindings,不存在该错误;v0 的 `PXL01x` 编号止于 `PXL006`。
