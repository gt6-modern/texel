package io.github.texel.core;

import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Current component-suite conformance runner.
 *
 * <p>它不做版本分派、不读 {@code profiles.tsv}：these cases belong to the {@code components} suite，
 * the profile selects the complete current suite; 本执行器负责「按索引逐条跑、逐条判」。
 *
 * <p>每个用例目录**就是** asset root，入口逻辑路径恒为 {@code entry.pxl}（规范 §13.3.1）；
 * 素材用内存 resolver 装配，因此路径语义与宿主文件系统无关。
 */
public final class CompilerConformanceMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final List<String> FAILURES = new ArrayList<String>();
    private static int executed;
    private static int pending;

    private CompilerConformanceMain() {
    }

    public static void main(String[] args) throws IOException {
        Path repo = Paths.get(System.getProperty("txel.repo", ".")).toAbsolutePath().normalize();
        Path root = repo.resolve("spec").resolve("conformance").resolve("v1");
        Path casesDir = root.resolve("cases");
        List<Map<String, String>> rows = readIndex(root.resolve("CASES.tsv"));
        String only = args.length > 0 ? args[0] : null;
        for (Map<String, String> row : rows) {
            String casePath = row.get("case");
            if (casePath == null || casePath.isEmpty()) {
                FAILURES.add("索引里有缺少 case 列的行");
                continue;
            }
            if (only != null && casePath.indexOf(only) < 0) {
                continue;
            }
            runCase(casePath, row, casesDir.resolve(casePath));
        }
        report();
    }

    private static void runCase(String casePath, Map<String, String> row, Path directory) {
        String expected = valueOr(row, "expected", "");
        // 索引声明了 dependencies 就必须有对应的黄金清单，否则"声明了却没判"（发布态复核）。
        if (expected.indexOf("dependencies") >= 0
                && !Files.isRegularFile(directory.resolve("expected.dependencies.tsv"))) {
            boolean anyNamed = false;
            java.util.List<String> declaredNames = loadBindingNames(directory);
            for (int index = 0; index < declaredNames.size(); index++) {                String name = declaredNames.get(index);
                if (!name.isEmpty() && Files.isRegularFile(
                        directory.resolve("expected." + name + ".dependencies.tsv"))) {
                    anyNamed = true;
                }
            }
            if (!anyNamed) {
                FAILURES.add(casePath + "：索引声明了 dependencies 但黄金清单缺失");
                return;
            }
        }
        if (!Files.isDirectory(directory)) {
            FAILURES.add(casePath + "：目录不存在");
            return;
        }
        if ("-".equals(expected)) {
            // pending 用例：三条同时成立才算合法（规范 §13.3.7）。
            pending++;
            String notes = valueOr(row, "notes", "");
            if (!notes.startsWith("pending:")) {
                FAILURES.add(casePath + "：expected 写 - 但 notes 没有 pending: 前缀");
            }
            if (hasExpectedFile(directory)) {
                FAILURES.add(casePath + "：pending 用例不得有 expected.* 文件");
            }
            if (!Files.isRegularFile(directory.resolve("entry.pxl"))) {
                FAILURES.add(casePath + "：缺少 entry.pxl");
            }
            return;
        }
        MemoryAssetResolver.Builder builder = MemoryAssetResolver.builder();
        try {
            register(builder, directory, directory);
            Path spelling = directory.resolve("spelling.tsv");
            if (Files.isRegularFile(spelling)) {
                for (String line : Files.readAllLines(spelling, UTF8)) {
                    if (line.trim().isEmpty() || line.trim().startsWith("#")) {
                        continue;
                    }
                    String[] columns = line.split("\t", -1);
                    if (columns.length != 2) {
                        FAILURES.add(casePath + "：spelling.tsv 不是两列: " + line);
                        continue;
                    }
                    builder.spelling(columns[0].trim(), columns[1].trim());
                }
            }
        } catch (IOException failure) {
            FAILURES.add(casePath + "：装配 resolver 失败 " + failure);
            return;
        }
        executed++;
        // bindings.json 是载体（规范 §13.3.3）：每个 target 一组 bindings + 一份期望像素。
        java.util.List<java.util.Map<String, String>> targets = loadBindings(directory);
        java.util.List<String> targetNames = loadBindingNames(directory);
        if (targets.isEmpty()) {
            targets.add(java.util.Collections.<String, String>emptyMap());
            targetNames.add("");
        }
        MemoryAssetResolver resolver;
        try {
            resolver = builder.build();
        } catch (RuntimeException failure) {
            FAILURES.add(casePath + "：装配 resolver 失败 " + failure);
            return;
        }
        boolean expectError = expected.indexOf("error") >= 0;
        for (int index = 0; index < targets.size(); index++) {
            // 每个 target 单独判：此前整个循环共用外层一个 try，第一个预期异常会**跳过其余 target**，
            // 于是「多 target 负例」只验证了第一个（发布态复核 P2-4）。隔离负例里只要把合法 target
            // 放在缺参 target 后面，门禁就会错误通过。
            String name = targetNames.get(index);
            String label = name.isEmpty() ? "-" : name;
            PxlCompiler.Compiled compiled = null;
            PxlFormatException failure = null;
            try {
                compiled = PxlCompiler.compile("entry.pxl", resolver, targets.get(index));
            } catch (PxlFormatException thrown) {
                failure = thrown;
            }
            if (expectError) {
                if (failure == null) {
                    FAILURES.add(casePath + "[" + label + "]：期望失败，实际编译成功");
                    continue;
                }
                String want = firstLine(directory.resolve("expected.error"));
                // 引用链也是判据的一部分（发布态复核 F20：缓存根截断时链会少一层）。
                Path chainFile = directory.resolve("expected.chain");
                if (Files.isRegularFile(chainFile)) {
                    String wantChain = firstLine(chainFile);
                    // 只看「引用链: 」之后的部分：消息体里也可能出现同样的文字
                    // （循环错误的说明本身就是一条链），那样断言会被说明文本满足。
                    String full = failure.getMessage();
                    int at = full.indexOf("引用链: ");
                    String chainPart = at < 0 ? "" : full.substring(at);
                    // 必须**精确**比较结构化链：子串匹配时把期望改成严格前缀也能通过
                    // （发布态复核 C2），那就证明不了链是对的。
                    if (wantChain != null && wantChain.length() > 0
                            && !chainEquals(chainPart, wantChain)) {
                        FAILURES.add(casePath + "[" + label + "]：引用链不符「" + wantChain
                                + "」（实际: " + chainPart + "）");
                    }
                }
                if (!want.equals(failure.code())) {
                    FAILURES.add(casePath + "[" + label + "]：期望 " + want + "，实际 "
                            + failure.code() + "（" + failure.getMessage() + "）");
                }
                continue;
            }
            if (failure != null) {
                FAILURES.add(casePath + "[" + label + "]：期望成功，实际 " + failure.code()
                        + " " + failure.getMessage());
                continue;
            }
            Path expectedFile = directory.resolve(name.isEmpty()
                    ? "expected.pixels.sha256" : "expected." + name + ".pixels.sha256");
            String wantPixels = firstLine(expectedFile);
            String got = PixelDigest.sha256Hex(compiled.width(), compiled.height(),
                    compiled.renderRgba());
            if (!wantPixels.equals(got)) {
                FAILURES.add(casePath + "[" + label + "]：像素摘要不一致（期望 " + wantPixels
                        + "，实际 " + got + "）");
            }
            // 黄金依赖清单：CASES.tsv 声明了 expected.* 就必须比对（发布态复核：此前从不读取）。
            Path depsFile = directory.resolve(name.isEmpty()
                    ? "expected.dependencies.tsv" : "expected." + name + ".dependencies.tsv");
            if (Files.isRegularFile(depsFile)) {
                StringBuilder actual = new StringBuilder();
                for (PxlCompiler.Dependency item : compiled.dependencies()) {
                    actual.append(item.toTsv()).append((char) 10);
                }
                String wantDeps;
                try {
                    wantDeps = new String(Files.readAllBytes(depsFile), UTF8);
                } catch (IOException ioFailure) {
                    FAILURES.add(casePath + "[" + label + "]：读黄金清单失败 " + ioFailure);
                    continue;
                }
                if (!sameLines(wantDeps, actual.toString())) {
                    FAILURES.add(casePath + "[" + label + "]：依赖清单不一致（期望 "
                            + wantDeps.trim() + "，实际 " + actual.toString().trim() + "）");
                }
            }
        }
    }

    /** 逐行比较两份文本（忽略行尾空白与结尾空行），用于黄金清单。 */
    private static boolean sameLines(String want, String got) {
        java.util.List<String> left = new java.util.ArrayList<String>();
        for (String line : want.split("\n", -1)) {
            if (line.trim().length() > 0) {
                left.add(line.trim());
            }
        }
        java.util.List<String> right = new java.util.ArrayList<String>();
        for (String line : got.split("\n", -1)) {
            if (line.trim().length() > 0) {
                right.add(line.trim());
            }
        }
        return left.equals(right);
    }

    /** 读 bindings.json 的 targets（没有该文件时返回空表）。 */
    private static java.util.List<java.util.Map<String, String>> loadBindings(Path directory) {
        java.util.List<java.util.Map<String, String>> out =
                new java.util.ArrayList<java.util.Map<String, String>>();
        Path file = directory.resolve("bindings.json");
        if (!Files.isRegularFile(file)) {
            return out;
        }
        String text;
        try {
            text = new String(Files.readAllBytes(file), UTF8).trim();
        } catch (IOException failure) {
            FAILURES.add(directory.getFileName() + "：读不了 bindings.json " + failure);
            return out;
        }
        int cursor = 0;
        while (true) {
            int bindings = text.indexOf("\"bindings\"", cursor);
            if (bindings < 0) {
                break;
            }
            int open = text.indexOf((char) 123, bindings);
            int close = text.indexOf((char) 125, open);
            java.util.Map<String, String> target = new java.util.LinkedHashMap<String, String>();
            for (String pair : text.substring(open + 1, close).split(",")) {
                String[] parts = pair.split(":");
                if (parts.length != 2) {
                    continue;
                }
                target.put(parts[0].trim().replace("\"", ""), parts[1].trim().replace("\"", ""));
            }
            out.add(target);
            cursor = close;
        }
        return out;
    }

    /** 读 bindings.json 里每个 target 的名字（与 loadBindings 一一对应）。 */
    private static java.util.List<String> loadBindingNames(Path directory) {
        java.util.List<String> out = new java.util.ArrayList<String>();
        Path file = directory.resolve("bindings.json");
        if (!Files.isRegularFile(file)) {
            return out;
        }
        String text;
        try {
            text = new String(Files.readAllBytes(file), UTF8);
        } catch (IOException failure) {
            return out;
        }
        int cursor = 0;
        while (true) {
            int at = text.indexOf("\"name\"", cursor);
            if (at < 0) {
                break;
            }
            int colon = text.indexOf(':', at);
            if (colon < 0) {
                break;
            }
            int start = text.indexOf('"', colon);
            int stop = text.indexOf('"', start + 1);
            if (start < 0 || stop < 0) {
                break;
            }
            out.add(text.substring(start + 1, stop));
            cursor = stop + 1;
        }
        return out;
    }

    private static void register(MemoryAssetResolver.Builder builder, Path root, Path directory)
            throws IOException {
        java.nio.file.DirectoryStream<Path> stream = Files.newDirectoryStream(directory);
        try {
            for (Path item : stream) {
                if (Files.isDirectory(item)) {
                    register(builder, root, item);
                    continue;
                }
                String name = item.getFileName().toString();
                if ("spelling.tsv".equals(name) || "notes.md".equals(name)
                        || name.startsWith("expected.") || name.startsWith("bindings.")) {
                    continue;
                }
                String logical = root.relativize(item).toString().replace(item.getFileSystem()
                        .getSeparator(), "/");
                builder.file(logical, Files.readAllBytes(item));
            }
        } finally {
            stream.close();
        }
    }

    private static boolean hasExpectedFile(Path directory) {
        java.nio.file.DirectoryStream<Path> stream = null;
        try {
            stream = Files.newDirectoryStream(directory, "expected.*");
            for (Path ignored : stream) {
                return true;
            }
        } catch (IOException failure) {
            return true;
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                    // 关闭失败不影响判定。
                }
            }
        }
        return false;
    }

    private static List<Map<String, String>> readIndex(Path index) throws IOException {
        List<String> lines = Files.readAllLines(index, UTF8);
        List<Map<String, String>> rows = new ArrayList<Map<String, String>>();
        if (lines.isEmpty()) {
            FAILURES.add("components/CASES.tsv 是空的");
            return rows;
        }
        String[] header = lines.get(0).split("\t", -1);
        for (int index2 = 1; index2 < lines.size(); index2++) {
            String line = lines.get(index2);
            if (line.trim().isEmpty()) {
                continue;
            }
            String[] columns = line.split("\t", -1);
            Map<String, String> row = new LinkedHashMap<String, String>();
            for (int column = 0; column < header.length; column++) {
                row.put(header[column].trim(), column < columns.length ? columns[column].trim() : "");
            }
            rows.add(row);
        }
        return rows;
    }

    /** 从「引用链: a -> b」里取出段序列，与期望**逐段**比较。 */
    private static boolean chainEquals(String chainPart, String want) {
        String prefix = "引用链: ";
        if (!chainPart.startsWith(prefix)) {
            return false;
        }
        String body = chainPart.substring(prefix.length()).trim();
        while (body.endsWith("）") || body.endsWith(")")) {
            body = body.substring(0, body.length() - 1).trim();
        }
        String[] got = body.split(" -> ", -1);
        String[] wantParts = want.trim().split(" -> ", -1);
        return java.util.Arrays.equals(got, wantParts);
    }
    private static String firstLine(Path file) {
        try {
            List<String> lines = Files.readAllLines(file, UTF8);
            return lines.isEmpty() ? "" : lines.get(0).trim();
        } catch (IOException failure) {
            return "";
        }
    }

    private static String valueOr(Map<String, String> row, String key, String fallback) {
        String value = row.get(key);
        return value == null ? fallback : value;
    }

    private static void report() {
        // §5.1 要求 pending 清单为 0：pending 借的是「期望值待定」的语义，它一旦非零，
        // 就说明有语料没有判据却在算通过（发布态复核旧账 A：PENDING 只打印、从不判红）。
        if (pending != 0) {
            FAILURES.add("还有 " + pending + " 条 pending 语料（§5.1 要求 0 条）");
        }
        System.out.println("COMPONENT_CHECKS=" + executed + " PENDING=" + pending
                + " FAILURES=" + FAILURES.size());
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("COMPONENT CONFORMANCE FAILED");
            System.exit(1);
        }
        System.out.println("COMPONENT CONFORMANCE OK");
    }
}
