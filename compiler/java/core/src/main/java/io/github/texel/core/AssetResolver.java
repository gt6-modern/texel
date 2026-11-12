package io.github.texel.core;

/**
 * v1 的素材解析抽象（规范 §8.2）。compiler core **只**依赖它，不直接搜索文件系统。
 *
 * <p>合同：输入是**已规范化**的逻辑路径；输出是四种结果之一。
 *
 * <ul>
 *   <li>{@link Status#OK}：命中，带原始字节；</li>
 *   <li>{@link Status#MISSING}：该逻辑路径不存在 → {@code PXL009}；</li>
 *   <li>{@link Status#CASE_MISMATCH}：只有大小写折叠后才相等 → {@code PXL008}；</li>
 *   <li>{@link Status#ESCAPE}：真实路径落到 asset root 之外 → {@code PXL008}（平台 resolver 才会产生）。</li>
 * </ul>
 *
 * <p>实现不得搜索备用目录、不得读环境变量、不得联网（规范 §8.2）。
 */
public interface AssetResolver {

    /** 解析结果的状态；枚举顺序即本文件 javadoc 的列举顺序。 */
    enum Status {
        /** 命中。 */
        OK,
        /** 不存在。 */
        MISSING,
        /** 只有大小写折叠后相等。 */
        CASE_MISMATCH,
        /** 真实路径逃出 asset root。 */
        ESCAPE,
        /** 文件超过文档字节上限：**读盘之前**就判出来，避免先分配再拒绝（R04）。 */
        TOO_LARGE
    }

    /** 一次解析的结果：状态 + （仅 {@code OK} 时）原始字节。 */
    final class Result {

        private final Status status;
        private final byte[] content;
        private final boolean pngSignature;

        private Result(Status status, byte[] content, boolean pngSignature) {
            this.status = status;
            this.content = content;
            this.pngSignature = pngSignature;
        }

        /** 命中，返回内容（调用方不得修改返回数组）。 */
        public static Result ok(byte[] content) {
            if (content == null) {
                throw new NullPointerException("content");
            }
            return new Result(Status.OK, content, false);
        }

        /** 超限结果：调用方按文件头和声明类型决定报 {@code PXL002} 或 {@code PXL009}，不携带内容。 */
        public static Result tooLarge() {
            return tooLarge(false);
        }

        /** 超限结果，同时保留文件头是否为 PNG，避免调用方为分类而读完整文件。 */
        public static Result tooLarge(boolean pngSignature) {
            return new Result(Status.TOO_LARGE, null, pngSignature);
        }

        /** 只有状态的结果。 */
        public static Result of(Status status) {
            if (status == null || status == Status.OK) {
                throw new IllegalArgumentException("非-OK 结果请用 Result.of(MISSING/CASE_MISMATCH/ESCAPE)");
            }
            return new Result(status, null, false);
        }

        public Status status() {
            return status;
        }

        /** 仅 {@link Status#OK} 时非 {@code null}。 */
        public byte[] content() {
            return content;
        }

        /** 仅 {@link Status#TOO_LARGE} 时有意义：文件头是否匹配 PNG 签名。 */
        public boolean pngSignature() {
            return pngSignature;
        }
    }

    /**
     * 解析一个已规范化的逻辑路径；实现必须对同一输入始终返回同一结果。
     * 标准文件系统 resolver 的宿主权限、锁或读取失败按 §8.4 抛出
     * {@link java.io.UncheckedIOException}，由 CLI 报告，不折算为语义结果。
     */
    Result resolve(String logicalPath);

    /**
     * 按调用方声明的资源类型解析；默认实现保持旧 resolver 的兼容性。
     * 文件 resolver 用 expectedKind 在读盘前区分超大 PNG 与超大 PXL。
     */
    default Result resolve(String logicalPath, String expectedKind) {
        return resolve(logicalPath);
    }
}
