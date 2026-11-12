# negative/neg-doc-too-large

- 形态:**负例**(§13.1),并且是**生成型用例**(§13.6:输入不入库)
- **受测侧**:渲染侧(输入是一份 PXL 文档;本目录的 `input.pxl` 由生成器产出,不入库)
- 期望错误码:`PXL002`(见 `expected.error`,一行 + LF)
- 缺陷数量:1

## 覆盖条款

`§5.0.2`(文档 UTF-8 字节数上界 **67,108,864 = 64 MiB**)、`§11.1` 阶段 6(文档级
数值域检查)、`§11`(`PXL002` 的触发条件里明写「文档字节数」)、`§13.6`(生成型用例)。

## 缺陷(只有这一处)

文档的 UTF-8 字节数超过 64 MiB,其余一切合规:

- `width` = 1、`height` = 1(面积 1,远低于 2^22);
- 图层数 1(上限 256),每层 `ops` 为 `[]`(上限 32,768);
- `sprites` 项数 **4096**(上限恰为 4096,**不超**);
- 每个 sprite 的行数 **1488**(上限 65,536,**不超**);
- 每行 1 个 token(上限 65,536),每行都是同一个合法 token `0`;
- `palette` 1 项、`encoding` 派生为 `char`,与声明一致。

也就是说:**项数与行数这两条上界都单独成立**,只有「所有 sprite 的字节加起来」越界。
这正是「文档字节上限必须独立存在」的理由——少了它,这份文档会被判为合法,
而它要求的输出体积根本装不进任何实现。

构造按 §3.7 的规范形式逐字节写出:4096 个 sprite(名字 `s0000`..`s4095`,ASCII 字节序)、
每个 1488 行、行内容 `"0"`、缩进 2 空格、`sprites` 的 `[` 紧跟成员键同行、每个元素的
行各占一行。实测 **67,129,581 字节 = 64.02 MiB**,比 64 MiB 多 **20,717 字节**——
刚好越过阈值,不是靠「远超」蒙过去的。

## recipe

```json
{
  "generator": "sprites-stuffing",
  "params": {
    "width": 1,
    "height": 1,
    "palette": ["FF0000FF"],
    "sprites": 4096,
    "rowsPerSprite": 1488,
    "rowToken": "0",
    "namePrefix": "s",
    "nameDigits": 4
  },
  "expectedInputBytes": 67129581,
  "expectedInputSha256": "0db1ae0414addc6ec5ee167e6080c0b8f93980cd0d15e353cda60227b393da34"
}
```

`expectedInputBytes` 与 `expectedInputSha256` 是**实测值**(生成器跑出来的字节数与
SHA-256),不是估算。runner 用它们判断生成物是否正确,而不是只看「文件存在」。

生成命令(`<py>` 表示本机任一可用的 Python 3.8+ 解释器,不是固定字符串):

```
<py> spec/conformance/tools/gen_cases.py negative/neg-doc-too-large
<py> spec/conformance/tools/gen_cases.py --all
<py> spec/conformance/tools/gen_cases.py --check
```

生成物**从不入库**:`input.pxl` 已被 `.gitignore` 忽略(§13.6「仓库应忽略这些目录里的
输入文件名」)。`--check` 在输入不存在时**失败**——不得因为「没有可检查的文件」
而当成通过。

## 判据

§13.2c:实现必须以 `PXL002` 失败,且**不产出任何输出文件**(不得先写出半份再报错)。
本目录没有 `expected.pxl`、没有 `expected.pixels.sha256`:负例不产出期望像素。

注意这与 `negative/neg-resource-ops-over-limit`(实测 2,130,209 字节,低于 4 MiB,
**必须入库**)的区别不在「大小档次」,而在 §13.6 的判定:输入超过 4 MiB 才允许走生成
路径。
