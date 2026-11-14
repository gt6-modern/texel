package io.github.texel.core;

import java.io.ByteArrayOutputStream;
import java.util.zip.CRC32;
import java.util.zip.Deflater;

/**
 * PNG 编码器（规范 §10）。
 *
 * <p>只用 {@code java.util.zip.Deflater} + 自写的 chunk / CRC 封装：core 不允许
 * {@code java.desktop}，所以 {@code javax.imageio} 与 AWT 都不能用（`AGENTS.md` 契约 7）。
 *
 * <p>输出约定全部钉死，为的是"同一实现、同一输入，两次写出的 PNG 必须字节相同"：
 *
 * <ul>
 *   <li>每通道 8 bit、色彩类型 6（RGBA）、非交错；</li>
 *   <li><b>所有行的过滤器字节恒为 0</b>（None）——固定下来就不依赖压缩启发式；</li>
 *   <li>只写 {@code IHDR} / {@code IDAT} / {@code IEND}，不写任何 ancillary chunk；</li>
 *   <li>压缩级别与策略显式设定，不读环境、不看时钟。</li>
 * </ul>
 */
public final class PngEncoder {

    private static final byte[] SIGNATURE = {
        (byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n',
    };

    /** 固定压缩级别：同一实现内必须可复现（§9）。 */
    private static final int COMPRESSION_LEVEL = Deflater.BEST_COMPRESSION;

    private PngEncoder() {
    }

    /**
     * 把 RGBA8 字节流编码成 PNG。
     *
     * @param width  宽，1..65536
     * @param height 高，1..65536
     * @param rgba   逐像素 R,G,B,A，长度必须恰为 {@code 4 × width × height}
     */
    public static byte[] encode(int width, int height, byte[] rgba) {
        if (width < 1 || width > Validator.MAX_DIMENSION) {
            throw new IllegalArgumentException("width 越界: " + width);
        }
        if (height < 1 || height > Validator.MAX_DIMENSION) {
            throw new IllegalArgumentException("height 越界: " + height);
        }
        if (rgba == null) {
            throw new NullPointerException("rgba");
        }
        long expected = 4L * (long) width * (long) height;
        if (expected != rgba.length) {
            throw new IllegalArgumentException(
                    "RGBA 字节数 " + rgba.length + " 与 " + width + "x" + height + " 不符");
        }

        int stride = width * 4;
        byte[] scanlines = new byte[height * (1 + stride)];
        int source = 0;
        int destination = 0;
        for (int row = 0; row < height; row++) {
            scanlines[destination++] = 0;   // 过滤器：恒为 None
            System.arraycopy(rgba, source, scanlines, destination, stride);
            source += stride;
            destination += stride;
        }

        byte[] compressed = deflate(scanlines);

        ByteArrayOutputStream out = new ByteArrayOutputStream(compressed.length + 64);
        out.write(SIGNATURE, 0, SIGNATURE.length);
        writeChunk(out, "IHDR", header(width, height));
        writeChunk(out, "IDAT", compressed);
        writeChunk(out, "IEND", new byte[0]);
        return out.toByteArray();
    }

    private static byte[] header(int width, int height) {
        byte[] ihdr = new byte[13];
        putInt(ihdr, 0, width);
        putInt(ihdr, 4, height);
        ihdr[8] = 8;    // 位深
        ihdr[9] = 6;    // 色彩类型：RGBA
        ihdr[10] = 0;   // 压缩方法
        ihdr[11] = 0;   // 过滤方法
        ihdr[12] = 0;   // 交错：无
        return ihdr;
    }

    private static byte[] deflate(byte[] input) {
        Deflater deflater = new Deflater(COMPRESSION_LEVEL);
        try {
            deflater.setStrategy(Deflater.DEFAULT_STRATEGY);
            deflater.setInput(input);
            deflater.finish();
            ByteArrayOutputStream out = new ByteArrayOutputStream(Math.max(64, input.length / 2));
            byte[] buffer = new byte[8192];
            while (!deflater.finished()) {
                int written = deflater.deflate(buffer);
                if (written == 0 && deflater.needsInput()) {
                    break;
                }
                out.write(buffer, 0, written);
            }
            return out.toByteArray();
        } finally {
            deflater.end();
        }
    }

    private static void writeChunk(ByteArrayOutputStream out, String type, byte[] data) {
        byte[] typeBytes = new byte[4];
        for (int index = 0; index < 4; index++) {
            typeBytes[index] = (byte) type.charAt(index);
        }
        byte[] length = new byte[4];
        putInt(length, 0, data.length);
        out.write(length, 0, 4);
        out.write(typeBytes, 0, 4);
        out.write(data, 0, data.length);

        CRC32 crc = new CRC32();
        crc.update(typeBytes, 0, 4);
        crc.update(data, 0, data.length);
        byte[] checksum = new byte[4];
        putInt(checksum, 0, (int) crc.getValue());
        out.write(checksum, 0, 4);
    }

    private static void putInt(byte[] target, int offset, int value) {
        target[offset] = (byte) ((value >>> 24) & 0xFF);
        target[offset + 1] = (byte) ((value >>> 16) & 0xFF);
        target[offset + 2] = (byte) ((value >>> 8) & 0xFF);
        target[offset + 3] = (byte) (value & 0xFF);
    }
}
