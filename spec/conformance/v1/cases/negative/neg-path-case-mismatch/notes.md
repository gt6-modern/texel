# v1/cases/negative/neg-path-case-mismatch

- 形态:`negative` 负例(PXL.md §13.3);`defect: path`
- **受测侧**:内存 resolver 的 `CASE_MISMATCH` 结果(阶段 R2 → §8.2 映射为 `PXL008`)
- 受测输入:`entry.pxl` + `spelling.tsv`
- 期望错误码:`PXL008`(见 `expected.error`,一行 + LF)
- 缺陷数量:**1**

## 覆盖条款

- §8.2 resolver 合同四种结果 `OK`/`MISSING`/`CASE_MISMATCH`/`ESCAPE`:
  **`CASE_MISMATCH` 与 `ESCAPE` 都映射 `PXL008`,`MISSING` 才是 `PXL009`**;
- §8.3 内存 resolver:完全一致 → `OK`;仅大小写折叠后相等 → `CASE_MISMATCH`;否则 `MISSING`;
- §13.3.2 **拼写声明载体** `spelling.tsv`:两列 `logical<TAB>actual`,`actual` 必须是同目录下
  真实存在的文件;存在该文件时 resolver 先按它回答,未列出的逻辑路径按常规处理;
- PRD §13.2:大小写违规必须能在**平台无关**的语料里判定,不依赖宿主文件系统是否大小写敏感。

## 缺陷(只有这一处)

`spelling.tsv` 声明:按逻辑路径 `parts/blade.pxl` 查找时,命中的文件实际拼写是
`parts/Blade.pxl`。二者仅大小写折叠后相等 → `CASE_MISMATCH` → `PXL008`。

入口声明的是 `parts/blade.pxl`(全小写、语法完全合法),磁盘上存在的也正是 `parts/Blade.pxl`。
三条出路被逐一排除:语法错?不是,声明合法;`MISSING`?不是,文件在,只是拼写不同;
`OK`?不是,拼写并不一致。

## 与 `neg-path-uppercase` 的一对关系

两条用**同一个磁盘文件** `parts/Blade.pxl`,却钉不同规则:上一条错在“**声明**里有大写”,
本条错在“**磁盘拼写**与合法声明不一致”。合并成一条(或只留一条)都会让其中一类实现悄悄过关。

## 判定方式

实现必须以 `PXL008` 失败,且不产出任何输出文件。本条的 asset **被 `stamp` 选中** ——
只有真正去解析,resolver 才会回答 `CASE_MISMATCH`。

## 与 v0 的差异

v0 无 resolver,本条是 v1 独有语义。
