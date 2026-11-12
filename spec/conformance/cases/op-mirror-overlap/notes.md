# op-mirror-overlap

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:16x16
- `expected.pixels.sha256`:`b11448ccd2ef265c0024a06cd50fb29931881a4ec1919d9dd15540e4be152781`

## 覆盖条款

`§5.6`(快照语义:源区必须在任何写入发生前整体读出)、`§5.6`(`flipX` 水平、`flipY` 垂直,两者同真等于 180 度旋转)、`§5.6`(**读取侧不裁剪**,越界源像素按透明 slot 0 参与快照)、`§5.0`(写入侧裁剪)。

## 期望值来源

第 1–2 条在 `0..7 × 0..3` 画出一块上下、左右都不对称的内容(`rect(0,1,4,1)` 的 slot 2 横条)。第 3 条 `mirror from (0,0,8,4) to (2,0)`:目标区 `2..9 × 0..3` 与源区重叠,若实现按「边读边写」就会读到刚写下的内容;按 §5.6 必须先整体快照,结果唯一。第 4–6 条分别是 flipX、flipY、两者同真,各自的源都是**当时的**图层内容(前一条 mirror 的输出也算在内,因为快照只在本条 op 内成立)。第 8 条 `from.x = -4`:左半 4 列落在图层之外,按透明 slot 0 参与,于是目标 `8..11` 列被写成 `00000000`,而 `12..15` 列拿到源 `0..3` 列(slot 1)——这条钉的正是「越界源全零」,不是「越界源跳过」。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-mirror-overlap
```
