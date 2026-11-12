# item-material-family-copper-stick-overlay

- 形态:**往返正例**(§13.1 / README §2.1a)
- 来源:`item/material_family/copper/stick_overlay.png`(逐字节副本;见 `samples/MANIFEST.tsv`)
- 所属用例:`real-item-material-family-400`(批量形态,§13.7)
- `manifest.tsv` 的 `input` 列:`sample:item/material_family/copper/stick_overlay.png`
- 尺寸:16x16
- 非透明色数(n):1 → `encoding` = `char`(§4.5 由 `palette` 长度派生)
- `expected.pixels.sha256`:`4ab894760b4d2518a56d7f03e98f1c91ddb01b915a22ef9e09ab3071e1d5cb79`

## 覆盖条款

`§13.7`(批数组):`item/material_family` 前 400 个文件——吞吐与去重统计的真实素材面;16×16、档位从 `char` 到 `index4` 都有。`§8.1`、`§4.5`、`§13.2a` 六条。

## 期望值来源

两条独立路径,任一错都会被交叉发现:

- `expected.pxl`:由**现有 Python 抽取器**(`importer.extract.extract_png`)按附录 A
  对 `input.png` 产出,**未手调**任何空白或缩进;
- `expected.pixels.sha256`:由 `spec/conformance/tools/png_ref.py`(fixture 侧的独立
  解码器,与 `importer` 零共享代码)解码 `input.png` 得到 RGBA8 流,按 §13.4 计算
  (逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充,共 1024 字节)。

## 判据

§13.2a 六条同时成立,其中容易漏掉的两条:

- **尺寸一致**:回渲 PNG 的宽高必须等于 `expected.pxl` 声明值、也等于 `input.png` 的尺寸
  (摘要只覆盖像素字节,不含尺寸);
- **输出结构合规**:8 bit / 色彩类型 6 / 非交错 / 所有行过滤器字节恒 0 /
  chunk 类型序列 `IHDR` → `IDAT`⁺ → `IEND`(§10)。
