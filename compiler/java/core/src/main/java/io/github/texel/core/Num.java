package io.github.texel.core;

/**
 * JSON 数字词法的**精确十进制**分析（规范 §3.8、§11 {@code PXL002}）。
 *
 * <p>为什么不能用 {@code Double.parseDouble}：{@code 1.0000000000000001} 双精度解析后
 * 等于 {@code 1.0}，于是一份非法文档被静默接受；{@code 65536.0000000000001} 同理。
 * 规范明确要求"按原始词法做精确十进制判断，不得先转成二进制浮点"。
 *
 * <p>做法：把词法拆成「有效数字串 + 十进制指数」，只在最后一步把**已经证明是整数
 * 且在 long 可表示范围内**的值装箱。指数再大也不会展开成巨型整数——那正是攻击面。
 */
final class Num {

    /** 分析结果。{@code integral} 为假时 {@code value} 无意义。 */
    static final class Info {
        final boolean integral;
        final boolean inLongRange;
        final long value;

        Info(boolean integral, boolean inLongRange, long value) {
            this.integral = integral;
            this.inLongRange = inLongRange;
            this.value = value;
        }
    }

    private static final Info NOT_INTEGRAL = new Info(false, false, 0L);
    private static final Info OUT_OF_RANGE = new Info(true, false, 0L);

    private Num() {
    }

    /**
     * 分析一个**已经通过 JSON 语法校验**的数字词法。
     *
     * @param lexeme 原始词法，如 {@code -12}、{@code 4.0}、{@code 6.5536e4}
     */
    static Info analyze(String lexeme) {
        int length = lexeme.length();
        int index = 0;
        boolean negative = false;
        if (index < length && lexeme.charAt(index) == '-') {
            negative = true;
            index++;
        }

        StringBuilder digits = new StringBuilder(length);
        int integerDigits = 0;
        while (index < length && isDigit(lexeme.charAt(index))) {
            digits.append(lexeme.charAt(index));
            integerDigits++;
            index++;
        }
        int fractionDigits = 0;
        if (index < length && lexeme.charAt(index) == '.') {
            index++;
            while (index < length && isDigit(lexeme.charAt(index))) {
                digits.append(lexeme.charAt(index));
                fractionDigits++;
                index++;
            }
        }

        long exponent = 0L;
        if (index < length && (lexeme.charAt(index) == 'e' || lexeme.charAt(index) == 'E')) {
            index++;
            boolean negativeExponent = false;
            if (index < length && (lexeme.charAt(index) == '+' || lexeme.charAt(index) == '-')) {
                negativeExponent = lexeme.charAt(index) == '-';
                index++;
            }
            long magnitude = 0L;
            while (index < length && isDigit(lexeme.charAt(index))) {
                // 指数本身可以长得离谱；超过阈值后饱和，因为决定性的比较只用到
                // 「有效数字长度」这种有限量，细节位再多也不改变结论。
                if (magnitude < 100000000L) {
                    magnitude = magnitude * 10L + (lexeme.charAt(index) - '0');
                }
                index++;
            }
            exponent = negativeExponent ? -magnitude : magnitude;
        }
        if (integerDigits == 0) {
            return NOT_INTEGRAL;
        }

        int firstSignificant = 0;
        while (firstSignificant < digits.length() && digits.charAt(firstSignificant) == '0') {
            firstSignificant++;
        }
        if (firstSignificant == digits.length()) {
            // 全部数字都是 0：值与符号无关，恒为整数 0（含 -0、0.0、0e5）。
            return new Info(true, true, 0L);
        }

        String significant = digits.substring(firstSignificant);
        long scale = exponent - fractionDigits;

        String magnitudeDigits;
        if (scale >= 0) {
            magnitudeDigits = significant;
        } else {
            long drop = -scale;
            if (drop > significant.length()) {
                // 绝对值 < 1 且不为 0，不可能是整数。
                return NOT_INTEGRAL;
            }
            int cut = (int) drop;
            for (int position = significant.length() - cut; position < significant.length(); position++) {
                if (significant.charAt(position) != '0') {
                    return NOT_INTEGRAL;
                }
            }
            magnitudeDigits = significant.substring(0, significant.length() - cut);
            if (magnitudeDigits.isEmpty()) {
                // 只可能是 "0.000…0" 这类，但全零已在上面返回；这里防御性处理。
                return new Info(true, true, 0L);
            }
        }

        long totalDigits = (long) magnitudeDigits.length() + Math.max(0L, scale);
        if (totalDigits > 19L) {
            return OUT_OF_RANGE;
        }
        long value = 0L;
        try {
            for (int position = 0; position < magnitudeDigits.length(); position++) {
                value = Math.multiplyExact(value, 10L);
                value = Math.addExact(value, (long) (magnitudeDigits.charAt(position) - '0'));
            }
            for (long step = 0L; step < scale; step++) {
                value = Math.multiplyExact(value, 10L);
            }
        } catch (ArithmeticException overflow) {
            // 下界的唯一特例：-9223372036854775808 的**幅值**超出 long 正半轴，
            // 但它本身是合法的 long 最小值。不特判就会把 Long.MIN_VALUE 判成"不可表示"。
            if (negative && scale == 0L && "9223372036854775808".equals(magnitudeDigits)) {
                return new Info(true, true, Long.MIN_VALUE);
            }
            return OUT_OF_RANGE;
        }
        if (negative) {
            value = -value;
        }
        return new Info(true, true, value);
    }

    private static boolean isDigit(char ch) {
        return ch >= '0' && ch <= '9';
    }
}
