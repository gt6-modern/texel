# blend-mask-alters-alpha

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`c65d66eac47193d821fcddf87cecfb5141230d64257f3bfced417fb40ea2f13c`

## 覆盖条款

`§6.7` 第 6 条(`mask` 必须改下层 alpha:`11223300` mask 到 `445566FF` 得 `44556600`)、`§6.5`(`mask` 只乘 alpha、RGB 取下层)、`§7` 第 3 条(`mask` 不得清除下层 RGB)。

## 期望值来源

1×1,两层:底层 `fill slot 1` = `445566FF`;上层 `blend: mask`、`fill slot 2` = `11223300`(RGB 非零、A = 0 的普通像素,不是透明 slot)。算式:

```
out = (dr, dg, db, div255(da * sa)) = (0x44, 0x55, 0x66, div255(255*0) = 0)
```

结果 `44556600`:下层 alpha 被改掉了,RGB 原样保留。若把 `mask` 误实现成 `normal`,结果会是 `11223300`(源作为不可见颜色在下层为空时不适用;此处下层可见,`normal` 的 `aNum == 0` 分支要求保留下层 RGB 与 alpha,得到 `445566FF`),两种错法都与 `44556600` 不同,所以摘要能区分。

§6.7 第 6 条里另半句「`mask` 作底层得 `00000000` 且不保留源 RGB」无法在本例同一个 1×1 摘要里同时钉住(最终值只能有一个);它在本文具的 `pxl_render_ref.py --selftest` 里作为公式级断言独立覆盖(`mask 11223300 over 00000000 → 00000000`)。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-mask-alters-alpha
```
