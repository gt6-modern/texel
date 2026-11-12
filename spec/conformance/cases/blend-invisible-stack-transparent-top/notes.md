# blend-invisible-stack-transparent-top

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`6fc6f4524161c3ae0d316812d7088e3fcd372023edaea2d7821093be40ae1060`

## 覆盖条款

`§6.6(e)`(透明 slot 不吞掉下层的不可见颜色)、`§7` 第 3 条(上层是透明 slot → 保留**下层** RGB)、`§6.3` 的 `aNum == 0` 分支。

## 期望值来源

1×1,下层 `FFFFFF00`,上层是显式 `fill slot 0`(即透明 slot `00000000`)。`aNum = 0 + div255(0*255) = 0`,源是透明 slot,故保留下层 RGB:

```
out = (dr, dg, db, 0) = (0xFF, 0xFF, 0xFF, 0) = FFFFFF00
```

结果 `FFFFFF00`。与 `blend-invisible-stack-upper-wins` 配对,钉住「有颜色者胜」的两个分支。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-invisible-stack-transparent-top
```
