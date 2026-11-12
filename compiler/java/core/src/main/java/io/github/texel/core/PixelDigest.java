package io.github.texel.core;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * 像素摘要：RGBA8 原始字节流的 SHA-256（规范 §13.4）。
 *
 * <p>字节序定义必须钉死，否则跨语言比较没有意义：逐行、行内从左到右、自上而下，
 * 每像素 4 字节 R,G,B,A，无行间填充、无对齐、无头尾附加，总长恒为 {@code 4 × w × h}。
 *
 * <p><b>摘要不含尺寸</b>：一份 2×2 的文档若被渲染成 1×4，字节序列可以完全相同。
 * 所以尺寸必须由调用方单独断言（§13.2a 第 5 条）。
 */
public final class PixelDigest {

    private PixelDigest() {
    }

    /**
     * 对一段 RGBA8 字节流求小写十六进制 SHA-256。
     *
     * @param rgba 逐像素 R,G,B,A 的原始字节
     */
    public static String sha256Hex(byte[] rgba) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] result = digest.digest(rgba);
            StringBuilder out = new StringBuilder(result.length * 2);
            for (int index = 0; index < result.length; index++) {
                int value = result[index] & 0xFF;
                out.append("0123456789ABCDEF".charAt(value >>> 4));
                out.append("0123456789ABCDEF".charAt(value & 0xF));
            }
            return out.toString().toLowerCase(java.util.Locale.ROOT);
        } catch (NoSuchAlgorithmException failure) {
            // SHA-256 是 java.base 的强制实现算法（Java SE 规范要求），不可能缺席。
            throw new IllegalStateException("运行环境缺少 SHA-256", failure);
        }
    }

    /**
     * 校验长度后再求摘要。
     *
     * @param width  画布宽
     * @param height 画布高
     * @param rgba   RGBA8 字节流，长度必须恰为 {@code 4 × width × height}
     */
    public static String sha256Hex(int width, int height, byte[] rgba) {
        long expected = 4L * (long) width * (long) height;
        if (rgba == null) {
            throw new NullPointerException("rgba");
        }
        if (expected != rgba.length) {
            throw new IllegalArgumentException(
                    "RGBA 字节数 " + rgba.length + " 与 " + width + "x" + height + " 不符");
        }
        return sha256Hex(rgba);
    }
}
