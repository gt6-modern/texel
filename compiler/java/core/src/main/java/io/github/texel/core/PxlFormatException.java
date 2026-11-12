package io.github.texel.core;

/**
 * 带稳定错误码的格式失败（规范 §11）。
 *
 * <p>码空间是规范 §11.2 的 {@code PXL001}..{@code PXL006}（{@code PXL006} 属输入位图侧，core 今天不产生），触发条件按 §11 / §11.1
 * 的阶段表执行。错误文本可以不同，码与触发条件不得不同。
 *
 * <p>这是 core 唯一的受检异常类型：任何输入文档不合法都从这里出去，调用方靠
 * {@link #code()} 分辨类别，不靠解析消息文本。
 *
 * <p><b>引用链（规范 v1 §9.3）</b>：每个错误必须携带「根入口 → … → 实际失败文件」。
 * 链在**首次被包装处定型**（{@link #withChain} 幂等）：被引用文档原样抛出的码一路向上传，
 * 由最靠近失败的加载帧补上，外层不再改动——否则链会被从中间的某一层截断。
 */
public final class PxlFormatException extends Exception {

    private static final long serialVersionUID = 1L;

    private final String code;
    /** 不含码的说明。链是**追加**上去的，所以要留住原文。 */
    private final String detail;
    /** §9.3 的引用链；还没被补过时为 {@code null}。 */
    private final String chain;

    /**
     * @param code 规范 §11.2 的码，只能是 {@code PXL001}..{@code PXL012} 之一
     * @param message 给人看的说明；不同实现可以不同
     * @throws IllegalArgumentException {@code code} 不在码空间内（编程错误，不是输入错误）
     */
    public PxlFormatException(String code, String message) {
        this(code, message, null);
    }

    private PxlFormatException(String code, String message, String chain) {
        super(chain == null
                ? code + ": " + message
                : code + ": " + message + "（引用链: " + chain + "）");
        if (!Codes.isKnown(code)) {
            // 与 importer/errors.py 同强度：非法码在构造处就炸，而不是让 code() 的
            // 承诺变成一句空话。
            throw new IllegalArgumentException("未知错误码: " + code);
        }
        this.code = code;
        this.detail = message;
        this.chain = chain;
    }

    /** 规范 §11 的错误码。 */
    public String code() {
        return code;
    }

    /** 不含码与链的说明文字。 */
    public String detail() {
        return detail;
    }

    /** §9.3 的引用链（{@code a -> b -> c}）；未补过时为 {@code null}。 */
    public String chain() {
        return chain;
    }

    public boolean hasChain() {
        return chain != null;
    }

    /**
     * 补一次引用链：**首见即定型**。已经有链就原样返回同一个实例，外层不会再覆盖。
     */
    PxlFormatException withChain(String chainText) {
        if (chain != null || chainText == null) {
            return this;
        }
        return new PxlFormatException(code, detail, chainText);
    }
}
