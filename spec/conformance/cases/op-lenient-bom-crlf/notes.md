# op-lenient-bom-crlf

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`6fe956938716b428b67340b343b47866167bcdc17d93cb95ca3f7af94e075e39`

## 覆盖条款

`§3.8`(渲染器必须接受带 UTF-8 BOM 的文本)、`§3.8`(任意换行风格,含 CRLF)、`§3.8`(有无结尾换行均可)、`§9`(确定性)。

## 期望值来源

这份 `input.pxl` **故意**不是规范形式:它以 UTF-8 BOM(`EF BB BF`)开头、所有换行都是 CRLF、结尾没有换行符。像素意图与 `op-numeric-literal-forms` 同构(2×2,上半 slot 1、下半 slot 2),因此摘要可与那一例互相参照。只接受「无 BOM + LF + 有结尾换行」的渲染器必须在本用例失败(§3.8)。注意本文件由 `.gitattributes` 的 `-text -eol` 例外保护,否则 checkout 时 CRLF 会被翻成 LF、BOM 会被剥掉。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-lenient-bom-crlf
```
