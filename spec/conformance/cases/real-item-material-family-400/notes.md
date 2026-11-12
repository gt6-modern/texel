# real-item-material-family-400

- 形态:**批量用例**(§13.7):一个用例多个输入,成员放在 `members/` 下
- 成员清单:`manifest.tsv`(制表符分隔,表头 `member` / `input` / `encoding` / `colors`)
- `input` 列恒为 `sample:<samples/ 下的相对路径>`,且该路径已在 `samples/MANIFEST.tsv` 登记

## aggregate

**批数组**(§13.7):

1. `manifest.tsv` 行数 = 成员目录数 = **400** = `## aggregate` 里写死的成员数;
2. 每个成员按 §13.2a 六条各自通过;
3. 成员集合是 `item/material_family/**/*.png` 按**相对路径 UTF-8 字节序**排序后的前 400 个——排序方式写死,否则「取前 400 个」在两台机器上不是同一批文件。
