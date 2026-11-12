# blend-invisible-stack-upper-wins

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`81ff65efc4487853bdb4625559e69ab44f19e0f5efbd6d5b2af5e3ab267c8e06`

## 覆盖条款

`§7` 第 3 条(两层都是「RGB 非零而 A = 0」时**取上层**)、`§6.3`(`aNum == 0` 分支:源不是透明 slot → 保留 `sr/sg/sb`)、`§6.6(f)`。

## 期望值来源

1×1,两层都是不可见颜色:下层 `FFFFFF00`、上层 `FF000000`。`sa = 0`、`da = 0` → `aNum = 0`,源不是透明 slot,故走「保留上层 RGB」分支:

```
out = (sr, sg, sb, 0) = (0xFF, 0x00, 0x00, 0) = FF000000
```

结果 `FF000000`,**不是** `FFFFFF00`。这条钉住 §7 的散文与公式不矛盾:「有颜色者胜;两者都有颜色时上层胜」。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-invisible-stack-upper-wins
```
