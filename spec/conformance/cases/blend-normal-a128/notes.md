# blend-normal-a128

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`5387e9ddc00eb46c86c79009de0a5f391e1dbb2fc5e67c382435b97089c13dcb`

## 覆盖条款

`§6.6(a)`(`normal` 源 `FF000080` over `0000FFFF` 必须得 `80007FFF`)、`§6.3`(用**未取整**的 `aNum` 当分母)、`§6.7` 第 1 条(单图层 normal 恒等性由下层 `0000FFFF` 的自渲染保证)。

## 期望值来源

2×2 全同像素。下层 `fill slot 1` = `0000FFFF`(不透明蓝,单层 normal 下恒等);上层 `blend: normal`、`fill slot 2` = `FF000080`。逐步算式(§6.6a):

```
sa=128  t=127  da=255
aNum = 128*255 + 255*127 = 65025
ao   = (65025 + 127) / 255 = 255
half = 65025 / 2 = 32512
r: (255*128*255 +   0*255*127 + 32512) / 65025 = 8355712 / 65025 = 128
g: (  0*128*255 +   0*255*127 + 32512) / 65025 =   32512 / 65025 =   0
b: (  0*128*255 + 255*255*127 + 32512) / 65025 = 8290687 / 65025 = 127
```

结果 `80007FFF`,四个像素相同。注意 r + b = 255 不是「各半取整」,而是上面这条唯一确定的整数结果。

## 判据

§13.2b 的五条同时成立:

1. **回渲像素一致**:渲染器读 `input.pxl`,产出的 RGBA8 字节流 SHA-256 等于
   `expected.pixels.sha256`;
2. **确定性**:同一文档两跑,PNG **文件**逐字节相同(§9);
3. **不拘形式**:该文档本就不是规范抽取器能产出的形态(含 `pixel` / `line` /
   `mirror` / `erase` / `stamp` 之一,或含非 `normal` 的 `blend`),渲染器必须照样接受(§3.8);
4. **尺寸一致**:输出 PNG 的宽高等于 `input.pxl` 声明的值(摘要不含尺寸,必须单独断言);
5. **输出结构合规**:8 bit / 色彩类型 6 / 非交错 / 所有行过滤器字节恒 0 /
   chunk 类型序列 `IHDR` → `IDAT`⁺ → `IEND`(§10)。

## 摘要怎么来的

`expected.pixels.sha256` 由 `spec/conformance/tools/pxl_render_ref.py` —— **按规范另写**的
渲染 oracle(不 import `importer`、不 import `tests/`)—— 对 `input.pxl` 渲染后
按 §13.4 的字节流定义(逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充)
计算,并经被测实现之外的第二条路径复核。生成命令:

```
<py> spec/conformance/tools/gen_conformance_cases.py blend-normal-a128
```
