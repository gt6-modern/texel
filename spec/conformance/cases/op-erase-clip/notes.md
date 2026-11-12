# op-erase-clip

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:16x16
- `expected.pixels.sha256`:`441539ba40bfefc2c976388b0468bd9e564a66b70fa1b5879b2d1cdcbeb9026c`

## 覆盖条款

`§5.7`(`erase` 把本层矩形清成透明 slot 0,等价于 `rect(..., slot=0)`)、`§5.0`(矩形右开下开、`w`/`h` 为 0 等于空操作、越界一律裁剪不报错)。

## 期望值来源

先 `fill slot 1` 铺满,再挖三个洞、清一条带:第 2 条清 `2..5 × 2..5`;第 3 条把下半 8 行刷成 slot 2;第 4 条负坐标 `x=-4,w=8` 只覆盖 `x 0..3`;第 5 条 `x=12,w=100` 只覆盖 `x 12..15`、`y 14..15`;第 6–9 条分别因 `w=0`、`h=0`、完全越界而是空操作(它们一条都不改像素,正是「不得因越界报错」的部分)。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-erase-clip
```
