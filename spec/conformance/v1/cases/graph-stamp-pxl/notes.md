# v1/cases/graph-stamp-pxl

**pending:依赖 v1 oracle 的阶段 2 实现(PXL 引用 + 依赖图);在它落地前本条没有可判定的期望像素。**

- 形态:`graph` 正例(PXL.md §13.3 / §13.3.1)
- 受测输入:`entry.pxl`;被引用素材:`parts/blade.pxl`
- 期望像素:**暂缺**(见上),索引 `expected` 列写 `-`

## 覆盖条款

- **§13.3.1 用例目录即 asset root**:入口逻辑路径恒为 `entry.pxl`;被引用素材按**其逻辑路径**
  原样放在用例目录里 —— 逻辑路径 `parts/blade.pxl` 对应 `<case>/parts/blade.pxl`。
  本目录**没有** `assets/` 这一层(PRD §18.2 清单里那一项,§13.3.1 明确不采用);
- §5.1 `assets` 声明(成员集合恰好 `kind`/`path`);
- §5.3 `stamp` 的 `asset` 来源(`sprite` 与 `asset` 恰一出现,本用例只给 `asset`);
- §7 逻辑路径相对**声明该引用的文档所在逻辑目录**解析:`entry.pxl` 在 root,所以写
  `parts/blade.pxl`;若写成 `blade.pxl`,实现就会去 root 找而 `PXL009`;
- §8.3 内存 resolver:默认按磁盘拼写命中;
- §9.4 依赖清单:本 target 应产出两行(入口 + 一个 dependency)。

## 期望值怎么算(为何现在算不出)

渲染它需要:读并递归校验被引用文档 → 依赖图在光栅化前固定 → asset stamp 执行
(阶段 R2 + §5.3)。这些都属 `v1/tools/compiler_render_ref.py` 的**阶段 2/3**,当前骨架对 version 1
抛 `NotImplementedError`,所以本条**没有** `expected.pixels.sha256`,索引 `expected` 写 `-`。

**不要**用“把素材手动内联进 entry 再交给 v0 oracle”凑一个摘要:那等于在语料里手写一份 v1 的
解析与依赖语义,正是 PXL.md §13.4 禁止的“复制后独立漂移”。落地后期望值必须由 v1 oracle
自己的 resolver + 依赖图 + stamp 路径算出。

## 落地后要判的四条

1. 尺寸等于声明的 4×4(摘要不含尺寸,必须单独断言);
2. 像素流 SHA-256 等于届时写下的 `expected.pixels.sha256`;
3. `expected.dependencies.tsv` 逐字节相同(两行,第一行 `entry`,其余按 `logical_path` 的 ASCII
   升序;**不带表头行**,§13.3.4);
4. 同实现两跑 PNG 字节相同(§12)。

## 为什么素材是 2×2

入口画布 4×4、stamp 到 `(0,0)`:裁剪后只写 4 个像素,其余保持透明 —— 这样“stamp 只写它自己的
矩形”成为可判定事实。素材若也做成 4×4,就分不清实现有没有画满整层。

## 与 v0 的差异

v0 的 `stamp` 只认文档内 `sprites`;本条用外部 `assets`。除来源之外,图层/blend/token/裁剪
全部沿用 v0 §5.8(§3.3 / §5.3)。
