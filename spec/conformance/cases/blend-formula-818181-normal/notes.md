# blend-formula-818181-normal

- 形态:**仅渲染正例**(`op-` / `blend-` 前缀,§13.1 / README §2.1b)
- 输入:`input.pxl`(手写;**没有** `input.png`,也没有「抽取零 diff」判据)
- 尺寸:1x1
- `expected.pixels.sha256`:`e4edc5ab2e50f634c09f5a7e37036e2952dd27279ee068e3cb4a24d326ea999f`

## 覆盖条款

`§6.6(g)` 的补充例(钳位识别)、`§6.3`(必须用未取整的 `aNum` 归一化)。

## 期望值来源

1×1,下层 `81818180`、上层 `81818101`(`normal`)。逐步算式:

```
sa=1  t=254  da=128
aNum = 1*255 + 128*254 = 32767
ao   = (32767 + 127) / 255 = 128
half = 16383
每通道: (0x81*1*255 + 0x81*128*254 + 16383) / 32767
      = (32913 + 4193280 + 16383) / 32767 = 4242576 / 32767 = 129 (0x81)
```

正确结果 `81818180`。**旧公式**(先取整 `ao = 128`,再用 `den = ao*255 = 32640` 做分母)得到 `82828280`(通道 130)——它没有越界,只是错。所以本条钉的是「即使不越界也必须用 `aNum` 归一」,与 `blend-bound-overflow` 的「旧公式会产出域外值 256」互补。

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
<py> spec/conformance/tools/gen_conformance_cases.py blend-formula-818181-normal
```
