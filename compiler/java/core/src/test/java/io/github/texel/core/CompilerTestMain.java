package io.github.texel.core;

import java.nio.charset.Charset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Current compiler unit tests（无第三方框架）：逻辑路径、resolver、asset stamp、依赖图、循环、预算、清单。
 *
 * <p>These tests cover resolver, dependency, parameter, and session-budget behavior.
 */
public final class CompilerTestMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static int executed;
    private static final java.util.List<String> FAILURES = new java.util.ArrayList<String>();

    /**
     * 平台不支持的用例必须**显式记为 SKIPPED**，不能 return。
     *
     * <p>此前两条 symlink 用例在 Windows 上建链接失败就 return，runner 把它当成 PASS——
     * 于是 V1UNITS 这个数字高估了覆盖率（发布态复核 GATE-1）。
     */
    private static final java.util.List<String> SKIPPED = new java.util.ArrayList<String>();

    private static final class Skip extends RuntimeException {
        Skip(String why) {
            super(why);
        }
    }

    private static void skipped(String why) {
        throw new Skip(why);
    }

    private CompilerTestMain() {
    }

    public static void main(String[] args) {
        logicalPath();
        resolver();
        assetStamp();
        manifestAndCycle();
        versionGateAndGaps();
        params();
        versionDispatch();
        budgets();
        forwarding();
        cacheCycle();
        cacheDepth();
        try {
            symlinkResolver();
        } catch (Exception failure) {
            throw new RuntimeException(failure);
        }
        twoPhaseBudgets();
        nestedRelativePaths();
        roundFour();
        mirrorRasterWork();
        report();
    }

    // ------------------------------------------------------------ 逻辑路径（§7）

    private static void logicalPath() {
        expectOk("a.pxl", "a.pxl");
        expectOk("parts/blade.pxl", "parts/blade.pxl");
        expectOk("parts/../legacy/x.png", "legacy/x.png");
        expectCode(Codes.PXL008, "Parts/Blade.pxl", "大写必须非法");
        expectCode(Codes.PXL008, "parts\\blade.pxl", "反斜杠必须非法");
        expectCode(Codes.PXL008, "/abs/x.png", "绝对路径必须非法");
        expectCode(Codes.PXL008, "a//b.png", "空段必须非法");
        expectCode(Codes.PXL008, "a/b.png/", "尾随斜杠必须非法");
        expectCode(Codes.PXL008, "a/./b.png", ". 段必须非法");
        expectCode(Codes.PXL008, "../x.png", "弹出根必须非法");
        expectCode(Codes.PXL008, "", "空路径必须非法");
        run("logical.resolve", new Body() {
            public void run() throws Exception {
                equal("parts/blade.pxl", LogicalPath.resolve("entry.pxl", "parts/blade.pxl"),
                        "相对解析");
                equal("legacy/x.png", LogicalPath.resolve("parts/a.pxl", "../legacy/x.png"),
                        "相对解析 + ..");
            }
        });
    }

    // ------------------------------------------------------------ resolver（§8）

    private static void resolver() {
        run("resolver.statuses", new Body() {
            public void run() throws Exception {
                MemoryAssetResolver mem = MemoryAssetResolver.builder()
                        .file("a.pxl", "x".getBytes(UTF8))
                        .spelling("b.pxl", "B.pxl")
                        .build();
                equal(AssetResolver.Status.OK, mem.resolve("a.pxl").status(), "命中");
                equal(AssetResolver.Status.MISSING, mem.resolve("zzz.pxl").status(), "缺失");
                equal(AssetResolver.Status.CASE_MISMATCH, mem.resolve("b.pxl").status(),
                        "显式声明的拼写不匹配");
                equal(AssetResolver.Status.CASE_MISMATCH, mem.resolve("A.pxl").status(),
                        "折叠后才相等的键也算拼写不匹配");
            }
        });
    }

    // ------------------------------------------------------------ asset stamp（§5.3）

    private static void assetStamp() {
        run("v1.stamp-asset", new Body() {
            public void run() throws Exception {
                String child = currentDocument(2, 2, "[\"FF0000FF\",\"00FF00FF\"]",
                        "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"01\",\"10\"]}]");
                String entry = v1Document(4, 2,
                        "{\"blade\":{\"kind\":\"pxl\",\"path\":\"parts/blade.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"blade\",\"x\":1,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("parts/blade.pxl", child.getBytes(UTF8));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                equal(4, out.width(), "宽度");
                equal(2, out.height(), "高度");
                byte[] rgba = out.renderRgba();
                // (0,0) 未被任何 stamp 覆盖 → 全零； (1,0)=红; (2,0)=绿; (3,0)=零
                assertPixel(rgba, 4, 0, 0, 0, 0, 0, 0);
                assertPixel(rgba, 4, 1, 0, 0xFF, 0x00, 0x00, 0xFF);
                assertPixel(rgba, 4, 2, 0, 0x00, 0xFF, 0x00, 0xFF);
                assertPixel(rgba, 4, 3, 0, 0, 0, 0, 0);
                // 第二行是翻转的： (1,1)=绿, (2,1)=红
                assertPixel(rgba, 4, 1, 1, 0x00, 0xFF, 0x00, 0xFF);
                assertPixel(rgba, 4, 2, 1, 0xFF, 0x00, 0x00, 0xFF);
            }
        });
    }

    // ------------------------------------------------------------ 清单 / 循环 / 预算

    private static void manifestAndCycle() {
        run("v1.manifest", new Body() {
            public void run() throws Exception {
                String leaf = currentDocument(1, 1, "[\"112233FF\"]",
                        "[{\"op\":\"fill\",\"slot\":1}]");
                String mid = v1Document(1, 1,
                        "{\"leaf\":{\"kind\":\"pxl\",\"path\":\"z/leaf.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"leaf\",\"x\":0,\"y\":0}]");
                String entry = v1Document(1, 1,
                        "{\"mid\":{\"kind\":\"pxl\",\"path\":\"a/mid.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"mid\",\"x\":0,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("a/mid.pxl", mid.getBytes(UTF8));
                files.put("a/z/leaf.pxl", leaf.getBytes(UTF8));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                List<PxlCompiler.Dependency> manifest = out.dependencies();
                equal(3, manifest.size(), "清单行数");
                equal("entry", manifest.get(0).role(), "第一行必须是入口");
                equal("entry.pxl", manifest.get(0).logicalPath(), "入口逻辑路径");
                equal("a/mid.pxl", manifest.get(1).logicalPath(), "其余按 ASCII 升序");
                equal("a/z/leaf.pxl", manifest.get(2).logicalPath(), "其余按 ASCII 升序");
                equal(64, manifest.get(1).sha256().length(), "sha256 十六进制长度");
                equal("dependency", manifest.get(1).role(), "依赖的 role");
                // 像素：leaf 的 1x1 色被 mid stamp 一次、entry 再 stamp 一次 → 仍是同一个色。
                assertPixel(out.renderRgba(), 1, 0, 0, 0x11, 0x22, 0x33, 0xFF);
            }
        });
        run("v1.cycle-self", new Body() {
            public void run() throws Exception {
                String entry = v1Document(1, 1,
                        "{\"me\":{\"kind\":\"pxl\",\"path\":\"entry.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"me\",\"x\":0,\"y\":0}]");
                expectCompile("entry.pxl", mapOf("entry.pxl", entry), Codes.PXL010, "自引用是循环");
            }
        });
        run("v1.depth", new Body() {
            public void run() throws Exception {
                Map<String, String> chain = new LinkedHashMap<String, String>();
                int layers = PxlCompiler.MAX_DEPTH + 1;
                for (int index = 0; index <= layers; index++) {
                    String self = index == 0 ? "entry.pxl" : "p" + index + ".pxl";
                    String child = "p" + (index + 1) + ".pxl";
                    if (index == layers) {
                        chain.put(self, currentDocument(1, 1, "[\"000000FF\"]",
                                "[{\"op\":\"fill\",\"slot\":1}]"));
                    } else {
                        chain.put(self, v1Document(1, 1,
                                "{\"c\":{\"kind\":\"pxl\",\"path\":\"" + child + "\"}}",
                                "[{\"op\":\"stamp\",\"asset\":\"c\",\"x\":0,\"y\":0}]"));
                    }
                }
                expectCompile("entry.pxl", chain, Codes.PXL011, "深度超限是 PXL011");
            }
        });
        run("v1.missing-dependency", new Body() {
            public void run() throws Exception {
                String entry = v1Document(1, 1,
                        "{\"gone\":{\"kind\":\"png\",\"path\":\"legacy/gone.png\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"gone\",\"x\":0,\"y\":0}]");
                expectCompile("entry.pxl", mapOf("entry.pxl", entry), Codes.PXL009,
                        "缺失依赖是 PXL009");
            }
        });
    }

    // ------------------------------------------------------------ 预算与深度门禁（§10.2 / §11.2）

    private static void budgets() {
        run("v1.ref-depth-64-is-legal", new Body() {
            public void run() throws Exception {
                // 64 层合法、65 层越界（§10.2）。这条是 F03 的**正例**：此前只有 65/66 的负例。
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(chain(64)));
                equal(Integer.valueOf(1), Integer.valueOf(out.width()), "64 层应能编出来");
            }
        });
        run("v1.ref-depth-65-is-budget-error", new Body() {
            public void run() throws Exception {
                expectCompileBytes("entry.pxl", chain(65), Codes.PXL011, "65 层越界");
            }
        });
        run("v1.png-pixels-counted-per-unique-file", new Body() {
            public void run() throws Exception {
                // F05 的**正例**：同一份 PNG 被 5 份文档 stamp，像素只算一次，不得误报 PXL011。
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                StringBuilder ops = new StringBuilder();
                for (int index = 0; index < 5; index++) {
                    String name = "d" + index + ".pxl";
                    files.put(name, stampPngDocument("img.png").getBytes(UTF8));
                    if (index > 0) {
                        ops.append(',');
                    }
                    ops.append("{\"op\":\"stamp\",\"asset\":\"a").append(index).append("\",\"x\":0,\"y\":0}");
                }
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":64,\"height\":64,"
                        + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{";
                for (int index = 0; index < 5; index++) {
                    if (index > 0) {
                        entry += ',';
                    }
                    entry += "\"a" + index + "\":{\"kind\":\"pxl\",\"path\":\"d" + index + ".pxl\"}";
                }
                entry += "},\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":["
                        + ops + "]}]}";
                files.put("entry.pxl", entry.getBytes(UTF8));
                // 规模必须**真的能触发预算**：2048x2048 = 2^22 像素，唯一文件口径合法
                // （4,194,304 <= 2^24），而"每次引用都计"是 5 x 2^22 > 2^24 -> PXL011。
                // 早前这里用 32x32（5120 像素，离 2^24 差 3000 倍），拆掉去重也全绿 —— 假门禁。
                files.put("img.png", PngEncoder.encode(2048, 2048, new byte[2048 * 2048 * 4]));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                equal(Integer.valueOf(64), Integer.valueOf(out.width()), "同图多次 stamp 不该失败");
            }
        });
        run("v1.dependencies-visited-in-ascii-order", new Body() {
            public void run() throws Exception {
                // §9.3：按 asset 名 ASCII 升序访问。两个依赖都缺失时，报的必须是 ASCII 靠前的那个
                // （否则"第一个失败"取决于 op 的书写顺序，两套实现会分叉）。
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                        + "\"zbad\":{\"kind\":\"pxl\",\"path\":\"zzz.pxl\"},"
                        + "\"abad\":{\"kind\":\"pxl\",\"path\":\"aaa.pxl\"}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":["
                        + "{\"op\":\"stamp\",\"asset\":\"zbad\",\"x\":0,\"y\":0},"
                        + "{\"op\":\"stamp\",\"asset\":\"abad\",\"x\":0,\"y\":0}]}]}";
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                    throw new AssertionError("两个依赖都缺失，必须失败");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL009, failure.code(), "缺依赖是 PXL009");
                    equal(Boolean.TRUE,
                            Boolean.valueOf(failure.getMessage().contains("aaa.pxl")),
                            "应按 ASCII 序先报 aaa.pxl，实际: " + failure.getMessage());
                }
            }
        });
        run("v1.missing-dependency-beats-budget", new Body() {
            public void run() throws Exception {
                // §11.2：R2（依赖可解析）先于 R4（预算）。所以"缺依赖"必须压过"超预算"。
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":2048,\"height\":2048,"
                        + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                        + "\"big\":{\"kind\":\"pxl\",\"path\":\"gone.pxl\"}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":["
                        + "{\"op\":\"stamp\",\"asset\":\"big\",\"x\":0,\"y\":0},"
                        + "{\"op\":\"stamp\",\"asset\":\"big\",\"x\":1,\"y\":0},"
                        + "{\"op\":\"stamp\",\"asset\":\"big\",\"x\":2,\"y\":0},"
                        + "{\"op\":\"stamp\",\"asset\":\"big\",\"x\":3,\"y\":0},"
                        + "{\"op\":\"stamp\",\"asset\":\"big\",\"x\":4,\"y\":0}]}]}";
                files.put("entry.pxl", entry.getBytes(UTF8));
                expectCompileBytes("entry.pxl", files, Codes.PXL009, "缺依赖必须压过预算");
            }
        });
    }

    /** 造一条 depth 层深的 PXL 引用链（entry.pxl -> p1.pxl -> ...）。 */
    private static Map<String, byte[]> chain(int depth) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        for (int index = 0; index < depth; index++) {
            String name = index == 0 ? "entry.pxl" : "p" + index + ".pxl";
            if (index == depth - 1) {
                files.put(name, currentDocument(1, 1, "[\"112233FF\"]",
                        "[{\"op\":\"fill\",\"slot\":1}]").getBytes(UTF8));
            } else {
                files.put(name, stampDocument("p" + (index + 1) + ".pxl").getBytes(UTF8));
            }
        }
        return files;
    }

    /** 一个只 stamp 指定 pxl 的最小 v1 文档。 */
    /** 一个只 stamp 指定 png asset 的最小 v1 文档。 */
    private static String stampPngDocument(String path) {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"c\":{\"kind\":\"png\",\"path\":\"" + path + "\"}},"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"c\",\"x\":0,\"y\":0}]}]}";
    }

    private static String stampDocument(String path) {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"c\":{\"kind\":\"pxl\",\"path\":\"" + path + "\"}},"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"c\",\"x\":0,\"y\":0}]}]}";
    }

    // ------------------------------------------------------------ 文件系统 resolver 的符号链接（§8.4）

    private static void symlinkResolver() throws Exception {
        run("v1.resolver-follows-final-symlink", new Body() {
            public void run() throws Exception {
                // N05：最后一级是符号链接、目标仍在 root 内 -> 必须正常解析（此前被误判为不存在）。
                java.nio.file.Path root = java.nio.file.Files.createTempDirectory("resolver-ok");
                java.nio.file.Files.write(root.resolve("real.pxl"), "x".getBytes(UTF8));
                try {
                    java.nio.file.Files.createSymbolicLink(root.resolve("link.pxl"),
                            root.resolve("real.pxl"));
                } catch (UnsupportedOperationException | java.io.IOException notAllowed) {
                    skipped("该平台不允许建符号链接");
                }
                FileAssetResolver resolver = FileAssetResolver.rootedAt(root);
                equal(AssetResolver.Status.OK, resolver.resolve("link.pxl").status(),
                        "root 内的符号链接必须可解析");
            }
        });
        run("v1.resolver-rejects-symlink-escape", new Body() {
            public void run() throws Exception {
                java.nio.file.Path base = java.nio.file.Files.createTempDirectory("resolver-esc");
                java.nio.file.Path root = base.resolve("root");
                java.nio.file.Files.createDirectory(root);
                java.nio.file.Files.write(base.resolve("outside.pxl"), "x".getBytes(UTF8));
                try {
                    java.nio.file.Files.createSymbolicLink(root.resolve("bad.pxl"),
                            base.resolve("outside.pxl"));
                } catch (UnsupportedOperationException | java.io.IOException notAllowed) {
                    skipped("该平台不允许建符号链接");
                }
                FileAssetResolver resolver = FileAssetResolver.rootedAt(root);
                equal(AssetResolver.Status.ESCAPE, resolver.resolve("bad.pxl").status(),
                        "指向 root 外的符号链接必须判逃逸");

                // 悬垂的越界链接：目标**不存在**，但仍必须判 ESCAPE（否则错误码取决于
                // "root 外那个文件在不在"，发布态复核 P3）。
                java.nio.file.Files.createSymbolicLink(root.resolve("dangling.pxl"),
                        base.resolve("not-there.pxl"));
                equal(AssetResolver.Status.ESCAPE, resolver.resolve("dangling.pxl").status(),
                        "悬垂的越界链接也必须判逃逸");
            }
        });
    }

    // ------------------------------------------------------------ 缓存不得绕过深度校验（§10.4）

    private static void cacheDepth() {
        run("v1.cached-deep-subtree-at-64-is-legal", new Body() {
            public void run() throws Exception {
                // asset 名按 ASCII 升序解析：a_leaf（浅）先解析 -> 预热缓存；z_head（深链）后解析 -> 链尾命中。
                // 命中点 stack = 1 + 59 + 1(s 自身) = 61 -> 总深度 61+4-1 = 64，合法。
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(cachedDepthGraph(59)));
                equal(Integer.valueOf(1), Integer.valueOf(out.width()), "64 篇应合法");
            }
        });
        run("v1.cached-deep-subtree-over-limit-is-pxl011", new Body() {
            public void run() throws Exception {
                // 同一形状再长一层：栈 62 < 64（普通检查不触发），但 62+4-1 = 65 越界。
                // 只有缓存命中的上下文校验能抓住它——这条才真正钉住 F02。
                expectCompileBytes("entry.pxl", cachedDepthGraph(60), Codes.PXL011,
                        "缓存子树也要计入深度");
            }
        });
    }

    private static Map<String, byte[]> cachedDepthGraph(int links) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        String entry = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":1,\"height\":1,\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"a_leaf\":{\"kind\":\"pxl\",\"path\":\"s.pxl\"},"
                + "\"z_head\":{\"kind\":\"pxl\",\"path\":\"c1.pxl\"}},"
                + "\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"a_leaf\",\"x\":0,\"y\":0},"
                + "{\"op\":\"stamp\",\"asset\":\"z_head\",\"x\":0,\"y\":0}]}]}";
        files.put("entry.pxl", entry.getBytes(UTF8));
        files.put("s.pxl", stampDocument("s2.pxl").getBytes(UTF8));
        files.put("s2.pxl", stampDocument("s3.pxl").getBytes(UTF8));
        files.put("s3.pxl", stampDocument("s4.pxl").getBytes(UTF8));
        files.put("s4.pxl", currentDocument(1, 1, "[\"112233FF\"]",
                "[{\"op\":\"fill\",\"slot\":1}]").getBytes(UTF8));
        for (int index = 1; index <= links; index++) {
            String child = index == links ? "s.pxl" : "c" + (index + 1) + ".pxl";
            files.put("c" + index + ".pxl", stampDocument(child).getBytes(UTF8));
        }
        return files;
    }

    // ------------------------------------------------------------ 缓存不得隐藏循环（§9.3 / §6.2）

    private static void cacheCycle() {
        run("v1.cache-hit-cannot-hide-a-cycle", new Body() {
            public void run() throws Exception {
                // DSv4 round6/7 的反例：§6.2 允许 asset 参数替换 stamp.asset，于是「同一路径的
                // 边集」会随 bindings 变化——box 被缓存时其子树里含 a.pxl(which=p)，随后
                // a.pxl(which=q) 再引用 box 就命中缓存，此时 a.pxl 是**当前祖先**，构成
                // 只有缓存才看得见的环。删掉缓存命中处的循环检查，这条就会 exit 0。
                expectCompileBytes("entry.pxl", cacheCycleGraph(), Codes.PXL010,
                        "缓存命中也要判祖先是否在后代集合里");
            }
        });
        run("v1.compiled-rgba-is-a-copy", new Body() {
            public void run() throws Exception {
                // 不泄漏内部数组：拿到的字节流被改写，不能影响下一次读取。
                PxlCompiler.Compiled compiled = new PxlCompiler.Compiled(1, 1,
                        new byte[] {1, 2, 3, 4},
                        new java.util.ArrayList<PxlCompiler.Dependency>());
                byte[] handed = compiled.renderRgba();
                handed[0] = 99;
                equal(Integer.valueOf(1), Integer.valueOf(compiled.renderRgba()[0] & 0xFF),
                        "renderRgba 必须返回副本，不能泄漏内部数组");
            }
        });
        run("v1.param-identity-treats-negative-zero-as-zero", new Body() {
            public void run() throws Exception {
                // 整数的身份是数学值（§6.4）：0 与 -0 必须落到同一个节点键，否则同一文档
                // 会被解析两次、节点数翻倍（发布态复核 F16）。
                equal("0", Params.identity(Json.Value.number("0")), "0 的身份");
                equal("0", Params.identity(Json.Value.number("-0")), "-0 归一到 0");
                equal("-1", Params.identity(Json.Value.number("-1")), "非零保留负号");
                equal("x", Params.identity(Json.Value.string("x")), "字符串原样");
            }
        });
    }

    private static Map<String, byte[]> cacheCycleGraph() {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        String entry = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":1,\"height\":1,\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"zbox\":{\"kind\":\"pxl\",\"path\":\"box.pxl\"},"
                + "\"zsecond\":{\"kind\":\"pxl\",\"path\":\"a.pxl\","
                + "\"params\":{\"which\":\"q\"}}},"
                + "\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"zbox\",\"x\":0,\"y\":0},"
                + "{\"op\":\"stamp\",\"asset\":\"zsecond\",\"x\":0,\"y\":0}]}]}";
        files.put("entry.pxl", entry.getBytes(UTF8));
        // box -> a(which=p) -> leaf（先把 box 与 a(p) 一起缓存下来）
        String box = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":1,\"height\":1,\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"n\":{\"kind\":\"pxl\",\"path\":\"a.pxl\","
                + "\"params\":{\"which\":\"p\"}}},"
                + "\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"n\",\"x\":0,\"y\":0}]}]}";
        files.put("box.pxl", box.getBytes(UTF8));
        // a.pxl：asset 参数 which 决定引用 leaf 还是 box
        String a = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":1,\"height\":1,\"params\":{\"which\":{\"type\":\"asset\"}},"
                + "\"palette\":[\"FFFFFFFF\"],\"assets\":{"
                + "\"p\":{\"kind\":\"pxl\",\"path\":\"leaf.pxl\"},"
                + "\"q\":{\"kind\":\"pxl\",\"path\":\"box.pxl\"}},"
                + "\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":["
                + "{\"op\":\"stamp\",\"asset\":\"$which\",\"x\":0,\"y\":0}]}]}";
        files.put("a.pxl", a.getBytes(UTF8));
        files.put("leaf.pxl", currentDocument(1, 1, "[\"112233FF\"]",
                "[{\"op\":\"fill\",\"slot\":1}]").getBytes(UTF8));
        return files;
    }

    // ------------------------------------------------------------ 父参引转发（§5.2）

    private static void forwarding() {
        run("v1.parent-param-forwarding", new Body() {
            public void run() throws Exception {
                // 回归（发布态复核 P1/F04）：父参引经 asset.params 转发给子 PXL。
                // 此前子文档拿到替换前的字面 "$c"，把合法转发拒成 PXL012。
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", upstream(null));
                assertPixel(out.renderRgba(), 2, 0, 0, 0xB8, 0x73, 0x33, 0xFF);
            }
        });
        run("v1.parent-param-forwarding-uses-bound-value", new Body() {
            public void run() throws Exception {
                // 转发必须是**绑定值**，不是默认值——否则"转发"只是把默认值抄了一遍。
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("c", "112233FF");
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", upstream(null), bindings);
                assertPixel(out.renderRgba(), 2, 0, 0, 0x11, 0x22, 0x33, 0xFF);
            }
        });
    }

    /** 父文档 + 子文档的最小转发夹具（子文档 palette 是 ["$tint"]）。 */
    private static MemoryAssetResolver upstream(Map<String, byte[]> ignored) {
        String entry = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":2,\"height\":2,\"params\":{\"c\":{\"type\":\"color\","
                + "\"default\":\"B87333FF\"}},\"palette\":[\"FFFFFFFF\"],"
                + "\"assets\":{\"blade\":{\"kind\":\"pxl\",\"path\":\"parts/blade.pxl\","
                + "\"params\":{\"tint\":\"$c\"}}},\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":[{\"op\":\"stamp\",\"asset\":\"blade\","
                + "\"x\":0,\"y\":0}]}]}";
        String child = "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                + "\"width\":2,\"height\":2,\"params\":{\"tint\":{\"type\":\"color\"}},"
                + "\"palette\":[\"$tint\"],\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put("entry.pxl", entry.getBytes(UTF8));
        files.put("parts/blade.pxl", child.getBytes(UTF8));
        return MemoryAssetResolver.of(files);
    }

    // ------------------------------------------------------------ 版本分派与预算门禁（§2 / §11.2）

    private static void versionDispatch() {
        // 走**生产入口** PxlCompiler（不是 PxlParser）：F01 之所以漏网，就是因为门禁一直用
        // PxlParser 这个 v0 入口跑，v1 的域判定根本没被碰到。
        run("v1.version-negative-is-pxl001", new Body() {
            public void run() throws Exception {
                expectCompile("entry.pxl", mapOf("entry.pxl", versionDocument(-1)),
                        Codes.PXL001, "version -1 越出通用域");
            }
        });
        run("v1.version-over-domain-is-pxl001", new Body() {
            public void run() throws Exception {
                expectCompile("entry.pxl", mapOf("entry.pxl", versionDocument(65536)),
                        Codes.PXL001, "version 65536 越出通用域");
            }
        });
        run("v1.version-2-is-pxl007", new Body() {
            public void run() throws Exception {
                expectCompile("entry.pxl", mapOf("entry.pxl", versionDocument(2)),
                        Codes.PXL007, "域内但不受支持");
            }
        });
    }

    /** 造一个只有 version 不同的最小文档（结构故意最小：先撞版本判定）。 */
    private static String versionDocument(int version) {
        return "{\"format\":\"PXL\",\"version\":" + version
                + ",\"encoding\":\"char\",\"width\":1,\"height\":1,\"palette\":[\"FFFFFFFF\"],"
                + "\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":[]}]}";
    }

    // ------------------------------------------------------------ 参数与 bindings（§6）

    private static void params() {
        run("v1.params-default", new Body() {
            public void run() throws Exception {
                // color 参数带默认值：不绑定也能编，且 palette 元素被替换成默认色。
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"base\":{\"type\":\"color\","
                        + "\"default\":\"112233FF\"}},\"palette\":[\"$base\"],\"layers\":"
                        + "[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"fill\",\"slot\":1}]}]}";
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)));
                assertPixel(out.renderRgba(), 1, 0, 0, 0x11, 0x22, 0x33, 0xFF);
            }
        });
        run("v1.params-bound-color", new Body() {
            public void run() throws Exception {
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"base\":{\"type\":\"color\"}},"
                        + "\"palette\":[\"$base\"],\"layers\":[{\"name\":\"base\","
                        + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("base", "AABBCCFF");
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)), bindings);
                assertPixel(out.renderRgba(), 1, 0, 0, 0xAA, 0xBB, 0xCC, 0xFF);
            }
        });
        run("v1.params-required-missing", new Body() {
            public void run() throws Exception {
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"base\":{\"type\":\"color\"}},"
                        + "\"palette\":[\"$base\"],\"layers\":[{\"name\":\"base\","
                        + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
                expectCompile("entry.pxl", mapOf("entry.pxl", entry), Codes.PXL012, "缺必需参数");
            }
        });
        run("v1.params-undeclared", new Body() {
            public void run() throws Exception {
                String entry = currentDocument(1, 1, "[\"FFFFFFFF\"]",
                        "[{\"op\":\"fill\",\"slot\":1}]");
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("nope", "1");
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)),
                            bindings);
                    throw new AssertionError("v0 文档不接受 bindings");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL012, failure.code(), "绑定未声明参数是 PXL012");
                }
            }
        });
        run("v1.params-type-mismatch", new Body() {
            public void run() throws Exception {
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"n\":{\"type\":\"integer\"}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"pixel\",\"x\":\"$n\",\"y\":0,\"slot\":0}]}]}";
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("n", "abc");
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)),
                            bindings);
                    throw new AssertionError("整数参数给非整数必须失败");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL012, failure.code(), "类型不符是 PXL012");
                }
            }
        });
        run("v1.params-outside-whitelist", new Body() {
            public void run() throws Exception {
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":\"$w\",\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"w\":{\"type\":\"integer\"}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":[]}]}";
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("w", "4");
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)),
                            bindings);
                    throw new AssertionError("width 上的参引必须失败");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL012, failure.code(), "白名单之外的参引是 PXL012");
                }
            }
        });
        run("v1.params-slot-overflow-is-pxl005", new Body() {
            public void run() throws Exception {
                // 替换后 slot 越过 palette 长度：按目标字段原码报 PXL005，不是 PXL012（规范 §6.5）。
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"n\":{\"type\":\"integer\"}},"
                        + "\"palette\":[\"FFFFFFFF\"],\"layers\":[{\"name\":\"base\","
                        + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":\"$n\"}]}]}";
                Map<String, String> bindings = new LinkedHashMap<String, String>();
                bindings.put("n", "9");
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(mapOfBytes("entry.pxl", entry)),
                            bindings);
                    throw new AssertionError("slot 越界必须失败");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL005, failure.code(), "替换后 slot 越界报 PXL005");
                }
            }
        });
        run("v1.params-subdocument-mapping", new Body() {
            public void run() throws Exception {
                String child = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"tint\":{\"type\":\"color\"}},"
                        + "\"palette\":[\"$tint\"],\"layers\":[{\"name\":\"base\","
                        + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{\"blade\":{\"kind\":\"pxl\","
                        + "\"path\":\"parts/blade.pxl\",\"params\":{\"tint\":\"336699FF\"}}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"stamp\",\"asset\":\"blade\",\"x\":0,\"y\":0}]}]}";
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("parts/blade.pxl", child.getBytes(UTF8));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                assertPixel(out.renderRgba(), 1, 0, 0, 0x33, 0x66, 0x99, 0xFF);
            }
        });
        run("v1.params-subdocument-missing", new Body() {
            public void run() throws Exception {
                String child = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"params\":{\"tint\":{\"type\":\"color\"}},"
                        + "\"palette\":[\"$tint\"],\"layers\":[{\"name\":\"base\","
                        + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
                String entry = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"
                        + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":{\"blade\":{\"kind\":\"pxl\","
                        + "\"path\":\"parts/blade.pxl\"}},"
                        + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                        + "[{\"op\":\"stamp\",\"asset\":\"blade\",\"x\":0,\"y\":0}]}]}";
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("parts/blade.pxl", child.getBytes(UTF8));
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                    throw new AssertionError("子文档缺必需参数必须失败");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL012, failure.code(), "子文档缺必需参数是 PXL012");
                }
            }
        });
    }

    // ------------------------------------------------------------ 分派与阶段缺口

    private static void versionGateAndGaps() {
        run("v1.unsupported-subdocument", new Body() {
            public void run() throws Exception {
                String entry = v1Document(1, 1,
                        "{\"future\":{\"kind\":\"pxl\",\"path\":\"f.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"future\",\"x\":0,\"y\":0}]");
                String future = "{\"format\":\"PXL\",\"version\":2}";
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("f.pxl", future.getBytes(UTF8));
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                    throw new AssertionError("version 2 的子文档必须被拒绝");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL007, failure.code(), "不支持的版本是 PXL007");
                }
            }
        });
        run("v1.stamp-png-asset", new Body() {
            public void run() throws Exception {
                // 用**生产编码器**造一张 2x2 PNG，再让 v1 的 png asset 引用它：
                // 这样既测到了解码路径，也测到了 PNG asset 的放置语义（与 sprite stamp 一致）。
                byte[] rgba = new byte[] {
                    (byte) 0xFF, 0, 0, (byte) 0xFF,   0, (byte) 0xFF, 0, (byte) 0xFF,
                    0, 0, (byte) 0xFF, (byte) 0xFF,   (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF,
                };
                byte[] png = PngEncoder.encode(2, 2, rgba);
                String entry = v1Document(2, 2,
                        "{\"img\":{\"kind\":\"png\",\"path\":\"art/tile.png\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"img\",\"x\":0,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("art/tile.png", png);
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                assertPixel(out.renderRgba(), 2, 0, 0, 0xFF, 0x00, 0x00, 0xFF);
                assertPixel(out.renderRgba(), 2, 1, 0, 0x00, 0xFF, 0x00, 0xFF);
                assertPixel(out.renderRgba(), 2, 0, 1, 0x00, 0x00, 0xFF, 0xFF);
                assertPixel(out.renderRgba(), 2, 1, 1, 0xFF, 0xFF, 0xFF, 0xFF);
                // 清单里 png 依赖的 kind 必须是 png（增量构建按它分类）。
                boolean sawPng = false;
                for (PxlCompiler.Dependency item : out.dependencies()) {
                    if ("art/tile.png".equals(item.logicalPath())) {
                        sawPng = "png".equals(item.kind());
                    }
                }
                equal(Boolean.TRUE, Boolean.valueOf(sawPng), "清单里应有 kind=png 的依赖行");
            }
        });
        run("v1.png-asset-with-non-png-bytes", new Body() {
            public void run() throws Exception {
                String entry = v1Document(1, 1,
                        "{\"img\":{\"kind\":\"png\",\"path\":\"bad.png\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"img\",\"x\":0,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("bad.png", new byte[] {1, 2, 3, 4, 5, 6, 7, 8, 9});
                try {
                    PxlCompiler.compile("entry.pxl", MemoryAssetResolver.of(files));
                    throw new AssertionError("非法 PNG 必须被拒绝");
                } catch (PxlFormatException failure) {
                    // 规范 §11.1：声明的 kind 与实际字节不符（这里连 PNG 签名都没有）归 PXL009；
                    // 有签名但结构非法才归 PXL006。这条原先写的是 PXL006，与 Python 侧相反
                    // （审查 R4-03），规范补了那句之后两侧同码。
                    equal(Codes.PXL009, failure.code(), "不是 PNG 的字节是 PXL009");
                }
            }
        });
        run("current.rejects-undeclared-asset-stamp", new Body() {
            public void run() throws Exception {
                String document = "{\"format\":\"PXL\",\"version\":1,\"width\":1,\"height\":1,"

                        + "\"encoding\":\"char\",\"layers\":[{\"name\":\"base\",\"blend\":\"normal\","

                        + "\"ops\":[{\"op\":\"stamp\",\"asset\":\"x\",\"x\":0,\"y\":0}]}]}";
                try {
                    PxlParser.parse(document.getBytes(UTF8));
                    throw new AssertionError("undeclared asset stamp must be rejected");
                } catch (PxlFormatException failure) {
                    equal(Codes.PXL005, failure.code(), "undeclared asset stamp is PXL005");
                }
            }
        });
    }

// ------------------------------------------------------------ 两相位预算门禁（§11.2 / §10.2）

    /**
     * 审查 N-01..N-05 的门禁：预算必须在**依赖图解析完之后**统一判（R2 先于 R4），
     * 只有单文档的格式边界先于会话累计（65 MiB 是 PXL002 不是 PXL011），
     * 且判预算必须早于光栅化（R4 先于"写像素"）。
     */
    private static void twoPhaseBudgets() {
        run("v1.nodes-over-limit-is-pxl011", new Body() {
            public void run() throws Exception {
                // 4097 个唯一渲染节点 > 4096（§10.2）。入口 1 + 4096 个 1x1 子文档。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        nodeFanOut(PxlCompiler.MAX_NODES, false), Codes.PXL011, "节点数超限");
                contains(failure.getMessage(), "唯一渲染节点数", "PXL011 必须说明是哪一项预算");
                contains(failure.getMessage(), "引用链:", "PXL011 必须带引用链（§9.3）");
            }
        });
        run("v1.nodes-over-limit-with-missing-dependency", new Body() {
            public void run() throws Exception {
                // §11.2：R2（依赖可解析）先于 R4（会话预算）。节点数会先越过上限，但缺失依赖
                // 在 ASCII 序里排在后面（zz），所以"先记满、后报缺"才是对的：报 PXL009。
                expectCompileFailure("entry.pxl", nodeFanOut(PxlCompiler.MAX_NODES, true),
                        Codes.PXL009, "缺失依赖必须压过节点数超限");
            }
        });
        run("v1.files-over-limit-is-pxl011", new Body() {
            public void run() throws Exception {
                // 唯一实际源文件 8194 > 8192（§10.2）。用 png 叶节点：PNG 只计文件、不建渲染节点
                // （§10.4 第 1 条），否则这条会先撞节点数上限，测不到文件数预算。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        pngFanOut(false), Codes.PXL011, "文件数超限");
                contains(failure.getMessage(), "唯一实际源文件数", "PXL011 必须说明是哪一项预算");
                contains(failure.getMessage(), "引用链:", "PXL011 必须带引用链（§9.3）");
            }
        });
        run("v1.files-over-limit-with-missing-dependency", new Body() {
            public void run() throws Exception {
                expectCompileFailure("entry.pxl", pngFanOut(true),
                        Codes.PXL009, "缺失依赖必须压过文件数超限");
            }
        });
        run("v1.bytes-over-limit-is-pxl011", new Body() {
            public void run() throws Exception {
                // 累计 PXL 字节按**唯一文件**计（§10.2）：64 份 1.1 MiB 的链 = 70.4 MiB > 64 MiB。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        byteChain(64, 1100 * 1024, false), Codes.PXL011, "累计字节超限");
                contains(failure.getMessage(), "累计 PXL UTF-8 字节数", "PXL011 必须说明是哪一项预算");
                contains(failure.getMessage(), "引用链:", "PXL011 必须带引用链（§9.3）");
            }
        });
        run("v1.bytes-over-limit-with-missing-dependency", new Body() {
            public void run() throws Exception {
                expectCompileFailure("entry.pxl", byteChain(64, 1100 * 1024, true),
                        Codes.PXL009, "缺失依赖必须压过累计字节超限");
            }
        });
        run("v1.single-document-65mib-is-pxl002", new Body() {
            public void run() throws Exception {
                // §5.0.2：**单文档** 64 MiB 是格式边界 → PXL002。PXL011 只用于会话累计，
                // 把这一条报成 PXL011 就是把"这一份文档不合法"说成"这次编译太大"。
                byte[] huge = padded(currentDocument(1, 1, "[\"FFFFFFFF\"]",
                        "[{\"op\":\"fill\",\"slot\":1}]"), 65 * 1024 * 1024);
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", huge);
                expectCompileFailure("entry.pxl", files, Codes.PXL002, "单文档 65 MiB 是 PXL002");
            }
        });
        run("v1.global-raster-over-limit-is-pxl011", new Body() {
            public void run() throws Exception {
                // 全局光栅工作 401 × 2^22 = 1681915904（子文档 fill 2^22 + 入口 400 次 stamp
                // 各 2^22）> 2^24。判据两条：码是 PXL011，且**在光栅化之前**拒绝（见下）。
                long started = System.nanoTime();
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        rasterHeavy(400), Codes.PXL011, "全局光栅超限");
                long elapsedMs = (System.nanoTime() - started) / 1000000L;
                System.out.println("[GATE] v1.global-raster-over-limit 判定耗时=" + elapsedMs
                        + "ms（若光栅化先发生，本例要写 1681915904 个像素；修前实测 200 次 stamp 是"
                        + " 1173ms、400 次约 2.3s）");
                contains(failure.getMessage(), "1681915904", "PXL011 必须报出算出来的工作量");
                contains(failure.getMessage(), "引用链:", "PXL011 必须带引用链（§9.3）");
                // 相位 A 只做解析与算术；同样的输入若先把像素画出来再判，耗时在秒级。
                // 实测：修后 1ms，修前 1173ms（200 次）/ 约 2.3s（400 次）。阈值取 500ms：
                // 比修后的实测值松 500 倍，又比修前的实测值紧 2.3 倍以上，两头都不脆。
                if (elapsedMs >= 500L) {
                    throw new AssertionError("预算判定用了 " + elapsedMs + "ms：光栅化发生在预算之前"
                            + "（§11.2：R4 必须先于光栅化）");
                }
            }
        });
    }

    /**
     * 入口 + {@code children} 个互不相同的 1x1 子文档，入口把它们各 stamp 一次。
     *
     * <p>缺失依赖放在**最后一个子文档**里而不是入口：每文档 {@code assets} 声明数上限是 4096
     * （§10.1），往入口再塞一个就变成 PXL002，测不到预算。放在最深处的子文档还有个好处——
     * 此时所有文件/节点/字节都已经记满了，正是"计数器先越过上限、随后才撞上缺失依赖"的顺序。
     */
    private static Map<String, byte[]> nodeFanOut(int children, boolean withMissing) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        String leaf = currentDocument(1, 1, "[\"112233FF\"]", "[{\"op\":\"fill\",\"slot\":1}]");
        StringBuilder assets = new StringBuilder();
        StringBuilder ops = new StringBuilder();
        for (int index = 0; index < children; index++) {
            String name = "d" + pad4(index);
            boolean last = index == children - 1;
            files.put(name + ".pxl", last && withMissing
                    ? v1Document(1, 1, "{\"zz\":{\"kind\":\"pxl\",\"path\":\"gone.pxl\"}}",
                            "[{\"op\":\"fill\",\"slot\":1},"
                                    + "{\"op\":\"stamp\",\"asset\":\"zz\",\"x\":0,\"y\":0}]")
                            .getBytes(UTF8)
                    : leaf.getBytes(UTF8));
            if (index > 0) {
                assets.append(',');
                ops.append(',');
            }
            assets.append("\"a").append(name).append("\":{\"kind\":\"pxl\",\"path\":\"")
                    .append(name).append(".pxl\"}");
            ops.append("{\"op\":\"stamp\",\"asset\":\"a").append(name).append("\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(1, 1, "{" + assets + "}", "[" + ops + "]").getBytes(UTF8));
        return files;
    }

    /**
     * 唯一 PNG 叶节点总数够 {@code MAX_FILES + 1} 组文件，但每文档 {@code assets} 声明数必须
     * ≤ 4096（§10.1），所以拆成三份文档：entry → mid0 → mid1。PNG 只计文件、不建渲染节点
     * （§10.4 第 1 条），于是节点数仍是 3，测到的确实是**文件数**预算。
     *
     * <p>缺失依赖放在最深处的 mid1 里：那时文件数早已越过上限。
     */
    private static Map<String, byte[]> pngFanOut(boolean withMissing) {
        byte[] png = PngEncoder.encode(1, 1, new byte[] {(byte) 0xFF, 0, 0, (byte) 0xFF});
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        String[] names = {"mid0.pxl", "mid1.pxl"};
        int perDocument = 4095;
        int counter = 0;
        for (int document = 0; document < names.length + 1; document++) {
            StringBuilder assets = new StringBuilder();
            StringBuilder ops = new StringBuilder();
            if (document < names.length) {
                for (int index = 0; index < perDocument; index++) {
                    String name = "p" + pad4(counter) + ".png";
                    counter++;
                    files.put(name, png);
                    if (assets.length() > 0) {
                        assets.append(',');
                        ops.append(',');
                    }
                    assets.append("\"a").append(name.substring(0, name.length() - 4))
                            .append("\":{\"kind\":\"png\",\"path\":\"").append(name).append("\"}");
                    ops.append("{\"op\":\"stamp\",\"asset\":\"a")
                            .append(name.substring(0, name.length() - 4))
                            .append("\",\"x\":0,\"y\":0}");
                }
                if (assets.length() > 0) {
                    assets.append(',');
                    ops.append(',');
                }
                assets.append("\"next\":{\"kind\":\"pxl\",\"path\":\"").append(names[document])
                        .append("\"}");
                ops.append("{\"op\":\"stamp\",\"asset\":\"next\",\"x\":0,\"y\":0}");
                files.put(document == 0 ? "entry.pxl" : names[document - 1],
                        v1Document(1, 1, "{" + assets + "}", "[" + ops + "]").getBytes(UTF8));
            } else {
                String leaf = currentDocument(1, 1, "[\"FFFFFFFF\"]",
                        "[{\"op\":\"fill\",\"slot\":1}]");
                files.put(names[names.length - 1], withMissing
                        ? v1Document(1, 1, "{\"zz\":{\"kind\":\"png\",\"path\":\"gone.png\"}}",
                                "[{\"op\":\"fill\",\"slot\":1},"
                                        + "{\"op\":\"stamp\",\"asset\":\"zz\",\"x\":0,\"y\":0}]")
                                .getBytes(UTF8)
                        : leaf.getBytes(UTF8));
            }
        }
        return files;
    }

    /**
     * 一条 {@code depth} 层的链，每份文档都填充到 {@code bytes} 字节（尾部空白，合法 JSON）。
     *
     * <p>尾部空白是刻意的：累计字节按**原始字节数**算（§10.2），而 JSON 允许对象之后有空白，
     * 于是"文档很小、字节很大"这种组合可以被精确构造出来。
     */
    private static Map<String, byte[]> byteChain(int depth, int bytes, boolean withMissing) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        for (int index = 0; index < depth; index++) {
            String name = index == 0 ? "entry.pxl" : "p" + index + ".pxl";
            StringBuilder assets = new StringBuilder();
            StringBuilder ops = new StringBuilder();
            if (index == depth - 1) {
                // 链尾：只有一个 fill，不再引用别的文档。
                files.put(name, padded(v1Document(1, 1, "{}", "[{\"op\":\"fill\",\"slot\":1}]"),
                        bytes));
                continue;
            }
            String child = "p" + (index + 1) + ".pxl";
            assets.append("\"c\":{\"kind\":\"pxl\",\"path\":\"").append(child).append("\"}");
            ops.append("{\"op\":\"stamp\",\"asset\":\"c\",\"x\":0,\"y\":0}");
            if (index == 0) {
                appendMissing(files, assets, ops, withMissing, "pxl");
            }
            files.put(name, padded(v1Document(1, 1, "{" + assets + "}", "[" + ops + "]"), bytes));
        }
        return files;
    }

    /**
     * 一份 2048x2048 的子文档 + 入口 {@code stamps} 次全幅 stamp。
     *
     * <p>次数刻意取大：判据要能区分"预算先于光栅化"与"光栅化之后再判"。修的代码在相位 A 只
     * 做算术（毫秒级），坏的代码会真的画完 {@code stamps × 2^22} 次像素写入。5 次只有 243ms、
     * 与 0ms 拉不开量级；200 次是 8.4e8 次写入（实测约 8s），两者相差三个数量级，阈值才不脆。
     */
    private static Map<String, byte[]> rasterHeavy(int stamps) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put("big.pxl", currentDocument(2048, 2048, "[\"FFFFFFFF\"]",
                "[{\"op\":\"fill\",\"slot\":1}]").getBytes(UTF8));
        StringBuilder ops = new StringBuilder();
        for (int index = 0; index < stamps; index++) {
            if (index > 0) {
                ops.append(',');
            }
            ops.append("{\"op\":\"stamp\",\"asset\":\"big\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(2048, 2048,
                "{\"big\":{\"kind\":\"pxl\",\"path\":\"big.pxl\"}}", "[" + ops + "]").getBytes(UTF8));
        return files;
    }

    private static void appendMissing(Map<String, byte[]> files, StringBuilder assets,
            StringBuilder ops, boolean withMissing, String kind) {
        if (!withMissing) {
            return;
        }
        // 名字以 z 开头：按 asset 名 ASCII 升序排在最后，于是"计数器先越过上限、随后才撞上
        // 缺失依赖"这条最刁钻的顺序被真的构造出来。
        if (assets.length() > 0) {
            assets.append(',');
            ops.append(',');
        }
        assets.append("\"zz\":{\"kind\":\"").append(kind).append("\",\"path\":\"gone."
                + ("png".equals(kind) ? "png" : "pxl") + "\"}");
        ops.append("{\"op\":\"stamp\",\"asset\":\"zz\",\"x\":0,\"y\":0}");
    }

    private static byte[] padded(String document, int totalBytes) {
        byte[] base = document.getBytes(UTF8);
        if (base.length >= totalBytes) {
            return base;
        }
        byte[] out = new byte[totalBytes];
        System.arraycopy(base, 0, out, 0, base.length);
        for (int index = base.length; index < totalBytes; index++) {
            out[index] = ' ';
        }
        return out;
    }

    private static String pad4(int value) {
        StringBuilder out = new StringBuilder();
        out.append(value);
        while (out.length() < 4) {
            out.insert(0, '0');
        }
        return out.toString();
    }

    private static PxlFormatException expectCompileFailure(String entry, Map<String, byte[]> files,
            String code, String what) {
        try {
            PxlCompiler.compile(entry, MemoryAssetResolver.of(files));
            throw new AssertionError(what + "：期望 " + code + "，实际编译成功");
        } catch (PxlFormatException failure) {
            equal(code, failure.code(), what);
            return failure;
        }
    }

    private static void contains(String text, String needle, String what) {
        if (text == null || text.indexOf(needle) < 0) {
            throw new AssertionError(what + "：消息里没有 \"" + needle + "\"，实际: " + text);
        }
    }

// ------------------------------------------------------------ 逻辑路径相对声明文档（§7 / F07）

    /**
     * R1 的逃逸判定必须相对**声明该引用的文档**，而不是相对 asset root。
     *
     * <p>F07：parts/a.pxl 里的 ../shared/b.pxl 是合法的（结果 shared/b.pxl），按 root 规范化会
     * 弹出根、把它误判成 PXL008，于是与 Python 侧分叉。
     */
    private static void nestedRelativePaths() {
        run("v1.nested-parent-reference-is-legal", new Body() {
            public void run() throws Exception {
                String leaf = currentDocument(2, 2, "[\"FF0000FF\",\"00FF00FF\"]",
                        "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"01\",\"10\"]}]");
                String mid = v1Document(2, 2,
                        "{\"b\":{\"kind\":\"pxl\",\"path\":\"../shared/b.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"b\",\"x\":0,\"y\":0}]");
                String entry = v1Document(2, 2,
                        "{\"a\":{\"kind\":\"pxl\",\"path\":\"parts/a.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"a\",\"x\":0,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("parts/a.pxl", mid.getBytes(UTF8));
                files.put("shared/b.pxl", leaf.getBytes(UTF8));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(files));
                byte[] rgba = out.renderRgba();
                assertPixel(rgba, 2, 0, 0, 0xFF, 0x00, 0x00, 0xFF);
                assertPixel(rgba, 2, 1, 0, 0x00, 0xFF, 0x00, 0xFF);
                assertPixel(rgba, 2, 0, 1, 0x00, 0xFF, 0x00, 0xFF);
                assertPixel(rgba, 2, 1, 1, 0xFF, 0x00, 0x00, 0xFF);
                // 清单里必须是**解析后**的逻辑路径（shared/b.pxl），不能留着 ../shared/b.pxl。
                StringBuilder paths = new StringBuilder();
                for (PxlCompiler.Dependency item : out.dependencies()) {
                    if (paths.length() > 0) {
                        paths.append(", ");
                    }
                    paths.append(item.logicalPath());
                }
                equal("entry.pxl, parts/a.pxl, shared/b.pxl", paths.toString(), "清单里的依赖路径");
            }
        });
        run("v1.nested-escape-is-pxl008", new Body() {
            public void run() throws Exception {
                // 同一份排版，但 ../../ 从 parts/ 出发仍然弹出根 -> PXL008。
                // 而且这条声明**没有被选中**（没有 stamp）：§9.1 要求未选中的声明也过路径校验。
                String mid = v1Document(1, 1,
                        "{\"b\":{\"kind\":\"pxl\",\"path\":\"../../outside/b.pxl\"}}",
                        "[{\"op\":\"fill\",\"slot\":1}]");
                String entry = v1Document(1, 1,
                        "{\"a\":{\"kind\":\"pxl\",\"path\":\"parts/a.pxl\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"a\",\"x\":0,\"y\":0}]");
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", entry.getBytes(UTF8));
                files.put("parts/a.pxl", mid.getBytes(UTF8));
                PxlFormatException failure = expectCompileFailure("entry.pxl", files, Codes.PXL008,
                        "相对声明文档的逃逸仍是 PXL008");
                contains(failure.getMessage(), "逃逸", "应当是逃逸而非语法错");
            }
        });
    }

    // ------------------------------------------------------------ 工具

    /** 期望编译失败并给出指定错误码（字节夹具版）。 */
    private static void expectCompileBytes(String entry, Map<String, byte[]> files, String code,
            String what) {
        try {
            PxlCompiler.compile(entry, MemoryAssetResolver.of(files));
            throw new AssertionError(what + "：期望 " + code + "，实际编译成功");
        } catch (PxlFormatException failure) {
            equal(code, failure.code(), what);
        }
    }

    private static Map<String, byte[]> mapOfBytes(String key, String text) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put(key, text.getBytes(UTF8));
        return files;
    }

    private static Map<String, String> mapOf(String key, String text) {
        Map<String, String> files = new LinkedHashMap<String, String>();
        files.put(key, text);
        return files;
    }

    private static String currentDocument(int width, int height, String palette, String ops) {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":" + width + ",\"height\":" + height
                + ",\"encoding\":\"char\",\"palette\":" + palette + ",\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":" + ops + "}]}";
    }

    private static String v1Document(int width, int height, String assets, String ops) {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":" + width + ",\"height\":" + height
                + ",\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],\"assets\":" + assets
                + ",\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":" + ops + "}]}";
    }

    private static void expectCompile(String entryId, Map<String, String> files, String code,
            String what) throws PxlFormatException {
        Map<String, byte[]> raw = new LinkedHashMap<String, byte[]>();
        for (Map.Entry<String, String> item : files.entrySet()) {
            raw.put(item.getKey(), item.getValue().getBytes(UTF8));
        }
        try {
            PxlCompiler.compile(entryId, MemoryAssetResolver.of(raw));
            throw new AssertionError(what + "：应当失败却没有");
        } catch (PxlFormatException failure) {
            equal(code, failure.code(), what);
        }
    }

    private static void assertPixel(byte[] rgba, int width, int x, int y,
            int r, int g, int b, int a) {
        int offset = (y * width + x) * 4;
        equal(r, rgba[offset] & 0xff, "像素 (" + x + "," + y + ") 的 R");
        equal(g, rgba[offset + 1] & 0xff, "像素 (" + x + "," + y + ") 的 G");
        equal(b, rgba[offset + 2] & 0xff, "像素 (" + x + "," + y + ") 的 B");
        equal(a, rgba[offset + 3] & 0xff, "像素 (" + x + "," + y + ") 的 A");
    }

    private static void expectOk(final String input, final String expected) {
        run("logical.ok " + input, new Body() {
            public void run() throws Exception {
                equal(expected, LogicalPath.normalize(input), "规范化 " + input);
            }
        });
    }

    private static void expectCode(final String code, final String input, final String what) {
        run("logical.reject " + input, new Body() {
            public void run() throws Exception {
                try {
                    LogicalPath.normalize(input);
                    throw new AssertionError(what + "：应当失败却没有");
                } catch (PxlFormatException failure) {
                    equal(code, failure.code(), what);
                }
            }
        });
    }

    private static void equal(Object expected, Object actual, String what) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(what + "：期望 " + expected + "，实际 " + actual);
        }
    }

    private interface Body {
        void run() throws Exception;
    }

    /**
     * 平台相关的跳过**只允许**这两条：名单或总数一变就红。
     *
     * <p>为什么需要不变量（DSv4 round7）：SKIPPED 通道只让跳过可打印，但 SKIPPED&gt;0 仍然
     * exit 0；CI 里 grep "COMPILER_UNITS=68" 会把「66 跑 + 2 跳过」和「68 全跑」混为一谈。
     * 断言 executed + SKIPPED == 总数，并限定跳过名单，才让这个数字可信。
     */
    private static final int EXPECTED_TOTAL = 71;

    private static final java.util.Set<String> ALLOWED_SKIPS =
            new java.util.HashSet<String>(java.util.Arrays.asList(
                    "v1.resolver-follows-final-symlink",
                    "v1.resolver-rejects-symlink-escape"));

    private static void run(String name, Body body) {
        executed++;
        try {
            body.run();
        } catch (Skip skip) {
            executed--;
            SKIPPED.add(name + " -> " + skip.getMessage());
        } catch (AssertionError failure) {
            FAILURES.add(name + " → " + failure.getMessage());
        } catch (Exception failure) {
            StackTraceElement[] frames = failure.getStackTrace();
            String where = frames.length == 0 ? "?" : frames[0].toString();
            FAILURES.add(name + " → 异常 " + failure + " @ " + where);
        }
    }

    // ------------------------------------------------------------ 第四轮：预算口径 / 引用链 / 解码缓存 / 别名

    private static void roundFour() {
        run("v1.same-pxl-many-asset-names-is-legal", new Body() {
            public void run() throws Exception {
                // §10.2 的"唯一实际源文件数"按**唯一文件**计，不按读取次数：两份子文档各声明
                // 4096 个 asset 名、全部指向同一个 tile.pxl，读取 8195 次、唯一文件只有 4 个。
                // 按读取次数计会误报 PXL011（审查 R3-01）。
                //
                // 为什么 target 是 **.pxl** 而不是 .png：本轮给 PNG 加了解码缓存，同一个 PNG 路径
                // 在一次会话里只会被 read 一次，于是"按读取次数计"与"按唯一文件计"在 PNG 上恰好
                // 相等——用 PNG 做 target 时这条门禁对计数口径**不敏感**（审查 R4-01 实测：退掉
                // R3-01 的守卫它仍全绿）。PXL 没有读取缓存（loadGraph 的 read 发生在节点缓存
                // 检查之前），换成 .pxl 后按读取次数计会立刻误报 PXL011，门禁才咬得住。
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                        MemoryAssetResolver.of(samePxlManyNames()));
                equal(4, out.dependencies().size(), "唯一实际源文件应是 4");
                assertPixel(out.renderRgba(), 1, 0, 0, 0xFF, 0x00, 0x00, 0xFF);
            }
        });
        run("v1.budget-table-order-nodes-before-files", new Body() {
            public void run() throws Exception {
                // §11.2 阶段 R4 的表内顺序：节点数在文件数之前。两项同时越界时，消息里必须是
                // 表内第一项（审查 PM5：交换两者顺序原先没有任何门禁）。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        nodesAndFilesOverLimit(), Codes.PXL011, "节点数与文件数同时越界");
                contains(failure.getMessage(), "唯一渲染节点数", "必须报表内第一项（节点数）");
            }
        });
        run("v1.propagated-unknown-op-carries-chain", new Body() {
            public void run() throws Exception {
                // §9.3：被引用文档原样传播上来的码也要带「入口 -> … -> 失败文件」。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        nestedBrokenDocument("{\"op\":\"bogus\",\"x\":0,\"y\":0}"), Codes.PXL004,
                        "三层嵌套的未知 op");
                equal("entry.pxl -> parts/a.pxl -> parts/deep/b.pxl", failure.chain(), "三层的链");
                contains(failure.getMessage(), "parts/deep/b.pxl", "消息里必须出现失败文件");
            }
        });
        run("v1.propagated-reference-error-carries-chain", new Body() {
            public void run() throws Exception {
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        nestedBrokenDocument("{\"op\":\"fill\",\"slot\":2}"), Codes.PXL005,
                        "三层嵌套的 slot 越界");
                equal("entry.pxl -> parts/a.pxl -> parts/deep/b.pxl", failure.chain(), "三层的链");
            }
        });
        run("v1.png-read-once-per-unique-file", new Body() {
            public void run() throws Exception {
                // 同一张 2048x2048 的 PNG 被 60 个节点各 stamp 一次：预算全合法（唯一像素
                // 2^22 <= 2^24），但没有解码缓存时峰值要 60 x 16 MiB（审查 R3-03，-Xmx1g 先 OOM）。
                //
                // 观测方式（审查 R4-07：生产 core 不携带测试埋点）：解码缓存挡住了**读取**，
                // 所以"同一 PNG 只被读一次"与"只被解码一次"是同一件事，用计数 resolver 从
                // 测试侧观测即可；命中缓存后 resolve 只发生一次，退掉缓存则为 60 次。
                CountingResolver resolver = new CountingResolver(manyNodesOnePng(60));
                PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl", resolver);
                equal(1, resolver.count("big.png"), "同一 PNG 必须只被读（=解码）一次");
                equal(1, out.width(), "画布宽度");
            }
        });
        run("v1.png-pixel-budget-stops-decoding", new Body() {
            public void run() throws Exception {
                // §10.2「累计 PNG 解码像素数」按名字就该约束**解码工作量**（审查 R4-02）：
                // 32 张互不相同的 2048x2048，预算 2^24 只容得下 4 张，第 5 张起必须**不解码**。
                //
                // 判据是结构性的，既不靠计时也不靠计数器：第 5 张起只有 签名+IHDR（没有
                // IDAT/IEND），一旦被解码就必报 PXL006「缺少 IEND」；正确实现只读头部，
                // 于是报 PXL011。改回"全部解码完再判预算"时这条立刻变红。
                long started = System.nanoTime();
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        manyDistinctPngs(32, 4), Codes.PXL011, "32 张 2048^2 越过累计像素预算");
                long millis = (System.nanoTime() - started) / 1000000L;
                contains(failure.getMessage(), "累计 PNG 解码像素数", "必须是像素预算");
                contains(failure.getMessage(), "134217728", "32 x 2048 x 2048 的累加值");
                // 墙钟只打印、**不判定**：只解 4 张约 0.4s，32 张全解约 1.2s（审查实测），
                // 但共享 CI 上"只解 4 张"也可能超过任何固定阈值 —— 那种门禁不可判定，
                // 慢机上会假红、快机上又挡不住。判据已由上面的结构性断言给出：
                // 第 5 张起只有 IHDR，一旦解码必 PXL006，所以走到 PXL011 就等于"没有解码"。
                System.out.println("[INFO] 像素预算判定耗时=" + millis + "ms（参考：只解 4 张约 0.4s，"
                        + "32 张全解约 1.2s；本门禁以结构性断言判定，不用计时）");
            }
        });
        run("v1.asset-stamp-area-only-in-session-budget", new Body() {
            public void run() throws Exception {
                // 规范 §10.1：asset stamp 的写入面积**不计入**单文档 2^22，只按 §10.3 计入会话
                // 全局光栅（尺寸要等 R2 才知道，单文档预算属阶段 6；审查要求把它写成明文）。
                // 构造：画布 2048^2（恰好等于单文档面积上限）、本文档自身 op 工作为 0（只有 stamp），
                //   2048^2 x 4 次 = 16,777,216；1024^2 x 1 次 = 1,048,576；合计 17,825,792 > 2^24。
                // 面积若被算进单文档就会先报 PXL002 —— 所以"报 PXL011 且数字是 17825792"
                // 同时钉住"只进会话"与"两个不同尺寸各自按自己的面积求和"。
                byte[] bigPng = PngEncoder.encode(2048, 2048, new byte[2048 * 2048 * 4]);
                byte[] smallPng = PngEncoder.encode(1024, 1024, new byte[1024 * 1024 * 4]);
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("big.png", bigPng);
                files.put("small.png", smallPng);
                StringBuilder ops = new StringBuilder();
                for (int index = 0; index < 4; index++) {
                    if (index > 0) {
                        ops.append(',');
                    }
                    ops.append("{\"op\":\"stamp\",\"asset\":\"big\",\"x\":0,\"y\":0}");
                }
                ops.append(",{\"op\":\"stamp\",\"asset\":\"small\",\"x\":0,\"y\":0}");
                files.put("entry.pxl", v1Document(2048, 2048,
                        "{\"big\":{\"kind\":\"png\",\"path\":\"big.png\"},"
                                + "\"small\":{\"kind\":\"png\",\"path\":\"small.png\"}}",
                        "[" + ops + "]").getBytes(UTF8));
                PxlFormatException failure = expectCompileFailure("entry.pxl", files, Codes.PXL011,
                        "asset stamp 的会话光栅超限");
                contains(failure.getMessage(), "全局光栅工作", "必须是会话光栅那一项");
                contains(failure.getMessage(), "17825792", "两个尺寸各自按面积求和");
            }
        });
        run("v1.png-asset-kind-mismatch-is-pxl009", new Body() {
            public void run() throws Exception {
                // 规范 §11.1：声明的 kind 与实际字节不符 → PXL009；有签名但结构非法 → PXL006。
                // 这条钉住两个码的边界（审查 R4-03：Java 曾在第一类上报 PXL006，与 Python 相反）。
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", stampPngDocument("art/tile.png").getBytes(UTF8));
                files.put("art/tile.png", "这不是 PNG".getBytes(UTF8));
                PxlFormatException failure = expectCompileFailure("entry.pxl", files, Codes.PXL009,
                        "声明为 png 但字节不是 PNG");
                equal("entry.pxl -> art/tile.png", failure.chain(), "失败文件是那张 png");
                files.put("art/tile.png", new byte[0]);
                expectCompileFailure("entry.pxl", files, Codes.PXL009, "零字节的 png asset");
                // 对照：签名正确但结构非法（只有 IHDR）→ PXL006，不是 PXL009。
                files.put("art/tile.png", ihdrOnlyPng(1, 1));
                expectCompileFailure("entry.pxl", files, Codes.PXL006, "有签名但缺 IDAT/IEND");
            }
        });
        run("v1.output-alias-covers-dependencies", new Body() {
            public void run() throws Exception {
                // PRD §13.2 CLI002：输出目标不得与**任何被读的源文件**别名冲突。只挡入口时，
                // -o 指向被引用的素材会把它静默改写成 PNG（审查 R3-04）。
                java.nio.file.Path root = java.nio.file.Files.createTempDirectory("texel-alias");
                try {
                    java.nio.file.Path parts = root.resolve("parts");
                    java.nio.file.Files.createDirectories(parts);
                    java.nio.file.Files.write(root.resolve("entry.pxl"),
                            v1Document(1, 1, "{\"a\":{\"kind\":\"pxl\",\"path\":\"parts/blade.pxl\"}}",
                                    "[{\"op\":\"stamp\",\"asset\":\"a\",\"x\":0,\"y\":0}]")
                                    .getBytes(UTF8));
                    java.nio.file.Files.write(parts.resolve("blade.pxl"),
                            currentDocument(1, 1, "[\"112233FF\"]", "[{\"op\":\"fill\",\"slot\":1}]")
                                    .getBytes(UTF8));
                    PxlCompiler.Compiled out = PxlCompiler.compile("entry.pxl",
                            FileAssetResolver.rootedAt(root));
                    java.io.File base = root.toFile();
                    equal("parts/blade.pxl", PxlCompiler.conflictingSource(
                            parts.resolve("blade.pxl").toString(), base, out.dependencies()),
                            "依赖必须被挡住");
                    equal("entry.pxl", PxlCompiler.conflictingSource(
                            root.resolve("entry.pxl").toString(), base, out.dependencies()),
                            "入口同样必须被挡住");
                    equal(null, PxlCompiler.conflictingSource(
                            root.resolve("out.png").toString(), base, out.dependencies()),
                            "无关的输出路径不得误报");
                } finally {
                    deleteQuietly(root);
                }
            }
        });
        run("v1.ct2-trns-before-plte-is-pxl006", new Body() {
            public void run() throws Exception {
                // §8.5：tRNS 存在时 PLTE 必须更早。色彩类型 3 的等价用例会先被"ct3 必须有
                // PLTE"抓住（语料 neg-png-trns-before-plte 就是 ct3），所以 ct2 这条支路
                // 一直没有门禁（审查 PM6）。
                Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
                files.put("entry.pxl", v1Document(1, 1,
                        "{\"img\":{\"kind\":\"png\",\"path\":\"art/tile.png\"}}",
                        "[{\"op\":\"stamp\",\"asset\":\"img\",\"x\":0,\"y\":0}]").getBytes(UTF8));
                files.put("art/tile.png", ct2TrnsBeforePltePng());
                PxlFormatException failure = expectCompileFailure("entry.pxl", files, Codes.PXL006,
                        "ct2 的 tRNS 早于 PLTE");
                equal("entry.pxl -> art/tile.png", failure.chain(), "失败文件是那张 png");
            }
        });
    }

    /** 两份子文档各 4096 个 asset 名，全部指向同一个 tile.pxl：唯一文件 4、读取 8195。 */
    private static Map<String, byte[]> samePxlManyNames() {
        byte[] tile = currentDocument(1, 1, "[\"FF0000FF\"]", "[{\"op\":\"fill\",\"slot\":1}]")
                .getBytes(UTF8);
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put("tile.pxl", tile);
        StringBuilder entryAssets = new StringBuilder();
        StringBuilder entryOps = new StringBuilder();
        for (int child = 1; child <= 2; child++) {
            StringBuilder assets = new StringBuilder();
            StringBuilder ops = new StringBuilder();
            for (int index = 0; index < 4096; index++) {
                String name = "a" + pad4(index);
                if (index > 0) {
                    assets.append(',');
                    ops.append(',');
                }
                assets.append('"').append(name).append("\":{\"kind\":\"pxl\",\"path\":\"tile.pxl\"}");
                ops.append("{\"op\":\"stamp\",\"asset\":\"").append(name)
                        .append("\",\"x\":0,\"y\":0}");
            }
            files.put("c" + child + ".pxl",
                    v1Document(1, 1, "{" + assets + "}", "[" + ops + "]").getBytes(UTF8));
            if (child > 1) {
                entryAssets.append(',');
                entryOps.append(',');
            }
            entryAssets.append("\"c").append(child).append("\":{\"kind\":\"pxl\",\"path\":\"c")
                    .append(child).append(".pxl\"}");
            entryOps.append("{\"op\":\"stamp\",\"asset\":\"c").append(child)
                    .append("\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(1, 1, "{" + entryAssets + "}", "[" + entryOps + "]")
                .getBytes(UTF8));
        return files;
    }

    /** {@code total} 张互不相同的 2048x2048 PNG；前 {@code complete} 张完整，其余只到 IHDR。 */
    private static Map<String, byte[]> manyDistinctPngs(int total, int complete) {
        byte[] rgba = new byte[2048 * 2048 * 4];
        byte[] full = PngEncoder.encode(2048, 2048, rgba);
        byte[] headerOnly = ihdrOnlyPng(2048, 2048);
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        StringBuilder assets = new StringBuilder();
        StringBuilder ops = new StringBuilder();
        for (int index = 0; index < total; index++) {
            String name = "p" + pad4(index);
            String path = "art/p" + pad4(index) + ".png";
            files.put(path, index < complete ? full : headerOnly);
            if (index > 0) {
                assets.append(',');
                ops.append(',');
            }
            assets.append('"').append(name).append("\":{\"kind\":\"png\",\"path\":\"")
                    .append(path).append("\"}");
            ops.append("{\"op\":\"stamp\",\"asset\":\"").append(name)
                    .append("\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(1, 1, "{" + assets + "}", "[" + ops + "]")
                .getBytes(UTF8));
        return files;
    }

    /** 只有 签名 + IHDR 的 PNG（其后没有 IDAT/IEND）：读头部合法，解码必报 PXL006。 */
    private static byte[] ihdrOnlyPng(int width, int height) {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] signature = {(byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
        out.write(signature, 0, signature.length);
        byte[] ihdr = {
            (byte) (width >>> 24), (byte) (width >>> 16), (byte) (width >>> 8), (byte) width,
            (byte) (height >>> 24), (byte) (height >>> 16), (byte) (height >>> 8), (byte) height,
            8,
            0,
            0,
            0,
            0,
        };
        writeChunk(out, "IHDR", ihdr);
        return out.toByteArray();
    }

    /** 计数 resolver：同一逻辑路径被 resolve 几次（门禁从测试侧观测缓存，生产 core 不埋点）。 */
    private static final class CountingResolver implements AssetResolver {

        private final Map<String, byte[]> files;
        private final Map<String, Integer> counts = new LinkedHashMap<String, Integer>();

        CountingResolver(Map<String, byte[]> files) {
            this.files = files;
        }

        public Result resolve(String logicalPath) {
            Integer previous = counts.get(logicalPath);
            counts.put(logicalPath, previous == null ? 1 : previous + 1);
            byte[] content = files.get(logicalPath);
            if (content == null) {
                return Result.of(Status.MISSING);
            }
            return Result.ok(content);
        }

        int count(String logicalPath) {
            Integer value = counts.get(logicalPath);
            return value == null ? 0 : value;
        }
    }

    /** 节点数 4097 > 4096 且唯一文件 8193 > 8192：用来钉 R4 的表内顺序。 */
    private static Map<String, byte[]> nodesAndFilesOverLimit() {
        byte[] png = PngEncoder.encode(1, 1, new byte[] {(byte) 0xFF, 0, 0, (byte) 0xFF});
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        StringBuilder entryAssets = new StringBuilder();
        StringBuilder entryOps = new StringBuilder();
        for (int index = 0; index < PxlCompiler.MAX_NODES; index++) {
            String name = "d" + pad4(index);
            String left = name + "x.png";
            String right = name + "y.png";
            files.put(left, png);
            files.put(right, png);
            StringBuilder assets = new StringBuilder();
            assets.append("\"p\":{\"kind\":\"png\",\"path\":\"").append(left).append("\"},");
            assets.append("\"q\":{\"kind\":\"png\",\"path\":\"").append(right).append("\"}");
            files.put(name + ".pxl", v1Document(1, 1, "{" + assets + "}",
                    "[{\"op\":\"stamp\",\"asset\":\"p\",\"x\":0,\"y\":0},"
                            + "{\"op\":\"stamp\",\"asset\":\"q\",\"x\":0,\"y\":0}]").getBytes(UTF8));
            if (index > 0) {
                entryAssets.append(',');
                entryOps.append(',');
            }
            entryAssets.append("\"a").append(name).append("\":{\"kind\":\"pxl\",\"path\":\"")
                    .append(name).append(".pxl\"}");
            entryOps.append("{\"op\":\"stamp\",\"asset\":\"a").append(name)
                    .append("\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(1, 1, "{" + entryAssets + "}", "[" + entryOps + "]")
                .getBytes(UTF8));
        return files;
    }

    /** entry -> parts/a.pxl -> parts/deep/b.pxl，最内层带一条坏 op。 */
    private static Map<String, byte[]> nestedBrokenDocument(String innerOp) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put("entry.pxl", v1Document(1, 1,
                "{\"a\":{\"kind\":\"pxl\",\"path\":\"parts/a.pxl\"}}",
                "[{\"op\":\"stamp\",\"asset\":\"a\",\"x\":0,\"y\":0}]").getBytes(UTF8));
        files.put("parts/a.pxl", v1Document(1, 1,
                "{\"b\":{\"kind\":\"pxl\",\"path\":\"deep/b.pxl\"}}",
                "[{\"op\":\"stamp\",\"asset\":\"b\",\"x\":0,\"y\":0}]").getBytes(UTF8));
        files.put("parts/deep/b.pxl",
                v1Document(1, 1, "{}", "[" + innerOp + "]").getBytes(UTF8));
        return files;
    }

    /** 60 个节点各 stamp 同一张 2048x2048 的 PNG；唯一像素 2^22，预算全合法。 */
    private static Map<String, byte[]> manyNodesOnePng(int nodes) {
        byte[] rgba = new byte[2048 * 2048 * 4];
        byte[] png = PngEncoder.encode(2048, 2048, rgba);
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        files.put("big.png", png);
        StringBuilder entryAssets = new StringBuilder();
        StringBuilder entryOps = new StringBuilder();
        for (int index = 0; index < nodes; index++) {
            String name = "n" + pad4(index);
            files.put(name + ".pxl", v1Document(1, 1,
                    "{\"big\":{\"kind\":\"png\",\"path\":\"big.png\"}}",
                    "[{\"op\":\"stamp\",\"asset\":\"big\",\"x\":0,\"y\":0}]").getBytes(UTF8));
            if (index > 0) {
                entryAssets.append(',');
                entryOps.append(',');
            }
            entryAssets.append("\"a").append(name).append("\":{\"kind\":\"pxl\",\"path\":\"")
                    .append(name).append(".pxl\"}");
            entryOps.append("{\"op\":\"stamp\",\"asset\":\"a").append(name)
                    .append("\",\"x\":0,\"y\":0}");
        }
        files.put("entry.pxl", v1Document(1, 1, "{" + entryAssets + "}", "[" + entryOps + "]")
                .getBytes(UTF8));
        return files;
    }

    /** 1x1、色彩类型 2（RGB）、chunk 顺序 IHDR, tRNS, PLTE, IDAT, IEND —— 必须 PXL006。 */
    private static byte[] ct2TrnsBeforePltePng() {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] signature = {(byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
        out.write(signature, 0, signature.length);
        byte[] ihdr = {
            0, 0, 0, 1,
            0, 0, 0, 1,
            8,
            2,
            0,
            0,
            0,
        };
        writeChunk(out, "IHDR", ihdr);
        writeChunk(out, "tRNS", new byte[6]);
        writeChunk(out, "PLTE", new byte[] {0, 0, 0});
        java.util.zip.Deflater deflater = new java.util.zip.Deflater();
        byte[] row = {0, 1, 2, 3};
        deflater.setInput(row);
        deflater.finish();
        byte[] buffer = new byte[64];
        int length = deflater.deflate(buffer);
        deflater.end();
        byte[] idat = new byte[length];
        System.arraycopy(buffer, 0, idat, 0, length);
        writeChunk(out, "IDAT", idat);
        writeChunk(out, "IEND", new byte[0]);
        return out.toByteArray();
    }

    private static void writeChunk(java.io.ByteArrayOutputStream out, String type, byte[] payload) {
        out.write((payload.length >>> 24) & 0xFF);
        out.write((payload.length >>> 16) & 0xFF);
        out.write((payload.length >>> 8) & 0xFF);
        out.write(payload.length & 0xFF);
        byte[] kind = type.getBytes(UTF8);
        out.write(kind, 0, kind.length);
        out.write(payload, 0, payload.length);
        java.util.zip.CRC32 crc = new java.util.zip.CRC32();
        crc.update(kind);
        crc.update(payload);
        long value = crc.getValue();
        out.write((int) ((value >>> 24) & 0xFF));
        out.write((int) ((value >>> 16) & 0xFF));
        out.write((int) ((value >>> 8) & 0xFF));
        out.write((int) (value & 0xFF));
    }

    private static void deleteQuietly(java.nio.file.Path root) {
        try {
            java.util.List<java.nio.file.Path> paths =
                    new java.util.ArrayList<java.nio.file.Path>();
            java.nio.file.Files.walk(root).forEach(paths::add);
            java.util.Collections.reverse(paths);
            for (java.nio.file.Path path : paths) {
                java.nio.file.Files.deleteIfExists(path);
            }
        } catch (java.io.IOException ignored) {
            // 临时目录清不掉不影响判定。
        } catch (java.io.UncheckedIOException ignored) {
            // 同上。
        }
    }

// ------------------------------------------------------------ 第五轮：mirror 面积 / 假门禁

    /**
     * G-02：会话全局光栅必须把 **mirror** 的写入面积算进去。
     *
     * <p>mirror 的面积在 {@code fromW/fromH}（目标侧面积与 from 同尺寸），而它的 {@code w/h}
     * 从来不被赋值——按 {@code op.w/op.h} 算会恒得 0，于是"只由 mirror 组成的超预算图"在
     * 会话光栅里完全不计数，Java 直接编译成功并真的写掉几亿个像素。Python 侧一直是
     * {@code fromW/fromH}，所以这条同时是对齐门禁。
     */
    private static void mirrorRasterWork() {
        run("v1.mirror-only-session-raster-is-pxl011", new Body() {
            public void run() throws Exception {
                // 5 份 2048² 纯 mirror 文档：mirror 5 x 2^22 = 20,971,520，加 4 次 stamp
                // 4 x 2^22 = 16,777,216，合计 37,748,736 > 2^24。
                // 把 mirror 算成 0 时总数恰好是 16,777,216（== 上限，不超），于是编译成功 -> 这条会红。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        mirrorChain(5), Codes.PXL011, "mirror-only 的会话超限");
                contains(failure.getMessage(), "37748736", "工作量必须是相位 A 算出来的那个数");
            }
        });
        run("v1.mirror-raster-matches-python-fixture", new Body() {
            public void run() throws Exception {
                // 与 Python 侧 tests/test_python_compiler.py 的 raster_heavy() **同一份形状**
                // （4 份 2048² 的 mirror + stamp）：两侧报的工作量必须逐位相同。
                PxlFormatException failure = expectCompileFailure("entry.pxl",
                        mirrorChain(4), Codes.PXL011, "4 份 mirror 链");
                contains(failure.getMessage(), "29360128", "与 Python 侧同一个数");
            }
        });
        run("v1.ref-depth-message-reports-actual-depth", new Body() {
            public void run() throws Exception {
                // 深度消息印的是**实际走到的层数**（在进入第 65 篇之前停），并明写"更深的层
                // 未被读取"——否则 66 / 70 篇的链都会打"深度 65"，读者会把它误当成链长
                // （审查 R4-04）。深度必须 fail-fast 才符合 §10.4「进入更深一层之前判」，
                // 所以输入链的真实长度本就不可知；这里钉的是措辞不再冒充链长。
                for (int depth = 65; depth <= 66; depth++) {
                    PxlFormatException failure = expectCompileFailure("entry.pxl", chain(depth),
                            Codes.PXL011, depth + " 层越界");
                    contains(failure.getMessage(), "深度 65",
                            depth + " 层链：停在第 65 篇");
                    contains(failure.getMessage(), "拒绝再进入第 65 篇",
                            depth + " 层链：必须说明停在哪儿");
                    contains(failure.getMessage(), "更深的层未被读取",
                            depth + " 层链：不得让人把 65 读成链长");
                }
            }
        });
    }

    /** {@code docs} 份 2048x2048 文档的链，每份只有一条全幅 mirror（flipX）+ 一次子文档 stamp。 */
    private static Map<String, byte[]> mirrorChain(int docs) {
        Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        String mirror = "{\"op\":\"mirror\",\"flipX\":true,\"from\":{\"x\":0,\"y\":0,"
                + "\"w\":2048,\"h\":2048},\"to\":{\"x\":0,\"y\":0}}";
        String[] names = new String[docs];
        for (int index = 0; index < docs; index++) {
            names[index] = index == 0 ? "entry.pxl" : "p" + index + ".pxl";
        }
        for (int index = 0; index < docs; index++) {
            if (index == docs - 1) {
                files.put(names[index], v1Document(2048, 2048, "{}", "[" + mirror + "]")
                        .getBytes(UTF8));
                continue;
            }
            files.put(names[index], v1Document(2048, 2048,
                    "{\"next\":{\"kind\":\"pxl\",\"path\":\"" + names[index + 1] + "\"}}",
                    "[" + mirror + ",{\"op\":\"stamp\",\"asset\":\"next\",\"x\":0,\"y\":0}]")
                    .getBytes(UTF8));
        }
        return files;
    }

    private static void report() {
        // 不变量必须**先**判、再打印 summary：否则 CI 里 grep "FAILURES=0" 会把
        // 「总数不符但 FAILURES 还没加上」读成绿（DSv4 round8 P2-1）。
        for (String skip : SKIPPED) {
            String name = skip.contains(" -> ") ? skip.substring(0, skip.indexOf(" -> ")) : skip;
            if (!ALLOWED_SKIPS.contains(name)) {
                FAILURES.add("非白名单跳过: " + name);
            }
        }
        if (executed + SKIPPED.size() != EXPECTED_TOTAL) {
            FAILURES.add("用例总数 " + (executed + SKIPPED.size()) + " != " + EXPECTED_TOTAL);
        }
        System.out.println("COMPILER_UNITS=" + executed + " SKIPPED=" + SKIPPED.size()
                + " FAILURES=" + FAILURES.size());
        for (String skip : SKIPPED) {
            System.out.println("  SKIP " + skip);
        }
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("COMPILER UNIT TESTS FAILED");
            System.exit(1);
        }
        System.out.println("COMPILER UNIT TESTS OK");
    }
}
