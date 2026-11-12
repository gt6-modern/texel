"""PNG -> RGBA8 解码器(v0 §8)。

v1 的 png asset 与「永久双路径 PNG 门禁」(PXL-v1 §13.5)都要求 compiler 侧有一份**完整**的
接受集实现,而不是把测试用的最小解码器当生产件。本模块按 §8.1 / §8.2 / §8.5 逐条实现:
签名、每个 chunk 的 CRC、chunk 类型名与保留位、长度防溢出、IHDR/IEND/IDAT 的时序与重复规则、
PLTE / tRNS 的允许与禁止、zlib 单流与 Adler、精确解压长度、行过滤器 0..4。

任何一条不满足即 PXL006(v0 §8.3:在产出任何 PXL 之前拒绝)。
"""

import struct
import zlib

from .errors import PxlError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: §5.0.2 的面积上界(输入侧同一口径,§8.1)
MAX_AREA = 1 << 22
MAX_SIDE = 65536

_CRITICAL_UNKNOWN = "未知 critical chunk"


def _fail(message):
    raise PxlError("PXL006", message)


class Image(object):
    """解码结果:RGBA8 原始字节流 + 尺寸。"""

    __slots__ = ("width", "height", "rgba")

    def __init__(self, width, height, rgba):
        self.width = width
        self.height = height
        self.rgba = rgba


def decode(data):
    """解码 PNG,返回 Image;任何不受支持或损坏的输入都抛 PXL006。"""
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        _fail("PNG 签名不匹配")
    pos = 8
    ihdr = None
    plte = None
    trns = None
    idat = bytearray()
    idat_started = False
    idat_finished = False
    iend_seen = False
    while True:
        if pos == len(data):
            break
        if pos + 8 > len(data):
            _fail("chunk 头部被截断")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if length > 0x7FFFFFFF:
            _fail("chunk 长度最高位为 1")
        if length + 12 > len(data) - pos:
            _fail("chunk 声明长度越过文件末尾")
        kind = data[pos + 4:pos + 8]
        _check_type(kind)
        payload = data[pos + 8:pos + 8 + length]
        stored_crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        computed = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
        if stored_crc != computed:
            _fail("chunk " + kind.decode("ascii", "replace") + " 的 CRC 不匹配")
        pos += 12 + length
        if iend_seen:
            _fail("IEND 之后还有字节")
        if idat_started and kind != b"IDAT":
            # IDAT 必须连续出现(§8.5):中间夹了任何别的 chunk 之后就不能再有 IDAT。
            idat_finished = True
        if kind == b"IHDR":
            if ihdr is not None:
                _fail("IHDR 出现多次")
            if len(payload) != 13:
                _fail("IHDR 长度不是 13")
            ihdr = _parse_ihdr(payload)
            continue
        if ihdr is None:
            _fail("第一个 chunk 不是 IHDR")
        if kind == b"PLTE":
            if plte is not None:
                _fail("PLTE 出现多次")
            if idat_started:
                _fail("PLTE 晚于第一个 IDAT")
            if trns is not None:
                _fail('tRNS 早于 PLTE（规范 §8.5：存在 PLTE 时 tRNS 必须晚于 PLTE）')
            plte = _parse_plte(payload)
            continue
        if kind == b"tRNS":
            if trns is not None:
                _fail("tRNS 出现多次")
            if idat_started:
                _fail("tRNS 晚于第一个 IDAT")
            if plte is None and not _plte_optional_for(ihdr["color_type"]):
                # 色彩类型 3 的 tRNS 必须晚于 PLTE;其它类型没有 PLTE 也合法(如 ct0/ct2)。
                _fail("tRNS 早于 PLTE")
            trns = payload
            continue
        if kind == b"IDAT":
            if idat_finished:
                _fail("IDAT 不连续")
            idat_started = True
            idat += payload
            continue
        if kind == b"IEND":
            if length != 0:
                _fail("IEND 长度不是 0")
            iend_seen = True
            continue
        # 其它 chunk:关键块拒绝,ancillary 忽略(可重复)。
        if _is_critical(kind):
            _fail(_CRITICAL_UNKNOWN + ": " + kind.decode("ascii", "replace"))
        if idat_started:
            continue
    if not iend_seen:
        _fail("缺少 IEND")
    if ihdr is None:
        _fail("缺少 IHDR")
    if not idat:
        _fail("缺少 IDAT")
    return _decode_pixels(ihdr, plte, trns, bytes(idat))


def has_signature(data):
    """前 8 字节是否就是 PNG 签名。**只看签名**,不做任何结构判断。

    v1 的 asset 层用它区分两个码(规范 §11.1):声明为 png 而字节不是 PNG -> PXL009;
    有签名但结构非法 -> PXL006(由 decode 判)。边界是「是不是 PNG」与「PNG 是否合法」。
    """
    return len(data) >= 8 and data[:8] == PNG_SIGNATURE


def read_header(data):
    """只读签名与 IHDR,返回 (width, height);**不分配像素缓冲,也不解压 IDAT**。

    为什么需要它:「累计 PNG 解码像素数」预算(规范 §10.2)按名字就该约束**解码工作量**。
    先解码再判预算的话,N 张各自合法的大图会全部解完才报 PXL011 —— 32 张 2048^2 常驻约
    512 MiB,4096 张是 64 GiB,先 OOM 再轮到预算(审查 R4-02)。所以 v1 的 asset 层在读到
    IHDR 时就把像素数计入并即时判断。

    只覆盖到 IHDR 为止的结构检查;IDAT / IEND 等仍由 decode 判(同为 PXL006)。
    """
    if not has_signature(data):
        _fail("PNG 签名不匹配")
    if len(data) < 8 + 8 + 13 + 4:
        _fail("IHDR 被截断")
    length = struct.unpack(">I", data[8:12])[0]
    if length != 13:
        _fail("IHDR 长度不是 13")
    kind = data[12:16]
    if kind != b"IHDR":
        _fail("第一个 chunk 不是 IHDR")
    payload = data[16:29]
    stored_crc = struct.unpack(">I", data[29:33])[0]
    computed = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    if stored_crc != computed:
        _fail("chunk IHDR 的 CRC 不匹配")
    ihdr = _parse_ihdr(payload)
    return ihdr["width"], ihdr["height"]


def _check_type(kind):
    if len(kind) != 4:
        _fail("chunk 类型名不是 4 字节")
    for byte in kind:
        if not (65 <= byte <= 90 or 97 <= byte <= 122):
            _fail("chunk 类型名含非字母字节")
    if not 65 <= kind[2] <= 90:
        _fail("chunk 类型名第三字节的保留位不是大写")


def _is_critical(kind):
    return 65 <= kind[0] <= 90


def _plte_optional_for(color_type):
    # PLTE 对 ct3 必需、对 ct0/4 禁止、对 ct2/6 可忽略。这里只回答“PLTE 可以不存在吗”。
    return color_type != 3


def _parse_ihdr(payload):
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", payload)
    if width == 0 or height == 0:
        _fail("宽或高为 0")
    if width > MAX_SIDE or height > MAX_SIDE:
        _fail("宽或高超过 65536")
    if width * height > MAX_AREA:
        _fail("面积超过 2^22")
    if depth != 8:
        _fail("样本深度不是 8")
    if color_type not in (0, 2, 3, 4, 6):
        _fail("不受支持的色彩类型 " + str(color_type))
    if compression != 0:
        _fail("IHDR 压缩方法不是 0")
    if filter_method != 0:
        _fail("IHDR 过滤方法不是 0")
    if interlace != 0:
        _fail("交错(Adam7)输入不受支持")
    return {"width": width, "height": height, "color_type": color_type}


def _parse_plte(payload):
    if len(payload) % 3 != 0:
        _fail("PLTE 长度不是 3 的倍数")
    count = len(payload) // 3
    if count < 1 or count > 256:
        _fail("PLTE 项数不在 1..256")
    entries = []
    for index in range(count):
        entries.append((payload[3 * index], payload[3 * index + 1], payload[3 * index + 2]))
    return entries


def _channels(color_type):
    return {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]


def _decode_pixels(ihdr, plte, trns, compressed):
    width = ihdr["width"]
    height = ihdr["height"]
    color_type = ihdr["color_type"]
    channels = _channels(color_type)
    if color_type == 3:
        if plte is None:
            _fail("色彩类型 3 缺少 PLTE")
    elif color_type in (0, 4):
        if plte is not None:
            _fail("色彩类型 " + str(color_type) + " 不得有 PLTE")
    if trns is not None:
        if color_type == 0:
            if len(trns) != 2:
                _fail("色彩类型 0 的 tRNS 长度不是 2")
        elif color_type == 2:
            if len(trns) != 6:
                _fail("色彩类型 2 的 tRNS 长度不是 6")
        elif color_type == 3:
            if len(trns) > len(plte):
                _fail("tRNS 长度超过 PLTE 项数")
        else:
            _fail("色彩类型 " + str(color_type) + " 不得有 tRNS")
    expected = height * (1 + width * channels)
    raw = _inflate(compressed, expected)
    stride = width * channels
    pixels = _unfilter(raw, height, stride, channels)
    return Image(width, height, _to_rgba(width, height, color_type, plte, trns, pixels, stride))


def _inflate(compressed, expected):
    decompressor = zlib.decompressobj(15)
    try:
        out = decompressor.decompress(compressed, expected + 1)
    except zlib.error as failure:
        _fail("IDAT zlib 流损坏: " + str(failure))
    if len(out) > expected:
        _fail("IDAT 解压结果过长")
    if not decompressor.eof:
        _fail("IDAT 解压结果过短(流未正常收尾)")
    if decompressor.unused_data:
        _fail("zlib 流之后还有剩余字节")
    if len(out) != expected:
        _fail("IDAT 解压结果过短")
    return out


def _unfilter(raw, height, stride, channels):
    out = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0
    for row in range(height):
        filter_type = raw[pos]
        pos += 1
        if filter_type > 4:
            _fail("行过滤器字节不在 0..4: " + str(filter_type))
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                upleft = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, up, upleft)) & 0xFF
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return out


def _paeth(left, up, upleft):
    estimate = left + up - upleft
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upleft = abs(estimate - upleft)
    if distance_left <= distance_up and distance_left <= distance_upleft:
        return left
    if distance_up <= distance_upleft:
        return up
    return upleft


def _to_rgba(width, height, color_type, plte, trns, pixels, stride):
    out = bytearray(width * height * 4)
    if color_type == 0:
        key = None
        if trns is not None:
            key = struct.unpack(">H", trns)[0] & 0xFF
        for index in range(width * height):
            value = pixels[index]
            base = index * 4
            out[base] = value
            out[base + 1] = value
            out[base + 2] = value
            out[base + 3] = 0 if key is not None and value == key else 255
        return out
    if color_type == 2:
        key = None
        if trns is not None:
            key = (trns[1], trns[3], trns[5])
        for index in range(width * height):
            base = index * 3
            r = pixels[base]
            g = pixels[base + 1]
            b = pixels[base + 2]
            target = index * 4
            out[target] = r
            out[target + 1] = g
            out[target + 2] = b
            out[target + 3] = 0 if key is not None and (r, g, b) == key else 255
        return out
    if color_type == 3:
        alphas = trns if trns is not None else b""
        limit = len(plte)
        for index in range(width * height):
            sample = pixels[index]
            if sample >= limit:
                _fail("调色板索引越过 PLTE 项数")
            r, g, b = plte[sample]
            target = index * 4
            out[target] = r
            out[target + 1] = g
            out[target + 2] = b
            out[target + 3] = alphas[sample] if sample < len(alphas) else 255
        return out
    if color_type == 4:
        for index in range(width * height):
            value = pixels[index * 2]
            target = index * 4
            out[target] = value
            out[target + 1] = value
            out[target + 2] = value
            out[target + 3] = pixels[index * 2 + 1]
        return out
    out[:] = pixels
    return out
