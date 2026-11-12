"""规范序列化的单测(规范 §3.7)。

权威 oracle 是规范 §3.5 自己给出的 4×4 完整文档;它逐字节抄在下面,因为那是
"规范形式"唯一的逐字定义。其余用例都围绕它的排版规则展开。
"""

import unittest

from importer.serialize import serialize
from pxl_oracle import CLEAR_PIXEL, render_pxl_text

RED = bytes.fromhex("FF0000FF")
GREEN = bytes.fromhex("00FF00FF")

#: 规范 §3.5(第 134–164 行)给出的完整规范形式文档。
SPEC_3_5 = "\n".join(
    [
        "{",
        '  "format": "PXL",',
        '  "version": 1,',
        '  "encoding": "char",',
        '  "width": 4,',
        '  "height": 4,',
        '  "palette": [',
        '    "FF0000FF",',
        '    "00FF00FF"',
        "  ],",
        '  "layers": [',
        "    {",
        '      "name": "base",',
        '      "blend": "normal",',
        '      "ops": [',
        "        {",
        '          "op": "grid",',
        '          "x": 0,',
        '          "y": 0,',
        '          "grid": [',
        '            "0..0",',
        '            ".11.",',
        '            ".11.",',
        '            "0..0"',
        "          ]",
        "        }",
        "      ]",
        "    }",
        "  ]",
        "}",
    ]
) + "\n"

EXPECTED_1X1_TRANSPARENT = "\n".join(
    [
        "{",
        '  "format": "PXL",',
        '  "version": 1,',
        '  "encoding": "char",',
        '  "width": 1,',
        '  "height": 1,',
        '  "palette": [],',
        '  "layers": [',
        "    {",
        '      "name": "base",',
        '      "blend": "normal",',
        '      "ops": [',
        "        {",
        '          "op": "fill",',
        '          "slot": 0',
        "        }",
        "      ]",
        "    }",
        "  ]",
        "}",
    ]
) + "\n"

EXPECTED_1X1_OPAQUE = "\n".join(
    [
        "{",
        '  "format": "PXL",',
        '  "version": 1,',
        '  "encoding": "char",',
        '  "width": 1,',
        '  "height": 1,',
        '  "palette": [',
        '    "FF0000FF"',
        "  ],",
        '  "layers": [',
        "    {",
        '      "name": "base",',
        '      "blend": "normal",',
        '      "ops": [',
        "        {",
        '          "op": "fill",',
        '          "slot": 1',
        "        }",
        "      ]",
        "    }",
        "  ]",
        "}",
    ]
) + "\n"


class SpecExampleTest(unittest.TestCase):
    def test_spec_3_5_is_reproduced_byte_for_byte(self):
        text = serialize(
            4,
            4,
            "char",
            ["FF0000FF", "00FF00FF"],
            [{"op": "grid", "x": 0, "y": 0, "grid": ["0..0", ".11.", ".11.", "0..0"]}],
        )
        self.assertEqual(SPEC_3_5, text)

    def test_spec_3_5_renders_back_to_its_pixels(self):
        width, height, pixels = render_pxl_text(SPEC_3_5)
        self.assertEqual((4, 4), (width, height))
        expected = [
            RED,
            CLEAR_PIXEL,
            CLEAR_PIXEL,
            RED,
            CLEAR_PIXEL,
            GREEN,
            GREEN,
            CLEAR_PIXEL,
            CLEAR_PIXEL,
            GREEN,
            GREEN,
            CLEAR_PIXEL,
            RED,
            CLEAR_PIXEL,
            CLEAR_PIXEL,
            RED,
        ]
        actual = [pixels[i * 4 : i * 4 + 4] for i in range(16)]
        self.assertEqual(expected, actual)


class MinimalDocumentTest(unittest.TestCase):
    def test_one_by_one_transparent(self):
        text = serialize(1, 1, "char", [], [{"op": "fill", "slot": 0}])
        self.assertEqual(EXPECTED_1X1_TRANSPARENT, text)

    def test_one_by_one_opaque(self):
        text = serialize(1, 1, "char", ["FF0000FF"], [{"op": "fill", "slot": 1}])
        self.assertEqual(EXPECTED_1X1_OPAQUE, text)

    def test_empty_ops_is_written_on_one_line(self):
        text = serialize(1, 1, "char", [], [])
        self.assertIn('      "ops": []\n', text)
        self.assertTrue(text.endswith('  ]\n}\n'))


class OpMemberOrderTest(unittest.TestCase):
    def test_rect_member_order_and_commas(self):
        text = serialize(
            4,
            2,
            "char",
            ["FF0000FF"],
            [
                {"op": "rect", "x": 0, "y": 0, "w": 2, "h": 1, "slot": 1},
                {"op": "fill", "slot": 0},
            ],
        )
        lines = text.split("\n")
        start = lines.index('          "op": "rect",')
        self.assertEqual(
            [
                '          "op": "rect",',
                '          "x": 0,',
                '          "y": 0,',
                '          "w": 2,',
                '          "h": 1,',
                '          "slot": 1',
                "        },",
            ],
            lines[start : start + 7],
        )
        # 最后一个 op 不得带尾逗号。
        self.assertIn('          "slot": 0', lines)
        self.assertIn("        }", lines)

    def test_ops_are_separated_by_commas_only_between_elements(self):
        text = serialize(
            2,
            1,
            "char",
            ["FF0000FF", "00FF00FF"],
            [
                {"op": "rect", "x": 0, "y": 0, "w": 1, "h": 1, "slot": 1},
                {"op": "rect", "x": 1, "y": 0, "w": 1, "h": 1, "slot": 2},
            ],
        )
        self.assertIn('        },\n        {\n', text)
        self.assertNotIn("}\n        },", text)

    def test_grid_rows_are_one_per_line(self):
        text = serialize(
            3,
            2,
            "char",
            ["FF0000FF", "00FF00FF"],
            [{"op": "grid", "x": 0, "y": 0, "grid": ["010", "101"]}],
        )
        self.assertIn(
            '          "grid": [\n            "010",\n            "101"\n          ]\n',
            text,
        )

    def test_unknown_op_is_refused(self):
        with self.assertRaises(ValueError):
            serialize(1, 1, "char", [], [{"op": "line", "x0": 0, "y0": 0, "x1": 0, "y1": 0, "slot": 1}])


class ByteShapeTest(unittest.TestCase):
    def _sample(self):
        return serialize(
            2,
            2,
            "index",
            ["FF0000FF", "00FF00FF"],
            [{"op": "grid", "x": 0, "y": 0, "grid": ["0102", "0000"]}],
        )

    def test_ends_with_exactly_one_lf(self):
        text = self._sample()
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_carriage_return(self):
        self.assertNotIn("\r", self._sample())

    def test_no_trailing_whitespace(self):
        for number, line in enumerate(self._sample().split("\n"), start=1):
            self.assertEqual(line, line.rstrip(), "第 %d 行有行尾空白" % number)

    def test_no_tab_characters(self):
        self.assertNotIn("\t", self._sample())

    def test_indentation_is_two_space_multiples(self):
        for number, line in enumerate(self._sample().split("\n"), start=1):
            indent = len(line) - len(line.lstrip(" "))
            self.assertEqual(0, indent % 2, "第 %d 行缩进不是 2 的倍数" % number)

    def test_top_level_member_order(self):
        keys = []
        for line in self._sample().split("\n"):
            stripped = line.strip()
            if stripped.startswith('"') and '": ' in stripped and line.startswith("  \""):
                keys.append(stripped.split('"')[1])
        self.assertEqual(
            ["format", "version", "encoding", "width", "height", "palette", "layers"],
            keys,
        )

    def test_layer_member_order(self):
        text = self._sample()
        self.assertLess(text.index('"name": "base"'), text.index('"blend": "normal"'))
        self.assertLess(text.index('"blend": "normal"'), text.index('"ops": ['))

    def test_output_is_printable_ascii_only(self):
        """§3.7:文档只用到 U+0020..U+007E,因此规范形式一律原样书写、无转义。

        (原先这里断言的是 ``x == x.encode("utf-8").decode("utf-8")``——对任何
        字符串恒真,测不出任何东西。)
        """
        text = self._sample()
        offenders = sorted(
            {"U+%04X" % ord(ch) for ch in text if not (0x20 <= ord(ch) <= 0x7E or ch == "\n")}
        )
        self.assertEqual([], offenders, "出现了可打印 ASCII 之外的字符")
        self.assertEqual(text.encode("ascii").decode("ascii"), text)


if __name__ == "__main__":
    unittest.main()
