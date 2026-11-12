# v1/cases/negative/neg-params-type-mismatch

- 形态:`negative` 负例(PXL.md §13.3);`defect: params`
- **受测侧**:阶段 P(bindings 的类型)
- 受测输入:`entry.pxl` + `bindings.json`
- 期望错误码:`PXL012`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(类型不匹配)

## 覆盖条款

- §6.3:绑定值时,类型必须与该参数的 `type` 一致;**字符串 `"1"` 不能绑给 `integer`**;
- §6.4 `integer` 的规范形式是“十进制整数,无前导零、无 `+`、无 `.`、无指数”,身份是数学整数值;
- §11.2 阶段 P;§6.5 第 2 步:本阶段失败即结束。

## 缺陷(只有这一处)

参数 `tint_slot` 是 `integer`(带 `default: 1`,所以是**可选**参数、不涉及“必需缺失”);
`bindings.json` 绑的是**字符串** `"1"`。

§6.3 把这条单独写死是有原因的:很多实现会顺手做一次“看起来无害”的字符串转数字,于是
`"1"` 与 `1` 都通过 —— 而规范要求强类型替换,不做隐式转换。本用例就是给这种“便利”设的闸。

注意别把它与 `neg-params-undeclared`(未声明)和 `neg-params-required-missing`(缺失)混起来:
三条都报 `PXL012`,但分别打“名字不存在”“必需项没给”“给了但类型错”三种绑定缺陷。

## 判定方式

实现必须以 `PXL012` 失败,且不产出任何输出文件。

## 待规范确认

1. 同 `neg-params-undeclared` 的待确认项 1(`negative` 用例借用 `bindings.json` 是否合规)。

## 与 v0 的差异

v0 没有绑定值,不存在“类型”这一维;v0 的类型错只出现在文档字段本身(`PXL001`/`PXL002`)。

> 修正（阶段 4）：原 bindings 写的是 `1`，那对 integer 参数是**合法**绑定，用例本身失效；改为 `not-an-integer` —— 这才是类型不符（规范 §6.3：绑定值按声明类型解析）。
