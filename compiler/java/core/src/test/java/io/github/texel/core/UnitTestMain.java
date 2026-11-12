package io.github.texel.core;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * core 的单元测试（无第三方框架）。
 *
 * <p>覆盖面刻意与 `spec/conformance` 互补：语料钉的是"规范条文可判定"，这里钉的是
 * 语料到不了的地方——阶段序的组合、数字词法的边界、快照语义的内部实现约束、
 * PNG 编解码的结构细节。
 */
public final class UnitTestMain {

    private static final String FILL_SLOT_1 = "[{\"op\":\"fill\",\"slot\":1}]";
    private static final String FILL_SLOT_2 = "[{\"op\":\"fill\",\"slot\":2}]";
    private static final String CLEAR_LAYER = "[{\"op\":\"fill\",\"slot\":0}]";
    private static final String EMPTY_LAYER = "[]";

    private static final List<String> FAILURES = new ArrayList<String>();
    private static int executed;

    private UnitTestMain() {
    }

    public static void main(String[] args) {
        run("json.duplicate-key", new Runnable() {
            public void run() {
                Assert.expectCode(Codes.PXL001,
                        bytes("{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                                + "\"width\":1,\"width\":1,\"height\":1,\"palette\":[],"
                                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}"),
                        "顶层重复 width");
            }
        });

        run("json.invalid", new Runnable() {
            public void run() {
                Assert.expectCode(Codes.PXL001, bytes("{\"format\":\"PXL\",,\"version\":1}"), "多余逗号");
                Assert.expectCode(Codes.PXL001, bytes("{\"a\":}"), "缺少值");
                Assert.expectCode(Codes.PXL001, bytes(""), "空文档");
                Assert.expectCode(Codes.PXL001, bytes("[]"), "顶层是数组");
            }
        });

        run("json.lenient-forms", new Runnable() {
            public void run() {
                parse("\uFEFF{\r\n  \"format\" : \"PXL\",\r\n"
                        + "\t\"version\": 1.0,\r\n\"encoding\":\"char\",\"width\":2.0,\r\n"
                        + "\"height\": 2,\"palette\":[\"FF0000FF\"],\r\n"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\","
                        + "\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}   ");
            }
        });

        run("json.number-precision", new Runnable() {
            public void run() {
                // 双精度会把它舍成 1.0 从而误接受；规范要求按词法精确判断。
                Assert.expectCode(Codes.PXL002, doc("1.0000000000000001", "1"), "不精确的小数");
                Assert.expectCode(Codes.PXL002, doc("65536.0000000000001", "1"), "越界前先判精确");
                Assert.expectCode(Codes.PXL002, doc("1e-1", "1"), "1e-1 不是整数");
                Assert.expectCode(Codes.PXL002, doc("1.5", "1"), "1.5");
                Assert.expectCode(Codes.PXL002, doc("1e1000", "1"), "巨大指数");
                Assert.equal(16, parse(doc("16.0", "1")).width(), "16.0 == 16");
                Assert.equal(1000, parse(doc("1e3", "1")).width(), "1e3 == 1000");
            }
        });

        run("phase.encoding-before-token", new Runnable() {
            public void run() {
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"hex\","
                        + "\"width\":1,\"height\":1,\"palette\":[\"FF0000FF\"],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"z\"]}]}]}";
                Assert.expectCode(Codes.PXL003, bytes(document), "未知 encoding 先于 token 检查");
            }
        });

        run("phase.structure-before-encoding", new Runnable() {
            public void run() {
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"hex\","
                        + "\"width\":1,\"height\":1,\"palette\":[],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[]}]}]}";
                Assert.expectCode(Codes.PXL001, bytes(document), "空行数组先于 encoding 检查");
            }
        });

        run("phase.codepoint-length", new Runnable() {
            public void run() {
                // 按码点是 1 vs 2 不等长（PXL001）；按 UTF-16 单元是 2 vs 2，会滑到 PXL003。
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"hex\","
                        + "\"width\":1,\"height\":1,\"palette\":[],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"\\uD83D\\uDE00\",\"00\"]}]}]}";
                Assert.expectCode(Codes.PXL001, bytes(document), "按码点计长度");
            }
        });

        run("phase.nbsp-is-not-whitespace", new Runnable() {
            public void run() {
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                        + "\"width\":1,\"height\":1,\"palette\":[\"FF0000FF\"],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"\u00a0\"]}]}]}";
                Assert.expectCode(Codes.PXL001, bytes(document), "U+00A0 不是合法 token");
            }
        });

        run("phase.unknown-op-only-skips-op-specific", new Runnable() {
            public void run() {
                Assert.expectCode(Codes.PXL004, layerOp("{\"op\":\"circle\",\"x\":99999}"),
                        "未知 op 跳过数值域");
                Assert.expectCode(Codes.PXL001, layerOp("{\"op\":\"circle\",\"x\":1,\"x\":2}"),
                        "未知 op 仍查重复键");
                Assert.expectCode(Codes.PXL004,
                        layerOp("{\"op\":\"circle\",\"grid\":[\"a\",\"bb\"]}"),
                        "未知 op 的 grid 成员不按行数组校验");
            }
        });

        run("phase.version-out-of-long-range", new Runnable() {
            public void run() {
                // 词法是整数、但超出 64 位可表示范围。只看「整数值 == 0」会把无法表示的值
                // 读成 0，于是 §12 的 fail-closed 被绕开（审查发现的实际缺陷）。
                Assert.expectCode(Codes.PXL001,
                        doc2("[\"FF0000FF\"]", "[]").replace("\"version\":1",
                                "\"version\":9223372036854775808"),
                        "version 超出 64 位");
                // §12 明写：非整数（1.5）与超 64 位（1e99999）也一律 PXL001，
                // 不走 PXL002 的数值域检查。
                Assert.expectCode(Codes.PXL001,
                        doc2("[\"FF0000FF\"]", "[]").replace("\"version\":1",
                                "\"version\":1.5"), "version 非整数");
                Assert.expectCode(Codes.PXL001,
                        doc2("[\"FF0000FF\"]", "[]").replace("\"version\":1",
                                "\"version\":1e99999"), "version 巨大指数");
                // 对照：等值字面量仍然接受。
                parse("{\"format\":\"PXL\",\"version\":1.0,\"encoding\":\"char\","
                        + "\"width\":1,\"height\":1,\"palette\":[],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}");
            }
        });

        run("phase.sprite-token-count", new Runnable() {
            public void run() {
                StringBuilder row = new StringBuilder(65537);
                for (int index = 0; index < 65537; index++) {
                    row.append('0');
                }
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                        + "\"width\":1,\"height\":1,\"palette\":[\"FF0000FF\"],"
                        + "\"sprites\":{\"s\":[\"" + row + "\"]},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}";
                // §3.4 的 token 数上界适用于**凡出现 token 行数组的地方**：只查 grid
                // 会让 sprite 绕过它，并与独立实现分叉。
                Assert.expectCode(Codes.PXL002, document, "sprite 单行 65537 token");
            }
        });

        run("phase.tier-boundaries", new Runnable() {
            public void run() {
                Assert.equal("char", Validator.deriveEncoding(61), "61 应为 char");
                Assert.equal("index", Validator.deriveEncoding(62), "62 应为 index");
                Assert.equal("index", Validator.deriveEncoding(255), "255 应为 index");
                Assert.equal("index4", Validator.deriveEncoding(256), "256 应为 index4");
                Assert.equal("index4", Validator.deriveEncoding(65535), "65535 应为 index4");
            }
        });

        run("phase.palette-capacity-skips-phase-7", new Runnable() {
            public void run() {
                StringBuilder palette = new StringBuilder();
                for (int index = 0; index < 65536; index++) {
                    if (index > 0) {
                        palette.append(',');
                    }
                    palette.append('"').append(Assert.hex(new byte[] {
                        (byte) (index >>> 16), (byte) (index >>> 8), (byte) index, (byte) 0xFF,
                    })).append('"');
                }
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                        + "\"width\":1,\"height\":1,\"palette\":[" + palette + "],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}";
                // n >= 65536 时阶段 7 无定义被跳过，必须由阶段 9 报 PXL005。
                Assert.expectCode(Codes.PXL005, bytes(document), "palette 超容量");
            }
        });

        run("phase.resource-64bit", new Runnable() {
            public void run() {
                String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                        + "\"width\":65536,\"height\":65536,\"palette\":[],"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}";
                Assert.expectCode(Codes.PXL002, bytes(document), "面积用 64 位算不会回绕成 0");
            }
        });

        run("phase.slot-checks", new Runnable() {
            public void run() {
                Assert.expectCode(Codes.PXL005, doc2("[\"FF0000FF\"]",
                        "[{\"op\":\"fill\",\"slot\":2}]"), "fill slot 越过 palette");
                Assert.expectCode(Codes.PXL005, doc2("[\"FF0000FF\"]",
                        "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"2\"]}]"), "token slot 越界");
                Assert.expectCode(Codes.PXL005,
                        "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":1,"
                                + "\"height\":1,\"palette\":[],\"sprites\":{\"s\":[\"0\"]},"
                                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}",
                        "未引用 sprite 的 slot 也要校验");
            }
        });

        run("blend.normal-a128", new Runnable() {
            public void run() {
                Assert.equal("80007FFF", stack("normal", FILL_SLOT_1, "normal", FILL_SLOT_2,
                        "0000FFFF", "FF000080"), "§6.6a");
            }
        });

        run("blend.multiply-a128", new Runnable() {
            public void run() {
                Assert.equal("0D2219FF", stack("normal", FILL_SLOT_1, "multiply", FILL_SLOT_2,
                        "112233FF", "80FF0080"), "§6.6b");
            }
        });

        run("blend.mask-a128", new Runnable() {
            public void run() {
                Assert.equal("11223380", stack("normal", FILL_SLOT_1, "mask", FILL_SLOT_2,
                        "112233FF", "00FF0080"), "§6.6c");
                Assert.equal("11223300", stack("normal", FILL_SLOT_1, "mask", FILL_SLOT_2,
                        "112233FF", "00FF0000"), "§6.6d：不得折叠成 00000000");
            }
        });

        run("blend.transparent-slot-identity", new Runnable() {
            public void run() {
                // 透明源用 `fill slot 0` 表达：palette 里放 00000000 是非法的（§3.2 / PXL005）。
                Assert.equal("FFFFFF00", stack("normal", FILL_SLOT_1, "normal", CLEAR_LAYER,
                        "FFFFFF00"), "§6.6e");
                Assert.equal("FFFFFF00", stack("normal", FILL_SLOT_1, "multiply", CLEAR_LAYER,
                        "FFFFFF00"), "multiply 下透明源恒等");
                Assert.equal("FFFFFF00", stack("normal", FILL_SLOT_1, "mask", CLEAR_LAYER,
                        "FFFFFF00"), "mask 下透明源恒等");
            }
        });

        run("blend.invisible-colour-propagation", new Runnable() {
            public void run() {
                Assert.equal("FFFFFF00", stack("normal", EMPTY_LAYER, "normal", FILL_SLOT_1,
                        "FFFFFF00"), "§6.6f");
                Assert.equal("FF000000", stack("normal", FILL_SLOT_1, "normal", FILL_SLOT_2,
                        "FFFFFF00", "FF000000"), "§7：两层不同颜色的不可见像素取上层");
            }
        });

        run("blend.aNum-normalisation", new Runnable() {
            public void run() {
                Assert.equal("FFFFFF80", stack("normal", FILL_SLOT_1, "normal", FILL_SLOT_2,
                        "FFFFFF80", "FFFFFF01"), "§6.6g");
                Assert.equal("81818180", stack("normal", FILL_SLOT_1, "normal", FILL_SLOT_2,
                        "81818180", "81818101"), "blend-formula-818181-normal");
                Assert.equal("81818180", stack("normal", FILL_SLOT_1, "multiply", FILL_SLOT_2,
                        "81818180", "FFFFFF01"), "blend-formula-818181-multiply");
            }
        });

        run("blend.multiply-is-not-covering", new Runnable() {
            public void run() {
                Assert.equal("000000FF", stack("multiply", FILL_SLOT_1, "normal", EMPTY_LAYER,
                        "FF0000FF"), "multiply 底层乘成黑");
            }
        });

        run("blend.mask-alters-alpha", new Runnable() {
            public void run() {
                Assert.equal("44556600", stack("normal", FILL_SLOT_1, "mask", FILL_SLOT_2,
                        "445566FF", "11223300"), "mask 改下层 alpha");
                Assert.equal("00000000", stack("mask", FILL_SLOT_1, "normal", EMPTY_LAYER,
                        "11223300"), "mask 作底层不保留源 RGB");
            }
        });

        run("op.pixel-overwrite-and-clip", new Runnable() {
            public void run() {
                PxlDocumentBuilder builder = PxlDocumentBuilder.of(2, 2,
                        "[\"FF0000FF\",\"00FF00FF\"]");
                builder.op("{\"op\":\"pixel\",\"x\":0,\"y\":0,\"slot\":1}");
                builder.op("{\"op\":\"pixel\",\"x\":0,\"y\":0,\"slot\":2}");
                builder.op("{\"op\":\"pixel\",\"x\":9,\"y\":9,\"slot\":1}");
                String hex = Assert.hex(builder.parse().renderRgba());
                Assert.equal("00FF00FF" + "00000000" + "00000000" + "00000000", hex,
                        "后写覆盖先写，越界为空操作");
            }
        });

        run("op.line-endpoints-and-connectivity", new Runnable() {
            public void run() {
                PxlDocumentBuilder builder = PxlDocumentBuilder.of(16, 16, "[\"FF0000FF\"]");
                builder.op("{\"op\":\"line\",\"x0\":2,\"y0\":6,\"x1\":10,\"y1\":5,\"slot\":1}");
                byte[] rgba = builder.parse().renderRgba();
                int painted = 0;
                for (int index = 3; index < rgba.length; index += 4) {
                    if (rgba[index] != 0) {
                        painted++;
                    }
                }
                Assert.equal(9, painted, "点数 = max(|dx|,|dy|)+1");
                Assert.equal((byte) 0xFF, rgba[(6 * 16 + 2) * 4 + 3], "起点被写入");
                Assert.equal((byte) 0xFF, rgba[(5 * 16 + 10) * 4 + 3], "终点被写入");
            }
        });

        run("op.line-not-reverse-symmetric", new Runnable() {
            public void run() {
                byte[] forward = lineBytes(2, 6, 10, 5);
                byte[] backward = lineBytes(10, 5, 2, 6);
                Assert.isFalse(Arrays.equals(forward, backward),
                        "(2,6)->(10,5) 与反向的像素集合不同：不做端点交换归一");
            }
        });

        run("op.mirror-snapshot-and-out-of-bounds-read", new Runnable() {
            public void run() {
                PxlDocumentBuilder builder = PxlDocumentBuilder.of(3, 1,
                        "[\"FF0000FF\",\"00FF00FF\",\"0000FFFF\"]");
                builder.op("{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"012\"]}");
                builder.op("{\"op\":\"mirror\",\"from\":{\"x\":0,\"y\":0,\"w\":3,\"h\":1},"
                        + "\"to\":{\"x\":0,\"y\":0},\"flipX\":true}");
                Assert.equal("0000FFFF00FF00FFFF0000FF", Assert.hex(builder.parse().renderRgba()),
                        "重叠镜像用快照，不依赖写入顺序");

                PxlDocumentBuilder oob = PxlDocumentBuilder.of(2, 1, "[\"FF0000FF\"]");
                oob.op("{\"op\":\"fill\",\"slot\":1}");
                oob.op("{\"op\":\"mirror\",\"from\":{\"x\":5,\"y\":0,\"w\":2,\"h\":1},"
                        + "\"to\":{\"x\":0,\"y\":0}}");
                Assert.equal("0000000000000000", Assert.hex(oob.parse().renderRgba()),
                        "越界源按透明 slot 0 参与，目标被写 0");
            }
        });

        run("op.stamp-flips", new Runnable() {
            public void run() {
                String sprites = "{\"t\":[\"01\",\"23\"]}";
                assertStamp(sprites, "\"flipX\":true", "2143");
                assertStamp(sprites, "\"flipY\":true", "3412");
                assertStamp(sprites, "\"flipX\":true,\"flipY\":true", "4321");
            }
        });

        run("op.erase-clip", new Runnable() {
            public void run() {
                PxlDocumentBuilder builder = PxlDocumentBuilder.of(2, 2, "[\"FF0000FF\"]");
                builder.op("{\"op\":\"fill\",\"slot\":1}");
                builder.op("{\"op\":\"erase\",\"x\":-1,\"y\":-1,\"w\":2,\"h\":2}");
                Assert.equal("00000000" + "FF0000FF" + "FF0000FF" + "FF0000FF",
                        Assert.hex(builder.parse().renderRgba()), "erase 逐点裁剪");
            }
        });

        run("op.extreme-coordinates-terminate", new Runnable() {
            public void run() {
                PxlDocumentBuilder builder = PxlDocumentBuilder.of(16, 16, "[\"FF0000FF\"]");
                builder.op("{\"op\":\"line\",\"x0\":-65536,\"y0\":-65536,"
                        + "\"x1\":65536,\"y1\":65536,\"slot\":1}");
                byte[] rgba = builder.parse().renderRgba();
                Assert.equal(16 * 16 * 4, rgba.length, "极限坐标仍然终止");
            }
        });

        run("png.roundtrip-and-determinism", new Runnable() {
            public void run() {
                byte[] rgba = new byte[4 * 4 * 4];
                for (int index = 0; index < rgba.length; index++) {
                    rgba[index] = (byte) (index * 7);
                }
                byte[] first = PngEncoder.encode(4, 4, rgba);
                byte[] second = PngEncoder.encode(4, 4, rgba);
                Assert.isTrue(Arrays.equals(first, second), "两次编码字节相同");

                PngDecoder.Decoded decoded = PngDecoder.decode(first);
                Assert.equal(4, decoded.width, "宽");
                Assert.equal(4, decoded.height, "高");
                Assert.isTrue(Arrays.equals(rgba, decoded.rgba), "像素往返一致");
                Assert.equal("[IHDR, IDAT, IEND]", decoded.chunkTypes.toString(), "chunk 序列");
                Assert.isTrue(decoded.allFiltersZero, "所有行过滤器为 0");
            }
        });

        run("png.rejects-broken-input", new Runnable() {
            public void run() {
                byte[] rgba = new byte[2 * 2 * 4];
                byte[] png = PngEncoder.encode(2, 2, rgba);
                byte[] broken = png.clone();
                broken[broken.length - 24] ^= 0x01;
                expectInvalidPng(broken, "坏 IDAT");
                expectInvalidPng(Arrays.copyOf(png, png.length - 1), "截断");
                expectInvalidPng(Arrays.copyOf(png, png.length + 1), "IEND 之后还有字节");
            }
        });

        run("png.encoder-rejects-bad-length", new Runnable() {
            public void run() {
                try {
                    PngEncoder.encode(2, 2, new byte[3]);
                    Assert.fail("长度不符应拒绝");
                } catch (IllegalArgumentException expected) {
                    // 预期
                }
            }
        });

        run("png.decoder-rejects-overflowing-size", new Runnable() {
            public void run() {
                // width = 0x40000000 时 `width * 4` 用 int 算回绕成 0，于是"精确解压长度"
                // 检查被绕过、返回 0 字节像素。解码器必须先用 long 校验尺寸。
                Assert.isFalse(decodeProbe(0x40000000, 1), "超大 width 必须被拒绝");
            }
        });

        run("codomain.v0-code-space", new Runnable() {
            public void run() {
                // 码域按规范 §11.2 定义（PXL001..PXL006），不按"core 今天产生的码"定义：
                // PXL006 是输入位图侧的码，core 不产生它，但必须把它当合法码接受。
                for (String code : Codes.knownCodes()) {
                    PxlFormatException failure = new PxlFormatException(code, "x");
                    Assert.equal(code, failure.code(), "码域必须接受 " + code);
                }
                Assert.isTrue(Codes.isKnown(Codes.PXL006), "PXL006 属于 §11.2 码空间");
                try {
                    new PxlFormatException("BOGUS", "x");
                    Assert.fail("非法码必须抛 IllegalArgumentException");
                } catch (IllegalArgumentException expected) {
                    Assert.isTrue(expected.getMessage() != null
                            && expected.getMessage().contains("BOGUS"),
                            "非法码要出现在消息里");
                }
            }
        });

        run("phase.doc-bytes-before-parse", new Runnable() {
            public void run() {
                // 64 MiB 上限必须在**解析之前**判：否则 67,129,581 字节的文档会先在
                // JSON 对象树上 OOM，上限形同虚设（实测 -Xmx128m/256m/512m 全 OOM）。
                int size = (int) Validator.MAX_DOC_BYTES + 1;
                byte[] huge = new byte[size];
                java.util.Arrays.fill(huge, (byte) ' ');
                Assert.expectCode(Codes.PXL002, huge, "超 64 MiB 直接 PXL002");
            }
        });

        run("phase.raster-work-budget", new Runnable() {
            public void run() {
                // 面积、图层数、每层 ops 三个上限各自合法，但累计像素写入超预算。
                String one = "{\"op\":\"rect\",\"x\":0,\"y\":0,\"w\":2048,\"h\":2048,"
                        + "\"slot\":1}";
                String two = "[" + one + "," + one + "]";
                Assert.expectCode(Codes.PXL002, bigDoc(two), "两条全屏 rect 超 2^22 像素写入");
                // 对照：一条恰好等于 2^22，必须接受（不渲染，只解析）。
                parse(bigDoc("[" + one + "]"));
            }
        });

        run("num.long-minimum-is-representable", new Runnable() {
            public void run() {
                Num.Info minimum = Num.analyze("-9223372036854775808");
                Assert.isTrue(minimum.integral, "MIN 是整数");
                Assert.isTrue(minimum.inLongRange, "MIN 在 long 范围内");
                Assert.equal(Long.MIN_VALUE, minimum.value, "MIN 的值");
                Assert.isFalse(Num.analyze("-9223372036854775809").inLongRange, "MIN-1 越界");
            }
        });

        report();
    }

    // ---------------------------------------------------------------- 辅助

    /** 手工拼一张 PNG（只用于探针）。返回 true 表示解码器接受了它。 */
    private static boolean decodeProbe(int width, int height) {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] signature = {(byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
        out.write(signature, 0, signature.length);
        byte[] header = new byte[13];
        header[0] = (byte) (width >>> 24);
        header[1] = (byte) (width >>> 16);
        header[2] = (byte) (width >>> 8);
        header[3] = (byte) width;
        header[4] = (byte) (height >>> 24);
        header[5] = (byte) (height >>> 16);
        header[6] = (byte) (height >>> 8);
        header[7] = (byte) height;
        header[8] = 8;
        header[9] = 6;
        writeProbeChunk(out, "IHDR", header);
        java.util.zip.Deflater deflater = new java.util.zip.Deflater();
        try {
            deflater.setInput(new byte[] {0});
            deflater.finish();
            byte[] buffer = new byte[64];
            int written = deflater.deflate(buffer);
            writeProbeChunk(out, "IDAT", Arrays.copyOf(buffer, written));
        } finally {
            deflater.end();
        }
        writeProbeChunk(out, "IEND", new byte[0]);
        try {
            PngDecoder.decode(out.toByteArray());
            return true;
        } catch (IllegalArgumentException rejected) {
            return false;
        }
    }

    private static void writeProbeChunk(java.io.ByteArrayOutputStream out, String type,
            byte[] data) {
        byte[] typeBytes = type.getBytes(java.nio.charset.Charset.forName("US-ASCII"));
        out.write((data.length >>> 24) & 0xFF);
        out.write((data.length >>> 16) & 0xFF);
        out.write((data.length >>> 8) & 0xFF);
        out.write(data.length & 0xFF);
        out.write(typeBytes, 0, 4);
        out.write(data, 0, data.length);
        java.util.zip.CRC32 crc = new java.util.zip.CRC32();
        crc.update(typeBytes, 0, 4);
        crc.update(data, 0, data.length);
        long value = crc.getValue();
        out.write((int) ((value >>> 24) & 0xFF));
        out.write((int) ((value >>> 16) & 0xFF));
        out.write((int) ((value >>> 8) & 0xFF));
        out.write((int) (value & 0xFF));
    }

    private static void assertStamp(String sprites, String flips, String expectedSlots) {
        String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":2,\"height\":2,"
                + "\"palette\":[\"FF0000FF\",\"00FF00FF\",\"0000FFFF\",\"FFFFFF00\"],"
                + "\"sprites\":" + sprites + ",\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":[{\"op\":\"stamp\",\"sprite\":\"t\","
                + "\"x\":0,\"y\":0," + flips + "}]}]}";
        String paletteHex = "FF0000FF00FF00FF0000FFFFFFFFFF00";
        StringBuilder expected = new StringBuilder();
        for (int index = 0; index < expectedSlots.length(); index++) {
            int slot = expectedSlots.charAt(index) - '0';
            expected.append(paletteHex, (slot - 1) * 8, slot * 8);
        }
        Assert.equal(expected.toString(), Assert.hex(parse(document).renderRgba()),
                "stamp 翻转 " + flips);
    }

    private static byte[] lineBytes(int x0, int y0, int x1, int y1) {
        PxlDocumentBuilder builder = PxlDocumentBuilder.of(16, 16, "[\"FF0000FF\"]");
        builder.op("{\"op\":\"line\",\"x0\":" + x0 + ",\"y0\":" + y0 + ",\"x1\":" + x1
                + ",\"y1\":" + y1 + ",\"slot\":1}");
        return builder.parse().renderRgba();
    }

    /**
     * 叠两层 1x1 图层，返回最终 RGBA 的十六进制。
     *
     * <p>palette 只放**非透明**色：透明源必须用 `fill slot 0`（palette 里写 00000000
     * 是 PXL005）。
     */
    private static String stack(String bottomBlend, String bottomOps,
            String topBlend, String topOps, String... palette) {
        StringBuilder paletteJson = new StringBuilder("[");
        for (int index = 0; index < palette.length; index++) {
            if (index > 0) {
                paletteJson.append(',');
            }
            paletteJson.append('"').append(palette[index]).append('"');
        }
        paletteJson.append(']');
        String document = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":1,\"height\":1,\"palette\":" + paletteJson + ","
                + "\"layers\":["
                + "{\"name\":\"bottom\",\"blend\":\"" + bottomBlend + "\",\"ops\":"
                + bottomOps + "},"
                + "{\"name\":\"top\",\"blend\":\"" + topBlend + "\",\"ops\":"
                + topOps + "}]}";
        return render1x1(document);
    }

    private static String render1x1(String document) {
        return Assert.hex(parse(document).renderRgba());
    }

    private static String bigDoc(String ops) {
        return "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":2048,"
                + "\"height\":2048,\"palette\":[\"FF0000FF\"],"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":" + ops
                + "}]}";
    }

    private static String doc(String width, String height) {
        return "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":" + width
                + ",\"height\":" + height + ",\"palette\":[],"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}";
    }

    private static String doc2(String palette, String ops) {
        return "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":1,"
                + "\"height\":1,\"palette\":" + palette + ","
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":" + ops + "}]}";
    }

    private static byte[] layerOp(String op) {
        return bytes(doc2("[\"FF0000FF\"]", "[" + op + "]"));
    }

    private static byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }

    private static Renderer parse(String document) {
        try {
            return PxlParser.parse(bytes(document));
        } catch (PxlFormatException failure) {
            throw new AssertionError("文档应解析成功，实际 " + failure.getMessage(), failure);
        }
    }

    private static void expectInvalidPng(byte[] png, String what) {
        try {
            PngDecoder.decode(png);
            Assert.fail(what + "：应拒绝");
        } catch (IllegalArgumentException expected) {
            // 预期
        }
    }

    /** 构造一份单图层 normal 文档，省掉重复的样板。 */
    static final class PxlDocumentBuilder {

        private final StringBuilder ops = new StringBuilder();
        private final String head;

        private PxlDocumentBuilder(String head) {
            this.head = head;
        }

        static PxlDocumentBuilder of(int width, int height, String palette) {
            return new PxlDocumentBuilder(
                    "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":" + width
                            + ",\"height\":" + height + ",\"palette\":" + palette + ","
                            + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[");
        }

        void op(String json) {
            if (ops.length() > 0) {
                ops.append(',');
            }
            ops.append(json);
        }

        Renderer parse() {
            return UnitTestMain.parse(head + ops + "]}]}");
        }
    }

    private static void run(String name, Runnable body) {
        executed++;
        try {
            body.run();
        } catch (AssertionError failure) {
            FAILURES.add(name + " → " + failure.getMessage());
        } catch (RuntimeException failure) {
            FAILURES.add(name + " → 异常 " + failure);
        }
    }

    private static void report() {
        // ASCII 计数器：脚本/门禁按它判定"真的跑了多少组"，不依赖本地化文本。
        System.out.println("UNITS=" + executed + " FAILURES=" + FAILURES.size());
        System.out.println("core 单元测试：执行 " + executed + " 组，失败 " + FAILURES.size() + " 组");
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("UNIT TESTS FAILED");
            System.exit(1);
        }
        System.out.println("UNIT TESTS OK");
    }
}
