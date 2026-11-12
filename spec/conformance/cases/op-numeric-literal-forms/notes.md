# op-numeric-literal-forms

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`6fe956938716b428b67340b343b47866167bcdc17d93cb95ca3f7af94e075e39`

## 覆盖条款

`§3.8`(`width` / `height` / `x` / `y` / `w` / `h` / `slot` 写成等值的小数字面量必须被接受)、`§3.8`(整数值判定必须按原始词法精确做,不得先转二进制浮点)。

## 期望值来源

全部整数成员都写成 `<n>.0` 形式:`width`/`height` 是 `2.0`,`rect` 的 `x`/`y`/`w`/`h` 与 `slot` 也都是 `n.0`。按 §3.8 它们分别等于 2 与 1 等整数值,渲染器必须接受:上半天是 slot 1(`FF0000FF`)、下半天是 slot 2(`00FF00FF`)。与 `negative/neg-width-not-integer-precise` 成对:那一例的 `1.0000000000000001` 按精确十进制**不等于** 1,必须是 `PXL002`。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-numeric-literal-forms
```
