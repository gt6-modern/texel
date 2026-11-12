"""附录 A 规范抽取策略的单测。

每个测试都对应附录 A 的某一步,而不是只比最终文本——否则"压缩算法错在哪一步"
读不出来。
"""

import unittest

from importer.compress import MIN_RUN, OPS_CAP, build_ops, raw_ops

ALLOWED_OPS = frozenset(("fill", "rect", "grid"))


def flat(rows):
    out = []
    for row in rows:
        out.extend(row)
    return out


class UniformImageTest(unittest.TestCase):
    def test_single_opaque_color_becomes_one_fill(self):
        ops = build_ops([1] * 12, 4, 3, "char")
        self.assertEqual([{"op": "fill", "slot": 1}], ops)

    def test_single_transparent_becomes_fill_slot_zero(self):
        ops = build_ops([0] * 4, 2, 2, "char")
        self.assertEqual([{"op": "fill", "slot": 0}], ops)

    def test_one_by_one_opaque(self):
        self.assertEqual([{"op": "fill", "slot": 1}], build_ops([1], 1, 1, "char"))


class RowClassificationTest(unittest.TestCase):
    def test_short_segments_make_a_grid_row(self):
        # 段长 2 < MIN_RUN → GRID 行 → 一条 grid,行内 token 串连写。
        ops = build_ops([1, 1, 2, 2], 4, 1, "char")
        self.assertEqual(1, len(ops))
        self.assertEqual("grid", ops[0]["op"])
        self.assertEqual(["0011"], ops[0]["grid"])

    def test_segments_of_min_run_make_a_runner_row(self):
        ops = build_ops([1, 1, 1, 2, 2, 2], 6, 1, "char")
        self.assertEqual(
            [
                {"op": "rect", "x": 0, "y": 0, "w": 3, "h": 1, "slot": 1},
                {"op": "rect", "x": 3, "y": 0, "w": 3, "h": 1, "slot": 2},
            ],
            ops,
        )

    def test_min_run_constant_is_three(self):
        self.assertEqual(3, MIN_RUN)
        # 段长 2 与段长 3 的分界:前者 GRID,后者 RUNNER。
        self.assertEqual("grid", build_ops([1, 1, 2, 2], 4, 1, "char")[0]["op"])
        self.assertEqual("rect", build_ops([1, 1, 1, 2, 2, 2], 6, 1, "char")[0]["op"])


class VerticalMergeTest(unittest.TestCase):
    def test_identical_rows_merge_into_taller_rect(self):
        ops = build_ops(flat([[1, 1, 1, 2, 2, 2]] * 2), 6, 2, "char")
        self.assertEqual(
            [
                {"op": "rect", "x": 0, "y": 0, "w": 3, "h": 2, "slot": 1},
                {"op": "rect", "x": 3, "y": 0, "w": 3, "h": 2, "slot": 2},
            ],
            ops,
        )

    def test_merge_is_per_identical_segment_not_per_row(self):
        # x、w 相同但 slot 不同 → 只有完全相同的段才合并,h 各自独立增长。
        ops = build_ops(flat([[1, 1, 1, 2, 2, 2], [1, 1, 1, 3, 3, 3]]), 6, 2, "char")
        self.assertEqual(
            [(0, 2, 1), (0, 1, 2), (1, 1, 3)],
            [(op["y"], op["h"], op["slot"]) for op in ops],
        )

    def test_merge_requires_identical_x_and_w(self):
        # 两行都是 RUNNER,但段边界错开(x/w 不同)→ 不得合并。
        rows = [[1, 1, 1, 1, 2, 2, 2, 2], [1, 1, 1, 1, 1, 2, 2, 2]]
        ops = build_ops(flat(rows), 8, 2, "char")
        self.assertEqual(
            [(0, 0, 4, 1), (0, 4, 4, 1), (1, 0, 5, 1), (1, 5, 3, 1)],
            [(op["y"], op["x"], op["w"], op["h"]) for op in ops],
        )

    def test_merge_does_not_cross_blocks(self):
        # RUNNER / GRID / RUNNER:第一块与第三块的同形段不得合并。
        rows = [
            [1, 1, 1, 2, 2, 2],
            [1, 2, 1, 2, 1, 2],
            [1, 1, 1, 2, 2, 2],
        ]
        ops = build_ops(flat(rows), 6, 3, "char")
        self.assertEqual(["rect", "rect", "grid", "rect", "rect"], [o["op"] for o in ops])
        self.assertEqual(1, ops[0]["h"])
        self.assertEqual(1, ops[3]["h"])
        self.assertEqual(1, ops[2]["y"])

    def test_eight_wide_bands_merge_to_full_height(self):
        width, height, bands = 64, 32, 8
        slots = []
        for _y in range(height):
            for x in range(width):
                slots.append(x // bands + 1)
        ops = build_ops(slots, width, height, "char")
        self.assertEqual(8, len(ops))
        for index, op in enumerate(ops):
            self.assertEqual(index * bands, op["x"])
            self.assertEqual(bands, op["w"])
            self.assertEqual(height, op["h"])


class BlockOrderTest(unittest.TestCase):
    def test_blocks_are_emitted_in_row_order(self):
        rows = [
            [1, 1, 1, 2, 2, 2],  # RUNNER
            [1, 2, 1, 2, 1, 2],  # GRID
            [3, 3, 3, 3, 3, 3],  # RUNNER
            [1, 2, 1, 2, 1, 2],  # GRID
            [1, 2, 1, 2, 1, 2],  # GRID(与前一行同类 → 同一块)
        ]
        ops = build_ops(flat(rows), 6, 5, "char")
        kinds = [op["op"] for op in ops]
        self.assertEqual(["rect", "rect", "grid", "rect", "grid"], kinds)
        self.assertEqual(1, ops[2]["y"])
        self.assertEqual(2, ops[3]["y"])
        self.assertEqual(3, ops[4]["y"])
        # 第 4、5 行同属一个 GRID 块 → 一条 grid 两行。
        self.assertEqual(2, len(ops[4]["grid"]))

    def test_grid_row_tokens_use_the_right_encoding(self):
        ops = build_ops([1, 2, 3], 3, 1, "index")
        self.assertEqual(["010203"], ops[0]["grid"])
        ops4 = build_ops([1, 2, 3], 3, 1, "index4")
        self.assertEqual(["000100020003"], ops4[0]["grid"])


class OpsCapFallbackTest(unittest.TestCase):
    """附录 A 第 8 步:每层 op 数超预算 → 整层退化为一条覆盖全图的 grid。"""

    width = 65535
    height = 4

    def _slots(self):
        # 每行段宽恒为 3、相邻行反色:四行都是 RUNNER 且上下不可合并。
        slots = []
        for y in range(self.height):
            for x in range(self.width):
                slots.append(1 if ((x // 3) + y) % 2 == 0 else 2)
        return slots

    def test_the_uncompressed_rect_count_really_exceeds_the_cap(self):
        """先确认这份输入**确实**会产出远超预算的 rect,否则回退测的是另一件事。

        数一遍用的是测试自己的分段口径(`compress` 不导出内部函数),所以它证明的是
        "这份输入的形状满足前提",不是"实现数得对"——后者由 `test_fallback_*` 与
        `test_conformance` 的边界用例负责。
        """
        slots = self._slots()
        rects = 0
        for y in range(self.height):
            row = slots[y * self.width:(y + 1) * self.width]
            for x in range(1, self.width + 1):
                if x == self.width or row[x] != row[x - 1]:
                    rects += 1     # 每段一条;相邻行反色,故无垂直合并
        self.assertEqual(21845 * self.height, rects)
        self.assertGreater(rects, OPS_CAP)

    def test_fallback_emits_one_grid_covering_the_whole_image(self):
        slots = self._slots()
        ops = build_ops(slots, self.width, self.height, "char")
        self.assertEqual(1, len(ops))
        op = ops[0]
        self.assertEqual("grid", op["op"])
        self.assertEqual(0, op["x"])
        self.assertEqual(0, op["y"])
        self.assertEqual(self.height, len(op["grid"]))
        for row in op["grid"]:
            self.assertEqual(self.width, len(row))

    def test_fallback_equals_the_per_pixel_baseline(self):
        slots = self._slots()
        self.assertEqual(
            raw_ops(slots, self.width, self.height, "char"),
            build_ops(slots, self.width, self.height, "char"),
        )

    def test_ops_cap_constant_is_32768(self):
        self.assertEqual(32768, OPS_CAP)


class FallbackCostTest(unittest.TestCase):
    """回退要在构造过程中触发,不能先把注定被丢弃的 rect 全造出来。"""

    width = 65535
    height = 16

    def _slots(self):
        slots = []
        for y in range(self.height):
            for x in range(self.width):
                slots.append(1 if ((x // 3) + y) % 2 == 0 else 2)
        return slots

    def test_large_runner_block_falls_back_without_building_every_rect(self):
        import tracemalloc

        slots = self._slots()
        self.assertEqual(self.width * self.height, len(slots))
        # 常规输出将是 21845 x 16 = 349,520 条 rect。
        self.assertGreater(21845 * self.height, OPS_CAP)

        tracemalloc.start()
        try:
            ops = build_ops(slots, self.width, self.height, "char")
        finally:
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        self.assertEqual(1, len(ops))
        self.assertEqual("grid", ops[0]["op"])
        # 349,520 个 rect dict 约 100 MB 量级;回退路径不该出现那个峰值。
        self.assertLess(
            peak,
            64 * 1024 * 1024,
            "回退路径峰值 %.1f MB,说明 rect 在判定前被整体构造了"
            % (peak / 1048576.0),
        )


class RawOpsTest(unittest.TestCase):
    def test_raw_is_a_single_full_image_grid(self):
        slots = [1, 2, 1, 2, 3, 3]
        ops = raw_ops(slots, 3, 2, "char")
        self.assertEqual(1, len(ops))
        self.assertEqual("grid", ops[0]["op"])
        self.assertEqual(0, ops[0]["x"])
        self.assertEqual(0, ops[0]["y"])
        self.assertEqual(["010", "122"], ops[0]["grid"])

    def test_raw_works_for_uniform_images_too(self):
        ops = raw_ops([1] * 4, 2, 2, "char")
        self.assertEqual(["00", "00"], ops[0]["grid"])


class AppendixPurityTest(unittest.TestCase):
    def test_extractor_never_emits_the_five_uncompressed_ops(self):
        samples = [
            ([1] * 12, 4, 3),
            ([1, 1, 1, 2, 2, 2], 6, 1),
            ([1, 2, 1, 2], 4, 1),
            ([1, 1, 1, 2, 2, 2, 1, 2, 1, 2, 1, 2], 6, 2),
        ]
        for slots, width, height in samples:
            for op in build_ops(slots, width, height, "char"):
                self.assertIn(op["op"], ALLOWED_OPS)

    def test_op_members_are_exactly_the_spec_set(self):
        ops = build_ops([1, 1, 1, 2, 2, 2, 1, 2, 1, 2, 1, 2], 6, 2, "char")
        for op in ops:
            if op["op"] == "rect":
                self.assertEqual(["op", "x", "y", "w", "h", "slot"], list(op.keys()))
            elif op["op"] == "grid":
                self.assertEqual(["op", "x", "y", "grid"], list(op.keys()))
            else:
                self.assertEqual(["op", "slot"], list(op.keys()))

    def test_slot_table_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            build_ops([1, 2, 3], 2, 2, "char")


if __name__ == "__main__":
    unittest.main()
