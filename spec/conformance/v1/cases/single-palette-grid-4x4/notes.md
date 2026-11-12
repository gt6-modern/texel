# v1/cases/single-palette-grid-4x4

- 形态:**`single` 正例**(PRD §18.2 / PXL.md §13.3):单入口、无外部依赖
- 受测输入:`entry.pxl`
- 期望像素:`expected.pixels.sha256`
- 目录内**没有** `assets/`、`bindings.json`、`expected.dependencies.tsv`、`expected.error`

## 覆盖条款

- PXL.md §3.2 / §3.3:`palette` 与八条 op 的语义沿用 v0;
- v0 §4.2:`char` 档 token → slot 的映射(`0` → slot 1 …),以及 `.` = 透明 slot 0;
- v0 §4.4:调色板 slot 按行优先首次出现顺序分配;
- v0 §3.4:token 行数组的形状规则(每行至少 1 token、同数组内各行等长、只含枚举字符);
- v0 §5.5:`grid` 的成员集合恰好是 `op`/`x`/`y`/**`grid`**——**成员名是 `grid`,不是 `rows`**。
  本条按 v0 §5.5 与 §3.7 的成员表书写;v1 §3.3 明写“八条 op 的参数集合与语义全部按 v0 执行”。

## 期望值怎么算出来的

1. 同 `single-fill-4x4`:本文档不含 `assets`、`params` 与参引,故与「`version` 改成 0 的孪生文档」
   逐像素相同(PXL.md §1 / §3.3)。
2. 用 `spec/conformance/tools/pxl_render_ref.py` 对孪生文档渲染,得到 `4x4` 与
   `ebe0c8fd410f3c60632f8f6441cbbf8a36642c6aee569fb9a8b4794eec8e4a1d`。
3. **独立第二条路径复核**(手算 token → slot → texel,不经 oracle):

   | 行 | token 串 | 解出的 slot | texel |
   |---|---|---|---|
   | y=0 | `012.` | 1,2,3,0 | `FF0000FF`,`00FF00FF`,`0000FFFF`,`00000000` |
   | y=1 | `1230` | 2,3,4,1 | `00FF00FF`,`0000FFFF`,`FFFFFFFF`,`FF0000FF` |
   | y=2 | `2301` | 3,4,0,2 | `0000FFFF`,`FFFFFFFF`,`00000000`,`00FF00FF` |
   | y=3 | `3012` | 4,1,2,3 | `FFFFFFFF`,`FF0000FF`,`00FF00FF`, `0000FFFF` |

   按 §13.4 的行序拼成 64 字节像素流后直接求 SHA-256,结果与第 2 步相同。

> **为什么 `palette` 有 4 项**:`char` 档的 `0`/`1`/`2`/`3` 分别指向 slot **1/2/3/4**,
> 不是 0/1/2/3。token `3` 要解到 `palette[3]`,所以 palette 必须是 4 项;若只写 2 项,
> `grid` 的 token `2` 会指向 slot 3 而越过 palette 长度,**按 v0 §11.1 阶段 9 应报 `PXL005`**,
> 这条正例就写错了。本用例的 palette 与 token 一一对应,冗余度为零。

## 判定方式

同 `single-fill-4x4`(见 `v1/README.md` §2.1):尺寸 4×4、像素摘要相等、两跑字节相同。

## 待规范确认

1. 同 `single-fill-4x4` 的待确认项 1(`expected.dependencies.tsv` 是否对 `single` 必需)。
