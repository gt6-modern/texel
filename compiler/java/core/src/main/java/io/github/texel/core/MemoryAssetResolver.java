package io.github.texel.core;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 内存素材解析器（规范 §8.3）：逻辑路径 → 字节，另可声明「实际拼写」。
 *
 * <p>它让路径语义**平台无关**：{@code CASE_MISMATCH} 由 {@code spellings} 显式声明，而不是靠宿主
 * 文件系统的大小写行为；conformance 的 {@code spelling.tsv} 就是这个类的输入。
 *
 * <p>{@code ESCAPE} 不由本类产生——字符串层逃逸在 {@link LogicalPath#normalize} 就已经报
 * {@code PXL008}，真实路径逃逸只可能来自平台 resolver。
 */
public final class MemoryAssetResolver implements AssetResolver {

    private final Map<String, byte[]> files;
    private final Map<String, String> mismatches;

    private MemoryAssetResolver(Map<String, byte[]> files, Map<String, String> mismatches) {
        this.files = files;
        this.mismatches = mismatches;
    }

    /** 只有文件、没有拼写声明的解析器。 */
    public static MemoryAssetResolver of(Map<String, byte[]> files) {
        return new MemoryAssetResolver(new LinkedHashMap<String, byte[]>(files),
                new HashMap<String, String>());
    }

    /** 构建器：注册文件与「只有大小写折叠后才相等」的条目。 */
    public static Builder builder() {
        return new Builder();
    }

    /** 一个用例目录装配方式：注册文件 + 注册拼写不匹配条目。 */
    public static final class Builder {

        private final Map<String, byte[]> files = new LinkedHashMap<String, byte[]>();
        private final Map<String, String> mismatches = new HashMap<String, String>();

        private Builder() {
        }

        /** 注册一个命中条目（逻辑路径 → 原始字节）。 */
        public Builder file(String logicalPath, byte[] content) {
            files.put(logicalPath, content);
            return this;
        }

        /**
         * 声明一个「查找用逻辑路径与磁盘实际拼写不同」的条目（{@code CASE_MISMATCH}）。
         *
         * @param logicalPath 文档里写的（已规范化的）逻辑路径
         * @param actualSpelling 实际存在的拼写（大小写不同即可，不要求别的差异）
         */
        public Builder spelling(String logicalPath, String actualSpelling) {
            mismatches.put(logicalPath, actualSpelling);
            return this;
        }

        public MemoryAssetResolver build() {
            return new MemoryAssetResolver(new LinkedHashMap<String, byte[]>(files),
                    new HashMap<String, String>(mismatches));
        }
    }

    @Override
    public Result resolve(String logicalPath) {
        if (logicalPath == null) {
            throw new NullPointerException("logicalPath");
        }
        byte[] content = files.get(logicalPath);
        if (content != null) {
            return Result.ok(content);
        }
        if (mismatches.containsKey(logicalPath)) {
            return Result.of(Status.CASE_MISMATCH);
        }
        // 兜底：注册表里存在一个「折叠后相等」的条目，同样算拼写不匹配。
        for (String known : files.keySet()) {
            if (known.equalsIgnoreCase(logicalPath)) {
                return Result.of(Status.CASE_MISMATCH);
            }
        }
        for (String known : mismatches.keySet()) {
            if (known.equalsIgnoreCase(logicalPath)) {
                return Result.of(Status.CASE_MISMATCH);
            }
        }
        return Result.of(Status.MISSING);
    }
}
