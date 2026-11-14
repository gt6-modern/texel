package io.github.texel.core;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * 构建纪律断言（`AGENTS.md` 契约 7：class major 52、生产代码只用 `java.base`）。
 *
 * <p>三条都必须由测试钉住，而不是靠"记得别这么写"：
 *
 * <ol>
 *   <li><b>class major == 52</b>：这是"所有世代的 mod 都能依赖"的唯一可判定形式。
 *       只断言一个文件是不够的——一个模块里漏配 {@code release} 就只影响那一个模块。</li>
 *   <li><b>只用 {@code java.base}</b>：{@code jdeps} 解析两个生产 JAR 的模块依赖，
 *       任何 {@code java.desktop}（ImageIO / AWT）混进来都直接失败。
 *       JDK 8 没有 jdeps 也没有模块系统，此时**明确跳过**并打印原因。</li>
 *   <li><b>没有 Java 9+ 语言特性与 API</b>：{@code --release 8} 挡住大部分，这条把
 *       "禁止清单"变成可执行断言。</li>
 * </ol>
 */
public final class BuildDisciplineMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int JAVA_8_MAJOR = 52;

    /** Java 9+ 的 API / 语法，出现在源码里即失败。 */
    private static final String[] FORBIDDEN = {
        "List.of(", "Map.of(", "Set.of(", "Files.readString", "Files.writeString",
        "Path.of(", ".isBlank()", ".strip()", ".repeat(", "Optional.isEmpty",
        "InputStream.readAllBytes", "transferTo(",
        "javax.imageio", "java.awt", "java.desktop",
    };

    /**
     * {@code var} 声明。要覆盖三种形态：赋值、for-each、普通 for 初始化。
     *
     * <p>以前只匹配 {@code var x = }，{@code for (var e : map.entrySet())} 会漏网——
     * 虽然有 {@code --release 8} 兜底，但这条断言的承诺是"把禁止清单变成可执行断言"。
     */
    private static final Pattern VAR_DECLARATION =
            Pattern.compile("(?m)(?:^|[;{(:]\\s*)var\\s+[A-Za-z_$][A-Za-z0-9_$]*\\s*[:=)]");
    private static final Pattern RECORD_DECLARATION =
            Pattern.compile("(?m)^\\s*(public\\s+|final\\s+|static\\s+)*record\\s+[A-Za-z_]");
    private static final Pattern TEXT_BLOCK = Pattern.compile("\"\"\"\\s*\\r?\\n");

    private static final List<String> FAILURES = new ArrayList<String>();

    private BuildDisciplineMain() {
    }

    public static void main(String[] args) {
        String classDirs = System.getProperty("txel.classdirs", "");
        String sourceDirs = System.getProperty("txel.sourcedirs", "");
        String jars = System.getProperty("txel.jars", "");

        checkClassVersions(classDirs);
        checkSourceDiscipline(sourceDirs);
        checkModuleDependencies(jars);

        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("DISCIPLINE FAILED");
            System.exit(1);
        }
        System.out.println("DISCIPLINE OK");
    }

    private static void checkClassVersions(String classDirs) {
        List<Path> roots = split(classDirs);
        if (roots.isEmpty()) {
            FAILURES.add("没有给到任何 class 目录，这条断言会变成空集合通过");
            return;
        }
        final int[] counted = {0};
        final int[] wrong = {0};
        for (Path root : roots) {
            if (!Files.isDirectory(root)) {
                FAILURES.add("class 目录不存在: " + root);
                continue;
            }
            try {
                Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
                    @Override
                    public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                            throws IOException {
                        if (!file.getFileName().toString().endsWith(".class")) {
                            return FileVisitResult.CONTINUE;
                        }
                        byte[] head = new byte[8];
                        InputStream stream = Files.newInputStream(file);
                        try {
                            int read = 0;
                            while (read < 8) {
                                int step = stream.read(head, read, 8 - read);
                                if (step < 0) {
                                    break;
                                }
                                read += step;
                            }
                        } finally {
                            stream.close();
                        }
                        counted[0]++;
                        int magic = ((head[0] & 0xFF) << 24) | ((head[1] & 0xFF) << 16)
                                | ((head[2] & 0xFF) << 8) | (head[3] & 0xFF);
                        int major = ((head[6] & 0xFF) << 8) | (head[7] & 0xFF);
                        if (magic != 0xCAFEBABE) {
                            wrong[0]++;
                            FAILURES.add(file + "：不是 class 文件");
                        } else if (major != JAVA_8_MAJOR) {
                            wrong[0]++;
                            FAILURES.add(file + "：class major = " + major + "，应为 52");
                        }
                        return FileVisitResult.CONTINUE;
                    }
                });
            } catch (IOException failure) {
                FAILURES.add("扫描 " + root + " 失败: " + failure);
            }
        }
        if (counted[0] == 0) {
            FAILURES.add("一个 .class 都没扫到（空集合通过）");
        }
        System.out.println("CLASSES=" + counted[0] + " WRONG_MAJOR=" + wrong[0]);
    }

    private static void checkSourceDiscipline(String sourceDirs) {
        List<Path> roots = split(sourceDirs);
        if (roots.isEmpty()) {
            FAILURES.add("没有给到任何源码目录");
            return;
        }
        final int[] counted = {0};
        for (Path root : roots) {
            if (!Files.isDirectory(root)) {
                FAILURES.add("源码目录不存在: " + root);
                continue;
            }
            try {
                Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
                    @Override
                    public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                            throws IOException {
                        String name = file.getFileName().toString();
                        if (!name.endsWith(".java")) {
                            return FileVisitResult.CONTINUE;
                        }
                        if ("BuildDisciplineMain.java".equals(name)) {
                            // 本文件自己就写着禁用清单的字面量；扫它只会扫出它自己的规则。
                            return FileVisitResult.CONTINUE;
                        }
                        counted[0]++;
                        String where = name;
                        // 注释里提到 javax.imageio 是**说明为什么不用它**，不是"用了它"，
                        // 所以先剥掉注释再扫，否则这条断言会惩罚正确的文档。
                        String text = stripComments(new String(Files.readAllBytes(file), UTF8));
                        for (String token : FORBIDDEN) {
                            if (text.contains(token)) {
                                FAILURES.add(where + "：出现禁用 API \"" + token + "\"");
                            }
                        }
                        if (VAR_DECLARATION.matcher(text).find()) {
                            FAILURES.add(where + "：出现 var 声明（Java 10+）");
                        }
                        if (RECORD_DECLARATION.matcher(text).find()) {
                            FAILURES.add(where + "：出现 record 声明（Java 16+）");
                        }
                        if (TEXT_BLOCK.matcher(text).find()) {
                            FAILURES.add(where + "：出现 text block（Java 15+）");
                        }
                        return FileVisitResult.CONTINUE;
                    }
                });
            } catch (IOException failure) {
                FAILURES.add("扫描 " + root + " 失败: " + failure);
            }
        }
        if (counted[0] == 0) {
            FAILURES.add("一个 .java 都没扫到（空集合通过）");
        }
        System.out.println("SOURCES=" + counted[0]);
    }

    private static void checkModuleDependencies(String jars) {
        List<Path> files = split(jars);
        if (files.isEmpty()) {
            FAILURES.add("没有给到任何 JAR，模块依赖检查会变成空集合通过");
            return;
        }
        // JDK 8 也**自带 jdeps**（老版本、没有 `--print-module-deps`），所以不能只判
        // 二进制在不在：必须先判运行期有没有模块系统（`java.specification.version`
        // 为 1.x 即 JDK 8 及更早）。在 8 上本检查**明确跳过**而不是红——否则
        // "同一份字节码在 8 上全绿"这句话不成立（审查第七轮实测过）。
        String specification = System.getProperty("java.specification.version", "");
        if (specification.startsWith("1.")) {
            System.out.println("跳过 jdeps：运行期没有模块系统（需要 JDK 9+），specification="
                    + specification);
            return;
        }
        String jdeps = findJdeps();
        if (jdeps == null) {
            System.out.println("跳过 jdeps：当前 JDK 没有该工具（模块依赖检查需要 JDK 9+）");
            return;
        }
        StringBuilder classpath = new StringBuilder();
        for (int index = 0; index < files.size(); index++) {
            if (index > 0) {
                classpath.append(java.io.File.pathSeparatorChar);
            }
            classpath.append(files.get(index).toString());
        }
        for (Path jar : files) {
            if (!Files.exists(jar)) {
                FAILURES.add("JAR 不存在: " + jar);
                continue;
            }
            Result result = exec(jdeps, "--print-module-deps", "-cp", classpath.toString(),
                    jar.toString());
            if (result.exit != 0) {
                FAILURES.add("jdeps 失败 " + jar.getFileName() + " exit=" + result.exit
                        + " out=[" + result.stdout.trim() + "] err=[" + result.stderr.trim() + "]");
                continue;
            }
            String modules = result.stdout.replace("\r", "").replace("\n", "").trim();
            if (!"java.base".equals(modules)) {
                FAILURES.add(jar.getFileName() + "：模块依赖是 \"" + modules + "\"，应为 java.base");
            }
        }
        System.out.println("JARS=" + files.size());
    }

    /** 找 jdeps：先按 java.home/bin，再按 JDK 8 的 java.home/../bin。 */
    private static String findJdeps() {
        String home = System.getProperty("java.home");
        String[] candidates = {
            home + "/bin/jdeps",
            home + "/bin/jdeps.exe",
            home + "/../bin/jdeps",
            home + "/../bin/jdeps.exe",
        };
        for (String candidate : candidates) {
            if (Files.exists(Paths.get(candidate))) {
                return candidate;
            }
        }
        return null;
    }

    /** 粗略剥掉行注释与块注释，只留代码本身（文档里提到禁用的库不算"用了它"）。 */
    private static String stripComments(String source) {
        StringBuilder out = new StringBuilder(source.length());
        int index = 0;
        int length = source.length();
        while (index < length) {
            if (source.startsWith("/*", index)) {
                int end = source.indexOf("*/", index + 2);
                index = end < 0 ? length : end + 2;
                continue;
            }
            if (source.startsWith("//", index)) {
                int end = source.indexOf('\n', index);
                index = end < 0 ? length : end;
                continue;
            }
            char ch = source.charAt(index);
            if (ch == '"') {
                out.append(ch);
                index++;
                while (index < length) {
                    char inner = source.charAt(index);
                    out.append(inner);
                    index++;
                    if (inner == '\\' && index < length) {
                        out.append(source.charAt(index));
                        index++;
                        continue;
                    }
                    if (inner == '"') {
                        break;
                    }
                }
                continue;
            }
            out.append(ch);
            index++;
        }
        return out.toString();
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
    }

    private static Result exec(String... command) {
        try {
            ProcessBuilder builder = new ProcessBuilder(command);
            Process process = builder.start();
            process.getOutputStream().close();
            String stdout = drain(process.getInputStream());
            String stderr = drain(process.getErrorStream());
            return new Result(process.waitFor(), stdout, stderr);
        } catch (IOException failure) {
            return new Result(-1, "", "启动失败: " + failure);
        } catch (InterruptedException failure) {
            Thread.currentThread().interrupt();
            return new Result(-1, "", "被中断");
        }
    }

    private static String drain(InputStream stream) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read;
        while ((read = stream.read(buffer)) >= 0) {
            out.write(buffer, 0, read);
        }
        return new String(out.toByteArray(), UTF8);
    }

    private static List<Path> split(String value) {
        List<Path> paths = new ArrayList<Path>();
        if (value == null || value.isEmpty()) {
            return paths;
        }
        for (String part : value.split(",")) {
            String trimmed = part.trim();
            if (!trimmed.isEmpty()) {
                paths.add(Paths.get(trimmed));
            }
        }
        return paths;
    }
}
