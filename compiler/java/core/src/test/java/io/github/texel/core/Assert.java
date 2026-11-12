package io.github.texel.core;

/** 极简断言工具：没有第三方测试框架，失败就抛 {@link AssertionError}。 */
final class Assert {

    private Assert() {
    }

    static void equal(Object expected, Object actual, String what) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(what + "：期望 <" + expected + ">，实际 <" + actual + ">");
        }
    }

    static void equal(long expected, long actual, String what) {
        if (expected != actual) {
            throw new AssertionError(what + "：期望 " + expected + "，实际 " + actual);
        }
    }

    static void isTrue(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    static void isFalse(boolean condition, String what) {
        if (condition) {
            throw new AssertionError(what);
        }
    }

    static void fail(String what) {
        throw new AssertionError(what);
    }

    /** 断言解析失败且错误码恰为 {@code code}。 */
    static PxlFormatException expectCode(String code, byte[] document, String what) {        try {
            PxlParser.parse(document);
        } catch (PxlFormatException failure) {
            if (!code.equals(failure.code())) {
                throw new AssertionError(
                        what + "：期望错误码 " + code + "，实际 " + failure.code()
                                + "（" + failure.getMessage() + "）");
            }
            return failure;
        }
        throw new AssertionError(what + "：期望抛 " + code + "，实际解析成功");
    }

/** 便捷重载：文档以 UTF-8 文本给出。 */
    static PxlFormatException expectCode(String code, String document, String what) {
        return expectCode(code, document.getBytes(java.nio.charset.Charset.forName("UTF-8")), what);
    }

    static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(data.length * 2);
        for (int index = 0; index < data.length; index++) {
            int value = data[index] & 0xFF;
            out.append("0123456789ABCDEF".charAt(value >>> 4));
            out.append("0123456789ABCDEF".charAt(value & 0xF));
        }
        return out.toString();
    }
}
