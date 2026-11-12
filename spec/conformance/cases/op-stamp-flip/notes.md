# op-stamp-flip

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:16x16
- `expected.pixels.sha256`:`7749a24f064d13e8090a2f021844a4dc5811d65cb1f3d65d41391388a1942acd`

## 覆盖条款

`§5.8`(`stamp` 把命名 sprite 写到 `(x,y)`,先 X 后 Y 翻转,两者同真等于 180 度旋转)、`§3.7`(`sprites` 成员按名字的 ASCII 字节序排列)、`§5.8`(定义但从未被引用的 sprite 对输出没有影响)、`§5.0`(逐点裁剪)。

## 期望值来源

`sprites` 有两条:`lshape`(3×3,含 slot 1 与 slot 2,左右上下都不对称,所以三种翻转互不相同)与 `unused`(从未被 `stamp` 引用,按 §5.8 必须对输出零影响——它若被误画进来,摘要必然不同)。四个 `stamp` 分别是原样、flipX、flipY、flipX+flipY;后两条放在 x=-1 与 x=14 处,验证逐点裁剪不报错。按 §3.7,`sprites` 的成员顺序是 `lshape`、`unused`(ASCII 字节序)。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-stamp-flip
```
