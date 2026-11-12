"""调色板分配与档位派生(规范 §4.4、§4.5、§7)。

两个硬约束:

* **透明只看四通道全零**:只有精确 ``00000000`` 归入 slot 0;``FFFFFF00`` 这类
  "RGB 非零而 A=0" 的像素必须占一个自己的 slot(§7)。
* **档位由 ``palette`` 长度唯一决定**,不"选择"、不"猜"(§4.5)。
"""

from .errors import PXL005, PxlError
from .tokens import TIER_LIMIT

#: 透明 slot 的 texel 值(§2 / §7)。
TRANSPARENT_PIXEL = b"\x00\x00\x00\x00"

#: 非透明色数的硬上限,直接取自 §4.1 的档位表(避免同一组数字有两处真源)。
MAX_COLORS = TIER_LIMIT["index4"]

_TIER_BY_LENGTH = (
    (TIER_LIMIT["char"], "char"),
    (TIER_LIMIT["index"], "index"),
    (MAX_COLORS, "index4"),
)


def assign_slots(rgba, width, height):
    """按 §4.4 行优先首次出现顺序分配 slot。

    返回 ``(slots, palette)``:``slots`` 是长度 ``width*height`` 的行优先扁平表,
    值为 slot 编号(0 = 透明);``palette`` 是 8 位大写十六进制字符串列表,
    ``palette[i]`` 对应 slot ``i+1``。
    """
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(
            "RGBA 长度 %d 与 %dx%d 不符(应为 %d)" % (len(rgba), width, height, expected)
        )

    slots = [0] * (width * height)
    seen = {}
    palette = []
    for index in range(width * height):
        offset = index * 4
        key = bytes(rgba[offset : offset + 4])
        if key == TRANSPARENT_PIXEL:
            continue
        slot = seen.get(key)
        if slot is None:
            # 容量在见到第 65536 个不同颜色时就已判死,不必扫完剩下的像素:
            # 按面积上界线性外推,1024x1024 全异色要白算 16 秒、占 180 MB 才
            # 由 derive_encoding 拒绝。语义完全等价,只是拒绝得早。
            if len(palette) >= MAX_COLORS:
                raise PxlError(
                    PXL005,
                    "非透明色数已超过 index4 容量 %d(在第 %d 个像素处确认)"
                    % (MAX_COLORS, index))
            slot = len(palette) + 1
            seen[key] = slot
            palette.append("%02X%02X%02X%02X" % (key[0], key[1], key[2], key[3]))
        slots[index] = slot
    return slots, palette


def derive_encoding(count):
    """由 ``palette`` 长度派生 ``encoding``(§4.5)。

    ``n >= 65536`` 时没有任何档位可派生(四位十六进制的 65536 个值里有一个
    已被透明占用),报 ``PXL005``。
    """
    for limit, name in _TIER_BY_LENGTH:
        if count <= limit:
            return name
    raise PxlError(
        PXL005,
        "非透明色数 %d 超过 index4 容量 65535(四位十六进制共 65536 个值,"
        "其中 0000 保留给透明 slot)" % count,
    )
