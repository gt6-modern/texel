package io.github.texel.core;

/**
 * v1 逻辑路径：校验 + 规范化（规范 §7）。
 *
 * <p>逻辑路径是**可移植命名空间**，不是宿主文件系统字符串：只允许 ASCII 小写字母、数字、
 * {@code -}、{@code _}、{@code .}、{@code /}；反斜杠、大写、绝对路径、盘符、空段、尾随斜杠、
 * {@code .} 段一律非法；{@code ..} 只允许参与规范化，且规范化时不得弹出根。
 *
 * <p>{@link #normalize} 是**纯字符串**判定：不做 realpath、不看文件系统、不碰大小写折叠。
 * 因此同一份文档在任何平台上得到同一个名字——这正是依赖身份、循环检测与清单输出要的东西。
 */
final class LogicalPath {

    /** 单段上限（字节；本类只接受 ASCII，故字符数 == 字节数）。 */
    static final int MAX_SEGMENT = 255;
    /** 规范化后的完整路径上限。 */
    static final int MAX_TOTAL = 4096;

    private LogicalPath() {
    }

    /**
     * 校验并规范化一个逻辑路径。
     *
     * @param text 文档里写的那串字符（不得为 {@code null}）
     * @return 规范化结果；非空、无前导/尾随斜杠、不含 {@code .} 或 {@code ..} 段
     * @throws PxlFormatException {@code PXL008}：语法非法、字符串层逃逸、或超出长度上限
     */
    static String normalize(String text) throws PxlFormatException {
        if (text == null || text.isEmpty()) {
            throw escape("逻辑路径为空");
        }
        for (int index = 0; index < text.length(); index++) {
            char ch = text.charAt(index);
            if (!isAllowed(ch)) {
                throw escape("逻辑路径出现非法字符 " + describe(ch) + "（只允许 a-z 0-9 - _ . /）");
            }
        }
        if (text.charAt(0) == '/') {
            throw escape("逻辑路径不得以 / 开头: " + text);
        }
        if (text.endsWith("/")) {
            throw escape("逻辑路径不得以 / 结尾: " + text);
        }
        String[] segments = text.split("/", -1);
        java.util.List<String> stack = new java.util.ArrayList<String>();
        for (String segment : segments) {
            if (segment.isEmpty()) {
                throw escape("逻辑路径含空段: " + text);
            }
            if (segment.length() > MAX_SEGMENT) {
                throw escape("路径段超过 " + MAX_SEGMENT + " 字节: " + segment);
            }
            if ((".").equals(segment)) {
                throw escape("逻辑路径不得出现 . 段: " + text);
            }
            if (("..").equals(segment)) {
                if (stack.isEmpty()) {
                    throw escape("逻辑路径规范化时弹出根（字符串层逃逸）: " + text);
                }
                stack.remove(stack.size() - 1);
                continue;
            }
            stack.add(segment);
        }
        if (stack.isEmpty()) {
            throw escape("逻辑路径规范化后为空: " + text);
        }
        StringBuilder out = new StringBuilder(text.length());
        for (int index = 0; index < stack.size(); index++) {
            if (index > 0) {
                out.append('/');
            }
            out.append(stack.get(index));
        }
        String normalized = out.toString();
        if (normalized.length() > MAX_TOTAL) {
            throw escape("逻辑路径超过 " + MAX_TOTAL + " 字节: " + normalized.length());
        }
        return normalized;
    }

    /**
     * 把 {@code base} 所在目录与相对引用拼起来再规范化（引用相对声明它的文档所在**逻辑目录**）。
     *
     * @param baseDocument 声明该引用的文档的逻辑路径（不含前导斜杠）
     * @param reference    文档里写的引用
     */
    static String resolve(String baseDocument, String reference) throws PxlFormatException {
        String directory = "";
        int slash = baseDocument.lastIndexOf('/');
        if (slash >= 0) {
            directory = baseDocument.substring(0, slash + 1);
        }
        return normalize(directory + reference);
    }

    /**
     * **只查语法**，不做规范化、不判根逃逸（规范 §11.2：R1 只查语法，逃逸要等 R2 带上声明文档目录）。
     *
     * <p>为什么不能直接用 {@link #normalize}：{@code ../x.png} 相对**声明它的文档**是合法的，
     * 但相对 asset root 会弹出根。R1 时还不知道声明文档是谁，所以这里只验字符集与段形状。
     */
    static String syntaxOnly(String text) throws PxlFormatException {
        if (text == null || text.isEmpty()) {
            throw escape("逻辑路径为空");
        }
        for (int index = 0; index < text.length(); index++) {
            char ch = text.charAt(index);
            if (!isAllowed(ch)) {
                throw escape("逻辑路径出现非法字符 " + describe(ch) + "（只允许 a-z 0-9 - _ . /）");
            }
        }
        if (text.charAt(0) == '/') {
            throw escape("逻辑路径不得以 / 开头: " + text);
        }
        if (text.endsWith("/")) {
            throw escape("逻辑路径不得以 / 结尾: " + text);
        }
        String[] segments = text.split("/", -1);
        for (String segment : segments) {
            if (segment.isEmpty()) {
                throw escape("逻辑路径含空段: " + text);
            }
            if (segment.length() > MAX_SEGMENT) {
                throw escape("路径段超过 " + MAX_SEGMENT + " 字节: " + segment);
            }
            if (".".equals(segment)) {
                throw escape("逻辑路径不得出现 . 段: " + text);
            }
        }
        // 这里不按原始文本判总长度：规范限制的是**规范化之后**的结果（§5.1），
        // 而 a/../a/../…/x 这种能抵消的长写法规范化后是合法的短路径。
        // 按原始长度判会让 Java 报 PXL008 而 Python 成功（发布态复核 F18）。结果长度由 normalize() 检查。
        return text;
    }

    static boolean isAllowed(char ch) {
        return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')
                || ch == '-' || ch == '_' || ch == '.' || ch == '/';
    }

    private static String describe(char ch) {
        if (ch == '\\') {
            return "反斜杠";
        }
        if (ch >= 'A' && ch <= 'Z') {
            return "大写字母 '" + ch + "'";
        }
        if (ch < 0x20 || ch == 0x7f) {
            return String.format("控制字符 U+%04X", Integer.valueOf(ch));
        }
        return String.format("'%c' (U+%04X)", Character.valueOf(ch), Integer.valueOf(ch));
    }

    private static PxlFormatException escape(String message) {
        return new PxlFormatException(Codes.PXL008, message);
    }
}
