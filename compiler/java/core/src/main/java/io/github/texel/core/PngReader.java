package io.github.texel.core;

import java.util.ArrayList;
import java.util.List;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/**
 * 生产级 PNG 解码器（规范 v0 §8.1 / §8.5 的封闭规则，v1 §8.3 复用同一语义）。
 *
 * <p>为什么自己写：{@code javax.imageio} 属 {@code java.desktop}，core 只允许 {@code java.base}；
 * 而宿主解码器还会做色彩管理、静默丢弃 ancillary chunk 的问题、对损坏 CRC 视而不见——这些都与
 * 「同一份字节在任何实现上解出同一批像素」直接冲突。
 *
 * <p>名字叫 PngReader 而不是 PngDecoder：test source set 里已有一个同包名 decoder（只解自家产物），
 * 同名会在测试编译期把生产类遮蔽掉。
 *
 * <p>接受集固定：8 bit/通道、非交错、色彩类型 0 / 2 / 3 / 4 / 6（含各自的 {@code tRNS}）。
 * 其余一律 {@link PxlFormatException} {@code PXL006}。**不接受任何"宽容"**：CRC 全校验、
 * Adler-32 由 {@link Inflater} 校验、IDAT 必须连续、IEND 之后不得有字节。
 */
final class PngReader {

    private static final byte[] SIGNATURE = {(byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};

    /** 解码结果：宽、高、RGBA8 原始字节流（长度恒为 4*width*height）。 */
    static final class Decoded {

        final int width;
        final int height;
        final byte[] rgba;

        Decoded(int width, int height, byte[] rgba) {
            this.width = width;
            this.height = height;
            this.rgba = rgba;
        }
    }

    /** 只读 IHDR 的结果：宽高，不持有任何像素缓冲（{@link #readHeader} 的返回值）。 */
    static final class Header {

        final int width;
        final int height;

        Header(int width, int height) {
            this.width = width;
            this.height = height;
        }
    }

    private PngReader() {
    }

    /**
     * 前 8 字节是否就是 PNG 签名。**只看签名**，不做任何结构判断。
     *
     * <p>它只服务一件事：v1 的 asset 层判断「声明的 kind 与实际字节是否相符」（规范 §11.1：
     * 声明为 {@code png} 而字节不是 PNG 归 {@code PXL009}）。PNG 结构本身的合法性仍归
     * {@link #decode} 的 {@code PXL006}，两个码的边界是「是不是 PNG」与「PNG 是否合法」。
     */
    static boolean hasSignature(byte[] png) {
        if (png.length < SIGNATURE.length) {
            return false;
        }
        for (int index = 0; index < SIGNATURE.length; index++) {
            if (png[index] != SIGNATURE[index]) {
                return false;
            }
        }
        return true;
    }

    /**
     * 只读签名与 IHDR，返回宽高；**不分配任何像素缓冲，也不解压 IDAT**。
     *
     * <p>为什么需要它：「累计 PNG 解码像素数」这条预算（规范 §10.2）按名字就该约束**解码
     * 工作量**。若先解码再判预算，N 张各自合法的大图会全部解完才报 PXL011——32 张 2048²
     * 就常驻约 512 MiB，4096 张是 64 GiB，先 OOM 再轮到预算（审查 R4-02）。所以 v1 的
     * asset 层在**读到 IHDR 时**就把像素数计入并即时判断。
     *
     * <p>这里只覆盖到 IHDR 为止的结构检查；IDAT/IEND 等由 {@link #decode} 判。
     */
    static Header readHeader(byte[] png) throws PxlFormatException {
        checkSignature(png);
        int ihdrStart = SIGNATURE.length + 8;
        if (png.length < ihdrStart + 13 + 4) {
            throw fail("IHDR 被截断");
        }
        if (readUint32(png, SIGNATURE.length) != 13) {
            throw fail("IHDR 长度必须是 13");
        }
        if (!"IHDR".equals(chunkType(png, SIGNATURE.length + 4))) {
            throw fail("第一个 chunk 必须是 IHDR");
        }
        long declared = readUint32(png, ihdrStart + 13);
        long actual = crc32(png, SIGNATURE.length + 4, 17);
        if (declared != actual) {
            throw fail("chunk IHDR 的 CRC 不符");
        }
        checkIhdr(png, ihdrStart);
        return new Header((int) readUint32(png, ihdrStart), (int) readUint32(png, ihdrStart + 4));
    }

    private static void checkSignature(byte[] png) throws PxlFormatException {
        if (png.length < SIGNATURE.length) {
            throw fail("文件短于 PNG 签名");
        }
        for (int index = 0; index < SIGNATURE.length; index++) {
            if (png[index] != SIGNATURE[index]) {
                throw fail("PNG 签名不符");
            }
        }
    }

    /** IHDR 的字段级规则（宽高 / 位深 / 色彩类型 / 方法 / 交错 / 面积），{@code decode} 与 {@code readHeader} 共用。 */
    private static void checkIhdr(byte[] png, int dataStart) throws PxlFormatException {
        int width = (int) readUint32(png, dataStart);
        int height = (int) readUint32(png, dataStart + 4);
        int bitDepth = png[dataStart + 8] & 0xff;
        int colorType = png[dataStart + 9] & 0xff;
        int compression = png[dataStart + 10] & 0xff;
        int filter = png[dataStart + 11] & 0xff;
        int interlace = png[dataStart + 12] & 0xff;
        if (compression != 0) {
            throw fail("压缩方法必须为 0");
        }
        if (filter != 0) {
            throw fail("过滤方法必须为 0");
        }
        if (interlace != 0) {
            throw fail("不接受交错(interlace=" + interlace + ")");
        }
        if (bitDepth != 8) {
            throw fail("只接受 8 bit 通道，实际 " + bitDepth);
        }
        if (colorType != 0 && colorType != 2 && colorType != 3
                && colorType != 4 && colorType != 6) {
            throw fail("不支持的色彩类型 " + colorType);
        }
        if (width < 1 || height < 1) {
            throw fail("宽高必须 >= 1");
        }
        if (width > 65536 || height > 65536) {
            throw fail("宽高超过 65536");
        }
        if ((long) width * height > (1L << 22)) {
            throw fail("像素面积超过 2^22");
        }
    }

    static Decoded decode(byte[] png) throws PxlFormatException {
        checkSignature(png);
        int offset = SIGNATURE.length;
        boolean sawIhdr = false;
        boolean sawIend = false;
        boolean sawPlte = false;
        boolean sawTrns = false;
        boolean idatStarted = false;
        boolean idatEnded = false;
        int width = 0;
        int height = 0;
        int bitDepth = 8;
        int colorType = 0;
        int trnsLength = -1;
        byte[] trnsData = null;
        byte[] plte = null;
        List<byte[]> idat = new ArrayList<byte[]>();
        while (offset < png.length) {
            if (offset + 8 > png.length) {
                throw fail("chunk 头被截断");
            }
            long length = readUint32(png, offset);
            if (length > 0x7fffffffL) {
                throw fail("chunk 长度最高位为 1");
            }
            if (offset + 8L + length + 4L > png.length) {
                throw fail("chunk 声明长度越过文件尾");
            }
            String type = chunkType(png, offset + 4);
            int dataStart = offset + 8;
            int dataEnd = dataStart + (int) length;
            long declared = readUint32(png, dataEnd);
            long actual = crc32(png, offset + 4, (int) length + 4);
            if (declared != actual) {
                throw fail("chunk " + type + " 的 CRC 不符");
            }
            boolean critical = Character.isUpperCase(type.charAt(0));
            if ("IHDR".equals(type)) {
                if (sawIhdr || offset != SIGNATURE.length) {
                    throw fail("IHDR 必须唯一且排第一");
                }
                if (length != 13) {
                    throw fail("IHDR 长度必须是 13");
                }
                sawIhdr = true;
                checkIhdr(png, dataStart);
                width = (int) readUint32(png, dataStart);
                height = (int) readUint32(png, dataStart + 4);
                bitDepth = png[dataStart + 8] & 0xff;
                colorType = png[dataStart + 9] & 0xff;
            } else if ("PLTE".equals(type)) {
                if (sawPlte) {
                    throw fail("PLTE 至多一个");
                }
                if (idatStarted) {
                    throw fail("PLTE 必须早于第一个 IDAT");
                }
                if (sawTrns) {
                    throw fail("tRNS 存在时 PLTE 必须更早");
                }
                if (colorType == 0 || colorType == 4) {
                    throw fail("色彩类型 " + colorType + " 不得有 PLTE");
                }
                if (length % 3 != 0 || length < 3 || length > 768) {
                    throw fail("PLTE 长度必须是 3 的倍数且项数 1..256");
                }
                sawPlte = true;
                plte = java.util.Arrays.copyOfRange(png, dataStart, dataEnd);
            } else if ("tRNS".equals(type)) {
                if (sawTrns) {
                    throw fail("tRNS 至多一个");
                }
                if (idatStarted) {
                    throw fail("tRNS 必须早于第一个 IDAT");
                }
                // "tRNS 早于 PLTE" 的顺序检查在 PLTE 分支里（遇到 PLTE 时 sawTrns 为真即报错）。
                // 这里曾有一句 `plte != null && !sawPlte`，而 plte 只在 sawPlte 为真时才赋值，
                // 所以它永假、永不可达（审查 R4-07），已删除以免让人以为有两道顺序检查。
                if (colorType == 4 || colorType == 6) {
                    throw fail("色彩类型 " + colorType + " 不得有 tRNS");
                }
                if (colorType == 3) {
                    int entries = plte == null ? 0 : plte.length / 3;
                    if (length > entries) {
                        throw fail("tRNS 长度超过 PLTE 项数");
                    }
                } else if (colorType == 0) {
                    if (length != 2) {
                        throw fail("色彩类型 0 的 tRNS 必须恰为 2 字节");
                    }
                } else if (colorType == 2) {
                    if (length != 6) {
                        throw fail("色彩类型 2 的 tRNS 必须恰为 6 字节");
                    }
                }
                sawTrns = true;
                trnsLength = (int) length;
                trnsData = java.util.Arrays.copyOfRange(png, dataStart, dataEnd);
            } else if ("IDAT".equals(type)) {
                if (idatEnded) {
                    throw fail("IDAT 必须连续出现");
                }
                idatStarted = true;
                idat.add(java.util.Arrays.copyOfRange(png, dataStart, dataEnd));
            } else if ("IEND".equals(type)) {
                if (sawIend) {
                    throw fail("IEND 至多一个");
                }
                if (length != 0) {
                    throw fail("IEND 的长度必须为 0");
                }
                sawIend = true;
                if (dataEnd + 4 != png.length) {
                    throw fail("IEND 之后不得有任何字节");
                }
                break;
            } else if (critical) {
                throw fail("未知的 critical chunk: " + type);
            }
            if (idatStarted && !"IDAT".equals(type) && !"IEND".equals(type)) {
                idatEnded = true;
            }
            offset = dataEnd + 4;
        }
        if (!sawIhdr) {
            throw fail("缺少 IHDR");
        }
        if (!sawIend) {
            throw fail("缺少 IEND");
        }
        if (idat.isEmpty()) {
            throw fail("缺少 IDAT");
        }
        if (colorType == 3 && !sawPlte) {
            throw fail("色彩类型 3 必须有 PLTE");
        }
        int channels = channelsOf(colorType);
        byte[] inflated = inflate(idat, height, width, channels);
        return unfilter(inflated, width, height, colorType, channels, plte, trnsData, trnsLength);
    }

    private static int channelsOf(int colorType) {
        switch (colorType) {
            case 0:
            case 3:
                return 1;
            case 2:
                return 3;
            case 4:
                return 2;
            default:
                return 4;
        }
    }

    private static byte[] inflate(List<byte[]> idat, int height, int width, int channels)
            throws PxlFormatException {
        int total = 0;
        for (byte[] part : idat) {
            total += part.length;
        }
        byte[] compressed = new byte[total];
        int at = 0;
        for (byte[] part : idat) {
            System.arraycopy(part, 0, compressed, at, part.length);
            at += part.length;
        }
        long expected = (long) height * (1L + (long) width * channels);
        if (expected > Integer.MAX_VALUE) {
            throw fail("像素缓冲超过 2 GiB");
        }
        Inflater inflater = new Inflater();
        try {
            inflater.setInput(compressed);
            byte[] out = new byte[(int) expected];
            int written = 0;
            while (written < out.length) {
                int produced = inflater.inflate(out, written, out.length - written);
                if (produced == 0) {
                    if (inflater.finished() || inflater.needsInput() || inflater.needsDictionary()) {
                        throw fail("IDAT 解压后字节数不足");
                    }
                }
                written += produced;
            }
            if (!inflater.finished()) {
                throw fail("IDAT 不是恰好一个完整 zlib 流");
            }
            if (inflater.getRemaining() != 0) {
                throw fail("zlib 流之后还有剩余字节");
            }
            return out;
        } catch (DataFormatException failure) {
            throw fail("zlib 数据非法(含 Adler-32 校验): " + failure.getMessage());
        } finally {
            inflater.end();
        }
    }

    private static Decoded unfilter(byte[] raw, int width, int height, int colorType,
            int channels, byte[] plte, byte[] trns, int trnsLength) throws PxlFormatException {
        int stride = width * channels;
        byte[] previous = new byte[stride];
        byte[] current = new byte[stride];
        byte[] rgba = new byte[width * height * 4];
        int offset = 0;
        for (int row = 0; row < height; row++) {
            int filter = raw[offset++] & 0xff;
            if (filter > 4) {
                throw fail("行过滤器字节必须是 0..4，实际 " + filter);
            }
            System.arraycopy(raw, offset, current, 0, stride);
            offset += stride;
            for (int index = 0; index < stride; index++) {
                int left = index >= channels ? current[index - channels] & 0xff : 0;
                int up = previous[index] & 0xff;
                int upLeft = index >= channels ? previous[index - channels] & 0xff : 0;
                int value = current[index] & 0xff;
                switch (filter) {
                    case 1:
                        value += left;
                        break;
                    case 2:
                        value += up;
                        break;
                    case 3:
                        value += (left + up) / 2;
                        break;
                    case 4:
                        value += paeth(left, up, upLeft);
                        break;
                    default:
                        break;
                }
                current[index] = (byte) value;
            }
            writeRow(rgba, current, row, width, colorType, channels, plte, trns, trnsLength);
            byte[] swap = previous;
            previous = current;
            current = swap;
        }
        return new Decoded(width, height, rgba);
    }

    private static void writeRow(byte[] rgba, byte[] row, int y, int width, int colorType,
            int channels, byte[] plte, byte[] trns, int trnsLength) throws PxlFormatException {
        for (int x = 0; x < width; x++) {
            int base = x * channels;
            int out = (y * width + x) * 4;
            switch (colorType) {
                case 0: {
                    int gray = row[base] & 0xff;
                    int alpha = 255;
                    if (trns != null && trnsLength == 2) {
                        int key = ((trns[0] & 0xff) << 8) | (trns[1] & 0xff);
                        if ((key & 0xff) == gray) {
                            alpha = 0;
                        }
                    }
                    rgba[out] = (byte) gray;
                    rgba[out + 1] = (byte) gray;
                    rgba[out + 2] = (byte) gray;
                    rgba[out + 3] = (byte) alpha;
                    break;
                }
                case 2: {
                    int red = row[base] & 0xff;
                    int green = row[base + 1] & 0xff;
                    int blue = row[base + 2] & 0xff;
                    int alpha = 255;
                    if (trns != null && trnsLength == 6) {
                        int keyRed = (((trns[0] & 0xff) << 8) | (trns[1] & 0xff)) & 0xff;
                        int keyGreen = (((trns[2] & 0xff) << 8) | (trns[3] & 0xff)) & 0xff;
                        int keyBlue = (((trns[4] & 0xff) << 8) | (trns[5] & 0xff)) & 0xff;
                        if (keyRed == red && keyGreen == green && keyBlue == blue) {
                            alpha = 0;
                        }
                    }
                    rgba[out] = (byte) red;
                    rgba[out + 1] = (byte) green;
                    rgba[out + 2] = (byte) blue;
                    rgba[out + 3] = (byte) alpha;
                    break;
                }
                case 3: {
                    int index = row[base] & 0xff;
                    int entries = plte.length / 3;
                    if (index >= entries) {
                        throw fail("调色板索引 " + index + " 越过 PLTE 项数 " + entries);
                    }
                    int alpha = 255;
                    if (trns != null && index < trnsLength) {
                        alpha = trns[index] & 0xff;
                    }
                    rgba[out] = plte[index * 3];
                    rgba[out + 1] = plte[index * 3 + 1];
                    rgba[out + 2] = plte[index * 3 + 2];
                    rgba[out + 3] = (byte) alpha;
                    break;
                }
                case 4: {
                    int gray = row[base] & 0xff;
                    rgba[out] = (byte) gray;
                    rgba[out + 1] = (byte) gray;
                    rgba[out + 2] = (byte) gray;
                    rgba[out + 3] = row[base + 1];
                    break;
                }
                default: {
                    rgba[out] = row[base];
                    rgba[out + 1] = row[base + 1];
                    rgba[out + 2] = row[base + 2];
                    rgba[out + 3] = row[base + 3];
                    break;
                }
            }
        }
    }

    private static int paeth(int left, int up, int upLeft) {
        int estimate = left + up - upLeft;
        int distLeft = Math.abs(estimate - left);
        int distUp = Math.abs(estimate - up);
        int distUpLeft = Math.abs(estimate - upLeft);
        if (distLeft <= distUp && distLeft <= distUpLeft) {
            return left;
        }
        return distUp <= distUpLeft ? up : upLeft;
    }

    private static long readUint32(byte[] data, int offset) {
        return ((long) (data[offset] & 0xff) << 24) | ((long) (data[offset + 1] & 0xff) << 16)
                | ((long) (data[offset + 2] & 0xff) << 8) | (long) (data[offset + 3] & 0xff);
    }

    private static String chunkType(byte[] data, int offset) throws PxlFormatException {
        for (int index = 0; index < 4; index++) {
            int value = data[offset + index] & 0xff;
            boolean letter = (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z');
            if (!letter) {
                throw fail("chunk 类型名必须是 4 个 ASCII 字母");
            }
            if (index == 2 && value >= 'a') {
                throw fail("chunk 类型名的第三字节必须是保留位(大写)");
            }
        }
        return new String(new char[] {(char) (data[offset] & 0xff), (char) (data[offset + 1] & 0xff),
                (char) (data[offset + 2] & 0xff), (char) (data[offset + 3] & 0xff)});
    }

    private static long crc32(byte[] data, int offset, int length) {
        java.util.zip.CRC32 crc = new java.util.zip.CRC32();
        crc.update(data, offset, length);
        return crc.getValue();
    }

    private static PxlFormatException fail(String message) {
        return new PxlFormatException(Codes.PXL006, message);
    }
}
