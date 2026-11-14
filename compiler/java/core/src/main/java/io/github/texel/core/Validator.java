package io.github.texel.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 规范 §11.1 的九个校验阶段。
 *
 * <p>三个容易写错、这里刻意钉住的地方：
 *
 * <ol>
 *   <li><b>必须按阶段全量检查，不能"遍历到哪个错就报哪个"。</b>阶段序不是错误码大小序：
 *       未知 {@code encoding} 搭配坏 {@code grid} 必须报 {@code PXL003}（阶段 4），
 *       而不是 {@code PXL001}（阶段 5）——因为"哪些字符合法"依赖 token 宽度，而宽度
 *       本身还没定值。</li>
 *   <li><b>未知 op 只跳过"专属"检查</b>（成员集合、数值域、token），但重复成员名、
 *       {@code op} 存在且是字符串这两条<b>照常</b>执行。</li>
 *   <li><b>资源乘积必须用 64 位。</b>{@code 65536 × 65536} 用 32 位算会回绕成 0，
 *       于是超限文档"通过"。见 §5.0.1 的论证。</li>
 * </ol>
 */
final class Validator {

    static final int MAX_DIMENSION = 65536;
    static final long MAX_AREA = 4194304L;                 // 2^22
    static final int MAX_LAYERS = 256;
    static final long MAX_LAYERS_TIMES_AREA = 16777216L;   // 2^24
    static final int MAX_OPS_PER_LAYER = 32768;
    static final int MAX_SPRITES = 4096;
    static final int MAX_SPRITE_ROWS = 65536;
    static final long MAX_DOC_BYTES = 67108864L;           // 64 MiB

    /** 上限的人类可读写法：错误消息从这里取，不再各自硬编码 "64 MiB"。 */
    static String maxDocBytesText() {
        return (MAX_DOC_BYTES / (1024L * 1024L)) + " MiB";
    }
    /** 光栅工作预算：所有 op 裁剪后的像素写入总数（规范 §5.0.2）。 */
    static final long MAX_RASTER_WORK = 4194304L;          // 2^22
    static final int MAX_TOKENS_PER_ROW = 65536;
    static final int MAX_COORD = 65536;
    static final int MAX_SLOT = 65535;
    static final int MAX_PALETTE = 65535;

    /** §4.2 的 61 字符表；{@code .} 是透明，{@code z} 保留未用。 */
    private static final String CHAR_TOKENS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxy";

    private static final String[] OP_NAMES = {
        "fill", "rect", "pixel", "line", "grid", "mirror", "erase", "stamp",
    };

    private Validator() {
    }

    /** 把 JSON 根节点校验并构造成 {@link Doc}。任何不合法都抛 {@link PxlFormatException}。 */
    static Doc validate(byte[] raw, Json.Value root) throws PxlFormatException {
        return validate(raw, root, false);
    }

    /**
     * v1 入口：{@code v1 == false} 时与 {@link #validate(byte[], Json.Value)} 逐字节等价。
     *
     * <p>v1 只放宽两处：顶层多认 {@code assets}（并明确拒绝尚未实现的 {@code params}）、
     * {@code stamp} 多认 {@code asset} 来源。v0 文档永远走 {@code v1 == false}，判定不变。
     */
    static Doc validate(byte[] raw, Json.Value root, boolean v1) throws PxlFormatException {
        return validate(raw, root, v1, "");
    }

    /**
     * 带**本文档逻辑路径**的校验入口。
     *
     * <p>为什么需要它：R1 的逃逸判定必须相对**声明该引用的文档所在目录**（规范 §7），而不是相对
     * asset root。{@code parts/a.pxl} 里写 {@code ../shared/b.pxl} 是合法的（结果 {@code shared/b.pxl}），
     * 但按 root 规范化会弹出根而被误判 PXL008。校验器自己不知道文档是谁，只有调用方知道。
     *
     * @param documentPath 本文档自己的规范化逻辑路径（入口或子文档）
     */
    static Doc validate(byte[] raw, Json.Value root, boolean v1, String documentPath)
            throws PxlFormatException {
        if (!root.isObject()) {
            throw fail(Codes.PXL001, "顶层必须是 JSON 对象");
        }
        Skeleton doc = new Skeleton();
        doc.docBytes = raw.length;
        doc.v1 = v1;

        // ---------- 阶段 3：结构、成员集合、类型类别、字面量合法性 ----------
        phaseStructure(root, doc);
        if (v1) {
            // asset 声明的字段/类型/名字属于结构层；逻辑路径留到阶段 R1（v0 九阶段之后）。
            phaseAssets(root, doc);
        }

        // ---------- 阶段 4：encoding 取值 ----------
        if (!"char".equals(doc.encoding)
                && !"index".equals(doc.encoding)
                && !"index4".equals(doc.encoding)) {
            throw fail(Codes.PXL003, "encoding 取值非法: " + doc.encoding);
        }
        int tokenWidth = tokenWidth(doc.encoding);

        // ---------- 阶段 5：依赖 token 宽度的检查 ----------
        phaseTokens(doc, tokenWidth);

        // ---------- 阶段 6：数值域与资源上界 ----------
        phaseNumbers(doc, tokenWidth);


        // ---------- 阶段 7：档位是否等于由 palette 长度派生出的值 ----------
        // palette 长度 >= 65536 时不存在可派生的档位，本阶段跳过（留给阶段 9）。
        if (doc.palette.size() <= MAX_PALETTE) {
            String derived = deriveEncoding(doc.palette.size());
            if (!derived.equals(doc.encoding)) {
                throw fail(Codes.PXL003,
                        "palette 长度 " + doc.palette.size() + " 应派生 " + derived
                                + "，文档声明 " + doc.encoding);
            }
        }

        // ---------- 阶段 8：op 名是否在八条之内 ----------
        phaseOpNames(doc);

        // ---------- 阶段 9：调色板、slot 引用、sprite 引用 ----------
        phaseReferences(doc, tokenWidth);

        if (v1) {
            // 阶段 R1：逻辑路径语法与规范化。放在 v0 九阶段之后，v0 的错误码与优先级才逐条不变。
            normalizeAssetPaths(doc, documentPath);
        }

        return build(doc);
    }

    // ---------------------------------------------------------------- 阶段 3

    private static void phaseStructure(Json.Value root, Skeleton doc) throws PxlFormatException {
        Set<String> allowedTop = doc.v1
                ? setOf("format", "version", "encoding", "width", "height", "palette", "sprites",
                        "layers", "assets", "params")
                : setOf("format", "version", "encoding", "width", "height", "palette", "sprites",
                        "layers");
        for (String name : root.members.keySet()) {
            if (!allowedTop.contains(name)) {
                throw fail(Codes.PXL001, "顶层出现未定义字段: \"" + name + "\"");
            }
        }

        Json.Value format = required(root, "format");
        if (!format.isString() || !"PXL".equals(format.text)) {
            throw fail(Codes.PXL001, "format 必须恒为 \"PXL\"");
        }

        Json.Value version = required(root, "version");
        if (!version.isNumber()) {
            throw fail(Codes.PXL001, "version 必须是数字");
        }
        Num.Info versionInfo = Num.analyze(version.text);
        // `inLongRange` 必须一起查：超出 64 位的词法（如 1e99999）在 Num 里是
        // 「是整数但不可表示」，此时 `value` 是没有意义的 0。只看 `value == 0` 会把
        // 任意大的指数读成 0，于是 §12 的 fail-closed（version 非 0 一律 PXL001）被绕开。
        long expectedVersion = doc.v1 ? 1L : 0L;
        if (!versionInfo.integral || !versionInfo.inLongRange
                || versionInfo.value != expectedVersion) {
            throw fail(Codes.PXL001, "version 必须恒为 " + expectedVersion + "，收到 " + version.text);
        }

        Json.Value encoding = required(root, "encoding");
        if (!encoding.isString()) {
            throw fail(Codes.PXL001, "encoding 必须是字符串");
        }
        doc.encoding = encoding.text;

        Json.Value width = required(root, "width");
        if (!width.isNumber()) {
            throw fail(Codes.PXL001, "width 必须是数字");
        }
        doc.widthLexeme = width.text;

        Json.Value height = required(root, "height");
        if (!height.isNumber()) {
            throw fail(Codes.PXL001, "height 必须是数字");
        }
        doc.heightLexeme = height.text;

        // v1 允许省略 palette（等同空调色板：透明填充、纯 asset 文档用不到颜色）；
        // v0 仍然要求字段存在，判定不变（发布态复核 B1）。
        Json.Value palette = doc.v1 ? root.members.get("palette") : required(root, "palette");
        if (palette != null) {
            if (!palette.isArray()) {
                throw fail(Codes.PXL001, "palette 必须是数组");
            }
            for (Json.Value item : palette.items) {
                if (!item.isString()) {
                    throw fail(Codes.PXL001, "palette 的每一项都必须是字符串");
                }
                doc.palette.add(item.text);
            }
        }

        Json.Value sprites = root.members.get("sprites");
        if (sprites != null) {
            if (!sprites.isObject()) {
                throw fail(Codes.PXL001, "sprites 必须是对象");
            }
            for (Map.Entry<String, Json.Value> entry : sprites.members.entrySet()) {
                String name = entry.getKey();
                requireName(name, "sprite 名");
                Json.Value rows = entry.getValue();
                if (!rows.isArray()) {
                    throw fail(Codes.PXL001, "sprite \"" + name + "\" 的值必须是数组");
                }
                doc.spriteNames.add(name);
                doc.spriteRows.put(name, checkRows(rows, "sprite \"" + name + "\""));
            }
        }

        Json.Value layers = required(root, "layers");
        if (!layers.isArray()) {
            throw fail(Codes.PXL001, "layers 必须是数组");
        }
        if (layers.items.isEmpty()) {
            throw fail(Codes.PXL001, "layers 至少要有 1 个图层");
        }
        Set<String> layerNames = new HashSet<String>();
        for (Json.Value layerValue : layers.items) {
            if (!layerValue.isObject()) {
                throw fail(Codes.PXL001, "layers 的每个元素都必须是对象");
            }
            Set<String> allowedLayer = setOf("name", "blend", "ops");
            for (String name : layerValue.members.keySet()) {
                if (!allowedLayer.contains(name)) {
                    throw fail(Codes.PXL001, "图层对象出现未定义字段: \"" + name + "\"");
                }
            }

            Json.Value nameValue = required(layerValue, "name");
            if (!nameValue.isString()) {
                throw fail(Codes.PXL001, "图层 name 必须是字符串");
            }
            requireName(nameValue.text, "图层名");
            if (!layerNames.add(nameValue.text)) {
                throw fail(Codes.PXL001, "图层名在文档内重复: \"" + nameValue.text + "\"");
            }

            Json.Value blendValue = required(layerValue, "blend");
            if (!blendValue.isString()) {
                throw fail(Codes.PXL001, "图层 blend 必须是字符串");
            }
            if (!"normal".equals(blendValue.text)
                    && !"multiply".equals(blendValue.text)
                    && !"mask".equals(blendValue.text)) {
                throw fail(Codes.PXL001, "图层 blend 取值非法: " + blendValue.text);
            }

            Json.Value opsValue = required(layerValue, "ops");
            if (!opsValue.isArray()) {
                throw fail(Codes.PXL001, "图层 ops 必须是数组");
            }

            RawLayer layer = new RawLayer(nameValue.text, blendValue.text);
            for (Json.Value opValue : opsValue.items) {
                layer.ops.add(checkOp(opValue, doc.v1));
            }
            doc.layers.add(layer);
        }
    }

    /** 一个 op 对象的**通用**检查 + 已知 op 的专属结构检查。 */
    private static RawOp checkOp(Json.Value opValue, boolean v1) throws PxlFormatException {
        if (!opValue.isObject()) {
            throw fail(Codes.PXL001, "ops 的每个元素都必须是对象");
        }
        Json.Value kindValue = opValue.members.get("op");
        if (kindValue == null) {
            throw fail(Codes.PXL001, "op 对象缺少 \"op\" 字段");
        }
        if (!kindValue.isString()) {
            throw fail(Codes.PXL001, "op 字段必须是字符串");
        }
        String kind = kindValue.text;
        RawOp op = new RawOp(kind, opValue);

        if (!isKnownOp(kind)) {
            // 未知 op：只做通用检查（重复成员名已由 JSON 层拒绝），阶段 8 报 PXL004。
            return op;
        }

        switch (kind) {
            case "fill":
                expectMembers(opValue, kind, new String[] {"slot"}, NONE);
                requireNumberMember(opValue, "slot", kind);
                break;
            case "rect":
                expectMembers(opValue, kind, new String[] {"x", "y", "w", "h", "slot"}, NONE);
                requireNumberMember(opValue, "x", kind);
                requireNumberMember(opValue, "y", kind);
                requireNumberMember(opValue, "w", kind);
                requireNumberMember(opValue, "h", kind);
                requireNumberMember(opValue, "slot", kind);
                break;
            case "pixel":
                expectMembers(opValue, kind, new String[] {"x", "y", "slot"}, NONE);
                requireNumberMember(opValue, "x", kind);
                requireNumberMember(opValue, "y", kind);
                requireNumberMember(opValue, "slot", kind);
                break;
            case "line":
                expectMembers(opValue, kind, new String[] {"x0", "y0", "x1", "y1", "slot"}, NONE);
                requireNumberMember(opValue, "x0", kind);
                requireNumberMember(opValue, "y0", kind);
                requireNumberMember(opValue, "x1", kind);
                requireNumberMember(opValue, "y1", kind);
                requireNumberMember(opValue, "slot", kind);
                break;
            case "grid":
                expectMembers(opValue, kind, new String[] {"x", "y", "grid"}, NONE);
                requireNumberMember(opValue, "x", kind);
                requireNumberMember(opValue, "y", kind);
                Json.Value rows = opValue.members.get("grid");
                if (rows == null || !rows.isArray()) {
                    throw fail(Codes.PXL001, "grid op 的 \"grid\" 必须是数组");
                }
                op.rows = checkRows(rows, "grid op");
                break;
            case "mirror": {
                expectMembers(opValue, kind, new String[] {"from", "to"}, FLIP);
                Json.Value from = requireObjectMember(opValue, "from", kind);
                expectMembers(from, "from", new String[] {"x", "y", "w", "h"}, NONE);
                requireNumberMember(from, "x", "from");
                requireNumberMember(from, "y", "from");
                requireNumberMember(from, "w", "from");
                requireNumberMember(from, "h", "from");
                Json.Value to = requireObjectMember(opValue, "to", kind);
                expectMembers(to, "to", new String[] {"x", "y"}, NONE);
                requireNumberMember(to, "x", "to");
                requireNumberMember(to, "y", "to");
                optionalBooleanMember(opValue, "flipX", kind);
                optionalBooleanMember(opValue, "flipY", kind);
                break;
            }
            case "erase":
                expectMembers(opValue, kind, new String[] {"x", "y", "w", "h"}, NONE);
                requireNumberMember(opValue, "x", kind);
                requireNumberMember(opValue, "y", kind);
                requireNumberMember(opValue, "w", kind);
                requireNumberMember(opValue, "h", kind);
                break;
            case "stamp": {
                // v1 只扩展**来源**（§5.3）：sprite 与 asset 恰好出现一个。
                boolean hasSprite = opValue.members.containsKey("sprite");
                boolean hasAsset = opValue.members.containsKey("asset");
                if (hasAsset && !v1) {
                    throw fail(Codes.PXL001, "v0 的 stamp 不接受 \"asset\"（v1 才扩展来源）");
                }
                if (hasSprite && hasAsset) {
                    throw fail(Codes.PXL001, "stamp 的 \"sprite\" 与 \"asset\" 不得同时出现");
                }
                if (!hasSprite && !hasAsset) {
                    throw fail(Codes.PXL001, "stamp 必须恰好给出 \"sprite\" 或 \"asset\" 之一");
                }
                expectMembers(opValue, kind,
                        hasAsset ? new String[] {"asset", "x", "y"} : new String[] {"sprite", "x", "y"},
                        FLIP);
                Json.Value source = opValue.members.get(hasAsset ? "asset" : "sprite");
                if (!source.isString()) {
                    throw fail(Codes.PXL001, "stamp 的 \"" + (hasAsset ? "asset" : "sprite")
                            + "\" 必须是字符串");
                }
                if (hasAsset) {
                    op.asset = source.text;
                } else {
                    op.sprite = source.text;
                }
                requireNumberMember(opValue, "x", kind);
                requireNumberMember(opValue, "y", kind);
                optionalBooleanMember(opValue, "flipX", kind);
                optionalBooleanMember(opValue, "flipY", kind);
                break;
            }
            default:
                break;
        }
        return op;
    }

    /**
     * 一个 token 行数组的**形状**检查：至少 1 行、每行非空、各行等长（按码点）。
     *
     * <p>这三条都<b>不</b>依赖 token 宽度，因此在 {@code encoding} 定值之前就能判定，
     * 归阶段 3（§11.1）。行内字符是否合法才依赖档位，归阶段 5。
     */
    private static String[] checkRows(Json.Value rows, String where) throws PxlFormatException {
        if (rows.items.isEmpty()) {
            throw fail(Codes.PXL001, where + " 的行数组不能为空");
        }
        String[] out = new String[rows.items.size()];
        int expectedLength = -1;
        for (int index = 0; index < rows.items.size(); index++) {
            Json.Value row = rows.items.get(index);
            if (!row.isString()) {
                throw fail(Codes.PXL001, where + " 的第 " + index + " 行不是字符串");
            }
            int codePoints = row.text.codePointCount(0, row.text.length());
            if (codePoints == 0) {
                throw fail(Codes.PXL001, where + " 的第 " + index + " 行是空串");
            }
            if (expectedLength < 0) {
                expectedLength = codePoints;
            } else if (codePoints != expectedLength) {
                throw fail(Codes.PXL001, where + " 的各行长度必须相等");
            }
            out[index] = row.text;
        }
        return out;
    }

    // ---------------------------------------------------------------- 阶段 5

    private static void phaseTokens(Skeleton doc, int tokenWidth) throws PxlFormatException {
        for (String name : doc.spriteNames) {
            checkTokenRows(doc.spriteRows.get(name), tokenWidth, "sprite \"" + name + "\"");
        }
        for (RawLayer layer : doc.layers) {
            for (RawOp op : layer.ops) {
                if ("grid".equals(op.kind) && op.rows != null) {
                    checkTokenRows(op.rows, tokenWidth, "grid op");
                }
            }
        }
    }

    private static void checkTokenRows(String[] rows, int tokenWidth, String where)
            throws PxlFormatException {
        for (int index = 0; index < rows.length; index++) {
            String row = rows[index];
            int codePoints = row.codePointCount(0, row.length());
            if (codePoints % tokenWidth != 0) {
                throw fail(Codes.PXL001,
                        where + " 第 " + index + " 行字符数 " + codePoints
                                + " 不是 token 宽度 " + tokenWidth + " 的正整数倍");
            }
            checkTokenChars(row, tokenWidth, where, index);
        }
    }

    /**
     * 逐码点检查字符集。
     *
     * <p><b>不用</b>宿主语言的"是否为空白"分类：规范 §3.4 明写只认枚举出的字符集，
     * 因为不同实现对 U+00A0 这类码点分类不同，照语言分类会让同一份文件在两处得到
     * {@code PXL001} 与 {@code PXL003} 两个答案。
     */
    private static void checkTokenChars(String row, int tokenWidth, String where, int rowIndex)
            throws PxlFormatException {
        for (int offset = 0; offset < row.length(); ) {
            int codePoint = row.codePointAt(offset);
            offset += Character.charCount(codePoint);
            boolean legal;
            if (tokenWidth == 1) {
                legal = codePoint == '.' || (codePoint < 0x80 && CHAR_TOKENS.indexOf(codePoint) >= 0);
            } else {
                legal = codePoint < 0x80 && isUpperHexDigit((char) codePoint);
            }
            if (!legal) {
                throw fail(Codes.PXL001,
                        where + " 第 " + rowIndex + " 行含非法 token 字符 U+"
                                + toHex4(codePoint));
            }
        }
    }

    private static boolean isUpperHexDigit(char ch) {
        return (ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F');
    }

    // ---------------------------------------------------------------- 阶段 6

    private static void phaseNumbers(Skeleton doc, int tokenWidth) throws PxlFormatException {
        int width = requireInteger(doc.widthLexeme, 1, MAX_DIMENSION, "width");
        int height = requireInteger(doc.heightLexeme, 1, MAX_DIMENSION, "height");
        doc.width = width;
        doc.height = height;

        long area = (long) width * (long) height;
        if (area > MAX_AREA) {
            throw fail(Codes.PXL002, "width × height = " + area + " 超过 2^22");
        }
        if (doc.layers.size() > MAX_LAYERS) {
            throw fail(Codes.PXL002, "图层数 " + doc.layers.size() + " 超过 256");
        }
        if ((long) doc.layers.size() * area > MAX_LAYERS_TIMES_AREA) {
            throw fail(Codes.PXL002, "图层数 × 面积 = " + ((long) doc.layers.size() * area)
                    + " 超过 2^24");
        }
        if (doc.spriteNames.size() > MAX_SPRITES) {
            throw fail(Codes.PXL002, "sprites 项数 " + doc.spriteNames.size() + " 超过 4096");
        }
        for (String name : doc.spriteNames) {
            String[] spriteRows = doc.spriteRows.get(name);
            if (spriteRows.length > MAX_SPRITE_ROWS) {
                throw fail(Codes.PXL002, "sprite \"" + name + "\" 有 " + spriteRows.length
                        + " 行，超过 65536");
            }
            // §3.4 的 token 数上界适用于**凡出现 token 行数组的地方**：grid 字段与
            // sprites 的每个值。只查 grid 会让「sprite 单行 65537 个 token」被接受，
            // 与独立实现分叉（它是同一份文档、同一个上界）。
            checkTokenCount(spriteRows, tokenWidth, "sprite \"" + name + "\"");
        }
        if (doc.docBytes > MAX_DOC_BYTES) {
            throw fail(Codes.PXL002, "文档 UTF-8 字节数 " + doc.docBytes + " 超过 " + maxDocBytesText());
        }

        long rasterWork = 0L;
        for (RawLayer layer : doc.layers) {
            if (layer.ops.size() > MAX_OPS_PER_LAYER) {
                throw fail(Codes.PXL002,
                        "图层 \"" + layer.name + "\" 有 " + layer.ops.size() + " 条 op，超过 32768");
            }
            for (RawOp op : layer.ops) {
                if (!isKnownOp(op.kind)) {
                    continue;
                }
                phaseOpNumbers(op);
                if ("grid".equals(op.kind) && op.rows != null) {
                    checkTokenCount(op.rows, tokenWidth, "grid op");
                }
                rasterWork += opWork(doc, op, width, height, tokenWidth);
                if (rasterWork > MAX_RASTER_WORK) {
                    throw fail(Codes.PXL002, "光栅工作 " + rasterWork
                            + " 超过 2^22 的预算（裁剪后的像素写入总数）");
                }
            }
        }
    }

    /**
     * 行 token 数上界（§3.4 / §11.1 阶段 6）。
     *
     * <p>上界限制的是 **token 数**而不是字符数：{@code index4} 档下一行可以合法地有
     * 262144 个字符，却只能有 65536 个 token。按字符数判会把一个合法文档误拒。
     */
    private static void checkTokenCount(String[] rows, int tokenWidth, String where)
            throws PxlFormatException {
        for (int index = 0; index < rows.length; index++) {
            long tokens = (long) rows[index].codePointCount(0, rows[index].length()) / tokenWidth;
            if (tokens > MAX_TOKENS_PER_ROW) {
                throw fail(Codes.PXL002,
                        where + " 第 " + index + " 行有 " + tokens + " 个 token，超过 65536");
            }
        }
    }

    private static void phaseOpNumbers(RawOp op) throws PxlFormatException {
        switch (op.kind) {
            case "fill":
                op.slot = requireSlot(op, "slot");
                break;
            case "rect":
                op.x = requireCoord(op, "x");
                op.y = requireCoord(op, "y");
                op.w = requireSpan(op, "w");
                op.h = requireSpan(op, "h");
                op.slot = requireSlot(op, "slot");
                break;
            case "pixel":
                op.x = requireCoord(op, "x");
                op.y = requireCoord(op, "y");
                op.slot = requireSlot(op, "slot");
                break;
            case "line":
                op.x0 = requireCoord(op, "x0");
                op.y0 = requireCoord(op, "y0");
                op.x1 = requireCoord(op, "x1");
                op.y1 = requireCoord(op, "y1");
                op.slot = requireSlot(op, "slot");
                break;
            case "grid":
                op.x = requireCoord(op, "x");
                op.y = requireCoord(op, "y");
                break;
            case "mirror":
                op.fromX = requireCoord(op, "from", "x");
                op.fromY = requireCoord(op, "from", "y");
                op.fromW = requireSpan(op, "from", "w");
                op.fromH = requireSpan(op, "from", "h");
                op.x = requireCoord(op, "to", "x");
                op.y = requireCoord(op, "to", "y");
                op.flipX = readBoolean(op, "flipX");
                op.flipY = readBoolean(op, "flipY");
                break;
            case "erase":
                op.x = requireCoord(op, "x");
                op.y = requireCoord(op, "y");
                op.w = requireSpan(op, "w");
                op.h = requireSpan(op, "h");
                break;
            case "stamp":
                // v1：来源可能是 asset；asset 的实际尺寸要等解析期才知道，
                // 因此这里只登记名字，像素写入量由 PxlCompiler 在解析后按实际面积计。
                if (op.value.members.containsKey("asset")) {
                    op.asset = op.value.members.get("asset").text;
                } else {
                    op.sprite = op.value.members.get("sprite").text;
                }
                op.x = requireCoord(op, "x");
                op.y = requireCoord(op, "y");
                op.flipX = readBoolean(op, "flipX");
                op.flipY = readBoolean(op, "flipY");
                break;
            default:
                break;
        }
    }

    /**
     * 一条 op 裁剪后的像素写入数（规范 §5.0.2 的计数口径）。
     *
     * <p>口径必须与实际写入一一对应，否则"预算"会被实现悄悄绕过：
     * `line` 按步数计（越界点也走一步，不打折），`grid` / `stamp` 按与画布求交后的
     * 行列计，`mirror` 按目标侧面积计（读侧不额外计）。
     */
    private static long opWork(Skeleton doc, RawOp op, int width, int height, int tokenWidth) {
        switch (op.kind) {
            case "fill":
                return (long) width * (long) height;
            case "rect":
            case "erase":
                return clippedArea(width, height, op.x, op.y, op.w, op.h);
            case "pixel":
                return inside(width, height, op.x, op.y) ? 1L : 0L;
            case "line":
                return (long) Math.max(Math.abs(op.x1 - op.x0), Math.abs(op.y1 - op.y0)) + 1L;
            case "grid": {
                if (op.rows == null || op.rows.length == 0) {
                    return 0L;
                }
                long columns = (long) op.rows[0].codePointCount(0, op.rows[0].length())
                        / tokenWidth;
                return clippedArea(width, height, op.x, op.y, (int) Math.min(columns, 65536L),
                        op.rows.length);
            }
            case "mirror":
                return clippedArea(width, height, op.x, op.y, op.fromW, op.fromH);
            case "stamp": {
                String[] rows = doc.spriteRows.get(op.sprite);
                if (rows == null || rows.length == 0) {
                    return 0L;
                }
                long columns = (long) rows[0].codePointCount(0, rows[0].length()) / tokenWidth;
                return clippedArea(width, height, op.x, op.y, (int) Math.min(columns, 65536L),
                        rows.length);
            }
            default:
                return 0L;
        }
    }

    private static long clippedArea(int width, int height, int x, int y, int w, int h) {
        if (w <= 0 || h <= 0) {
            return 0L;
        }
        long left = Math.max(0L, (long) x);
        long top = Math.max(0L, (long) y);
        long right = Math.min((long) width, (long) x + (long) w);
        long bottom = Math.min((long) height, (long) y + (long) h);
        if (left >= right || top >= bottom) {
            return 0L;
        }
        return (right - left) * (bottom - top);
    }

    private static boolean inside(int width, int height, int x, int y) {
        return x >= 0 && x < width && y >= 0 && y < height;
    }

    private static int requireCoord(RawOp op, String member) throws PxlFormatException {
        return requireInteger(op.value.members.get(member).text, -MAX_COORD, MAX_COORD,
                op.kind + "." + member);
    }

    private static int requireCoord(RawOp op, String group, String member) throws PxlFormatException {
        Json.Value holder = op.value.members.get(group);
        return requireInteger(holder.members.get(member).text, -MAX_COORD, MAX_COORD,
                op.kind + "." + group + "." + member);
    }

    private static int requireSpan(RawOp op, String member) throws PxlFormatException {
        return requireInteger(op.value.members.get(member).text, 0, MAX_DIMENSION,
                op.kind + "." + member);
    }

    private static int requireSpan(RawOp op, String group, String member) throws PxlFormatException {
        Json.Value holder = op.value.members.get(group);
        return requireInteger(holder.members.get(member).text, 0, MAX_DIMENSION,
                op.kind + "." + group + "." + member);
    }

    private static int requireSlot(RawOp op, String member) throws PxlFormatException {
        return requireInteger(op.value.members.get(member).text, 0, MAX_SLOT,
                op.kind + "." + member);
    }

    private static boolean readBoolean(RawOp op, String member) {
        Json.Value value = op.value.members.get(member);
        return value != null && value.isBoolean() && value.truth;
    }

    private static int requireInteger(String lexeme, long min, long max, String where)
            throws PxlFormatException {
        Num.Info info = Num.analyze(lexeme);
        if (!info.integral) {
            throw fail(Codes.PXL002, where + " 必须是整数值，收到 " + lexeme);
        }
        if (!info.inLongRange || info.value < min || info.value > max) {
            throw fail(Codes.PXL002,
                    where + " = " + lexeme + " 越出允许区间 " + min + ".." + max);
        }
        return (int) info.value;
    }

    // ---------------------------------------------------------------- 阶段 8

    private static void phaseOpNames(Skeleton doc) throws PxlFormatException {
        for (RawLayer layer : doc.layers) {
            for (RawOp op : layer.ops) {
                if (!isKnownOp(op.kind)) {
                    throw fail(Codes.PXL004, "未知 op: \"" + op.kind + "\"");
                }
            }
        }
    }

    // ---------------------------------------------------------------- 阶段 9

    private static void phaseReferences(Skeleton doc, int tokenWidth) throws PxlFormatException {
        int paletteSize = doc.palette.size();
        if (paletteSize > MAX_PALETTE) {
            throw fail(Codes.PXL005, "palette 长度 " + paletteSize + " 无档位可派生");
        }
        for (int index = 0; index < paletteSize; index++) {
            String item = doc.palette.get(index);
            if (item.length() != 8 || !isUpperHex(item)) {
                throw fail(Codes.PXL005, "palette 第 " + (index + 1) + " 项不是 8 位大写十六进制");
            }
            if ("00000000".equals(item)) {
                throw fail(Codes.PXL005, "palette 第 " + (index + 1) + " 项是 00000000，与透明 slot 冲突");
            }
        }

        int tierCap = tierCapacity(doc.encoding);
        int maxSlot = Math.min(tierCap, paletteSize);

        for (RawLayer layer : doc.layers) {
            for (RawOp op : layer.ops) {
                if ("grid".equals(op.kind)) {
                    // grid 的 slot 全来自 token 解码，没有数值 slot 参数。
                    checkRowSlots(op.rows, tokenWidth, paletteSize, "grid op");
                } else if ("erase".equals(op.kind)) {
                    // erase 没有 slot 参数，隐含写 slot 0（§5.7）。
                } else if ("stamp".equals(op.kind)) {
                    if (op.asset != null) {
                        if (!doc.assetPath.containsKey(op.asset)) {
                            throw fail(Codes.PXL005,
                                    "stamp 引用了未声明的 asset: \"" + op.asset + "\"");
                        }
                    } else if (!doc.spriteRows.containsKey(op.sprite)) {
                        throw fail(Codes.PXL005, "stamp 引用了不存在的 sprite: \"" + op.sprite + "\"");
                    }
                } else if (op.slot > maxSlot) {
                    throw fail(Codes.PXL005,
                            op.kind + ".slot = " + op.slot + " 超过上限 " + maxSlot);
                }
            }
        }

        // 未被任何 stamp 引用的 sprite 同样要校验（§11.1 阶段 9 明写"未引用不等于不校验"）。
        for (String name : doc.spriteNames) {
            checkRowSlots(doc.spriteRows.get(name), tokenWidth, paletteSize, "sprite \"" + name + "\"");
        }
    }

    private static void checkRowSlots(String[] rows, int tokenWidth, int paletteSize, String where)
            throws PxlFormatException {
        for (int rowIndex = 0; rowIndex < rows.length; rowIndex++) {
            String row = rows[rowIndex];
            for (int offset = 0; offset + tokenWidth <= row.length(); offset += tokenWidth) {
                int slot = decodeSlot(row, offset, tokenWidth);
                if (slot > paletteSize) {
                    throw fail(Codes.PXL005,
                            where + " 第 " + rowIndex + " 行的 token 指向 slot " + slot
                                    + "，超过 palette 长度 " + paletteSize);
                }
            }
        }
    }

    /** 按固定宽度切一段 token 并解成 slot；{@code 0} 是透明 slot。 */
    static int decodeSlot(String row, int offset, int tokenWidth) {
        if (tokenWidth == 1) {
            char ch = row.charAt(offset);
            if (ch == '.') {
                return 0;
            }
            return CHAR_TOKENS.indexOf(ch) + 1;
        }
        int value = 0;
        for (int index = 0; index < tokenWidth; index++) {
            char ch = row.charAt(offset + index);
            value = value * 16 + (ch <= '9' ? ch - '0' : ch - 'A' + 10);
        }
        return value;
    }

    // ---------------------------------------------------------------- 构建

    // ---------------------------------------------------------------- v1：asset

    // 每文档 asset 声明数上限（规范 §10.1）只有一处定义：Params.MAX_ASSET_DECLARATIONS。
    // 这里不再保留第二份副本——两份同名常量里只要有一份没被引用，改另一份时就会静默分叉。

    private static final java.util.regex.Pattern ASSET_NAME =
            java.util.regex.Pattern.compile("[a-z][a-z0-9_]{0,63}");

    /**
     * asset 声明的结构检查（规范 §5.1）。逻辑路径**不在这里**查——它在阶段 R1。
     *
     * <p>{@code params} 尚未实现（PRD 阶段 4），出现即 {@code PXL012}：宁可明确拒绝，
     * 也不能静默忽略掉它再渲染出一张"看起来对"的图。
     */
    private static void phaseAssets(Json.Value root, Skeleton doc) throws PxlFormatException {
        Json.Value assets = root.members.get("assets");
        if (assets == null) {
            return;
        }
        if (!assets.isObject()) {
            throw fail(Codes.PXL001, "\"assets\" 必须是对象");
        }
        // 声明数上限属于文档级数值域（阶段表第 7 行），不在结构阶段判：
        // 否则会抢在 encoding 的 PXL003 前面（发布态复核 F2 / LA1）。
        for (Map.Entry<String, Json.Value> entry : assets.members.entrySet()) {
            String name = entry.getKey();
            if (!ASSET_NAME.matcher(name).matches()) {
                throw fail(Codes.PXL001, "asset 名不匹配 [a-z][a-z0-9_]{0,63}: \"" + name + "\"");
            }
            Json.Value value = entry.getValue();
            if (!value.isObject()) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 必须是对象");
            }
            for (String member : value.members.keySet()) {
                if (!"kind".equals(member) && !"path".equals(member) && !"params".equals(member)) {
                    throw fail(Codes.PXL001, "asset \"" + name + "\" 出现未定义字段: \"" + member + "\"");
                }
            }
            Json.Value kind = value.members.get("kind");
            if (kind == null) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 缺少 \"kind\"");
            }
            if (!kind.isString()) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 的 \"kind\" 必须是字符串");
            }
            if (!"pxl".equals(kind.text) && !"png".equals(kind.text)) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 的 kind 只能是 \"pxl\" 或 \"png\"");
            }
            Json.Value path = value.members.get("path");
            if (path == null) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 缺少 \"path\"");
            }
            if (!path.isString()) {
                throw fail(Codes.PXL001, "asset \"" + name + "\" 的 \"path\" 必须是字符串");
            }
            Json.Value assetParams = value.members.get("params");
            if (assetParams != null) {
                if (!assetParams.isObject()) {
                    throw fail(Codes.PXL001, "asset \"" + name + "\" 的 \"params\" 必须是对象");
                }
                if ("png".equals(kind.text)) {
                    throw fail(Codes.PXL001, "kind=png 的 asset 不得有 \"params\"");
                }
                doc.assetParams.put(name, assetParams);
            }
            doc.assetKind.put(name, kind.text);
            doc.assetPath.put(name, path.text);
        }
    }

    /**
     * 阶段 R1：把每个声明的 path 规范化成「相对 asset root 的唯一名称」（原地替换）。
     *
     * <p>两件事分开做，因为它们依赖不同的东西：
     *
     * <ol>
     *   <li>{@link LogicalPath#syntaxOnly} —— 字符集、段形状、长度。这与"声明它的文档在哪"无关，
     *       所以先单独查一遍，让语法错（大写、反斜杠、{@code .} 段）报在"规范化"之前，诊断更准。</li>
     *   <li>{@link LogicalPath#resolve} —— 按**声明该引用的文档所在目录**拼再规范化，于是
     *       {@code parts/a.pxl} 里的 {@code ../shared/b.pxl} 合法（= {@code shared/b.pxl}），
     *       而根文档里的 {@code ../outside/x.pxl} 会弹出根 -> {@code PXL008}。</li>
     * </ol>
     *
     * <p>逃逸判定留在这里而不是 R2，是因为 §9.1 要求**未选中的声明**也要过语法与路径校验：
     * 语料 {@code negative/neg-path-escape-root} 的 asset 恰恰没有被 stamp 选中。
     */
    private static void normalizeAssetPaths(Skeleton doc, String documentPath)
            throws PxlFormatException {
        for (Map.Entry<String, String> entry : doc.assetPath.entrySet()) {
            String raw = entry.getValue();
            LogicalPath.syntaxOnly(raw);
            entry.setValue(LogicalPath.resolve(documentPath == null ? "" : documentPath, raw));
        }
    }

    private static Doc build(Skeleton doc) {
        byte[][] palette = new byte[doc.palette.size()][];
        for (int index = 0; index < palette.length; index++) {
            palette[index] = hexToRgba(doc.palette.get(index));
        }
        Doc.Layer[] layers = new Doc.Layer[doc.layers.size()];
        for (int index = 0; index < layers.length; index++) {
            RawLayer raw = doc.layers.get(index);
            Doc.Op[] ops = new Doc.Op[raw.ops.size()];
            for (int opIndex = 0; opIndex < ops.length; opIndex++) {
                ops[opIndex] = raw.ops.get(opIndex);
            }
            layers[index] = new Doc.Layer(raw.blend, ops);
        }
        Map<String, Doc.Asset> assets = new LinkedHashMap<String, Doc.Asset>();
        for (Map.Entry<String, String> item : doc.assetPath.entrySet()) {
            assets.put(item.getKey(), new Doc.Asset(doc.assetKind.get(item.getKey()), item.getValue(),
                    doc.assetParams.get(item.getKey())));
        }
        return new Doc(doc.width, doc.height, doc.encoding, palette, doc.spriteRows, assets, layers);
    }

    private static byte[] hexToRgba(String hex) {
        byte[] out = new byte[4];
        for (int index = 0; index < 4; index++) {
            int high = hexDigit(hex.charAt(index * 2));
            int low = hexDigit(hex.charAt(index * 2 + 1));
            out[index] = (byte) ((high << 4) | low);
        }
        return out;
    }

    private static int hexDigit(char ch) {
        return ch <= '9' ? ch - '0' : ch - 'A' + 10;
    }

    // ---------------------------------------------------------------- 小工具

    static boolean isKnownOp(String kind) {
        for (String name : OP_NAMES) {
            if (name.equals(kind)) {
                return true;
            }
        }
        return false;
    }

    static int tokenWidth(String encoding) {
        if ("char".equals(encoding)) {
            return 1;
        }
        if ("index".equals(encoding)) {
            return 2;
        }
        return 4;
    }

    // 档位容量只在这里定义一次（Core §4.1）。派档位与判 slot 上限都必须读它们：
    // 两处各写一遍字面量时，改一处就会出现"阶段 7 判 PXL003、阶段 9 却放行"的自相矛盾。
    static final int TIER_CHAR = 61;
    static final int TIER_INDEX = 255;
    static final int TIER_INDEX4 = 65535;

    static int tierCapacity(String encoding) {
        if ("char".equals(encoding)) {
            return TIER_CHAR;
        }
        if ("index".equals(encoding)) {
            return TIER_INDEX;
        }
        return TIER_INDEX4;
    }

    static String deriveEncoding(int paletteSize) {
        if (paletteSize <= TIER_CHAR) {
            return "char";
        }
        if (paletteSize <= TIER_INDEX) {
            return "index";
        }
        return "index4";
    }

    private static Json.Value required(Json.Value obj, String name) throws PxlFormatException {
        Json.Value value = obj.members.get(name);
        if (value == null) {
            throw fail(Codes.PXL001, "缺少必需字段: \"" + name + "\"");
        }
        return value;
    }

    private static Json.Value requireObjectMember(Json.Value obj, String name, String where)
            throws PxlFormatException {
        Json.Value value = obj.members.get(name);
        if (value == null) {
            throw fail(Codes.PXL001, where + " 缺少字段 \"" + name + "\"");
        }
        if (!value.isObject()) {
            throw fail(Codes.PXL001, where + " 的 \"" + name + "\" 必须是对象");
        }
        return value;
    }

    private static void requireNumberMember(Json.Value obj, String name, String where)
            throws PxlFormatException {
        Json.Value value = obj.members.get(name);
        if (value == null) {
            throw fail(Codes.PXL001, where + " 缺少字段 \"" + name + "\"");
        }
        if (!value.isNumber()) {
            throw fail(Codes.PXL001, where + " 的 \"" + name + "\" 必须是数字");
        }
    }

    private static void optionalBooleanMember(Json.Value obj, String name, String where)
            throws PxlFormatException {
        Json.Value value = obj.members.get(name);
        if (value != null && !value.isBoolean()) {
            throw fail(Codes.PXL001, where + " 的 \"" + name + "\" 必须是布尔值");
        }
    }

    /**
     * 成员集合必须**恰好**等于规范给该对象的那一组。
     *
     * <p>{@code required} 与 {@code optional} 必须分开：{@code mirror} / {@code stamp}
     * 的 {@code flipX} / {@code flipY} 是可选成员，缺了合法、写了也合法（显式
     * {@code false} 与省略等价，§3.7）。把它们塞进"必需"会让 {@code op-stamp-flip}
     * 这类用例直接判定失败。
     */
    private static void expectMembers(Json.Value obj, String where,
            String[] required, String[] optional) throws PxlFormatException {
        Set<String> permitted = new HashSet<String>();
        if (isKnownOp(where)) {
            permitted.add("op");
        }
        permitted.addAll(Arrays.asList(required));
        permitted.addAll(Arrays.asList(optional));
        for (String name : obj.members.keySet()) {
            if (!permitted.contains(name)) {
                throw fail(Codes.PXL001, where + " 出现未定义字段: \"" + name + "\"");
            }
        }
        for (String name : required) {
            if (!obj.members.containsKey(name)) {
                throw fail(Codes.PXL001, where + " 缺少必需字段: \"" + name + "\"");
            }
        }
    }

    private static final String[] NONE = new String[0];
    private static final String[] FLIP = {"flipX", "flipY"};

    private static void requireName(String name, String what) throws PxlFormatException {
        if (name.isEmpty()) {
            throw fail(Codes.PXL001, what + "不能为空");
        }
        char first = name.charAt(0);
        if (first < 'a' || first > 'z') {
            throw fail(Codes.PXL001, what + " 必须匹配 [a-z][a-z0-9_-]*: \"" + name + "\"");
        }
        for (int index = 1; index < name.length(); index++) {
            char ch = name.charAt(index);
            boolean ok = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')
                    || ch == '_' || ch == '-';
            if (!ok) {
                throw fail(Codes.PXL001, what + " 必须匹配 [a-z][a-z0-9_-]*: \"" + name + "\"");
            }
        }
    }

    private static boolean isUpperHex(String text) {
        for (int index = 0; index < text.length(); index++) {
            if (!isUpperHexDigit(text.charAt(index))) {
                return false;
            }
        }
        return true;
    }

    private static String toHex4(int codePoint) {
        StringBuilder out = new StringBuilder(4);
        for (int shift = 12; shift >= 0; shift -= 4) {
            out.append("0123456789ABCDEF".charAt((codePoint >> shift) & 0xF));
        }
        return out.toString();
    }

    private static Set<String> setOf(String... names) {
        return new HashSet<String>(Arrays.asList(names));
    }

    private static PxlFormatException fail(String code, String message) {
        return new PxlFormatException(code, message);
    }

    // ---------------------------------------------------------------- 中间结构

    private static final class Skeleton {
        int docBytes;
        String encoding;
        String widthLexeme;
        String heightLexeme;
        int width;
        int height;
        final List<String> palette = new ArrayList<String>();
        final List<String> spriteNames = new ArrayList<String>();
        final Map<String, String[]> spriteRows = new LinkedHashMap<String, String[]>();
        final List<RawLayer> layers = new ArrayList<RawLayer>();
        boolean v1;
        /** asset 名 -> kind（pxl / png）。 */
        final Map<String, String> assetKind = new LinkedHashMap<String, String>();
        /** asset 名 -> 文档里写的原始 path；阶段 R1 规范化后原地替换。 */
        final Map<String, String> assetPath = new LinkedHashMap<String, String>();
        /** asset 名 -> 子文档参数映射的原始 JSON。 */
        final Map<String, Json.Value> assetParams = new LinkedHashMap<String, Json.Value>();
    }

    private static final class RawLayer {
        final String name;
        final String blend;
        final List<RawOp> ops = new ArrayList<RawOp>();

        RawLayer(String name, String blend) {
            this.name = name;
            this.blend = blend;
        }
    }

    /**
     * 校验期的 op：直接在 {@link Doc.Op} 上加"原始 JSON"。
     *
     * <p>上一版这里把 {@code Doc.Op} 的 15 个字段又抄了一遍，再用 {@code toOp()} 逐字段
     * 复抄回去：给 op 加一个字段就要改三处，漏一处就是渲染期读到默认值（2026-11-14 审查 P2）。
     * 现在继承父类字段，只多一个 {@code value}，校验结束后直接就是渲染期要的 {@link Doc.Op}。
     *
     * <p>没有 {@code slot} 成员的 op（{@code erase} / {@code grid}）用父类的默认值 0——
     * 那正是渲染期的语义，不再需要 {@code -1} 哨兵 + 一次归一。
     */
    private static final class RawOp extends Doc.Op {
        final Json.Value value;

        RawOp(String kind, Json.Value value) {
            super(kind);
            this.value = value;
        }
    }
}
