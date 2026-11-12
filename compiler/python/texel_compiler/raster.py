"""图层缓冲、八条图元与三种 blend(v0 §5 / §6)。

两条不变量贯穿本模块:

* **op 只写当前图层缓冲**,整体替换目标 texel(含四通道),op 之间不混合(§5.0);
* **颜色运算全整数**,取整只由 div255 定义,禁止浮点参与(§6.2)。

内部表示:palette 是 list[bytes],palette[0] 恒为 b"\x00\x00\x00\x00";图层缓冲是
bytearray(RGBA 逐像素)。图元计划(plan)是 tuple 列表,由 doc 校验阶段产出,这里只执行。
"""

ZERO = b"\x00\x00\x00\x00"
(R, G, B, A, RO, GO, BO, AO) = (0, 1, 2, 3, 4, 5, 6, 7)


def div255(value):
    """§6.2 的唯一定义:(v + 127) / 255,整数除法。"""
    return (value + 127) // 255


def to_texel(text):
    """把 8 位大写十六进制调色板项转成 4 字节 texel。"""
    return bytes((int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), int(text[6:8], 16)))


class Surface(object):
    __slots__ = ("width", "height", "data")

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.data = bytearray(width * height * 4)


def _put(data, offset, texel):
    data[offset:offset + 4] = texel


def _clip_rect(rect, width, height):
    x, y, w, h = rect
    left = x if x > 0 else 0
    top = y if y > 0 else 0
    right = x + w
    bottom = y + h
    if right > width:
        right = width
    if bottom > height:
        bottom = height
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _fill_rect(surface, rect, texel):
    clipped = _clip_rect(rect, surface.width, surface.height)
    if clipped is None:
        return
    left, top, right, bottom = clipped
    run = texel * (right - left)
    data = surface.data
    stride = surface.width * 4
    for row in range(top, bottom):
        start = row * stride + left * 4
        data[start:start + len(run)] = run


def _draw_line(surface, x0, y0, x1, y1, texel):
    """§5.4 逐字符固定的 Bresenham:不做端点交换归一,两个判据各自独立。"""
    dx = x1 - x0 if x1 >= x0 else x0 - x1
    sx = 1 if x0 < x1 else -1
    dy = -(y1 - y0 if y1 >= y0 else y0 - y1)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x = x0
    y = y0
    width = surface.width
    height = surface.height
    data = surface.data
    while True:
        if 0 <= x < width and 0 <= y < height:
            _put(data, (y * width + x) * 4, texel)
        if x == x1 and y == y1:
            return
        double = 2 * err
        if double >= dy:
            err += dy
            x += sx
        if double <= dx:
            err += dx
            y += sy


def _draw_rows(surface, x, y, rows, cols, flipx, flipy):
    """把 texel 行矩形写到 (x, y);flipX 先翻,flipY 后翻(§5.6 / §5.8)。

    rows 是"每行一段扁平 RGBA 字节"(长度 cols * 4),不是像素对象列表:大 sprite 与
    asset 都可能是几十万像素,按像素建对象会把内存放大一个数量级。
    """
    height = len(rows)
    data = surface.data
    stride = surface.width * 4
    for j in range(height):
        source_row = rows[height - 1 - j] if flipy else rows[j]
        target_y = y + j
        if target_y < 0 or target_y >= surface.height:
            continue
        left = x if x > 0 else 0
        right = x + cols
        if right > surface.width:
            right = surface.width
        if right <= left:
            continue
        offset = target_y * stride + left * 4
        if flipx:
            chunk = bytearray()
            for i in range(left - x, right - x):
                start = (cols - 1 - i) * 4
                chunk += source_row[start:start + 4]
            data[offset:offset + len(chunk)] = chunk
        else:
            start = (left - x) * 4
            end = (right - x) * 4
            data[offset:offset + (end - start)] = source_row[start:end]


def _mirror(surface, from_rect, to_point, flipx, flipy):
    """§5.6:源区域先整体快照;读侧越界给透明 slot,写侧按 §5.0 裁剪。

    发布态复核 F03：这里原先把**整个源矩形**都复制成快照——目标完全离屏时也要分配
    fw*fh*4 字节（4096² = 64 MiB，按允许的最大源宽高推算可达 ~16 GiB）。
    现在只为**可能与目标重叠**的源像素建快照：读前快照语义只对"写会覆盖到源"的那部分
    才有意义，其余像素直接读原位即可。
    """
    fx, fy, fw, fh = from_rect
    tx, ty = to_point
    data = surface.data
    stride = surface.width * 4

    # 目标侧可见的行/列（写侧裁剪，§5.0）。
    j0 = max(0, -ty)
    j1 = min(fh, surface.height - ty)
    i0 = max(0, -tx)
    i1 = min(fw, surface.width - tx)
    if j1 <= j0 or i1 <= i0:
        return  # 目标完全离屏：一个源像素都不用读，更不用快照

    # 需要快照的源像素 = 源矩形与目标矩形的交集（翻转与否不影响"哪块会被覆盖"）。
    ox0 = max(fx, tx)
    ox1 = min(fx + fw, tx + fw)
    oy0 = max(fy, ty)
    oy1 = min(fy + fh, ty + fh)
    overlap = {}
    if ox1 > ox0 and oy1 > oy0:
        for sy in range(max(oy0, 0), min(oy1, surface.height)):
            base = sy * stride
            for sx in range(max(ox0, 0), min(ox1, surface.width)):
                off = base + sx * 4
                overlap[(sx, sy)] = data[off:off + 4]

    for j in range(j0, j1):
        dy = ty + j
        source_j = fh - 1 - j if flipy else j
        sy = fy + source_j
        for i in range(i0, i1):
            dx = tx + i
            source_i = fw - 1 - i if flipx else i
            sx = fx + source_i
            if not (0 <= sy < surface.height and 0 <= sx < surface.width):
                # 读侧越界要**写透明**（v0 §5.6 的原文），不是跳过——跳过会让目标像素
                # 保留原值，同一张图两侧就得到不同像素（发布态复核 F-R6-01）。
                texel = ZERO
            else:
                texel = overlap.get((sx, sy))
                if texel is None:
                    off = sy * stride + sx * 4
                    texel = data[off:off + 4]
            off = dy * stride + dx * 4
            data[off:off + 4] = texel


def execute_ops(surface, palette, plan):
    """按序执行一层的 op 计划。palette[slot] 是 4 字节 texel。"""
    for op in plan:
        kind = op[0]
        if kind == "fill":
            _fill_rect(surface, (0, 0, surface.width, surface.height), palette[op[1]])
        elif kind == "rect":
            _fill_rect(surface, (op[1], op[2], op[3], op[4]), palette[op[5]])
        elif kind == "pixel":
            _fill_rect(surface, (op[1], op[2], 1, 1), palette[op[3]])
        elif kind == "erase":
            _fill_rect(surface, (op[1], op[2], op[3], op[4]), ZERO)
        elif kind == "line":
            _draw_line(surface, op[1], op[2], op[3], op[4], palette[op[5]])
        elif kind == "grid":
            rows = []
            for row in op[3]:
                rows.append(b"".join([palette[slot] for slot in row]))
            _draw_rows(surface, op[1], op[2], rows, len(op[3][0]), False, False)
        elif kind == "mirror":
            _mirror(surface, op[1], op[2], op[3], op[4])
        elif kind == "stamp":
            _draw_rows(surface, op[2], op[3], op[1], op[6], op[4], op[5])
        elif kind == "stamp_asset":
            # asset 的行在这里才切:计划的构建发生在相位 A,那时子节点还没光栅化(§11.2:
            # 预算先于光栅化),所以 rgba 只能执行期读。
            stamp = op[1]
            asset = stamp.asset
            rgba = asset.rgba
            stride = asset.width * 4
            rows = [rgba[row * stride:(row + 1) * stride] for row in range(asset.height)]
            _draw_rows(surface, stamp.x, stamp.y, rows, asset.width,
                       stamp.flip_x, stamp.flip_y)
        else:
            raise AssertionError("未知的 op 计划项: " + str(kind))


def raster_work(width, height, plan):
    """§5.0.2 的"光栅工作"计数:裁剪之后的像素写入次数之和。"""
    total = 0
    for op in plan:
        kind = op[0]
        if kind == "fill":
            total += width * height
        elif kind in ("rect", "erase"):
            clipped = _clip_rect((op[1], op[2], op[3], op[4]), width, height)
            if clipped is not None:
                total += (clipped[2] - clipped[0]) * (clipped[3] - clipped[1])
        elif kind == "pixel":
            if 0 <= op[1] < width and 0 <= op[2] < height:
                total += 1
        elif kind == "grid":
            total += _rows_work(width, height, op[1], op[2], len(op[3][0]), len(op[3]))
        elif kind == "stamp":
            total += _rows_work(width, height, op[2], op[3], op[6], len(op[1]))
        elif kind == "stamp_asset":
            # asset stamp 的写入面积**不计入单文档的 2^22**(规范 §10.1:尺寸要等 R2 才知道,
            # 单文档预算属阶段 6);它按 §10.3 计入**会话**全局光栅,由 pxlcompile.Session 累加。
            # 这里**显式写出来**而不是靠"没有分支就落到 0":少一条分支就多一次读错形状的机会。
            continue
        elif kind == "line":
            dx = op[3] - op[1] if op[3] >= op[1] else op[1] - op[3]
            dy = op[4] - op[2] if op[4] >= op[2] else op[2] - op[4]
            total += (dx if dx > dy else dy) + 1
        elif kind == "mirror":
            to_rect = (op[2][0], op[2][1], op[1][2], op[1][3])
            clipped = _clip_rect(to_rect, width, height)
            if clipped is not None:
                total += (clipped[2] - clipped[0]) * (clipped[3] - clipped[1])
    return total


def clip_area(width, height, x, y, w, h):
    """裁剪后的面积(§5.0.2 的计数口径)。"""
    clipped = _clip_rect((x, y, w, h), width, height)
    if clipped is None:
        return 0
    return (clipped[2] - clipped[0]) * (clipped[3] - clipped[1])


def _rows_work(width, height, x, y, cols, rows):
    clipped = _clip_rect((x, y, cols, rows), width, height)
    if clipped is None:
        return 0
    return (clipped[2] - clipped[0]) * (clipped[3] - clipped[1])


# ---------------------------------------------------------------- 合成(§6)


def blend_layer(accumulator, layer, blend, width, height):
    """把 layer 按 blend 合到 accumulator 上(就地修改 accumulator)。"""
    if blend == "normal":
        _blend_normal(accumulator, layer.data, width * height)
    elif blend == "multiply":
        _blend_multiply(accumulator, layer.data, width * height)
    elif blend == "mask":
        _blend_mask(accumulator, layer.data, width * height)
    else:
        raise AssertionError("未知的 blend: " + str(blend))


def _blend_normal(dst, src, count):
    """§6.3。aNum == 0 的两个分支是「不可见颜色」语义的算术落点(§7)。"""
    for index in range(count):
        base = index * 4
        sa = src[base + 3]
        da = dst[base + 3]
        if sa == 255 and da == 0:
            # 快路径:不透明源覆盖空累加器,结果就是源(ao = 255,outc = sc)。
            dst[base] = src[base]
            dst[base + 1] = src[base + 1]
            dst[base + 2] = src[base + 2]
            dst[base + 3] = 255
            continue
        t = 255 - sa
        a_num = sa * 255 + da * t
        if a_num == 0:
            if src[base] == 0 and src[base + 1] == 0 and src[base + 2] == 0:
                dst[base + 3] = 0
            else:
                dst[base] = src[base]
                dst[base + 1] = src[base + 1]
                dst[base + 2] = src[base + 2]
                dst[base + 3] = 0
            continue
        half = a_num // 2
        dst[base] = (src[base] * sa * 255 + dst[base] * da * t + half) // a_num
        dst[base + 1] = (src[base + 1] * sa * 255 + dst[base + 1] * da * t + half) // a_num
        dst[base + 2] = (src[base + 2] * sa * 255 + dst[base + 2] * da * t + half) // a_num
        dst[base + 3] = div255(a_num)


def _blend_multiply(dst, src, count):
    """§6.4:先 div255(sc * dc),再用与 normal 相同的公式合成。"""
    for index in range(count):
        base = index * 4
        sa = src[base + 3]
        da = dst[base + 3]
        t = 255 - sa
        a_num = sa * 255 + da * t
        if a_num == 0:
            if src[base] == 0 and src[base + 1] == 0 and src[base + 2] == 0:
                dst[base + 3] = 0
            else:
                dst[base] = src[base]
                dst[base + 1] = src[base + 1]
                dst[base + 2] = src[base + 2]
                dst[base + 3] = 0
            continue
        half = a_num // 2
        mr = div255(src[base] * dst[base])
        mg = div255(src[base + 1] * dst[base + 1])
        mb = div255(src[base + 2] * dst[base + 2])
        dst[base] = (mr * sa * 255 + dst[base] * da * t + half) // a_num
        dst[base + 1] = (mg * sa * 255 + dst[base + 1] * da * t + half) // a_num
        dst[base + 2] = (mb * sa * 255 + dst[base + 2] * da * t + half) // a_num
        dst[base + 3] = div255(a_num)


def _blend_mask(dst, src, count):
    """§6.5:只乘 alpha,RGB 恒取下层。"""
    for index in range(count):
        base = index * 4
        sa = src[base + 3]
        if sa == 0 and src[base] == 0 and src[base + 1] == 0 and src[base + 2] == 0:
            continue
        dst[base + 3] = div255(dst[base + 3] * sa)
