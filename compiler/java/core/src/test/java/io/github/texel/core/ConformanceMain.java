package io.github.texel.core;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 索引驱动的完整语料验收（规范 §13，README §2 的判据）。
 *
 * <p>判定只从 {@code CASES.tsv} 出发，不遍历目录"看有什么跑什么"——后者会让漏掉的
 * 用例静默通过。四条硬规矩：
 *
 * <ol>
 *   <li>索引里的每一条都必须有目录，目录里的每一个都必须在索引里（双向核对）。</li>
 *   <li>负例按<b>目录里实际存在的输入文件</b>分派受测侧，不按错误码猜（§13.1）。</li>
 *   <li>没有"跳过"这个概念：缺文件、生成失败、空集合一律失败。</li>
 *   <li>计数必须对得上，否则"跑完了"这句话没有证据。</li>
 * </ol>
 */
public final class ConformanceMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final String[] COLUMNS = {
        "case", "source", "form", "size", "colors", "encoding", "covers", "input",
    };

    private static Path repo;
    private static Path casesDir;
    private static String python;
    private static final List<String> FAILURES = new ArrayList<String>();

    /** 一次性批处理得到的探针结果：casePath → 抽取产物与像素摘要。 */
    private static final Map<String, Probe> PROBES = new HashMap<String, Probe>();
    private static Path probeDirectory;

    /** 一个用例在 Python 侧的结果（批处理一次写回，取代逐用例 spawn）。 */
    private static final class Probe {
        final boolean ok;
        final String pxl;
        final String sha256;
        final String size;
        final String code;
        final String message;

        Probe(boolean ok, String pxl, String sha256, String size, String code, String message) {
            this.ok = ok;
            this.pxl = pxl;
            this.sha256 = sha256;
            this.size = size;
            this.code = code;
            this.message = message;
        }
    }
    private static final Map<String, Integer> EXECUTED = new LinkedHashMap<String, Integer>();
    /** suite 成员也被真的判定过,必须计入"执行了多少"，否则过滤到 suite 时 CHECKS=0。 */
    private static int suiteMembers;
    /** Compiler-declared conformance profile and selected base cases. */
    private static String profile;
    private static java.util.Set<String> baseSelection;

    private ConformanceMain() {
    }

    public static void main(String[] args) {
        // 必须**绝对化**:下面的 python 子进程会 chdir 到 repo，而它接收的路径参数是
        // 本进程拼出来的。若 repo 是相对的(`-Dtxel.repo=..`)，chdir 之后同一个相对路径
        // 会指向别处，于是"文件不存在"——一个会让变异自检误判"逮住了"的坑。
        repo = Paths.get(System.getProperty("txel.repo", ".")).toAbsolutePath().normalize();
        casesDir = repo.resolve("spec").resolve("conformance").resolve("cases");
        python = System.getProperty("txel.python", "python");

        // 可选参数：只跑名字里包含该子串的用例（调试与变异自检用）。
        // 一旦过滤，全量门禁（计数/覆盖）就失去意义，必须一起停用——否则
        // "跑了 1 条"会被当成"全覆盖"。
        // 过滤器可以有多个（OR）：CI 在 JDK 8/11/17 上只跑代表性用例，
        // 全量 809 项留给 JDK 21 那一遍。
        final String[] filters = args.length > 0 ? args.clone() : new String[0];
        String filter = filters.length > 0 ? filters[0] : null;
        if (filter == null) {
            generateInputs();
        } else {
            System.out.println("过滤模式：" + Arrays.toString(filters)
                    + "（跳过生成器与全量门禁）");
        }
        Map<String, Map<String, String>> index = loadIndex();
        selectProfileCases(index);
        checkIndexSemantics(index);
        collectProbes(index, filters);
        checkDirectoriesMatchIndex(index);
        checkStrayDirectories(index);

        int roundTrip = 0;
        int renderOnly = 0;
        int negativeRender = 0;
        int negativeExtract = 0;

        // 每个过滤器至少要命中一条：拼错的名字会让"整组参数仍然全绿"——那比报错更糟。
        Map<String, Integer> filterHits = new LinkedHashMap<String, Integer>();
        for (String item : filters) {
            filterHits.put(item, Integer.valueOf(0));
        }
        for (Map.Entry<String, Map<String, String>> entry : index.entrySet()) {
            String casePath = entry.getKey();
            Map<String, String> row = entry.getValue();
            if (filters.length > 0 && !matchesAny(casePath, filters)) {
                continue;
            }
            if (baseSelection != null && !baseSelection.contains(casePath)) {
                continue;
            }
            for (String item : filters) {
                if (casePath.indexOf(item) >= 0) {
                    filterHits.put(item, Integer.valueOf(filterHits.get(item).intValue() + 1));
                }
            }
            Path directory = casesDir.resolve(casePath);
            if (!Files.isDirectory(directory)) {
                continue;   // 已在 checkDirectoriesMatchIndex 里报过
            }
            String name = directory.getFileName().toString();
            if (casePath.startsWith("negative/")) {
                if (Files.exists(directory.resolve("input.pxl"))) {
                    runRenderNegative(casePath, directory);
                    negativeRender++;
                } else if (Files.exists(directory.resolve("input.png"))) {
                    runExtractNegative(casePath, directory);
                    negativeExtract++;
                } else {
                    FAILURES.add(casePath + "：负例既没有 input.pxl 也没有 input.png");
                }
                continue;
            }
            if (casePath.startsWith("syn-") || casePath.startsWith("real-")) {
                // 形态由索引的 form 列决定（§13.3），不看目录里有没有 members/。
                if (isBatchCase(row)) {
                    runSuite(casePath, directory, row);
                } else {
                    runRoundTrip(casePath, directory);
                    roundTrip++;
                }
                continue;
            }
            if (name.startsWith("op-") || name.startsWith("blend-")) {
                runRenderOnly(casePath, directory, row);
                renderOnly++;
            } else {
                FAILURES.add(casePath + "：无法按前缀判定用例形态");
            }
        }

        for (Map.Entry<String, Integer> hit : filterHits.entrySet()) {
            if (hit.getValue().intValue() == 0) {
                FAILURES.add("过滤器 " + hit.getKey() + " 一条用例都没命中（拼错了？）");
            }
        }

        EXECUTED.put("往返正例", Integer.valueOf(roundTrip));
        EXECUTED.put("仅渲染正例", Integer.valueOf(renderOnly));
        EXECUTED.put("渲染负例", Integer.valueOf(negativeRender));
        EXECUTED.put("抽取负例", Integer.valueOf(negativeExtract));

        // 覆盖门禁：六个错误码、两种受测侧都必须至少被真的触发过一次。
        // 过滤模式下不成立（只跑了子集），明确跳过而不是让它"看起来通过"。
        if (filter == null) {
            checkCoverage(index, negativeRender, negativeExtract);
        }
        // 批处理目录里有每个用例的 .pxl（726 个文件、约 6 MiB），成功与失败路径都要删。
        deleteTree(probeDirectory);
        report(index.size(), roundTrip, renderOnly, negativeRender, negativeExtract);
    }

    // ---------------------------------------------------------------- 能力档案 → 语料子集

    private static void selectProfileCases(Map<String, Map<String, String>> index) {
        profile = PxlParser.conformanceProfile();
        Path profilesFile = repo.resolve("spec").resolve("conformance").resolve("profiles.tsv");
        try {
            baseSelection = ProfileSelection.select(profilesFile, profile, "base", index.keySet());
        } catch (IOException failure) {
            FAILURES.add("读不了 profiles.tsv: " + failure);
            return;
        } catch (IllegalStateException failure) {
            FAILURES.add("profiles.tsv 映射非法: " + failure.getMessage());
            return;
        }
        if (baseSelection == null) {
            FAILURES.add("能力档案 " + profile + " 没有声明 base suite");
        }
        System.out.println("能力档案 " + profile + "：base "
                + (baseSelection == null ? "-" : Integer.toString(baseSelection.size()))
                + " / " + index.size() + " 条");
    }

    private static boolean matchesAny(String casePath, String[] filters) {
        for (String filter : filters) {
            if (casePath.indexOf(filter) >= 0) {
                return true;
            }
        }
        return false;
    }

    // ---------------------------------------------------------------- Python 批处理

    /**
     * 把全部需要的 `input.png` 交给**一个** Python 进程处理。
     *
     * <p>此前每个往返正例 spawn 两次（抽取 + 摘要）、每个抽取负例再 spawn 一次，整份语料
     * 约 1,400 次进程启动，单次 `gradlew check` 因此要 4 分多钟。判据一条都没变：抽取仍
     * 走 `importer`、摘要仍走解码器，只是不再"一个文件一次冷启动"。
     */
    private static void collectProbes(Map<String, Map<String, String>> index,
            String[] filters) {
        List<String> tasks = new ArrayList<String>();
        try {
            probeDirectory = Files.createTempDirectory("conformance-probes-");
        } catch (IOException failure) {
            FAILURES.add("无法创建批处理目录 " + failure);
            return;
        }
        for (Map.Entry<String, Map<String, String>> entry : index.entrySet()) {
            String casePath = entry.getKey();
            Map<String, String> row = entry.getValue();
            Path directory = casesDir.resolve(casePath);
            if (!Files.isDirectory(directory)) {
                continue;
            }
            // 过滤模式下只探真正会跑的用例：否则 Java 判 13 项、Python 仍要处理全部 726 个
            // 任务（审查实测），smoke 的"快"就落空了。
            if (filters.length > 0 && !matchesAny(casePath, filters)) {
                continue;
            }
            if (casePath.startsWith("negative/")) {
                Path png = directory.resolve("input.png");
                if (Files.exists(png)) {
                    tasks.add(taskLine(casePath, png));
                }
                continue;
            }
            if (!(casePath.startsWith("syn-") || casePath.startsWith("real-"))) {
                continue;
            }
            if (!isBatchCase(row)) {
                Path png = directory.resolve("input.png");
                if (Files.exists(png)) {
                    tasks.add(taskLine(casePath, png));
                }
                continue;
            }
            Path membersDir = directory.resolve("members");
            if (!Files.isDirectory(membersDir)) {
                continue;
            }
            try {
                DirectoryStream<Path> stream = Files.newDirectoryStream(membersDir);
                try {
                    for (Path member : stream) {
                        if (!Files.isDirectory(member)) {
                            continue;
                        }
                        Path png = member.resolve("input.png");
                        // 缺陷成员（只有 expected.error）也要探：它验的是"必须失败"。
                        if (Files.exists(png)) {
                            tasks.add(taskLine(casePath + "/" + member.getFileName(), png));
                        }
                    }
                } finally {
                    stream.close();
                }
            } catch (IOException failure) {
                FAILURES.add(casePath + "：无法枚举 members/ " + failure);
            }
        }
        if (tasks.isEmpty()) {
            return;
        }
        Path taskFile = probeDirectory.resolve("tasks.tsv");
        StringBuilder builder = new StringBuilder();
        for (String task : tasks) {
            builder.append(task).append('\n');
        }
        try {
            Files.write(taskFile, builder.toString().getBytes(UTF8));
        } catch (IOException failure) {
            FAILURES.add("无法写出批处理任务表 " + failure);
            return;
        }
        Result result = run(python, script("compiler/java/tools/batch_probe.py").toString(),
                taskFile.toString(), probeDirectory.toString());
        if (result.exit != 0) {
            FAILURES.add("Python 批处理失败（" + tasks.size() + " 个任务）："
                    + firstLine(result.stderr));
            return;
        }
        for (String line : result.stdout.split("\n")) {
            if (line.trim().isEmpty()) {
                continue;
            }
            Json.Value record;
            try {
                record = Json.parse(line.getBytes(UTF8));
            } catch (PxlFormatException failure) {
                FAILURES.add("批处理输出不是合法 JSON：" + line);
                continue;
            }
            String id = textOf(record, "id");
            if (id == null) {
                FAILURES.add("批处理输出缺 id：" + line);
                continue;
            }
            Json.Value okValue = record.members == null ? null : record.members.get("ok");
            boolean ok = okValue != null && okValue.truth;
            PROBES.put(id, new Probe(ok, textOf(record, "pxl"), textOf(record, "sha256"),
                    textOf(record, "size"), textOf(record, "code"), textOf(record, "message")));
        }
        if (PROBES.size() != tasks.size()) {
            FAILURES.add("批处理只回填了 " + PROBES.size() + " / " + tasks.size() + " 个结果");
        }
    }

    private static String textOf(Json.Value record, String key) {
        Json.Value value = record.members == null ? null : record.members.get(key);
        return value == null ? null : value.text;
    }

    private static String taskLine(String casePath, Path png) {
        return casePath + "\t" + png.toAbsolutePath().toString();
    }

    private static Path script(String relative) {
        Path path = repo;
        for (String part : relative.split("/")) {
            path = path.resolve(part);
        }
        return path;
    }

    // ---------------------------------------------------------------- 索引

    private static Map<String, Map<String, String>> loadIndex() {
        Map<String, Map<String, String>> index = new LinkedHashMap<String, Map<String, String>>();
        Path tsv = repo.resolve("spec").resolve("conformance").resolve("CASES.tsv");
        List<String> lines;
        try {
            lines = Files.readAllLines(tsv, UTF8);
        } catch (IOException failure) {
            throw new IllegalStateException("读不到 CASES.tsv: " + failure, failure);
        }
        if (lines.isEmpty()) {
            throw new IllegalStateException("CASES.tsv 是空的");
        }
        String header = lines.get(0);
        List<String> headerCells = Arrays.asList(header.split("\t", -1));
        if (!headerCells.equals(Arrays.asList(COLUMNS))) {
            throw new IllegalStateException("CASES.tsv 表头与 §3 的八列不一致: " + headerCells);
        }
        for (int number = 1; number < lines.size(); number++) {
            String line = lines.get(number);
            if (line.isEmpty()) {
                continue;
            }
            String[] cells = line.split("\t", -1);
            if (cells.length != COLUMNS.length) {
                throw new IllegalStateException(
                        "CASES.tsv 第 " + (number + 1) + " 行有 " + cells.length + " 列，应为 8");
            }
            Map<String, String> row = new LinkedHashMap<String, String>();
            for (int index2 = 0; index2 < COLUMNS.length; index2++) {
                row.put(COLUMNS[index2], cells[index2]);
            }
            if (index.containsKey(cells[0])) {
                throw new IllegalStateException("用例名重复: " + cells[0]);
            }
            index.put(cells[0], row);
        }
        return index;
    }

    private static void checkIndexSemantics(Map<String, Map<String, String>> index) {
        for (Map.Entry<String, Map<String, String>> entry : index.entrySet()) {
            String casePath = entry.getKey();
            Map<String, String> row = entry.getValue();
            for (String column : COLUMNS) {
                if (row.get(column).isEmpty()) {
                    FAILURES.add(casePath + "：索引列 " + column + " 为空");
                }
            }
            String source = row.get("source");
            if (!"synthetic".equals(source) && !"real".equals(source)) {
                FAILURES.add(casePath + "：source 取值非法 " + source);
            }
            String form = row.get("form");
            if (!"single".equals(form) && !"batch".equals(form)) {
                FAILURES.add(casePath + "：form 取值非法 " + form);
            }
            if ("batch".equals(form) && casePath.startsWith("negative/")) {
                FAILURES.add(casePath + "：负例的 form 必须是 single（§13.1）");
            }
            String input = row.get("input");
            if (!"committed".equals(input) && !"generated".equals(input)) {
                FAILURES.add(casePath + "：input 取值非法 " + input);
            }
            String encoding = row.get("encoding");
            // §13.3:`varying` / `encoding` 区间 / `size` 的 `Wx(...)` 都是**批量行**的
            // 写法。单例与负例上用它们等于声明一件无从执行的事。
            if (!"batch".equals(form)) {
                if ("varying".equals(row.get("colors"))) {
                    FAILURES.add(casePath + "：只有 form=batch 的行才能写 colors=varying");
                }
                if (encoding != null && encoding.indexOf("..") > 0) {
                    FAILURES.add(casePath + "：只有 form=batch 的行才能写 encoding 区间");
                }
                String sizeValue = row.get("size");
                if (sizeValue != null && sizeValue.indexOf('(') >= 0) {
                    FAILURES.add(casePath + "：只有 form=batch 的行才能写 `Wx(...)` 形式的 size");
                }
            }
            if (!validEncodingColumn(encoding)) {
                FAILURES.add(casePath + "：encoding 取值非法 " + encoding);
            }
            String colors = row.get("colors");
            if (!"-".equals(colors) && !"varying".equals(colors) && !colors.matches("\\d+")) {
                FAILURES.add(casePath + "：colors 取值非法 " + colors);
            }
            String size = row.get("size");
            // §13.3：`WxH` / `Wx(Hlo..Hhi)` / `-`。括号里**不允许**自由说明。
            if (!"-".equals(size) && !size.matches("\\d+x\\d+")
                    && !size.matches("\\d+x\\(\\d+\\.\\.\\d+\\)")) {
                FAILURES.add(casePath + "：size 取值非法 " + size);
            }
            if (casePath.startsWith("negative/")) {
                if (!row.get("covers").matches("^PXL00[1-6]\\((渲染侧|抽取侧)\\):.*")) {
                    FAILURES.add(casePath + "：负例 covers 必须以 PXL00N(侧): 开头");
                }
                // §13.3：负例没有 expected.pxl，`size` / `colors` 在那里是**无法校验的
                // 声明**，一律写 `-`（实测信息属于 notes.md）。
                if (!"-".equals(row.get("size"))) {
                    FAILURES.add(casePath + "：负例的 size 必须写 `-`，实际 " + row.get("size"));
                }
                if (!"-".equals(row.get("colors"))) {
                    FAILURES.add(casePath + "：负例的 colors 必须写 `-`，实际 " + row.get("colors"));
                }
                if (!"single".equals(row.get("form"))) {
                    FAILURES.add(casePath + "：负例的 form 必须是 single");
                }
            } else {
                // §13.3 把 `-` 只留给负例：正例行三列都必须给出可校验的值。
                // 否则批量行三列一起写 `-` 就能让三列同时"免责"（审查第十一轮实测）。
                for (String column : new String[] {"size", "colors", "encoding"}) {
                    if ("-".equals(row.get(column))) {
                        FAILURES.add(casePath + "：正例的 " + column + " 不得写 `-`");
                    }
                }
            }
            Path notes = casesDir.resolve(casePath).resolve("notes.md");
            boolean hasRecipe = Files.exists(notes) && readFully(notes).contains("## recipe");
            if ("generated".equals(input) && !hasRecipe) {
                FAILURES.add(casePath + "：input=generated 但 notes.md 没有 ## recipe");
            }
            if ("committed".equals(input) && hasRecipe) {
                FAILURES.add(casePath + "：input=committed 却带 ## recipe（索引自相矛盾）");
            }
        }
    }

    private static void checkDirectoriesMatchIndex(Map<String, Map<String, String>> index) {
        List<String> missing = new ArrayList<String>();
        for (String casePath : index.keySet()) {
            if (!Files.isDirectory(casesDir.resolve(casePath))) {
                missing.add(casePath);
            }
        }
        if (!missing.isEmpty()) {
            FAILURES.add("索引里有 " + missing.size() + " 条没有目录: " + preview(missing));
        }
        Set<String> present = new HashSet<String>();
        collectCaseDirs(casesDir, "", present, index);
        List<String> extra = new ArrayList<String>();
        for (String casePath : present) {
            if (!index.containsKey(casePath)) {
                extra.add(casePath);
            }
        }
        if (!extra.isEmpty()) {
            FAILURES.add("目录里有 " + extra.size() + " 条不在索引中: " + preview(extra));
        }
    }

    /** 目录下不得再有子目录（成员目录是叶子）。 */
    private static void checkNoSubdirectories(Path directory, String label) {
        try {
            DirectoryStream<Path> stream = Files.newDirectoryStream(directory);
            try {
                for (Path child : stream) {
                    if (Files.isDirectory(child)) {
                        FAILURES.add(label + "：成员目录里有多余子目录 " + child.getFileName());
                    }
                }
            } finally {
                stream.close();
            }
        } catch (IOException failure) {
            FAILURES.add(label + "：读取目录失败 " + failure);
        }
    }
    /** 取索引里某一行的列（取不到返回 null）。 */
    private static Map<String, String> rowOf(Map<String, Map<String, String>> index, String casePath) {
        return index.get(casePath);
    }

    /**
     * 是否 §13.7 的批量用例——**只看索引的 `form` 列**（规范 §13.3）。
     *
     * <p>此前先后用过两种更弱的判据，都被审查推翻了：「目录里有 manifest.tsv」——
     * 投一个空文件就能把整棵 `members/` 变成无人校验区；「`covers` 里出现 §13.7」——
     * 那是对自由文本做子串匹配，§13.6 明写判定不看它，而且负例也会提到 §13.7。
     */
    private static boolean isBatchCase(Map<String, String> row) {
        return row != null && "batch".equals(row.get("form"));
    }

    /**
     * 用例目录内部不得有多余子目录。
     *
     * <p>只核对"cases/ 下的一级目录"是不够的：`syn-1x1-opaque/stray-subdir/` 这种
     * 谁都没在用的目录会被双向核对漏掉（审查实测过，仍然全绿）。批量用例的
     * `members/` 是唯一合法子目录，且其合法性由**索引声明**决定，不由文件存在决定。
     */
    private static void checkStrayDirectories(Map<String, Map<String, String>> index) {
        for (String casePath : index.keySet()) {
            Path directory = casesDir.resolve(casePath);
            if (!Files.isDirectory(directory)) {
                continue;
            }
            try {
                DirectoryStream<Path> stream = Files.newDirectoryStream(directory);
                try {
                    for (Path child : stream) {
                        if (!Files.isDirectory(child)) {
                            continue;
                        }
                        String name = child.getFileName().toString();
                        if ("members".equals(name)) {
                            // `members/` 是 §13.7 批量用例的目录形态。判据只看
                            // 索引的 `form` 列（§13.3）——既不看目录里有没有
                            // manifest.tsv，也不看 covers 的散文。
                            if (!isBatchCase(rowOf(index, casePath))) {
                                FAILURES.add(casePath + "：索引 form 不是 batch，但目录里有 members/");
                            }
                            continue;
                        }
                        FAILURES.add(casePath + "：用例目录里有多余子目录 " + name);
                    }
                } finally {
                    stream.close();
                }
            } catch (IOException failure) {
                FAILURES.add(casePath + "：读取目录失败 " + failure);
            }
            boolean declared = isBatchCase(rowOf(index, casePath));
            boolean hasManifest = Files.exists(directory.resolve("manifest.tsv"));
            if (declared != hasManifest) {
                FAILURES.add(casePath + "：索引" + (declared ? "声明" : "未声明")
                        + "为 §13.7 批量用例，但目录" + (hasManifest ? "有" : "没有")
                        + " manifest.tsv");
            }
        }
    }
    private static void collectCaseDirs(Path root, String prefix, Set<String> into,
            Map<String, Map<String, String>> index) {
        if (!Files.isDirectory(root)) {
            return;
        }
        try {
            DirectoryStream<Path> stream = Files.newDirectoryStream(root);
            try {
                for (Path child : stream) {
                    if (!Files.isDirectory(child)) {
                        continue;
                    }
                    String name = child.getFileName().toString();
                    String relative = prefix.isEmpty() ? name : prefix + "/" + name;
                    if ("negative".equals(name)) {
                        collectCaseDirs(child, relative, into, index);
                    } else if ("members".equals(name) && isBatchCase(rowOf(index, prefix))) {
                        // 只有**索引声明为批量用例**的用例目录，其 members/ 才合法；
                        // 其余任何层级出现 members/（含 cases/members、negative/members）
                        // 都要当成"不在索引中的目录"报出来。
                        continue;
                    } else {
                        into.add(relative);
                    }
                }
            } finally {
                stream.close();
            }
        } catch (IOException failure) {
            FAILURES.add("遍历 " + root + " 失败: " + failure);
        }
    }

    // ---------------------------------------------------------------- 各形态

    private static void runRoundTrip(String casePath, Path directory) {
        Path png = directory.resolve("input.png");
        Path pxl = directory.resolve("expected.pxl");
        Path digest = directory.resolve("expected.pixels.sha256");
        for (Path required : new Path[] {png, pxl, digest, directory.resolve("notes.md")}) {
            if (!Files.exists(required)) {
                FAILURES.add(casePath + "：缺文件 " + required.getFileName());
                return;
            }
        }
        String expectedDigest = readFully(digest).trim();
        byte[] document = readBytes(pxl);
        Renderer renderer;
        try {
            renderer = PxlParser.parse(document);
        } catch (PxlFormatException failure) {
            FAILURES.add(casePath + "：expected.pxl 解析失败 " + failure.getMessage());
            return;
        }
        byte[] rgba = renderer.renderRgba();
        checkRender(casePath, "expected.pxl", renderer, rgba, expectedDigest);

        // 判据 1 + 3：抽取器从 input.png 产出逐字节相同的文本，且像素闭环一致。
        // 抽取与摘要都来自**同一次** Python 批处理（见 collectProbes）。
        Probe probe = PROBES.get(casePath);
        if (probe == null) {
            FAILURES.add(casePath + "：批处理里没有这个用例的结果");
            return;
        }
        if (!probe.ok) {
            FAILURES.add(casePath + "：抽取器失败 " + probe.code + " " + probe.message);
            return;
        }
        try {
            byte[] produced = Files.readAllBytes(probeDirectory.resolve(probe.pxl));
            if (!Arrays.equals(produced, document)) {
                FAILURES.add(casePath + "：抽取结果与 expected.pxl 不是逐字节相同"
                        + "（抽取 " + produced.length + " 字节，期望 " + document.length + " 字节）");
            }
        } catch (IOException failure) {
            FAILURES.add(casePath + "：读不到批处理的抽取产物 " + failure);
            return;
        }
        if (probe.sha256 == null || probe.size == null) {
            FAILURES.add(casePath + "：input.png 摘要输出异常");
            return;
        }
        String[] parts = new String[] {probe.size, probe.sha256};
        if (!parts[1].equals(expectedDigest)) {
            FAILURES.add(casePath + "：input.png 的像素摘要与 expected.pixels.sha256 不符");
        }
        if (!parts[0].equals(renderer.width() + "x" + renderer.height())) {
            FAILURES.add(casePath + "：input.png 尺寸 " + parts[0] + " 与文档声明 "
                    + renderer.width() + "x" + renderer.height() + " 不符");
        }
    }

    private static void runRenderOnly(String casePath, Path directory,
            Map<String, String> row) {
        Path pxl = directory.resolve("input.pxl");
        Path digest = directory.resolve("expected.pixels.sha256");
        for (Path required : new Path[] {pxl, digest, directory.resolve("notes.md")}) {
            if (!Files.exists(required)) {
                FAILURES.add(casePath + "：缺文件 " + required.getFileName());
                return;
            }
        }
        if (Files.exists(directory.resolve("input.png"))) {
            FAILURES.add(casePath + "：仅渲染正例不应有 input.png");
        }
        try {
            Renderer renderer = PxlParser.parse(readBytes(pxl));
            byte[] rgba = renderer.renderRgba();
            checkRender(casePath, "input.pxl", renderer, rgba, readFully(digest).trim());
            // 索引的 size / colors / encoding 三列在 op-/blend- 上也必须与文档一致。
            // 早先这三列只有往返正例被内容层核对过，其余形态只验形状——把 op-pixel
            // 的 size 改成 99x99 两侧门禁都不会红。
            checkIndexColumns(casePath, row, renderer.width(), renderer.height(),
                    renderer.paletteSize(), renderer.encoding());
        } catch (PxlFormatException failure) {
            FAILURES.add(casePath + "：input.pxl 应被接受，实际 " + failure.getMessage());
        }
    }

    private static void runRenderNegative(String casePath, Path directory) {
        if (Files.exists(directory.resolve("expected.pxl"))) {
            FAILURES.add(casePath + "：负例不得有 expected.pxl");
        }
        String expectedCode = expectedError(casePath, directory);
        if (expectedCode == null) {
            return;
        }
        try {
            PxlParser.parse(readBytes(directory.resolve("input.pxl")));
            FAILURES.add(casePath + "：应抛 " + expectedCode + "，实际解析成功");
        } catch (PxlFormatException failure) {
            if (!expectedCode.equals(failure.code())) {
                FAILURES.add(casePath + "：期望 " + expectedCode + "，实际 " + failure.code()
                        + "（" + failure.getMessage() + "）");
            }
        }
    }

    private static void runExtractNegative(String casePath, Path directory) {
        if (Files.exists(directory.resolve("input.pxl"))) {
            FAILURES.add(casePath + "：input.pxl 与 input.png 互斥（§13.1）");
        }
        String expectedCode = expectedError(casePath, directory);
        if (expectedCode == null) {
            return;
        }
        Probe probe = PROBES.get(casePath);
        if (probe == null) {
            FAILURES.add(casePath + "：批处理里没有这个用例的结果");
            return;
        }
        if (probe.ok) {
            FAILURES.add(casePath + "：抽取器应失败，实际成功");
            return;
        }
        if (probe.code == null || !probe.code.equals(expectedCode)) {
            FAILURES.add(casePath + "：期望错误码 " + expectedCode + "，实际 "
                    + probe.code + " " + probe.message);
        }
        if (probe.pxl != null && Files.exists(probeDirectory.resolve(probe.pxl))) {
            FAILURES.add(casePath + "：负例不得产出输出文件");
        }
    }

    private static void runSuite(String casePath, Path directory,
            Map<String, String> row) {
        Path manifest = directory.resolve("manifest.tsv");
        if (!Files.exists(manifest)) {
            FAILURES.add(casePath + "：批量用例缺 manifest.tsv");
            return;
        }
        List<String> lines;
        try {
            lines = Files.readAllLines(manifest, UTF8);
        } catch (IOException failure) {
            FAILURES.add(casePath + "：读不到 manifest.tsv");
            return;
        }
        if (lines.isEmpty() || !lines.get(0).equals("member\tinput\tencoding\tcolors")) {
            FAILURES.add(casePath + "：manifest.tsv 表头必须是 member/input/encoding/colors");
            return;
        }
        if (lines.size() <= 1) {
            // §13 的「空集合一律失败」：一个成员都没有的批量用例等于没有判据，
            // 0 == 0 的计数相等不能算通过。
            FAILURES.add(casePath + "：批量用例没有任何成员");
            return;
        }
        Path membersDir = directory.resolve("members");
        // `samples/MANIFEST.tsv` 只读一次：每个成员的 input 都要在里面登记（§13.7:1090）。
        Path provenancePath = repo.resolve("samples").resolve("MANIFEST.tsv");
        String sampleManifest = Files.exists(provenancePath) ? readFully(provenancePath) : "";
        int positives = 0;
        int defects = 0;
        Set<String> members = new HashSet<String>();
        List<int[]> memberSizes = new ArrayList<int[]>();
        List<String> memberEncodings = new ArrayList<String>();
        List<Integer> memberPalettes = new ArrayList<Integer>();
        for (int number = 1; number < lines.size(); number++) {
            String line = lines.get(number);
            if (line.isEmpty()) {
                continue;
            }
            String[] cells = line.split("\t", -1);
            if (cells.length != 4) {
                FAILURES.add(casePath + "：manifest 第 " + (number + 1) + " 行不是 4 列");
                continue;
            }
            String member = cells[0];
            if (!members.add(member)) {
                FAILURES.add(casePath + "：manifest 里成员重复 " + member);
            }
            if (!cells[1].startsWith("sample:")) {
                FAILURES.add(casePath + "：成员 " + member + " 的 input 列必须是 sample: 前缀");
            } else {
                checkMemberSource(casePath, member, cells[1].substring("sample:".length()),
                        membersDir.resolve(member), sampleManifest);
            }
            Path memberDir = membersDir.resolve(member);
            if (!Files.isDirectory(memberDir)) {
                FAILURES.add(casePath + "：manifest 声明的成员缺目录 " + member);
                continue;
            }
            // 成员目录本身是叶子：底下再出现子目录，两侧门禁都不会去看它。
            checkNoSubdirectories(memberDir, casePath + "/members/" + member);
            if (Files.exists(memberDir.resolve("input.png"))) {
                if (Files.exists(memberDir.resolve("expected.pxl"))) {
                    runRoundTrip(casePath + "/" + member, memberDir);
                    positives++;
                    suiteMembers++;
                    // 记下成员的实际尺寸与档位，供 checkBatchColumns 与索引列比对。
                    try {
                        Renderer probe = PxlParser.parse(
                                readBytes(memberDir.resolve("expected.pxl")));
                        memberSizes.add(new int[] {probe.width(), probe.height()});
                        memberEncodings.add(probe.encoding());
                        memberPalettes.add(Integer.valueOf(probe.paletteSize()));
                    } catch (PxlFormatException ignored) {
                        // 解析失败已由 runRoundTrip 报出，这里不重复计。
                    }
                } else if (Files.exists(memberDir.resolve("expected.error"))) {
                    runExtractNegative(casePath + "/" + member, memberDir);
                    defects++;
                    suiteMembers++;
                } else {
                    FAILURES.add(casePath + "：成员 " + member + " 既无 expected.pxl 也无 expected.error");
                }
            } else {
                FAILURES.add(casePath + "：成员 " + member + " 缺 input.png");
            }
        }
        int directories = 0;
        try {
            DirectoryStream<Path> stream = Files.newDirectoryStream(membersDir);
            try {
                for (Path child : stream) {
                    if (Files.isDirectory(child)) {
                        directories++;
                        if (!members.contains(child.getFileName().toString())) {
                            FAILURES.add(casePath + "：有成员目录未在 manifest 登记: "
                                    + child.getFileName());
                        }
                    }
                }
            } finally {
                stream.close();
            }
        } catch (IOException failure) {
            FAILURES.add(casePath + "：读不到 members/");
        }
        if (directories != members.size()) {
            FAILURES.add(casePath + "：members/ 目录数 " + directories + " 与 manifest 行数 "
                    + members.size() + " 不符");
        }
        checkBatchColumns(casePath, row, memberSizes, memberEncodings, memberPalettes);
        checkAggregate(casePath, directory, positives, defects);
    }

    /**
     * 索引三列（`size` / `colors` / `encoding`）与文档实际内容逐项核对。
     *
     * <p>README §3 把三列定义成**事实**：`colors` 等于 `palette` 长度、`size` 是宽×高、
     * `encoding` 是期望档位。索引与语料分叉必须失败——否则改坏索引没有任何门禁会红。
     * 批量行的区间写法（`char..index4`）与 `varying` / `-` 跳过。
     */
    private static void checkIndexColumns(String casePath, Map<String, String> row,
            int width, int height, int paletteSize, String encoding) {
        if (row == null) {
            return;
        }
        String size = row.get("size");
        if (size != null && size.matches("\\d+x\\d+")) {
            String actual = width + "x" + height;
            if (!size.equals(actual)) {
                FAILURES.add(casePath + "：size 列写的是 " + size + "，文档是 " + actual);
            }
        }
        String colors = row.get("colors");
        if (colors != null && colors.matches("\\d+")) {
            if (Integer.parseInt(colors) != paletteSize) {
                FAILURES.add(casePath + "：colors 列写的是 " + colors
                        + "，文档 palette 长度是 " + paletteSize);
            }
        }
        String declared = row.get("encoding");
        if (declared != null && !"-".equals(declared) && declared.indexOf("..") < 0) {
            if (!declared.equals(encoding)) {
                FAILURES.add(casePath + "：encoding 列写的是 " + declared
                        + "，文档是 " + encoding);
            }
        }
    }

    /**
     * 批量行（§13.7）的 `size` / `encoding` 列同样必须与成员实际内容一致。
     *
     * <p>批量行用的是区间写法：`size = 16x(4 种高度,16..512)`、`encoding = char..index4`。
     * 这里的判据是——每个成员的宽必须等于 `size` 里 `x` 前那个数，高必须落在括号里的
     * 区间内；每个成员的档位必须落在 `encoding` 声明的区间内。只验形状会让
     * "把这两列改成 16x16 / char..char" 三道门禁全绿（审查第八轮实测过）。
     */
    private static void checkBatchColumns(String casePath, Map<String, String> row,
            List<int[]> sizes, List<String> encodings, List<Integer> palettes) {
        if (row == null || sizes.isEmpty()) {
            return;
        }
        String size = row.get("size");
        if (size == null) {
            return;
        }
        // 注意：**不能**在 `size == "-"` 时 return ——那会让 colors/encoding 的检查
        // 一起被跳过，批量行三列写 `-` 就全绿了。`-` 本身的合法性由上一条（正例不得
        // 写 `-`）负责。
        java.util.regex.Matcher plain = java.util.regex.Pattern
                .compile("^(\\d+)x(\\d+)$").matcher(size);
        // 规范 §13.3：批量行的 size 是 `宽x(低..高)`，括号里**不允许**自由说明。
        java.util.regex.Matcher batch = java.util.regex.Pattern
                .compile("^(\\d+)x\\((\\d+)\\.\\.(\\d+)\\)$").matcher(size);
        if (plain.matches()) {
            int width = Integer.parseInt(plain.group(1));
            int height = Integer.parseInt(plain.group(2));
            for (int index = 0; index < sizes.size(); index++) {
                int[] actual = sizes.get(index);
                if (actual[0] != width || actual[1] != height) {
                    FAILURES.add(casePath + "：成员尺寸 " + actual[0] + "x" + actual[1]
                            + " 与 size 列声明的 " + size + " 不符");
                    break;
                }
            }
        } else if (batch.matches()) {
            int width = Integer.parseInt(batch.group(1));
            int low = Integer.parseInt(batch.group(2));
            int high = Integer.parseInt(batch.group(3));
            for (int index = 0; index < sizes.size(); index++) {
                int[] actual = sizes.get(index);
                if (actual[0] != width) {
                    FAILURES.add(casePath + "：成员宽 " + actual[0] + " 与 size 列声明的 "
                            + width + " 不符");
                    break;
                }
                if (low >= 0 && (actual[1] < low || actual[1] > high)) {
                    FAILURES.add(casePath + "：成员高 " + actual[1] + " 不在 size 列声明的 "
                            + low + ".." + high + " 内");
                    break;
                }
            }
        } else {
            FAILURES.add(casePath + "：批量行的 size 既不是 `宽x高` 也不是 `宽x(...)`: " + size);
        }
        String colors = row.get("colors");
        if (colors != null && colors.matches("\\d+")) {
            for (int index = 0; index < palettes.size(); index++) {
                if (Integer.parseInt(colors) != palettes.get(index)) {
                    FAILURES.add(casePath + "：colors 列写单值 " + colors
                            + "，但成员 palette 长度是 " + palettes.get(index));
                    break;
                }
            }
        }
        String declared = row.get("encoding");
        if (declared != null && declared.indexOf("..") < 0 && tierRank(declared) >= 0) {
            // 批量行写单值 = 断言"全部成员同档"。
            for (int index = 0; index < encodings.size(); index++) {
                if (!declared.equals(encodings.get(index))) {
                    FAILURES.add(casePath + "：encoding 列写单值 " + declared
                            + "，但成员档位是 " + encodings.get(index));
                    break;
                }
            }
        }
        if (declared != null && declared.indexOf("..") > 0) {
            int separator = declared.indexOf("..");
            int low = tierRank(declared.substring(0, separator));
            int high = tierRank(declared.substring(separator + 2));
            if (low >= 0 && high >= 0) {
                for (int index = 0; index < encodings.size(); index++) {
                    int actual = tierRank(encodings.get(index));
                    if (actual < low || actual > high) {
                        FAILURES.add(casePath + "：成员档位 " + encodings.get(index)
                                + " 不在 encoding 列声明的 " + declared + " 内");
                        break;
                    }
                }
            }
        }
    }

    /**
     * `CASES.tsv` 的 `encoding` 列取值（README §4）：单值、`-`，或 `char..index4` 这类区间。
     *
     * <p>区间还要求**左端不宽于右端**（顺序 char &lt; index &lt; index4）。只做正则形状匹配
     * 会把 `index4..char` 放过——而 README §4 第 7 条明写两侧校验规则必须逐字一致，
     * `tests/test_conformance.py` 早就拦它了，Java 侧漏了就会让"只跑 gradlew check"
     * 带着非法索引通过。
     */
    private static boolean validEncodingColumn(String encoding) {
        if ("-".equals(encoding) || "char".equals(encoding)
                || "index".equals(encoding) || "index4".equals(encoding)) {
            return true;
        }
        int separator = encoding.indexOf("..");
        if (separator < 0) {
            return false;
        }
        int left = tierRank(encoding.substring(0, separator));
        int right = tierRank(encoding.substring(separator + 2));
        return left >= 0 && right >= 0 && left <= right;
    }

    private static int tierRank(String name) {
        if ("char".equals(name)) {
            return 0;
        }
        if ("index".equals(name)) {
            return 1;
        }
        if ("index4".equals(name)) {
            return 2;
        }
        return -1;
    }

    /**
     * 成员的 `sample:<rel>` 三件事（§13.7:1090 / :1092）。
     *
     * <ol>
     *   <li>{@code samples/<rel>} 必须真实存在；</li>
     *   <li>该相对路径必须在 {@code samples/MANIFEST.tsv} 里登记；</li>
     *   <li>必须与该成员 {@code notes.md} 里声明的来源一致。</li>
     * </ol>
     */
    private static void checkMemberSource(String casePath, String member, String relative,
            Path memberDir, String sampleManifest) {
        String label = casePath + "/members/" + member;
        String normalized = relative.replace('\\', '/');
        Path sample = repo.resolve("samples").resolve(normalized);
        if (!Files.exists(sample)) {
            FAILURES.add(label + "：manifest 声明的 sample 不存在: samples/" + normalized);
        }
        if (sampleManifest.isEmpty()) {
            FAILURES.add(label + "：读不到 samples/MANIFEST.tsv");
        } else if (sampleManifest.indexOf(normalized + "\t") < 0) {
            FAILURES.add(label + "：sample 未在 samples/MANIFEST.tsv 登记: " + normalized);
        }
        Path notes = memberDir.resolve("notes.md");
        if (!Files.exists(notes)) {
            FAILURES.add(label + "：成员缺 notes.md");
            return;
        }
        if (readFully(notes).indexOf(normalized) < 0) {
            FAILURES.add(label + "：manifest 的 input 与 notes.md 声明的来源不一致（notes 里没有 "
                    + normalized + "）");
        }
    }

    /** {@code ## aggregate} 必须真的能判定：正例数、缺陷数、总数都要在正文里写死。 */
    private static void checkAggregate(String casePath, Path directory, int positives, int defects) {
        Path notes = directory.resolve("notes.md");
        if (!Files.exists(notes)) {
            FAILURES.add(casePath + "：批量用例缺 notes.md");
            return;
        }
        String text = readFully(notes);
        int index = text.indexOf("## aggregate");
        if (index < 0) {
            FAILURES.add(casePath + "：notes.md 缺 ## aggregate 一节");
            return;
        }
        String section = text.substring(index);
        int next = section.indexOf("\n## ", 1);
        if (next > 0) {
            section = section.substring(0, next);
        }
        // 总数必须写死在「行数 / 成员目录数」那一行（见 statesMemberCount 的注释：
        // 整段搜数字会被同一节里的散文满足）。正例/缺陷的拆分由下面的 statesSplitCount
        // 负责——它们出现在各自的行上，不能拿同一条规则套。
        int total = positives + defects;
        if (!statesMemberCount(section, total)) {
            FAILURES.add(casePath + "：## aggregate 里没有把成员数 " + total
                    + " 写死在「行数 / 成员目录数」那一行（实测 正例 " + positives
                    + " / 缺陷 " + defects + "）");
        }
        if (defects > 0) {
            // §13.7「正例成员数 + 缺陷成员数 = 写死的成员数，两者都要写死」。
            if (!statesSplitCount(section, "正例", positives)) {
                FAILURES.add(casePath + "：## aggregate 里没有把「正例成员数 " + positives + "」写死");
            }
            if (!statesSplitCount(section, "缺陷", defects)) {
                FAILURES.add(casePath + "：## aggregate 里没有把「缺陷成员数 " + defects + "」写死");
            }
        }
        if (section.contains("去重组") && !positiveDigestsEqual(directory)) {
            FAILURES.add(casePath + "：## aggregate 声明为去重组，但成员 expected.pxl 不是逐字节相同");
        }
    }
    /**
     * 数字必须以**计数的形式**出现：紧跟在 {@code =} 之后，或与"正例/缺陷/成员/总数/
     * 合计/行数/目录数/个数"这类计数词相邻（前面或后面都可以，覆盖中英文两种语序）。
     */
    /**
     * 总成员数是否写死在 `## aggregate` 里。
     *
     * <p>判据必须**按行**锚定在含「行数 / 成员目录数」的那一行、并且紧跟 `=`。
     * 整段搜数字会被同一节里的散文满足——fluid 的 aggregate 里就有一句
     * 「缺陷成员的清单…对 255 个文件逐个跑」，让"段内出现 255"恒真（审查第十二轮实测）。
     */
    private static boolean statesMemberCount(String section, int value) {
        String number = Integer.toString(value);
        java.util.regex.Pattern equal = java.util.regex.Pattern
                .compile("=\\s*\\*{0,2}" + number + "\\*{0,2}(?![0-9])");
        for (String raw : section.split("\n")) {
            String line = raw.replaceFirst("^\\s*\\d+[.)]\\s*", "");
            if (line.indexOf("行数") < 0 && line.indexOf("成员目录数") < 0) {
                continue;
            }
            if (equal.matcher(line).find()) {
                return true;
            }
        }
        return false;
    }

    /** 「正例 N / 缺陷 N」是否在同一行里写死。 */
    private static boolean statesSplitCount(String section, String label, int value) {
        java.util.regex.Pattern pattern = java.util.regex.Pattern.compile(
                label + "[^0-9\\n]{0,12}\\*{0,2}" + value + "\\*{0,2}\\s*个");
        for (String raw : section.split("\n")) {
            String line = raw.replaceFirst("^\\s*\\d+[.)]\\s*", "");
            if (line.indexOf(label) >= 0 && pattern.matcher(line).find()) {
                return true;
            }
        }
        return false;
    }

    // ---------------------------------------------------------------- 渲染判定

    /** 一条正例的全部渲染侧判定：摘要、尺寸、PNG 结构、两次字节一致。 */
    private static void checkRender(String label, String what, Renderer renderer, byte[] rgba,
            String expectedDigest) {
        String actual = PixelDigest.sha256Hex(rgba);
        if (!actual.equals(expectedDigest)) {
            FAILURES.add(label + "：" + what + " 的像素摘要不符：期望 " + expectedDigest
                    + "，实际 " + actual);
        }
        byte[] png;
        try {
            png = PngEncoder.encode(renderer.width(), renderer.height(), rgba);
        } catch (RuntimeException failure) {
            FAILURES.add(label + "：编码 PNG 失败 " + failure);
            return;
        }
        byte[] again = PngEncoder.encode(renderer.width(), renderer.height(), rgba);
        if (!Arrays.equals(png, again)) {
            FAILURES.add(label + "：两次渲染的 PNG 字节不同（§9）");
        }
        PngDecoder.Decoded decoded;
        try {
            decoded = PngDecoder.decode(png);
        } catch (RuntimeException failure) {
            FAILURES.add(label + "：产物 PNG 无法解码 " + failure.getMessage());
            return;
        }
        if (decoded.width != renderer.width() || decoded.height != renderer.height()) {
            FAILURES.add(label + "：产物尺寸与文档声明不符");
        }
        if (!Arrays.equals(decoded.rgba, rgba)) {
            FAILURES.add(label + "：产物 PNG 的像素与渲染结果不符");
        }
        if (!decoded.allFiltersZero) {
            FAILURES.add(label + "：产物 PNG 有非零过滤器字节（§10）");
        }
        // §13.2a 第 6 条：`IHDR` → `IDAT`⁺（至少一块、**可多块连续**）→ `IEND`，
        // 不出现其它类型。不能要求"恰好三个 chunk"——那会把将来合法的多块 IDAT 判失败。
        if (!wellFormedChunkSequence(decoded.chunkTypes)) {
            FAILURES.add(label + "：chunk 类型序列非法 " + decoded.chunkTypes);
        }
    }

    /** 去重组（§13.7）的判据：该组每个成员的 {@code expected.pxl} 必须逐字节相同。 */
    private static boolean positiveDigestsEqual(Path suiteDir) {
        Path membersDir = suiteDir.resolve("members");
        byte[] reference = null;
        boolean any = false;
        try {
            DirectoryStream<Path> stream = Files.newDirectoryStream(membersDir);
            try {
                for (Path member : stream) {
                    if (!Files.isDirectory(member)) {
                        continue;
                    }
                    Path expected = member.resolve("expected.pxl");
                    if (!Files.exists(expected)) {
                        continue;
                    }
                    byte[] bytes = Files.readAllBytes(expected);
                    if (reference == null) {
                        reference = bytes;
                        any = true;
                    } else if (!Arrays.equals(reference, bytes)) {
                        return false;
                    }
                }
            } finally {
                stream.close();
            }
        } catch (IOException failure) {
            return false;
        }
        return any;
    }

    /** {@code IHDR → IDAT⁺ → IEND}，中间只允许 IDAT，且至少一块。 */
    private static boolean wellFormedChunkSequence(List<String> types) {
        if (types == null || types.size() < 3) {
            return false;
        }
        if (!"IHDR".equals(types.get(0))) {
            return false;
        }
        if (!"IEND".equals(types.get(types.size() - 1))) {
            return false;
        }
        for (int index = 1; index < types.size() - 1; index++) {
            if (!"IDAT".equals(types.get(index))) {
                return false;
            }
        }
        return true;
    }

    // ---------------------------------------------------------------- 覆盖门禁

    private static void checkCoverage(Map<String, Map<String, String>> index, int negativeRender,
            int negativeExtract) {
        if (negativeRender == 0) {
            FAILURES.add("覆盖门禁：一个渲染负例都没跑（空集合不算通过）");
        }
        if (negativeExtract == 0) {
            FAILURES.add("覆盖门禁：一个抽取负例都没跑（空集合不算通过）");
        }
        Set<String> codes = new HashSet<String>();
        for (Map.Entry<String, Map<String, String>> entry : index.entrySet()) {
            if (!entry.getKey().startsWith("negative/")) {
                continue;
            }
            String covers = entry.getValue().get("covers");
            int space = covers.indexOf('(');
            if (space > 0) {
                codes.add(covers.substring(0, space));
            }
        }
        for (String code : new String[] {"PXL001", "PXL002", "PXL003", "PXL004", "PXL005", "PXL006"}) {
            if (!codes.contains(code)) {
                FAILURES.add("覆盖门禁：索引里没有任何 " + code + " 负例");
            }
        }
        // op 与 blend 两种仅渲染形态都必须有覆盖：抽取器永不产出这两类文档。
        boolean hasOp = false;
        boolean hasBlend = false;
        for (String casePath : index.keySet()) {
            hasOp |= casePath.startsWith("op-");
            hasBlend |= casePath.startsWith("blend-");
        }
        if (!hasOp) {
            FAILURES.add("覆盖门禁：没有 op- 用例（八条图元里五条只能靠它判定）");
        }
        if (!hasBlend) {
            FAILURES.add("覆盖门禁：没有 blend- 用例（三种 blend 只能靠它判定）");
        }
    }

    // ---------------------------------------------------------------- 生成型

    private static void generateInputs() {
        Path generator = repo.resolve("spec").resolve("conformance").resolve("tools")
                .resolve("gen_cases.py");
        if (!Files.exists(generator)) {
            FAILURES.add("缺少 §13.6 生成入口 spec/conformance/tools/gen_cases.py");
            return;
        }
        Result all = run(python, generator.toString(), "--all");
        if (all.exit != 0) {
            FAILURES.add("gen_cases.py --all 失败: " + firstLine(all.stderr));
            return;
        }
        Result check = run(python, generator.toString(), "--check");
        if (check.exit != 0) {
            FAILURES.add("gen_cases.py --check 失败: " + firstLine(check.stderr));
        }
    }

    // ---------------------------------------------------------------- 进程

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

    private static Result run(String... command) {
        try {
            ProcessBuilder builder = new ProcessBuilder(command);
            builder.directory(repo.toFile());
            builder.redirectErrorStream(false);
            Process process = builder.start();
            process.getOutputStream().close();
            String stdout = drain(process.getInputStream());
            String stderr = drain(process.getErrorStream());
            return new Result(process.waitFor(), stdout, stderr);
        } catch (IOException failure) {
            return new Result(-1, "", "启动进程失败: " + failure);
        } catch (InterruptedException failure) {
            Thread.currentThread().interrupt();
            return new Result(-1, "", "被中断");
        }
    }

    private static String drain(InputStream stream) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        while ((read = stream.read(buffer)) >= 0) {
            out.write(buffer, 0, read);
        }
        return new String(out.toByteArray(), UTF8);
    }

    // ---------------------------------------------------------------- 杂项

    private static String expectedError(String casePath, Path directory) {
        Path file = directory.resolve("expected.error");
        if (!Files.exists(file)) {
            FAILURES.add(casePath + "：缺 expected.error");
            return null;
        }
        String code = readFully(file).trim();
        if (!code.matches("PXL00[1-6]")) {
            FAILURES.add(casePath + "：expected.error 内容非法 " + code);
            return null;
        }
        return code;
    }

    private static byte[] readBytes(Path path) {
        try {
            return Files.readAllBytes(path);
        } catch (IOException failure) {
            FAILURES.add("读不到 " + path + ": " + failure);
            return new byte[0];
        }
    }

    private static String readFully(Path path) {
        return new String(readBytes(path), UTF8);
    }

    private static void deleteTree(Path root) {
        if (root == null || !Files.exists(root)) {
            return;
        }
        try {
            DirectoryStream<Path> stream = Files.newDirectoryStream(root);
            try {
                for (Path child : stream) {
                    if (Files.isDirectory(child)) {
                        deleteTree(child);
                    } else {
                        Files.deleteIfExists(child);
                    }
                }
            } finally {
                stream.close();
            }
            Files.deleteIfExists(root);
        } catch (IOException ignored) {
            // 清理失败不影响判定；这里不 add FAILURE，否则一次磁盘权限问题会伪造成用例失败。
        }
    }
    private static String firstLine(String text) {
        int newline = text.indexOf('\n');
        return (newline < 0 ? text : text.substring(0, newline)).trim();
    }

    private static String preview(List<String> values) {
        List<String> sorted = new ArrayList<String>(values);
        java.util.Collections.sort(sorted);
        int limit = Math.min(8, sorted.size());
        return sorted.subList(0, limit) + (sorted.size() > limit ? " …" : "");
    }

    private static void report(int indexed, int roundTrip, int renderOnly, int negativeRender,
            int negativeExtract) {
        System.out.println("conformance：索引 " + indexed + " 条");
        System.out.println("  往返正例 " + roundTrip + " / 仅渲染正例 " + renderOnly
                + " / 渲染负例 " + negativeRender + " / 抽取负例 " + negativeExtract);
        // 空集合必须**失败**，不能只打印。打印一行 FAIL 却以 0 退出，等于把
        // "什么都没执行"报成"全部通过"——这正是本类开头那条规矩要防的事。
        if (indexed == 0) {
            FAILURES.add("索引是空的：没有任何用例");
        }
        if (roundTrip + renderOnly + negativeRender + negativeExtract + suiteMembers == 0) {
            FAILURES.add("一条用例都没执行（过滤参数拼错时也会落到这里，不许当成通过）");
        }
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        System.out.println("CHECKS=" + (roundTrip + renderOnly + negativeRender + negativeExtract
                + suiteMembers) + " SUITE_MEMBERS=" + suiteMembers
                + " FAILURES=" + FAILURES.size());
        if (!FAILURES.isEmpty()) {
            System.out.println("CONFORMANCE FAILED");
            System.exit(1);
        }
        System.out.println("CONFORMANCE OK");
    }

}
