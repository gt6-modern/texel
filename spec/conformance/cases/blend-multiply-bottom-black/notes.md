# blend-multiply-bottom-black

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`e3820096cb82366b860b8a4e668453a7aaaf423af03bdf289fa308ea03a79332`

## 覆盖条款

`§6.7` 第 4 条(`multiply` 不是「覆盖」:下层为空时任何不透明源都乘成黑)、`§6.4`。

## 期望值来源

1×1。第一层就是 `blend: multiply` 的底层(下层是空累加器,`dc = 0`):

```
mc = div255(255*0) = 0
aNum = 255*255 + 0*0 = 65025   ao = 255
r = (0*255*255 + 0*0*255 + 32512) / 65025 = 0
```

得到 `000000FF`。第二层 `blend: mask` 源 `000000FF`:`out = (dr,dg,db, div255(255*255)) = (0,0,0,255)`,不改变上一步结果。若实现给 `multiply` 加了「全不透明即覆盖」的特例,第一层会得到 `FF0000FF`,摘要必然不同——这正是本条要挡的。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-multiply-bottom-black
```
