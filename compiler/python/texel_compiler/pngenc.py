"""RGBA8 -> PNG 编码(v0 §10)。

输出约定:每通道 8 bit、色彩类型 6(RGBA)、非交错;**所有行过滤器恒为 0**;只写
IHDR / IDAT / IEND 三种 chunk;同一实现同一输入两次输出逐字节相同(§9),因此 deflate
参数写死、不做任何启发式。
"""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: 固定的 deflate 参数。取 9 是为了让输出小一些;关键是它固定,不随时钟/环境变化。
_LEVEL = 9
_MEM_LEVEL = 8
_CHUNK_LIMIT = 1 << 20

#: PNG 输入侧的尺寸上限(§8.1),与 ``pngdec`` 保持一致:单边 1..65536、面积 ≤ 2^22。
MAX_SIDE = 65536
MAX_AREA = 1 << 22


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF))


def encode(width, height, rgba):
    """把 RGBA8 原始字节流编码成 PNG 文件字节。

    尺寸在**写出任何字节之前**按 §8.1 校验(单边 1..65536、面积 ≤ 2^22)。不校验的
    编码器能产出自己解不出来的 PNG —— ``pngdec.decode`` 会以 ``PXL006`` 拒绝它,于是
    错误被推迟到下游的导入/解析阶段,而调用方已经拿到"成功"的字节(审查 #9/#10)。
    """
    if width < 1 or height < 1:
        raise ValueError("宽或高为 0: %dx%d" % (width, height))
    if width > MAX_SIDE or height > MAX_SIDE:
        raise ValueError("宽或高超过 65536: %dx%d" % (width, height))
    if width * height > MAX_AREA:
        raise ValueError("面积超过 2^22: %d x %d = %d" % (width, height, width * height))
    stride = width * 4
    if len(rgba) != stride * height:
        raise ValueError("RGBA 字节流长度与尺寸不符")
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # 过滤器恒定 0(None)
        raw += rgba[row * stride:(row + 1) * stride]
    compressor = zlib.compressobj(_LEVEL, zlib.DEFLATED, 15, _MEM_LEVEL, 0)
    deflated = compressor.compress(bytes(raw)) + compressor.flush()
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    out = bytearray(PNG_SIGNATURE)
    out += _chunk(b"IHDR", header)
    # IDAT 允许分块(§10)。固定按 1 MiB 切,块数只取决于输入大小,因此仍然确定。
    for start in range(0, len(deflated), _CHUNK_LIMIT):
        out += _chunk(b"IDAT", deflated[start:start + _CHUNK_LIMIT])
    out += _chunk(b"IEND", b"")
    return bytes(out)


def chunk_types(data):
    """按文件顺序返回 chunk 类型序列;只给 runner/测试做结构断言用。"""
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("不是 PNG 签名")
    types = []
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        types.append(data[pos + 4:pos + 8])
        pos += 12 + length
    return types


def row_filter_bytes(data, width, height):
    """返回每行开头的过滤器字节列表;用于断言「恒为 0」(§10 / §13.2a 第 6 条)。"""
    pos = 8
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    filters = []
    offset = 0
    for _ in range(height):
        filters.append(raw[offset])
        offset += 1 + stride
    return filters
