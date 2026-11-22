package io.github.texel.cli;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * CLI 的行为测试（无第三方框架）。
 *
 * <p>刻意用<b>子进程</b>而不是在进程内调 {@code run}：退出码、stderr 的"每条错误一行"、
 * 以及"失败不留半份文件"都只有在真的跑一个进程时才算被测到。进程内调用会把
 * {@code System.exit} 与标准流的时序整个绕过去。
 */
public final class CliTestMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");

    /** 仓库内的规范样例；路径来自 Gradle 传进来的 {@code txel.repo}。 */
    private static Path capabilitiesFixture() {
        String repo = System.getProperty("txel.repo");
        if (repo == null || repo.isEmpty()) {
            throw new IllegalStateException("缺少 -Dtxel.repo，无法定位能力档案样例");
        }
        return java.nio.file.Paths.get(repo, "spec", "conformance", "capabilities", "pxl-v1.json");
    }
    private static final List<String> FAILURES = new ArrayList<String>();
    private static int executed;
    private static Path workspace;

    private CliTestMain() {
    }

    public static void main(String[] args) throws IOException {
        workspace = Files.createTempDirectory("texel-compiler-cli-tests");
        try {
            run("help", new Body() {
                public void run() throws Exception {
                    Result result = cli("--help");
                    check(result.exit == 0, "help 退出码应为 0，实际 " + result.exit);
                    check(result.stdout.contains("usage: texel-compiler"), "usage 打到 stdout");
                    check(result.stderr.isEmpty(), "help 不应有 stderr");
                }
            });

            run("capabilities-json", new Body() {
                public void run() throws Exception {
                    // PRD §6.3：能力档案是跨实现的机器可读契约。期望值直接取仓库里的规范样例，
                    // 不在测试里再抄一份——抄一份就等于允许两边悄悄分叉。
                    Result result = cli("capabilities", "--json");
                    check(result.exit == 0, "capabilities 退出码应为 0，实际 " + result.exit
                            + " / " + result.stderr);
                    check(result.stderr.isEmpty(), "能力档案不得混入 stderr: " + result.stderr);
                    String expected = new String(Files.readAllBytes(capabilitiesFixture()), UTF8);
                    check(expected.equals(result.stdout),
                            "能力档案与 spec/conformance/capabilities/pxl-v1.json 逐字节不同");
                    // 样例里的档位必须与 core 声明的档位一致（否则 harness 会按样例选例、按 core 判版本，两边打架）。
                    check(result.stdout.contains("\"conformance_profile\": \""
                                    + io.github.texel.core.PxlParser.conformanceProfile() + "\""),
                            "能力档案的 conformance_profile 与 PxlParser.conformanceProfile() 不一致");
                    check(!result.stdout.isEmpty()
                            && result.stdout.charAt(result.stdout.length() - 1) == '\n',
                            "能力档案应以单个 LF 结尾");
                }
            });

            run("capabilities-without-json-is-usage-error", new Body() {
                public void run() throws Exception {
                    Result result = cli("capabilities");
                    check(result.exit == 2, "capabilities 不带 --json 应为 2，实际 " + result.exit);
                    check(result.stderrLineCount() == 1, "用法错误恰好一行");
                }
            });

            run("v1-compile-single-target", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "pxlcompile");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
                    Path output = root.resolve("out.png");
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", output.toString());
                    check(result.exit == 0, "compile 应成功，实际 " + result.exit + " / " + result.stderr);
                    check(Files.exists(output), "应产出 out.png");
                    check(isPng(Files.readAllBytes(output)), "产物应是 PNG");
                }
            });

            run("v1-compile-is-determined", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1det");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
                    Path first = root.resolve("a.png");
                    Path second = root.resolve("b.png");
                    cli("compile", "entry.pxl", "--asset-root", root.toString(), "-o", first.toString());
                    cli("compile", "entry.pxl", "--asset-root", root.toString(), "-o", second.toString());
                    check(java.util.Arrays.equals(Files.readAllBytes(first),
                            Files.readAllBytes(second)), "两次编译的 PNG 字节必须相同");
                }
            });

            run("v1-check-writes-nothing", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1check");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
                    // 审查 R4-09：原先断言的是 !Files.exists(root/"entry.png") —— 没有任何实现会写
                    // 这个名字，该断言永远为真、等于空转。改成**逐项快照**：check 前后，asset root 与
                    // 子进程 cwd 里的文件清单必须逐字节相同（多一个 .tmp、多一份清单、少一个输入都红）。
                    Path cwd = Files.createTempDirectory(workspace, "v1checkcwd");
                    List<String> rootBefore = snapshot(root);
                    List<String> cwdBefore = snapshot(cwd);
                    Result result = cliIn(cwd, "check", "entry.pxl", "--asset-root", root.toString());
                    check(result.exit == 0, "check 应成功，实际 " + result.exit + " / " + result.stderr);
                    List<String> rootAfter = snapshot(root);
                    List<String> cwdAfter = snapshot(cwd);
                    check(rootBefore.equals(rootAfter), "check 不得改动 asset root，差异 = "
                            + difference(rootBefore, rootAfter));
                    check(cwdBefore.equals(cwdAfter), "check 不得在工作目录留下任何文件，差异 = "
                            + difference(cwdBefore, cwdAfter));
                }
            });

            run("v1-check-rejects-output", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1checkout");
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Result result = cli("check", "entry.pxl", "--asset-root", root.toString(),
                            "-o", root.resolve("x.png").toString());
                    check(result.exit == 2, "check 带 -o 应为 2，实际 " + result.exit);
                }
            });

            run("check-with-deps-dash-prints-the-manifest", new Body() {
                public void run() throws Exception {
                    // check 不产出 PNG，清单就是它唯一的产物：`--deps -` 时不打 stdout 等于命令
                    // 返回 0 却什么也没给出（Python 侧一直是打的，2026-11-20 审查 P2）。
                    Path root = Files.createTempDirectory(workspace, "v1checkdash");
                    Files.write(root.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Result result = cli("check", "entry.pxl", "--asset-root", root.toString(),
                            "--deps", "-");
                    check(result.exit == 0, "check 应为 0，实际 " + result.exit
                            + " / " + result.stderr);
                    check(!result.stdout.isEmpty(),
                            "check --deps - 必须把依赖清单打到 stdout");
                    check(result.stdout.contains("entry.pxl"),
                            "清单里要有入口逻辑路径: " + result.stdout);
                    check(result.stderr.isEmpty(), "成功路径不该有 stderr: " + result.stderr);
                }
            });

            run("v1-missing-dependency-exit-3", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1missing");
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", root.resolve("out.png").toString());
                    check(result.exit == 3, "缺依赖应为 3，实际 " + result.exit);
                    check(result.stderrLineCount() == 1, "错误必须恰好一行");
                    check(result.stderr.contains("PXL009"), "应报 PXL009: " + result.stderr);
                    check(!Files.exists(root.resolve("out.png")), "失败不得留下输出");
                }
            });

            run("v1-oversized-png-declared-pxl-is-pxl009", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1oversizedpngaspxl");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"),
                            v1EntryWithAsset("pxl", "parts/blob.pxl").getBytes(UTF8));
                    createSparse(root.resolve("parts/blob.pxl"), 64L * 1024L * 1024L + 1L,
                            new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A});
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", root.resolve("out.png").toString());
                    check(result.exit == 3, "类型不符应为 PXL 语义错误，实际 " + result.exit);
                    check(result.stderr.contains("PXL009"), "PNG 声明为 pxl 应报 PXL009: " + result.stderr);
                    check(!result.stderr.contains("PXL002"), "类型不符不应先报 PXL002: " + result.stderr);
                }
            });

            run("v1-oversized-non-png-declared-png-is-pxl009", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1oversizednonpng");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"),
                            v1EntryWithAsset("png", "parts/blob.png").getBytes(UTF8));
                    createSparse(root.resolve("parts/blob.png"), 64L * 1024L * 1024L + 1L,
                            new byte[] {0, 1, 2, 3});
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", root.resolve("out.png").toString());
                    check(result.exit == 3, "类型不符应为 PXL 语义错误，实际 " + result.exit);
                    check(result.stderr.contains("PXL009"), "非 PNG 声明为 png 应报 PXL009: " + result.stderr);
                    check(!result.stderr.contains("PXL002"), "类型不符不应先报 PXL002: " + result.stderr);
                }
            });

            run("v1-directory-iteration-is-host-io", new Body() {
                public void run() throws Exception {
                    directoryIterationIsHostIo();
                }
            });

            run("v1-locked-source-is-host-io", new Body() {
                public void run() throws Exception {
                    // Windows 的范围锁阻止子进程读取；POSIX 锁是协作式，不能充当本判据。
                    if (!System.getProperty("os.name").startsWith("Windows")) {
                        System.out.println("CLI SKIP v1-locked-source-is-host-io: Windows mandatory locks");
                        return;
                    }
                    for (String kind : new String[] {"pxl", "png"}) {
                        for (boolean large : new boolean[] {false, true}) {
                            Path root = Files.createTempDirectory(workspace, "locked-source");
                            Path source = root.resolve("blob.bin");
                            byte[] prefix = "pxl".equals(kind) ? v0Leaf().getBytes(UTF8)
                                    : io.github.texel.core.PngEncoder.encode(1, 1, new byte[4]);
                            createSparse(source, large ? 64L * 1024L * 1024L + 1L
                                    : prefix.length, prefix);
                            Files.write(root.resolve("entry.pxl"),
                                    v1EntryWithAsset(kind, "blob.bin").getBytes(UTF8));
                            try (java.nio.channels.FileChannel channel = java.nio.channels.FileChannel.open(
                                    source, java.nio.file.StandardOpenOption.READ,
                                    java.nio.file.StandardOpenOption.WRITE);
                                    java.nio.channels.FileLock lock = channel.lock()) {
                                check(lock.isValid(), "必须持有真实文件锁");
                                Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                                        "-o", root.resolve("out.png").toString(),
                                        "--deps", root.resolve("deps.tsv").toString());
                                check(result.exit == 4, kind + " large=" + large
                                        + " 锁失败必须是宿主 I/O: " + result.stderr);
                                check(result.stderr.contains("CLI001"), "缺少 CLI001: " + result.stderr);
                                check(!result.stderr.contains("PXL00"), "宿主锁失败不得伪装 PXL 错误");
                                check(!Files.exists(root.resolve("out.png")), "不得留下输出");
                                check(!Files.exists(root.resolve("deps.tsv")), "不得留下清单");
                            }
                        }
                    }
                }
            });

            run("v1-deps-manifest", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "v1deps");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
                    Path deps = root.resolve("deps.tsv");
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", root.resolve("out.png").toString(), "--deps", deps.toString());
                    check(result.exit == 0, "compile 应成功，实际 " + result.exit + " / " + result.stderr);
                    java.util.List<String> lines = Files.readAllLines(deps, UTF8);
                    check(lines.size() == 2, "清单应有两行，实际 " + lines.size());
                    check(lines.get(0).startsWith("entry	pxl	entry.pxl	"), "第一行必须是入口: " + lines.get(0));
                    check(lines.get(1).startsWith("dependency	pxl	parts/blade.pxl	"),
                            "第二行应是依赖: " + lines.get(1));
                }
            });

            run("v1-deps-hardlink-cannot-truncate-entry", new Body() {
                public void run() throws Exception {
                    // 回归（发布态复核 P1/F01）：把清单路径硬链接到入口上，路径相等检查抓不到
                    // （它们是两个不同的目录项，指向同一个 inode），而 Files.write 会截断共享
                    // 内容——入口源文件因此被写成 TSV。必须按文件身份拒绝，且源文件字节不变。
                    Path root = Files.createTempDirectory(workspace, "v1hardlink");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
                    byte[] before = Files.readAllBytes(root.resolve("entry.pxl"));
                    Path alias = root.resolve("deps-alias.tsv");
                    try {
                        Files.createLink(alias, root.resolve("entry.pxl"));
                    } catch (UnsupportedOperationException | IOException notSupported) {
                        return; // 该文件系统不支持硬链接：本条不适用
                    }
                    Result result = cli("check", "entry.pxl", "--asset-root", root.toString(),
                            "--deps", alias.toString());
                    check(result.exit != 0, "硬链接到入口的 --deps 必须被拒，实际 exit "
                            + result.exit);
                    check(java.util.Arrays.equals(before,
                            Files.readAllBytes(root.resolve("entry.pxl"))),
                            "入口源文件不得被改写");
                }
            });

            run("no-arguments-is-usage-error", new Body() {
                public void run() throws Exception {
                    Result result = cli();
                    check(result.exit == 2, "无参数应为 2，实际 " + result.exit);
                    check(result.stderrLineCount() == 1, "用法错误恰好一行");
                }
            });

            run("single-file", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "single");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Result result = cli(source.toString());
                    check(result.exit == 0, "退出码 0，实际 " + result.exit + " / " + result.stderr);
                    Path png = directory.resolve("a.png");
                    check(Files.exists(png), "应产出 a.png");
                    byte[] data = Files.readAllBytes(png);
                    check(isPng(data), "产物应是 PNG");
                }
            });

            run("explicit-output", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "output");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Path target = directory.resolve("named.png");
                    Result result = cli("-o", target.toString(), source.toString());
                    check(result.exit == 0, "退出码 0，实际 " + result.exit);
                    check(Files.exists(target), "应产出指定的 named.png");
                    check(!Files.exists(directory.resolve("a.png")), "不应额外产出 a.png");
                }
            });

            run("directory-is-sorted-and-recursive", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "tree");
                    Files.createDirectories(directory.resolve("sub"));
                    Files.write(directory.resolve("b.pxl"), validDocument().getBytes(UTF8));
                    Files.write(directory.resolve("a.pxl"), validDocument().getBytes(UTF8));
                    Files.write(directory.resolve("sub").resolve("c.pxl"),
                            validDocument().getBytes(UTF8));
                    Result result = cli(directory.toString());
                    check(result.exit == 0, "退出码 0，实际 " + result.exit);
                    check(Files.exists(directory.resolve("a.png")), "a.png");
                    check(Files.exists(directory.resolve("b.png")), "b.png");
                    check(Files.exists(directory.resolve("sub").resolve("c.png")), "递归收集");
                }
            });

            run("format-error-leaves-no-output", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "bad");
                    Path source = directory.resolve("bad.pxl");
                    Files.write(source, invalidDocument().getBytes(UTF8));
                    Result result = cli(source.toString());
                    check(result.exit == 1, "格式错误应为 1，实际 " + result.exit);
                    check(result.stderr.contains("PXL002"),
                            "错误行应带 PXL002，实际 " + result.stderr);
                    check(result.stderrLineCount() == 1, "每条错误恰好一行");
                    check(!Files.exists(directory.resolve("bad.png")), "不得留下半份输出");
                }
            });

            run("format-error-preserves-existing-target", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "preserve");
                    Path source = directory.resolve("bad.pxl");
                    Files.write(source, invalidDocument().getBytes(UTF8));
                    Path target = directory.resolve("bad.png");
                    byte[] sentinel = "keep-me".getBytes(UTF8);
                    Files.write(target, sentinel);
                    Result result = cli(source.toString());
                    check(result.exit == 1, "应为失败");
                    check(java.util.Arrays.equals(sentinel, Files.readAllBytes(target)),
                            "既有目标不得被破坏");
                }
            });

            run("output-overwrites-input-is-refused", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "collide");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Result result = cli("-o", source.toString(), source.toString());
                    check(result.exit == 2, "输出撞输入应为 2，实际 " + result.exit);
                    check(!result.stderr.isEmpty(), "应有说明");
                }
            });

            run("out-with-many-inputs-is-refused", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "many");
                    Files.write(directory.resolve("a.pxl"), validDocument().getBytes(UTF8));
                    Files.write(directory.resolve("b.pxl"), validDocument().getBytes(UTF8));
                    Result result = cli("-o", directory.resolve("x.png").toString(),
                            directory.resolve("a.pxl").toString(),
                            directory.resolve("b.pxl").toString());
                    check(result.exit == 2, "多输入配 -o 应为 2，实际 " + result.exit);
                }
            });

            run("error-message-with-newline-stays-one-line", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "msg");
                    Path source = directory.resolve("evil.pxl");
                    // op 名里带 \n：错误消息会把 op 名原样带出来，不转义就会变成两行。
                    Files.write(source, ("{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\","
                            + "\"width\":1,\"height\":1,\"palette\":[\"FF0000FF\"],"
                            + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\","
                            + "\"ops\":[{\"op\":\"a\\nb\"}]}]}").getBytes(UTF8));
                    Result result = cli(source.toString());
                    check(result.exit == 1, "应为格式失败，实际 " + result.exit);
                    check(result.stderrLineCount() == 1,
                            "错误消息里的换行必须被转义，实际 " + result.stderrLineCount() + " 行");
                }
            });

            run("out-pointing-at-directory-is-usage-error", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "outisdir");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Result result = cli("-o", directory.toString(), source.toString());
                    check(result.exit == 2, "输出是目录应为 2，实际 " + result.exit);
                    check(!result.stderr.contains(".texel-compiler-"),
                            "不应泄漏内部临时文件名: " + result.stderr);
                }
            });
            run("duplicate-input-is-deduplicated", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "dup");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    // 同一个文件给两次不是"两个输入撞车"：重复输入只处理一次。
                    // 与抽取器 importer 的 _dedupe 同一语义（那里 exit 0）。
                    Result result = cli(source.toString(), source.toString());
                    check(result.exit == 0, "重复输入应为 0，实际 " + result.exit
                            + " / " + result.stderr);
                    check(Files.exists(directory.resolve("a.png")), "应产出 a.png");
                }
            });

            run("hardlink-output-to-input-is-refused", new Body() {
                public void run() throws Exception {
                    // 两个路径不同、inode 相同：路径身份归不了这一类，必须问文件身份。
                    Path directory = Files.createTempDirectory(workspace, "hardlinkin");
                    Path source = directory.resolve("a.pxl");
                    byte[] document = validDocument().getBytes(UTF8);
                    Files.write(source, document);
                    Path target = directory.resolve("a.png");
                    try {
                        Files.createLink(target, source);
                    } catch (java.io.IOException failure) {
                        return;   // 该文件系统不支持硬链接。
                    } catch (UnsupportedOperationException failure) {
                        return;
                    }
                    Result result = cli("-o", target.toString(), source.toString());
                    check(result.exit == 2, "输出与输入同一 inode 应为 2，实际 " + result.exit
                            + " / " + result.stderr);
                    check(result.stderr.contains("CLI002"), "应是 CLI002: " + result.stderr);
                    check(java.util.Arrays.equals(document, Files.readAllBytes(source)),
                            "入口源文件不得被改写");
                }
            });

            run("out-with-directory-holding-many-files-is-usage-error", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "outdir");
                    Files.write(directory.resolve("a.pxl"), validDocument().getBytes(UTF8));
                    Files.write(directory.resolve("b.pxl"), validDocument().getBytes(UTF8));
                    Path target = directory.resolve("out.png");
                    Result result = cli("-o", target.toString(), directory.toString());
                    check(result.exit == 2, "目录展开成 2 个输入 + -o 应为 2，实际 " + result.exit);
                    // 诊断语必须指向真实原因(-o 只允许单输入)，不能报成"两个输入撞车"——
                    // 后者会把人引向错误的修法(去改输出路径，而不是去掉 -o)。
                    check(result.stderr.contains("-o"), "应说明是 -o 的约束: " + result.stderr);
                    check(!result.stderr.contains("同一个输出"),
                            "不应误报成输出冲突: " + result.stderr);
                    check(!Files.exists(target), "不应写出任何文件");
                }
            });

            run("empty-output-path-is-usage-error", new Body() {
                public void run() throws Exception {
                    // 空串会被 Files.isDirectory 当成当前目录，从而报"输出路径是一个目录:"
                    // 后面什么都没有——诊断指向错误原因。这条钉住专门的检查。
                    Path directory = Files.createTempDirectory(workspace, "emptyout");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Result result = cli("-o", "", source.toString());
                    check(result.exit == 2, "空输出路径应为 2，实际 " + result.exit);
                    check(result.stderrLineCount() == 1, "恰好一行");
                    check(result.stderr.contains("输出路径不能为空"),
                            "应说明原因，实际: " + result.stderr);
                }
            });

            run("deps-pointing-at-directory-keeps-the-png", new Body() {
                public void run() throws Exception {
                    // 清单目标是目录时必须在写任何东西之前挡住：走到改名阶段才发现的话，
                    // PNG 已经替换成功，命令失败却留下了新产物。
                    Path directory = Files.createTempDirectory(workspace, "depsdir");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path png = directory.resolve("out.png");
                    byte[] old = "OLD-PNG".getBytes(UTF8);
                    Files.write(png, old);
                    Path depsDirectory = directory.resolve("depsdir");
                    Files.createDirectory(depsDirectory);
                    Result result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                            "-o", png.toString(), "--deps", depsDirectory.toString());
                    check(result.exit == 2, "清单路径是目录应为 2，实际 " + result.exit
                            + " / " + result.stderr);
                    check(java.util.Arrays.equals(old, Files.readAllBytes(png)),
                            "用法错不得改动已有产物");
                    check(result.stderrLineCount() == 1, "恰好一行，实际: " + result.stderr);
                }
            });

            run("locked-deps-rolls-back-the-png", new Body() {
                public void run() throws Exception {
                    // 清单目标被别的进程打开时改名必然失败：这时 PNG 必须跟着回滚，
                    // 否则命令失败却留下"新 PNG + 旧清单"。
                    Path directory = Files.createTempDirectory(workspace, "lockeddeps");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path png = directory.resolve("out.png");
                    Path deps = directory.resolve("deps.tsv");
                    byte[] oldPng = "OLD-PNG".getBytes(UTF8);
                    byte[] oldDeps = "OLD-MANIFEST".getBytes(UTF8);
                    Files.write(png, oldPng);
                    Files.write(deps, oldDeps);
                    java.io.RandomAccessFile handle =
                            new java.io.RandomAccessFile(deps.toFile(), "r");
                    Result result;
                    try {
                        result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                                "-o", png.toString(), "--deps", deps.toString());
                    } finally {
                        handle.close();
                    }
                    if (result.exit == 0) {
                        // 该平台允许在文件被打开时替换（POSIX）：这条判据在本机无从验证。
                        return;
                    }
                    check(result.exit == 4, "I/O 失败应为 4，实际 " + result.exit
                            + " / " + result.stderr);
                    check(java.util.Arrays.equals(oldPng, Files.readAllBytes(png)),
                            "改名失败时 PNG 必须回滚：命令失败就等于什么都没变");
                    check(java.util.Arrays.equals(oldDeps, Files.readAllBytes(deps)),
                            "清单不得被改动");
                    java.util.List<String> leftovers = new ArrayList<String>();
                    java.nio.file.DirectoryStream<Path> stream =
                            Files.newDirectoryStream(directory, ".texel-*");
                    try {
                        for (Path path : stream) {
                            leftovers.add(path.getFileName().toString());
                        }
                    } finally {
                        stream.close();
                    }
                    check(leftovers.isEmpty(), "临时/备份文件必须清掉，实际 " + leftovers);
                }
            });

            run("hardlinked-png-target-keeps-its-identity", new Body() {
                public void run() throws Exception {
                    // 回滚只恢复内容的话，硬链接会退化成普通文件（链接关系永久丢失）。
                    Path directory = Files.createTempDirectory(workspace, "hardlinkrollback");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path real = directory.resolve("real.png");
                    Path png = directory.resolve("out.png");
                    byte[] old = "OLD-REAL".getBytes(UTF8);
                    Files.write(real, old);
                    try {
                        Files.createLink(png, real);
                    } catch (IOException unsupported) {
                        return;
                    }
                    Path deps = directory.resolve("deps.tsv");
                    byte[] oldDeps = "OLD-MANIFEST".getBytes(UTF8);
                    Files.write(deps, oldDeps);
                    java.io.RandomAccessFile handle =
                            new java.io.RandomAccessFile(deps.toFile(), "r");
                    Result result;
                    try {
                        result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                                "-o", png.toString(), "--deps", deps.toString());
                    } finally {
                        handle.close();
                    }
                    if (result.exit == 0) {
                        return;
                    }
                    check(result.exit == 4, "应为 4，实际 " + result.exit + " / " + result.stderr);
                    check(Files.isSameFile(png, real), "回滚必须恢复硬链接身份，而不是换个新文件");
                    check(java.util.Arrays.equals(old, Files.readAllBytes(real)), "旧内容必须原样");
                    check(java.util.Arrays.equals(oldDeps, Files.readAllBytes(deps)),
                            "清单不得被改动");
                    check(noTexelLeftovers(directory), "临时文件与备份都必须清掉");
                }
            });

            run("symlinked-png-target-stays-a-link", new Body() {
                public void run() throws Exception {
                    // 回滚不能把符号链接换成一个普通文件。
                    Path directory = Files.createTempDirectory(workspace, "symlinkrollback");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path real = directory.resolve("real.png");
                    Path png = directory.resolve("out.png");
                    byte[] old = "OLD-REAL".getBytes(UTF8);
                    Files.write(real, old);
                    try {
                        Files.createSymbolicLink(png, java.nio.file.Paths.get("real.png"));
                    } catch (IOException unsupported) {
                        return;
                    } catch (UnsupportedOperationException unsupported) {
                        return;
                    }
                    Path deps = directory.resolve("deps.tsv");
                    Files.write(deps, "OLD-MANIFEST".getBytes(UTF8));
                    java.io.RandomAccessFile handle =
                            new java.io.RandomAccessFile(deps.toFile(), "r");
                    Result result;
                    try {
                        result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                                "-o", png.toString(), "--deps", deps.toString());
                    } finally {
                        handle.close();
                    }
                    if (result.exit == 0) {
                        return;
                    }
                    check(result.exit == 4, "应为 4，实际 " + result.exit + " / " + result.stderr);
                    check(Files.isSymbolicLink(png), "回滚必须重建符号链接，而不是留下普通文件");
                    check(java.nio.file.Paths.get("real.png").equals(Files.readSymbolicLink(png)),
                            "链接目标必须原样");
                    check(java.util.Arrays.equals(old, Files.readAllBytes(real)),
                            "链接指向的文件不得被改写");
                    check(noTexelLeftovers(directory), "临时文件与备份都必须清掉");
                }
            });

            run("failed-png-write-keeps-the-manifest-off-stdout", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "depsdash");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Files.write(directory.resolve("blocker"), "x".getBytes(UTF8));
                    Result result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                            "-o", directory.resolve("blocker").resolve("out.png").toString(),
                            "--deps", "-");
                    check(result.exit == 4, "应为 4，实际 " + result.exit + " / " + result.stderr);
                    check(result.stdout.isEmpty(),
                            "PNG 没写出来时清单不该已经打到 stdout: " + result.stdout);
                }
            });

            run("hardlinked-deps-and-output-are-refused", new Body() {
                public void run() throws Exception {
                    // 两个路径不同、inode 相同：resolve() 看不出来，Java 侧靠 Files.isSameFile。
                    Path directory = Files.createTempDirectory(workspace, "hardlinkdeps");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path png = directory.resolve("out.png");
                    byte[] old = "OLD-PNG".getBytes(UTF8);
                    Files.write(png, old);
                    Path alias = directory.resolve("alias.tsv");
                    try {
                        Files.createLink(alias, png);
                    } catch (IOException failure) {
                        return;   // 该文件系统不支持硬链接：这条判据无从验证。
                    } catch (UnsupportedOperationException failure) {
                        return;
                    }
                    Result result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                            "-o", png.toString(), "--deps", alias.toString());
                    check(result.exit == 2, "输出与清单是同一份内容应为 2，实际 " + result.exit
                            + " / " + result.stderr);
                    check(java.util.Arrays.equals(old, Files.readAllBytes(png)),
                            "拒绝前不得改写产物");
                }
            });

            run("single-output-publish-leaves-no-backup", new Body() {
                public void run() throws Exception {
                    // 单产物必须是"临时文件直接原子替换目标"：备份若靠移走目标来建立，
                    // 中间那一刻目标路径不存在，被终止就只剩一份隐藏备份。
                    Path directory = Files.createTempDirectory(workspace, "singleref");
                    Files.write(directory.resolve("entry.pxl"), validDocument().getBytes(UTF8));
                    Path png = directory.resolve("out.png");
                    Files.write(png, "OLD-PNG".getBytes(UTF8));
                    Result result = cli("compile", "entry.pxl", "--asset-root", directory.toString(),
                            "-o", png.toString());
                    check(result.exit == 0, "应为 0，实际 " + result.exit + " / " + result.stderr);
                    java.util.List<String> leftovers = new ArrayList<String>();
                    java.nio.file.DirectoryStream<Path> stream =
                            Files.newDirectoryStream(directory, ".texel-*");
                    try {
                        for (Path path : stream) {
                            leftovers.add(path.getFileName().toString());
                        }
                    } finally {
                        stream.close();
                    }
                    check(leftovers.isEmpty(), "不得留下临时/备份文件，实际 " + leftovers);
                    byte[] head = java.util.Arrays.copyOf(Files.readAllBytes(png), 4);
                    check(java.util.Arrays.equals(
                                    new byte[] {(byte) 0x89, (byte) 'P', (byte) 'N', (byte) 'G'}, head),
                            "产物必须是 PNG");
                }
            });

            run("rollback-keeps-the-backup-when-restore-fails", new Body() {
                public void run() throws Exception {
                    // 双重故障（提交失败 + 恢复也失败）时，备份是人工恢复的唯一退路：
                    // 曾经它会被"没用上的备份"清理循环删掉，旧产物就此永久丢失。
                    Path directory = Files.createTempDirectory(workspace, "rollbackkeeps");
                    Path target = directory.resolve("out.png");
                    Path backup = directory.resolve(".texel-x-png.tmp.prev");
                    byte[] old = "OLD-PNG".getBytes(UTF8);
                    Files.write(target, "NEW-PNG".getBytes(UTF8));
                    Files.write(backup, old);
                    java.util.List<Path> committed = new ArrayList<Path>();
                    committed.add(target);
                    java.util.Map<Path, Main.Backup> backups =
                            new java.util.LinkedHashMap<Path, Main.Backup>();
                    backups.put(target, new Main.Backup(backup));
                    java.io.RandomAccessFile handle =
                            new java.io.RandomAccessFile(target.toFile(), "r");
                    java.util.List<Path> kept;
                    try {
                        kept = Main.rollback(committed, backups);
                    } finally {
                        handle.close();
                    }
                    if (!Files.exists(backup)) {
                        // 该平台允许替换被打开的文件：恢复成功、备份被正常消耗，
                        // 这条判据在本机无从验证。
                        return;
                    }
                    check(kept.contains(backup), "恢复失败的备份必须报出来，实际: " + kept);
                    check(java.util.Arrays.equals(old, Files.readAllBytes(backup)),
                            "备份必须保留旧内容");
                }
            });

            run("root-path-with-parent-segments-is-not-a-junction", new Body() {
                public void run() throws Exception {
                    // `dir/..` 是普通父目录引用：判据必须先把词法段归一，否则真路径与带 `..`
                    // 的路径比较不等，普通目录会被当成 junction 拒掉（2026-11-20 审查 P2）。
                    Path directory = Files.createTempDirectory(workspace, "dotdot");
                    Path inner = directory.resolve("inner");
                    Files.createDirectory(inner);
                    Files.write(directory.resolve("a.pxl"), validDocument().getBytes(UTF8));
                    Result result = cli(inner.resolve("..").toString());
                    check(result.exit == 0, "dir/.. 是普通目录，应为 0，实际 " + result.exit
                            + " / " + result.stderr);
                    check(!result.stderr.contains("junction"),
                            "普通父目录引用不得被当成链接: " + result.stderr);
                    check(Files.exists(directory.resolve("a.png")), "应产出 a.png");
                }
            });

            run("check-deps-file-is-published-only-after-encoding", new Body() {
                public void run() throws Exception {
                    // check 失败（这里是堆不够，失败点落在编译/编码阶段）不得留下**新的**依赖清单。
                    // 注意：本机实测这种 OOM 发生在清单写入之前，所以这条用例**分不出**"编码前后"
                    // 两种顺序；顺序本身由 tests/test_cli_identity.py 的源码顺序断言钉住。
                    // 4 M 像素、char 编码：48 MiB 堆下编码阶段必然 OOM（实测），正好检验顺序。
                    Path root = Files.createTempDirectory(workspace, "checkorder");
                    String document = "{\"format\":\"PXL\",\"version\":1,"
                            + "\"encoding\":\"char\",\"width\":2048,\"height\":2048,"
                            + "\"palette\":[\"FF0000FF\"],\"layers\":[{\"name\":\"b\","
                            + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":0}]}]}";
                    Files.write(root.resolve("entry.pxl"), document.getBytes(UTF8));
                    Path manifest = root.resolve("deps.tsv");
                    List<String> options = new ArrayList<String>();
                    options.add("-Xmx48m");
                    Result result = cliIn(root, options, "check", "entry.pxl",
                            "--asset-root", root.toString(), "--deps", manifest.toString());
                    if (result.exit == 0) {
                        // 这台机器/这个堆设置下编码没失败：判据无从验证，但成功路径必须有清单。
                        check(Files.exists(manifest), "成功的 check 必须写出清单");
                        return;
                    }
                    check(result.stderr.contains("CLI001"),
                            "这条判据依赖编码阶段的内存失败，实际: " + result.stderr);
                    check(!Files.exists(manifest),
                            "check 失败时不得留下新的依赖清单，实际 exit " + result.exit
                                    + " / " + result.stderr);
                }
            });

            run("double-dash-terminates-options", new Body() {
                public void run() throws Exception {
                    // `--` 之后的参数一律当路径：文件名以 - 开头时这是唯一办法。
                    Path directory = Files.createTempDirectory(workspace, "dashdash");
                    Path source = directory.resolve("-dash.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    Result result = cliIn(directory, "--", "-dash.pxl");
                    check(result.exit == 0, "-- 之后应作为路径，实际 exit " + result.exit
                            + " / " + result.stderr);
                    check(Files.exists(directory.resolve("-dash.png")),
                            "应产出 -dash.png");
                }
            });
            run("illegal-path-character-is-usage-error", new Body() {
                public void run() throws Exception {
                    // `Paths.get` 对非法字符抛 InvalidPathException（RuntimeException）。
                    // 不捕获就是 10 行栈回溯 + 整批不处理；契约是单行 + 退出 2。
                    Result result = cli("a<b.pxl");
                    check(result.exit == 2, "非法路径应为 2，实际 " + result.exit);
                    check(result.stderrLineCount() == 1,
                            "应恰好一行（不得打栈回溯），实际 " + result.stderrLineCount() + " 行");
                    check(!result.stderr.contains("Exception in thread"),
                            "不得输出栈回溯: " + result.stderr);
                }
            });
            run("missing-input-is-refused", new Body() {
                public void run() throws Exception {
                    Result result = cli(workspace.resolve("nope.pxl").toString());
                    check(result.exit == 2, "输入不存在应为 2，实际 " + result.exit);
                    check(result.stderrLineCount() == 1, "恰好一行");
                }
            });

            run("empty-directory-is-usage-error", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "empty");
                    Result result = cli(directory.toString());
                    check(result.exit == 2, "没有 .pxl 的目录应为 2，实际 " + result.exit);
                }
            });

            run("directory-mode-does-not-follow-a-junction", new Body() {
                public void run() throws Exception {
                    // walkFileTree 的默认只挡**符号链接**；Windows 的 junction 不算符号链接，
                    // 会被当成普通目录走进去，于是给定目录**之外**的 .pxl 也被编译出 PNG。
                    // Python 侧曾有同一个洞（那边靠 os.walk(followlinks=False)，同样只认
                    // islink），两边现在都剪枝。
                    Path tree = Files.createTempDirectory(workspace, "junctiontree");
                    Path outside = Files.createTempDirectory(workspace, "junctionoutside");
                    Files.write(tree.resolve("a.pxl"), validDocument().getBytes(UTF8));
                    Files.write(outside.resolve("z.pxl"), validDocument().getBytes(UTF8));
                    if (!createJunction(tree.resolve("link"), outside)) {
                        // 非 Windows 或本机不许建链接：这条判据在本机无从验证。
                        return;
                    }
                    Result result = cli(tree.toString());
                    check(result.exit == 0, "应为 0，实际 " + result.exit + " / " + result.stderr);
                    check(Files.exists(tree.resolve("a.png")), "树内的 a.pxl 要产出 PNG");
                    check(!Files.exists(outside.resolve("z.png")),
                            "不得编译给定目录之外的 .pxl（junction 被跟随了）");
                }
            });

            run("no-temp-files-left-behind", new Body() {
                public void run() throws Exception {
                    Path directory = Files.createTempDirectory(workspace, "clean");
                    Path source = directory.resolve("a.pxl");
                    Files.write(source, validDocument().getBytes(UTF8));
                    cli(source.toString());
                    java.io.File[] leftovers = directory.toFile().listFiles();
                    for (java.io.File ignored : leftovers) {
                        String name = ignored.getName();
                        check(!name.endsWith(".tmp"), "不得留下临时文件: " + name);
                    }
                }
            });

            run("v1-output-pointing-at-dependency-is-refused", new Body() {
                public void run() throws Exception {
                    // PRD §13.2 CLI002：输出不得与**任何被读的源文件**别名冲突。依赖也算输入，
                    // 只挡入口时这里会把 parts/blade.pxl 静默改写成 PNG，退出码还是 0。
                    Path root = Files.createTempDirectory(workspace, "aliasout");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Path dependency = root.resolve("parts/blade.pxl");
                    Files.write(dependency, v0Leaf().getBytes(UTF8));
                    byte[] before = Files.readAllBytes(dependency);
                    Result result = cli("compile", "entry.pxl", "--asset-root", root.toString(),
                            "-o", dependency.toString());
                    check(result.exit == 2, "输出撞依赖应为 2，实际 " + result.exit
                            + " / " + result.stderr);
                    check(java.util.Arrays.equals(before, Files.readAllBytes(dependency)),
                            "依赖不得被改写");
                }
            });

            run("v1-deps-pointing-at-dependency-is-refused", new Body() {
                public void run() throws Exception {
                    Path root = Files.createTempDirectory(workspace, "aliasdeps");
                    Files.createDirectories(root.resolve("parts"));
                    Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
                    Path dependency = root.resolve("parts/blade.pxl");
                    Files.write(dependency, v0Leaf().getBytes(UTF8));
                    byte[] before = Files.readAllBytes(dependency);
                    Result result = cli("check", "entry.pxl", "--asset-root", root.toString(),
                            "--deps", dependency.toString());
                    check(result.exit == 2, "--deps 撞依赖应为 2，实际 " + result.exit
                            + " / " + result.stderr);
                    check(java.util.Arrays.equals(before, Files.readAllBytes(dependency)),
                            "依赖不得被改写");
                }
            });

            run("format-error-prints-code-once", new Body() {
                public void run() throws Exception {
                    // PxlFormatException 的 message 已经以"码: "开头；再拼一次码就会印成
                    // "PXL002: PXL002: ..."（审查 P3）。
                    Path directory = Files.createTempDirectory(workspace, "codeonce");
                    Path source = directory.resolve("bad.pxl");
                    Files.write(source, invalidDocument().getBytes(UTF8));
                    Path output = directory.resolve("out.png");
                    Result result = cli("compile", "bad.pxl", "--asset-root", directory.toString(),
                            "-o", output.toString());
                    check(result.exit == 3, "格式错误应为 3，实际 " + result.exit);
                    int occurrences = 0;
                    int from = 0;
                    String text = result.stderr;
                    while (true) {
                        int index = text.indexOf("PXL002", from);
                        if (index < 0) {
                            break;
                        }
                        occurrences++;
                        from = index + 6;
                    }
                    check(occurrences == 1, "错误码只能出现一次，实际 " + occurrences + " 次: " + text);
                }
            });
        } finally {
            deleteTree(workspace);
        }
        report();
    }

    // ---------------------------------------------------------------- 辅助

    private static void directoryIterationIsHostIo() throws Exception {
        for (boolean fail : new boolean[] {false, true}) {
            Path root = Files.createTempDirectory(workspace, "directory-iteration");
            Files.createDirectory(root.resolve("parts"));
            Files.write(root.resolve("entry.pxl"), v1Entry().getBytes(UTF8));
            Files.write(root.resolve("parts/blade.pxl"), v0Leaf().getBytes(UTF8));
            Path output = root.resolve("out.png");
            Path deps = root.resolve("deps.tsv");
            List<String> options = new ArrayList<String>();
            options.add("-Djava.nio.file.spi.DefaultFileSystemProvider="
                    + DirectoryIterationFailureProvider.class.getName());
            options.add("-D" + DirectoryIterationFailureProvider.FAILURE_PROPERTY + "=" + fail);
            Result result = cliIn(null, options, "compile", "entry.pxl", "--asset-root", root.toString(),
                    "-o", output.toString(), "--deps", deps.toString());
            check(result.stdout.isEmpty(), "目录迭代测试不应写 stdout: " + result.stdout);
            if (!fail) {
                check(result.exit == 0, "同 provider 的正常对照应成功，实际 "
                        + result.exit + " / " + result.stderr);
                check(result.stderr.isEmpty(), "正常对照不应有 stderr: " + result.stderr);
                check(Files.exists(output) && isPng(Files.readAllBytes(output)), "正常对照应产出 PNG");
                check(Files.exists(deps), "正常对照应产出清单");
                check(new String(Files.readAllBytes(deps), UTF8).contains("parts/blade.pxl"),
                        "正常对照清单应包含真实依赖");
            } else {
                check(result.exit == 4, "目录成功打开、返回一个项后的 I/O 失败应为 4，实际 "
                        + result.exit + " / " + result.stderr);
                check(result.stderr.contains("CLI001"), "迭代失败应报 CLI001: " + result.stderr);
                check(result.stderr.contains(DirectoryIterationFailureProvider.FAILURE_MESSAGE),
                        "必须实际到达返回一个真实项后的故障边界: " + result.stderr);
                check(result.stderrLineCount() == 1, "迭代失败必须恰好一行: " + result.stderr);
                check(!result.stderr.contains("Exception in thread") && !result.stderr.contains("\tat "),
                        "迭代失败不得泄漏 Java 堆栈: " + result.stderr);
                check(!result.stderr.contains("PXL"), "宿主迭代故障不得折算成 PXL 码: " + result.stderr);
                check(!Files.exists(output), "迭代失败不得生成 PNG");
                check(!Files.exists(deps), "迭代失败不得生成清单");
            }
        }
    }

    private static String v1EntryWithAsset(String kind, String path) {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":2,\"height\":2,"
                + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],"
                + "\"assets\":{\"blade\":{\"kind\":\"" + kind
                + "\",\"path\":\"" + path + "\"}},"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                + "[{\"op\":\"stamp\",\"asset\":\"blade\",\"x\":0,\"y\":0}]}]}";
    }

    private static void createSparse(Path path, long size, byte[] prefix) throws IOException {
        RandomAccessFile file = new RandomAccessFile(path.toFile(), "rw");
        try {
            file.setLength(size);
            file.seek(0L);
            file.write(prefix);
        } finally {
            file.close();
        }
    }

    /** 一个最小 v1 入口：stamp 一个 PXL asset。 */
    private static String v1Entry() {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":2,\"height\":2,"
                + "\"encoding\":\"char\",\"palette\":[\"FFFFFFFF\"],"
                + "\"assets\":{\"blade\":{\"kind\":\"pxl\",\"path\":\"parts/blade.pxl\"}},"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                + "[{\"op\":\"stamp\",\"asset\":\"blade\",\"x\":0,\"y\":0}]}]}";
    }

    /** v0 的叶子素材：2x2 两色。 */
    private static String v0Leaf() {
        return "{\"format\":\"PXL\",\"version\":1,\"width\":2,\"height\":2,"
                + "\"encoding\":\"char\",\"palette\":[\"FF0000FF\",\"00FF00FF\"],"
                + "\"layers\":[{\"name\":\"base\",\"blend\":\"normal\",\"ops\":"
                + "[{\"op\":\"grid\",\"x\":0,\"y\":0,\"grid\":[\"01\",\"10\"]}]}]}";
    }

    private static String validDocument() {
        return "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":2,\"height\":2,"
                + "\"palette\":[\"FF0000FF\"],\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":[{\"op\":\"fill\",\"slot\":1}]}]}";
    }

    private static String invalidDocument() {
        // width = 0 → PXL002（数值域）。
        return "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":0,\"height\":2,"
                + "\"palette\":[\"FF0000FF\"],\"layers\":[{\"name\":\"base\","
                + "\"blend\":\"normal\",\"ops\":[]}]}";
    }

    private static boolean isPng(byte[] data) {
        byte[] signature = {(byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
        if (data.length < signature.length) {
            return false;
        }
        for (int index = 0; index < signature.length; index++) {
            if (data[index] != signature[index]) {
                return false;
            }
        }
        return true;
    }

    private static final class Result {
        final int exit;
        final String stdout;
        final String stderr;

        Result(int exit, String stdout, String stderr) {
            this.exit = exit;
            this.stdout = stdout;
            this.stderr = stderr;
        }

        int stderrLineCount() {
            if (stderr.isEmpty()) {
                return 0;
            }
            String trimmed = stderr.endsWith("\n")
                    ? stderr.substring(0, stderr.length() - 1) : stderr;
            return trimmed.split("\n", -1).length;
        }
    }

    private static Result cli(String... arguments) throws IOException, InterruptedException {
        return cliIn(null, arguments);
    }

    /**
     * 在指定工作目录里跑 CLI。
     *
     * <p>测 `--` 终止符时必须让参数**以 `-` 开头**才有意义，而那只能是相对路径，
     * 所以需要控制子进程的 cwd。
     */
    /**
     * 目录树的**逐项快照**：每个条目的相对路径（目录以 / 结尾），排序后返回。
     *
     * <p>它让"不得写文件"这类断言可判定：任何新增/删除都会让两份快照不相等。
     */
    private static List<String> snapshot(Path root) throws IOException {
        final List<String> out = new ArrayList<String>();
        Files.walk(root).forEach(new java.util.function.Consumer<Path>() {
            public void accept(Path path) {
                if (path.equals(root)) {
                    return;
                }
                String relative = root.relativize(path).toString().replace('\\', '/');
                out.add(Files.isDirectory(path) ? relative + "/" : relative);
            }
        });
        java.util.Collections.sort(out);
        return out;
    }

    /** 两份快照的差异，仅用于失败信息。 */
    private static String difference(List<String> before, List<String> after) {
        List<String> added = new ArrayList<String>(after);
        added.removeAll(before);
        List<String> removed = new ArrayList<String>(before);
        removed.removeAll(after);
        return "新增 " + added + " / 消失 " + removed;
    }

    /** 把 {@code java.class.path} 的每一项解析成绝对路径。 */
    private static String absoluteClasspath() {
        Path base = java.nio.file.Paths.get(System.getProperty("user.dir", "."));
        StringBuilder out = new StringBuilder();
        for (String entry : System.getProperty("java.class.path").split(java.io.File.pathSeparator)) {
            if (entry.isEmpty()) {
                continue;
            }
            if (out.length() > 0) {
                out.append(java.io.File.pathSeparatorChar);
            }
            Path path = java.nio.file.Paths.get(entry);
            out.append(path.isAbsolute() ? path.toString() : base.resolve(path).normalize().toString());
        }
        return out.toString();
    }
    private static Result cliIn(Path workdir, String... arguments)
            throws IOException, InterruptedException {
        return cliIn(workdir, java.util.Collections.<String>emptyList(), arguments);
    }

    private static Result cliIn(Path workdir, List<String> jvmOptions, String... arguments)
            throws IOException, InterruptedException {
        List<String> command = new ArrayList<String>();
        command.add(System.getProperty("java.home") + "/bin/java");
        command.add("-Dfile.encoding=UTF-8");
        command.addAll(jvmOptions);
        command.add("-cp");
        // classpath 必须**绝对化**：`cliIn` 会把子进程的 cwd 换到临时目录，相对
        // classpath 到了那边就指向别处，子进程会以 "找不到 Main" 失败——而那会被
        // 误读成"CLI 行为错"。Gradle 的 JavaExec 给的是绝对路径，手工跑 java 时不是。
        command.add(absoluteClasspath());
        command.add("io.github.texel.cli.Main");
        for (String argument : arguments) {
            command.add(argument);
        }
        ProcessBuilder builder = new ProcessBuilder(command);
        if (workdir != null) {
            builder.directory(workdir.toFile());
        }
        Process process = builder.start();
        process.getOutputStream().close();
        String stdout = read(process.getInputStream());
        String stderr = read(process.getErrorStream());
        int exit = process.waitFor();
        return new Result(exit, stdout, stderr);
    }

    private static String read(InputStream stream) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read;
        while ((read = stream.read(buffer)) >= 0) {
            out.write(buffer, 0, read);
        }
        return new String(out.toByteArray(), UTF8);
    }

    private static void deleteTree(Path root) {
        try {
            if (!Files.exists(root)) {
                return;
            }
            Files.walkFileTree(root, new java.nio.file.SimpleFileVisitor<Path>() {
                @Override
                public java.nio.file.FileVisitResult visitFile(Path file,
                        java.nio.file.attribute.BasicFileAttributes attributes)
                        throws IOException {
                    Files.deleteIfExists(file);
                    return java.nio.file.FileVisitResult.CONTINUE;
                }

                @Override
                public java.nio.file.FileVisitResult postVisitDirectory(Path directory,
                        IOException failure) throws IOException {
                    Files.deleteIfExists(directory);
                    return java.nio.file.FileVisitResult.CONTINUE;
                }
            });
        } catch (IOException ignored) {
            // 清理失败不影响判定。
        }
    }

    /** 目录里是否没有 .texel-* 残留（暂存文件、回滚备份）。 */
    private static boolean noTexelLeftovers(Path directory) throws IOException {
        java.nio.file.DirectoryStream<Path> stream =
                Files.newDirectoryStream(directory, ".texel-*");
        try {
            return !stream.iterator().hasNext();
        } finally {
            stream.close();
        }
    }

    /** 建一个指向目录的链接（Windows 用 junction，无需管理员）；建不出来返回 false。 */
    private static boolean createJunction(Path link, Path target) {
        String os = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT);
        if (os.contains("win")) {
            try {
                ProcessBuilder builder = new ProcessBuilder("cmd", "/c", "mklink", "/J",
                        link.toString(), target.toString());
                builder.redirectErrorStream(true);
                Process process = builder.start();
                read(process.getInputStream());
                process.waitFor();
            } catch (IOException failure) {
                return false;
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return false;
            }
            return Files.exists(link);
        }
        try {
            // POSIX 下用符号链接走同一条判据（walkFileTree 默认就不跟随符号链接）。
            Files.createSymbolicLink(link, target);
            return true;
        } catch (IOException failure) {
            return false;
        } catch (UnsupportedOperationException failure) {
            return false;
        }
    }

    private interface Body {
        void run() throws Exception;
    }

    private static void run(String name, Body body) {
        executed++;
        try {
            body.run();
        } catch (AssertionError failure) {
            FAILURES.add(name + " → " + failure.getMessage());
        } catch (Exception failure) {
            FAILURES.add(name + " → 异常 " + failure);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void report() {
        System.out.println("CLI 测试：执行 " + executed + " 组，失败 " + FAILURES.size() + " 组");
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("CLI TESTS FAILED");
            System.exit(1);
        }
        System.out.println("CLI TESTS OK");
    }

}
