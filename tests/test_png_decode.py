"""测试:PNG 解码器(规范 §8.1 / §8.3 / §8.5)与 RGBA8 展开(§8.2)。

本文件自带内存 PNG 构造器,只用 ``struct`` / ``zlib`` / ``binascii``。
刻意不复用 ``spec/conformance/tools/`` 下的代码:那是独立实现的 fixture 工具,
与被测解码器同源就失去交叉验证的意义。

每个拒绝用例只放一个缺陷,断言 ``err.code == "PXL006"``。
"""

import binascii
import struct
import tracemalloc
import unittest
import zlib

from importer.errors import PXL006, PxlError
from importer.png import PngImage, decode_png
from importer.rgba import sha256_hex, to_rgba8

SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 构造器

def mk_chunk(name, body, crc=None):
    """拼一个 chunk;``crc`` 显式给出时可用于造假。"""
    if not isinstance(name, bytes):
        name = name.encode("ascii")
    if crc is None:
        crc = binascii.crc32(name + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + name + body + struct.pack(">I", crc)


def mk_png(chunks):
    """签名 + 若干已拼好的 chunk(允许非法字节,便于构造拒绝用例)。"""
    return SIGNATURE + b"".join(chunks)


def ihdr_body(width, height, bit_depth=8, color_type=6,
              compression=0, filter_method=0, interlace=0):
    return struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                       compression, filter_method, interlace)


def plte_of(entries):
    out = bytearray()
    for rgb in entries:
        out.extend(rgb)
    return bytes(out)


def rgba_of(pixels):
    out = bytearray()
    for px in pixels:
        out.extend(px)
    return bytes(out)


def paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def filter_row(ftype, cur, prev, bpp):
    """按 PNG 规范把一行原始像素编码成过滤后的字节(不含过滤字节本身)。"""
    out = bytearray()
    for i in range(len(cur)):
        a = cur[i - bpp] if i >= bpp else 0
        b = prev[i] if prev is not None else 0
        c = prev[i - bpp] if (prev is not None and i >= bpp) else 0
        x = cur[i]
        if ftype == 0:
            v = x
        elif ftype == 1:
            v = x - a
        elif ftype == 2:
            v = x - b
        elif ftype == 3:
            v = x - ((a + b) >> 1)
        elif ftype == 4:
            v = x - paeth(a, b, c)
        else:
            raise ValueError("过滤器编号非法: %d" % ftype)
        out.append(v & 0xFF)
    return bytes(out)


def encode_scanlines(pixels, bpp, filters):
    """``pixels`` 是每行的原始像素字节,``filters`` 是每行的过滤器编号。"""
    blob = bytearray()
    prev = None
    for idx, row in enumerate(pixels):
        ftype = filters[idx]
        blob.append(ftype)
        blob += filter_row(ftype, row, prev, bpp)
        prev = row
    return bytes(blob)


def split_bytes(data, parts):
    if parts <= 1:
        return [data]
    step = (len(data) + parts - 1) // parts
    pieces = [data[i:i + step] for i in range(0, len(data), step)]
    return pieces if pieces else [b""]


def synth(width, height, color_type, scanlines, plte=None, trns=None,
          before_idat=(), after_idat=(), parts=1, level=6, bit_depth=8,
          interlace=0, compression=0, filter_method=0, ihdr_raw=None):
    """构造一个结构合法的 PNG(除刻意注入的缺陷外)。"""
    body = ihdr_raw if ihdr_raw is not None else ihdr_body(
        width, height, bit_depth=bit_depth, color_type=color_type,
        compression=compression, filter_method=filter_method,
        interlace=interlace)
    chunks = [mk_chunk("IHDR", body)]
    if plte is not None:
        chunks.append(mk_chunk("PLTE", plte))
    if trns is not None:
        chunks.append(mk_chunk("tRNS", trns))
    chunks.extend(before_idat)
    for piece in split_bytes(zlib.compress(scanlines, level), parts):
        chunks.append(mk_chunk("IDAT", piece))
    chunks.extend(after_idat)
    chunks.append(mk_chunk("IEND", b""))
    return mk_png(chunks)


def corrupt_crc(blob):
    """把某个 chunk 的 CRC 末字节取反(长度与类型名保持合法)。"""
    return blob[:-1] + bytes([blob[-1] ^ 0xFF])


# ---------------------------------------------------------------- 共用素材

PLTE2 = plte_of([(1, 2, 3), (4, 5, 6)])                       # 2 项
PLTE4 = plte_of([(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)])  # 4 项
PLTE8 = plte_of([(i * 3, i * 3 + 1, i * 3 + 2) for i in range(8)])  # 8 项

CT6_PIXELS = [b"\x01\x02\x03\x04\x05\x06\x07\x08",
              b"\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"]           # 2x2 RGBA


def ct6_scanlines():
    return encode_scanlines(CT6_PIXELS, 4, [0, 0])


def ct6_chunks():
    """2x2 RGBA 的三块 chunk:IHDR / IDAT / IEND。"""
    return [
        mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
        mk_chunk("IDAT", zlib.compress(ct6_scanlines())),
        mk_chunk("IEND", b""),
    ]


def ct6_png(**kwargs):
    return synth(2, 2, 6, ct6_scanlines(), **kwargs)


def ct3_png(pixels, plte=None, trns=None, **kwargs):
    plte = PLTE2 if plte is None else plte
    rows = [bytes(p) for p in pixels]
    scan = encode_scanlines(rows, 1, [0] * len(rows))
    return synth(len(rows[0]), len(rows), 3, scan, plte=plte, trns=trns, **kwargs)


class RejectCase(unittest.TestCase):
    def assertRejects(self, data, msg=None, expect=None):
        """断言以 ``PXL006`` 失败;``expect`` 给出时还要求报错文本含该片段。

        **为什么需要 ``expect``**:一份 fixture 往往能同时触发好几条规则,先命中的
        那条会遮住后命中的。只断言错误码,就可能出现"测的是 A 规则、实际被 B 规则
        接住"的假覆盖(§5.0.1/§5.0.2 的 6 个上界用例就踩过这个坑)。
        """
        with self.assertRaises(PxlError, msg=msg) as ctx:
            decode_png(data)
        self.assertEqual(ctx.exception.code, PXL006, msg)
        if expect is not None:
            self.assertIn(
                expect,
                ctx.exception.message,
                "%s: 报错文本应含 %r,实际 %r"
                % (msg or "拒绝用例", expect, ctx.exception.message),
            )
        return ctx.exception


# ---------------------------------------------------------------- 接受路径:§8.2 转换表

class TestColorTypes(unittest.TestCase):
    def test_ct0_no_trns(self):
        pixels = [b"\x00\x80\xff", b"\x01\x02\x03"]
        image = decode_png(synth(3, 2, 0, encode_scanlines(pixels, 1, [0, 0])))
        self.assertEqual((image.width, image.height), (3, 2))
        self.assertEqual(image.color_type, 0)
        self.assertEqual(image.bit_depth, 8)
        self.assertIsNone(image.plte)
        self.assertIsNone(image.trns)
        self.assertEqual(image.rows, [b"\x00\x80\xff", b"\x01\x02\x03"])
        self.assertEqual(to_rgba8(image), rgba_of([
            (0, 0, 0, 255), (128, 128, 128, 255), (255, 255, 255, 255),
            (1, 1, 1, 255), (2, 2, 2, 255), (3, 3, 3, 255),
        ]))

    def test_ct0_with_trns(self):
        pixels = [b"\x80\x07", b"\xff\x00"]
        image = decode_png(synth(2, 2, 0, encode_scanlines(pixels, 1, [0, 0]),
                                 trns=b"\x00\x80"))
        self.assertEqual(image.trns, b"\x00\x80")
        self.assertEqual(to_rgba8(image), rgba_of([
            (128, 128, 128, 0), (7, 7, 7, 255),
            (255, 255, 255, 255), (0, 0, 0, 255),
        ]))

    def test_ct0_trns_masks_high_bits(self):
        """§8.5:tRNS 先按样本深度掩掉高位再比较（PNG 第三版 §11.3.2.1）。

        样本深度 8 时只有低 8 位有效，因此 tRNS=0x0180 与 tRNS=0x0080 对样本 0x80
        都**命中**。早前这里按 16 位整值比较（0x0180 ≠ 0x80、不命中），与 PNG 标准冲突，
        规范已更正。
        """
        pixels = [b"\x80"]
        high = decode_png(synth(1, 1, 0, encode_scanlines(pixels, 1, [0]),
                                trns=b"\x01\x80"))
        self.assertEqual(rgba_of([(128, 128, 128, 0)]), to_rgba8(high))
        low = decode_png(synth(1, 1, 0, encode_scanlines(pixels, 1, [0]),
                               trns=b"\x00\x80"))
        self.assertEqual(rgba_of([(128, 128, 128, 0)]), to_rgba8(low))
        # 对照:未命中时 alpha=255
        miss = decode_png(synth(1, 1, 0, encode_scanlines([b"\x81"], 1, [0]),
                                trns=b"\x01\x80"))
        self.assertEqual(rgba_of([(129, 129, 129, 255)]), to_rgba8(miss))

    def test_ct2_no_trns(self):
        pixels = [b"\xff\x00\x00\x00\xff\x00", b"\x00\x00\xff\x01\x02\x03"]
        image = decode_png(synth(2, 2, 2, encode_scanlines(pixels, 3, [0, 0])))
        self.assertEqual(image.rows, pixels)
        self.assertEqual(to_rgba8(image), rgba_of([
            (255, 0, 0, 255), (0, 255, 0, 255),
            (0, 0, 255, 255), (1, 2, 3, 255),
        ]))

    def test_ct2_with_trns(self):
        pixels = [bytes([0x11, 0x22, 0x33, 0xff, 0xff, 0xff]),
                  bytes([0x01, 0x02, 0x03, 0x00, 0x00, 0x00])]
        image = decode_png(synth(2, 2, 2, encode_scanlines(pixels, 3, [0, 0]),
                                 trns=struct.pack(">HHH", 17, 34, 51)))
        self.assertEqual(image.trns, struct.pack(">HHH", 17, 34, 51))
        self.assertEqual(to_rgba8(image), rgba_of([
            (17, 34, 51, 0), (255, 255, 255, 255),
            (1, 2, 3, 255), (0, 0, 0, 255),
        ]))

    def test_ct3_no_trns(self):
        image = decode_png(ct3_png([b"\x00\x01", b"\x02\x03"], plte=PLTE4))
        self.assertEqual(image.plte, PLTE4)
        self.assertIsNone(image.trns)
        self.assertEqual(to_rgba8(image), rgba_of([
            (1, 2, 3, 255), (4, 5, 6, 255),
            (7, 8, 9, 255), (10, 11, 12, 255),
        ]))

    def test_ct3_with_trns(self):
        # PLTE 8 项、tRNS 只给前 2 项:索引 >= 2 时 alpha 为 255
        image = decode_png(ct3_png([b"\x00\x01\x02", b"\x07\x00\x01"],
                                   plte=PLTE8, trns=b"\x00\x80"))
        self.assertEqual(image.trns, b"\x00\x80")
        self.assertEqual(to_rgba8(image), rgba_of([
            (0, 1, 2, 0), (3, 4, 5, 128), (6, 7, 8, 255),
            (21, 22, 23, 255), (0, 1, 2, 0), (3, 4, 5, 128),
        ]))

    def test_ct4_gray_alpha(self):
        pixels = [b"\x00\xff\x80\x00", b"\xff\x40\x07\x07"]
        image = decode_png(synth(2, 2, 4, encode_scanlines(pixels, 2, [0, 0])))
        self.assertEqual(image.rows, pixels)
        self.assertEqual(to_rgba8(image), rgba_of([
            (0, 0, 0, 255), (128, 128, 128, 0),
            (255, 255, 255, 64), (7, 7, 7, 7),
        ]))

    def test_ct6_rgba(self):
        image = decode_png(ct6_png())
        self.assertEqual(image.rows, CT6_PIXELS)
        self.assertEqual(to_rgba8(image), b"".join(CT6_PIXELS))

    def test_ct6_rgb_nonzero_alpha_zero_kept(self):
        # §7:RGB 非零且 A=0 的像素原样保留,不得归一成 00000000
        pixels = [b"\xff\xff\xff\x00\x11\x22\x33\x00"]
        image = decode_png(synth(2, 1, 6, encode_scanlines(pixels, 4, [0])))
        self.assertEqual(to_rgba8(image), b"\xff\xff\xff\x00\x11\x22\x33\x00")

    def test_ct6_plte_allowed_but_ignored(self):
        plain = decode_png(ct6_png())
        with_plte = decode_png(ct6_png(before_idat=[mk_chunk("PLTE", PLTE8)]))
        self.assertEqual(with_plte.plte, PLTE8)
        self.assertEqual(to_rgba8(with_plte), to_rgba8(plain))

    def test_ct2_plte_allowed_but_ignored(self):
        pixels = [b"\xff\x00\x00\x00\xff\x00"]
        scan = encode_scanlines(pixels, 3, [0])
        plain = decode_png(synth(2, 1, 2, scan))
        with_plte = decode_png(synth(2, 1, 2, scan, plte=PLTE2))
        self.assertEqual(with_plte.plte, PLTE2)
        self.assertEqual(to_rgba8(with_plte), to_rgba8(plain))

    def test_ct3_plte_256_entries_and_index_255(self):
        plte = plte_of([(i, i, i) for i in range(256)])
        image = decode_png(ct3_png([b"\xff\x00"], plte=plte))
        self.assertEqual(len(image.plte), 768)
        self.assertEqual(to_rgba8(image), rgba_of([(255, 255, 255, 255), (0, 0, 0, 255)]))

    def test_ct3_trns_length_equal_to_plte_entries(self):
        image = decode_png(ct3_png([b"\x00\x01"], trns=b"\x00\xff"))
        self.assertEqual(to_rgba8(image), rgba_of([
            (1, 2, 3, 0), (4, 5, 6, 255),
        ]))


# ---------------------------------------------------------------- 逆过滤:过滤器 0..4

class TestUnfilter(unittest.TestCase):
    def _pixels(self, width, height):
        rows = []
        for y in range(height):
            row = bytearray()
            for x in range(width):
                row.extend([
                    (x * 37 + y * 11) & 0xFF,
                    (x * 5 + y * 71) & 0xFF,
                    (y * 23 + x * 3) & 0xFF,
                    (x * 13 + y * 29) & 0xFF,
                ])
            rows.append(bytes(row))
        return rows

    def test_each_filter_alone(self):
        rows = self._pixels(4, 5)
        for ftype in range(5):
            scan = encode_scanlines(rows, 4, [ftype] * 5)
            image = decode_png(synth(4, 5, 6, scan))
            self.assertEqual(image.rows, rows, "过滤器 %d" % ftype)
            self.assertEqual(to_rgba8(image), b"".join(rows), "过滤器 %d" % ftype)

    def test_mixed_filters(self):
        rows = self._pixels(4, 5)
        scan = encode_scanlines(rows, 4, [0, 1, 2, 3, 4])
        image = decode_png(synth(4, 5, 6, scan))
        self.assertEqual(image.rows, rows)
        self.assertEqual(to_rgba8(image), b"".join(rows))

    def test_rgb_stride_is_three(self):
        # bpp 取决于色彩类型:ct2 的 Sub/Average/Paeth 用 3 字节的回溯距离
        rows = self._pixels(3, 4)
        rows = [b"".join(row[i:i + 3] for i in range(0, len(row), 4)) for row in rows]
        scan = encode_scanlines(rows, 3, [1, 2, 3, 4])
        image = decode_png(synth(3, 4, 2, scan))
        self.assertEqual(image.rows, rows)


# ---------------------------------------------------------------- 未知 ancillary:必须接受

class TestAncillary(unittest.TestCase):
    def test_unknown_ancillary_accepted_and_ignored(self):
        base = ct6_png()
        before = [
            mk_chunk("tEXt", b"Comment\x00first"),
            mk_chunk("tEXt", b"Software\x00second"),
            mk_chunk("pHYs", struct.pack(">IIB", 2835, 2835, 1)),
            mk_chunk("pHYs", struct.pack(">IIB", 1000, 1000, 1)),
            mk_chunk("prVt", b"\x00\x01\x02"),                       # 未知 ancillary
            mk_chunk("iCCP", b"profile\x00\x00" + zlib.compress(b"x" * 16)),
            mk_chunk("zTXt", b"Raw profile type exif\x00\x00" + zlib.compress(b"{}")),
            mk_chunk("gAMA", struct.pack(">I", 45455)),
            mk_chunk("sRGB", b"\x00"),
            mk_chunk("bKGD", struct.pack(">HHH", 1, 2, 3)),
        ]
        after = [
            mk_chunk("tIME", struct.pack(">HBBBBB", 2026, 1, 1, 0, 0, 0)),
            mk_chunk("tEXt", b"Note\x00again"),
        ]
        rich = ct6_png(before_idat=before, after_idat=after)
        plain_image = decode_png(base)
        rich_image = decode_png(rich)
        self.assertEqual(rich_image.rows, CT6_PIXELS)
        self.assertEqual(to_rgba8(rich_image), to_rgba8(plain_image))
        self.assertEqual(to_rgba8(rich_image), b"".join(CT6_PIXELS))

    def test_multi_idat_chunks(self):
        image = decode_png(ct6_png(parts=3))
        self.assertEqual(image.rows, CT6_PIXELS)
        self.assertEqual(to_rgba8(image), b"".join(CT6_PIXELS))


# ---------------------------------------------------------------- 尺寸边界

class TestBounds(unittest.TestCase):
    def test_one_by_one_accepted(self):
        image = decode_png(synth(1, 1, 6, b"\x00\x01\x02\x03\x04"))
        self.assertEqual((image.width, image.height), (1, 1))
        self.assertEqual(to_rgba8(image), b"\x01\x02\x03\x04")

    def test_width_65536_accepted(self):
        scanlines = b"\x00" + bytes(65536)
        image = decode_png(synth(65536, 1, 0, scanlines))
        self.assertEqual((image.width, image.height), (65536, 1))
        self.assertEqual(len(image.rows), 1)
        self.assertEqual(image.rows[0], bytes(65536))

    def test_area_2_pow_22_accepted(self):
        # 2048x2048 恰好等于面积上界;只断言 decode 成功,不调 to_rgba8(那要 16 MiB)
        scanlines = (b"\x00" + bytes(2048)) * 2048
        image = decode_png(synth(2048, 2048, 0, scanlines))
        self.assertEqual((image.width, image.height), (2048, 2048))
        self.assertEqual(len(image.rows), 2048)
        self.assertEqual(image.rows[0], bytes(2048))


# ---------------------------------------------------------------- 拒绝:签名与 chunk 结构

class TestRejectStructure(RejectCase):
    def test_bad_signature(self):
        good = ct6_png()
        self.assertRejects(b"\x88" + good[1:], "签名首字节不符")

    def test_truncated_signature(self):
        self.assertRejects(SIGNATURE[:7], "只有 7 字节")

    def test_crc_ihdr(self):
        chunks = ct6_chunks()
        chunks[0] = corrupt_crc(chunks[0])
        self.assertRejects(mk_png(chunks), "IHDR CRC")

    def test_crc_idat(self):
        chunks = ct6_chunks()
        chunks[1] = corrupt_crc(chunks[1])
        self.assertRejects(mk_png(chunks), "IDAT CRC")

    def test_crc_iend(self):
        chunks = ct6_chunks()
        chunks[2] = corrupt_crc(chunks[2])
        self.assertRejects(mk_png(chunks), "IEND CRC")

    def test_crc_plte(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=3)),
                  corrupt_crc(mk_chunk("PLTE", PLTE2)),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x00\x01")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "PLTE CRC")

    def test_crc_trns(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=0)),
                  corrupt_crc(mk_chunk("tRNS", b"\x00\x01")),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x01\x02")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "tRNS CRC")

    def test_type_name_not_letters(self):
        # 三个子例在第 4 个字节上违反"4 个 ASCII 字母";它们各自都必须由类型名
        # 检查本身接住,所以连报错文本一起断言(否则被别的规则接住也算通过)。
        for name in (b"tE1t", b"tE t", b"tE\x80t"):
            with self.subTest(name=name):
                chunks = ct6_chunks()
                chunks.insert(1, mk_chunk(name, b""))
                self.assertRejects(mk_png(chunks), "类型名 %r 不是 4 个 ASCII 字母" % name,
                                   expect="chunk 类型名不是 4 个 ASCII 字母")

    def test_type_name_five_bytes(self):
        # 5 字节的"类型名"会让后续 chunk 布局整体移位,先命中「CRC 不符」是合理的;
        # 这条只钉"必须被拒",不要写成类型名规则覆盖。
        chunks = ct6_chunks()
        chunks.insert(1, mk_chunk(b"tEXt ", b""))
        self.assertRejects(mk_png(chunks), "5 字节类型名")

    def test_type_name_reserved_bit(self):
        chunks = ct6_chunks()
        chunks.insert(1, mk_chunk("tExt", b""))
        self.assertRejects(mk_png(chunks), "类型名第三字节小写")

    def test_chunk_length_over_2_pow_31(self):
        # 必须断言**报错文本**:把 MAX_CHUNK_LENGTH 放宽后,同一份输入会被
        # 「8 + len + 4 越出文件末尾」接住,只断错误码就测不出这条规则。
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  struct.pack(">I", 0x80000000) + b"tEXt" + struct.pack(">I", 0),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "长度字段 > 2^31-1",
                           expect="超过 2^31-1")

    def test_chunk_length_past_eof(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  struct.pack(">I", 100) + b"tEXt" + b"abcd",
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "8+len+4 越出文件")

    def test_ihdr_not_first(self):
        chunks = ct6_chunks()
        chunks.insert(0, mk_chunk("tEXt", b"k\x00v"))
        self.assertRejects(mk_png(chunks), "IHDR 不在第一位")

    def test_ihdr_twice(self):
        chunks = ct6_chunks()
        chunks.insert(1, mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)))
        self.assertRejects(mk_png(chunks), "两个 IHDR")

    def test_iend_not_last(self):
        self.assertRejects(mk_png(ct6_chunks() + [mk_chunk("tEXt", b"k\x00v")]),
                           "IEND 之后还有 chunk")

    def test_second_iend_is_rejected_as_trailing_bytes(self):
        """§8.5 的「IEND 恰好一个且排在最后」实际由两条规则合成。

        解析在**第一个** IEND 处停止,所以第二个 IEND 落在"IEND 之后不得有任何字节"
        这条上——名字必须照实写,否则读者会以为另有一条"IEND 只能出现一次"的检查。
        """
        self.assertRejects(mk_png(ct6_chunks() + [mk_chunk("IEND", b"")]),
                           "第二个 IEND", expect="IEND 之后还有")

    def test_iend_trailing_byte(self):
        self.assertRejects(ct6_png() + b"\x00", "IEND 之后还有字节")

    def test_iend_missing(self):
        self.assertRejects(mk_png(ct6_chunks()[:2]), "没有 IEND")

    def test_ihdr_length_not_13(self):
        body = struct.pack(">IIBBBB", 2, 2, 8, 6, 0, 0)          # 12 字节
        chunks = [mk_chunk("IHDR", body),
                  mk_chunk("IDAT", zlib.compress(ct6_scanlines())),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "IHDR 长度 12")

    def test_compression_method_not_zero(self):
        self.assertRejects(synth(2, 2, 6, ct6_scanlines(), compression=1))

    def test_filter_method_not_zero(self):
        self.assertRejects(synth(2, 2, 6, ct6_scanlines(), filter_method=1))

    def test_iend_length_not_zero(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", zlib.compress(ct6_scanlines())),
                  mk_chunk("IEND", b"\x00")]
        self.assertRejects(mk_png(chunks), "IEND 数据长度 1")

    def test_plte_after_idat(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=3)),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x00\x01")),
                  mk_chunk("PLTE", PLTE2),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "PLTE 晚于第一个 IDAT")

    def test_trns_after_idat(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=2)),
                  mk_chunk("IDAT", zlib.compress(b"\x00" + bytes(6))),
                  mk_chunk("tRNS", struct.pack(">HHH", 1, 2, 3)),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "tRNS 晚于第一个 IDAT")

    def test_trns_before_plte(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=3)),
                  mk_chunk("tRNS", b"\x00\x00"),
                  mk_chunk("PLTE", PLTE2),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x00\x01")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "tRNS 早于 PLTE")

    def test_idat_discontinuous(self):
        comp = zlib.compress(ct6_scanlines())
        pieces = split_bytes(comp, 2)
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", pieces[0]),
                  mk_chunk("tEXt", b"k\x00v"),
                  mk_chunk("IDAT", pieces[1]),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "IDAT 中间夹了 tEXt")

    def test_plte_twice(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=3)),
                  mk_chunk("PLTE", PLTE2),
                  mk_chunk("PLTE", PLTE2),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x00\x01")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "两个 PLTE")

    def test_trns_twice(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=0)),
                  mk_chunk("tRNS", b"\x00\x01"),
                  mk_chunk("tRNS", b"\x00\x01"),
                  mk_chunk("IDAT", zlib.compress(b"\x00\x01\x02")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "两个 tRNS")

    def test_unknown_critical_chunk(self):
        chunks = ct6_chunks()
        chunks.insert(1, mk_chunk("CrIt", b"\x00"))
        self.assertRejects(mk_png(chunks), "未知 critical chunk")


# ---------------------------------------------------------------- 拒绝:IHDR 取值

class TestRejectIhdr(RejectCase):
    def test_bit_depth_not_8(self):
        for depth in (1, 2, 4, 16):
            with self.subTest(depth=depth):
                self.assertRejects(synth(2, 2, 0, b"\x00\x00\x00\x00\x00\x00",
                                         bit_depth=depth), "位深 %d" % depth)

    def test_interlace(self):
        self.assertRejects(synth(2, 2, 6, ct6_scanlines(), interlace=1))

    def test_color_type_unsupported(self):
        for ct in (1, 5, 7):
            with self.subTest(color_type=ct):
                self.assertRejects(synth(2, 2, ct, ct6_scanlines()), "色彩类型 %d" % ct)

    # ---- 下面六个是 §5.0.1 / §5.0.2 的**输入侧**上界。每个 fixture 都必须带上
    # 一个合法的 IDAT:否则 `_check_skeleton` 会先因"缺少 IDAT"报错,把真正要测的
    # 规则整个遮住(这六个用例原先全是这样假通过的),因此同时断言报错文本。

    def _png_with_idat(self, width, height, color_type=6):
        """IHDR + 一段合法 IDAT + IEND;IDAT 内容不会被读到(上界先拒)。"""
        return mk_png([mk_chunk("IHDR", ihdr_body(width, height, color_type=color_type)),
                       mk_chunk("IDAT", zlib.compress(b"\x00" * 4, 9)),
                       mk_chunk("IEND", b"")])

    def test_width_zero(self):
        self.assertRejects(self._png_with_idat(0, 2), "width 0", expect="宽或高为 0")

    def test_height_zero(self):
        self.assertRejects(self._png_with_idat(2, 0), "height 0", expect="宽或高为 0")

    def test_width_too_large(self):
        self.assertRejects(self._png_with_idat(70000, 1, 0), "width 70000",
                           expect="超过 65536")

    def test_height_too_large(self):
        self.assertRejects(self._png_with_idat(1, 70000, 0), "height 70000",
                           expect="超过 65536")

    def test_area_just_over_limit(self):
        self.assertRejects(self._png_with_idat(2049, 2048, 0), "面积 2^22+2048",
                           expect="面积 4196352 超过上界 4194304")

    def test_area_32bit_wrap(self):
        # 65536x65536 用 32 位乘法会回绕成 0,必须靠宽整型判定为超限。
        self.assertRejects(self._png_with_idat(65536, 65536, 0), "面积 2^32",
                           expect="面积 4294967296 超过上界 4194304")


# ---------------------------------------------------------------- 拒绝:PLTE / tRNS

class TestRejectPalette(RejectCase):
    def test_ct3_without_plte(self):
        self.assertRejects(synth(2, 1, 3, b"\x00\x00\x01", plte=None))

    def test_ct0_with_plte(self):
        self.assertRejects(synth(2, 1, 0, b"\x00\x01\x02", plte=PLTE2))

    def test_ct4_with_plte(self):
        self.assertRejects(synth(2, 1, 4, b"\x00\x01\x02\x03", plte=PLTE2))

    def test_plte_length_not_multiple_of_three(self):
        self.assertRejects(ct3_png([b"\x00\x01"], plte=b"\x01\x02\x03\x04"))

    def test_plte_zero_entries(self):
        self.assertRejects(ct3_png([b"\x00\x01"], plte=b""))

    def test_plte_too_many_entries(self):
        self.assertRejects(ct3_png([b"\x00\x01"], plte=plte_of([(0, 0, 0)] * 257)))

    def test_ct3_index_out_of_range(self):
        self.assertRejects(ct3_png([b"\x00\x02"]), "索引 2 >= 2 项")

    def test_ct3_index_out_of_range_high(self):
        self.assertRejects(ct3_png([b"\xff"]), "索引 255 >= 2 项")

    def test_ct3_trns_longer_than_plte(self):
        self.assertRejects(ct3_png([b"\x00\x01"], trns=b"\x01\x02\x03"))

    def test_ct0_trns_length(self):
        self.assertRejects(synth(2, 1, 0, b"\x00\x01\x02", trns=b"\x00\x01\x02"))

    def test_ct2_trns_length(self):
        self.assertRejects(synth(2, 1, 2, b"\x00" + bytes(6), trns=b"\x00" * 5))

    def test_ct4_with_trns(self):
        self.assertRejects(synth(2, 1, 4, b"\x00\x01\x02\x03", trns=b"\x00\x01"))

    def test_ct6_with_trns(self):
        self.assertRejects(ct6_png(trns=b"\x00" * 6))


# ---------------------------------------------------------------- 拒绝:IDAT 与 zlib

class TestRejectIdat(RejectCase):
    def test_idat_missing(self):
        self.assertRejects(mk_png([mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                                   mk_chunk("IEND", b"")]))

    def test_zlib_stream_truncated(self):
        comp = zlib.compress(ct6_scanlines())
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", comp[:-6]),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "流提前结束")

    def test_zlib_missing_adler_only(self):
        """只砍掉 Adler-32 尾巴:解压出**恰好正确**的字节数,外层长度比较抓不到。

        这是「流提前结束」那条守卫唯一的独立证据——上面那条砍 6 字节会先把解压
        长度改短,于是被 `decode_png` 的长度比较抢先,`eof` 检查从未被走到。
        """
        comp = zlib.compress(ct6_scanlines())
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", comp[:-4]),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "只缺 Adler-32", expect="提前结束")

    def test_zlib_trailing_bytes(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", zlib.compress(ct6_scanlines()) + b"\x00\x00"),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "流后有多余字节")

    def test_zlib_second_stream(self):
        comp = zlib.compress(ct6_scanlines())
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", comp + comp),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "两个 zlib 流")

    def test_zlib_adler_mismatch(self):
        comp = zlib.compress(ct6_scanlines())
        broken = comp[:-1] + bytes([comp[-1] ^ 0xFF])
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", broken),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "Adler-32 不符")

    def test_zlib_bad_header(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", b"\x00\x00\x00\x00\x00"),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "zlib 头非法")

    def test_decompressed_too_short(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", zlib.compress(ct6_scanlines()[:-1])),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "解压后少一字节")

    def test_decompressed_too_long(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 2, color_type=6)),
                  mk_chunk("IDAT", zlib.compress(ct6_scanlines() + b"\x00")),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "解压后多一字节")

    def test_filter_byte_out_of_range(self):
        chunks = [mk_chunk("IHDR", ihdr_body(2, 1, color_type=6)),
                  mk_chunk("IDAT", zlib.compress(b"\x05" + bytes(8))),
                  mk_chunk("IEND", b"")]
        self.assertRejects(mk_png(chunks), "过滤器 5")


# ---------------------------------------------------------------- to_rgba8 防御

class TestInflateIsBounded(unittest.TestCase):
    """解压必须有上界:期望长度 ``height*(1+stride)`` 在解压前就能由 IHDR 算出。"""

    def _bomb(self, declared_width=1, declared_height=1, megabytes=8):
        payload = b"\x00" * (megabytes * 1024 * 1024)
        blob = zlib.compress(payload, 9)
        return mk_png(
            [
                mk_chunk(b"IHDR", ihdr_body(declared_width, declared_height, color_type=6)),
                mk_chunk(b"IDAT", blob),
                mk_chunk(b"IEND", b""),
            ]
        )

    def test_huge_stream_is_rejected_without_buffering_it(self):
        png = self._bomb(megabytes=8)
        tracemalloc.start()
        try:
            with self.assertRaises(PxlError) as caught:
                decode_png(png)
        finally:
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        self.assertEqual(PXL006, caught.exception.code)
        # 若先把 8 MB 膨胀结果收进内存再比大小,峰值不可能低于 8 MB。
        self.assertLess(
            peak,
            2 * 1024 * 1024,
            "解压结果被整体缓冲了:峰值 %.1f MB" % (peak / 1048576.0),
        )

    def test_one_byte_over_expect_is_rejected(self):
        # 2x2 ct0 期望 2*(1+2) = 6 字节;给 7 字节必须拒绝。
        blob = zlib.compress(b"\x00" * 7, 9)
        png = mk_png(
            [
                mk_chunk(b"IHDR", ihdr_body(2, 2, color_type=0)),
                mk_chunk(b"IDAT", blob),
                mk_chunk(b"IEND", b""),
            ]
        )
        with self.assertRaises(PxlError) as caught:
            decode_png(png)
        self.assertEqual(PXL006, caught.exception.code)

    def test_exact_expect_is_accepted(self):
        blob = zlib.compress(b"\x00" * 6, 9)
        png = mk_png(
            [
                mk_chunk(b"IHDR", ihdr_body(2, 2, color_type=0)),
                mk_chunk(b"IDAT", blob),
                mk_chunk(b"IEND", b""),
            ]
        )
        image = decode_png(png)
        self.assertEqual(2, image.height)
        self.assertEqual(2, len(image.rows[0]))


class TestRgbaGuards(unittest.TestCase):
    def test_palette_index_out_of_range(self):
        image = PngImage(width=1, height=1, color_type=3, bit_depth=8,
                         plte=b"\x01\x02\x03", trns=None, rows=[b"\x01"])
        with self.assertRaises(PxlError) as ctx:
            to_rgba8(image)
        self.assertEqual(ctx.exception.code, PXL006)


# ---------------------------------------------------------------- sha256_hex

class TestSha256Hex(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(
            sha256_hex(b"\x00"),
            "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d")

    def test_shape(self):
        digest = sha256_hex(rgba_of([(0, 0, 0, 0)]))
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())
        self.assertFalse(digest.startswith("sha256"))
        self.assertNotIn("\n", digest)


if __name__ == "__main__":
    unittest.main()
