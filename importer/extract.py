"""抽取器编排:PNG 字节 → 规范形式 PXL 文本(规范 §4.5 / §7 / 附录 A)。

流水线:

    bytes → decode_png (§8)  → PngImage
          → to_rgba8  (§8.2) → RGBA8 流
          → assign_slots (§4.4/§7) → slot 网格 + palette
          → derive_encoding (§4.5) → encoding
          → build_ops / raw_ops (附录 A)  → ops
          → serialize (§3.7) → 文本

拒绝一律发生在**产出任何文本之前**(§8.3):本模块只有在全部校验通过后才拼字符串。
"""

from .compress import build_ops, raw_ops
from .palette import assign_slots, derive_encoding
from .png import decode_png
from .rgba import to_rgba8
from .serialize import serialize


def extract_png(data, raw=False):
    """把一份 PNG 字节抽成规范形式 PXL 文本(末尾恰一个 LF)。

    ``raw=True`` 时不走附录 A 的压缩,输出整图一条 ``grid``(逐像素基准)。
    两条路径的渲染结果必须逐像素相同——这是 `AGENTS.md` 契约 5 的判定方式。
    """
    image = decode_png(data)
    rgba = to_rgba8(image)
    slots, palette = assign_slots(rgba, image.width, image.height)
    encoding = derive_encoding(len(palette))
    if raw:
        ops = raw_ops(slots, image.width, image.height, encoding)
    else:
        ops = build_ops(slots, image.width, image.height, encoding)
    return serialize(image.width, image.height, encoding, palette, ops)


def extract_file(path, raw=False):
    """``extract_png`` 的文件版;只读字节,不解释路径。"""
    with open(str(path), "rb") as handle:
        return extract_png(handle.read(), raw=raw)
