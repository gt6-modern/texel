package io.github.texel.core;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.CRC32;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/**
 * PNG 解码器（只用于验证自己写出的产物）。
 *
 * <p>为什么 core 里需要一个解码器：conformance 要判定"回渲出的 PNG 解出的 RGBA8 流
 * 是否等于期望摘要"，以及"chunk 类型序列是 {@code IHDR → IDAT+ → IEND}、所有行过滤器
 * 恒为 0"。这些都要求把 PNG 读回来；而用 {@code javax.imageio} 读就等于放弃了这两条
 * 断言（ImageIO 不告诉你过滤器字节，也不告诉你 chunk 类型序列）。
 *
 * <p>接受集按本实现自己的输出约定收窄：只接受 8 bit、色彩类型 6、非交错；
 * 但**结构校验仍是严格的**——逐 chunk 校验 CRC、长度防溢出、IEND 必须在最后且之后无字节、
 * 拼接后的 IDAT 必须恰是一个完整 zlib 流并校验 Adler-32、解压长度必须精确等于
 * {@code height × (1 + width × 4)}。宽泛的解析器会让"编码器写坏了"变成"看起来能读"。
 */
final class PngDecoder {

    private static final byte[] SIGNATURE = {
        (byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n',
    };

    /** 解码结果。{@code chunkTypes} 保留出现顺序，供 §10 的类型序列断言使用。 */
    static final class Decoded {

        final int width;
        final int height;
        final byte[] rgba;
        final List<String> chunkTypes;
        final boolean allFiltersZero;

        Decoded(int width, int height, byte[] rgba, List<String> chunkTypes,
                boolean allFiltersZero) {
            this.width = width;
            this.height = height;
            this.rgba = rgba;
            this.chunkTypes = chunkTypes;
            this.allFiltersZero = allFiltersZero;
        }
    }

    private PngDecoder() {
    }

    static Decoded decode(byte[] png) {
        if (png == null) {
            throw new IllegalArgumentException("png 为 null");
        }
        if (png.length < SIGNATURE.length) {
            throw new IllegalArgumentException("文件短于 PNG 签名");
        }
        for (int index = 0; index < SIGNATURE.length; index++) {
            if (png[index] != SIGNATURE[index]) {
                throw new IllegalArgumentException("PNG 签名不符");
            }
        }

        int width = -1;
        int height = -1;
        boolean sawHeader = false;
        boolean sawEnd = false;
        ByteArrayOutputStream idat = new ByteArrayOutputStream();
        List<String> types = new ArrayList<String>();
        int position = SIGNATURE.length;

        while (position < png.length) {
            if (position + 8 > png.length) {
                throw new IllegalArgumentException("chunk 头被截断");
            }
            long length = readUnsignedInt(png, position);
            if (length > 2147483647L) {
                throw new IllegalArgumentException("chunk 长度最高位为 1");
            }
            if (length + 12L > (long) (png.length - position)) {
                throw new IllegalArgumentException("chunk 声明长度越过文件尾");
            }
            int dataStart = position + 8;
            int dataLength = (int) length;
            String type = new String(png, position + 4, 4, java.nio.charset.StandardCharsets.US_ASCII);
            for (int index = 0; index < 4; index++) {
                char ch = type.charAt(index);
                boolean letter = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z');
                if (!letter) {
                    throw new IllegalArgumentException("chunk 类型名不是 4 个 ASCII 字母");
                }
            }

            CRC32 crc = new CRC32();
            crc.update(png, position + 4, 4 + dataLength);
            long stored = readUnsignedInt(png, dataStart + dataLength);
            if (crc.getValue() != stored) {
                throw new IllegalArgumentException("chunk " + type + " 的 CRC 不符");
            }

            types.add(type);
            if ("IHDR".equals(type)) {
                if (sawHeader || types.size() != 1) {
                    throw new IllegalArgumentException("IHDR 必须唯一且排第一");
                }
                if (dataLength != 13) {
                    throw new IllegalArgumentException("IHDR 长度必须是 13");
                }
                sawHeader = true;
                width = (int) readUnsignedInt(png, dataStart);
                height = (int) readUnsignedInt(png, dataStart + 4);
                if (png[dataStart + 8] != 8) {
                    throw new IllegalArgumentException("只接受 8 bit 通道");
                }
                if (png[dataStart + 9] != 6) {
                    throw new IllegalArgumentException("只接受色彩类型 6（RGBA）");
                }
                if (png[dataStart + 10] != 0 || png[dataStart + 11] != 0) {
                    throw new IllegalArgumentException("压缩/过滤方法必须为 0");
                }
                if (png[dataStart + 12] != 0) {
                    throw new IllegalArgumentException("不接受交错");
                }
                if (width < 1 || height < 1) {
                    throw new IllegalArgumentException("宽高必须 >= 1");
                }
                // 尺寸必须以 long 校验：`width * 4` 与 `width * height * 4` 用 int 算会回绕，
                // 于是一张 width = 0x40000000 的图能被"精确长度"检查放过（实测被接受，
                // 返回 0 字节像素）。解码器只为校验本实现的产物服务，所以直接按输出契约
                // 收窄到 1..65536 与 `4×w×h <= Integer.MAX_VALUE`。
                if (width > Validator.MAX_DIMENSION || height > Validator.MAX_DIMENSION) {
                    throw new IllegalArgumentException("宽高超过 65536");
                }
                if (4L * (long) width * (long) height > Integer.MAX_VALUE) {
                    throw new IllegalArgumentException("像素缓冲超过 2 GiB");
                }
            } else if ("IDAT".equals(type)) {
                if (!sawHeader) {
                    throw new IllegalArgumentException("IDAT 早于 IHDR");
                }
                idat.write(png, dataStart, dataLength);
            } else if ("IEND".equals(type)) {
                if (dataLength != 0) {
                    throw new IllegalArgumentException("IEND 长度必须为 0");
                }
                sawEnd = true;
            } else {
                throw new IllegalArgumentException("出现约定之外的 chunk: " + type);
            }
            position = dataStart + dataLength + 4;
            if (sawEnd) {
                break;
            }
        }

        if (!sawHeader) {
            throw new IllegalArgumentException("缺少 IHDR");
        }
        if (!sawEnd) {
            throw new IllegalArgumentException("缺少 IEND");
        }
        if (position != png.length) {
            throw new IllegalArgumentException("IEND 之后还有字节");
        }
        if (idat.size() == 0) {
            throw new IllegalArgumentException("缺少 IDAT");
        }

        int stride = width * 4;
        long expectedBytes = (long) height * (1L + (long) stride);
        byte[] scanlines = inflate(idat.toByteArray(), expectedBytes);

        byte[] rgba = new byte[width * height * 4];
        boolean allFiltersZero = true;
        int source = 0;
        int destination = 0;
        for (int row = 0; row < height; row++) {
            int filter = scanlines[source++] & 0xFF;
            if (filter != 0) {
                allFiltersZero = false;
            }
            switch (filter) {
                case 0:
                    System.arraycopy(scanlines, source, rgba, destination, stride);
                    break;
                case 1:
                case 2:
                case 3:
                case 4:
                    for (int index = 0; index < stride; index++) {
                        int raw = scanlines[source + index] & 0xFF;
                        int left = index >= 4 ? rgba[destination + index - 4] & 0xFF : 0;
                        int up = row > 0 ? rgba[destination + index - stride] & 0xFF : 0;
                        int corner = (index >= 4 && row > 0)
                                ? rgba[destination + index - stride - 4] & 0xFF : 0;
                        int value;
                        switch (filter) {
                            case 1:
                                value = raw + left;
                                break;
                            case 2:
                                value = raw + up;
                                break;
                            case 3:
                                value = raw + ((left + up) >>> 1);
                                break;
                            default:
                                value = raw + paeth(left, up, corner);
                                break;
                        }
                        rgba[destination + index] = (byte) value;
                    }
                    break;
                default:
                    throw new IllegalArgumentException("非法过滤器字节: " + filter);
            }
            source += stride;
            destination += stride;
        }
        return new Decoded(width, height, rgba, types, allFiltersZero);
    }

    private static int paeth(int left, int up, int corner) {
        int estimate = left + up - corner;
        int leftDistance = Math.abs(estimate - left);
        int upDistance = Math.abs(estimate - up);
        int cornerDistance = Math.abs(estimate - corner);
        if (leftDistance <= upDistance && leftDistance <= cornerDistance) {
            return left;
        }
        return upDistance <= cornerDistance ? up : corner;
    }

    private static byte[] inflate(byte[] compressed, long expectedBytes) {
        Inflater inflater = new Inflater();
        try {
            inflater.setInput(compressed);
            ByteArrayOutputStream out = new ByteArrayOutputStream((int) Math.min(expectedBytes, 1 << 20));
            byte[] buffer = new byte[8192];
            while (!inflater.finished()) {
                int written = inflater.inflate(buffer);
                if (written == 0) {
                    if (inflater.needsDictionary()) {
                        throw new IllegalArgumentException("zlib 流需要预置字典");
                    }
                    if (inflater.needsInput()) {
                        throw new IllegalArgumentException("zlib 流提前结束");
                    }
                    continue;
                }
                out.write(buffer, 0, written);
                if ((long) out.size() > expectedBytes) {
                    throw new IllegalArgumentException("解压后字节数超出期望");
                }
            }
            if (inflater.getRemaining() != 0) {
                throw new IllegalArgumentException("zlib 流之后还有多余字节");
            }
            if ((long) out.size() != expectedBytes) {
                throw new IllegalArgumentException(
                        "解压后 " + out.size() + " 字节，期望 " + expectedBytes);
            }
            return out.toByteArray();
        } catch (DataFormatException failure) {
            throw new IllegalArgumentException("zlib 流损坏: " + failure.getMessage(), failure);
        } finally {
            inflater.end();
        }
    }

    private static long readUnsignedInt(byte[] source, int offset) {
        return ((long) (source[offset] & 0xFF) << 24)
                | ((long) (source[offset + 1] & 0xFF) << 16)
                | ((long) (source[offset + 2] & 0xFF) << 8)
                | (long) (source[offset + 3] & 0xFF);
    }
}
