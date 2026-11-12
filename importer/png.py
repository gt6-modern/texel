"""PNG 解码:结构解析、zlib 整流校验与逆过滤(规范 §8.1 / §8.3 / §8.5)。

接受集是封闭的:样本深度恒为 8、非交错、色彩类型 ∈ {0, 2, 3, 4, 6}(§8.1)。
§8.5 的每一条结构规则都在本模块逐条实现:签名、每个 chunk 的 CRC(含 IEND)、
类型名与保留位、长度字段的溢出防护、IHDR / PLTE / tRNS / IDAT 的顺序与重复约束、
IEND 的位置与长度、未知 critical 拒绝 / 未知 ancillary 忽略、PLTE 与 tRNS 的
长度与索引约束、宽高与面积上界、单一 zlib 流(含 Adler-32)、解压字节数、行过滤器。

任何拒绝一律 ``PxlError(PXL006, 具体原因)``。产出的 :class:`PngImage` 中 ``rows``
已是逆过滤后的行数据,不含过滤字节。
"""

import binascii
import struct
import zlib

from .errors import PXL006, PxlError

#: PNG 签名,必须恰好这 8 字节(§8.5)。
SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: 宽高各自的上界(§5.0.1)。
MAX_DIMENSION = 65536

#: 面积上界 2^22 = 2048 x 2048(§5.0.2)。
MAX_AREA = 1 << 22

#: chunk 长度字段的上界(§8.5 要求 len <= 2,147,483,647)。
MAX_CHUNK_LENGTH = 2147483647

#: 每像素字节数,8 bit 样本、非交错(§3.2 的 stride 除以 width)。
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: 已知 critical chunk;其余首字母大写的类型名一律拒绝(§8.5)。
KNOWN_CRITICAL = frozenset([b"IHDR", b"PLTE", b"IDAT", b"IEND"])


class PngImage(object):
    """一张已解码的 PNG:结构字段 + 逆过滤后的行(不含过滤字节)。

    每行长度等于 ``stride``:ct0 / ct3 为 ``width``,ct2 为 ``3*width``,
    ct4 为 ``2*width``,ct6 为 ``4*width``。
    """

    __slots__ = ("width", "height", "color_type", "bit_depth", "plte", "trns", "rows")

    def __init__(self, width, height, color_type, bit_depth, plte, trns, rows):
        self.width = width
        self.height = height
        self.color_type = color_type
        self.bit_depth = bit_depth
        self.plte = plte
        self.trns = trns
        self.rows = rows

    def __repr__(self):
        return "PngImage(%dx%d, color_type=%d)" % (
            self.width, self.height, self.color_type)


def decode_png(data):
    """把 PNG 字节解成 :class:`PngImage`;任何拒绝都是 ``PxlError(PXL006, ...)``。"""
    if not isinstance(data, (bytes, bytearray)):
        raise PxlError(PXL006, "输入不是字节串")
    buf = bytes(data)
    if len(buf) < 8:
        raise PxlError(PXL006, "文件短于 8 字节,签名不完整")
    if buf[:8] != SIGNATURE:
        raise PxlError(PXL006, "PNG 签名不符")

    chunks = _read_chunks(buf)
    _check_skeleton(chunks)

    width, height, color_type = _parse_ihdr(chunks[0][1])
    plte, trns = _check_palette_chunks(chunks, color_type)

    idat = b"".join(body for name, body in chunks if name == b"IDAT")
    stride = width * CHANNELS[color_type]
    expect = height * (1 + stride)
    # 期望长度在解压之前就能算出来,所以解压本身要带上界:1 MB 的 IDAT 可以膨胀到
    # GB 级,先解完再比大小等于把攻击面留在内存里。
    raw = _inflate(idat, expect)
    if len(raw) != expect:
        raise PxlError(
            PXL006, "解压后字节数为 %d,应为 %d(= height x (1 + stride))"
            % (len(raw), expect))

    rows = _unfilter(raw, height, stride, CHANNELS[color_type])

    if color_type == 3:
        entries = len(plte) // 3
        for row in rows:
            if max(row) >= entries:
                raise PxlError(
                    PXL006, "调色板索引 %d 越出 PLTE 的 %d 项" % (max(row), entries))

    return PngImage(width, height, color_type, 8, plte, trns, rows)


# ---------------------------------------------------------------- chunk 层

def _check_type_name(name):
    """类型名必须恰为 4 个 ASCII 字母,且第三字节的保留位为 0(大写)。"""
    for byte in name:
        if not (65 <= byte <= 90 or 97 <= byte <= 122):
            raise PxlError(PXL006, "chunk 类型名不是 4 个 ASCII 字母: %r" % (name,))
    if not (65 <= name[2] <= 90):
        raise PxlError(PXL006, "chunk 类型名第三字节不是大写字母(保留位为 1): %r"
                       % (name,))


def _read_chunks(buf):
    """逐块切分并校验长度、类型名与 CRC;到第一个 IEND 为止。"""
    total = len(buf)
    pos = 8
    chunks = []
    while True:
        if pos == total:
            raise PxlError(PXL006, "缺少 IEND")
        if total - pos < 8:
            raise PxlError(PXL006, "chunk 头被截断")
        length = struct.unpack(">I", buf[pos:pos + 4])[0]
        if length > MAX_CHUNK_LENGTH:
            raise PxlError(PXL006, "chunk 长度 %d 超过 2^31-1" % length)
        # 8 + len + 4 <= 剩余字节数;此处比较用 Python 整数,不会回绕
        if 8 + length + 4 > total - pos:
            raise PxlError(PXL006, "chunk 长度 %d 越出文件末尾" % length)
        name = buf[pos + 4:pos + 8]
        _check_type_name(name)
        body = buf[pos + 8:pos + 8 + length]
        stored = struct.unpack(">I", buf[pos + 8 + length:pos + 12 + length])[0]
        if stored != binascii.crc32(name + body) & 0xFFFFFFFF:
            raise PxlError(PXL006, "chunk %s 的 CRC 不符"
                           % name.decode("ascii"))
        chunks.append((name, body))
        pos += 12 + length
        if name == b"IEND":
            if length != 0:
                raise PxlError(PXL006, "IEND 数据长度为 %d,应为 0" % length)
            if pos != total:
                raise PxlError(PXL006, "IEND 之后还有 %d 字节" % (total - pos))
            break
    return chunks


def _check_skeleton(chunks):
    """IHDR / IEND 的位置唯一性、未知 critical、IDAT/PLTE/tRNS 的顺序与重复。"""
    names = [name for name, _body in chunks]

    if names[0] != b"IHDR":
        raise PxlError(PXL006, "第一个 chunk 不是 IHDR")
    if names.count(b"IHDR") != 1:
        raise PxlError(PXL006, "IHDR 出现了 %d 次,应恰好一次" % names.count(b"IHDR"))
    if names[-1] != b"IEND":
        raise PxlError(PXL006, "最后一个 chunk 不是 IEND")

    for name in names:
        if 65 <= name[0] <= 90 and name not in KNOWN_CRITICAL:
            raise PxlError(PXL006, "未知 critical chunk: %s" % name.decode("ascii"))

    plte_at = [i for i, name in enumerate(names) if name == b"PLTE"]
    trns_at = [i for i, name in enumerate(names) if name == b"tRNS"]
    idat_at = [i for i, name in enumerate(names) if name == b"IDAT"]

    if not idat_at:
        raise PxlError(PXL006, "缺少 IDAT")
    if idat_at[-1] - idat_at[0] + 1 != len(idat_at):
        raise PxlError(PXL006, "IDAT 不连续")
    if len(plte_at) > 1:
        raise PxlError(PXL006, "PLTE 出现了 %d 次,应至多一次" % len(plte_at))
    if len(trns_at) > 1:
        raise PxlError(PXL006, "tRNS 出现了 %d 次,应至多一次" % len(trns_at))

    first_idat = idat_at[0]
    if plte_at and plte_at[0] > first_idat:
        raise PxlError(PXL006, "PLTE 不早于第一个 IDAT")
    if trns_at and trns_at[0] > first_idat:
        raise PxlError(PXL006, "tRNS 不早于第一个 IDAT")
    if plte_at and trns_at and trns_at[0] < plte_at[0]:
        raise PxlError(PXL006, "tRNS 早于 PLTE")


def _parse_ihdr(body):
    """IHDR 的 13 字节字段与宽高、面积上界(§8.1 / §8.3 / §5.0.2)。"""
    if len(body) != 13:
        raise PxlError(PXL006, "IHDR 数据长度为 %d,应为 13" % len(body))
    (width, height, bit_depth, color_type,
     compression, filter_method, interlace) = struct.unpack(">IIBBBBB", body)

    if compression != 0:
        raise PxlError(PXL006, "压缩方法为 %d,应为 0" % compression)
    if filter_method != 0:
        raise PxlError(PXL006, "过滤方法为 %d,应为 0" % filter_method)
    if interlace != 0:
        raise PxlError(PXL006, "交错方法为 %d,只接受非交错(0)" % interlace)
    if bit_depth != 8:
        raise PxlError(PXL006, "样本深度为 %d,只接受 8" % bit_depth)
    if color_type not in CHANNELS:
        raise PxlError(PXL006, "色彩类型 %d 不受支持" % color_type)
    if width == 0 or height == 0:
        raise PxlError(PXL006, "宽或高为 0(%dx%d)" % (width, height))
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise PxlError(PXL006, "宽或高超过 %d(%dx%d)"
                       % (MAX_DIMENSION, width, height))
    # Python 整数不溢出;32 位乘法会把 65536x65536 回绕成 0 而放行
    if width * height > MAX_AREA:
        raise PxlError(PXL006, "面积 %d 超过上界 %d(%dx%d)"
                       % (width * height, MAX_AREA, width, height))
    return width, height, color_type


def _check_palette_chunks(chunks, color_type):
    """PLTE / tRNS 的存在性、长度与逐类型约束(§8.5)。"""
    plte = None
    trns = None
    for name, body in chunks:
        if name == b"PLTE":
            plte = body
        elif name == b"tRNS":
            trns = body

    entries = 0
    if plte is not None:
        if len(plte) % 3 != 0:
            raise PxlError(PXL006, "PLTE 长度 %d 不是 3 的倍数" % len(plte))
        entries = len(plte) // 3
        if entries < 1 or entries > 256:
            raise PxlError(PXL006, "PLTE 项数为 %d,应在 1..256" % entries)

    if color_type == 3 and plte is None:
        raise PxlError(PXL006, "色彩类型 3 缺少 PLTE")
    if color_type in (0, 4) and plte is not None:
        raise PxlError(PXL006, "色彩类型 %d 不得有 PLTE" % color_type)

    if trns is not None:
        if color_type == 3:
            if len(trns) > entries:
                raise PxlError(PXL006, "tRNS 长度 %d 超过 PLTE 的 %d 项"
                               % (len(trns), entries))
        elif color_type == 0:
            if len(trns) != 2:
                raise PxlError(PXL006, "色彩类型 0 的 tRNS 长度为 %d,应恰为 2"
                               % len(trns))
        elif color_type == 2:
            if len(trns) != 6:
                raise PxlError(PXL006, "色彩类型 2 的 tRNS 长度为 %d,应恰为 6"
                               % len(trns))
        else:
            raise PxlError(PXL006, "色彩类型 %d 不得有 tRNS" % color_type)

    return plte, trns


# ---------------------------------------------------------------- zlib 与行解码

def _inflate(idat, expect):
    """拼接后的 IDAT 必须是恰好一个完整 zlib 流(含 Adler-32 校验)。

    ``expect`` 是 §8.5 要求的解压长度 ``height * (1 + stride)``,由 IHDR 先算出。
    据此把 ``decompress`` 的 ``max_length`` 定成 ``expect + 1``:多一个字节就足以
    判定"解压过长",不必把整段膨胀结果收进内存。行为与"先解完再比大小"完全等价,
    区别只是拒绝路径不再分配。
    """
    obj = zlib.decompressobj()
    limit = expect + 1
    try:
        out = obj.decompress(idat, limit)
        if len(out) < limit:
            # 未触顶说明输入已耗尽;此时 flush 只可能吐出残余(正常为 0 字节)。
            out += obj.flush()
    except zlib.error as exc:
        raise PxlError(PXL006, "IDAT 的 zlib 流非法: %s" % exc)
    if len(out) > expect:
        raise PxlError(
            PXL006, "解压后字节数超过 %d(= height x (1 + stride))" % expect)
    if not obj.eof:
        raise PxlError(PXL006, "IDAT 的 zlib 流提前结束")
    if obj.unused_data:
        raise PxlError(PXL006, "IDAT 的 zlib 流之后还有 %d 字节剩余"
                       % len(obj.unused_data))
    return out


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, height, stride, bpp):
    """按 PNG 规范逐行还原过滤;每行的过滤字节必须落在 0..4。"""
    rows = []
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        else:
            raise PxlError(PXL006, "第 %d 行的过滤字节为 %d,应在 0..4"
                           % (y, ftype))
        rows.append(bytes(line))
        prev = line
    return rows
