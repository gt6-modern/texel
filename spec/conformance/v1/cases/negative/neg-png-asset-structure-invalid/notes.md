# v1/cases/negative/neg-png-asset-structure-invalid

- 形态:`negative` 负例(PXL.md §13.3);`defect: png`
- **受测侧**:阶段 R2(解码器,§8.3 引用 v0 §8.5 的接受集)
- 受测输入:`entry.pxl` + `art/tile.png`(33 字节:PNG 签名 + IHDR,其后**没有** IDAT/IEND)
- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**(PNG 结构不完整:缺 IDAT 与 IEND)

## 覆盖条款

- §11.1 的边界句:**有正确的 PNG 签名但结构不符合 §8.3 的 → `PXL006`**;
- §8.3:compiler 对 PNG asset 使用与 Importer 相同的输入语义(v0 §8.5),非法 PNG 报 `PXL006`
  并附带完整引用链;
- v0 §8.5:缺 `IEND`(以及缺 `IDAT`)是结构错;
- §10.2 与阶段 R4 的配合:**像素预算只读 IHDR**(见 §11.2 新增的那条边界),结构合法性仍要解码才判
  —— 本用例尺寸 1×1、预算远未超,所以一定会走到解码。

## 缺陷(只有这一处)

素材 = 签名(8 字节)+ `IHDR` chunk(13 字节载荷 + CRC,共 25 字节)= 33 字节,文件到此为止。
读头部(签名 + IHDR + CRC)全部合法,于是预算照常记账;解码到收尾发现缺 `IEND` →
`PXL006`(Java `缺少 IEND` / Python `缺少 IEND`,同码同因)。

## 为什么不写成“零字节”或“签名错”

那两种都属于**声明类型与实际字节不符** → `PXL009`(见 `neg-png-asset-not-png`)。
本用例要的是**另一侧**:字节确实是 PNG 开头,只是不完整。这个区分是 §11.1 明文划的,
也是两侧实现曾经分叉的地方(R4-03)。

## 判定方式

实现必须以 `PXL006` 失败,且不产出任何输出文件。

## 与 v0 的差异

素材本身是 v0 §8.5 就能拒绝的形态(v0 语料里由 `neg-png-missing-iend` 覆盖**抽取侧**);
v1 这里判的是 **compiler 的 PNG asset 解码路径**,即 §13.5 的双路径门禁在 v1 asset 上的那一半。
