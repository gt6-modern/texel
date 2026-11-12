# real-fluid-source-255

- 形态:**批量用例**(§13.7):一个用例多个输入,成员放在 `members/` 下
- 成员清单:`manifest.tsv`(制表符分隔,表头 `member` / `input` / `encoding` / `colors`)
- `input` 列恒为 `sample:<samples/ 下的相对路径>`,且该路径已在 `samples/MANIFEST.tsv` 登记

## aggregate

**批数组**(§13.7):

1. `manifest.tsv` 行数 = 成员目录数 = **255** = `## aggregate` 里写死的成员数;
2. 往返正例成员 **245** 个,已知缺陷成员 **10** 个,**245 + 10 = 255**;
3. 每个成员按 §13.2a(正例)或 §13.2c(缺陷成员)各自通过。

缺陷成员的清单由**实测**得到:对 255 个文件逐个跑现有解码器,被 `PXL006` 拒绝的就是它们;清单与实测 CRC 见 `notes.md` 末尾与 `samples/MANIFEST.tsv`。

| 成员 | 存储 CRC | 计算 CRC |
|---|---|---|
| `block-fluid-source-aerotheum` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hotcarbondioxide` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hotheavywater` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hothelium` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hotmoltenlicl` | `3BBA79D6` | `3F090335` |
| `block-fluid-source-hotmoltensodium` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hotmoltentin` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hotsemiheavywater` | `96CEE38A` | `927D9969` |
| `block-fluid-source-hottritiatedwater` | `96CEE38A` | `927D9969` |
| `block-fluid-source-thoriumsalt` | `EEF9409D` | `D7FE4025` |
