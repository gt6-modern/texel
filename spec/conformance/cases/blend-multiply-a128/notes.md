# blend-multiply-a128

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`e8a309856bc637c69f525c9c470be32dc32e2a8cf70e6d1cad7ed4856b445e2e`

## 覆盖条款

`§6.6(b)`(`multiply` 源 `80FF0080` over `112233FF` 必须得 `0D2219FF`)、`§6.4`(先 `mc = div255(sc*dc)`,再用与 `normal` 完全相同的公式)。

## 期望值来源

2×2 全同像素。下层 `112233FF`,上层 `blend: multiply`、`fill slot 2` = `80FF0080`。逐步算式(§6.6b):

```
mr = div255(128*17 = 2176) = 9
mg = div255(255*34 = 8670) = 34
mb = div255(  0*51 =    0) = 0
sa=128  t=127  da=255  aNum=65025  ao=255  half=32512
r: (  9*128*255 + 17*255*127 + 32512) / 65025 =  876817 / 65025 = 13
g: ( 34*128*255 + 34*255*127 + 32512) / 65025 = 2243362 / 65025 = 34
b: (  0*128*255 + 51*255*127 + 32512) / 65025 = 1684147 / 65025 = 25
```

结果 `0D2219FF`。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-multiply-a128
```
