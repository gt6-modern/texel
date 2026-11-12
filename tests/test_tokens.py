"""token 编码的单测(规范 §4.1–§4.3)。

抽取器只**编码** token;解码侧由渲染器(Java)与独立参考渲染器
(`spec/conformance/tools/pxl_render_ref.py`)各自实现,本仓不再保留第三份。
"""

import unittest

from importer.errors import PXL003, PXL005, PxlError
from importer.tokens import (
    CHAR_ALPHABET,
    TIER_LIMIT,
    TRANSPARENT_TOKEN,
    encode_token,
)


class CharAlphabetTest(unittest.TestCase):
    def test_length_is_sixty_one(self):
        self.assertEqual(61, len(CHAR_ALPHABET))

    def test_contents_follow_digits_upper_then_a_to_y(self):
        self.assertEqual("0123456789", CHAR_ALPHABET[:10])
        self.assertEqual("ABC", CHAR_ALPHABET[10:13])
        self.assertEqual("Z", CHAR_ALPHABET[35])
        self.assertEqual("a", CHAR_ALPHABET[36])
        self.assertEqual("y", CHAR_ALPHABET[60])

    def test_z_is_reserved_and_not_in_alphabet(self):
        self.assertNotIn("z", CHAR_ALPHABET)
        self.assertNotIn(".", CHAR_ALPHABET)

    def test_all_characters_unique(self):
        self.assertEqual(61, len(set(CHAR_ALPHABET)))


class EncodeTokenTest(unittest.TestCase):
    def test_char_tier_edges(self):
        self.assertEqual("0", encode_token("char", 1))
        self.assertEqual("9", encode_token("char", 10))
        self.assertEqual("A", encode_token("char", 11))
        self.assertEqual("y", encode_token("char", 61))

    def test_index_tier_is_uppercase_zero_padded(self):
        self.assertEqual("01", encode_token("index", 1))
        self.assertEqual("0A", encode_token("index", 10))
        self.assertEqual("FF", encode_token("index", 255))

    def test_index4_tier_is_uppercase_zero_padded(self):
        self.assertEqual("0001", encode_token("index4", 1))
        self.assertEqual("012C", encode_token("index4", 300))
        self.assertEqual("FFFF", encode_token("index4", 65535))

    def test_slot_zero_is_the_transparent_token(self):
        for encoding, token in TRANSPARENT_TOKEN.items():
            self.assertEqual(token, encode_token(encoding, 0))

    def test_out_of_range_raises_pxl005(self):
        for encoding, limit in TIER_LIMIT.items():
            with self.subTest(encoding=encoding):
                with self.assertRaises(PxlError) as caught:
                    encode_token(encoding, limit + 1)
                self.assertEqual(PXL005, caught.exception.code)

    def test_unknown_encoding_raises_pxl003(self):
        with self.assertRaises(PxlError) as caught:
            encode_token("hex", 1)
        self.assertEqual(PXL003, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
