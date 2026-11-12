# op-pixel

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:16x16
- `expected.pixels.sha256`:`42341abd8c29e29b25d332be1308c4f35e5ad6838234c7d7ae30934a229111b7`

## 覆盖条款

`§5.3`(`pixel` 只写一个像素,等价于 `rect(x,y,1,1)`)、`§5.0`(越界一律裁剪、不报错)、`§5.0`(后写的 op 整体替换同格 texel 值)`。

## 期望值来源

7 条 `pixel` 依序执行:第 2 条把 `(0,0)` 从 slot 1 覆盖成 slot 2;末尾 4 条落在画布之外(或负坐标)被丢弃,不产生任何像素。最终 `(0,0)` = slot 2 = `00FF00FF`、`(15,15)` = slot 1 = `FF0000FF`,其余 254 个像素仍是图层的初始透明 `00000000`。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-pixel
```
