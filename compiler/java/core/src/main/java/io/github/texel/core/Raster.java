package io.github.texel.core;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/**
 * 光栅化与合成（规范 §5 与 §6）。
 *
 * <p>三条不可协商的性质：
 *
 * <ul>
 *   <li><b>不许有半覆盖像素</b>：任何 op 要么把某像素整体设成某 slot 的四字节，
 *       要么完全不动它。没有插值、没有取样、没有抗锯齿。</li>
 *   <li><b>越界一律裁剪，不报错</b>：落在画布外的写入被丢弃；矩形先求交集再逐行写。</li>
 *   <li><b>颜色运算全整数</b>：只做加、减、乘、比较与整数除法，唯一取整规则是
 *       {@code (v + 127) / 255}，且 alpha 用<b>未取整</b>的 {@code aNum} 归一——
 *       否则通道会算出 256（§6.3 的上界证明）。</li>
 * </ul>
 */
final class Raster {

    private Raster() {
    }

    /** 渲染整份文档：逐层在自己的透明缓冲上作画，再按 blend 合成到累加器。 */
    static byte[] render(Doc doc) {
        int width = doc.width;
        int height = doc.height;
        long pixelCount = (long) width * (long) height;
        byte[] accumulator = new byte[(int) (pixelCount * 4L)];
        byte[] layer = new byte[accumulator.length];
        Map<String, int[][]> spriteCache = new HashMap<String, int[][]>();

        for (int index = 0; index < doc.layers.length; index++) {
            Doc.Layer current = doc.layers[index];
            Arrays.fill(layer, (byte) 0);
            for (Doc.Op op : current.ops) {
                apply(layer, width, height, doc, op, spriteCache);
            }
            compose(accumulator, layer, current.blend);
        }
        return accumulator;
    }

    private static void apply(byte[] buffer, int width, int height, Doc doc, Doc.Op op,
            Map<String, int[][]> spriteCache) {
        switch (op.kind) {
            case "fill":
                fillRect(buffer, width, height, 0, 0, width, height, doc, op.slot);
                break;
            case "rect":
                fillRect(buffer, width, height, op.x, op.y, op.w, op.h, doc, op.slot);
                break;
            case "pixel":
                setPixel(buffer, width, height, op.x, op.y, doc, op.slot);
                break;
            case "line":
                drawLine(buffer, width, height, doc, op);
                break;
            case "grid":
                drawGrid(buffer, width, height, doc, op);
                break;
            case "mirror":
                drawMirror(buffer, width, height, op);
                break;
            case "erase":
                fillRect(buffer, width, height, op.x, op.y, op.w, op.h, doc, 0);
                break;
            case "stamp":
                if (op.assetRgba != null) {
                    drawAssetStamp(buffer, width, height, op);
                } else {
                    drawStamp(buffer, width, height, doc, op, spriteCache);
                }
                break;
            default:
                throw new IllegalStateException("校验器应已拒绝未知 op: " + op.kind);
        }
    }

    // ------------------------------------------------------------ 矩形与像素

    /**
     * 右开、下开的矩形填充，越界裁剪（§5.0）。
     *
     * <p>交集用 {@code long} 算：{@code x + w} 在合法输入下最大 131072，int 装得下，
     * 但这里不靠"合法输入"这个前提——校验器与渲染器之间还夹着一个别人可能改坏的
     * 中间层。
     */
    private static void fillRect(byte[] buffer, int width, int height,
            int x, int y, int w, int h, Doc doc, int slot) {
        if (w <= 0 || h <= 0) {
            return;
        }
        long left = Math.max(0L, (long) x);
        long top = Math.max(0L, (long) y);
        long right = Math.min((long) width, (long) x + (long) w);
        long bottom = Math.min((long) height, (long) y + (long) h);
        if (left >= right || top >= bottom) {
            return;
        }
        byte[] pixel = pixelOf(doc, slot);
        for (int row = (int) top; row < (int) bottom; row++) {
            int offset = (row * width + (int) left) * 4;
            for (int column = (int) left; column < (int) right; column++) {
                buffer[offset] = pixel[0];
                buffer[offset + 1] = pixel[1];
                buffer[offset + 2] = pixel[2];
                buffer[offset + 3] = pixel[3];
                offset += 4;
            }
        }
    }

    private static void setPixel(byte[] buffer, int width, int height,
            int x, int y, Doc doc, int slot) {
        if (x < 0 || x >= width || y < 0 || y >= height) {
            return;
        }
        byte[] pixel = pixelOf(doc, slot);
        int offset = (y * width + x) * 4;
        buffer[offset] = pixel[0];
        buffer[offset + 1] = pixel[1];
        buffer[offset + 2] = pixel[2];
        buffer[offset + 3] = pixel[3];
    }

    private static byte[] pixelOf(Doc doc, int slot) {
        if (slot == 0) {
            return ZERO;
        }
        return doc.palette[slot - 1];
    }

    private static final byte[] ZERO = new byte[4];

    // ------------------------------------------------------------ line

    /**
     * 规范 §5.4 逐字符固定的 Bresenham：含两端点，两个"若"各自独立判断。
     *
     * <p><b>不做端点交换归一</b>：同一对端点反向画在部分斜率下给出不同像素集
     * （实测 {@code (2,6)->(10,5)} 与反向点数相同但点集不同）。交换端点就是改变
     * 像素，会让两个实现分叉。
     *
     * <p>越界点是<b>逐点丢弃</b>而不是先做解析裁剪：Liang-Barsky 之类会得到与逐点
     * 裁剪不同的像素集合（§5.0 明写禁止）。
     */
    private static void drawLine(byte[] buffer, int width, int height, Doc doc, Doc.Op op) {
        int x0 = op.x0;
        int y0 = op.y0;
        int x1 = op.x1;
        int y1 = op.y1;
        int dx = Math.abs(x1 - x0);
        int sx = (x0 < x1) ? 1 : -1;
        int dy = -Math.abs(y1 - y0);
        int sy = (y0 < y1) ? 1 : -1;
        int err = dx + dy;
        int x = x0;
        int y = y0;
        byte[] pixel = pixelOf(doc, op.slot);
        while (true) {
            if (x >= 0 && x < width && y >= 0 && y < height) {
                int offset = (y * width + x) * 4;
                buffer[offset] = pixel[0];
                buffer[offset + 1] = pixel[1];
                buffer[offset + 2] = pixel[2];
                buffer[offset + 3] = pixel[3];
            }
            if (x == x1 && y == y1) {
                return;
            }
            int e2 = 2 * err;
            if (e2 >= dy) {
                err += dy;
                x += sx;
            }
            if (e2 <= dx) {
                err += dx;
                y += sy;
            }
        }
    }

    // ------------------------------------------------------------ grid / stamp

    private static void drawGrid(byte[] buffer, int width, int height, Doc doc, Doc.Op op) {
        int tokenWidth = Validator.tokenWidth(doc.encoding);
        for (int rowIndex = 0; rowIndex < op.rows.length; rowIndex++) {
            int y = op.y + rowIndex;
            if (y < 0 || y >= height) {
                continue;
            }
            String row = op.rows[rowIndex];
            int columns = row.length() / tokenWidth;
            for (int columnIndex = 0; columnIndex < columns; columnIndex++) {
                int x = op.x + columnIndex;
                if (x < 0 || x >= width) {
                    continue;
                }
                int slot = Validator.decodeSlot(row, columnIndex * tokenWidth, tokenWidth);
                byte[] pixel = pixelOf(doc, slot);
                int offset = (y * width + x) * 4;
                buffer[offset] = pixel[0];
                buffer[offset + 1] = pixel[1];
                buffer[offset + 2] = pixel[2];
                buffer[offset + 3] = pixel[3];
            }
        }
    }

    private static void drawStamp(byte[] buffer, int width, int height, Doc doc, Doc.Op op,
            Map<String, int[][]> spriteCache) {
        int[][] slots = spriteCache.get(op.sprite);
        if (slots == null) {
            slots = decodeSprite(doc, op.sprite);
            spriteCache.put(op.sprite, slots);
        }
        int rows = slots.length;
        int columns = slots[0].length;
        for (int rowIndex = 0; rowIndex < rows; rowIndex++) {
            int destinationRow = op.flipY ? (rows - 1 - rowIndex) : rowIndex;
            int y = op.y + destinationRow;
            if (y < 0 || y >= height) {
                continue;
            }
            for (int columnIndex = 0; columnIndex < columns; columnIndex++) {
                int destinationColumn = op.flipX ? (columns - 1 - columnIndex) : columnIndex;
                int x = op.x + destinationColumn;
                if (x < 0 || x >= width) {
                    continue;
                }
                byte[] pixel = pixelOf(doc, slots[rowIndex][columnIndex]);
                int offset = (y * width + x) * 4;
                buffer[offset] = pixel[0];
                buffer[offset + 1] = pixel[1];
                buffer[offset + 2] = pixel[2];
                buffer[offset + 3] = pixel[3];
            }
        }
    }

    /**
     * asset stamp：把被引用文档的最终位图整图贴上去（规范 §5.3 / §8.4）。
     *
     * <p>翻转与裁剪的语义与 sprite stamp **逐像素一致**（先 X 后 Y、逐点越界裁剪、整像素写入），
     * 唯一区别是源像素来自已解析的 RGBA 缓冲区，而不是本文档的 palette slot。
     */
    private static void drawAssetStamp(byte[] buffer, int width, int height, Doc.Op op) {
        int rows = op.assetHeight;
        int columns = op.assetWidth;
        for (int rowIndex = 0; rowIndex < rows; rowIndex++) {
            int destinationRow = op.flipY ? (rows - 1 - rowIndex) : rowIndex;
            int y = op.y + destinationRow;
            if (y < 0 || y >= height) {
                continue;
            }
            for (int columnIndex = 0; columnIndex < columns; columnIndex++) {
                int destinationColumn = op.flipX ? (columns - 1 - columnIndex) : columnIndex;
                int x = op.x + destinationColumn;
                if (x < 0 || x >= width) {
                    continue;
                }
                int source = (rowIndex * columns + columnIndex) * 4;
                int offset = (y * width + x) * 4;
                buffer[offset] = op.assetRgba[source];
                buffer[offset + 1] = op.assetRgba[source + 1];
                buffer[offset + 2] = op.assetRgba[source + 2];
                buffer[offset + 3] = op.assetRgba[source + 3];
            }
        }
    }

    private static int[][] decodeSprite(Doc doc, String name) {
        // 校验器保证 sprite 存在且行等长（§11.1 阶段 9 报了 PXL005 的话根本到不了这里）。
        int tokenWidth = Validator.tokenWidth(doc.encoding);
        String[] rows = doc.sprites.get(name);
        int[][] slots = new int[rows.length][];
        for (int rowIndex = 0; rowIndex < rows.length; rowIndex++) {
            String row = rows[rowIndex];
            int columns = row.length() / tokenWidth;
            int[] decoded = new int[columns];
            for (int columnIndex = 0; columnIndex < columns; columnIndex++) {
                decoded[columnIndex] = Validator.decodeSlot(row, columnIndex * tokenWidth, tokenWidth);
            }
            slots[rowIndex] = decoded;
        }
        return slots;
    }

    // ------------------------------------------------------------ mirror

    /**
     * {@code mirror}：把 {@code from} 内的**已绘内容**翻转后写到 {@code to}。
     *
     * <p>两条容易搞错的地方：
     *
     * <ul>
     *   <li><b>快照语义</b>：源区域必须在任何写入发生前整体读出，否则 {@code from}
     *       与目标重叠时结果依赖写入顺序（§5.6）。</li>
     *   <li><b>读取侧不裁剪</b>：{@code from} 落在图层之外的像素按透明 slot 0 参与
     *       快照，因此会向目标写出 0。若改成"越界源跳过"，同一份文档会渲染出不同
     *       像素——两种读法在文字上都讲得通，所以规范把它钉死成全函数。</li>
     * </ul>
     *
     * <p>快照只保存<b>会落到画布上</b>的那部分目标像素：{@code fromW × fromH} 在合法
     * 文档里可以到 65536 × 65536（4G 项），而目标侧被画布裁剪后不超过 2^22 项。
     * 按目标侧分配就既正确又不会为一份小画布文档申请几十 GB。
     */
    private static void drawMirror(byte[] buffer, int width, int height, Doc.Op op) {
        long destinationLeft = Math.max(0L, (long) op.x);
        long destinationTop = Math.max(0L, (long) op.y);
        long destinationRight = Math.min((long) width, (long) op.x + (long) op.fromW);
        long destinationBottom = Math.min((long) height, (long) op.y + (long) op.fromH);
        if (destinationLeft >= destinationRight || destinationTop >= destinationBottom) {
            return;
        }
        int destWidth = (int) (destinationRight - destinationLeft);
        int destHeight = (int) (destinationBottom - destinationTop);
        byte[] snapshot = new byte[destWidth * destHeight * 4];

        int cursor = 0;
        for (long destinationY = destinationTop; destinationY < destinationBottom; destinationY++) {
            int j = (int) (destinationY - op.y);
            long sourceY = (long) op.fromY + (op.flipY ? (op.fromH - 1 - j) : j);
            for (long destinationX = destinationLeft; destinationX < destinationRight; destinationX++) {
                int i = (int) (destinationX - op.x);
                long sourceX = (long) op.fromX + (op.flipX ? (op.fromW - 1 - i) : i);
                if (sourceX >= 0 && sourceX < width && sourceY >= 0 && sourceY < height) {
                    int offset = ((int) sourceY * width + (int) sourceX) * 4;
                    snapshot[cursor] = buffer[offset];
                    snapshot[cursor + 1] = buffer[offset + 1];
                    snapshot[cursor + 2] = buffer[offset + 2];
                    snapshot[cursor + 3] = buffer[offset + 3];
                }
                // 越界源保持全零 = 透明 slot 0。
                cursor += 4;
            }
        }

        cursor = 0;
        for (long destinationY = destinationTop; destinationY < destinationBottom; destinationY++) {
            int offset = ((int) destinationY * width + (int) destinationLeft) * 4;
            for (int column = 0; column < destWidth; column++) {
                buffer[offset] = snapshot[cursor];
                buffer[offset + 1] = snapshot[cursor + 1];
                buffer[offset + 2] = snapshot[cursor + 2];
                buffer[offset + 3] = snapshot[cursor + 3];
                offset += 4;
                cursor += 4;
            }
        }
    }

    // ------------------------------------------------------------ 合成

    private static void compose(byte[] accumulator, byte[] layer, String blend) {
        if ("normal".equals(blend)) {
            composeNormal(accumulator, layer);
        } else if ("multiply".equals(blend)) {
            composeMultiply(accumulator, layer);
        } else {
            composeMask(accumulator, layer);
        }
    }

    private static void composeNormal(byte[] destination, byte[] source) {
        for (int offset = 0; offset < source.length; offset += 4) {
            int sr = source[offset] & 0xFF;
            int sg = source[offset + 1] & 0xFF;
            int sb = source[offset + 2] & 0xFF;
            int sa = source[offset + 3] & 0xFF;
            int dr = destination[offset] & 0xFF;
            int dg = destination[offset + 1] & 0xFF;
            int db = destination[offset + 2] & 0xFF;
            int da = destination[offset + 3] & 0xFF;

            int transparency = 255 - sa;
            int aNum = sa * 255 + da * transparency;
            if (aNum == 0) {
                // 上下都不可见：谁有颜色就留谁的（§7 第 3 条）。
                if ((sr | sg | sb | sa) == 0) {
                    destination[offset + 3] = 0;
                } else {
                    destination[offset] = (byte) sr;
                    destination[offset + 1] = (byte) sg;
                    destination[offset + 2] = (byte) sb;
                    destination[offset + 3] = 0;
                }
                continue;
            }
            int half = aNum / 2;
            int outR = (sr * sa * 255 + dr * da * transparency + half) / aNum;
            int outG = (sg * sa * 255 + dg * da * transparency + half) / aNum;
            int outB = (sb * sa * 255 + db * da * transparency + half) / aNum;
            destination[offset] = (byte) outR;
            destination[offset + 1] = (byte) outG;
            destination[offset + 2] = (byte) outB;
            destination[offset + 3] = (byte) ((aNum + 127) / 255);
        }
    }

    private static void composeMultiply(byte[] destination, byte[] source) {
        for (int offset = 0; offset < source.length; offset += 4) {
            int sr = source[offset] & 0xFF;
            int sg = source[offset + 1] & 0xFF;
            int sb = source[offset + 2] & 0xFF;
            int sa = source[offset + 3] & 0xFF;
            int dr = destination[offset] & 0xFF;
            int dg = destination[offset + 1] & 0xFF;
            int db = destination[offset + 2] & 0xFF;
            int da = destination[offset + 3] & 0xFF;

            int mr = div255(sr * dr);
            int mg = div255(sg * dg);
            int mb = div255(sb * db);
            int transparency = 255 - sa;
            int aNum = sa * 255 + da * transparency;
            if (aNum == 0) {
                if ((sr | sg | sb | sa) == 0) {
                    destination[offset + 3] = 0;
                } else {
                    // 底下没有东西可乘，源像素原样透过（§6.4）。
                    destination[offset] = (byte) sr;
                    destination[offset + 1] = (byte) sg;
                    destination[offset + 2] = (byte) sb;
                    destination[offset + 3] = 0;
                }
                continue;
            }
            int half = aNum / 2;
            int outR = (mr * sa * 255 + dr * da * transparency + half) / aNum;
            int outG = (mg * sa * 255 + dg * da * transparency + half) / aNum;
            int outB = (mb * sa * 255 + db * da * transparency + half) / aNum;
            destination[offset] = (byte) outR;
            destination[offset + 1] = (byte) outG;
            destination[offset + 2] = (byte) outB;
            destination[offset + 3] = (byte) ((aNum + 127) / 255);
        }
    }

    private static void composeMask(byte[] destination, byte[] source) {
        for (int offset = 0; offset < source.length; offset += 4) {
            int sr = source[offset] & 0xFF;
            int sg = source[offset + 1] & 0xFF;
            int sb = source[offset + 2] & 0xFF;
            int sa = source[offset + 3] & 0xFF;
            if ((sr | sg | sb | sa) == 0) {
                continue;   // 透明 slot 源：下层完全不变
            }
            int da = destination[offset + 3] & 0xFF;
            destination[offset + 3] = (byte) div255(da * sa);
        }
    }

    /** {@code div255(v) = (v + 127) / 255}，整数除法（§6.2）。 */
    private static int div255(int value) {
        return (value + 127) / 255;
    }
}
