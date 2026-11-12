#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""确定性 PNG 写出器(fixture 侧工具,仅标准库)。

契约与约束(见 spec/PXL.md(历史记录,标题用重命名前的旧名)与 spec/PXL.md §10):

  * 8 bit/通道、非交错(interlace=0)、compression 方法 0、filter 方法 0;
  * 行过滤器恒为 0:每行先补一个 0x00 过滤器字节,再整块 `zlib.compress(payload, 9)` 成单块 IDAT;
  * CRC = `binascii.crc32(type + data) & 0xffffffff`,按 4 字节大端写出;
  * chunk 顺序:签名 → IHDR → PLTE? → tRNS? → extra_chunks* → IDAT → IEND;
  * 完全确定性:不读时钟、不用随机、不依赖 locale 或目录枚举顺序(§9)。

本文件**不 import** `importer/` 的任何东西:fixture 不得与被测实现共享代码。
"""

import binascii
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: 8 bit 样本、非交错时每像素占用的字节数(色彩类型 → 因子)
BYTES_PER_PIXEL = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def row_stride(color_type, width):
    """每行原始像素字节数(**不含**过滤器字节)。"""
    if color_type not in BYTES_PER_PIXEL:
        raise ValueError("不支持的色彩类型: %r" % (color_type,))
    return BYTES_PER_PIXEL[color_type] * width


def _chunk(chunk_type, data):
    if len(chunk_type) != 4:
        raise ValueError("chunk 类型名必须恰为 4 字节: %r" % (chunk_type,))
    crc = binascii.crc32(chunk_type + data) & 0xffffffff
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png(width, height, color_type, rows, plte=None, trns=None,
               extra_chunks=()):
    """把已打包好的行数据编码成完整 PNG 字节串(内存版,供测试与工具用)。

    ``rows`` 是每行的**原始像素字节**(不含过滤字节),长度为 ``row_stride()``;
    ``extra_chunks`` 是 ``[(类型名 bytes, 数据 bytes), ...]``,插在 IDAT 之前。
    """
    if width < 1 or height < 1:
        raise ValueError("宽高必须 >= 1: %dx%d" % (width, height))
    stride = row_stride(color_type, width)
    packed_rows = [bytes(row) for row in rows]
    if len(packed_rows) != height:
        raise ValueError("rows 行数 %d != height %d" % (len(packed_rows), height))
    for index, row in enumerate(packed_rows):
        if len(row) != stride:
            raise ValueError("第 %d 行长度 %d != stride %d" % (index, len(row), stride))

    parts = [PNG_SIGNATURE]
    # IHDR:宽、高、8 bit、色彩类型、compression=0、filter=0、interlace=0
    parts.append(_chunk(b"IHDR",
                        struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)))
    if plte is not None:
        plte = bytes(plte)
        if len(plte) % 3 or not 3 <= len(plte) <= 768:
            raise ValueError("PLTE 长度非法: %d" % len(plte))
        parts.append(_chunk(b"PLTE", plte))
    if trns is not None:
        trns = bytes(trns)
        if not trns:
            raise ValueError("tRNS 不得为空")
        parts.append(_chunk(b"tRNS", trns))
    for chunk_type, chunk_data in extra_chunks:
        parts.append(_chunk(bytes(chunk_type), bytes(chunk_data)))

    # 行过滤器恒为 0(None),IDAT 单块、压缩级别固定 9
    payload = b"".join(b"\x00" + row for row in packed_rows)
    parts.append(_chunk(b"IDAT", zlib.compress(payload, 9)))
    parts.append(_chunk(b"IEND", b""))

    return b"".join(parts)
