# blend-mask-a128

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`a0122d91b9a80114ccac762b78a5d9d10e6b874465ae03fa0613926bd9574fce`

## 覆盖条款

`§6.6(c)`(`mask` 源 `00FF0080` over `112233FF` 得 `11223380`)、`§6.6(d)`(`mask` 源 `00FF0000` over `112233FF` 得 `11223300`,**不得**折叠成 `00000000`)、`§6.5`(`mask` 只乘 alpha,RGB 恒取下层)、`§6.7` 第 5 条。

## 期望值来源

三层:底层 `fill slot 1` = `112233FF`;`mask128` 层只覆盖**上**半两格,源 `00FF0080`;`mask0` 层只覆盖**下**半两格,源 `00FF0000`。逐步算式:

```
上排: out = (0x11, 0x22, 0x33, div255(255*128 = 32640) = 128) = 11223380
下排: out = (0x11, 0x22, 0x33, div255(255*0   =     0) =   0) = 11223300
```

下排的 `11223300` 是「RGB 非零而 A = 0」的**普通像素**(§7 第 2 条),不得被当作透明 slot 丢弃。

**索引与实际的差异(如实记录)**:`CASES.tsv` 该行的 `colors` 列写 2,而本条要同时钉住 §6.6(c) 与 §6.6(d) 两个不同的源 alpha(128 与 0),因此 `palette` 实为 3 项(`112233FF` / `00FF0080` / `00FF0000`)。1×1 之外的任何做法都无法用 2 项同时表达这两个源;索引的 `colors` 列只对有 `input.png` 的往返正例做逐条交叉校验(`tests/test_conformance.py::test_index_columns_match_the_landed_cases`),故此处按实际生成并在 notes 里登记差异,不改索引。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-mask-a128
```
