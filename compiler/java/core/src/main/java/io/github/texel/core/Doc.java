package io.github.texel.core;

import java.util.Map;

/**
 * 校验通过后的文档模型。package-private：对外只经 {@link Renderer} 使用。
 *
 * <p>所有值都已按规范归一：坐标是 {@code int}（都在 §5.0.1 的闭区间内，int 足够），
 * 调色板是解码后的 RGBA 字节，{@code grid} 行仍是原始字符串（渲染时才按档位切 token）。
 *
 * <p>为什么行不预先切成 {@code int[]}：{@code index4} 档下一份合法文档可以有
 * 1.6e7 个 token，预切会把这些全变成堆上的 int，纯浪费；渲染时按固定宽度逐段解码
 * 更省也更快。
 */
final class Doc {

    final int width;
    final int height;
    final String encoding;
    /** {@code palette[slot-1]} 是该 slot 的 RGBA 四字节。slot 0 不在此数组中。 */
    final byte[][] palette;
    /** sprite 名 → token 行。sprite 自身不参与合成，只被 {@code stamp} 读取。 */
    final Map<String, String[]> sprites;
    /** v1: asset 名 -> 声明(kind + 规范化后的逻辑路径); v0 文档为空表(不是 null)。 */
    final Map<String, Asset> assets;
    final Layer[] layers;

    Doc(int width, int height, String encoding, byte[][] palette,
            Map<String, String[]> sprites, Map<String, Asset> assets, Layer[] layers) {
        this.width = width;
        this.height = height;
        this.encoding = encoding;
        this.palette = palette;
        this.sprites = sprites;
        this.assets = assets;
        this.layers = layers;
    }

    /** 一条 v1 asset 声明(规范 §5.1): kind 是 pxl / png, path 已规范化。 */
    static final class Asset {

        final String kind;
        final String path;
        /** 子文档参数映射（v1 §5.2）；没有该成员时为 null。 */
        final Json.Value params;

        Asset(String kind, String path, Json.Value params) {
            this.kind = kind;
            this.path = path;
            this.params = params;
        }
    }

    /**
     * 一个图层：混合方式、依序执行的 op。
     *
     * <p>名字只在**校验期**用来定位错误（见 {@code Skeleton.RawLayer}），渲染模型不带它：
     * 渲染不需要，带着就只是一份没人读的副本。
     */
    static final class Layer {

        final String blend;
        final Op[] ops;

        Layer(String blend, Op[] ops) {
            this.blend = blend;
            this.ops = ops;
        }
    }

    /**
     * 一条 op。字段按图元取用，未用到的保持 0 / {@code null}。
     *
     * <p>校验器保证 {@code kind} 恒为八条之一；未知 op 在阶段 8 就被 {@code PXL004}
     * 挡掉了，不会进到这里。
     */
    static final class Op {

        final String kind;

        int x;
        int y;
        int x0;
        int y0;
        int x1;
        int y1;
        int w;
        int h;
        int slot;
        /** {@code mirror} 的源矩形。 */
        int fromX;
        int fromY;
        int fromW;
        int fromH;
        boolean flipX;
        boolean flipY;
        /** {@code stamp} 引用的 sprite 名。 */
        String sprite;
        /** {@code stamp} 引用的 asset 名(v1,与 sprite 恰好一个非 null)。 */
        String asset;
        /** 解析期填入的被引用文档位图与宽高(只有 asset stamp 才有)。 */
        byte[] assetRgba;
        int assetWidth;
        int assetHeight;
        /** {@code grid} 的 token 行。 */
        String[] rows;

        Op(String kind) {
            this.kind = kind;
        }
    }
}
