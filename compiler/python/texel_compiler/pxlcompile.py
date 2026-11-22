"""v1 编译会话:分派、依赖图、预算与依赖清单(v1 §2 / §5 / §9 / §10)。

一次编译就是一个 target:一个入口逻辑路径 + 一组显式 bindings + 一个 resolver 上下文,
产出一个 RGBA8 位图与一份 §9.4 的依赖清单。

**三相位**(与 Java 侧 compiler/java 的 PxlCompiler 逐格一致):

* **相位 A load_graph** —— 解析并校验整张依赖图,只统计尺寸与几何量(每个 stamp 裁剪后的
  写入面积),**一个像素都不写**;唯一路径 / 唯一文件 / 累计字节 / 渲染节点身份只记账**不判定**;
* **相位 B check_budgets** —— 依赖图固定之后,按 §11.2 阶段 R4 的表内顺序统一判预算
  (深度已在相位 A 入口判,见 §10.4 第 3 条);
* **相位 C rasterize** —— 预算通过之后才写像素。

为什么必须拆:预算若在 R2 期间就判,「依赖缺失」(PXL009)会被误报成「预算超限」(PXL011);
预算若等光栅化之后再判,判定之前的工作量就完全没有上界(`neg-budget-global-raster` 一条就要
两千万次写入)。两条同时满足只有一个结构:先只算数、后判数、最后才画。

**错误一律带引用链**(§9.3):每条 PxlError 都携带「入口 → … → 实际失败文件」的路径链,
由 Session 的统一包装点补齐(见 load_graph 的 _with_chain)。
"""

import hashlib
import os
from pathlib import Path

from . import jsonx, logicalpath, numlit, params as paramlib, pngdec
from .doc import MAX_DOCUMENT_BYTES, Builder, ResolvedAsset
from .errors import PxlError
from .jsonx import JsonNumber
from .resolver import CASE_MISMATCH, MISSING, OK

SUPPORTED_VERSIONS = (1,)

#: v1 §10.2 的会话级上界。全部是"不超过"语义:恰好等于上限合法。
MAX_REF_DEPTH = 64

#: 冷遍历顺序号游标（构建顺序即 DFS 顺序）。
_ORDER_SEQ = 0
MAX_RENDER_NODES = 4096
MAX_SOURCE_FILES = 8192
MAX_PXL_BYTES = 64 * 1024 * 1024
MAX_PNG_PIXELS = 1 << 24
MAX_GLOBAL_RASTER = 1 << 24

def chain_text(steps):
    return " -> ".join(steps)


class Dependency(object):
    """§9.4 清单的一行。"""

    __slots__ = ("role", "kind", "logical_path", "sha256")

    def __init__(self, role, kind, logical_path, sha256):
        self.role = role
        self.kind = kind
        self.logical_path = logical_path
        self.sha256 = sha256

    def to_tsv(self):
        return "\t".join([self.role, self.kind, self.logical_path, self.sha256])


class Compiled(object):
    __slots__ = ("width", "height", "rgba", "dependencies", "doc")

    def __init__(self, width, height, rgba, dependencies, doc):
        self.width = width
        self.height = height
        self.rgba = rgba
        self.dependencies = dependencies
        self.doc = doc

    def manifest_text(self):
        return "".join([item.to_tsv() + "\n" for item in self.dependencies])


class Budget(object):
    """一项会话预算:**只累计,不判定**。

    判定统一放在相位 B。记账时顺手记住"第一个把计数推过上限的源文件"和那一刻的引用链,
    这样 PXL011 的消息能带上 §9.3 要求的失败文件与完整链。
    """

    __slots__ = ("label", "limit", "value", "path", "chain")

    def __init__(self, label, limit):
        self.label = label
        self.limit = limit
        self.value = 0
        self.path = None
        self.chain = []

    def add(self, amount, where, chain):
        self.value += amount
        if self.path is None and self.value > self.limit:
            self.path = where
            self.chain = list(chain)

    def exceeded(self):
        return self.value > self.limit

    def describe(self):
        return (self.label + " " + str(self.value) + " 超过上限 " + str(self.limit)
                + "（越过上限的源文件: " + str(self.path) + "；引用链: "
                + chain_text(self.chain) + "）")


class _Node(object):
    __slots__ = ("logical_path", "doc", "key", "rgba", "height", "subtree", "min_depth", "min_order", "max_depth", "children")

    def __init__(self, logical_path, doc, key, order):
        self.logical_path = logical_path
        self.doc = doc
        self.key = key
        self.rgba = None
        self.height = 1
        self.subtree = set([logical_path])
        #: 后代路径 -> 从本节点出发的最小边距(自身 0)。缓存命中时用它还原
        #: 「冷遍历会在第几层撞上这个祖先」，而不是拿整棵子树的高度去估。
        self.min_depth = {logical_path: 0}
        #: 后代路径 -> 冷遍历顺序号（越小越先被访问）；缓存命中时用来判「循环与超深谁先」。
        #: 号由 _load_node 在**进入节点时**分配（先序）；在这里分配会变成后序，
        #: 那是发布态复核 P2：同一图形 Java 报深度 65、Python 报 73。
        self.min_order = {logical_path: order}
        #: 后代路径 -> 最大边距：判「是否存在超过深度上限的访问」。
        self.max_depth = {logical_path: 0}
        #: 直接子节点：重建引用链时按真实边下降，不能靠深度猜。
        self.children = {}


class Session(object):
    """一次编译会话:引用栈、依赖图、预算计数、清单。"""

    def __init__(self, resolver):
        self.resolver = resolver
        #: logical_path -> 原始字节(每个实际源文件只记一次)
        self.files = {}
        #: logical_path -> "pxl" / "png"
        self.kinds = {}
        #: (logical_path, 规范 bindings) -> _Node
        self.nodes = {}
        #: logical_path -> 解码像素数(按唯一文件)
        self.png_pixels = {}
        #: logical_path -> 已解码的 PNG(宽高 + rgba)。§10.2 按唯一文件计像素,隐含"内存也按
        #: 唯一文件持有一份":没有这张表时 N 个节点引用同一张图就是 N 份 4*w*h,预算全合法
        #: 的图也能把进程打 OOM。缓存命中同时就完成了像素预算的唯一文件口径。
        self.png_cache = {}
        #: 已计过累计字节的 PXL 文件(同一文件读两次只算一次,§10.2「按唯一文件」)
        self.pxl_counted = set()
        #: 引用栈:祖先逻辑路径。链文案与深度判定都用它。
        self.stack = []
        self.node_budget = Budget("唯一渲染节点数", MAX_RENDER_NODES)
        self.file_budget = Budget("唯一实际源文件数", MAX_SOURCE_FILES)
        self.byte_budget = Budget("累计 PXL UTF-8 字节数", MAX_PXL_BYTES)
        self.pixel_budget = Budget("累计 PNG 解码像素数", MAX_PNG_PIXELS)
        self.raster_budget = Budget("全局光栅工作", MAX_GLOBAL_RASTER)

    # ------------------------------------------------------------ 引用链

    def chain(self, failing=None):
        """「入口 → … → 失败文件」的路径链(§9.3)。"""
        steps = list(self.stack)
        # 只在末尾不是它时追加；自环的闭环由**循环检测点**自己补（见 PXL010 分支），
        # 否则普通失败会被追加两次（发布态复核 F7：a4 出现两遍）。
        if failing is not None and (not steps or steps[-1] != failing):
            steps.append(failing)
        return steps

    def error(self, code, message, failing=None):
        return PxlError(code, message, self.chain(failing))

    @staticmethod
    def _with_chain(failure, chain):
        """深层的 PxlError(doc/jsonx/params/pngdec 抛的)在这里补上引用链。"""
        if failure.chain:
            return failure
        return PxlError(failure.code, failure.message, list(chain))

    # ------------------------------------------------------------ 记账

    def add_file(self, logical_path, kind, data):
        if logical_path not in self.files:
            self.files[logical_path] = data
            self.kinds[logical_path] = kind
            self.file_budget.add(1, logical_path, self.chain(logical_path))

    def add_png_pixels(self, logical_path, pixels):
        if logical_path not in self.png_pixels:
            self.png_pixels[logical_path] = pixels
            self.pixel_budget.add(pixels, logical_path, self.chain(logical_path))

    # ------------------------------------------------------------ 相位 B

    def check_budgets(self):
        """按 §11.2 R4 的表内顺序判定:节点数 → 文件数 → 字节 → 像素 → 全局光栅。

        深度不在这里:它在相位 A 的入口处判(§10.4 第 3 条要求"进入更深一层之前")。
        """
        for budget in (self.node_budget, self.file_budget, self.byte_budget,
                       self.pixel_budget, self.raster_budget):
            if budget.exceeded():
                raise PxlError("PXL011", budget.describe(), budget.chain)

    # ------------------------------------------------------------ 相位 A

    def load_graph(self, logical_path, entry, raw_bindings, typed=False, source_kinds=None):
        """解析并校验一个节点(递归)。**不写任何像素**,只统计尺寸与几何量。"""
        # §10.4 第 3 条:深度在进入更深一层之前判。64 个祖先即第 65 篇 -> PXL011。
        if len(self.stack) >= MAX_REF_DEPTH:
            raise self.error("PXL011", "PXL 引用深度 " + str(len(self.stack) + 1)
                             + " 超过 " + str(MAX_REF_DEPTH) + "(已进入 "
                             + str(len(self.stack)) + " 篇,拒绝再进入第 "
                             + str(len(self.stack) + 1) + " 篇;更深的层未被读取)", logical_path)
        if logical_path in self.stack:
            # 链要显式收口到闭环末端：chain() 只在末尾不同时追加（发布态复核 F7）。
            raise PxlError("PXL010", "循环引用: "
                           + chain_text(self.stack + [logical_path]),
                           self.stack + [logical_path])
        self.stack.append(logical_path)
        try:
            return self._load_node(logical_path, entry, raw_bindings, typed=typed,
                                  source_kinds=source_kinds)
        except PxlError as failure:
            raise self._with_chain(failure, self.stack)
        finally:
            self.stack.pop()

    def _load_node(self, logical_path, entry, raw_bindings, typed=False, source_kinds=None):
        status, data = self.resolver.fetch(logical_path)
        if status == MISSING:
            raise PxlError("PXL009", "依赖缺失: " + logical_path)
        if status == CASE_MISMATCH:
            raise PxlError("PXL008", "逻辑路径的实际拼写与声明不一致: " + logical_path)
        if status != OK:
            raise PxlError("PXL008", "依赖逃出 asset root: " + logical_path)
        self.add_file(logical_path, "pxl", data)
        # Type mismatch precedes the PXL document-size limit: a PNG declared as
        # PXL is PXL009 even when its byte payload is larger than 64 MiB.
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            raise self.error("PXL009", "声明为 pxl 的文档实际是 PNG: " + logical_path,
                             logical_path)
        # 单文档的**格式边界**(§5.0.2,64 MiB)是 PXL002,不是会话累计的 PXL011;
        # 而且必须排在 JSON 解析之前。
        if len(data) > MAX_DOCUMENT_BYTES:
            raise PxlError("PXL002", "文档 UTF-8 字节数 " + str(len(data)) + " 超过 64 MiB")
        if logical_path not in self.pxl_counted:
            self.pxl_counted.add(logical_path)
            self.byte_budget.add(len(data), logical_path, self.chain(logical_path))

        # 先序分配：进入节点就拿号，早于解析子文档——缓存判定靠这个顺序。
        global _ORDER_SEQ
        order = _ORDER_SEQ
        _ORDER_SEQ += 1
        root = jsonx.parse(jsonx.decode_utf8(data))
        if not isinstance(root, dict):
            raise PxlError("PXL001", "顶层必须是 JSON 对象")
        version = _dispatch_version(root)
        # v1 阶段 P 的内部顺序是「参数声明语法 → 参引语法 → bindings → 白名单外参引」
        # （PXL-v1 §11.2 行 3 的列举顺序）：声明外壳要先于参引扫描（发布态复核 E18）。
        if version == 1 and "params" in root and not isinstance(root["params"], dict):
            raise PxlError("PXL001", "params 必须是对象", [logical_path])
        # 参数阶段先于结构阶段：白名单外的参引必须先报 PXL012（E10）。
        if version == 1:
            paramlib.scan_references(root)
        # 显式 null 不等于「没写」：Java 侧一律 PXL001（发布态复核 F12）。
        # 但 assets 的 null 检查要排在整个参数阶段之后（E16：参引错误先报 PXL012）。
        if "params" in root and root["params"] is None:
            raise PxlError("PXL001", "params 必须是对象", [logical_path])
        params = {}
        asset_decls = {}
        bound = {}
        sprites = root.get("sprites") if isinstance(root.get("sprites"), dict) else {}
        if version == 1:
            params = paramlib.parse_params(root.get("params"))

            # 整个参数阶段（缺必需、未声明、类型、默认符号）都要先于 assets 结构检查：
            # 只提前某一种错误会被 parse_assets 抢先覆盖（发布态复核 E05/E06/E07）。
            raw_assets = root.get("assets") if isinstance(root.get("assets"), dict) else {}
            bound = paramlib.canonical_bindings(params, raw_bindings, sprites, raw_assets,
                                                source_kinds=source_kinds, typed=typed)
            paramlib.validate_symbol_defaults(params, raw_assets, sprites)
            # 参数替换也要在 assets 结构检查之前：替换阶段才发现的错误（未声明参引、
            # 参引类型不符）必须先报（发布态复核 E11/E12）。这里用**原始** assets 字典，
            # 结构合法性交给后面的 parse_assets。
            forwarded = {}
            concrete = paramlib.substitute(root, params, bound, sprites, raw_assets,
                                           forwarded=forwarded)
            if "assets" in root and root["assets"] is None:
                raise PxlError("PXL001", "assets 必须是对象", [logical_path])
            asset_decls = paramlib.parse_assets(root.get("assets"))
        node_key = (logical_path, paramlib.identity_key(bound))
        cached = self.nodes.get(node_key)
        # 缓存子树可能把引用链推过深度上限时不用缓存：重新走一遍即冷语义（C08）。
        if cached is not None and len(self.stack) + cached.height - 1 > MAX_REF_DEPTH:
            cached = None
        if cached is not None:
            # 子树里出现当前祖先 = 可能成环；此时不用缓存，重新走一遍，保证冷热同错误。
            for ancestor in self.stack[:-1]:
                if ancestor in cached.subtree:
                    cached = None
                    break
        if cached is not None:
            # 缓存命中不能跳过**当前上下文**的校验（发布态复核 F-R6-02）：缓存只说这棵子树
            # 解析过，不代表它接在现在这条引用链上仍合法。实测：命中点 len(self.stack) 已含
            # 当前节点，cached.height 也把自身算 1，故总深度 = stack + height - 1。
            # 冷遍历先撞到什么就报什么：把「回到祖先」与「某后代超过深度上限」都换算成
            # 冷遍历顺序号，序号小的先发生（只用最短祖先距离不够）。
            cycle_order = None
            cycle_index = -1
            for index in range(len(self.stack) - 1):
                offset = cached.min_depth.get(self.stack[index])
                order = cached.min_order.get(self.stack[index])
                if offset is not None and order is not None:
                    if cycle_order is None or order < cycle_order:
                        cycle_order = order
                        cycle_index = index
            depth_order = None
            depth_worst = 0
            depth_at = None
            # 前沿 = 最小的越界绝对深度（同深度再按顺序号取最早）：用「最早顺序号」会挑到
            # 共享叶在另一次访问里的深实例（发布态复核 F2）。
            for descendant, offset in cached.max_depth.items():
                absolute = len(self.stack) + offset
                if absolute <= MAX_REF_DEPTH:
                    continue
                order = cached.min_order.get(descendant)
                better = depth_worst == 0 or absolute < depth_worst
                if not better and absolute == depth_worst and order is not None \
                        and (depth_order is None or order < depth_order):
                    better = True
                if better:
                    depth_worst = absolute
                    depth_order = order if order is not None else None
                    depth_at = descendant
            if cycle_index >= 0 and (depth_order is None or cycle_order < depth_order):
                raise self.error("PXL010", "循环引用: " + self.stack[cycle_index], logical_path)
            if depth_order is not None:
                # 逐层展开缓存子树里的真实路径（只补终点会与冷路径不一致，发布态复核 P2）。
                if depth_at is None:
                    raise self.error("PXL011", "PXL 引用深度 " + str(depth_worst)
                                     + " 超过 " + str(MAX_REF_DEPTH)
                                     + "(经已缓存的子树 " + logical_path + ")", logical_path)
                # 从缓存根沿**真实父子边**下降到越界节点：每步选「max_depth 含目标且最深」
                # 的子节点，保证拼出来的是一条真实存在的引用边（发布态复核 R1）。
                hops = []
                cursor = cached
                guard = 0
                while cursor.logical_path != depth_at and guard < 4096:
                    guard += 1
                    step_node = None
                    best = None
                    for kid in cursor.children.values():
                        reach = kid.max_depth.get(depth_at)
                        if reach is not None and (best is None or reach > best):
                            best = reach
                            step_node = kid
                    if step_node is None:
                        break
                    hops.append(step_node.logical_path)
                    cursor = step_node
                walked = hops
                raise self.error("PXL011", "PXL 引用深度 " + str(depth_worst)
                                 + " 超过 " + str(MAX_REF_DEPTH)
                                 + "(经已缓存的子树 " + logical_path + ")",
                                 " -> ".join(walked))
            if cycle_index >= 0:
                raise self.error("PXL010", "循环引用: " + self.stack[cycle_index], logical_path)
            depth = len(self.stack) + cached.height - 1
            if depth > MAX_REF_DEPTH:
                raise self.error("PXL011", "PXL 引用深度 " + str(depth)
                                 + " 超过 " + str(MAX_REF_DEPTH)
                                 + "(经已缓存的子树 " + logical_path + ")", logical_path)
            # 同一渲染节点第二次被引用:不重解析、不重复计内部工作量(§10.3 第 1 条)。
            for ancestor in self.stack[:-1]:
                if ancestor in cached.subtree:
                    raise self.error("PXL010", "循环引用: " + ancestor, logical_path)
            return cached

        builder = Builder(concrete, len(data), version, asset_decls, {}, params)
        builder.stage3()
        builder.stage4()
        builder.stage5()
        builder.stage6()
        builder.stage7()
        builder.stage8()
        builder.stage9()
        # ---- 阶段 R1:每个 asset 声明的逻辑路径语法与规范化(与是否被选中无关)
        base_directory = logicalpath.directory_of(logical_path)
        normalized = {}
        for name in sorted(asset_decls):
            normalized[name] = logicalpath.normalize(asset_decls[name].path, base_directory)
        # ---- 阶段 R2:按 ASCII 升序解析**被选中**的依赖
        resolved = {}
        for name in _selected_assets(builder):
            decl = asset_decls[name]
            target = normalized[name]
            if decl.kind == "pxl":
                # 子文档参数取**父文档已绑定的强类型值**：父参引 "$c" 原样传下去会让子文档
                # 把它当成字面绑定值 -> PXL012，合法的参数转发因此无法表达（发布态复核 F04）。
                # 子文档参数取父文档已绑定的强类型值，并**保留类型**（§6.3：类型必须一致）。
                # 先转成字符串会让数字变成合法颜色、字符串变成合法整数（与 Java 侧同一处修正）。
                child_params = {}
                for key, value in dict(decl.params).items():
                    if isinstance(value, str) and value.startswith("$") and value[1:] in bound:
                        child_params[key] = bound[value[1:]]
                    else:
                        child_params[key] = value
                child = self.load_graph(target, False, child_params, typed=True,
                                        source_kinds=forwarded.get(name))
                asset = ResolvedAsset(name, "pxl", target)
                asset.node = child
                asset.width = child.doc.width
                asset.height = child.doc.height
                resolved[name] = asset
            else:
                image = self.png_cache.get(target)
                if image is None:
                    image = self._decode_png(target)
                    self.png_cache[target] = image
                asset = ResolvedAsset(name, "png", target)
                asset.width = image.width
                asset.height = image.height
                asset.rgba = image.rgba
                resolved[name] = asset
        builder.assets = resolved
        builder.recheck_raster()
        doc = builder.build()
        node = _Node(logical_path, doc, node_key, order)
        for asset in resolved.values():
            child_node = getattr(asset, "node", None)
            if child_node is not None:
                if 1 + child_node.height > node.height:
                    node.height = 1 + child_node.height
                node.subtree |= child_node.subtree
                # 键用渲染节点身份（路径 + bindings），否则 deep/short 两次绑定互相覆盖（F1）。
                node.children[child_node.key] = child_node
                for descendant, offset in child_node.min_depth.items():
                    candidate = offset + 1
                    if descendant not in node.min_depth or candidate < node.min_depth[descendant]:
                        node.min_depth[descendant] = candidate
                for descendant, order in child_node.min_order.items():
                    known_order = node.min_order.get(descendant)
                    if known_order is None or order < known_order:
                        node.min_order[descendant] = order
                for descendant, deep in child_node.max_depth.items():
                    joined = deep + 1
                    seen_deep = node.max_depth.get(descendant)
                    if seen_deep is None or joined > seen_deep:
                        node.max_depth[descendant] = joined
        self.nodes[node_key] = node
        self.node_budget.add(1, logical_path, self.chain(logical_path))
        # 会话全局光栅 = 本文档内部 op 的工作(每唯一节点一次) + asset stamp 的写入面积
        # (每次 stamp 都计,§10.3)。与 Java 侧口径逐句一致。
        self.raster_budget.add(doc.work + builder.asset_stamp_work(), logical_path,
                               self.chain(logical_path))
        return node

    def _decode_png(self, target):
        """读入并解码一个 PNG 叶节点;失败时把失败的 png 补进引用链(它不在引用栈上)。

        两个码的边界(规范 §11.1,两个实现必须同码):
          * 声明的 kind 与实际字节不符 —— 没有 PNG 签名(含短于 8 字节):PXL009;
          * 有签名但结构非法 —— v0 §8.5 的任何一条:PXL006。

        预算口径(§10.2 / §10.3):像素数在**读到 IHDR 时**就计入并即时判断,超限即**不再解码**
        后续 PNG —— 否则"累计 PNG 解码像素数"只约束了内存里的唯一文件数,不约束解码工作量
        (审查 R4-02)。判读仍在 check_budgets(相位 B),所以「依赖缺失」(PXL009)依旧优先。
        """
        status, payload = self.resolver.fetch(target)
        if status == MISSING:
            raise self.error("PXL009", "依赖缺失: " + target, target)
        if status == CASE_MISMATCH:
            raise self.error("PXL008", "逻辑路径的实际拼写与声明不一致: " + target, target)
        if status != OK:
            raise self.error("PXL008", "依赖逃出 asset root: " + target, target)
        if not pngdec.has_signature(payload):
            # §11.1:PXL009 含「声明类型与实际资源不符」。链由外层补(这里没有栈帧)。
            raise self.error("PXL009", "asset 声明为 png,但实际字节不是 PNG: " + target, target)
        self.add_file(target, "png", payload)
        try:
            width, height = pngdec.read_header(payload)
        except PxlError as failure:
            raise PxlError(failure.code, failure.message, self.chain(target))
        self.add_png_pixels(target, width * height)
        if self.pixel_budget.exceeded():
            # 已超预算:后面的 PNG 一律不再解码(仍继续解析,让 R2 的 PXL009/PXL008 优先)。
            # 光栅化只在预算通过后发生;若走到相位 C,rasterize 会以 RuntimeError 暴露不变量被破坏。
            return pngdec.Image(width, height, None)
        try:
            image = pngdec.decode(payload)
        except PxlError as failure:
            # PNG 不是节点,栈里没有它,所以在这里把"失败的 png"补进引用链(§9.3)。
            raise PxlError(failure.code, failure.message, self.chain(target))
        return image

    # ------------------------------------------------------------ 相位 C

    def rasterize(self, node):
        """光栅化:子节点先画,父节点再画;每个节点只画一次。"""
        if node.rgba is not None:
            return node.rgba
        for asset in node.doc.assets.values():
            if asset.node is not None:
                asset.rgba = self.rasterize(asset.node)
            elif asset.rgba is None:
                # 只有一种可能:像素预算已超,_decode_png 跳过了解码。check_budgets 必先失败,
                # 走到这里说明相位顺序被破坏 —— 这是实现不变量,不是 PXL 语义错。
                raise RuntimeError("asset 未解码就进入光栅化")
        node.rgba = node.doc.render_rgba()
        return node.rgba

    # ------------------------------------------------------------ 清单

    def manifest(self, entry_path):
        """§9.4:第一行是唯一入口,其余按 logical_path 的 ASCII 升序。"""
        items = []
        for path in sorted(self.files):
            role = "entry" if path == entry_path else "dependency"
            digest = hashlib.sha256(self.files[path]).hexdigest()
            items.append(Dependency(role, self.kinds[path], path, digest))
        entry = [item for item in items if item.role == "entry"]
        rest = [item for item in items if item.role != "entry"]
        return entry + rest


def _load_graph(entry, resolver, bindings=None):
    """相位 A:规范化入口逻辑路径并解析依赖图。不判预算,**一个像素都不写**。

    单独一层是为了让"只解析、不渲染"成为可复用的入口(`dependency_manifest`):批量
    入口要在任何写入之前收齐全批的依赖清单,而它拿不到、也不需要位图。
    """
    if not isinstance(entry, str) or not entry:
        raise PxlError("PXL008", "入口逻辑路径为空", ["<入口>"])
    try:
        entry_path = logicalpath.normalize(entry)
    except PxlError as failure:
        raise PxlError(failure.code, failure.message, [entry])
    session = Session(resolver)
    node = session.load_graph(entry_path, True, bindings or {})
    return session, node, entry_path


def dependency_manifest(entry, resolver, bindings=None):
    """只跑相位 A,返回依赖清单:``Compiled.dependencies`` 的同一份东西(§9.4)。

    批量入口需要它做**跨入口**的输出冲突检查:某个入口的输出可能落在**另一个**入口
    读过的素材上,而依赖清单只有解析完才知道。这里不判预算、不光栅化、不产 PNG,
    所以一次预扫不需要同时保留多份位图。
    """
    session, _node, entry_path = _load_graph(entry, resolver, bindings)
    return session.manifest(entry_path)


def best_effort_manifest(entry, resolver, bindings=None):
    """返回 ``(manifest, failure)``:解析失败时仍然给出**已经读到**的依赖。

    为什么失败也要给清单:批量入口拿这份清单做"输出会不会覆盖素材"的判据。一个入口
    读了 `sprite.png` 之后才因为缺别的依赖而失败,那份 `sprite.png` 仍然是**被读过、
    不能被覆盖**的文件。丢掉它,同批另一个入口的输出就会把素材改掉,而命令只是失败退出
    (审查 P1)。``failure`` 为 None 表示相位 A 成功。
    """
    session = Session(resolver)
    try:
        entry_path = logicalpath.normalize(entry) if isinstance(entry, str) and entry else None
    except PxlError as failure:
        return [], PxlError(failure.code, failure.message, [entry])
    if entry_path is None:
        return [], PxlError("PXL008", "入口逻辑路径为空", ["<入口>"])
    failure = None
    try:
        session.load_graph(entry_path, True, bindings or {})
    except PxlError as caught:
        failure = caught
    # 解析失败时 Session 里留下的是"读到哪算哪"的记账;manifest() 只遍历 self.files,
    # 因此对部分图同样成立(入口没读到时返回空清单)。
    return session.manifest(entry_path), failure


def compile_target(entry, resolver, bindings=None):
    """编译一个 target。entry 是逻辑路径;bindings 是 name -> JSON 值/字符串。"""
    # 相位 A:只解析与记账,一个像素都不写。
    session, node, entry_path = _load_graph(entry, resolver, bindings)
    # 相位 B:R4。依赖图已经固定,此刻"依赖缺失"该报的早就报了(§11.2 的 R2 先于 R4)。
    session.check_budgets()
    # 相位 C:预算通过,才允许写像素。
    rgba = session.rasterize(node)
    return Compiled(node.doc.width, node.doc.height, rgba, session.manifest(entry_path),
                    node.doc)


def conflicting_source(candidate, asset_root, dependencies):
    """输出目标是否落到**本次编译读过的某个源文件**上(PRD §13.2 CLI002)。

    依赖清单就是"这次读了哪些文件"的权威答案:入口与全部依赖都在里面。只挡入口是不够的
    ——把输出指向被引用的素材,那份素材会被静默改写成 PNG,而编译还报成功。

    比较按**真实路径**(Path.resolve):符号链接与宿主的大小写都归一。

    :return: 命中的逻辑路径;不冲突返回 None
    """
    if candidate is None or asset_root is None or dependencies is None:
        return None
    try:
        target = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return None
    for item in dependencies:
        try:
            source = (Path(asset_root) / item.logical_path).resolve()
        except (OSError, RuntimeError):
            continue
        if target == source:
            return item.logical_path
        # 路径相等只抓「同一个目录项」。硬链接是另一个目录项指向同一 inode，路径绝不相等，
        # 但写入会截断同一份内容——按文件身份再比一次（发布态复核 F-R6-05；Java 侧已有）。
        try:
            if target.exists() and source.exists() and os.path.samefile(target, source):
                return item.logical_path
        except OSError:
            pass
    return None


def _selected_assets(builder):
    """当前 target 实际选中的 asset 名(§9.1)。按 ASCII 升序返回。"""
    selected = set()
    for layer in builder.draft_layers:
        for op in layer.ops:
            if op.known and op.name == "stamp" and "asset" in op.raw:
                selected.add(op.raw["asset"])
    return sorted(selected)


def _dispatch_version(root):
    """v1 §2 的版本分派。返回 1；不支持的版本抛 PXL007,无法表示的抛 PXL001。"""
    if "format" not in root:
        raise PxlError("PXL001", "顶层缺少必需成员: format")
    if not isinstance(root["format"], str) or root["format"] != "PXL":
        raise PxlError("PXL001", "format 必须是字符串 PXL")
    if "version" not in root:
        raise PxlError("PXL001", "顶层缺少必需成员: version")
    node = root["version"]
    if not isinstance(node, JsonNumber):
        raise PxlError("PXL001", "version 必须是数字")
    value = numlit.integer_value(node.text)
    if value is None or value is numlit.HUGE or value < 0 or value > 65535:
        raise PxlError("PXL001", "version 不在分派域 0..65535 之内: " + node.text)
    if value not in SUPPORTED_VERSIONS:
        raise PxlError("PXL007", "本 compiler 不支持 version " + str(value))
    return value
