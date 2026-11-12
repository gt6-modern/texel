# op-coord-extreme

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:16x16
- `expected.pixels.sha256`:`806f936f2baf6eff50265f62b69e35f309608f32cd02d846c5cbd0f6ddcea9c6`

## 覆盖条款

`§5.0.1`(坐标取值域 `-65536..65536`、`w`/`h` 取值域 `0..65536`)、`§5.0.1`(终止性:步进轮数不超过 `|dx| + |dy| + 1 <= 262145`)、`§5.0`(逐点裁剪,不得先做解析裁剪)。

## 期望值来源

第 1 条 `x=-65536,w=65536` 的右边界恰为 0,覆盖集为空(不是「夹到边界」);第 2 条同理;第 3 条 `(0,0,65536,65536)` 裁剪后等于整张 16×16 铺满 slot 1。第 4、5 条是从极端端点出发的两条线:按 §5.4 的逐点算法,每轮步进 1,`|dx|+|dy|+1 = 262145` 轮后必然终止,其中落在画布内的点被写入、其余丢弃;由于 `dx == |dy|`,两条线在画布内都是对角步进。第 6、7 条落在四角之外,是空操作。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-coord-extreme
```
