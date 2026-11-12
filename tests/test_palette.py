"""调色板分配与档位派生的单测(规范 §4.4、§4.5、§7)。"""

import unittest

from importer.errors import PXL005, PxlError
from importer.palette import assign_slots, derive_encoding

RED = b"\xff\x00\x00\xff"
GREEN = b"\x00\xff\x00\xff"
BLUE = b"\x00\x00\xff\xff"
WHITE_ALPHA0 = b"\xff\xff\xff\x00"
CLEAR = b"\x00\x00\x00\x00"


def flatten(pixels):
    out = bytearray()
    for pixel in pixels:
        out.extend(pixel)
    return bytes(out)


class AssignSlotsTest(unittest.TestCase):
    def test_row_major_first_appearance_order(self):
        # 3x3:
        #   A . A
        #   B B .
        #   A C D
        pixels = [
            RED,
            CLEAR,
            RED,
            GREEN,
            GREEN,
            CLEAR,
            RED,
            BLUE,
            WHITE_ALPHA0,
        ]
        slots, palette = assign_slots(flatten(pixels), 3, 3)
        self.assertEqual([1, 0, 1, 2, 2, 0, 1, 3, 4], slots)
        self.assertEqual(
            ["FF0000FF", "00FF00FF", "0000FFFF", "FFFFFF00"], palette
        )

    def test_exact_zero_quad_is_the_only_transparent_pixel(self):
        # §7:只有四通道全零归入 slot 0;FFFFFF00 必须占自己的 slot。
        slots, palette = assign_slots(flatten([CLEAR, WHITE_ALPHA0]), 2, 1)
        self.assertEqual([0, 1], slots)
        self.assertEqual(["FFFFFF00"], palette)

    def test_all_transparent_image_has_empty_palette(self):
        slots, palette = assign_slots(flatten([CLEAR, CLEAR, CLEAR, CLEAR]), 2, 2)
        self.assertEqual([0, 0, 0, 0], slots)
        self.assertEqual([], palette)

    def test_same_color_twice_gets_one_slot(self):
        slots, palette = assign_slots(flatten([RED, RED, RED]), 3, 1)
        self.assertEqual([1, 1, 1], slots)
        self.assertEqual(["FF0000FF"], palette)

    def test_palette_entries_are_uppercase_eight_hex_digits(self):
        pixels = [b"\xab\xcd\xef\x01", b"\x00\x0a\xb0\xc0"]
        _slots, palette = assign_slots(flatten(pixels), 2, 1)
        self.assertEqual(["ABCDEF01", "000AB0C0"], palette)
        for entry in palette:
            self.assertEqual(8, len(entry))
            self.assertEqual(entry.upper(), entry)

    def test_palette_length_equals_distinct_opaque_colors(self):
        pixels = [RED, GREEN, RED, BLUE, GREEN, WHITE_ALPHA0]
        _slots, palette = assign_slots(flatten(pixels), 3, 2)
        self.assertEqual(4, len(palette))

    def test_length_mismatch_raises_value_error(self):
        with self.assertRaises(ValueError):
            assign_slots(flatten([RED, GREEN, BLUE]), 2, 2)


class DeriveEncodingTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual("char", derive_encoding(0))
        self.assertEqual("char", derive_encoding(1))
        self.assertEqual("char", derive_encoding(60))
        self.assertEqual("char", derive_encoding(61))
        self.assertEqual("index", derive_encoding(62))
        self.assertEqual("index", derive_encoding(255))
        self.assertEqual("index4", derive_encoding(256))
        self.assertEqual("index4", derive_encoding(65535))

    def test_capacity_over_limit_raises_pxl005(self):
        # §4.5:n >= 65536 时没有档位可派生(四位十六进制共 65536 个值,
        # 其中 0000 保留给透明 slot)。
        for count in (65536, 65537, 100000):
            with self.subTest(count=count):
                with self.assertRaises(PxlError) as caught:
                    derive_encoding(count)
                self.assertEqual(PXL005, caught.exception.code)


class CapacityGuardTest(unittest.TestCase):
    """容量在见到第 65536 个不同颜色时就已判死,不必扫完剩余像素。"""

    @staticmethod
    def _distinct(count):
        buf = bytearray()
        for index in range(count):
            buf += bytes(((index >> 16) & 0xFF, (index >> 8) & 0xFF, index & 0xFF, 0xFF))
        return bytes(buf)

    def test_over_capacity_is_rejected(self):
        # 256x256 = 65536 个互不相同的非透明色。
        with self.assertRaises(PxlError) as caught:
            assign_slots(self._distinct(65536), 256, 256)
        self.assertEqual(PXL005, caught.exception.code)

    def test_over_capacity_on_a_large_image_is_rejected(self):
        # 512x512、前 65536 个像素互异、其余全同。容量在第 65536 个不同色处就已判死,
        # 报错文本会写明"在第 N 个像素处确认"——N=65535 远小于总像素 262144,
        # 那就是"没有扫完整个图"的可判定证据(只断言错误码测不出这一点)。
        width, height = 512, 512
        head = self._distinct(65536)
        tail = b"\x01\x02\x03\xff" * (width * height - 65536)
        self.assertEqual(width * height * 4, len(head) + len(tail))
        with self.assertRaises(PxlError) as caught:
            assign_slots(head + tail, width, height)
        self.assertEqual(PXL005, caught.exception.code)
        self.assertIn(
            "在第 65535 个像素处确认",
            caught.exception.message,
            "拒绝点不在第 65536 个不同色处,说明没有早退:%r" % (caught.exception.message,),
        )

    def test_exactly_65535_colors_is_accepted(self):
        count = 65535
        slots, palette = assign_slots(self._distinct(count), count, 1)
        self.assertEqual(count, len(palette))
        self.assertEqual(65535, slots[-1])
        self.assertEqual("index4", derive_encoding(len(palette)))


if __name__ == "__main__":
    unittest.main()
