# neg-default-string-as-integer

integer 参数的 default 写成字符串 "1"：default 必须是声明类型的 JSON 字面量，判 PXL012。
（发布态复核：Python 曾按文本解析并接受，Java 拒绝 —— 类型混用。）
