package io.github.texel.core;

/**
 * 规范 §11.2 的 v0 码空间：{@code PXL001}..{@code PXL006}。package-private：对外只用
 * {@link PxlFormatException#code()}。
 *
 * <p>**码域按规范定义，不按「本实现今天会产生的码」定义。** {@code PXL006} 是输入位图侧的码
 * （§8 / §11），今天只有 importer 会产出；core 仍必须把它当合法码——否则 PRD 阶段 3 把 PNG
 * asset 解码接进 core 时，一个「忘了同步判定表」的改动就会让合法码在
 * {@link PxlFormatException} 构造处抛 {@link IllegalArgumentException}（RuntimeException，
 * 而 CLI 的批处理循环只捕受检异常、main 没有顶层 catch，于是**第一个命中就整批中止并甩一次栈**，
 * 后面的文件一个都不处理）。
 */
final class Codes {

    static final String PXL001 = "PXL001";
    static final String PXL002 = "PXL002";
    static final String PXL003 = "PXL003";
    static final String PXL004 = "PXL004";
    static final String PXL005 = "PXL005";
    static final String PXL006 = "PXL006";

    // v1 新增码（规范 PXL.md §11.1）。它们的**触发条件**由 v1 定义，v0 文档永远不会产生。
    static final String PXL007 = "PXL007";
    static final String PXL008 = "PXL008";
    static final String PXL009 = "PXL009";
    static final String PXL010 = "PXL010";
    static final String PXL011 = "PXL011";
    static final String PXL012 = "PXL012";

    /**
     * **v0 码空间**（规范 PXL.md §11.2）：这是 v0 文档可能产生的全部码，也是能力档案里
     * {@code error_code_ranges} 的 version 1 范围。跨实现门禁（tests/test_packaging.py）与本表比对，
     * 因此这里**不许**混入 v1 的码。
     */
    static final String[] ALL = {PXL001, PXL002, PXL003, PXL004, PXL005, PXL006};

    /** v1 追加的码：只在 v1 文档里可能出现（PXL.md §11.1）。 */
    private static final String[] V1_EXTRA =
            {PXL007, PXL008, PXL009, PXL010, PXL011, PXL012};

    private Codes() {
    }

    /** 码域的只读快照：给测试与同一 package 的调用方用，拿不到可写引用。 */
    static String[] knownCodes() {
        return ALL.clone();
    }

    /**
     * 码域校验：只接受规范定义的码——v0 的 {@link #ALL} 加 v1 的 {@link #V1_EXTRA}。
     *
     * <p>v0 一侧与 importer/errors.py 的 ALL_CODES 同域（跨实现门禁比对的就是 {@link #ALL}）。
     */
    static boolean isKnown(String code) {
        return contains(ALL, code) || contains(V1_EXTRA, code);
    }

    private static boolean contains(String[] domain, String code) {
        for (String known : domain) {
            if (known.equals(code)) {
                return true;
            }
        }
        return false;
    }
}
