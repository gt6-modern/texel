# blend-bound-overflow

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`1d89d17ddf37084f28b17459d47ac655f2f82929c7c537b5210e48fefb659aee`

## 覆盖条款

`§6.6(g)`(源 `FFFFFF01` over `FFFFFF80` 必须得 `FFFFFF80`;先取整 `ao` 的旧写法会算出通道 **256**)、`§6.3`(通道上界证明依赖未取整的 `aNum`)、`§6.4`(`multiply` 的 `mc` 代入同一条证明)。

## 期望值来源

1×1,三层:底层 `FFFFFF80`;`over-normal` 层 `FFFFFF01`;`over-multiply` 层 `FFFFFF01`(源色全 255,`mc = div255(255*255) = 255`,于是退化为与 `normal` 相同的算术)。逐步算式(§6.6g):

```
sa=1  t=254  da=128
aNum = 1*255 + 128*254 = 32767
ao   = (32767 + 127) / 255 = 128
half = 16383
r = (255*1*255 + 255*128*254 + 16383) / 32767 = 8371968 / 32767 = 255
```

结果 `FFFFFF80`。**注意**:这一例单独看不出钳位——旧公式把 256 钳到 255 恰好等于正确值;真正的钳位识别由 `blend-formula-818181-normal` / `-multiply` 承担(那一对即使不越界也与正确值不同)。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-bound-overflow
```
