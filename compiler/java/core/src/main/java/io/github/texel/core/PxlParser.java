package io.github.texel.core;

/**
 * PXL 文本 → 可渲染文档。
 *
 * <p>这是 core 的入口之一。它做两件事并按此顺序失败：先把字节解码成 JSON
 * （UTF-8 必须严格合法），再按规范 §11.1 的九个阶段校验。两者失败都是
 * {@link PxlFormatException}，调用方靠 {@link PxlFormatException#code()} 分辨。
 *
 * <p>返回的 {@link Renderer} 是不透明句柄：文档模型本身不暴露，避免内部数组与可变
 * 集合泄漏出去（fail-closed：句柄不暴露可变内部状态）。
 */
public final class PxlParser {

    private PxlParser() {
    }

    /**
     * 解析一份 PXL 文档。
     *
     * @param document 文档的 UTF-8 原始字节（允许开头 BOM）
     * @return 可供渲染的句柄
     * @throws PxlFormatException 文档不合法，或字节不是合法 UTF-8
     */
    public static Renderer parse(byte[] document) throws PxlFormatException {
        if (document == null) {
            throw new NullPointerException("document");
        }
        // 字节上限必须**在解析之前**判（规范 §11.1 的例外条款）：67,129,581 字节的文档
        // 在 -Xmx128m/256m/512m 下都会在 JSON 解析阶段 OOM，先解析再判等于没有上限。
        // 这条的优先级高于阶段 2/3 的 UTF-8 与 JSON 语法错误，超限报 PXL002。
        if (document.length > Validator.MAX_DOC_BYTES) {
            throw new PxlFormatException(Codes.PXL002,
                    "文档 UTF-8 字节数 " + document.length + " 超过 " + Validator.maxDocBytesText());
        }
        Json.Value root = Json.parse(document);
        if (!root.isObject()) {
            throw new PxlFormatException(Codes.PXL001, "顶层必须是 JSON 对象");
        }
        requireFormat(root);
        long version = versionOf(root);
        if (version == 1L) {
            Doc doc = Validator.validate(document, root, true);
            return new Renderer(doc);
        }
        if (version >= 0L && version <= 65535L) {
            // 只接受当前 wire version 1；合法但不受支持的版本报 PXL007，
            // 而不是 v0 时代的 PXL001（PRD §6.2 的分派表）。v1 的编译入口是 PxlCompiler。
            throw new PxlFormatException(Codes.PXL007,
                    "本 compiler 的档位 " + conformanceProfile() + " 不支持 version " + version);
        }
        throw new PxlFormatException(Codes.PXL001, "version 超出通用域 0..65535: " + version);
    }

    /**
     * 本实现声明的能力档案档位（PRD §6.3）。
     *
     * <p>它是**唯一真源**：CLI 的 {@code capabilities --json} 从它取 {@code conformance_profile}，
     * conformance harness 也用它选语料子集。当前稳定值是 {@code pxl-v1}。
     */
    public static String conformanceProfile() {
        return "pxl-v1";
    }

    /** {@code format} 的判定与版本无关（v0 §11.1 阶段 3）。 */
    private static void requireFormat(Json.Value root) throws PxlFormatException {
        Json.Value format = root.members.get("format");
        if (format == null) {
            throw new PxlFormatException(Codes.PXL001, "缺少必需字段 \"format\"");
        }
        if (!format.isString() || !"PXL".equals(format.text)) {
            throw new PxlFormatException(Codes.PXL001, "format 必须恒为 \"PXL\"");
        }
    }

    /** 读出 {@code version} 的整数值；缺失/非数/不可表示一律 PXL001（§2 的分派域判定）。 */
    private static long versionOf(Json.Value root) throws PxlFormatException {
        Json.Value member = root.members.get("version");
        if (member == null) {
            throw new PxlFormatException(Codes.PXL001, "缺少必需字段 \"version\"");
        }
        if (!member.isNumber()) {
            throw new PxlFormatException(Codes.PXL001, "\"version\" 必须是数字");
        }
        Num.Info info = Num.analyze(member.text);
        if (!info.integral || !info.inLongRange) {
            throw new PxlFormatException(Codes.PXL001,
                    "version 必须是可表示的整数: " + member.text);
        }
        return info.value;
    }

    /**
     * 规范 §5.0.2 的文档 UTF-8 字节上限。
     *
     * <p>对外暴露是为了让调用方（CLI）**不必复制这个数字**：它一旦与 core 分叉，
     * 「同一份文档在任何实现上都该被接受或拒绝」这条就没有执行者了。
     */
    public static long maxDocumentBytes() {
        return Validator.MAX_DOC_BYTES;
    }

    /** {@link #maxDocumentBytes()} 的人类可读形式（错误消息用，避免调用方各自硬编码）。 */
    public static String maxDocumentBytesText() {
        return Validator.maxDocBytesText();
    }

    /** 超过 {@link #maxDocumentBytes()} 时使用的错误码（§11.1 阶段 6）。 */
    public static String documentTooLargeCode() {
        return Codes.PXL002;
    }
}
