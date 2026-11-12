"""RGBA8 展开(规范 §8.2)与像素流摘要(§13.4)。

只做字节搬运:不做色彩空间变换,不解释 ``iCCP``(§10)。
七个分支与 §8.2 的转换表逐行对应;ct0 / ct2 的 ``tRNS`` 值按大端读出后与
8 bit 样本比较,命中则 alpha = 0 而 RGB 原样保留(§8.5)。
"""

import hashlib
import struct

from .errors import PXL006, PxlError

def to_rgba8(image):
    """按 §8.2 把 ``image`` 展开成 ``width*height*4`` 字节,顺序 R,G,B,A。"""
    color_type = image.color_type
    rows = image.rows

    if color_type == 6:
        return b"".join(rows)

    out = bytearray()

    if color_type == 0:
        # tRNS 是 2 字节大端。**先按样本深度掩掉高位再比**:PNG 第三版 §11.3.2.1 规定
        # 位深不足 16 时只有低 depth 位有效,所以 8 bit 下 `tRNS = 0x0180` 命中样本 0x80
        # (`0x0180 & 0xFF == 0x80`)。早前这里按 16 位整值比较,与 PNG 标准不符,
        # 规范 §8.5 已同步更正。
        mask = (1 << image.bit_depth) - 1
        key = (struct.unpack(">H", image.trns)[0] & mask) if image.trns is not None else None
        for row in rows:
            for sample in row:
                out.append(sample)
                out.append(sample)
                out.append(sample)
                out.append(0 if sample == key else 255)
        return bytes(out)

    if color_type == 2:
        mask = (1 << image.bit_depth) - 1
        key = (tuple(value & mask for value in struct.unpack(">HHH", image.trns))
               if image.trns is not None else None)
        for row in rows:
            for i in range(0, len(row), 3):
                r = row[i]
                g = row[i + 1]
                b = row[i + 2]
                out.append(r)
                out.append(g)
                out.append(b)
                out.append(0 if key is not None and (r, g, b) == key else 255)
        return bytes(out)

    if color_type == 3:
        plte = image.plte
        if plte is None:
            raise PxlError(PXL006, "色彩类型 3 缺少 PLTE")
        entries = len(plte) // 3
        trns = image.trns if image.trns is not None else b""
        for row in rows:
            for index in row:
                if index >= entries:
                    raise PxlError(PXL006, "调色板索引 %d 越出 PLTE 的 %d 项"
                                   % (index, entries))
                base = index * 3
                out.append(plte[base])
                out.append(plte[base + 1])
                out.append(plte[base + 2])
                out.append(trns[index] if index < len(trns) else 255)
        return bytes(out)

    if color_type == 4:
        for row in rows:
            for i in range(0, len(row), 2):
                gray = row[i]
                out.append(gray)
                out.append(gray)
                out.append(gray)
                out.append(row[i + 1])
        return bytes(out)

    raise PxlError(PXL006, "色彩类型 %d 不受支持" % color_type)


def sha256_hex(payload):
    """小写十六进制 SHA-256,无前缀、无换行。"""
    return hashlib.sha256(payload).hexdigest()
