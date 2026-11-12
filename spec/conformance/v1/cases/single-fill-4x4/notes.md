# v1/cases/single-fill-4x4

- 形态:**`single` 正例**(PRD §18.2 / PXL.md §13.3):单入口、无外部依赖
- 受测输入:`entry.pxl`
- 期望像素:`expected.pixels.sha256`(字节流定义沿用 v0 §13.4)
- 目录内**没有** `assets/`、`bindings.json`、`expected.dependencies.tsv`、`expected.error`

## 覆盖条款

- PXL.md §3.2:v1 顶层成员集合(`format`/`version`/`encoding`/`width`/`height`/`palette`/`layers`)
  且 `version` 恒为 1;
- PXL.md §3.3:图层、token、op 语义一律沿用 v0——本条用 v0 §3.3(图层成员 `name`/`blend`/`ops`,
  `blend` **必需**)与 v0 §5.1(`fill` 的 `slot`);
- v0 §4.2:`char` 档 `0` → slot 1;
- 附带钉住:v1 的 `palette` 是**可选**成员(PXL.md §3.2),但本条**显式写了**它,让同一份文档
  在 v0 与 v1 的顶层成员集合下都合法,从而孪生关系(见下)成立得最直接。

## 期望值怎么算出来的(这是本条唯一需要解释的地方)

1. 本条文档只使用 **v0 与 v1 共有**的定义:**不含** `assets`、`params`,也**不含**任何参引
   (`$name`),不含 v1 专有的 `stamp` 的 `asset` 来源。
2. PXL.md §1 与 §3.3 规定:v1 在未重新定义的条款上**原样沿用** v0。因此对这类文档,
   「`version: 1` 的本文档」与「把 `version` 改成 0 的同一份文档」渲染结果**逐像素相同**;
   `expected.dependencies.tsv` 也因此只有一条 `entry` 行(无依赖),本用例不落该文件。
3. 于是**期望值用现有 v0 reference oracle 算出**:
   `spec/conformance/tools/pxl_render_ref.py` 对同一份 current-version 文档
   其余逐字节相同)渲染,得到尺寸 `4x4` 与
   `f7251f38b9099cb06322f823f7fd9ff7d0cee136bbf137ecdb3ac9b4377284f0`。
   复现命令(孪生文档不另入库,按上面一句话就地改):

   ```
   <py> spec/conformance/tools/pxl_render_ref.py <孪生文档>
   # single-fill-4x4: 4x4  sha256=f7251f38b9099cb06322f823f7fd9ff7d0cee136bbf137ecdb3ac9b4377284f0
   ```

4. **独立的第二条路径复核**(不经过 oracle):文档是 4×4、单图层、单条 `fill slot 1`,
   `palette[0] = B87333FF`,故像素流恒为 64 字节 = `B87333FF` × 16(§13.4 的字节流定义:
   逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充)。对该字节流直接求 SHA-256,
   结果与第 3 步相同,两条路径一致。

> 说明:本条**没有**取“全透明 4×4 + `fill slot 0`”那种最省事的形式。全透明文档与「`ops: []`」
> 的空图层像素完全相同,摘要无法区分“`fill` 真的被执行了”和“`fill` 被忽略”,用例就失去判定力。

## 判定方式

按 `v1/README.md` §2.1:`single` 正例的实现必须

1. 接受 `entry.pxl`(`version: 1`,档案 `pxl-v1 / {0,1}`);
2. 输出 PNG 的 `width`/`height` == `4`/`4`(摘要不含尺寸,必须单独断言);
3. 输出像素流的 SHA-256 == `expected.pixels.sha256`;
4. 同实现两跑 PNG 文件逐字节相同。

## 待规范确认

1. `single` 正例**是否也要有 `expected.dependencies.tsv`**:PXL.md §9.4 要求 compiler 返回依赖清单,
   且“第一行必须是唯一入口”,那么本用例的清单应当是恰好一行 `entry<TAB>pxl<TAB>...<TAB>sha256`。
   但 PRD §18.2 把它列为“用例目录**可以**包含”,v1 §13.3 也只说“可含”。本阶段按“可选、正例暂不落”处置,
   以免在清单表头(是否带表头行)未定之前先把它钉成事实。→ 见 §「待规范确认」第 3 条。
