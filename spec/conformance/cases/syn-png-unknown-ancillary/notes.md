# syn-png-unknown-ancillary

- 尺寸:16x16
- PNG:色彩类型 6(RGBA)+ 一个未知 ancillary chunk `prVt`(内容不参与像素)、8 bit、非交错、行过滤器恒 0
- 非透明色数(n):2

## 覆盖条款

`§8.5`(未知 ancillary chunk:首字母小写 → 忽略内容、重复出现也接受)、`§8.5`(chunk 类型名必须恰为 4 个 ASCII 字母且第三字节保留位为 0)、`§8.1`(ancillary chunk 接受并忽略)。

## 期望值来源

左 8 列 `123456`、右 8 列 `654321`,均 a = 255 → n = 2;每行两段、段长 8 → RUNNER 行,整图 1 个块 → 2 条 `rect`(h = 16)。`prVt` 的合法性:首字母 `p` 小写 = ancillary、第三字节 `V` 大写 = 保留位 0、四字节全是 ASCII 字母。该 chunk 不参与像素,抽取结果必须与不带它时完全相同。


`expected.pixels.sha256` 按 §13.4 计算:逐行、行内自左向右、自上而下,每像素 R,G,B,A 四字节,无填充,共 1024 字节;由本文件持有的**意图像素**直接算出,不经过任何 PNG 解码。
