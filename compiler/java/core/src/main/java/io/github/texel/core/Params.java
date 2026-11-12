package io.github.texel.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * v1 的参数与 bindings（规范 PXL.md §6）。
 *
 * <p>三个阶段固定：
 * <ol>
 *   <li>{@link #parse} 读 {@code params} 声明（结构错 {@code PXL001}、默认值类型错 {@code PXL012}、
 *       声明数超限 {@code PXL002}）；</li>
 *   <li>{@link #bind} 校验外部 bindings 并补默认值（缺失/未声明/类型不符 {@code PXL012}）；</li>
 *   <li>{@link #substitute} 按**白名单位置**把参引替换成强类型字面量，并去掉 {@code params} 成员；
 *       任何白名单之外的参引都是 {@code PXL012}。</li>
 * </ol>
 *
 * <p>替换完成后，后面的一切校验都在"不含参引的具体文档"上按原阶段进行——这正是
 * 「替换后的字段错误按目标字段原码报」的实现方式（例如 slot 由参数替换后越界报 {@code PXL005}，不是 {@code PXL012}）。
 */
final class Params {

    /** 每文档参数声明数上限（规范 §10.1）。 */
    static final int MAX_DECLARATIONS = 256;
    /** 每文档 assets 声明数上限（v1 §10.1）。 */
    static final int MAX_ASSET_DECLARATIONS = 4096;

    private static final String[] TYPES = {"color", "integer", "boolean", "asset", "sprite"};

    /** 一条参数声明。 */
    static final class Decl {

        final String name;
        final String type;
        final Json.Value fallback;

        Decl(String name, String type, Json.Value fallback) {
            this.name = name;
            this.type = type;
            this.fallback = fallback;
        }
    }

    private Params() {
    }

    /** 解析 {@code params} 声明；没有该成员时返回空表。 */
    static Map<String, Decl> parse(Json.Value root) throws PxlFormatException {
        Map<String, Decl> declarations = new LinkedHashMap<String, Decl>();
        Json.Value params = root.members.get("params");
        if (params == null) {
            return declarations;
        }
        if (!params.isObject()) {
            throw fail(Codes.PXL001, "\"params\" 必须是对象");
        }
        // 声明数上限属于 v1 §10.1 的**文档级数值域**（阶段表第 7 行），不能在这里判：
        // 否则与 encoding（第 5 行 PXL003）并存时会抢先报 PXL002（发布态复核 LP1/LA1）。
        for (Map.Entry<String, Json.Value> entry : params.members.entrySet()) {
            String name = entry.getKey();
            if (!isName(name)) {
                throw fail(Codes.PXL001, "参数名不匹配 [a-z][a-z0-9_]{0,63}: \"" + name + "\"");
            }
            Json.Value value = entry.getValue();
            if (!value.isObject()) {
                throw fail(Codes.PXL001, "参数 \"" + name + "\" 的声明必须是对象");
            }
            for (String member : value.members.keySet()) {
                if (!"type".equals(member) && !"default".equals(member)) {
                    throw fail(Codes.PXL001, "参数 \"" + name + "\" 出现未定义字段: \"" + member
                            + "\"");
                }
            }
            Json.Value type = value.members.get("type");
            if (type == null) {
                throw fail(Codes.PXL001, "参数 \"" + name + "\" 缺少 \"type\"");
            }
            if (!type.isString()) {
                throw fail(Codes.PXL001, "参数 \"" + name + "\" 的 \"type\" 必须是字符串");
            }
            if (!isType(type.text)) {
                throw fail(Codes.PXL001, "参数 \"" + name + "\" 的 type 取值非法: " + type.text);
            }
            Json.Value fallback = value.members.get("default");
            if (fallback != null) {
                checkKind(name, type.text, fallback);
            }
            declarations.put(name, new Decl(name, type.text, fallback));
        }
        return declarations;
    }

    /**
     * 校验 bindings 并补默认值，返回**规范 bindings**（按参数名 ASCII 升序的强类型映射）。
     *
     * @param bindings 调用方给的绑定（字符串形式，来自 CLI 或子文档映射）
     */
    static Map<String, Json.Value> bind(Map<String, Decl> declarations, Map<String, String> bindings)
            throws PxlFormatException {
        for (String name : bindings.keySet()) {
            if (!declarations.containsKey(name)) {
                throw fail(Codes.PXL012, "绑定了未声明的参数: \"" + name + "\"");
            }
        }
        List<String> names = new ArrayList<String>(declarations.keySet());
        java.util.Collections.sort(names);
        Map<String, Json.Value> canonical = new LinkedHashMap<String, Json.Value>();
        for (String name : names) {
            Decl declaration = declarations.get(name);
            String given = bindings.get(name);
            if (given == null) {
                if (declaration.fallback == null) {
                    throw fail(Codes.PXL012, "缺少必需参数: \"" + name + "\"");
                }
                canonical.put(name, declaration.fallback);
                continue;
            }
            canonical.put(name, literal(declaration.name, declaration.type, given));
        }
        return canonical;
    }

    /**
     * 按强类型绑定：值来自文档里的子文档 params（JSON 字面量），不是 CLI 文本。
     *
     * <p>规范 §6.3：值类型必须与子文档同名参数的类型一致。此前把它和 CLI 绑定一起
     * 先转成字符串，于是数字被当成十六进制颜色、字符串被当成整数，两侧都放行了类型不符的值。
     */
    static Map<String, Json.Value> bindTyped(Map<String, Decl> declarations,
            Map<String, Json.Value> given) throws PxlFormatException {
        return bindTyped(declarations, given, null);
    }

    /** 同上，但带上**来源参数的声明类型**（父文档转发进来的参引）。 */
    static Map<String, Json.Value> bindTyped(Map<String, Decl> declarations,
            Map<String, Json.Value> given, Map<String, String> sourceKinds)
            throws PxlFormatException {
        for (String name : given.keySet()) {
            if (!declarations.containsKey(name)) {
                throw fail(Codes.PXL012, "绑定了未声明的参数");
            }
        }
        List<String> names = new ArrayList<String>(declarations.keySet());
        java.util.Collections.sort(names);
        Map<String, Json.Value> canonical = new LinkedHashMap<String, Json.Value>();
        for (String name : names) {
            Decl declaration = declarations.get(name);
            Json.Value value = given.get(name);
            if (value == null) {
                if (declaration.fallback == null) {
                    throw fail(Codes.PXL012, "缺少必需参数");
                }
                canonical.put(name, declaration.fallback);
                continue;
            }
            checkKind(name, declaration.type, value);
            String origin = sourceKinds == null ? null : sourceKinds.get(name);
            if (origin != null && !origin.equals(declaration.type)) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的来源类型 " + origin
                        + " 与声明的 " + declaration.type + " 不符");
            }
            canonical.put(name, value);
        }
        return canonical;
    }
    /**
     * 参数值的**身份文本**：整数的身份是数学值，所以 -0 必须与 0 相同（§6.4）。
     * 非数值原样返回（字符串/布尔）。
     */
    static String identity(Json.Value value) {
        if (!value.isNumber()) {
            return value.text;
        }
        String text = value.text == null ? "" : value.text;
        boolean negative = text.startsWith("-");
        String digits = negative ? text.substring(1) : text;
        boolean allZero = digits.length() > 0;
        for (int index = 0; index < digits.length(); index++) {
            if (digits.charAt(index) != '0') {
                allZero = false;
                break;
            }
        }
        return negative && allZero ? "0" : text;
    }
    /** 把外部字符串按声明类型变成强类型字面量；不符合规范形式即 {@code PXL012}。 */
    /**
     * asset / sprite 的 default 必须是本文档已声明的名字：默认值是声明的一部分，
     * 只在用到处才发现（PXL005）是错的（发布态复核 D01–D04）。
     */

    private static final java.util.List<String> KNOWN_OPS = java.util.Arrays.asList(
            "fill", "rect", "pixel", "line", "grid", "mirror", "erase", "stamp");
    private static final java.util.List<String> NUMERIC_MEMBERS = java.util.Arrays.asList(
            "x", "y", "x0", "y0", "x1", "y1", "w", "h", "slot");

    // §11.2 行 3：参数阶段先于结构/op 阶段——非白名单位置的参引先报 PXL012。
    static void scanReferences(Json.Value node) throws PxlFormatException {
        scan(node, "", null);
    }

    private static void scan(Json.Value node, String where, String opKind)
            throws PxlFormatException {
        if (node == null) {
            return;
        }
        if (node.isString()) {
            // 数组里的字符串也必须有分支处理，否则 layers=["$n"] 会落到结构阶段（W04/W05/W06）。
            boolean candidate = node.text != null && node.text.startsWith("$");
            // N10–N12：以 $ 开头的候选即使名字不合法也算参引，白名单位置上必须报 PXL012。
            if (candidate && !isReference(node.text)) {
                throw fail(Codes.PXL012, "参引不合法: " + node.text);
            }
            if (candidate && !referenceAllowed(where, opKind)) {
                throw fail(Codes.PXL012, "参引 " + node.text
                        + " 出现在不允许的位置: " + where);
            }
            return;
        }
        if (node.isObject()) {
            // 只有真正的 layers[i].ops[j] 对象才提供 op selector（W07 的 from.op 不能覆盖它）。
            String kind = opKind;
            if (isOpObject(where)) {
                Json.Value op = node.members.get("op");
                if (op != null && op.isString()) {
                    kind = op.text;
                }
            }
            for (Map.Entry<String, Json.Value> entry : node.members.entrySet()) {
                // 对象键里的 / [ ] 要转义：否则键能拼出「假段」冒充数组下标（W13/W14）。
                String key = entry.getKey().replace("/", "%2F").replace("[", "%5B")
                        .replace("]", "%5D");
                // 一律以 / 开头拼：根自己也是空段，空键("")才能保留成独立一层
                // （发布态复核 W37/W38：丢掉空根键会把白名单外的位置当成合法）。
                String next = where + "/" + key;
                scan(entry.getValue(), next, kind);
            }
            return;
        }
        if (node.isArray()) {
            int index = 0;
            for (Json.Value item : node.items) {
                // 数组下标用 [i] 编码：对象键与下标不能混为一谈（W11）。
                scan(item, where + "/[" + index + "]", opKind);
                index++;
            }
        }
    }

    /** v0 §5 各 op 的整数成员。 */
    private static java.util.List<String> allowedIntegerMembers(String op) {
        if ("fill".equals(op)) {
            return java.util.Arrays.asList("slot");
        }
        if ("rect".equals(op)) {
            return java.util.Arrays.asList("x", "y", "w", "h", "slot");
        }
        if ("pixel".equals(op)) {
            return java.util.Arrays.asList("x", "y", "slot");
        }
        if ("line".equals(op)) {
            return java.util.Arrays.asList("x0", "y0", "x1", "y1", "slot");
        }
        if ("grid".equals(op)) {
            return java.util.Arrays.asList("x", "y");
        }
        if ("mirror".equals(op)) {
            return java.util.Arrays.asList("flipX", "flipY");
        }
        if ("erase".equals(op)) {
            return java.util.Arrays.asList("x", "y", "w", "h");
        }
        if ("stamp".equals(op)) {
            return java.util.Arrays.asList("x", "y", "flipX", "flipY");
        }
        return java.util.Collections.emptyList();
    }

    /** W13/W14：只有 [数字] 才是数组下标；对象键 [0] 之类的字符串不能冒充。 */
    /** mirror 的嵌套坐标：from = x,y,w,h；to = x,y（§6.2）。 */
    private static java.util.List<String> nestedMembers(String container) {
        if ("from".equals(container)) {
            return java.util.Arrays.asList("x", "y", "w", "h");
        }
        return java.util.Arrays.asList("x", "y");
    }
    private static boolean isIndex(String part) {
        if (part.length() < 3 || part.charAt(0) != '[' || !part.endsWith("]")) {
            return false;
        }
        for (int index = 1; index < part.length() - 1; index++) {
            if (part.charAt(index) < '0' || part.charAt(index) > '9') {
                return false;
            }
        }
        return true;
    }

    private static boolean isOpObject(String where) {
        String[] raw = where.split("/", -1);
        String[] parts = raw.length <= 1 ? new String[0]
                : java.util.Arrays.copyOfRange(raw, 1, raw.length);
        return parts.length == 4 && "layers".equals(parts[0]) && "ops".equals(parts[2])
                && isIndex(parts[1]) && isIndex(parts[3]);
    }

    private static boolean referenceAllowed(String path, String opKind) {
        String[] raw = path.split("/", -1);
        // 首段恒为空（根标记），丢掉它之后才是真正的路径分量。
        String[] parts = raw.length <= 1 ? new String[0]
                : java.util.Arrays.copyOfRange(raw, 1, raw.length);
        if (parts.length < 2) {
            return false;
        }
        String member = parts[parts.length - 1];
        String container = parts[parts.length - 2];
        if ("palette".equals(parts[0]) && parts.length == 2 && isIndex(parts[1])) {
            return true;
        }
        if (parts.length == 4 && "assets".equals(parts[0]) && "params".equals(parts[2])
                // <name> 必须是对象键（W41：数组下标不能冒充 asset 名），<p> 同理。
                && !isIndex(parts[1]) && !isIndex(parts[3])) {
            return true;
        }
        if (parts.length >= 4 && "layers".equals(parts[0]) && "ops".equals(parts[2])
                && isIndex(parts[1]) && isIndex(parts[3])) {
            if (opKind == null || !KNOWN_OPS.contains(opKind)) {
                return false;
            }
            // 只允许该 op 原有的整数成员（v0 §5 成员表；W12：fill 没有 x）。
            if (parts.length == 5 && allowedIntegerMembers(opKind).contains(member)) {
                return true;
            }
            // mirror 的 from/to 内的坐标（§6.2 已写明）；其它 op 没有这两个对象（W08）。
            if ("mirror".equals(opKind) && parts.length == 6
                    && ("from".equals(container) || "to".equals(container))
                    && nestedMembers(container).contains(member)) {
                return true;
            }
            if ("stamp".equals(opKind) && parts.length == 5
                    && ("flipX".equals(member) || "flipY".equals(member)
                            || "asset".equals(member) || "sprite".equals(member))) {
                return true;
            }
        }
        return false;
    }


    static void validateSymbolDefaults(Map<String, Decl> declarations,
            Json.Value assets, Json.Value sprites) throws PxlFormatException {
        for (Map.Entry<String, Decl> entry : declarations.entrySet()) {
            Decl decl = entry.getValue();
            if (decl.fallback == null || decl.fallback.text == null) {
                continue;
            }
            if ("asset".equals(decl.type)
                    && (assets == null || !assets.isObject()
                            || !assets.members.containsKey(decl.fallback.text))) {
                throw fail(Codes.PXL012, "参数 \"" + entry.getKey()
                        + "\" 的 default 引用了未声明的 asset: " + decl.fallback.text);
            }
            if ("sprite".equals(decl.type)
                    && (sprites == null || !sprites.isObject()
                            || !sprites.members.containsKey(decl.fallback.text))) {
                throw fail(Codes.PXL012, "参数 \"" + entry.getKey()
                        + "\" 的 default 引用了未声明的 sprite: " + decl.fallback.text);
            }
        }
    }

    /** 绑定得到的 asset/sprite 值也必须已声明：只查 default 会让未使用的非法绑定溜过去
     *  （发布态复核 B01–B04/B08）。 */
    static void validateSymbolValues(Map<String, Json.Value> bound,
            Map<String, Decl> declarations, Json.Value assets, Json.Value sprites)
            throws PxlFormatException {
        for (Map.Entry<String, Json.Value> entry : bound.entrySet()) {
            Decl decl = declarations.get(entry.getKey());
            if (decl == null || entry.getValue() == null || entry.getValue().text == null) {
                continue;
            }
            String value = entry.getValue().text;
            if ("asset".equals(decl.type)
                    && (assets == null || !assets.isObject()
                            || !assets.members.containsKey(value))) {
                throw fail(Codes.PXL012, "参数 \"" + entry.getKey()
                        + "\" 的绑定引用了未声明的 asset: " + value);
            }
            if ("sprite".equals(decl.type)
                    && (sprites == null || !sprites.isObject()
                            || !sprites.members.containsKey(value))) {
                throw fail(Codes.PXL012, "参数 \"" + entry.getKey()
                        + "\" 的绑定引用了未声明的 sprite: " + value);
            }
        }
    }
    /** 文档级声明数上限：在结构/编码检查**之后**调用（阶段表第 7 行）。 */
    static void validateDeclarationCounts(Json.Value root) throws PxlFormatException {
        Json.Value params = root.members.get("params");
        if (params != null && params.isObject()
                && params.members.size() > MAX_DECLARATIONS) {
            throw fail(Codes.PXL002, "params 声明数 " + params.members.size()
                    + " 超过上限 " + MAX_DECLARATIONS);
        }
        Json.Value assets = root.members.get("assets");
        if (assets != null && assets.isObject()
                && assets.members.size() > MAX_ASSET_DECLARATIONS) {
            throw fail(Codes.PXL002, "assets 声明数 " + assets.members.size()
                    + " 超过上限 " + MAX_ASSET_DECLARATIONS);
        }
    }
    static Json.Value literal(String name, String type, String text) throws PxlFormatException {
        if ("color".equals(type)) {
            if (!text.matches("[0-9A-F]{8}")) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的颜色必须是八位大写 RGBA 十六进制: "
                        + text);
            }
            return Json.Value.string(text);
        }
        if ("integer".equals(type)) {
            // 位数不在这里限：规范整数字面量的范围由**使用位置**判（坐标越界是 PXL002，§6.5）。
            if (!text.matches("-?(0|[1-9][0-9]*)")) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的整数形式非法: " + text);
            }
            return Json.Value.number(text);
        }
        if ("boolean".equals(type)) {
            if (!"true".equals(text) && !"false".equals(text)) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的布尔值只能是 true/false: " + text);
            }
            return Json.Value.bool("true".equals(text));
        }
        // asset / sprite 的值是**符号名**，它的合法性由「已声明」判定（下面 substitute /
        // validateSymbolDefaults 负责）。这里不能套参数名规则：v0 的 sprite 名允许 '-',
        // 也可以到 65 字符，套用会把合法输入拒掉（发布态复核 B09–B11）。
        return Json.Value.string(text);
    }

    /**
     * 按白名单位置替换参引，并去掉 {@code params} 成员，返回**具体文档**。
     *
     * <p>白名单（规范 §6.2）：palette 元素（color）、已知 op 的整数成员（integer）、
     * stamp 的 flipX/flipY（boolean）、stamp 的 asset/sprite（asset/sprite 名）。
     * 其它任何位置出现以 {@code $} 开头的字符串都是 {@code PXL012}。
     */
    static Json.Value substitute(Json.Value root, Map<String, Json.Value> values,
                                Map<String, Decl> declarations)
            throws PxlFormatException {
        return substitute(root, values, declarations, null);
    }

    /** 同上，并把「来源参数的声明类型」记进 {@code forwarded}（asset 名 -> 参数名 -> 类型）。 */
    static Json.Value substitute(Json.Value root, Map<String, Json.Value> values,
                                Map<String, Decl> declarations,
                                Map<String, Map<String, String>> forwarded)
            throws PxlFormatException {
        Map<String, Json.Value> members = new LinkedHashMap<String, Json.Value>();
        for (Map.Entry<String, Json.Value> entry : root.members.entrySet()) {
            String name = entry.getKey();
            if ("params".equals(name)) {
                continue; // 参数声明本身不进具体文档
            }
            members.put(name, entry.getValue());
        }
        Map<String, Json.Value> out = new LinkedHashMap<String, Json.Value>();
        for (Map.Entry<String, Json.Value> entry : members.entrySet()) {
            String name = entry.getKey();
            Json.Value value = entry.getValue();
            if ("palette".equals(name) && value.isArray()) {
                List<Json.Value> items = new ArrayList<Json.Value>();
                for (Json.Value item : value.items) {
                    items.add(item.isString() && isReference(item.text)
                            ? require(values, declarations, item.text, "color") : item);
                }
                out.put(name, Json.Value.array(items));
            } else if ("assets".equals(name) && value.isObject()) {
                out.put(name, substituteAssets(value, values, declarations, forwarded));
            } else if ("layers".equals(name) && value.isArray()) {
                out.put(name, substituteLayers(value, values, declarations));
            } else {
                rejectReference(value, name);
                out.put(name, value);
            }
        }
        return Json.Value.object(out);
    }

    /**
     * 替换 asset 声明里的**父参引**（规范 v1 §5.2：params 的值可以是父文档的参引）。
     *
     * <p>必须在父阶段替换掉：子文档拿到 "$c" 这种字符串会当成字面绑定值，于是要么类型不符
     * （PXL012）、要么把 $c 当成素材名——合法的参数转发因此**无法表达**。
     * 替换时直接放入**已定型的 JSON 值**，强类型因此得以保留。
     */
    private static Json.Value substituteAssets(Json.Value assets, Map<String, Json.Value> values,
                                                 Map<String, Decl> declarations,
                                                 Map<String, Map<String, String>> forwarded)
            throws PxlFormatException {
        Map<String, Json.Value> out = new LinkedHashMap<String, Json.Value>();
        for (Map.Entry<String, Json.Value> entry : assets.members.entrySet()) {
            Json.Value decl = entry.getValue();
            if (!decl.isObject()) {
                out.put(entry.getKey(), decl);
                continue;
            }
            Map<String, Json.Value> members = new LinkedHashMap<String, Json.Value>();
            for (Map.Entry<String, Json.Value> item : decl.members.entrySet()) {
                if ("params".equals(item.getKey()) && item.getValue().isObject()) {
                    Map<String, Json.Value> mapped = new LinkedHashMap<String, Json.Value>();
                    for (Map.Entry<String, Json.Value> bound : item.getValue().members.entrySet()) {
                        Json.Value boundValue = bound.getValue();
                        if (boundValue.isString() && isReference(boundValue.text)) {
                            String name = boundValue.text.substring(1);
                            Json.Value resolved = values.get(name);
                            if (resolved == null) {
                                throw fail(Codes.PXL012, "asset \"" + entry.getKey()
                                        + "\" 的参数 \"" + bound.getKey()
                                        + "\" 引用了未声明的参数: \"$" + name + "\"");
                            }
                            Decl source = declarations == null ? null : declarations.get(name);
                            if (source != null && forwarded != null) {
                                Map<String, String> perAsset = forwarded.get(entry.getKey());
                                if (perAsset == null) {
                                    perAsset = new LinkedHashMap<String, String>();
                                    forwarded.put(entry.getKey(), perAsset);
                                }
                                perAsset.put(bound.getKey(), source.type);
                            }
                            mapped.put(bound.getKey(), resolved);
                        } else {
                            mapped.put(bound.getKey(), boundValue);
                        }
                    }
                    members.put("params", Json.Value.object(mapped));
                } else {
                    rejectReference(item.getValue(), item.getKey());
                    members.put(item.getKey(), item.getValue());
                }
            }
            out.put(entry.getKey(), Json.Value.object(members));
        }
        return Json.Value.object(out);
    }

    private static Json.Value substituteLayers(Json.Value layers, Map<String, Json.Value> values,
                                                 Map<String, Decl> declarations)
            throws PxlFormatException {
        List<Json.Value> outLayers = new ArrayList<Json.Value>();
        for (Json.Value layer : layers.items) {
            if (!layer.isObject()) {
                outLayers.add(layer);
                continue;
            }
            Map<String, Json.Value> layerOut = new LinkedHashMap<String, Json.Value>();
            for (Map.Entry<String, Json.Value> item : layer.members.entrySet()) {
                if ("ops".equals(item.getKey()) && item.getValue().isArray()) {
                    List<Json.Value> ops = new ArrayList<Json.Value>();
                    for (Json.Value op : item.getValue().items) {
                        ops.add(substituteOp(op, values, declarations));
                    }
                    layerOut.put("ops", Json.Value.array(ops));
                } else {
                    rejectReference(item.getValue(), item.getKey());
                    layerOut.put(item.getKey(), item.getValue());
                }
            }
            outLayers.add(Json.Value.object(layerOut));
        }
        return Json.Value.array(outLayers);
    }

    private static Json.Value substituteOp(Json.Value op, Map<String, Json.Value> values,
                                          Map<String, Decl> declarations)
            throws PxlFormatException {
        if (!op.isObject()) {
            return op;
        }
        Json.Value kindValue = op.members.get("op");
        String kind = kindValue != null && kindValue.isString() ? kindValue.text : "";
        String[] integers = integerMembers(kind);
        String[] booleans = "stamp".equals(kind) ? new String[] {"flipX", "flipY"} : new String[0];
        String[] names = "stamp".equals(kind) ? new String[] {"asset", "sprite"} : new String[0];
        Map<String, Json.Value> out = new LinkedHashMap<String, Json.Value>();
        for (Map.Entry<String, Json.Value> entry : op.members.entrySet()) {
            String name = entry.getKey();
            Json.Value value = entry.getValue();
            if (contains(integers, name) && value.isString() && isReference(value.text)) {
                out.put(name, require(values, declarations, value.text, "integer"));
            } else if (contains(booleans, name) && value.isString() && isReference(value.text)) {
                out.put(name, require(values, declarations, value.text, "boolean"));
            } else if (contains(names, name) && value.isString() && isReference(value.text)) {
                Json.Value bound = values.get(value.text.substring(1));
                if (bound == null || !bound.isString()) {
                    throw fail(Codes.PXL012, "stamp 的 \"" + name + "\" 绑定的参数类型不符: "
                            + value.text);
                }
                // asset 与 sprite 都是字符串，必须靠声明类型区分：sprite 参数不能进
                // asset 位置，反之亦然（发布态复核 P2）。
                Decl slot = declarations == null ? null : declarations.get(value.text.substring(1));
                String slotKind = slot == null ? null : slot.type;
                if (slotKind != null && !slotKind.equals(name)) {
                    throw fail(Codes.PXL012, "参数 \"" + value.text.substring(1)
                            + "\" 声明为 " + slotKind + "，不能出现在需要 " + name + " 的位置");
                }
                out.put(name, bound);
            } else if ("from".equals(name) || "to".equals(name)) {
                out.put(name, substitutePoint(value, values, declarations));
            } else {
                rejectReference(value, name);
                out.put(name, value);
            }
        }
        return Json.Value.object(out);
    }

    private static Json.Value substitutePoint(Json.Value point, Map<String, Json.Value> values,
                                            Map<String, Decl> declarations)
            throws PxlFormatException {
        if (!point.isObject()) {
            return point;
        }
        Map<String, Json.Value> out = new LinkedHashMap<String, Json.Value>();
        for (Map.Entry<String, Json.Value> entry : point.members.entrySet()) {
            String name = entry.getKey();
            Json.Value value = entry.getValue();
            if (contains(new String[] {"x", "y", "w", "h"}, name) && value.isString()
                    && isReference(value.text)) {
                out.put(name, require(values, declarations, value.text, "integer"));
            } else {
                rejectReference(value, name);
                out.put(name, value);
            }
        }
        return Json.Value.object(out);
    }

    private static String[] integerMembers(String kind) {
        if ("fill".equals(kind)) {
            return new String[] {"slot"};
        }
        if ("rect".equals(kind)) {
            return new String[] {"x", "y", "w", "h", "slot"};
        }
        if ("pixel".equals(kind) || "line".equals(kind)) {
            return new String[] {"x", "y", "x0", "y0", "x1", "y1", "slot"};
        }
        if ("grid".equals(kind) || "erase".equals(kind)) {
            return new String[] {"x", "y", "w", "h"};
        }
        if ("stamp".equals(kind)) {
            return new String[] {"x", "y"};
        }
        return new String[0];
    }

    private static Json.Value require(Map<String, Json.Value> values,
                                      Map<String, Decl> declarations,
                                      String reference, String type)
            throws PxlFormatException {
        String name = reference.substring(1);
        Json.Value bound = values.get(name);
        if (bound == null) {
            throw fail(Codes.PXL012, "引用了未声明的参数: \"" + name + "\"");
        }
        // 声明类型也是判据：sprite 与 asset 都是字符串，只看 JSON 形状分不开
        // （发布态复核 P2：sprite 参数进 stamp.asset 时 Java 成功、Python 报 PXL012）。
        Decl declaration = declarations == null ? null : declarations.get(name);
        String declaredKind = declaration == null ? null : declaration.type;
        if (declaredKind != null && !declaredKind.equals(type)) {
            throw fail(Codes.PXL012, "参数 \"" + name + "\" 声明为 " + declaredKind
                    + "，不能出现在需要 " + type + " 的位置");
        }
        boolean ok = "boolean".equals(type) ? bound.isBoolean()
                : "integer".equals(type) ? bound.isNumber() : bound.isString();
        if (!ok) {
            throw fail(Codes.PXL012, "参数 \"" + name + "\" 的类型与该位置不符（需要 " + type + "）");
        }
        return bound;
    }

    private static void rejectReference(Json.Value value, String where) throws PxlFormatException {
        if (value.isString() && isReference(value.text)) {
            throw fail(Codes.PXL012, "参引出现在白名单之外的字段: \"" + where + "\"");
        }
    }

    static boolean isReference(String text) {
        return text.length() > 1 && text.charAt(0) == '$' && isName(text.substring(1));
    }

    private static void checkKind(String name, String type, Json.Value fallback)
            throws PxlFormatException {
        if ("color".equals(type)) {
            if (!fallback.isString() || !fallback.text.matches("[0-9A-F]{8}")) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的默认值必须是八位大写十七进制");
            }
        } else if ("integer".equals(type)) {
            if (!fallback.isNumber()) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的默认值必须是整数");
            }
            // 只判 isNumber 会放行 1.5 / 1e3 / 前导零（发布态复核 P2）：复用整数字面量校验。
            literal(name, "integer", fallback.text);
        } else if ("boolean".equals(type)) {
            if (!fallback.isBoolean()) {
                throw fail(Codes.PXL012, "参数 \"" + name + "\" 的默认值必须是布尔");
            }
        } else if (!fallback.isString()) {
            // 符号合法性由「是否已声明」判定（validateSymbolDefaults），不能套参数名规则：
            // v0 的 sprite 名允许 "-"（发布态复核 F1）。
            throw fail(Codes.PXL012, "参数 \"" + name + "\" 的默认值必须是已声明的名字");
        }
    }

    private static boolean isType(String type) {
        for (String known : TYPES) {
            if (known.equals(type)) {
                return true;
            }
        }
        return false;
    }

    static boolean isName(String text) {
        if (text == null || text.isEmpty() || text.length() > 64) {
            return false;
        }
        char first = text.charAt(0);
        if (first < 'a' || first > 'z') {
            return false;
        }
        for (int index = 1; index < text.length(); index++) {
            char ch = text.charAt(index);
            boolean ok = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_';
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    private static boolean contains(String[] names, String value) {
        for (String name : names) {
            if (name.equals(value)) {
                return true;
            }
        }
        return false;
    }

    private static PxlFormatException fail(String code, String message) {
        return new PxlFormatException(code, message);
    }
}
