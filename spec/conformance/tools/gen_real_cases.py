#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`real-*` 用例与 `samples/` 的落地器(10 条 real-* + 1 条抽取负例)。

素材源(只读):
``<set PXL_TEXTURE_ROOT or --samples-root>``
——仓库 `gt6-modern/gt6-modern`,许可 **CC0 1.0**。复制进 `samples/` 时**保留原始字节**,
一个 bit 都不改;来源 commit 记实测值,素材源根可由参数/环境变量传入。

本文件负责的用例(见 `CASES.tsv`):

| 用例 | 形态 | 成员 |
|---|---|---|
| `real-copper-billet` | 往返正例 | 1 |
| `real-void-uniform-alpha0` | 往返正例 | 1 |
| `real-brick-blockdust` | 往返正例 | 1 |
| `real-autoclave-front` | 往返正例 | 1 |
| `real-dragonbreath` | 往返正例 | 1 |
| `real-oil` | 往返正例 | 1 |
| `real-armor-hazard-0` | 往返正例 | 1 |
| `real-chain-overlay-dedupe` | 批量·去重组(§13.7) | 2 |
| `real-fluid-source-255` | 批量·批数组(§13.7) | 255(245 正例 + 10 缺陷成员) |
| `real-item-material-family-400` | 批量·批数组(§13.7) | 400 |
| `negative/neg-real-aerotheum-iccp-crc` | 抽取负例 | 1 |

约定(与 `gen_conformance_cases.py` 相同):

* 幂等、确定性:无时钟、无随机、无 locale、无目录枚举顺序;排序一律按相对路径的
  UTF-8 字节序;两次运行逐字节相同。
* 往返正例的 `expected.pxl` 由**现有 Python 抽取器**(`importer.extract.extract_png`,
  按附录 A)产出——不另写一份抽取逻辑;`expected.pixels.sha256` 由
  `tools/png_ref.py`(fixture 侧的独立解码器)解码 `input.png` 得到的 RGBA8 流按 §13.4 算出。
  两条路径独立,任一条错都会被发现。
* 缺陷成员(§13.7)只有 `input.png` + `expected.error` + `notes.md`,没有 `expected.pxl`、
  没有 `expected.pixels.sha256`;`manifest.tsv` 的 `colors`/`encoding` 列写 `-`。

用法::

    <py> spec/conformance/tools/gen_real_cases.py            # 复制素材 + 写出全部 real- 用例
    <py> spec/conformance/tools/gen_real_cases.py --check    # 只比对,不写文件
    <py> spec/conformance/tools/gen_real_cases.py real-oil   # 只做一条
"""

import hashlib
import os
import sys
import zlib
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parents[2]
CASES_DIR = TOOLS_DIR.parent / "cases"
SAMPLES_DIR = ROOT / "samples"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import png_ref  # noqa: E402  fixture 侧的独立解码器(与 importer 零共享代码)
from importer import extract_png  # noqa: E402  现有抽取器(附录 A 的唯一产线)

#: 素材源根目录(只读)。相对路径一律相对这里。
#:
#: **不再硬编码本机路径**:优先级是命令行 `--samples-root` > 环境变量
#: `PXL_TEXTURE_ROOT` > 下面这个本机默认值。其他开发者/CI 只要把源克隆到任意位置、
#: 设一次环境变量就能逐字节复现同一份语料(审查把它列为发布阻塞项之一)。
_DEFAULT_SOURCE_ROOT = (
    r"")


def _resolve_source_root(argv=None):
    """解析素材源根目录:命令行 > 环境变量 > 本机默认值。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    for index, argument in enumerate(arguments):
        if argument == "--samples-root" and index + 1 < len(arguments):
            return Path(arguments[index + 1])
        if argument.startswith("--samples-root="):
            return Path(argument.split("=", 1)[1])
    from_env = os.environ.get("PXL_TEXTURE_ROOT")
    if from_env:
        return Path(from_env)
    return Path(_DEFAULT_SOURCE_ROOT)


SOURCE_ROOT = _resolve_source_root()
SOURCE_ROOT_RELATIVE = "neoforge/src/main/resources/assets/gt6modern/textures"

SOURCE_REPO = "gt6-modern/gt6-modern"
SOURCE_LICENSE = "CC0 1.0"
#: 实测的上游 commit(素材源是 git 工作树,`git rev-parse HEAD` 可直接得到)。
SOURCE_COMMIT = "0d8c76109f46b52e4fd4f30b705f8407d01574ab"

#: 两条批量用例宣称的文件数(§13.7:必须与 manifest 行数、成员目录数一致)。
FLUID_COUNT = 255
MATERIAL_COUNT = 400

#: 单例用例 → 素材相对路径
SINGLES = (
    ("real-copper-billet", "item/material_family/copper/billet.png"),
    ("real-void-uniform-alpha0", "item/material_family/copper/void.png"),
    ("real-brick-blockdust", "block/material_family/brick/blockdust.png"),
    ("real-autoclave-front",
     "block/machine/basicmachines/autoclave/overlay_active/front.png"),
    ("real-dragonbreath", "block/fluid/source/dragonbreath.png"),
    ("real-oil", "block/fluid/source/oil.png"),
    ("real-armor-hazard-0", "armor/legacy/hazard_biochemgas/0.png"),
)

#: 去重组(§13.7):两个成员必须产出逐字节相同的 `expected.pxl`
DEDUPE = (
    ("copper-chain-overlay", "item/material_family/copper/chain_overlay.png"),
    ("wood-chain-overlay", "item/material_family/wood/chain_overlay.png"),
)


# --------------------------------------------------------------------------
# 素材枚举与复制
# --------------------------------------------------------------------------

def _relative_paths(directory, pattern="*.png", recursive=False):
    """按**相对素材根的路径的 UTF-8 字节序**排序(§9:不依赖目录枚举顺序)。"""
    if recursive:
        candidates = [path for path in directory.rglob(pattern) if path.is_file()]
    else:
        suffix = pattern.lstrip("*")
        candidates = [path for path in directory.iterdir()
                      if path.is_file() and path.name.endswith(suffix)]
    pairs = []
    for path in candidates:
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        pairs.append((relative.encode("utf-8"), relative))
    pairs.sort()
    return [relative for _key, relative in pairs]


def fluid_sources():
    """`block/fluid/source/` 下恰好 255 个 `.png`,按文件名 UTF-8 字节序。"""
    paths = _relative_paths(SOURCE_ROOT / "block" / "fluid" / "source")
    if len(paths) != FLUID_COUNT:
        raise RuntimeError("block/fluid/source 下应恰好 %d 个 .png,实测 %d"
                           % (FLUID_COUNT, len(paths)))
    return paths


def material_sources():
    """`item/material_family/**/*.png` 按相对路径 UTF-8 字节序取前 400。"""
    paths = _relative_paths(SOURCE_ROOT / "item" / "material_family", recursive=True)
    if len(paths) < MATERIAL_COUNT:
        raise RuntimeError("item/material_family 下只有 %d 个 .png,不足 %d"
                           % (len(paths), MATERIAL_COUNT))
    return paths[:MATERIAL_COUNT]


def sample_relpath(relative):
    """素材在 `samples/` 里的相对路径(与素材源同构,便于一眼对上)。"""
    return relative


def _read_source(relative):
    path = SOURCE_ROOT.joinpath(*relative.split("/"))
    return path.read_bytes()


def sha256_hex(payload):
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# 素材事实:尺寸、色数、档位、iCCP CRC
# --------------------------------------------------------------------------

def facts(payload):
    """用 fixture 侧的独立解码器量出 ``(width, height, n, encoding)``。"""
    width, height, rgba = png_ref.decode_rgba8(payload)
    seen = {}
    transparent = b"\x00\x00\x00\x00"
    for offset in range(0, len(rgba), 4):
        pixel = rgba[offset:offset + 4]
        if pixel == transparent:
            continue
        if pixel not in seen:
            seen[pixel] = len(seen) + 1
    count = len(seen)
    if count <= 61:
        encoding = "char"
    elif count <= 255:
        encoding = "index"
    elif count <= 65535:
        encoding = "index4"
    else:
        raise RuntimeError("色数 %d 超出 index4 容量" % count)
    return width, height, count, encoding


def iccp_crc(payload):
    """返回第一个 `iCCP` chunk 的 ``(存储 CRC, 计算 CRC, 数据长度)``;没有则为 ``None``。"""
    position = 8
    while position < len(payload):
        length = int.from_bytes(payload[position:position + 4], "big")
        ctype = payload[position + 4:position + 8]
        body = payload[position + 8:position + 8 + length]
        stored = int.from_bytes(payload[position + 8 + length:position + 12 + length],
                                "big")
        if ctype == b"iCCP":
            computed = zlib.crc32(ctype + body) & 0xFFFFFFFF
            return stored, computed, length
        position += 12 + length
    return None


def defect_list(paths):
    """逐条跑现有解码器:被 `PXL006` 拒绝的就是已知缺陷成员(§13.7)。"""
    from importer.errors import PxlError

    defects = []
    for relative in paths:
        payload = _read_source(relative)
        try:
            extract_png(payload)
        except PxlError as error:
            if error.code != "PXL006":
                raise RuntimeError("%s 被拒但不是 PXL006: %s" % (relative, error))
            defects.append((relative, payload))
    return defects


# --------------------------------------------------------------------------
# 文本拼装
# --------------------------------------------------------------------------

def _manifest(rows):
    lines = ["member\tinput\tencoding\tcolors"]
    for member, source, encoding, colors in rows:
        lines.append("%s\tsample:%s\t%s\t%s" % (member, source, encoding, colors))
    return "\n".join(lines) + "\n"


def _round_trip_notes(case, member, relative, width, height, count, encoding,
                      digest, suite=None, clauses=""):
    lines = [
        "# %s" % (member or case),
        "",
        "- 形态:**往返正例**(§13.1 / README §2.1a)",
        "- 来源:`%s`(逐字节副本;见 `samples/PROVENANCE.md`)" % (relative,),
    ]
    if suite:
        lines.append("- 所属用例:`%s`(批量形态,§13.7)" % (suite,))
        lines.append("- `manifest.tsv` 的 `input` 列:`sample:%s`" % (relative,))
    lines.extend([
        "- 尺寸:%dx%d" % (width, height),
        "- 非透明色数(n):%d → `encoding` = `%s`(§4.5 由 `palette` 长度派生)"
        % (count, encoding),
        "- `expected.pixels.sha256`:`%s`" % digest,
        "",
        "## 覆盖条款",
        "",
        clauses or ("`§13.2a` 六条:抽取零 diff、回渲像素摘要一致、跨语言闭环、两跑确定性、"
                    "尺寸一致、输出结构合规。"),
        "",
        "## 期望值来源",
        "",
        "两条独立路径,任一错都会被交叉发现:",
        "",
        "- `expected.pxl`:由**现有 Python 抽取器**(`importer.extract.extract_png`)按附录 A",
        "  对 `input.png` 产出,**未手调**任何空白或缩进;",
        "- `expected.pixels.sha256`:由 `spec/conformance/tools/png_ref.py`(fixture 侧的独立",
        "  解码器,与 `importer` 零共享代码)解码 `input.png` 得到 RGBA8 流,"
        "按 §13.4 计算",
        "  (逐行、行内自左向右、自上而下,每像素 R,G,B,A,无填充,共 %d 字节)。"
        % (4 * width * height),
        "",
        "## 判据",
        "",
        "§13.2a 六条同时成立,其中容易漏掉的两条:",
        "",
        "- **尺寸一致**:回渲 PNG 的宽高必须等于 `expected.pxl` 声明值、也等于 `input.png` 的尺寸",
        "  (摘要只覆盖像素字节,不含尺寸);",
        "- **输出结构合规**:8 bit / 色彩类型 6 / 非交错 / 所有行过滤器字节恒 0 /",
        "  chunk 类型序列 `IHDR` → `IDAT`⁺ → `IEND`(§10)。",
    ])
    return "\n".join(lines) + "\n"


def _defect_notes(case, member, relative, stored, computed, suite=None):
    lines = [
        "# %s" % (member or case),
        "",
        "- 形态:**已知缺陷成员**(§13.7),按负例判据(§13.2c)判定",
        "- **受测侧**:抽取侧(目录内只有 `input.png`)",
        "- 受测输入:`input.png`(来源 `%s`)" % (relative,),
        "- 期望错误码:`PXL006`(见 `expected.error`,一行 + LF)",
    ]
    if suite:
        lines.append("- 所属用例:`%s`(批量形态,§13.7)" % (suite,))
        lines.append("- `manifest.tsv` 的 `input` 列:`sample:%s`;该行 `colors`/`encoding` 写 `-`"
                     % (relative,))
    lines.extend([
        "",
        "## 覆盖条款",
        "",
        "`§8.5`(**每一个 chunk 的 CRC 都必须校验**,不逐类型列举)、`§11`(`PXL006` 的范围"
        "包含 §8.5 列出的全部结构与校验拒绝项)、`§13.7`(已知缺陷成员)。",
        "",
        "## 为什么这是素材缺陷而不是实现缺陷",
        "",
        "该文件的 `iCCP` chunk **CRC 损坏**:",
        "",
        "```",
        "chunk 类型  : iCCP",
        "存储 CRC    : %08X" % stored,
        "计算 CRC    : %08X   (CRC-32 over 类型名 + 数据)" % computed,
        "```",
        "",
        "其余 chunk(含 IDAT、IEND)的 CRC 全部正确,pixels 完好,IDAT 解压字节数也与",
        "`height × (1 + width × 4)` 严格吻合——**像素与容器合法性是两件事**。",
        "§8.5 规定 CRC 一律校验,所以 v0 只能拒绝它:`PXL006`。把它写成往返正例,"
        "要么迫使实现放宽 CRC,要么让用例永远失败,两者都不可接受。",
        "",
        "## 判据",
        "",
        "§13.2c:实现必须以 `PXL006` 失败,且**不产出任何输出文件**。",
        "本目录**没有** `expected.pxl`、**没有** `expected.pixels.sha256`——缺陷成员不可能有期望像素。",
    ])
    return "\n".join(lines) + "\n"


def _suite_notes(case, lines_block):
    head = [
        "# %s" % case,
        "",
        "- 形态:**批量用例**(§13.7):一个用例多个输入,成员放在 `members/` 下",
        "- 成员清单:`manifest.tsv`(制表符分隔,表头 `member` / `input` / `encoding` / `colors`)",
        "- `input` 列恒为 `sample:<samples/ 下的相对路径>`,且该路径已在 "
        "`samples/PROVENANCE.md` 登记",
        "",
    ]
    return "\n".join(head + lines_block) + "\n"


# --------------------------------------------------------------------------
# 各用例的构造
# --------------------------------------------------------------------------

def build_provenance():
    """`samples/PROVENANCE.md`:来源、license、逐文件 SHA-256。"""
    fluid = fluid_sources()
    material = material_sources()
    singles = [relative for _case, relative in SINGLES]
    dedupe = [relative for _member, relative in DEDUPE]
    listed = []
    for relative in sorted(set(fluid + material + singles + dedupe)):
        listed.append(relative)

    lines = [
        "# samples — 素材来源与指纹",
        "",
        "本目录是 `spec/conformance/cases/real-*` 用到的真实素材副本。",
        "**复制件逐字节等同原文件**(没有任何重编码、没有元数据剥离、没有行尾处理)。",
        "",
        "## 来源",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        "| 仓库 | `%s` |" % SOURCE_REPO,
        "| 许可 | **%s** |" % SOURCE_LICENSE,
        "| 上游 commit | **%s** |" % SOURCE_COMMIT,
        "| 素材源在仓库里的相对位置 | `%s` |" % SOURCE_ROOT_RELATIVE,
        "| 复现方式 | 设 `PXL_TEXTURE_ROOT=<素材源根>`（或传 `--samples-root <路径>`）后跑 "
        "`gen_real_cases.py`；同样的源应产出逐字节相同的 `samples/` 与语料 |",
        "| 素材源相对根 | `neoforge/src/main/resources/assets/gt6modern/textures/` |",
        "| 本机只读路径 | `%s` |" % SOURCE_ROOT.as_posix(),
        "| 复制方式 | 逐文件按字节复制(`shutil` 语义的 `read_bytes`/`write_bytes`) |",
        "",
        "## 覆盖范围",
        "",
        "| 目录 | 复制数量 | 用途 |",
        "|---|---|---|",
        "| `block/fluid/source/*.png` | %d | `real-fluid-source-255`(§13.7 批数组)与 "
        "`real-dragonbreath` / `real-oil` / `negative/neg-real-aerotheum-iccp-crc` |" % len(fluid),
        "| `item/material_family/**/*.png`(按相对路径 UTF-8 字节序取前 %d) | %d | "
        "`real-item-material-family-400`(§13.7 批数组)与 `real-copper-billet` / "
        "`real-void-uniform-alpha0` / `real-chain-overlay-dedupe` |"
        % (MATERIAL_COUNT, len(material)),
        "| 其余单例素材 | %d | `real-brick-blockdust` / `real-autoclave-front` / "
        "`real-armor-hazard-0` |" % len(singles),
        "",
        "`item/material_family` 全量有 8,610 个 `.png`;这里只复制排序后的前 %d 个"
        "(§13.7 只要求这一批)。" % MATERIAL_COUNT,
        "",
        "## 逐文件指纹(路径相对 `samples/`)",
        "",
        "| 路径 | 字节 | SHA-256 |",
        "|---|---|---|",
    ]
    total = 0
    for relative in listed:
        payload = _read_source(relative)
        total += len(payload)
        lines.append("| `%s` | %d | `%s` |"
                     % (sample_relpath(relative), len(payload), sha256_hex(payload)))
    lines.extend([
        "",
        "合计 **%d** 个文件、**%d** 字节。" % (len(listed), total),
        "",
        "## 已知缺陷(素材侧,非实现侧)",
        "",
        "`block/fluid/source/` 里 **10** 个文件的 `iCCP` chunk CRC 已损坏(其余 chunk 全部正确、",
        "像素完好)。按 `spec/PXL.md` §8.5「每个 chunk 的 CRC 都必须校验」它们只能是",
        "`PXL006`:",
        "",
    ])
    defects = defect_list(fluid)
    lines.append("| 文件 | 存储 CRC | 计算 CRC |")
    lines.append("|---|---|---|")
    for relative, payload in defects:
        crc = iccp_crc(payload)
        lines.append("| `%s` | `%08X` | `%08X` |"
                     % (sample_relpath(relative), crc[0], crc[1]))
    lines.extend([
        "",
        "实测 **%d** 个 .png 中 **%d** 个可往返、**%d** 个必须拒绝。裁决记录见 "
        "`docs/decisions/0002-strict-png-validation.md`。"
        % (len(fluid), len(fluid) - len(defects), len(defects)),
        "",
    ])
    return "\n".join(lines) + "\n", listed


def _member_files_round_trip(payload, relative, clauses, suite=None, member=None):
    width, height, count, encoding = facts(payload)
    digest = sha256_hex(png_ref.decode_rgba8(payload)[2])
    text = extract_png(payload)
    return {
        "input.png": payload,
        "expected.pxl": text.encode("utf-8"),
        "expected.pixels.sha256": (digest + "\n").encode("ascii"),
        "notes.md": _round_trip_notes(None, member, relative, width, height, count,
                                      encoding, digest, suite=suite,
                                      clauses=clauses).encode("utf-8"),
    }, (width, height, count, encoding)


def _single_clauses(case):
    table = {
        "real-copper-billet":
            "`§7` / `§3.5`(该图含 8 个 RGB 非零且 A = 0 的边界像素,必须保留自己的 slot、"
            "不得并入透明 slot;`char` 档真实素材)、`§5.0`(图元覆盖集)、`§13.2a`。",
        "real-void-uniform-alpha0":
            "`§3.5` / `§7`(全图 256 个像素均为 `FFFFFF00`):`palette` 长度必须是 **1** 而不是 0,"
            "回渲必须逐字节得到全 `FFFFFF00` 而不是全 `00000000` —— 钉住 §6.3 的 `aNum == 0` "
            "分支。`copper/void.png` 与 `copper/chain_overlay.png` **像素相同、字节不同**"
            "(§10 只比像素)。",
        "real-brick-blockdust":
            "`§4.4` / `§4.5`(实测 36 色、256/256 全不透明 → `char` 档)、"
            "`AGENTS.md` 契约 5 的 `rect` 压缩不破坏像素、附录 A 第 3–5 步。",
        "real-autoclave-front":
            "`§4.5`(实测 147 色 → `index` 档;62 ≤ n ≤ 255)、`§4.3`(两位大写十六进制 token)、"
            "`§3.5` / `§7`(256 个像素中 0 个 `00000000`、120 个 RGB 非零且 A = 0)。",
        "real-dragonbreath":
            "`§4.5`(实测 335 个非透明色 → 强制 `index4`;这也是两档方案被否决的原因)、"
            "`§4.3`(四位大写十六进制、`0000` 保留给透明)、真实 16×512 帧带。",
        "real-oil":
            "`§8.1` / `§8.5`(带 `bKGD` / `pHYs` / `tIME` 但没有 `iCCP` 的对照样本:ancillary "
            "必须被忽略、像素不变)、`§4.5`(实测 11 色 → `char`)。",
        "real-armor-hazard-0":
            "`§5.0.1`(64×32 非方形、面积 2048)、`§7`(1694 个 alpha = 0 像素中大量 RGB 非零)、"
            "`§4.5`(实测 14 色 → `char`)。",
    }
    return table.get(case, "")


def build_single(case, relative):
    payload = _read_source(relative)
    files, _fact = _member_files_round_trip(payload, relative, _single_clauses(case))
    return files


def build_dedupe():
    members = {}
    manifest_rows = []
    for member, relative in DEDUPE:
        payload = _read_source(relative)
        files, fact = _member_files_round_trip(
            payload, relative,
            "`§13.7`(去重组):`copper/chain_overlay.png` 与 `wood/chain_overlay.png` "
            "**字节完全相同**(SHA-256 `9a8136434004ec3a…`),同图多路径必须产出同一份 "
            "`expected.pxl`;`§3.5` / `§7`(全图 `FFFFFF00`,`palette` 长度 1)。",
            suite="real-chain-overlay-dedupe", member=member)
        members[member] = files
        manifest_rows.append((member, relative, fact[3], str(fact[2])))
    aggregate = [
        "## aggregate",
        "",
        "**去重组**(§13.7):",
        "",
        "1. `members/copper-chain-overlay/expected.pxl` 与 "
        "`members/wood-chain-overlay/expected.pxl` 必须**逐字节相同**"
        "(不是「看起来一样」);",
        "2. 两份 `expected.pixels.sha256` 也必须逐字节相同;",
        "3. `manifest.tsv` 行数 = 成员目录数 = **2**。",
        "",
        "两个成员的上游文件字节相同(SHA-256 `9a8136434004ec3a3d54f0474bac6b85ea86cc7ee869976cfd0c18b2a753019b`),",
        "因此 1、2 两条不成立时,要么抽取器依赖了路径,要么它依赖了没写进规范的东西。",
    ]
    return {"manifest.tsv": _manifest(manifest_rows).encode("utf-8"),
            "notes.md": _suite_notes("real-chain-overlay-dedupe", aggregate).encode("utf-8"),
            "members": members}


def build_batch(case, paths, clauses, aggregate_lines, manifest_limit=None):
    members = {}
    manifest_rows = []
    defects = defect_list(paths)
    defect_paths = {relative for relative, _payload in defects}
    positives = 0
    seen = {}
    for relative in paths:
        member = _member_name(relative)
        if member in seen:
            raise RuntimeError("成员名冲突:%s 与 %s 都折成 %r"
                               % (seen[member], relative, member))
        seen[member] = relative
        payload = _read_source(relative)
        if relative in defect_paths:
            crc = iccp_crc(payload)
            members[member] = {
                "input.png": payload,
                "expected.error": b"PXL006\n",
                # 拒绝原因也必须钉住（Core §13.1 / §13.2c）：这一排缺陷都报 PXL006，
                # 只判码时"CRC 错被别的分支拒掉"照样通过。
                "expected.error_detail": "iCCP 的 CRC\n".encode("utf-8"),
                "notes.md": _defect_notes(case, member, relative,
                                          crc[0], crc[1], suite=case).encode("utf-8"),
            }
            manifest_rows.append((member, relative, "-", "-"))
            continue
        files, fact = _member_files_round_trip(payload, relative, clauses,
                                               suite=case, member=member)
        members[member] = files
        manifest_rows.append((member, relative, fact[3], str(fact[2])))
        positives += 1
    if manifest_limit is not None and len(manifest_rows) != manifest_limit:
        raise RuntimeError("%s 的成员数 %d != %d" % (case, len(manifest_rows),
                                                     manifest_limit))
    aggregate = ["## aggregate", ""] + aggregate_lines
    return {
        "manifest.tsv": _manifest(manifest_rows).encode("utf-8"),
        "notes.md": _suite_notes(case, aggregate).encode("utf-8"),
        "members": members,
        "_positives": positives,
        "_defects": len(defects),
    }


def _member_name(relative):
    """kebab-case 成员名:相对路径逐段拼接,非 `[a-z0-9]` 一律折成 `-`。"""
    stem = relative[:-len(".png")] if relative.endswith(".png") else relative
    pieces = []
    for part in stem.split("/"):
        cleaned = []
        for char in part.lower():
            cleaned.append(char if char.isalnum() and char.isascii() else "-")
        pieces.append("".join(cleaned))
    name = "-".join(piece.strip("-") for piece in pieces if piece.strip("-"))
    return name


FLUID_CLAUSES = ("`§13.7`(批数组):255 个 `.png` 的全量解码回归——尺寸、深度 8、非交错、"
                 "色彩类型 6/2、色数与档位、抽取零 diff、回渲像素一致。"
                 "`§8.1`(输入接受集)、`§4.5`(档位派生)。")
MATERIAL_CLAUSES = ("`§13.7`(批数组):`item/material_family` 前 400 个文件——吞吐与"
                    "去重统计的真实素材面;16×16、档位从 `char` 到 `index4` 都有。"
                    "`§8.1`、`§4.5`、`§13.2a` 六条。")


def build_fluid():
    paths = fluid_sources()
    defects = defect_list(paths)
    positive = len(paths) - len(defects)
    aggregate = [
        "**批数组**(§13.7):",
        "",
        "1. `manifest.tsv` 行数 = 成员目录数 = **%d** = `## aggregate` 里写死的成员数;" % FLUID_COUNT,
        "2. 往返正例成员 **%d** 个,已知缺陷成员 **%d** 个,**%d + %d = %d**;"
        % (positive, len(defects), positive, len(defects), FLUID_COUNT),
        "3. 每个成员按 §13.2a(正例)或 §13.2c(缺陷成员)各自通过。",
        "",
        "缺陷成员的清单由**实测**得到:对 255 个文件逐个跑现有解码器,被 `PXL006` 拒绝的"
        "就是它们;清单与实测 CRC 见 `notes.md` 末尾与 `samples/PROVENANCE.md`。",
        "",
        "| 成员 | 存储 CRC | 计算 CRC |",
        "|---|---|---|",
    ]
    for relative, payload in defects:
        crc = iccp_crc(payload)
        aggregate.append("| `%s` | `%08X` | `%08X` |"
                         % (_member_name(relative), crc[0], crc[1]))
    return build_batch("real-fluid-source-255", paths, FLUID_CLAUSES, aggregate,
                       manifest_limit=FLUID_COUNT)


def build_material():
    paths = material_sources()
    aggregate = [
        "**批数组**(§13.7):",
        "",
        "1. `manifest.tsv` 行数 = 成员目录数 = **%d** = `## aggregate` 里写死的成员数;" % MATERIAL_COUNT,
        "2. 每个成员按 §13.2a 六条各自通过;",
        "3. 成员集合是 `item/material_family/**/*.png` 按**相对路径 UTF-8 字节序**排序后的"
        "前 %d 个——排序方式写死,否则「取前 400 个」在两台机器上不是同一批文件。"
        % MATERIAL_COUNT,
    ]
    return build_batch("real-item-material-family-400", paths, MATERIAL_CLAUSES,
                       aggregate, manifest_limit=MATERIAL_COUNT)


def build_aerotheum_negative():
    relative = "block/fluid/source/aerotheum.png"
    payload = _read_source(relative)
    crc = iccp_crc(payload)
    notes = _defect_notes("negative/neg-real-aerotheum-iccp-crc", None, relative,
                          crc[0], crc[1])
    notes += (
        "\n## 与批量用例的关系(§13.7)\n\n"
        "`aerotheum.png` **同时**是 `real-fluid-source-255` 的 10 个已知缺陷成员之一"
        "(算在 245 之外的 10 里),本条是它的**单例**负例:批量成员证明「这 10 个都拒」,"
        "单例证明「这一个拒得对」。两者不冲突,也不能相互替代。\n\n"
        "实测色数 **68**(计透明 slot 为 69)→ `index` 档;该文件另带 `zTXt`(EXIF)、"
        "`bKGD`、`pHYs`、`tIME`。iCCP/zTXt 的解码兼容性覆盖改由合成用例承担。\n")
    return {
        "input.png": payload,
        "expected.error": b"PXL006\n",
        "expected.error_detail": "iCCP 的 CRC\n".encode("utf-8"),
        "notes.md": notes.encode("utf-8"),
    }


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------

def all_jobs():
    jobs = []
    jobs.append(("real-copper-billet", lambda: build_single(*_find_single("real-copper-billet"))))
    jobs.append(("real-void-uniform-alpha0",
                 lambda: build_single(*_find_single("real-void-uniform-alpha0"))))
    jobs.append(("real-brick-blockdust", lambda: build_single(*_find_single("real-brick-blockdust"))))
    jobs.append(("real-autoclave-front", lambda: build_single(*_find_single("real-autoclave-front"))))
    jobs.append(("real-dragonbreath", lambda: build_single(*_find_single("real-dragonbreath"))))
    jobs.append(("real-oil", lambda: build_single(*_find_single("real-oil"))))
    jobs.append(("real-armor-hazard-0", lambda: build_single(*_find_single("real-armor-hazard-0"))))
    jobs.append(("real-chain-overlay-dedupe", build_dedupe))
    jobs.append(("real-fluid-source-255", build_fluid))
    jobs.append(("real-item-material-family-400", build_material))
    jobs.append(("negative/neg-real-aerotheum-iccp-crc", build_aerotheum_negative))
    return jobs


def _find_single(case):
    for name, relative in SINGLES:
        if name == case:
            return case, relative
    raise KeyError(case)


def _write_tree(directory, files, check_only):
    """把 ``{相对名: 字节}`` 写到目录下(``members/<member>/…`` 递归展开)。"""
    problems = []
    for name in sorted(files):
        if name.startswith("_"):
            continue                      # 元数据(正例/缺陷计数),不是文件
        value = files[name]
        if name == "members":
            for member in sorted(value):
                sub = directory / "members" / member
                problems.extend(_write_tree(sub, value[member], check_only))
            continue
        path = directory / name
        if check_only:
            if not path.is_file():
                problems.append("缺少 %s" % path.as_posix())
            elif path.read_bytes() != value:
                problems.append("%s 与生成结果不一致" % path.as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(value)
    return problems


def main(argv):
    global SOURCE_ROOT
    args = argv[1:]
    check_only = False
    selected = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--check":
            check_only = True
        elif item in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        elif item == "--samples-root":
            if index + 1 >= len(args):
                sys.stderr.write("--samples-root 需要一个路径\n")
                return 2
            index += 1
            SOURCE_ROOT = Path(args[index])
        elif item.startswith("--samples-root="):
            SOURCE_ROOT = Path(item.split("=", 1)[1])
        elif item.startswith("-"):
            sys.stderr.write("未知选项: %s\n" % item)
            return 2
        else:
            selected.append(item)
        index += 1

    if not SOURCE_ROOT.is_dir():
        sys.stderr.write("素材源不可读: %s\n" % SOURCE_ROOT.as_posix())
        return 2

    # 1) samples/ 与 PROVENANCE.md
    provenance, listed = build_provenance()
    sample_files = {}
    for relative in listed:
        sample_files[relative] = _read_source(relative)
    problems = _write_tree(SAMPLES_DIR, sample_files, check_only)
    if check_only:
        if not (SAMPLES_DIR / "PROVENANCE.md").is_file():
            problems.append("缺少 samples/PROVENANCE.md")
        elif (SAMPLES_DIR / "PROVENANCE.md").read_bytes() != provenance.encode("utf-8"):
            problems.append("samples/PROVENANCE.md 与生成结果不一致")
    else:
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        with (SAMPLES_DIR / "PROVENANCE.md").open("wb") as handle:
            handle.write(provenance.encode("utf-8"))
    print("samples/: %d 个素材 + PROVENANCE.md%s"
          % (len(sample_files), "" if not problems else "(见下方问题)"))

    # 2) 用例目录
    jobs = all_jobs()
    if selected:
        known = dict(jobs)
        missing = [name for name in selected if name not in known]
        if missing:
            sys.stderr.write("未知用例: %s\n可用: %s\n"
                             % (", ".join(missing), ", ".join(name for name, _ in jobs)))
            return 2
        jobs = [(name, known[name]) for name in selected]

    print("%-40s %-10s %s" % ("case", "成员", "状态"))
    total_members = 0
    for case, builder in jobs:
        files = builder()
        members = files.get("members")
        count = len(members) if members else 1
        total_members += count
        directory = CASES_DIR.joinpath(*case.split("/"))
        case_problems = _write_tree(directory, files, check_only)
        problems.extend(case_problems)
        detail = "OK" if not case_problems else "FAIL"
        extra = ""
        if "_positives" in files:
            extra = "  正例 %d / 缺陷 %d" % (files["_positives"], files["_defects"])
        print("%-40s %-10s %s%s" % (case, count, detail, extra))

    print("成员合计 %d 个" % total_members)
    if problems:
        print("问题 %d 条:" % len(problems))
        for problem in problems[:40]:
            print("  - %s" % problem)
        return 1
    print("校验完成:全部一致" if check_only else "写出完成")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
