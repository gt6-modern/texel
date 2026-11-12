package io.github.texel.core;

import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 按 {@code spec/conformance/profiles.tsv} 选出某个能力档案要跑的语料子集（PRD §6.3）。
 *
 * <p>集合代数固定为「include_all 与 include 的并集 − 精确 exclude」。任何映射错误（未知 suite、
 * 引用不存在的 case、动作冲突、排除未选中的 case）都抛 {@link IllegalStateException} —— runner
 * 必须失败，不得静默跳过。
 */
final class ProfileSelection {

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private ProfileSelection() {
    }

    /**
     * 选出一个 suite 在该档案下的 case 集合。
     *
     * @param profilesFile profiles.tsv 的路径
     * @param profile      能力档案名
     * @param suite        suite 名（{@code base} / {@code components}）
     * @param universe     该 suite 的**全部** case 名；用于 include_all 与存在性校验
     * @return 选中的 case 集合；该档案没有选中这个 suite 时返回 {@code null}
     * @throws IOException 读不到文件
     * @throws IllegalStateException 映射本身有问题
     */
    static Set<String> select(Path profilesFile, String profile, String suite,
            Collection<String> universe) throws IOException {
        if (!Files.isRegularFile(profilesFile)) {
            throw new IllegalStateException("找不到 " + profilesFile);
        }
        List<String> lines = Files.readAllLines(profilesFile, UTF8);
        boolean includeAll = false;
        boolean touched = false;
        Set<String> includes = new LinkedHashSet<String>();
        Set<String> excludes = new LinkedHashSet<String>();
        int number = 0;
        for (String line : lines) {
            number++;
            if (line.trim().isEmpty() || line.trim().startsWith("#")) {
                continue;
            }
            String[] columns = line.split("\t", -1);
            if (columns.length != 4) {
                throw new IllegalStateException(profilesFile.getFileName() + " 第 " + number
                        + " 行不是四列: " + line);
            }
            if (!columns[0].equals(profile) || !columns[1].equals(suite)) {
                continue;
            }
            touched = true;
            String action = columns[2];
            String value = columns[3];
            if ("include_all".equals(action)) {
                if (!"-".equals(value)) {
                    throw new IllegalStateException("include_all 的 case 必须是 -: " + line);
                }
                includeAll = true;
            } else if ("include".equals(action)) {
                includes.add(value);
            } else if ("exclude".equals(action)) {
                excludes.add(value);
            } else {
                throw new IllegalStateException("未知 action: " + line);
            }
        }
        if (!touched) {
            return null;
        }
        // 存在性校验：引用了 universe 里没有的 case 说明映射过期。
        List<String> known = new ArrayList<String>(universe);
        for (String name : includes) {
            if (!known.contains(name)) {
                throw new IllegalStateException("profile " + profile + " include 了 " + suite
                        + " 里不存在的 case: " + name);
            }
        }
        Set<String> selected = new LinkedHashSet<String>();
        if (includeAll) {
            selected.addAll(known);
        }
        selected.addAll(includes);
        Set<String> conflicting = new LinkedHashSet<String>(includes);
        conflicting.retainAll(excludes);
        if (!conflicting.isEmpty()) {
            throw new IllegalStateException("动作冲突（同时 include 与 exclude）: " + conflicting);
        }
        for (String name : excludes) {
            if (!selected.contains(name)) {
                throw new IllegalStateException("排除了一个本来就没被选中的 case: " + name);
            }
        }
        selected.removeAll(excludes);
        return selected;
    }
}
