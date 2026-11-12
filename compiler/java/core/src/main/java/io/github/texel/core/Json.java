package io.github.texel.core;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 严格 JSON 读取器（规范 §3.8 的接受集、§3.2 的重复成员名拒绝）。
 *
 * <p>自己写而不是用 {@code javax.script} 之类：core 只允许 {@code java.base}，
 * 而 {@code java.base} 里没有 JSON 解析器。三条比"能解析"更重要的性质：
 *
 * <ol>
 *   <li><b>重复成员名必须拒绝</b>。不同解析器取首值 / 取末值 / 直接报错，两个
 *       {@code width} 会让同一份文本得到不同像素（§3.2）。这里在<b>每个</b>对象上
 *       检查，包括未知 op 的内部对象——那是 §11.1 的"通用对象检查"。</li>
 *   <li><b>数字保留原始词法</b>。整数性是交给 {@link Num} 做精确十进制判断的，
 *       这里绝不能先转成 {@code double}。</li>
 *   <li><b>接受集按 §3.8 从宽</b>：任意缩进、CRLF、行尾空白、成员乱序、结尾无换行、
 *       开头的 BOM 全部接受。解析器只拒绝"不是合法 JSON"。</li>
 * </ol>
 */
final class Json {

    static final int OBJECT = 0;
    static final int ARRAY = 1;
    static final int STRING = 2;
    static final int NUMBER = 3;
    static final int BOOLEAN = 4;
    static final int NULL = 5;

    /** 嵌套深度上限。合法 PXL 文档最深 6 层，这条只挡恶意深嵌套导致的栈溢出。 */
    private static final int MAX_DEPTH = 128;

    /** 解析后的 JSON 值。不可变：构造后不再写字段。 */
    static final class Value {

        final int kind;
        /** STRING 的解码值；NUMBER 的原始词法；其它种类为 {@code null}。 */
        final String text;
        final boolean truth;
        final List<Value> items;
        final Map<String, Value> members;

        private Value(int kind, String text, boolean truth, List<Value> items, Map<String, Value> members) {
            this.kind = kind;
            this.text = text;
            this.truth = truth;
            this.items = items;
            this.members = members;
        }

        static Value object(Map<String, Value> members) {
            return new Value(OBJECT, null, false, null, members);
        }

        static Value array(List<Value> items) {
            return new Value(ARRAY, null, false, items, null);
        }

        static Value string(String text) {
            return new Value(STRING, text, false, null, null);
        }

        static Value number(String lexeme) {
            return new Value(NUMBER, lexeme, false, null, null);
        }

        static Value bool(boolean truth) {
            return new Value(BOOLEAN, null, truth, null, null);
        }

        static Value nullValue() {
            return new Value(NULL, null, false, null, null);
        }

        boolean isObject() {
            return kind == OBJECT;
        }

        boolean isArray() {
            return kind == ARRAY;
        }

        boolean isString() {
            return kind == STRING;
        }

        boolean isNumber() {
            return kind == NUMBER;
        }

        boolean isBoolean() {
            return kind == BOOLEAN;
        }
    }

    /**
     * 把原始字节解码成文本（允许并忽略开头的 BOM），再解析成 JSON。
     *
     * <p>UTF-8 解码用 <b>REPORT</b> 策略：非法字节序列必须显式失败，而不是被替换成
     * U+FFFD 后"看起来解析成功"。
     */
    static Value parse(byte[] raw) throws PxlFormatException {
        String text = decodeUtf8(raw);
        if (!text.isEmpty() && text.charAt(0) == '\uFEFF') {
            text = text.substring(1);
        }
        Parser parser = new Parser(text);
        return parser.parseDocument();
    }

    static String decodeUtf8(byte[] raw) throws PxlFormatException {
        CharsetDecoder decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
        try {
            return decoder.decode(ByteBuffer.wrap(raw)).toString();
        } catch (CharacterCodingException failure) {
            throw new PxlFormatException(Codes.PXL001, "文本不是合法 UTF-8");
        }
    }

    /** 递归下降解析器。字段可变，只在解析期间存在。 */
    private static final class Parser {

        private final String src;
        private int pos;

        Parser(String src) {
            this.src = src;
        }

        Value parseDocument() throws PxlFormatException {
            skipWhitespace();
            Value root = parseValue(0);
            skipWhitespace();
            if (pos != src.length()) {
                throw error("JSON 之后还有多余内容");
            }
            return root;
        }

        private Value parseValue(int depth) throws PxlFormatException {
            if (depth > MAX_DEPTH) {
                throw error("JSON 嵌套过深");
            }
            if (pos >= src.length()) {
                throw error("JSON 意外结束");
            }
            char ch = src.charAt(pos);
            switch (ch) {
                case '{':
                    return parseObject(depth);
                case '[':
                    return parseArray(depth);
                case '"':
                    return Value.string(parseString());
                case 't':
                    expectLiteral("true");
                    return Value.bool(true);
                case 'f':
                    expectLiteral("false");
                    return Value.bool(false);
                case 'n':
                    expectLiteral("null");
                    return Value.nullValue();
                default:
                    if (ch == '-' || (ch >= '0' && ch <= '9')) {
                        return Value.number(parseNumber());
                    }
                    throw error("JSON 出现非法字符 '" + describe(ch) + "'");
            }
        }

        private Value parseObject(int depth) throws PxlFormatException {
            expect('{');
            Map<String, Value> members = new LinkedHashMap<String, Value>();
            skipWhitespace();
            if (peek() == '}') {
                pos++;
                return Value.object(members);
            }
            while (true) {
                skipWhitespace();
                if (peek() != '"') {
                    throw error("JSON 对象的成员名必须是字符串");
                }
                String name = parseString();
                if (members.containsKey(name)) {
                    throw new PxlFormatException(
                            Codes.PXL001, "同一对象内成员名重复: \"" + name + "\"");
                }
                skipWhitespace();
                expect(':');
                skipWhitespace();
                members.put(name, parseValue(depth + 1));
                skipWhitespace();
                char ch = peek();
                if (ch == ',') {
                    pos++;
                    continue;
                }
                if (ch == '}') {
                    pos++;
                    return Value.object(members);
                }
                throw error("JSON 对象里缺少 ',' 或 '}'");
            }
        }

        private Value parseArray(int depth) throws PxlFormatException {
            expect('[');
            List<Value> items = new ArrayList<Value>();
            skipWhitespace();
            if (peek() == ']') {
                pos++;
                return Value.array(items);
            }
            while (true) {
                skipWhitespace();
                items.add(parseValue(depth + 1));
                skipWhitespace();
                char ch = peek();
                if (ch == ',') {
                    pos++;
                    continue;
                }
                if (ch == ']') {
                    pos++;
                    return Value.array(items);
                }
                throw error("JSON 数组里缺少 ',' 或 ']'");
            }
        }

        private String parseString() throws PxlFormatException {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                if (pos >= src.length()) {
                    throw error("字符串没有结束引号");
                }
                char ch = src.charAt(pos++);
                if (ch == '"') {
                    return out.toString();
                }
                if (ch == '\\') {
                    out.append(parseEscape());
                    continue;
                }
                if (ch < 0x20) {
                    throw error("字符串里出现未转义的控制字符");
                }
                out.append(ch);
            }
        }

        private String parseEscape() throws PxlFormatException {
            if (pos >= src.length()) {
                throw error("转义序列不完整");
            }
            char ch = src.charAt(pos++);
            switch (ch) {
                case '"':
                    return "\"";
                case '\\':
                    return "\\";
                case '/':
                    return "/";
                case 'b':
                    return "\b";
                case 'f':
                    return "\f";
                case 'n':
                    return "\n";
                case 'r':
                    return "\r";
                case 't':
                    return "\t";
                case 'u':
                    return parseUnicodeEscape();
                default:
                    throw error("非法转义 \\" + describe(ch));
            }
        }

        /**
         * {@code \\uXXXX}。代理对按<b>一个码点</b>处理——规范 §3.4 明写长度按 Unicode
         * 码点计数，{@code "\uD83D\uDE00"} 是 1 个字符。Java 的 {@code String} 里它
         * 是 2 个 char，因此后续一切长度判断都必须走 {@code codePointCount}。
         */
        private String parseUnicodeEscape() throws PxlFormatException {
            char high = readHex4();
            if (Character.isHighSurrogate(high)
                    && pos + 1 < src.length()
                    && src.charAt(pos) == '\\'
                    && src.charAt(pos + 1) == 'u') {
                int save = pos;
                pos += 2;
                char low = readHex4();
                if (Character.isLowSurrogate(low)) {
                    return new String(new char[] {high, low});
                }
                pos = save;
            }
            return String.valueOf(high);
        }

        private char readHex4() throws PxlFormatException {
            if (pos + 4 > src.length()) {
                throw error("\\u 转义不足 4 位十六进制");
            }
            int value = 0;
            for (int index = 0; index < 4; index++) {
                char ch = src.charAt(pos + index);
                int digit = hexValue(ch);
                if (digit < 0) {
                    throw error("\\u 转义出现非十六进制字符");
                }
                value = value * 16 + digit;
            }
            pos += 4;
            return (char) value;
        }

        private String parseNumber() throws PxlFormatException {
            int start = pos;
            if (peek() == '-') {
                pos++;
            }
            if (pos >= src.length()) {
                throw error("数字不完整");
            }
            char first = src.charAt(pos);
            if (first == '0') {
                pos++;
            } else if (first >= '1' && first <= '9') {
                while (pos < src.length() && isDigit(src.charAt(pos))) {
                    pos++;
                }
            } else {
                throw error("数字缺少整数部分");
            }
            if (pos < src.length() && src.charAt(pos) == '.') {
                pos++;
                if (pos >= src.length() || !isDigit(src.charAt(pos))) {
                    throw error("小数点后缺少数字");
                }
                while (pos < src.length() && isDigit(src.charAt(pos))) {
                    pos++;
                }
            }
            if (pos < src.length() && (src.charAt(pos) == 'e' || src.charAt(pos) == 'E')) {
                pos++;
                if (pos < src.length() && (src.charAt(pos) == '+' || src.charAt(pos) == '-')) {
                    pos++;
                }
                if (pos >= src.length() || !isDigit(src.charAt(pos))) {
                    throw error("指数部分缺少数字");
                }
                while (pos < src.length() && isDigit(src.charAt(pos))) {
                    pos++;
                }
            }
            return src.substring(start, pos);
        }

        private void expectLiteral(String literal) throws PxlFormatException {
            if (!src.startsWith(literal, pos)) {
                throw error("JSON 字面量非法");
            }
            pos += literal.length();
        }

        private void expect(char expected) throws PxlFormatException {
            if (pos >= src.length() || src.charAt(pos) != expected) {
                throw error("期望 '" + expected + "'");
            }
            pos++;
        }

        private char peek() throws PxlFormatException {
            if (pos >= src.length()) {
                throw error("JSON 意外结束");
            }
            return src.charAt(pos);
        }

        private void skipWhitespace() {
            while (pos < src.length()) {
                char ch = src.charAt(pos);
                if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
                    pos++;
                } else {
                    return;
                }
            }
        }

        private PxlFormatException error(String message) {
            return new PxlFormatException(Codes.PXL001, "JSON 语法错(偏移 " + pos + "): " + message);
        }

        private static String describe(char ch) {
            if (ch < 0x20 || ch == 0x7F) {
                // 不用 String.format：它受 locale 影响，而规范 §9 禁止 locale 依赖。
                StringBuilder out = new StringBuilder(6);
                out.append("\\u");
                for (int shift = 12; shift >= 0; shift -= 4) {
                    out.append("0123456789ABCDEF".charAt((ch >> shift) & 0xF));
                }
                return out.toString();
            }
            return String.valueOf(ch);
        }

        private static boolean isDigit(char ch) {
            return ch >= '0' && ch <= '9';
        }

        private static int hexValue(char ch) {
            if (ch >= '0' && ch <= '9') {
                return ch - '0';
            }
            if (ch >= 'a' && ch <= 'f') {
                return ch - 'a' + 10;
            }
            if (ch >= 'A' && ch <= 'F') {
                return ch - 'A' + 10;
            }
            return -1;
        }
    }
}
