package io.github.texel.cli;

import io.github.texel.core.PngEncoder;
import io.github.texel.core.PxlFormatException;
import io.github.texel.core.PxlParser;

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
            if (Files.isDirectory(path) && isLinkLike(path)) {
                // 递归里的剪枝只看子项：根是符号链接/junction 时整棵树都会走到链接目标上去，
                // "目录模式处理哪些文件"取决于本机链接（2026-11-20 审查 P2）。
                ERR.println("texel-compiler: " + escape(input)
                        + ": 输入目录是符号链接/junction，批量入口不跟随（请给出真实路径）");
                return 1;
            }
            if (Files.isDirectory(path)) {
                int before = sources.size();
                List<String> skipped = new ArrayList<String>();
                String problem = collect(path, sources, skipped);
                if (problem != null) {
                    // 目录存在但**读不动**是 I/O 失败(退出 1)，不是用法错误(退出 2)。
                    // 早先这里把异常吞掉、再因"列表还是空的"报"目录里没有 .pxl 文件"，
                    // 诊断与退出码都指错了原因。
                    ERR.println("texel-compiler: " + escape(input) + ": " + escape(problem));
                    return 1;
                }
                // 跳过的候选必须说出来：目录模式"少产出一个 PNG"否则无从发现
                // （符号链接不被跟随、X.PXL 的大小写不符，2026-11-14 审查 P2）。
                for (String skip : skipped) {
                    ERR.println("texel-compiler: 跳过 " + escape(skip));
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
            // 与单 target 路径一致：输出冲突是 CLI002（用法错），不能是一条没有码的裸文本
            // ——下游按 `CLI00x` 前缀分流，裸文本会被当成未知输出（2026-11-14 审查 P2）。
            for (String conflict : conflicts) {
                ERR.println("texel-compiler: CLI002: " + conflict);
            }
            return 2;
        }

        // 预检只看得见**输入**。输出还可能落在某个入口**读过的依赖**上（`-o sprite.png`
        // 覆盖被 stamp 的素材），而依赖清单只有解析完才知道；逐个入口"编译完再判自己"时
        // 前面的产物已经落盘。所以先做一次只解析不渲染的预扫，再整批判一次
        // （审查 P1；与 Python 批量入口同一条判据）。
        List<String> dependencyFailures = new ArrayList<String>();
        List<String> dependencyConflicts = dependencyConflicts(sources, targets, dependencyFailures);
        if (!dependencyConflicts.isEmpty()) {
            // 冲突是用法错（exit 2），但"某个入口本身坏了"这个事实不能被藏起来：先逐条报出来。
            for (String failure : dependencyFailures) {
                ERR.println("texel-compiler: " + failure);
            }
            for (String conflict : dependencyConflicts) {
                ERR.println("texel-compiler: CLI002: " + conflict);
            }
            ERR.println("texel-compiler: 检测到 " + dependencyConflicts.size()
                    + " 处输出覆盖编译依赖，整批未写入任何文件");
            return 2;
        }
        if (!dependencyFailures.isEmpty()) {
            // 预扫就把某个入口解析失败：我们不知道它的完整依赖集（PNG 素材要到渲染阶段
            // 才真正读），无法证明这批输出不会毁掉它依赖的东西。一个入口坏了，不能让同批
            // 另一个入口把它的素材改成 PNG（审查 P1；Python 侧同一条规则）。
            // 只报逐文件的诊断（每个失败入口一行），不加汇总行：契约是"每条错误恰好一行"。
            for (String failure : dependencyFailures) {
                ERR.println("texel-compiler: " + failure);
            }
            return 1;
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
            } catch (java.io.UncheckedIOException failure) {
                // resolver 的宿主读盘失败（权限/锁）：不能包装成 PXL 码，但也不能甩栈迹。
                failures++;
                ERR.println("texel-compiler: " + escape(source.toString())
                        + ": CLI001: I/O 失败: " + escape(failure.getCause().toString()));
            }
        }
        return failures == 0 ? 0 : 1;
    }

    /**
     * 每个输出对**所有**入口的依赖清单做一次覆盖检查（审查 P1）。
     *
     * <p>预扫走 {@code PxlCompiler.dependencies}（只跑相位 A），所以这一步一份位图都不留。
     * 解析失败的入口不在这里报（正式循环会逐文件报），但**它已经读到的依赖照样参与检查**：
     * 那份素材同样不能被别的输出覆盖。
     */
    private static List<String> dependencyConflicts(List<Path> sources, List<Path> targets,
            List<String> parseFailures) {
        List<String> conflicts = new ArrayList<String>();
        List<Path> roots = new ArrayList<Path>();
        List<List<io.github.texel.core.PxlCompiler.Dependency>> manifests =
                new ArrayList<List<io.github.texel.core.PxlCompiler.Dependency>>();
        for (Path source : sources) {
            Path root = source.toAbsolutePath().getParent();
            List<io.github.texel.core.PxlCompiler.Dependency> partial =
                    new ArrayList<io.github.texel.core.PxlCompiler.Dependency>();
            roots.add(root);
            manifests.add(partial);
            if (root == null) {
                continue;
            }
            try {
                io.github.texel.core.PxlCompiler.dependencies(
                        source.getFileName().toString(),
                        io.github.texel.core.FileAssetResolver.rootedAt(root),
                        java.util.Collections.<String, String>emptyMap(), partial);
            } catch (PxlFormatException failure) {
                // 消息本身已带 `PXL0xx: ` 前缀（PxlFormatException 的构造约定）。
                parseFailures.add(escape(source.toString()) + ": " + escape(failure.getMessage()));
            } catch (IOException failure) {
                parseFailures.add(escape(source.toString()) + ": " + describe(failure));
            } catch (RuntimeException failure) {
                Throwable cause = failure.getCause() == null ? failure : failure.getCause();
                parseFailures.add(escape(source.toString()) + ": CLI001: I/O 失败: "
                        + escape(cause.toString()));
            }
        }
        for (int index = 0; index < sources.size(); index++) {
            Path target = targets.get(index);
            for (int other = 0; other < sources.size(); other++) {
                Path root = roots.get(other);
                if (root == null) {
                    continue;
                }
                String clash = io.github.texel.core.PxlCompiler.conflictingSource(
                        target.toString(), root.toFile(), manifests.get(other));
                if (clash != null) {
                    conflicts.add(escape(sources.get(index).toString()) + ": 输出 "
                            + escape(target.toString()) + " 会覆盖 "
                            + escape(sources.get(other).toString()) + " 的编译依赖 "
                            + escape(clash) + "（未写入）");
                    break;
                }
            }
        }
        return conflicts;
    }

    private static void write(Path source, Path target) throws PxlFormatException, IOException {
        // 先看文件大小再读：64 MiB 的负例整份读进内存后，解析时的对象树会先 OOM，
        // 那样"文档字节上限"就形同虚设（规范 §11.1 与 §5.0.2 的例外条款）。
        long size = Files.size(source);
        if (size > PxlParser.maxDocumentBytes()) {
            throw new PxlFormatException(PxlParser.documentTooLargeCode(),
                    "文档 UTF-8 字节数 " + size + " 超过 " + PxlParser.maxDocumentBytesText());
        }
        // 旧式批量入口同样走**完整的 v1 编译管线**，asset root = 源文件所在目录
        // （python 侧就是这么做的）。直接用 PxlParser.parse + Raster 渲染没有 asset 解析，
        // 遇到引用 asset 的 stamp 会崩：Java 抛 NPE，Python 报 PXL009（2026-11-14 审查实测）。
        Path sourceDir = source.toAbsolutePath().getParent();
        if (sourceDir == null) {
            throw new IOException("输入路径没有父目录: " + escape(source.toString()));
        }
        io.github.texel.core.PxlCompiler.Compiled compiled =
                io.github.texel.core.PxlCompiler.compile(
                        source.getFileName().toString(),
                        io.github.texel.core.FileAssetResolver.rootedAt(sourceDir),
                        java.util.Collections.<String, String>emptyMap());
        byte[] png = PngEncoder.encode(compiled.width(), compiled.height(),
                compiled.renderRgba());

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
            // 与单 target 路径一致：先刷盘再原子改名，掉电不留半截 PNG
            // （批量路径原来只有原子改名、没有 fsync，2026-11-14 审查 P2）。
            syncFile(temporary);
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
     * 把依赖清单原子写入 {@code deps}。
     *
     * <p>先写临时文件再原子替换：直接 {@code Files.write} 会截断**已有 inode**，
     * 若清单路径是某个源文件的硬链接，源文件内容就没了（词法路径检查抓不到）。
     *
     * @return 0 成功；4 表示 I/O 失败（已就地打印 {@code CLI003}）
     */
    private static int writeDependencies(String deps,
            java.util.List<io.github.texel.core.PxlCompiler.Dependency> dependencies) {
        Path depsFile = Paths.get(deps).toAbsolutePath();
        Path temporary = null;
        try {
            temporary = stageFile(depsFile, manifestBytes(dependencies));
            atomicMove(temporary, depsFile);
            return 0;
        } catch (IOException failure) {
            deleteQuietly(temporary);
            ERR.println("texel-compiler: CLI003: I/O 失败: " + escape(failure.toString()));
            return 4;
        }
    }

    /** 依赖清单的 UTF-8 字节：写入文件与写 stdout 用的是**同一份**字节。 */
    private static byte[] manifestBytes(
            java.util.List<io.github.texel.core.PxlCompiler.Dependency> dependencies) {
        StringBuilder text = new StringBuilder();
        for (io.github.texel.core.PxlCompiler.Dependency item : dependencies) {
            text.append(item.toTsv()).append((char) 10);
        }
        return text.toString().getBytes(java.nio.charset.Charset.forName("UTF-8"));
    }

    /**
     * 目标已存在却**不是普通文件**时返回诊断用的后缀，否则 null。
     *
     * <p>必须在写任何东西之前问：等到改名阶段才发现（目标是个目录），先改名的那份产物
     * 已经替换成功，命令失败却留下了新产物。
     */
    private static String nonFileTarget(String path) {
        if (path == null) {
            return null;
        }
        Path target;
        try {
            target = Paths.get(path);
        } catch (java.nio.file.InvalidPathException invalid) {
            // 非法路径另有专门的诊断，这里不抢着报。
            return null;
        }
        if (!Files.exists(target) || Files.isRegularFile(target)) {
            return null;
        }
        return Files.isDirectory(target) ? "是一个目录" : "不是普通文件";
    }

    /** 把 payload 写进 target 同目录的独占临时文件并刷盘；返回临时路径，**不改名**。 */
    private static Path stageFile(Path target, byte[] payload) throws IOException {
        Path parent = target.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Path temporary = Files.createTempFile(parent, ".texel-", ".tmp");
        boolean staged = false;
        try {
            Files.write(temporary, payload);
            syncFile(temporary);
            staged = true;
        } finally {
            // finally 而不是 catch：写/刷盘阶段的 OutOfMemoryError 也是失败，而调用方
            // 拿不到这个临时文件名，只能在这里清（2026-11-20 审查 P2）。
            if (!staged) {
                deleteQuietly(temporary);
            }
        }
        return temporary;
    }

    /**
     * 把产物落盘。没有清单时就是**一次原子替换**；有清单时两份产物当成一次事务。
     *
     * <p>为什么不是"先写 PNG 再写清单"：那是两段独立事务，清单写失败会留下「新 PNG +
     * 旧清单」，下游会把一份过期清单当成这次构建的结果（2026-11-20 审查 P2）。
     *
     * <p>为什么备份是**复制**而不是移走：移走目标再改名，中间那一刻目标路径不存在——
     * 进程被打断就只剩一份隐藏备份，单产物输出也会跟着退化（2026-11-20 审查 P1）。
     * 这里任何时刻目标都在原位，读者看到的要么是旧内容要么是新内容。
     *
     * @return 0 成功；4 表示 I/O 失败（已就地打印 {@code CLI003}）
     */
    /**
     * 一份既有产物的**保身份**备份：硬链接、复制文件，或者另一个同目标的符号链接。
     *
     * <p>三种形态都是**磁盘上的对象**，所以恢复就是一次原子改名，备份也能被人捡回来。
     */
    static final class Backup {
        private final Path file;

        Backup(Path file) {
            this.file = file;
        }
    }

    private static int publish(Path pngTarget, byte[] png, Path depsTarget, byte[] manifest) {
        Path pngTemp = null;
        Path depsTemp = null;
        java.util.Map<Path, Backup> backups = new java.util.LinkedHashMap<Path, Backup>();
        // 容量预先分配成 2：`committed.add` 发生**在改名之后**，扩容一旦抛 OutOfMemoryError，
        // 回滚循环看不到刚改名的那份，而备份会被当成"没用上"删掉 —— 新产物留在目录里、
        // 旧内容再也恢复不了（CodeRabbit 复审）。
        java.util.List<Path> committed = new ArrayList<Path>(2);
        try {
            pngTemp = stageFile(pngTarget, png);
            if (depsTarget == null) {
                // 单份产物：一次原子替换，目标路径任何时刻都存在。
                atomicMove(pngTemp, pngTarget);
                pngTemp = null;
                return 0;
            }
            depsTemp = stageFile(depsTarget, manifest);
            // 备份不能移走目标：目标要一直留在原位，直到最后一次原子替换。
            if (existsOrLink(pngTarget)) {
                backups.put(pngTarget, backupTarget(pngTarget, pngTemp));
            }
            if (existsOrLink(depsTarget)) {
                backups.put(depsTarget, backupTarget(depsTarget, depsTemp));
            }
            // PNG 先改名、清单后改名：万一回滚本身也失败，「新 PNG + 旧清单」是保守的一侧
            // （看到旧清单下游会重建），反过来「旧 PNG + 新清单」等于让构建系统相信一份
            // 过期产物。
            atomicMove(pngTemp, pngTarget);
            pngTemp = null;
            committed.add(pngTarget);
            atomicMove(depsTemp, depsTarget);
            depsTemp = null;
            committed.add(depsTarget);
        } catch (IOException failure) {
            java.util.List<Path> kept = rollback(committed, backups);
            deleteQuietly(pngTemp);
            deleteQuietly(depsTemp);
            ERR.println("texel-compiler: CLI003: I/O 失败: " + escape(failure.toString()));
            reportKeptBackups(kept);
            return 4;
        } catch (Error failure) {
            // OutOfMemoryError 这类 Error 不是 IOException：不在这里清理的话，失败的编译会在
            // 目录里留下 .texel-*.tmp，已经提交的那份也留成新内容（2026-11-20 审查 P2）。
            java.util.List<Path> kept = rollback(committed, backups);
            deleteQuietly(pngTemp);
            deleteQuietly(depsTemp);
            reportKeptBackups(kept);
            throw failure;
        }
        for (Backup backup : backups.values()) {
            dropBackup(backup);
        }
        return 0;
    }

    /**
     * 失败回滚：已提交的产物按备份恢复原状（连目录项类型与硬链接身份一起恢复）。
     *
     * <p>退不回去的那一份**留着备份**：那份旧内容可能只在这里，删掉等于把退路也毁了。
     */
    static java.util.List<Path> rollback(java.util.List<Path> committed,
            java.util.Map<Path, Backup> backups) {
        java.util.List<Path> kept = new ArrayList<Path>();
        for (int index = committed.size() - 1; index >= 0; index--) {
            Path target = committed.get(index);
            Backup backup = backups.remove(target);
            try {
                if (backup == null) {
                    deleteQuietly(target);
                } else {
                    restoreTarget(target, backup);
                }
            } catch (IOException | Error ignored) {
                // 恢复本身也可能因资源耗尽失败（OutOfMemoryError）：一样**留着备份**并继续
                // 处理剩下的目标，不能在这里中断——否则后面的备份不清理、保留路径也不报。
                // 退不回去就**留着**备份：那份旧内容可能只在这里。它必须脱离下面那个
                // "没用上的备份"清理循环——双重故障（提交失败 + 恢复失败）曾把旧产物连
                // 备份一起删掉，那是不可恢复的丢失。
                if (backup != null && backup.file != null) {
                    kept.add(backup.file);
                }
            }
        }
        // 只清"还没提交的那一份"的备份；恢复失败的那份留着让人工接手。
        for (Backup backup : backups.values()) {
            try {
                dropBackup(backup);
            } catch (Error ignored) {
                // 清理本身失败不掩盖原始错误。
            }
        }
        return kept;
    }

    /** 目标的硬链接数；宿主给不出这个数时返回 0（当成"可能有别的名字"，不安全）。 */
    private static int hardLinkCount(Path target) {
        try {
            Object value = Files.getAttribute(target, "unix:nlink",
                    java.nio.file.LinkOption.NOFOLLOW_LINKS);
            if (value instanceof Number) {
                return ((Number) value).intValue();
            }
        } catch (IOException unreadable) {
            return 0;
        } catch (UnsupportedOperationException unsupported) {
            return 0;
        } catch (IllegalArgumentException unsupported) {
            return 0;
        }
        return 0;
    }

    /**
     * 给既有产物建一份**保身份**的备份。
     *
     * <p>只复制内容恢复不了目录项：目标原本是符号链接或硬链接时，回滚会把它换成普通文件，
     * 链接关系永久丢失（2026-11-20 审查 P2）。所以分三种形态：符号链接记原始文本（回滚时
     * 按原文本重建链接）、普通文件建**硬链接**（同一个 inode，回滚就是把它换回来，也不必把
     * 旧产物整份复制一遍）、只有**能证明目标没有别的名字**（nlink == 1，或文件系统根本不
     * 支持硬链接）时才退化成复制。目标还与别的名字共享 inode 时这里会失败，让整次发布回滚：
     * 复制备份恢复不了那些名字之间的链接关系，而失败的命令不该改变文件系统。
     */
    private static Backup backupTarget(Path target, Path temporary) throws IOException {
        Path backup = temporary.resolveSibling(temporary.getFileName() + ".prev");
        if (Files.isSymbolicLink(target)) {
            // 备份必须是**磁盘上的对象**：只记住链接文本，回滚时要先删掉目标再重建链接，
            // 重建一旦失败（权限、链接数上限）目标路径就没了，而那份"备份"只活在进程里
            // （2026-11-20 审查 P1）。所以备份就是另一个指向同一目标的符号链接。
            try {
                Files.createSymbolicLink(backup, Files.readSymbolicLink(target));
            } catch (IOException failure) {
                deleteQuietly(backup);
                throw new IOException("无法为符号链接产物建立备份（建立链接失败: " + failure
                        + "）: " + target);
            } catch (UnsupportedOperationException unsupported) {
                deleteQuietly(backup);
                throw new IOException("该文件系统不支持符号链接，无法备份符号链接产物: " + target);
            }
            return new Backup(backup);
        }
        try {
            Files.createLink(backup, target);
            return new Backup(backup);
        } catch (UnsupportedOperationException noHardLinks) {
            // 文件系统根本没有硬链接：那别处也不可能存在指向同一 inode 的名字，复制是等价的。
        } catch (IOException linkUnavailable) {
            // 权限拒绝、配额、链接数上限都不是"文件系统不支持硬链接"：目标很可能还与别的
            // 名字共享 inode，复制出来的备份回滚后是新 inode，那些名字之间的链接关系会永久
            // 丢掉。只有能证明 nlink == 1 时才允许复制；证明不了就让这次发布失败。
            int links = hardLinkCount(target);
            if (links != 1) {
                throw new IOException("无法为有硬链接的产物建立保身份备份（nlink=" + links
                        + "，与其它路径共享 inode）: " + target
                        + "（建硬链接失败: " + linkUnavailable + "）");
            }
        }
        // 复制也要**先写临时文件再改名**：直接写 .prev 的话，复制中途失败（磁盘满、读失败）
        // 会留下一个没有任何人跟踪的半份备份（2026-11-20 审查 P2）。
        Path partial = Files.createTempFile(backup.getParent(), ".texel-", ".tmp");
        try {
            Files.copy(target, partial, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            syncFile(partial);
            atomicMove(partial, backup);
        } catch (IOException failure) {
            deleteQuietly(partial);
            deleteQuietly(backup);
            throw failure;
        }
        return new Backup(backup);
    }

    /** 把"恢复失败、必须保留的备份"逐行说出来：那是人工恢复的唯一退路。 */
    private static void reportKeptBackups(java.util.List<Path> kept) {
        for (Path path : kept) {
            ERR.println("texel-compiler: 回滚失败，旧产物备份保留在: " + escape(path.toString()));
        }
    }

    /** 目标存在，或本身就是一个（可能是悬垂的）符号链接。 */
    private static boolean existsOrLink(Path target) {
        return Files.isSymbolicLink(target) || Files.exists(target);
    }

    /**
     * 把目标的**目录项**恢复原状：三种备份形态都是磁盘对象，一次原子改名就够。
     *
     * <p>不能"先删目标再重建链接"：重建失败会留下一个空路径，而符号链接的备份如果只
     * 存在内存里就连退路都没有（2026-11-20 审查 P1）。
     */
    private static void restoreTarget(Path target, Backup backup) throws IOException {
        atomicMove(backup.file, target);
    }

    /** 丢掉没用上的备份：三种形态都是磁盘对象，删掉即可。 */
    private static void dropBackup(Backup backup) {
        deleteQuietly(backup.file);
    }

    /** 删文件；不存在或删不掉都不掩盖原始错误。 */
    private static void deleteQuietly(Path file) {
        if (file == null) {
            return;
        }
        try {
            Files.deleteIfExists(file);
        } catch (IOException ignored) {
            // 清理失败不掩盖原始错误。
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
                // 只有"链接层数过多"才是环。把任意 IOException 都判成环会把
                // AccessDenied / 共享冲突 报成 CLI002"包含符号链接环"，真实原因被掩盖
                // （2026-11-14 审查 P2）。
                return isSymlinkLoop(failure);
            }
        }
        return false;
    }

    /** 宿主的"链接层数过多"错误：POSIX 给 FileSystemLoopException，Windows 只剩消息可认。 */
    private static boolean isSymlinkLoop(IOException failure) {
        if (failure instanceof java.nio.file.FileSystemLoopException) {
            return true;
        }
        String message = failure.getMessage();
        if (message == null) {
            return false;
        }
        // POSIX 的 ELOOP 由 JDK 转成上面那个异常；Windows 的 ERROR_CANT_RESOLVE_FILENAME
        // (1921) 只能靠消息识别，且中英文措辞不同。除此以外一律不是环。
        String lower = message.toLowerCase(java.util.Locale.ROOT);
        return lower.contains("too many levels of symbolic links")
                || lower.contains("cannot resolve")
                || message.contains("循环链接")
                || message.contains("无法解析文件名");
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

    /**
     * 递归收集 {@code *.pxl}；失败时返回原因（调用方按 I/O 失败处理），成功返回 null。
     *
     * <p>看起来像 PXL 但被跳过的候选（符号链接、扩展名大小写不符）追加进 {@code skipped}，
     * 由调用方逐条告知用户——静默跳过会让"目录模式少产出一个 PNG"无从发现。
     *
     * <p>符号链接与 Windows 的 junction 一律不跟随（见 {@link #isLinkLike(Path)}）：
     * {@code walkFileTree} 的默认只挡符号链接，junction 却被当成普通目录走进去，于是
     * 给定目录**之外**的 .pxl 也会被编译出 PNG（写回树外）——同一棵树的结果取决于这台
     * 机器上谁建过链接。
     */
    private static String collect(final Path root, final List<Path> into,
            final List<String> skipped) {
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
                @Override
                public FileVisitResult preVisitDirectory(Path directory,
                        BasicFileAttributes attributes) {
                    if (!directory.equals(root) && isLinkLike(directory)) {
                        return FileVisitResult.SKIP_SUBTREE;
                    }
                    return FileVisitResult.CONTINUE;
                }

                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attributes) {
                    String name = file.getFileName() == null ? "" : file.getFileName().toString();
                    boolean regular = attributes.isRegularFile();
                    if (regular && name.endsWith(".pxl")) {
                        into.add(file);
                    } else if (name.toLowerCase(java.util.Locale.ROOT).endsWith(".pxl")) {
                        // 走到这里只有两种可能：不是普通文件（walkFileTree 默认不跟随符号
                        // 链接），或者扩展名大小写不符（X.PXL 在 Windows 上也会落到这里）。
                        skipped.add(file.toString() + (regular
                                ? "（扩展名大小写不符，只认 .pxl）"
                                : "（不是普通文件：符号链接不被跟随）"));
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

    /**
     * 该目录是否是符号链接，或 Windows 的 junction / 挂载点。
     *
     * <p>{@code Files.isSymbolicLink} 对 junction 返回 false，而 {@code walkFileTree} 的默认
     * 行为正是靠它——于是 junction 被当成普通目录走进去（实测 Windows + JDK 21）。判据换成
     * "实路径"：junction 的 {@code toRealPath()} 落在别的目录上，普通子目录则等于"父目录的
     * 实路径 + 自己的名字"。用父目录的实路径比较，祖先里另有链接时不会误伤正常子目录。
     */
    private static boolean isLinkLike(final Path directory) {
        if (Files.isSymbolicLink(directory)) {
            return true;
        }
        try {
            // 先做**词法**归一：调用方可能给的是 `dir\..`、`dir\.\x` 这种带 `..`/`.` 的普通
            // 路径，而 toRealPath() 已经把词法段消解掉了。不归一的话，`compiler\java\..`
            // 会被当成 junction 拒掉（2026-11-20 审查 P2）。
            Path lexical = directory.toAbsolutePath().normalize();
            Path parent = lexical.getParent();
            if (parent == null) {
                return false;      // 盘符根/文件系统根：没有父目录可比，交给遍历自己判
            }
            Path real = directory.toRealPath();
            // 只比**父目录的实路径**。原来把 toRealPath() 与"父实路径 + 词法名字"相比：
            // Windows 上 `C:\LONGDI~1` 这类 8.3 短名称会被 toRealPath() 展开成长名字，
            // 两边于是不相等，普通目录被误判成 junction 并在批量入口被拒（CodeRabbit 复审）。
            // junction 与符号链接的实路径都落在**别的目录**上，父目录仍然不同，所以判据不变强。
            return !real.getParent().equals(parent.toRealPath());
        } catch (IOException unreadable) {
            // 读不出来就别在这里剪枝：交给 walkFileTree 自己报（否则整棵树会被静默丢掉）。
            return false;
        }
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
        List<Path> seenTargets = new ArrayList<Path>();
        for (int index = 0; index < targets.size(); index++) {
            Path target = targets.get(index);
            String key = identity(target);
            if (inputKeys.contains(key)) {
                problems.add("输出会覆盖输入: " + escape(target.toString()));
            } else if (sharesIdentity(target, sources)) {
                // 路径不同、inode 相同：写入会把入口（或某个素材）截断成 PNG。
                problems.add("输出与输入是同一个文件（硬链接）: " + escape(target.toString()));
            }
            boolean sameKey = !seen.add(key);
            boolean sameFile = sharesIdentity(target, seenTargets);
            seenTargets.add(target);
            if (sameKey || sameFile) {
                problems.add("两个输入写到同一个输出: " + escape(target.toString()));
            }
        }
        return problems;
    }

    /**
     * 目标是否与 {@code others} 里**已存在的**某个路径是同一个文件（硬链接）。
     *
     * <p>路径身份只能归一同名路径与符号链接：硬链接是另一个目录项指向同一个 inode，
     * 路径怎么比都不相等，而写入会截断同一份内容（2026-11-20 审查 P2）。
     * 这里逐对问 {@link Files#isSameFile} 而不是比 {@code fileKey()}：Windows 上
     * {@code BasicFileAttributes.fileKey()} 恒为 null（JDK 21 实测），拿它当判据等于没判。
     * Python 侧 ({@code cli._identity}) 用设备号 + inode 问同一件事。
     */
    private static boolean sharesIdentity(Path target, List<Path> others) {
        if (!Files.exists(target)) {
            return false;
        }
        for (Path other : others) {
            try {
                if (Files.exists(other) && Files.isSameFile(target, other)) {
                    return true;
                }
            } catch (IOException ignored) {
                // 身份查不出来就按路径判，调用方已经做过。
            }
        }
        return false;
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
        if (deps != null && !"-".equals(deps)) {
            // 与 `-o` / `--asset-root` 一致：非法路径是**用法**问题（exit 2）。
            // 晚一步解析会落到最底下的 InvalidPathException 分支，报成 exit 4
            // "宿主 I/O 失败"（2026-11-14 审查 P2）。
            try {
                Paths.get(deps);
            } catch (java.nio.file.InvalidPathException invalid) {
                return usage("--deps 路径含非法字符: " + escape(deps)
                        + "（" + invalid.getReason() + "）");
            }
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
            if (deps != null && !"-".equals(deps)) {
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
            // 目标已存在却**不是普通文件**（目录之类）时必须现在挡住：走到改名阶段才发现的话，
            // 两份产物里先改名的那份已经替换成功，命令失败却留下了新产物（2026-11-20 审查 P2）。
            String badOutput = nonFileTarget(check ? null : output);
            if (badOutput != null) {
                return usage("输出路径" + badOutput + ": " + escape(output));
            }
            String badDeps = ("-".equals(deps)) ? null : nonFileTarget(deps);
            if (badDeps != null) {
                return usage("清单路径" + badDeps + ": " + escape(deps));
            }
            // check 也要走完整编码：只渲染不编码时，编码器的失败对 check 不可见
            // （PRD 合同；发布态复核第二节）。清单必须**编码之后**才发布：先写清单再编码，
            // 编码阶段资源不足会留下"命令失败、清单却是新的"（2026-11-20 审查 P2）。
            byte[] png = io.github.texel.core.PngEncoder.encode(compiled.width(),
                    compiled.height(), compiled.renderRgba());
            if (check) {
                if (deps != null && !"-".equals(deps)) {
                    // check 不产出 PNG，清单就是它唯一的产物（集成层靠 `check --deps` 收集依赖）。
                    int code = writeDependencies(deps, compiled.dependencies());
                    if (code != 0) {
                        return code;
                    }
                }
                if ("-".equals(deps)) {
                    // check 不产出 PNG，清单就是它唯一的产物；`--deps -` 时必须打到 stdout，
                    // 否则命令返回 0 却什么也没给出（Python 侧一直是打的，2026-11-20 审查 P2）。
                    OUT.print(new String(manifestBytes(compiled.dependencies()),
                            java.nio.charset.Charset.forName("UTF-8")));
                    OUT.flush();
                }
                return 0;
            }
            Path targetFile = Paths.get(output).toAbsolutePath();
            Path depsFile = (deps == null || "-".equals(deps))
                    ? null : Paths.get(deps).toAbsolutePath();
            // `--deps -` 的清单文本在**发布之前**就序列化好：发布成功后再临时分配字节和字符串，
            // 一旦 OutOfMemoryError，PNG 已经被替换、stdout 却什么都没有，命令还报 CLI001
            // （CodeRabbit 复审）。先备好文本，发布成功后再打出去。
            String stdoutManifest = "-".equals(deps)
                    ? new String(manifestBytes(compiled.dependencies()),
                            java.nio.charset.Charset.forName("UTF-8"))
                    : null;
            // 两份产物当成一次事务：都写进临时文件并刷盘之后才改名，改名失败整体回滚。
            int published = publish(targetFile, png, depsFile,
                    depsFile == null ? null : manifestBytes(compiled.dependencies()));
            if (published != 0) {
                return published;
            }
            if (stdoutManifest != null) {
                // PRD 要求「依赖清单可写 stdout」；旗标本身 PRD 未规定，这里取约定的 '-'，
                // 并且**不能**把 '-' 当成文件名写出去。放到产物改名之后才打：否则 PNG
                // 写失败时清单已经在 stdout 上了，而产物一个都没更新。
                OUT.print(stdoutManifest);
                OUT.flush();
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
        // 三个 CLI（texel-compiler / texel-compiler-python / texel-import）共用一套版式：
        // `usage:` 首行 + 缩进选项表（说明列从第 22 列起）+ 最后一段中文说明，程序名用真实
        // 命令名。改这里时另外两份帮助文本要一起改。
        return "usage: texel-compiler capabilities --json\n"
                + "       texel-compiler compile ENTRY --asset-root ROOT -o OUT\n"
                + "       texel-compiler check ENTRY --asset-root ROOT\n"
                + "       texel-compiler [-o OUT] PATH...\n"
                + "\n"
                + "  ENTRY              入口逻辑路径(相对 --asset-root),compile/check 用\n"
                + "  --asset-root ROOT  资产根目录,compile/check 用\n"
                + "  --bind name=value  给入口参数绑一个值,可重复\n"
                + "  --deps FILE        写出依赖清单,FILE 为 - 时写到标准输出\n"
                + "  -o, --out OUT      输出路径(批量模式只在恰好一个输入时允许)\n"
                + "  PATH               输入 .pxl 文件或目录(目录递归收集 *.pxl)\n"
                + "  --                 其后的参数一律当路径(文件名以 - 开头时用)\n"
                + "  --json             只与 capabilities 合用:打印能力档案\n"
                + "\n"
                + "省略 -o 时,批量模式把每个输入写到同目录、同名的 .png。\n"
                + "compile 必须显式给 -o;check 不接受输出路径。\n";
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
