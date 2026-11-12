package io.github.texel.cli;

import io.github.texel.core.PngEncoder;
import io.github.texel.core.PxlFormatException;
import io.github.texel.core.PxlParser;
import io.github.texel.core.Renderer;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.io.UnsupportedEncodingException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.InvalidPathException;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 命令行入口：{@code texel-compiler [-o OUT] PATH...}。
 *
 * <p>PATH 可以是文件或目录；目录按 {@code *.pxl} 递归收集，处理顺序按路径的 UTF-8
 * 字节序显式排序（规范 §9：不得依赖文件系统遍历顺序）。
 *
 * <p>退出码：0 全成功；1 至少一个文件失败（每条失败一行，该文件不产出任何输出）；
 * 2 用法错误。
 *
 * <p><b>失败不留半份文件</b>：每个输入先完整解析、渲染、编码，再写到同目录的独占临时
 * 文件并原子改名。于是格式错误不会破坏已存在的同名 PNG，中途失败也不会留下半个输出。
 */
public final class Main {

    private static final PrintStream ERR = stream(FileDescriptor.err);
    private static final PrintStream OUT = stream(FileDescriptor.out);

    private Main() {
    }

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        try {
            return runInternal(args);
        } catch (OutOfMemoryError exhausted) {
            // §10.2.1：驻留内存不足是**宿主资源**失败，不是 PXL 码；必须受控上报，
            // 不能把 OutOfMemoryError 的堆栈甩给调用方。
            ERR.println("texel-compiler: CLI001: I/O 失败: 宿主内存不足（工作集超出可用堆）");
            return 4;
        }
    }

    private static int runInternal(String[] args) {
        // `capabilities --json` 是 PRD §6.3 的统一能力档案入口：它不读任何输入路径，
        // 也不能被当成"一个名叫 capabilities 的 .pxl 文件"去做前缀匹配。
        if (args.length > 0 && ("compile".equals(args[0]) || "check".equals(args[0]))) {
            return runTarget(args);
        }
        if (args.length > 0 && "capabilities".equals(args[0])) {
            if (args.length == 2 && "--json".equals(args[1])) {
                OUT.print(Capabilities.json());
                return 0;
            }
            return usage("capabilities 只接受 --json");
        }

        List<String> inputs = new ArrayList<String>();
        String output = null;
        boolean literal = false;

        for (int index = 0; index < args.length; index++) {
            String argument = args[index];
            if (!literal && "--".equals(argument)) {
                literal = true;
                continue;
            }
            if (!literal && ("--help".equals(argument) || "-h".equals(argument))) {
                OUT.print(usage());
                return 0;
            }
            if (!literal && ("-o".equals(argument) || "--out".equals(argument))) {
                if (index + 1 >= args.length) {
                    return usage("选项 " + argument + " 缺少参数");
                }
                if (output != null) {
                    return usage("只允许一个输出路径");
                }
                output = args[++index];
                continue;
            }
            inputs.add(argument);
        }

        if (inputs.isEmpty()) {
            return usage("缺少输入路径");
        }
        if (output != null && inputs.size() > 1) {
            return usage("-o 只允许在恰好一个输入时使用");
        }

        List<Path> sources = new ArrayList<Path>();
        for (String input : inputs) {
            // `Paths.get` 对非法字符（`a<b.pxl`、`*.pxl` 这种没被 shell 展开的）会抛
            // InvalidPathException —— 那是 RuntimeException，不捕获就会把 10 行栈回溯
            // 甩给使用者，而且整批一个文件都不处理。单行 + 退出 2 才是契约。
            Path path;
            try {
                path = Paths.get(input);
            } catch (InvalidPathException invalid) {
                return usage("输入路径含非法字符: " + escape(input) + "（" + invalid.getReason() + "）");
            }
            if (Files.isDirectory(path)) {
                int before = sources.size();
                String problem = collect(path, sources);
                if (problem != null) {
                    // 目录存在但**读不动**是 I/O 失败(退出 1)，不是用法错误(退出 2)。
                    // 早先这里把异常吞掉、再因"列表还是空的"报"目录里没有 .pxl 文件"，
                    // 诊断与退出码都指错了原因。
                    ERR.println("texel-compiler: " + escape(input) + ": " + escape(problem));
                    return 1;
                }
                if (sources.size() == before) {
                    return usage("目录里没有 .pxl 文件: " + escape(input));
                }
            } else if (Files.exists(path)) {
                sources.add(path);
            } else {
                return usage("输入不存在: " + escape(input));
            }
        }
        sort(sources);
        // 同一个文件被给两次只处理一次：否则两份输出会互相踩，而报"两个输入撞车"
        // 又指错了原因（真实原因是"重复输入"，不是"输出路径选错"）。
        // 与抽取器 importer 的 _dedupe 保持同一语义（按解析后的路径身份去重）。
        sources = dedupe(sources);

        // `-o` 的准入只能按**展开后**的输入数量判：`-o out.png dir/` 里 dir/ 有 2 个
        // 位图时，真实原因是"-o 只允许单输入"，而不是"两个输入撞车"——后者会把人
        // 引向错误的修法（去改输出路径，而不是去掉 -o）。
        if (output != null && sources.size() > 1) {
            return usage("-o 只允许在恰好一个输入时使用（这些路径展开后得到 "
                    + sources.size() + " 个 .pxl）");
        }

        // `-o` 的值必须**先**解析再使用：`Paths.get` 对非法字符抛 InvalidPathException
        // （RuntimeException），晚一步捕获就会先撞上栈回溯。
        Path explicitTarget = null;
        if (output != null) {
            if (output.isEmpty()) {
                // 空串会被 `Files.isDirectory` 当成当前目录，于是报"输出路径是一个目录:"
                // 后面什么都没有——诊断指向了错误的原因。
                return usage("输出路径不能为空");
            }
            try {
                explicitTarget = Paths.get(output);
            } catch (InvalidPathException invalid) {
                return usage("输出路径含非法字符: " + escape(output) + "（" + invalid.getReason() + "）");
            }
            // `-o` 指向一个**已存在的目录**是用法错误（退出 2），不是 I/O 失败：否则会
            // 走到临时文件 + 改名那一步，报出内部临时文件名（"…tmp -> outdir"），
            // 那条消息既泄漏实现细节，又把用户引向错误的方向。
            if (Files.isDirectory(explicitTarget)) {
                return usage("输出路径是一个目录: " + escape(output));
            }
        }

        List<Path> targets = new ArrayList<Path>();
        for (Path source : sources) {
            targets.add(explicitTarget != null ? explicitTarget : replaceExtension(source));
        }

        List<String> conflicts = conflicts(sources, targets);
        if (!conflicts.isEmpty()) {
            for (String conflict : conflicts) {
                ERR.println("texel-compiler: " + conflict);
            }
            return 2;
        }

        int failures = 0;
        for (int index = 0; index < sources.size(); index++) {
            Path source = sources.get(index);
            Path target = targets.get(index);
            try {
                write(source, target);
            } catch (PxlFormatException failure) {
                failures++;
                // 消息也必须转义：文档里的成员名/图层名可以含 \n 或 U+2028，原样打印会把
                // "每条错误一行"变成两行（路径早就转义了，消息当初漏了）。
                ERR.println("texel-compiler: " + escape(source.toString()) + ": " + escape(failure.getMessage()));
            } catch (IOException failure) {
                failures++;
                ERR.println("texel-compiler: " + escape(source.toString()) + ": " + describe(failure));
            }
        }
        return failures == 0 ? 0 : 1;
    }

    private static void write(Path source, Path target) throws PxlFormatException, IOException {
        // 先看文件大小再读：64 MiB 的负例整份读进内存后，解析时的对象树会先 OOM，
        // 那样"文档字节上限"就形同虚设（规范 §11.1 与 §5.0.2 的例外条款）。
        long size = Files.size(source);
        if (size > PxlParser.maxDocumentBytes()) {
            throw new PxlFormatException(PxlParser.documentTooLargeCode(),
                    "文档 UTF-8 字节数 " + size + " 超过 " + PxlParser.maxDocumentBytesText());
        }
        byte[] document = Files.readAllBytes(source);
        Renderer renderer = PxlParser.parse(document);
        byte[] rgba = renderer.renderRgba();
        byte[] png = PngEncoder.encode(renderer.width(), renderer.height(), rgba);

        Path directory = target.toAbsolutePath().getParent();
        if (directory == null) {
            throw new IOException("输出路径没有父目录: " + escape(target.toString()));
        }
        Path temporary = Files.createTempFile(directory, ".texel-compiler-", ".tmp");
        boolean moved = false;
        try {
            OutputStream stream = Files.newOutputStream(temporary);
            try {
                stream.write(png);
            } finally {
                stream.close();
            }
            try {
                Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            } catch (UnsupportedOperationException unsupported) {
                // 不做"退化成非原子覆盖"：那正是可能留下半个 PNG 的写法。
                throw new IOException("该文件系统不支持原子改名，拒绝写入: "
                        + escape(target.toString()));
            }
            moved = true;
        } finally {
            if (!moved) {
                try {
                    Files.deleteIfExists(temporary);
                } catch (IOException ignored) {
                    // 清理失败不该掩盖原始错误。
                }
            }
        }
    }

    /**
     * 把 I/O 失败渲染成"人能据此行动"的一行。
     *
     * <p>{@code AccessDeniedException} 之类的 {@code getMessage()} 常常**就是路径本身**，
     * 于是原来输出成"路径: 路径"——原因全丢了。补上异常类型名，并统一走 {@link #escape}。
     */
    /** 把临时文件刷到磁盘再发布：掉电时不留半截文件（PRD 合同）。 */
    private static void syncFile(Path file) throws IOException {
        try (java.nio.channels.FileChannel channel =
                     java.nio.channels.FileChannel.open(file, java.nio.file.StandardOpenOption.WRITE)) {
            channel.force(true);
        }
    }

    /** 优先原子移动；文件系统不支持时退化为普通替换。 */
    private static void atomicMove(Path from, Path to) throws IOException {
        try {
            Files.move(from, to, java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        } catch (java.nio.file.AtomicMoveNotSupportedException unsupported) {
            // 不能退化成普通替换：合同要求「同步后原子替换」，第二次非原子移动会
            // 覆盖旧文件（发布态复核 F6）。抛出由调用方报 CLI003。
            throw unsupported;
        }
    }
    /** 目标路径上是否存在符号链接环：环一律拒绝（§5.4 确定性）。 */
    private static boolean hasSymlinkCycle(String target) {
        // 从**目标本身**开始：叶子自己是链接环时，只看父目录会漏（发布态复核 F5）。
        Path probe = Paths.get(target).toAbsolutePath();
        while (probe != null) {
            try {
                probe.toRealPath();
                return false;
            } catch (java.nio.file.NoSuchFileException missing) {
                probe = probe.getParent();
            } catch (IOException failure) {
                return true;
            }
        }
        return false;
    }
    /** 解析链接后的真实形状：不存在的叶子先解析最近的已存在祖先，再补剩余路径。 */
    private static Path realish(Path path) {
        Path cursor = path;
        java.util.List<Path> tail = new java.util.ArrayList<Path>();
        while (cursor != null && !Files.exists(cursor)) {
            tail.add(0, cursor.getFileName());
            cursor = cursor.getParent();
        }
        Path resolved = path;
        if (cursor != null) {
            try {
                resolved = cursor.toRealPath();
            } catch (IOException ignored) {
                resolved = cursor;
            }
        }
        for (Path part : tail) {
            resolved = resolved.resolve(part);
        }
        return resolved;
    }
    private static String describe(IOException failure) {
        String kind = failure.getClass().getSimpleName();
        String message = failure.getMessage();
        if (message == null || message.isEmpty()) {
            return kind;
        }
        return escape(kind + ": " + message);
    }

    /** 递归收集 {@code *.pxl}；失败时返回原因（调用方按 I/O 失败处理），成功返回 null。 */
    private static String collect(final Path root, final List<Path> into) {
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attributes) {
                    if (attributes.isRegularFile() && hasExtension(file, ".pxl")) {
                        into.add(file);
                    }
                    return FileVisitResult.CONTINUE;
                }
            });
        } catch (IOException failure) {
            // 只把 getMessage() 交出去会退化成"路径: 路径"（AccessDeniedException 的消息
            // 就是路径本身）；带上类型名，调用方再统一 escape。
            return failure.getClass().getSimpleName() + ": " + failure.getMessage();
        }
        return null;
    }

    private static boolean hasExtension(Path file, String extension) {
        Path name = file.getFileName();
        return name != null && name.toString().endsWith(extension);
    }

    private static Path replaceExtension(Path source) {
        String name = source.getFileName().toString();
        String base = name.endsWith(".pxl") ? name.substring(0, name.length() - 4) : name;
        Path parent = source.getParent();
        return parent == null ? Paths.get(base + ".png") : parent.resolve(base + ".png");
    }

    private static void sort(List<Path> paths) {
        Collections.sort(paths, new Comparator<Path>() {
            public int compare(Path left, Path right) {
                byte[] a = left.toString().getBytes(java.nio.charset.Charset.forName("UTF-8"));
                byte[] b = right.toString().getBytes(java.nio.charset.Charset.forName("UTF-8"));
                int limit = Math.min(a.length, b.length);
                for (int index = 0; index < limit; index++) {
                    int difference = (a[index] & 0xFF) - (b[index] & 0xFF);
                    if (difference != 0) {
                        return difference;
                    }
                }
                return a.length - b.length;
            }
        });
    }

    /**
     * 找出所有"会踩坏输入"或"两个输出撞车"的写入。
     *
     * <p>必须<b>在写入之前</b>一次性算清：批量模式下 {@code a.pxl} 的输出 {@code a.png}
     * 可能正是同批另一个待处理的输入，先写的那份已经把后面的输入毁了，而后者的失败
     * 会让整批看起来只是"某个文件读取失败"。
     */
    private static List<String> conflicts(List<Path> sources, List<Path> targets) {
        List<String> problems = new ArrayList<String>();
        Set<String> inputKeys = new HashSet<String>();
        for (Path source : sources) {
            inputKeys.add(identity(source));
        }
        Set<String> seen = new HashSet<String>();
        for (int index = 0; index < targets.size(); index++) {
            Path target = targets.get(index);
            String key = identity(target);
            if (inputKeys.contains(key)) {
                problems.add("输出会覆盖输入: " + escape(target.toString()));
            }
            if (!seen.add(key)) {
                problems.add("两个输入写到同一个输出: " + escape(target.toString()));
            }
        }
        return problems;
    }

    /** 按路径身份去掉重复的输入，保持首次出现的顺序。 */
    private static List<Path> dedupe(List<Path> inputs) {
        Set<String> seen = new HashSet<String>();
        List<Path> unique = new ArrayList<Path>();
        for (Path input : inputs) {
            if (seen.add(identity(input))) {
                unique.add(input);
            }
        }
        return unique;
    }

    /**
     * 路径身份：能解析成真实路径就用真实路径（把符号链接与 {@code ../} 归一），
     * 并在 Windows 上按大小写不敏感比较；解析不了就退化为绝对路径。
     */
    private static String identity(Path path) {
        String suffix = "";
        Path probe = path.toAbsolutePath();
        if (!Files.exists(probe)) {
            Path parent = probe.getParent();
            if (parent != null) {
                suffix = probe.getFileName() == null ? "" : probe.getFileName().toString();
                probe = parent;
            }
        }
        String base;
        try {
            base = probe.toRealPath().toString();
        } catch (IOException failure) {
            base = probe.normalize().toString();
        }
        if (!suffix.isEmpty()) {
            base = base + "/" + suffix;
        }
        return isWindows() ? base.toLowerCase(java.util.Locale.ROOT) : base;
    }

    private static boolean isWindows() {
        return java.io.File.separatorChar == '\\';
    }

    private static int usage(String message) {
        // PRD §13.3：用法类错误带 CLI004；调用点已带 CLI00x 的（如 CLI002 输出冲突）不重复加。
        ERR.println("texel-compiler: " + (message.startsWith("CLI00") ? message
                : "CLI004: " + message));
        return 2;
    }

    // ---------------------------------------------------------------- 单 target（v1）

    /**
     * compile / check：一次调用编译**一个** target（PRD §17.1 / 规范 v1 §8.1）。
     *
     * <p>退出码：0 成功、2 用法错、3 PXL 语义错、4 宿主 I/O 失败。
     */
    private static int runTarget(String[] args) {
        String mode = args[0];
        boolean check = "check".equals(mode);
        String entry = null;
        Path assetRoot = null;
        String output = null;
        String deps = null;
        java.util.Map<String, String> bindings = new java.util.LinkedHashMap<String, String>();
        for (int index = 1; index < args.length; index++) {
            String argument = args[index];
            if ("--asset-root".equals(argument)) {
                if (index + 1 >= args.length) {
                    return usage("--asset-root 缺少参数");
                }
                // 宿主路径非法时 Paths.get 抛 InvalidPathException（RuntimeException）：
                // 这一步在 try 之外，不接就会吐栈迹 + exit 1（发布态复核 F-R6-07 的残余格）。
                try {
                    assetRoot = Paths.get(args[++index]);
                } catch (java.nio.file.InvalidPathException invalid) {
                    return usage("--asset-root 不是合法路径: " + escape(invalid.getMessage()));
                }
                continue;
            }
            if ("--bind".equals(argument)) {
                if (index + 1 >= args.length) {
                    return usage("--bind 缺少参数");
                }
                String pair = args[++index];
                int equals = pair.indexOf('=');
                if (equals <= 0) {
                    return usage("--bind 需要 name=value，收到 " + escape(pair));
                }
                bindings.put(pair.substring(0, equals), pair.substring(equals + 1));
                continue;
            }
                if ("-o".equals(argument) || "--out".equals(argument)) {
                    // 恰好一个显式输出：重复给出必须拒绝，不能静默采用后者（PRD 合同）。
                    if (output != null) {
                        return usage("只能给出一个输出参数（-o / --out）");
                    }
                if (index + 1 >= args.length) {
                    return usage(argument + " 缺少参数");
                }
                output = args[++index];
                continue;
            }
            if ("--deps".equals(argument)) {
                if (index + 1 >= args.length) {
                    return usage("--deps 缺少参数");
                }
                deps = args[++index];
                continue;
            }
            if (argument.startsWith("-")) {
                return usage("未知选项 " + escape(argument));
            }
            if (entry != null) {
                return usage("一次调用只接受一个入口（PRD §17.1）");
            }
            entry = argument;
        }
        if (entry == null) {
            return usage(mode + " 需要入口的逻辑路径");
        }
        if (assetRoot == null) {
            return usage(mode + " 需要 --asset-root");
        }
        if (check && output != null) {
            return usage("check 不接受输出路径");
        }
        if (!check && output == null) {
            return usage("compile 需要一个显式 -o 输出路径");
        }
        try {
            io.github.texel.core.FileAssetResolver resolver =
                    io.github.texel.core.FileAssetResolver.rootedAt(assetRoot);
            io.github.texel.core.PxlCompiler.Compiled compiled =
                    io.github.texel.core.PxlCompiler.compile(entry, resolver, bindings);
            // 输出与清单都不得落到**入口或任何依赖**上（PRD §13.2 CLI002）。判定用的是本次
            // 编译的依赖清单，所以入口与全部被读到的素材一起被挡住；比较按真实路径，符号链接
            // 与宿主大小写都归一。判在**写任何文件之前**。
            // 输出路径上出现符号链接环时一律拒绝（复核第二节 + §5.4）。
            if (deps != null && !"-".equals(deps) && hasSymlinkCycle(deps)) {
                return usage("CLI002: 清单路径包含符号链接环: " + escape(deps));
            }
            if (!check && output != null && hasSymlinkCycle(output)) {
                return usage("CLI002: 输出路径包含符号链接环: " + escape(output));
            }
            if ("-".equals(deps)) {
                // PRD 要求「依赖清单可写 stdout」；旗标本身 PRD 未规定，这里取约定的 '-'，
                // 并且**不能**把 '-' 当成文件名写出去（发布态复核第二节）。
                StringBuilder text = new StringBuilder();
                for (io.github.texel.core.PxlCompiler.Dependency item : compiled.dependencies()) {
                    text.append(item.toTsv()).append((char) 10);
                }
                OUT.print(text.toString());
                OUT.flush();
            } else if (deps != null) {
                String clash = io.github.texel.core.PxlCompiler.conflictingSource(deps,
                        assetRoot.toFile(), compiled.dependencies());
                if (clash != null) {
                    return usage("CLI002: --deps 路径与源文件相同: " + escape(deps) + "（" + clash + "）");
                }
            }
            if (!check) {
                String clash = io.github.texel.core.PxlCompiler.conflictingSource(output,
                        assetRoot.toFile(), compiled.dependencies());
                if (clash != null) {
                    return usage("CLI002: 输出路径与源文件相同: " + escape(output) + "（" + clash + "）");
                }
            }
            // 输出与清单也不得指向同一路径：否则先写出的清单会被 PNG 覆盖，命令还报成功
            // （发布态复核 P2：compile -o both --deps both 曾 exit 0）。
            if (output != null && deps != null) {
                // 必须比较**真实身份**：alias -> real 且叶子尚不存在时，词法路径不同、
                // 但写完 PNG 后清单会经别名落到同一个新文件（发布态复核 A1）。
                Path outPath = realish(Paths.get(output).toAbsolutePath().normalize());
                Path depsPath = realish(Paths.get(deps).toAbsolutePath().normalize());
                boolean samePath = outPath.equals(depsPath);
                if (!samePath && Files.exists(outPath) && Files.exists(depsPath)) {
                    try {
                        samePath = Files.isSameFile(outPath, depsPath);
                    } catch (IOException ignored) {
                        // 身份查不出来就按路径比较，上面已经做过。
                    }
                }
                if (samePath) {
                    return usage("CLI002: 输出与清单指向同一路径: " + escape(output));
                }
            }
            if (deps != null && !"-".equals(deps)) {
                StringBuilder text = new StringBuilder();
                for (io.github.texel.core.PxlCompiler.Dependency item : compiled.dependencies()) {
                    text.append(item.toTsv()).append((char) 10);
                }
                // 先写临时文件再原子替换：直接 Files.write 会截断**已有 inode**，
                // 若 deps 是某个源文件的硬链接，源文件内容就没了（路径检查抓不到）。
                Path depsFile = Paths.get(deps).toAbsolutePath();
                if (depsFile.getParent() != null) {
                    Files.createDirectories(depsFile.getParent());
                }
                Path depsTemporary = Files.createTempFile(depsFile.getParent(), ".texel-deps-", ".tmp");
                try {
                    Files.write(depsTemporary,
                            text.toString().getBytes(java.nio.charset.Charset.forName("UTF-8")));
                    syncFile(depsTemporary);
                    atomicMove(depsTemporary, depsFile);
                } catch (IOException failure) {
                    // 失败要清掉自己的临时文件：留着 .texel-deps-*.tmp 是"失败还污染目录"
                    // （发布态复核 F-R6-06）。
                    try {
                        Files.deleteIfExists(depsTemporary);
                    } catch (IOException ignored) {
                        // 清理失败不掩盖原始错误。
                    }
                    // PRD §13.3 CLI003：临时文件/同步/原子替换失败，就地报码并退出 4。
                    ERR.println("texel-compiler: CLI003: I/O 失败: "
                            + escape(failure.toString()));
                    return 4;
                }
            }
            // check 也要走完整编码：只渲染不编码时，编码器的失败对 check 不可见
            // （PRD 合同；发布态复核第二节）。
            byte[] png = io.github.texel.core.PngEncoder.encode(compiled.width(),
                    compiled.height(), compiled.renderRgba());
            if (check) {
                return 0;
            }
            Path targetFile = Paths.get(output).toAbsolutePath();
            // 输出目录由调用方给（构建系统常把产物放进步进目录），这里负责建出来。
            if (targetFile.getParent() != null) {
                Files.createDirectories(targetFile.getParent());
            }
            Path temporary = Files.createTempFile(targetFile.getParent(), ".texel-compiler-", ".tmp");
            try {
                Files.write(temporary, png);
                syncFile(temporary);
                atomicMove(temporary, targetFile);
            } catch (IOException failure) {
                try {
                    Files.deleteIfExists(temporary);
                } catch (IOException ignored) {
                    // 同上：清理失败不掩盖原始错误。
                }
                ERR.println("texel-compiler: CLI003: I/O 失败: " + escape(failure.toString()));
                return 4;
            }
            return 0;
        } catch (io.github.texel.core.PxlFormatException failure) {
            // getMessage() 已经以"码: "开头（PxlFormatException 的构造约定），再拼一次码就会
            // 印成 "PXL011: PXL011: ..."。
            ERR.println("texel-compiler: " + escape(failure.getMessage()));
            return 3;
        } catch (java.io.UncheckedIOException failure) {
            // resolver 的宿主读盘错误：不能用 PXL 码包装权限或锁失败。
            ERR.println("texel-compiler: CLI001: I/O 失败: " + escape(failure.getCause().toString()));
            return 4;
        } catch (java.nio.file.InvalidPathException failure) {
            // 宿主路径本身非法（换行、Windows 保留字符等）时 Paths.get 抛的是 RuntimeException，
            // 不捕获就会吐栈迹（发布态复核 F-R6-07）。
            ERR.println("texel-compiler: 非法路径: " + escape(failure.getMessage()));
            return 4;
        } catch (IOException failure) {
            // PRD §13.3 CLI001：宿主 I/O（asset root、读入口等）失败。
        ERR.println("texel-compiler: CLI001: I/O 失败: " + escape(failure.toString()));
            return 4;
        }
    }

    static String usage() {
        return "usage: texel-compiler [-o OUT] PATH...\n"
                + "\n"
                + "  PATH            输入 .pxl 文件或目录(目录递归收集 *.pxl)\n"
                + "  -o, --out OUT   输出路径(只在恰好一个输入时允许)\n"
                + "  --              其后的参数一律当路径(文件名以 - 开头时用)\n"
                + "\n"
                + "  capabilities --json   打印能力档案(PRD §6.3 的统一 schema)\n"
                + "\n"
                + "省略 -o 时,输出写到输入文件同目录、同名的 .png。\n";
    }

    /**
     * 把路径渲染成单行：控制字符与"会被当成换行"的码点一律转义。
     *
     * <p>Windows 允许文件名里带换行，直接把原样字符串写进 stderr 会让"每条错误一行"
     * 断成两行，而测试是按行数断言的。
     */
    static String escape(String text) {
        StringBuilder out = new StringBuilder(text.length());
        for (int index = 0; index < text.length(); index++) {
            char ch = text.charAt(index);
            if (ch < 0x20 || ch == 0x7F || ch == 0x85 || ch == 0x2028 || ch == 0x2029) {
                out.append("\\u");
                for (int shift = 12; shift >= 0; shift -= 4) {
                    out.append("0123456789ABCDEF".charAt((ch >> shift) & 0xF));
                }
            } else {
                out.append(ch);
            }
        }
        return out.toString();
    }

    private static PrintStream stream(FileDescriptor descriptor) {
        try {
            return new PrintStream(new FileOutputStream(descriptor), true, "UTF-8");
        } catch (UnsupportedEncodingException impossible) {
            throw new IllegalStateException("运行环境缺少 UTF-8", impossible);
        }
    }
}
