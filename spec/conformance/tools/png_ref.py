#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""极简独立 PNG 读取器 —— 只给 `gen_synthetic.py --verify` 用。

刻意做成与 `importer/` **完全无共享代码**的第二实现:它只覆盖本语料写出的形态
(8 bit、非交错、色彩类型 0/2/3/4/6),不做 §8.5 的完整合法性判定(那是被测实现
`importer` 的职责,重在这里会变成"让被测代码给自己出题")。

它校验签名与每个 chunk 的 CRC、解析 IHDR/PLTE/tRNS/IDAT、`zlib.decompress`,
实现全部 5 种行过滤器,并按 §8.2 的转换表展开成 RGBA8。
"""

import binascii
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: 8 bit、非交错时每像素字节数
BYTES_PER_PIXEL = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PngRefError(Exception):
    """读取失败(只用于 fixture 自检,不映射 PXL 错误码)。"""


def _unfilter(raw, height, stride, bpp):
    """把带过滤器字节的原始流还原成每行的像素字节(5 种过滤器都实现)。"""
    if len(raw) != height * (1 + stride):
        raise PngRefError("解压后长度 %d != %d" % (len(raw), height * (1 + stride)))
    rows = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
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
                up = prev[i]
                upleft = prev[i - bpp] if i >= bpp else 0
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                if pa <= pb and pa <= pc:
                    pred = left
                elif pb <= pc:
                    pred = up
                else:
                    pred = upleft
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise PngRefError("非法过滤器字节 %d" % ftype)
        rows.append(bytes(line))
        prev = line
    return rows


def decode_rgba8(data):
    """解码 PNG 字节串,返回 ``(width, height, RGBA8 字节流)``。"""
    if data[:8] != SIGNATURE:
        raise PngRefError("PNG 签名不符")
    pos = 8
    ihdr = None
    plte = None
    trns = None
    idat = []
    ended = False
    while pos < len(data):
        if pos + 8 > len(data):
            raise PngRefError("chunk 头部越界")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise PngRefError("chunk 数据越界: %r" % (ctype,))
        body = data[body_start:body_end]
        crc = struct.unpack(">I", data[body_end:body_end + 4])[0]
        if (binascii.crc32(ctype + body) & 0xffffffff) != crc:
            raise PngRefError("chunk CRC 不符: %r" % (ctype,))
        pos = body_end + 4
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            plte = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            ended = True
            break
    if ihdr is None:
        raise PngRefError("缺少 IHDR")
    if not ended:
        raise PngRefError("缺少 IEND")
    width, height, depth, color_type, comp, filt, interlace = ihdr
    if depth != 8 or comp != 0 or filt != 0 or interlace != 0:
        raise PngRefError("本读取器只支持 8 bit 非交错")
    if color_type not in BYTES_PER_PIXEL:
        raise PngRefError("不支持的色彩类型 %d" % color_type)
    bpp = BYTES_PER_PIXEL[color_type]
    row_bytes = bpp * width
    rows = _unfilter(zlib.decompress(b"".join(idat)), height, row_bytes, bpp)

    out = bytearray()
    if color_type == 0:
        # PNG 第三版 §11.3.2.1：位深不足 16 时只有低 depth 位参与比较。
        mask = (1 << depth) - 1
        key = None if trns is None else ((trns[0] << 8 | trns[1]) & mask)
        for line in rows:
            for sample in line:
                alpha = 0 if sample == key else 255
                out += bytes((sample, sample, sample, alpha))
    elif color_type == 2:
        key = None
        if trns is not None:
            mask = (1 << depth) - 1
            key = tuple(value & mask for value in (
                trns[0] << 8 | trns[1], trns[2] << 8 | trns[3], trns[4] << 8 | trns[5]))
        for line in rows:
            for i in range(0, len(line), 3):
                r, g, b = line[i], line[i + 1], line[i + 2]
                alpha = 0 if key is not None and (r, g, b) == key else 255
                out += bytes((r, g, b, alpha))
    elif color_type == 3:
        if plte is None:
            raise PngRefError("色彩类型 3 缺少 PLTE")
        entry_count = len(plte) // 3
        for line in rows:
            for index in line:
                if index >= entry_count:
                    raise PngRefError("索引 %d 越过 PLTE 项数 %d" % (index, entry_count))
                base = index * 3
                alpha = trns[index] if trns is not None and index < len(trns) else 255
                out += bytes((plte[base], plte[base + 1], plte[base + 2], alpha))
    elif color_type == 4:
        for line in rows:
            for i in range(0, len(line), 2):
                gray, alpha = line[i], line[i + 1]
                out += bytes((gray, gray, gray, alpha))
    else:  # color_type == 6
        for line in rows:
            out += line
    return width, height, bytes(out)
