# op-lenient-structure

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:2x2
- `expected.pixels.sha256`:`b0edd918f147bc6b4dc10bc07f25fae6cadf97d6d678b04f0ab9ed5ee5959f66`

## 覆盖条款

`§3.8`(任意缩进、任意行尾空白、任意成员顺序、冒号后多余空格全部必须接受)、`§3.8`(可选布尔成员写成 `false` 与整体省略等价)、`§3.8`(`2.0` 这类等值字面量表示整数)、`§3.7`(`flipX: false` 不是规范形式但仍然合法)。

## 期望值来源

这份 `input.pxl` 把 §3.8 的接受集逐条用上:缩进用了 4 空格、2 个 tab、空格与 tab 混用三种;至少两行带**行尾空白**(第 `},   ` 行是空格,第 `},\t` 行是 tab);冒号后有的是多个空格(`"y":   2.0` 那类);顶层与图层内的成员顺序都被打乱;`width` / `height` 写成 `2.0`;`mirror` 的 `flipX` / `flipY` 显式写 `false`。像素意图:`fill slot 1` 铺满 2×2,`pixel(0,0)` 改 slot 2,再把 `(0,0)` 镜到 `(1,1)`(flipX/flipY 皆 false,纯拷贝),最终 `[[2,1],[1,2]]`。只接受「4 空格缩进、拒绝 tab 与行尾空白」的解析器必须失败。

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
<py> spec/conformance/tools/gen_conformance_cases.py op-lenient-structure
```
