package io.github.texel.core;

/**
 * 已校验文档的渲染句柄。
 *
 * <p>只由 {@link PxlParser#parse(byte[])} 产生，构造器 package-private。句柄可以
 * 重复渲染：{@link #renderRgba()} 每次都从全透明累加器重新开始，因此两次调用的返回值
 * 逐字节相同（规范 §9 的渲染器确定性要求）。
 */
public final class Renderer {

    private final Doc doc;

    Renderer(Doc doc) {
        this.doc = doc;
    }

    /** 文档声明的画布宽（像素）。 */
    public int width() {
        return doc.width;
    }

    /** 文档声明的画布高（像素）。 */
    public int height() {
        return doc.height;
    }

    /**
     * 文档声明的 {@code encoding}（package-private）。
     *
     * <p>只给 conformance harness 核对 `CASES.tsv` 的 `encoding` 列用；不进公开 API。
     */
    String encoding() {
        return doc.encoding;
    }

    /** 调色板长度即 slot 数（package-private，同上）。 */
    int paletteSize() {
        return doc.palette.length;
    }

    /**
     * 渲染成 RGBA8 原始字节流：逐行、行内从左到右、自上而下，每像素 4 字节 R,G,B,A，
     * 无行间填充、无对齐、无头尾附加字节（规范 §13.4）。
     *
     * @return 长度恒为 {@code 4 × width × height} 的新数组；调用方拥有它
     */
    public byte[] renderRgba() {
        return Raster.render(doc);
    }
}
