# blend-normal-opaque-wins

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`34aaa746c25a0f105c4316bbb1f009aa359f49582656ee97d73c58132d563423`

## 覆盖条款

`§6.7` 第 3 条(`normal` 下 `sa = 255` 时结果恒等于上层源值且 `ao = 255`)、`§6.3`。

## 期望值来源

1×1,下层 `11223380`(alpha 非 255,确保不是「下层本来就被覆盖」的巧合),上层 `FF0000FF`。`sa = 255` → `t = 0`、`aNum = 255*255 = 65025`、`ao = 255`:

```
r = (255*255*255 + 0 + 32512) / 65025 = 16613887 / 65025 = 255
g = (0*...    + 0 + 32512) / 65025 = 0
b = 0
```

结果 `FF0000FF`,与上层源值逐字节相同。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-normal-opaque-wins
```
