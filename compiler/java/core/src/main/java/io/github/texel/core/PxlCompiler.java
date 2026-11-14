package io.github.texel.core;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * PXL v1 编译入口：一个入口逻辑路径 + 一个 resolver → 一个位图 + 一份依赖清单。
 *
 * <p>这是 PRD §7 的「单目标编译模型」在 Java 侧的落点：不扫描目录、不决定输出路径、不读当前工作
 * 目录、不联网；所有外部输入都必须经 {@link AssetResolver} 进来。
 *
 * <p><b>三相位结构</b>（规范 §11.2 的阶段表要求 R2 先于 R4，而 R4 又必须先于任何光栅化）：
 *
 * <ol>
 *   <li><b>相位 A（{@link #loadGraph}）</b>：解析并校验整张依赖图，只统计**尺寸与几何量**
 *       （每份文档的宽高、每个 {@code stamp} 的裁剪面积），**一个像素都不写**；同时记录
 *       唯一路径 / 唯一文件 / 累计字节 / 渲染节点身份，但**只记账不判定**。</li>
 *   <li><b>相位 B（{@link #checkBudgets}）</b>：依赖图全部解析完之后，按 §11.2 R4 的表内顺序
 *       （深度 → 节点数 → 文件数 → 累计字节 → 累计像素 → 全局光栅）统一判预算。</li>
 *   <li><b>相位 C（{@link #rasterize}）</b>：预算通过之后才真正光栅化。</li>
 * </ol>
 *
 * <p>为什么要拆相位：预算若只在末尾判，判定之前的工作量没有任何上界——超预算的图会先被完整
 * 渲染出来（实测 125.86M 次像素写入才报 PXL011，是预算 2^24 的 7.5 倍；极端图约 1.7e10 次写入，
 * 可能先 OOM）；反过来，预算若插在 R2 期间判，就会出现「依赖缺失」被误报成「预算超限」
 * （规范 §11.2：R2 先于 R4）。两条都满足只有一个结构：**先只算数、后判数、最后才画**。
 *
 * <p>深度是唯一的例外：§10.4 第 3 条要求深度在**进入更深一层之前**判定（否则一份几 KB 的
 * 65 层链要先读完再报），因此它在相位 A 的入口处判，不随其余五项延后。
 */
public final class PxlCompiler {

    /** 规范 §10.2 的编译会话上界。 */
    static final int MAX_DEPTH = 64;
    static final int MAX_NODES = 4096;
    static final int MAX_FILES = 8192;
    static final long MAX_PXL_BYTES = 64L * 1024L * 1024L;
    static final long MAX_RASTER_WORK = 1L << 24;
    /** 累计 PNG 解码像素上限（规范 §10.2）。 */
    static final long MAX_PNG_PIXELS = 1L << 24;

    /** 冷遍历顺序号游标（构建顺序即 DFS 顺序），用于判「循环与超深谁先出现」。 */
    static int ORDER_SEQ = 0;

    private PxlCompiler() {
    }

    /**
     * 编译一个入口。
     *
     * @param entryLogicalPath 入口的逻辑路径（已由调用方规范化；见 {@link LogicalPath}）
     * @param resolver         素材解析器
     * @return 位图 + 依赖清单
     * @throws PxlFormatException {@code PXL001}..{@code PXL012}，带完整引用链
     */
    public static Compiled compile(String entryLogicalPath, AssetResolver resolver)
            throws PxlFormatException {
        return compile(entryLogicalPath, resolver, java.util.Collections.<String, String>emptyMap());
    }

    /**
     * 带 bindings 的编译（规范 v1 §6.3）。bindings 只影响**入口**文档的参数；子文档的参数由
     * asset 声明里的 {@code params} 映射显式给出。
     */
    public static Compiled compile(String entryLogicalPath, AssetResolver resolver,
            Map<String, String> bindings) throws PxlFormatException {
        if (entryLogicalPath == null) {
            throw new NullPointerException("entryLogicalPath");
        }
        if (resolver == null) {
            throw new NullPointerException("resolver");
        }
        Session session = new Session(resolver);
        // 相位 A：只解析与记账，一个像素都不写。
        Node root = session.loadGraph(entryLogicalPath, true, bindings);
        // 相位 B：R4。依赖图已经固定，此刻"依赖缺失"该报的早就报了（§11.2 的 R2 先于 R4）。
        session.checkBudgets();
        // 相位 C：预算通过，才允许写像素。
        byte[] rgba = session.rasterize(root);
        return new Compiled(root.doc.width, root.doc.height, rgba, session.manifest());
    }

    /** 编译产物：位图 + 依赖清单（规范 §9.4）。 */
    public static final class Compiled {

        private final int width;
        private final int height;
        private final byte[] rgba;
        private final List<Dependency> dependencies;

        Compiled(int width, int height, byte[] rgba, List<Dependency> dependencies) {
            this.width = width;
            this.height = height;
            this.rgba = rgba;
            this.dependencies = dependencies;
        }

        public int width() {
            return width;
        }

        public int height() {
            return height;
        }

        /** RGBA8 原始字节流（§13.4 口径）；每次调用返回同一数组的副本由调用方负责。 */
        public byte[] renderRgba() {
            // 不泄漏内部数组：此前返回的是同一份引用，
            // 调用方改一个字节就篡改了编译产物（发布态复核旧账 F）。
            return rgba.clone();
        }

        /** 依赖清单：第一项恒为入口，其余按逻辑路径 ASCII 升序（§9.4）。 */
        public List<Dependency> dependencies() {
            return Collections.unmodifiableList(dependencies);
        }
    }

    /** 依赖清单的一行（规范 §9.4）。 */
    public static final class Dependency {

        private final String role;
        private final String kind;
        private final String logicalPath;
        private final String sha256;

        Dependency(String role, String kind, String logicalPath, String sha256) {
            this.role = role;
            this.kind = kind;
            this.logicalPath = logicalPath;
            this.sha256 = sha256;
        }

        public String role() {
            return role;
        }

        public String kind() {
            return kind;
        }

        public String logicalPath() {
            return logicalPath;
        }

        /** 源文件**原始字节**的小写 SHA-256（保守失效口径，§9.4）。 */
        public String sha256() {
            return sha256;
        }

        /** 一行的 TSV 形式（列顺序即 §9.4 的 schema）。 */
        public String toTsv() {
            return role + "\t" + kind + "\t" + logicalPath + "\t" + sha256;
        }
    }

    /**
     * 相位 A 的一个引用节点：校验过的文档 + 已解析的 asset 表。
     *
     * <p>位图字段（{@link #rgba}）在相位 C 才填：相位 A 只关心宽高与几何量，分配 4×w×h 字节
     * 的缓冲正是审查里"预算之前就可能 OOM"的来源，因此那一步必须挪到预算之后。
     */
    private static final class Node {
        /** 这棵子树完整遍历过的后代逻辑路径：缓存命中时用来判「祖先是否已在其中」（见下）。 */
        final java.util.Set<String> subtree = new java.util.LinkedHashSet<String>();
        /** 后代逻辑路径 -> 从本节点出发的最小边距（自身 0）。缓存命中时用来还原
         * 「冷遍历会在第几层撞上这个祖先」，而不是拿整棵子树的高度去估。 */
        /** 后代逻辑路径 -> 冷遍历顺序号（越小越先被访问）。 */
        final java.util.Map<String, Integer> minOrder =
                new java.util.LinkedHashMap<String, Integer>();

        final java.util.Map<String, Integer> minDepth =
                new java.util.LinkedHashMap<String, Integer>();
        /** 后代路径 -> 从本节点出发的**最大**边距：判「是否存在超过深度上限的访问」。
         *  用最小距会漏掉「同一叶子还另有一条更早、更深的访问路径」（发布态复核 P2）。 */
        final java.util.Map<String, Integer> maxDepth =
                new java.util.LinkedHashMap<String, Integer>();
        /** 直接子节点（按逻辑路径）：重建引用链时按**真实边**下降，不能靠深度猜。 */
        final java.util.Map<String, Node> children =
                new java.util.LinkedHashMap<String, Node>();
        /**
         * 这棵子树的高度（自身算 1）。缓存命中时用它把「缓存子树内部的深度」算进当前引用链——
         * 否则先缓存一段短支路、再从深层复用它，总深度就会被低估。
         *
         * <p>为什么不也缓存成员集合用于循环检查：{@code subtree} 是完整遍历过的后代集合，
         * 若某祖先落在其中，说明首次遍历时就已经存在回边、当场抛过 PXL010，缓存命中再见不到循环。
         * 保留一份永远不触发的集合只会白占内存并让人误以为它有判据。
         */
        int height = 1;

        final String path;
        /** 渲染节点身份 = 规范化路径 + 规范 bindings（§9.2）。 */
        final String key;
        final Doc doc;
        final Map<String, Ref> refs = new LinkedHashMap<String, Ref>();
        byte[] rgba;

        Node(String path, String key, Doc doc) {
            this.path = path;
            this.key = key;
            this.doc = doc;
        }
    }

    /** 一个已解析的 asset 引用：pxl 子节点，或已解码的 PNG。 */
    private static final class Ref {

        final Node node;
        final byte[] pngRgba;
        final int width;
        final int height;

        Ref(Node node, byte[] pngRgba, int width, int height) {
            this.node = node;
            this.pngRgba = pngRgba;
            this.width = width;
            this.height = height;
        }
    }

    /**
     * 一项会话预算：**只累计，不判定**。
     *
     * <p>判定统一放在相位 B（§11.2 R4）。记账时顺手记住"第一个把计数推过上限的源文件"以及
     * 那一刻的引用链，这样 PXL011 的消息能带上 §9.3 要求的失败文件与完整链，而不是一句干巴巴的
     * "超过 8192"。
     */
    private static final class Budget {

        private final String label;
        private final long limit;
        private long value;
        private String failingPath;
        private String failingChain;

        Budget(String label, long limit) {
            this.label = label;
            this.limit = limit;
        }

        void add(long amount, String where, String chain) {
            value += amount;
            if (failingPath == null && value > limit) {
                failingPath = where;
                failingChain = chain;
            }
        }

        boolean exceeded() {
            return value > limit;
        }

        String describe() {
            return label + " " + value + " 超过上限 " + limit
                    + "（越过上限的源文件: " + failingPath + "；引用链: " + failingChain + "）";
        }
    }

    /** 一次编译的会话状态：引用栈、依赖图、预算计数、清单。 */
    private static final class Session {

        private final AssetResolver resolver;
        private final List<String> stack = new ArrayList<String>();
        /** 渲染节点身份 → 节点。既是去重表也是"内部 op 工作只计一次"的依据（§10.3）。 */
        private final Map<String, Node> nodes = new LinkedHashMap<String, Node>();
        private final Map<String, Dependency> manifest = new LinkedHashMap<String, Dependency>();
        /** 唯一实际源文件（§10.2 的计数口径）。 */
        private final Set<String> files = new LinkedHashSet<String>();
        /** 已计过累计字节的 PXL 文件：同一文件读两次只算一次（§10.2「按唯一文件」）。 */
        private final Set<String> pxlCounted = new LinkedHashSet<String>();
        /**
         * 逻辑路径 -> 已解码的 PNG。
         *
         * <p>§10.2 按**唯一文件**计像素，隐含"内存也按唯一文件持有一份"：同一个 PNG 被 N 个
         * 节点引用时，没有这张表就是 N 份 4·(w·h) 的 rgba —— 一份预算全合法的图（60 个节点
         * × 同一张 2048²）要 1.3 GB 堆，-Xmx1g 直接 OOM。缓存命中同时也就完成了像素预算的
         * "唯一文件"口径，所以这里不再单设一个 pngCounted 集合。
         */
        private final Map<String, Ref> pngCache = new LinkedHashMap<String, Ref>();

        private final Budget nodeBudget = new Budget("唯一渲染节点数", MAX_NODES);
        private final Budget fileBudget = new Budget("唯一实际源文件数", MAX_FILES);
        private final Budget byteBudget = new Budget("累计 PXL UTF-8 字节数", MAX_PXL_BYTES);
        private final Budget pixelBudget = new Budget("累计 PNG 解码像素数", MAX_PNG_PIXELS);
        private final Budget rasterBudget = new Budget("全局光栅工作", MAX_RASTER_WORK);

        Session(AssetResolver resolver) {
            this.resolver = resolver;
        }

        // ------------------------------------------------------------ 相位 A

        /**
         * 解析并校验一个 PXL 节点（递归）。**不写任何像素**，只统计尺寸与几何量。
         */
        Node loadGraph(String logicalPath, boolean entry, Map<String, String> bindings)
                throws PxlFormatException {
            return loadGraph(logicalPath, entry, bindings, null);
        }

        Node loadGraph(String logicalPath, boolean entry, Map<String, String> bindings,
                Map<String, Json.Value> typedBindings) throws PxlFormatException {
            return loadGraph(logicalPath, entry, bindings, typedBindings, null);
        }

        Node loadGraph(String logicalPath, boolean entry, Map<String, String> bindings,
                Map<String, Json.Value> typedBindings, Map<String, String> sourceKinds)
                throws PxlFormatException {
            if (stack.size() >= MAX_DEPTH) {
                // 印**实际进到第几篇**（= 祖先数 + 1），与 Python 侧的措辞口径一致；
                // 只写"超过 64"会让 66 层的链与 65 层的链报出同一句话。
                // 消息里**不写**引用链：链由下面 catch 的 withChain 统一追加一次，
                // 否则同一句话会出现两段「引用链」（内联段 + 传播段），且两段尾端还不一样。
                // 链必须收口到被拒绝的那一篇：否则引用链停在父节点，与 Python 不一致
                // （发布态复核 F20：缓存根/父节点截断）。
                throw withChain(fail(Codes.PXL011, "PXL 引用深度 " + (stack.size() + 1)
                        + " 超过 " + MAX_DEPTH + "（已进入 " + stack.size() + " 篇，拒绝再进入第 "
                        + (stack.size() + 1) + " 篇；更深的层未被读取）"), logicalPath);
            }
            // §10.4 第 3 条：深度在进入更深一层之前判，不随其余预算延后。
            if (stack.contains(logicalPath)) {
                // 闭环末端要出现在链里（Python 侧是 a -> b -> a）。但 chainOf 在更深的环上已经
                // 含了末端，所以只在缺的时候补一次（发布态复核 F20）。
                String cycleChain = chainOf(logicalPath);
                // 直接自环（栈顶就是自己）时 chainOf 不会再补一次，闭环末端就丢了（B3）。
                boolean selfLoop = !stack.isEmpty()
                        && stack.get(stack.size() - 1).equals(logicalPath);
                if (selfLoop || !cycleChain.endsWith(" -> " + logicalPath)) {
                    cycleChain = cycleChain + " -> " + logicalPath;
                }
                // 链也要收口：否则引用链停在父节点，缺掉回到自己的那一环（发布态复核 F20）。
                // 结构化链也要用**收口后**的文本，否则 引用链: 字段仍然少一跳（B3）。
                throw fail(Codes.PXL010, "循环引用: " + cycleChain).withChain(cycleChain);
            }
            stack.add(logicalPath);
            try {
                return loadNode(logicalPath, entry, bindings, typedBindings, sourceKinds);
            } catch (PxlFormatException failure) {
                // §9.3：被引用文档**原样传播上来的码**（PXL001..PXL012 都算）也要带
                // 「入口 -> … -> 失败文件」。首见即定型：内层已经补过就原样抛。
                throw withChain(failure, logicalPath);
            } finally {
                stack.remove(stack.size() - 1);
            }
        }

        /** 真正加载一个节点：读字节、解析、校验、解析 asset。包装与引用栈由调用方负责。 */
        /** PNG 签名：分辨「声明 pxl、实际是 PNG」这种类型不符。 */
        private static boolean looksLikePng(byte[] raw) {
            byte[] signature = {(byte) 0x89, (byte) 0x50, (byte) 0x4E, (byte) 0x47,
                (byte) 0x0D, (byte) 0x0A, (byte) 0x1A, (byte) 0x0A};
            if (raw == null || raw.length < signature.length) {
                return false;
            }
            for (int index = 0; index < signature.length; index++) {
                if (raw[index] != signature[index]) {
                    return false;
                }
            }
            return true;
        }
        private Node loadNode(String logicalPath, boolean entry, Map<String, String> bindings,
                Map<String, Json.Value> typedBindings, Map<String, String> sourceKinds)
                throws PxlFormatException {
            // asset 名 -> 参数名 -> 来源参数的声明类型；由 substitute 填，供子节点绑定校验。
            Map<String, Map<String, String>> forwarded =
                    new LinkedHashMap<String, Map<String, String>>();
            byte[] raw = read(logicalPath, "pxl", entry ? "entry" : "dependency");
            // 声明 kind=pxl、实际字节却是 PNG：类型不符（PXL009）先于 PXL 文档大小边界。
            // 否则一个超大的 PNG 会被错误地当作超大 PXL（发布态复核 T01）。
            if (looksLikePng(raw)) {
                throw fail(Codes.PXL009, "声明为 pxl 的文档实际是 PNG: " + logicalPath);
            }
            // 单文档的**格式边界**（§5.0.2，64 MiB）是 PXL002，不是会话累计的 PXL011；
            // 而且必须排在 JSON 解析之前，否则 64 MiB 的文档会在解析期先把内存吃光。
            if (raw.length > Validator.MAX_DOC_BYTES) {
                throw fail(Codes.PXL002, "文档 UTF-8 字节数 " + raw.length + " 超过 "
                        + Validator.maxDocBytesText());
            }
            if (pxlCounted.add(logicalPath)) {
                byteBudget.add(raw.length, logicalPath, chainOf(logicalPath));
            }

            Json.Value root = Json.parse(raw);
        if (!root.isObject()) {
            // 顶层不是对象：此前会在这里 ClassCastException / NPE（发布态复核旧账 D）。
            throw fail(Codes.PXL001, "顶层必须是 JSON 对象");
        }
        Json.Value formatValue = root.members.get("format");
        if (formatValue == null || !formatValue.isString() || !"PXL".equals(formatValue.text)) {
            // §11.2：format 先于 version 判定。此前 format 错 + 版本不支持时先报 PXL007，
            // 而 Python 一直是 PXL001（发布态复核 F06 残余）。
            throw fail(Codes.PXL001, "format 必须是字符串 PXL");
        }
            int version = versionOf(root);
            Doc doc;
            Map<String, Json.Value> canonical = Collections.<String, Json.Value>emptyMap();
            if (version == 1) {
                // 阶段 P：声明 → bindings → 替换。替换后的**具体文档**才进入 v0 阶段 3 起的校验。
                Map<String, Params.Decl> declarations = Params.parse(root);
        Params.scanReferences(root);
        Params.validateSymbolDefaults(declarations, root.members.get("assets"),
                root.members.get("sprites"));
                canonical = typedBindings != null
                    ? Params.bindTyped(declarations, typedBindings, sourceKinds)
                    : Params.bind(declarations, bindings);
                Params.validateSymbolValues(canonical, declarations,
                        root.members.get("assets"), root.members.get("sprites"));
                // 声明数上限属于文档级数值域（阶段表第 7 行）：要在 encoding（第 5 行）之后、
                // op 名（第 9 行）之前报。encoding 非法时交给 Validator 报 PXL003；否则这里先报
                // PXL002，避免被阶段 9 的 PXL004 抢先（发布态复核 F2）。
                if (validEncoding(root)) {
                    Params.validateDeclarationCounts(root);
                }
                Json.Value concrete = Params.substitute(root, canonical, declarations, forwarded);
                doc = Validator.validate(raw, concrete, true, logicalPath);
            } else {
                throw fail(Codes.PXL007, "不支持的文档版本 " + version + ": " + logicalPath);
            }

            String nodeKey = logicalPath + "|" + canonicalKey(canonical);
            Node cached = nodes.get(nodeKey);
            // 缓存子树可能把引用链推过深度上限时**不使用缓存**：聚合量表示不了「哪一次
            // 访问」，热路径会挑错前沿（发布态复核 C08）。重新走一遍即冷语义。
            if (cached != null && stack.size() + cached.height - 1 > MAX_DEPTH) {
                cached = null;
            }
            if (cached != null) {
                // 子树里出现当前祖先 = 这里可能形成环。此时**不用缓存**，重新走一遍：
                // 缓存的 min/max 聚合无论怎么记都还原不了「当前这条边的访问顺序」，
                // 而冷热必须给出同一个错误（发布态复核 P2，cycle-first 夹具）。
                for (int index = 0; index < stack.size() - 1; index++) {
                    if (cached.subtree.contains(stack.get(index))) {
                        cached = null;
                        break;
                    }
                }
            }
            if (cached != null) {
                // 同一渲染节点第二次被引用：不重解析、不重复计内部工作量（§10.3 第 1 条）。
                // 但**当前上下文**的校验不能省：缓存只说"这棵子树解析过"，不代表它放在现在
                // 这条引用链上仍合法——深度是相对入口算的，循环是相对路径算的。
                // 缓存命中的循环检查：§6.2 允许 asset 参数替换 stamp.asset，于是「同一路径的边集」
                // 会随 bindings 变化——一条边被缓存的旧实例截断时，新实例的后代仍可能接回当前祖先。
                // stack 里已经包含当前节点本身，而 height 也把自身算作 1——直接相加会重复计一层，
                // 于是"恰好 64 篇"的合法链被误拒成 65。
                // 冷遍历先撞到什么，就报什么：把「回到某个祖先」与「某个后代超过深度上限」
                // 两件事都换算成**冷遍历顺序号**，序号小的先发生。
                // （只用最短祖先距离不够——发布态复核：先超深的分支在后访问时会被误报成循环。）
                int cycleOrder = Integer.MAX_VALUE;
                int cycleAncestor = -1;
                for (int index = 0; index < stack.size() - 1; index++) {
                    Integer offset = cached.minDepth.get(stack.get(index));
                    Integer order = cached.minOrder.get(stack.get(index));
                    if (offset != null && order != null && order.intValue() < cycleOrder) {
                        cycleOrder = order.intValue();
                        cycleAncestor = index;
                    }
                }
                int depthOrder = Integer.MAX_VALUE;
                int depthWorst = 0;
                String depthAt = null;
                // 前沿 = **最小的越界绝对深度**（同深度再按顺序号取最早）。
                // 用「最早顺序号」会挑到共享叶在另一次访问里的深实例（发布态复核 F2）。
                for (java.util.Map.Entry<String, Integer> probeEntry : cached.maxDepth.entrySet()) {
                    int absolute = stack.size() + probeEntry.getValue().intValue();
                    if (absolute <= MAX_DEPTH) {
                        continue;
                    }
                    Integer order = cached.minOrder.get(probeEntry.getKey());
                    boolean better = absolute < depthWorst || depthWorst == 0;
                    if (!better && absolute == depthWorst && order != null
                            && order.intValue() < depthOrder) {
                        better = true;
                    }
                    if (better) {
                        depthWorst = absolute;
                        depthOrder = order == null ? Integer.MAX_VALUE : order.intValue();
                        depthAt = probeEntry.getKey();
                    }
                }
                if (cycleAncestor >= 0 && cycleOrder < depthOrder) {
                    throw fail(Codes.PXL010, "循环引用: " + chainOf(logicalPath) + " -> "
                            + stack.get(cycleAncestor));
                }
                if (depthOrder != Integer.MAX_VALUE) {
                    // 链要继续走进缓存子树，并且逐层展开：只补终点会与冷路径不一致
                    // （发布态复核 P2）。
                    PxlFormatException tooDeep = fail(Codes.PXL011, "PXL 引用深度 " + depthWorst
                            + " 超过 " + MAX_DEPTH + "（经已缓存的子树 " + logicalPath + "）");
                    if (depthAt == null) {
                        throw tooDeep;
                    }
                    java.util.Map<Integer, String> atOffset = new java.util.TreeMap<Integer, String>();
                    for (java.util.Map.Entry<String, Integer> step : cached.maxDepth.entrySet()) {
                        int offset = step.getValue().intValue();
                        String known = atOffset.get(Integer.valueOf(offset));
                        if (known == null || step.getKey().compareTo(known) < 0) {
                            atOffset.put(Integer.valueOf(offset), step.getKey());
                        }
                    }
                    // 从缓存根沿**真实父子边**下降到越界节点：每步选「maxDepth 含目标且最深」
                    // 的子节点，保证拼出来的是一条真实存在的引用边（发布态复核 R1）。
                    java.util.List<String> hops = new java.util.ArrayList<String>();
                    Node cursor = cached;
                    String target = depthAt;
                    int guard = 0;
                    while (!cursor.path.equals(target) && guard++ < 4096) {
                        Node step = null;
                        Integer best = null;
                        for (Node kid : cursor.children.values()) {
                            Integer reach = kid.maxDepth.get(target);
                            if (reach != null && (best == null || reach.intValue() > best.intValue())) {
                                best = reach;
                                step = kid;
                            }
                        }
                        if (step == null) {
                            break;
                        }
                        hops.add(step.path);
                        cursor = step;
                    }
                    StringBuilder walked = new StringBuilder();
                    for (String hop : hops) {
                        if (walked.length() > 0) {
                            walked.append(" -> ");
                        }
                        walked.append(hop);
                    }                    throw withChain(tooDeep, walked.toString());
                }
                if (cycleAncestor >= 0) {
                    throw fail(Codes.PXL010, "循环引用: " + chainOf(logicalPath) + " -> "
                            + stack.get(cycleAncestor));
                }                int depth = stack.size() + cached.height - 1;
                if (depth > MAX_DEPTH) {
                    throw fail(Codes.PXL011, "PXL 引用深度 " + depth
                            + " 超过 " + MAX_DEPTH + "（经已缓存的子树 " + logicalPath + "）");
                }
                for (int index = 0; index < stack.size() - 1; index++) {
                    String ancestor = stack.get(index);
                    if (cached.subtree.contains(ancestor)) {
                        throw fail(Codes.PXL010, "循环引用: " + chainOf(logicalPath) + " -> "
                                + ancestor);
                    }
                }
                return cached;
            }
            Node node = new Node(logicalPath, nodeKey, doc);
            node.subtree.add(logicalPath);
            node.minDepth.put(logicalPath, Integer.valueOf(0));
            node.maxDepth.put(logicalPath, Integer.valueOf(0));
            node.minOrder.put(logicalPath, Integer.valueOf(ORDER_SEQ++));
            // 自身也要进 subtree：否则 child.subtree 的 addAll 只会在叶子处累积空集，
            // 所有节点的成员集合永远是空的，缓存命中的循环检查就等于没写。
            nodes.put(nodeKey, node);
            nodeBudget.add(1L, logicalPath, chainOf(logicalPath));
            rasterBudget.add(internalRasterWork(doc), logicalPath, chainOf(logicalPath));

            resolveAssets(node, forwarded);
            return node;
        }

        /** 给一个传播上来的失败补链（首见即定型）。 */
        private PxlFormatException withChain(PxlFormatException failure, String failing) {
            if (failure.hasChain()) {
                return failure;
            }
            return failure.withChain(chainOf(failing));
        }

        /** 解析本文档实际选中的 asset（§9.1），只记尺寸与几何量。 */
        private void resolveAssets(Node node, Map<String, Map<String, String>> forwarded)
                throws PxlFormatException {
            Doc doc = node.doc;
            // §9.3：按 asset 名的 ASCII 升序访问，而不是 op 在文档里的先后——否则"第一个失败"
            // 取决于作者把哪条 op 写在前，两套实现的错误码就会分叉。
            List<Doc.Op> ordered = new ArrayList<Doc.Op>();
            for (Doc.Layer layer : doc.layers) {
                for (Doc.Op op : layer.ops) {
                    if (op.asset != null) {
                        ordered.add(op);
                    }
                }
            }
            Collections.sort(ordered, new java.util.Comparator<Doc.Op>() {
                public int compare(Doc.Op left, Doc.Op right) {
                    return left.asset.compareTo(right.asset);
                }
            });
            for (Doc.Op op : ordered) {
                Ref ref = node.refs.get(op.asset);
                if (ref == null) {
                    Doc.Asset decl = doc.assets.get(op.asset);
                    if (decl == null) {
                        throw fail(Codes.PXL005, "stamp 引用了未声明的 asset: " + op.asset);
                    }
                    // R1 已经把声明路径解析并规范化成"相对 asset root 的唯一名称"
                    // （含相对声明文档目录的解析，见 Validator.normalizeAssetPaths），
                    // 这里**不能**再 resolve 一次——那会把文档目录前缀加两遍。
                    String childPath = decl.path;
                    if ("png".equals(decl.kind)) {
                        ref = pngCache.get(childPath);
                        if (ref == null) {
                            ref = loadPng(childPath);
                            pngCache.put(childPath, ref);
                        }
                    } else {
                        Node child = loadGraph(childPath, false,
                        new java.util.LinkedHashMap<String, String>(),
                        childTypedBindings(decl.params),
                        forwarded == null ? null : forwarded.get(op.asset));
                node.subtree.addAll(child.subtree);
                // 键必须是**渲染节点身份**（路径 + 规范化 bindings）：同一文件的 deep/short 两次
                // 绑定是两个节点，只用路径会让后者覆盖前者（发布态复核 F1）。
                node.children.put(child.key, child);
                for (java.util.Map.Entry<String, Integer> e : child.minDepth.entrySet()) {
                    Integer candidate = Integer.valueOf(e.getValue().intValue() + 1);
                    Integer known = node.minDepth.get(e.getKey());
                    if (known == null || candidate.intValue() < known.intValue()) {
                        node.minDepth.put(e.getKey(), candidate);
                    }
                }
                for (java.util.Map.Entry<String, Integer> deepEntry : child.maxDepth.entrySet()) {
                    Integer joined = Integer.valueOf(deepEntry.getValue().intValue() + 1);
                    Integer seen = node.maxDepth.get(deepEntry.getKey());
                    if (seen == null || joined.intValue() > seen.intValue()) {
                        node.maxDepth.put(deepEntry.getKey(), joined);
                    }
                }
                for (java.util.Map.Entry<String, Integer> o : child.minOrder.entrySet()) {
                    Integer knownOrder = node.minOrder.get(o.getKey());
                    if (knownOrder == null || o.getValue().intValue() < knownOrder.intValue()) {
                        node.minOrder.put(o.getKey(), o.getValue());
                    }
                }
                if (1 + child.height > node.height) {
                    node.height = 1 + child.height;
                }
                        ref = new Ref(child, null, child.doc.width, child.doc.height);
                    }
                    node.refs.put(op.asset, ref);
                }
                // §10.3：父文档里的每个 asset stamp 按**实际裁剪后**的写入面积计一次；
                // 同一依赖被多次 stamp 时每次都计（缓存命中不减免语义预算）。
                rasterBudget.add(clipArea(doc.width, doc.height, op.x, op.y, ref.width, ref.height),
                        node.path, chainOf(node.path));
            }
        }

        /**
         * 读入并解码一个 PNG 叶节点；失败时把失败的 png 补进引用链（它不在引用栈上）。
         *
         * <p>两个码的边界（规范 §11.1，两个实现必须同码）：
         * <ul>
         *   <li><b>声明的 kind 与实际字节不符</b>——没有 PNG 签名（含短于 8 字节）：{@code PXL009}；</li>
         *   <li><b>有签名但结构非法</b>——v0 §8.5 的任何一条：{@code PXL006}（与 Importer 同一 decoder）。</li>
         * </ul>
         *
         * <p>预算口径（§10.2 / §10.3）：像素数在**读到 IHDR 时**就计入并即时判断，超限即**不再
         * 解码**后续 PNG——否则"累计 PNG 解码像素数"只约束了内存里的唯一文件数，不约束解码工作量
         * （审查 R4-02：32 张 2048² 会全部解完才报错，4096 张先 OOM）。判读仍在相位 B（§11.2
         * R4），所以「依赖缺失」（PXL009）依旧优先于本预算。
         */
        private Ref loadPng(String childPath) throws PxlFormatException {
            try {
                byte[] png = read(childPath, "png", "dependency");
                if (!PngReader.hasSignature(png)) {
                    // §11.1：PXL009 含「声明类型与实际资源不符」。链由外层 withChain 补。
                    throw fail(Codes.PXL009, "asset 声明为 png，但实际字节不是 PNG: " + childPath);
                }
                PngReader.Header header = PngReader.readHeader(png);
                pixelBudget.add((long) header.width * (long) header.height, childPath,
                        chainOf(childPath));
                if (pixelBudget.exceeded()) {
                    // 已超预算:后面的 PNG 一律不再解码(仍继续解析,让 R2 的 PXL009/PXL008 优先)。
                    // 光栅化只在相位 B 通过后才发生,所以这里不需要像素;
                    // 万一走到相位 C,attachAssets 会以 IllegalStateException 暴露这条不变量被破坏。
                    return new Ref(null, null, header.width, header.height);
                }
                PngReader.Decoded decoded = PngReader.decode(png);
                return new Ref(null, decoded.rgba, decoded.width, decoded.height);
            } catch (PxlFormatException failure) {
                throw withChain(failure, childPath);
            }
        }

        /** 把 asset 声明里的 params 映射转成子文档的 bindings（值已由上一级替换成常量）。 */
        /** 把 asset 声明里的 params 映射成子文档的强类型绑定（值已由上一级替换成常量）。 */
        private Map<String, Json.Value> childTypedBindings(Json.Value params)
                throws PxlFormatException {
            Map<String, Json.Value> out = new LinkedHashMap<String, Json.Value>();
            if (params == null) {
                return out;
            }
            for (Map.Entry<String, Json.Value> item : params.members.entrySet()) {
                // 保留 JSON 类型：子文档参数按强类型校验（§6.3）。
                out.put(item.getKey(), item.getValue());
            }
            return out;
        }

        /** 规范 bindings 的可判定字符串形式（身份用；名字升序、值取规范字面量）。 */
        /** 参数值的身份文本：整数的身份是数学值，所以 -0 必须与 0 相同（§6.4）。 */
        private String identityText(Json.Value value) {
            return Params.identity(value);
        }

        private String canonicalKey(Map<String, Json.Value> canonical) {
            // 顺序必须**显式**排。`canonical` 现在是按 ASCII 升序填的 LinkedHashMap，所以
            // "不排序也恰好对"；但节点身份串不该依赖"上游恰好按序填"这种隐含约定——契约 2
            // 明确禁止依赖哈希迭代顺序，把这里的正确性外包给别处就是下一次漂移的入口。
            List<String> names = new ArrayList<String>(canonical.keySet());
            Collections.sort(names);
            StringBuilder out = new StringBuilder();
            for (String name : names) {
                if (out.length() > 0) {
                    out.append(',');
                }
                out.append(name).append('=').append(identityText(canonical.get(name)));
            }
            return out.toString();
        }

        // ------------------------------------------------------------ 相位 B

        /**
         * 会话预算（§11.2 阶段 R4）。顺序即表内顺序：深度（已在相位 A 判）→ 节点数 → 文件数 →
         * 累计字节 → 累计像素 → 全局光栅。每一项都在**依赖图固定之后**才判，所以「依赖缺失」
         * （PXL009）不会被误报成「预算超限」（PXL011）。
         */
        void checkBudgets() throws PxlFormatException {
            check(nodeBudget);
            check(fileBudget);
            check(byteBudget);
            check(pixelBudget);
            check(rasterBudget);
        }

        private static void check(Budget budget) throws PxlFormatException {
            if (budget.exceeded()) {
                throw fail(Codes.PXL011, budget.describe());
            }
        }

        // ------------------------------------------------------------ 相位 C

        /** 光栅化：预算通过之后才走这里。子节点先画，父节点再画。 */
        byte[] rasterize(Node node) {
            if (node.rgba != null) {
                return node.rgba;
            }
            for (Ref ref : node.refs.values()) {
                if (ref.node != null) {
                    rasterize(ref.node);
                }
            }
            attachAssets(node);
            node.rgba = Raster.render(node.doc);
            return node.rgba;
        }

        /** 把已解析的 asset 位图挂到 op 上（Raster 只认 op.assetRgba）。 */
        private void attachAssets(Node node) {
            for (Doc.Layer layer : node.doc.layers) {
                for (Doc.Op op : layer.ops) {
                    if (op.asset == null) {
                        continue;
                    }
                    Ref ref = node.refs.get(op.asset);
                    if (ref == null) {
                        throw new IllegalStateException("asset 未解析: " + op.asset);
                    }
                    byte[] pixels = ref.node != null ? ref.node.rgba : ref.pngRgba;
                    if (pixels == null) {
                        // 只有一种可能:像素预算已超,loadPng 跳过了解码。相位 B 必先失败,
                        // 所以走到这里说明相位顺序被破坏——这是实现不变量,不是 PXL 语义错。
                        throw new IllegalStateException("asset 未解码就进入光栅化: " + op.asset);
                    }
                    op.assetRgba = pixels;
                    op.assetWidth = ref.width;
                    op.assetHeight = ref.height;
                }
            }
        }

        // ------------------------------------------------------------ 读取与清单

        private byte[] read(String logicalPath, String kind, String role) throws PxlFormatException {
            AssetResolver.Result result = resolver.resolve(logicalPath, kind);
            AssetResolver.Status status = result.status();
            if (status == AssetResolver.Status.OK) {
                byte[] content = result.content();
                // §10.2 的口径是**唯一实际源文件数**，不是读取次数：同一个文件被多个 asset 名
                // 指向、被多个节点引用时只算一次。Set.add 的返回值正好就是"首次见到"。
                if (files.add(logicalPath)) {
                    fileBudget.add(1L, logicalPath, chainOf(logicalPath));
                }
                recordFile(role, kind, logicalPath, content);
                return content;
            }
            if (status == AssetResolver.Status.TOO_LARGE) {
                // resolver 在读盘前只能确认文件超限，但文件头足以判定基本类型。
                if (("png".equals(kind) && !result.pngSignature())
                        || ("pxl".equals(kind) && result.pngSignature())) {
                    throw fail(Codes.PXL009, "声明类型与实际资源不符: " + logicalPath);
                }
                throw fail(Codes.PXL002, "文档超过 " + Validator.maxDocBytesText()
                        + " 字节上限: " + logicalPath);
            }
            if (status == AssetResolver.Status.MISSING) {
                // 链由 loadGraph / resolveAssets 的统一包装点补（§9.3），这里不内联拼。
                throw fail(Codes.PXL009, "依赖不存在: " + logicalPath);
            }
            throw fail(Codes.PXL008, "依赖路径不符合逻辑路径的实际拼写: " + logicalPath);
        }

        private void recordFile(String role, String kind, String logicalPath, byte[] raw) {
            if (manifest.containsKey(logicalPath)) {
                return;
            }
            manifest.put(logicalPath, new Dependency(role, kind, logicalPath, sha256(raw)));
        }

        List<Dependency> manifest() {
            List<Dependency> out = new ArrayList<Dependency>();
            Dependency entry = null;
            List<String> rest = new ArrayList<String>();
            for (Map.Entry<String, Dependency> item : manifest.entrySet()) {
                if ("entry".equals(item.getValue().role())) {
                    entry = item.getValue();
                } else {
                    rest.add(item.getKey());
                }
            }
            if (entry == null) {
                throw new IllegalStateException("清单缺少入口行");
            }
            out.add(entry);
            Collections.sort(rest);
            for (String key : rest) {
                out.add(manifest.get(key));
            }
            return out;
        }

        /**
         * 引用链文案：祖先栈 + 失败者。
         *
         * <p>失败者可能**已经在栈里**（预算是按节点记账的，记账发生在该节点解析期间），此时不
         * 重复追加，否则会印出 "entry.pxl -> entry.pxl" 这种自相矛盾的链。
         */
        private String chainOf(String failing) {
            StringBuilder out = new StringBuilder();
            for (String step : stack) {
                if (out.length() > 0) {
                    out.append(" -> ");
                }
                out.append(step);
            }
            boolean stacked = !stack.isEmpty() && stack.get(stack.size() - 1).equals(failing);
            if (!stacked) {
                if (out.length() > 0) {
                    out.append(" -> ");
                }
                out.append(failing);
            }
            return out.toString();
        }
    }

    /**
     * 输出目标是否落到**本次编译读过的某个源文件**上（PRD §13.2 CLI002）。
     *
     * <p>清单就是"这次读了哪些文件"的权威答案：入口与全部依赖都在里面。只挡入口是不够的——
     * {@code -o parts/blade.pxl} 会把被引用的素材静默改写成 PNG，而编译还报成功。
     *
     * <p>比较按**真实路径**：符号链接与宿主的大小写都归一，所以 {@code ENTRY.PXL} 在
     * 大小写不敏感的文件系统上同样算冲突。
     *
     * @param candidate  输出的宿主路径（{@code -o} 或 {@code --deps} 的值）
     * @param assetRoot  本次编译的 asset root
     * @param dependencies 本次编译的依赖清单（{@link Compiled#dependencies()}）
     * @return 命中的逻辑路径；不冲突时返回 {@code null}
     */
    public static String conflictingSource(String candidate, java.io.File assetRoot,
            List<Dependency> dependencies) {
        if (candidate == null || assetRoot == null || dependencies == null) {
            return null;
        }
        java.nio.file.Path target = realPath(candidate);
        for (Dependency item : dependencies) {
            java.nio.file.Path source =
                    realPath(new java.io.File(assetRoot, item.logicalPath()).getPath());
            if (target.equals(source)) {
                return item.logicalPath();
            }
            // 路径相等只能抓住"同一个目录项"。硬链接是**另一个目录项指向同一个 inode**，
            // 路径绝不相等，但写入会截断同一份内容——所以必须再按文件身份比一次。
            if (java.nio.file.Files.exists(target) && java.nio.file.Files.exists(source)) {
                try {
                    if (java.nio.file.Files.isSameFile(target, source)) {
                        return item.logicalPath();
                    }
                } catch (java.io.IOException ignored) {
                    // 身份查不出来就退化为路径比较（上面的判断已经做过）。
                }
            }
        }
        return null;
    }

    /** 真实路径（存在时解符号链接并归一大小写）；不存在时退化为"绝对 + normalize"。 */
    private static java.nio.file.Path realPath(String path) {
        java.nio.file.Path resolved =
                new java.io.File(path).getAbsoluteFile().toPath().normalize();
        try {
            return resolved.toRealPath();
        } catch (java.io.IOException notThere) {
            return resolved;
        }
    }

    private static int versionOf(Json.Value root) throws PxlFormatException {
        Json.Value member = root.members.get("version");
        if (member == null) {
            throw fail(Codes.PXL001, "缺少必需字段 \"version\"");
        }
        if (!member.isNumber()) {
            throw fail(Codes.PXL001, "\"version\" 必须是数字");
        }
        Num.Info info = Num.analyze(member.text);
        if (!info.integral || !info.inLongRange) {
            throw fail(Codes.PXL001, "version 必须是可表示的整数: " + member.text);
        }
        // 规范 §2：通用分派域是 0..65535；域外一律 PXL001，域内不支持才是 PXL007。
        if (info.value < 0L || info.value > 65535L) {
            throw fail(Codes.PXL001, "version 超出通用域 0..65535: " + member.text);
        }
        return (int) info.value;
    }

    /** encoding 取值是否合法（与 Validator 阶段 4 同一判据）。 */
    private static boolean validEncoding(Json.Value root) {
        Json.Value encoding = root.members.get("encoding");
        if (encoding == null || !encoding.isString()) {
            return true;
        }
        String value = encoding.text;
        return "char".equals(value) || "index".equals(value) || "index4".equals(value);
    }
    /** 一份文档**内部** op 的光栅工作量（§5.0.2 的裁剪口径，不含 asset stamp 的写入）。 */
    private static long internalRasterWork(Doc doc) {
        long total = 0L;
        for (Doc.Layer layer : doc.layers) {
            for (Doc.Op op : layer.ops) {
                String kind = op.kind;
                if ("fill".equals(kind)) {
                    total += (long) doc.width * doc.height;
                } else if ("rect".equals(kind) || "erase".equals(kind)) {
                    // erase 也是矩形（v0 §5：op, x, y, w, h），按**裁剪后面积**计；
                    // 按整幅画布计会把零面积 erase 算成 width*height（发布态复核 B2）。
                    total += clipArea(doc.width, doc.height, op.x, op.y, op.w, op.h);
                } else if ("pixel".equals(kind)) {
                    total += (op.x >= 0 && op.x < doc.width && op.y >= 0 && op.y < doc.height) ? 1 : 0;
                } else if ("line".equals(kind)) {
                    total += Math.max(Math.abs(op.x1 - op.x0), Math.abs(op.y1 - op.y0)) + 1;
                } else if ("grid".equals(kind)) {
                    int columns = op.rows.length == 0 ? 0 : op.rows[0].length() / tokenWidth(doc.encoding);
                    total += clipArea(doc.width, doc.height, op.x, op.y, columns, op.rows.length);
                } else if ("mirror".equals(kind)) {
                    // §5.0.2：mirror 按**目标侧**面积计（读侧不额外计），而尺寸来自 from 矩形。
                    // 用 op.w/op.h 会恒算成 0——mirror 的 w/h 从来不被赋值（面积在 fromW/fromH），
                    // 于是"只由 mirror 组成的超预算图"在会话光栅里完全不计数。
                    total += clipArea(doc.width, doc.height, op.x, op.y, op.fromW, op.fromH);
                } else if ("stamp".equals(kind)) {
                    // asset stamp 的写入面积在 resolveAssets 里按实际面积单独计；
                    // sprite stamp 按 sprite 自身尺寸计。
                    if (op.asset == null) {
                        int columns = op.sprite == null || !doc.sprites.containsKey(op.sprite)
                                ? 0 : doc.sprites.get(op.sprite)[0].length() / tokenWidth(doc.encoding);
                        int rows = op.sprite == null || !doc.sprites.containsKey(op.sprite)
                                ? 0 : doc.sprites.get(op.sprite).length;
                        total += clipArea(doc.width, doc.height, op.x, op.y, columns, rows);
                    }
                }
            }
        }
        return total;
    }

    private static int tokenWidth(String encoding) {
        if ("char".equals(encoding)) {
            return 1;
        }
        return "index".equals(encoding) ? 2 : 4;
    }

    private static long clipArea(int width, int height, int x, int y, int w, int h) {
        long x0 = Math.max(0, x);
        long y0 = Math.max(0, y);
        long x1 = Math.min(width, (long) x + w);
        long y1 = Math.min(height, (long) y + h);
        if (x1 <= x0 || y1 <= y0) {
            return 0L;
        }
        return (x1 - x0) * (y1 - y0);
    }

    private static String sha256(byte[] data) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(data);
            StringBuilder out = new StringBuilder(hash.length * 2);
            for (byte value : hash) {
                out.append(Character.forDigit((value >> 4) & 0xf, 16));
                out.append(Character.forDigit(value & 0xf, 16));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("JRE 缺少 SHA-256", impossible);
        }
    }

    private static PxlFormatException fail(String code, String message) {
        return new PxlFormatException(code, message);
    }
}
