# syn-1x1-transparent

- 尺寸:1x1
- PNG:色彩类型 6(RGBA)、8 bit、非交错、行过滤器恒 0、无 PLTE/tRNS/额外 chunk
- 非透明色数(n):0

## 覆盖条款

`§4.5`(n = 0 的最小情形)、`§7`(只有四通道全零才是透明 slot)、附录 A 第 2 步(整图同一 slot → 单条 `fill`)。

## 期望值来源

唯一像素 `00000000`:四通道全零 → slot 0。n = 0,留在 char 档;整图只有同一个 slot,故 `ops = [fill slot=0]`。RGBA8 流恰 4 字节。


`expected.pixels.sha256` 按 §13.4 计算:逐行、行内自左向右、自上而下,每像素 R,G,B,A 四字节,无填充,共 4 字节;由本文件持有的**意图像素**直接算出,不经过任何 PNG 解码。
