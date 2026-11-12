# real-chain-overlay-dedupe

- 形态:**批量用例**(§13.7):一个用例多个输入,成员放在 `members/` 下
- 成员清单:`manifest.tsv`(制表符分隔,表头 `member` / `input` / `encoding` / `colors`)
- `input` 列恒为 `sample:<samples/ 下的相对路径>`,且该路径已在 `samples/MANIFEST.tsv` 登记

## aggregate

**去重组**(§13.7):

1. `members/copper-chain-overlay/expected.pxl` 与 `members/wood-chain-overlay/expected.pxl` 必须**逐字节相同**(不是「看起来一样」);
2. 两份 `expected.pixels.sha256` 也必须逐字节相同;
3. `manifest.tsv` 行数 = 成员目录数 = **2**。

两个成员的上游文件字节相同(SHA-256 `9a8136434004ec3a3d54f0474bac6b85ea86cc7ee869976cfd0c18b2a753019b`),
因此 1、2 两条不成立时,要么抽取器依赖了路径,要么它依赖了没写进规范的东西。
