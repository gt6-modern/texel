"""规范抽取策略:把 slot 网格压成 op 序列(规范**附录 A**,规范性)。

本模块只实现附录 A 钉死的算法,**不做任何"更聪明"的压缩**:任何偏离都会让
``expected.pxl`` 的"零 diff"失效,即使像素仍然正确(README §2.1a 判据 1)。

摘要:

1. 全图同一 slot → 一条 ``fill``;
2. 否则逐行取**段**(行内极大的同色连续区间);一行为 RUNNER 当且仅当**所有**
   段长度 >= ``MIN_RUN``,否则为 GRID;
3. 按行序切成**极大的同类型连续块**;
4. RUNNER 块内逐行取段,仅当**同一块内紧邻上一行**存在完全相同的
   ``(x, w, slot)`` 才把那条 ``rect`` 的 ``h`` 加 1;
5. GRID 块输出**一条** ``grid``;
6. op 总数超过 ``OPS_CAP`` 时**整层退化**为一条覆盖全图的 ``grid``。

内存纪律(不影响输出,只影响拒绝/回退路径的成本):行分类与段提取都是**逐行
惰性**做的,不预先把整图段表物化;``OPS_CAP`` 在**构造过程中**检查,超出即停。
合法输入例如 65,535×16、段宽恒 3、相邻行反色的图会产生 349,520 条 rect,预先把
它们全部造出来只会把内存烧在一条注定要被回退丢弃的序列上。
"""

from .tokens import encode_token

#: 附录 A 第 3 步的判定常数:段长度阈值(C-5 落地假设)。
MIN_RUN = 3

#: 附录 A 第 8 步的每层 op 数上限;超限即全局回退。
OPS_CAP = 32768

RUNNER = "runner"
GRID = "grid"


class _OverBudget(Exception):
    """内部信号:op 数已超预算。

    只在本模块内用于从构造深处立刻跳出,**不对外抛出**——对外可见的行为只有
    "整层退化为一条 grid"这一种(附录 A 第 8 步)。
    """


def build_ops(slots, width, height, encoding):
    """按附录 A 把 slot 网格压成规范 op 序列。"""
    if len(slots) != width * height:
        raise ValueError(
            "slot 表长度 %d 与 %dx%d 不符" % (len(slots), width, height)
        )

    # 第 2 步:整图纯色 → 一条 fill。
    first = slots[0]
    uniform = True
    for slot in slots:
        if slot != first:
            uniform = False
            break
    if uniform:
        return [{"op": "fill", "slot": first}]

    # 第 3 步:逐行分类(只需段长,不物化段表)。
    kinds = [_row_kind(slots, width, y) for y in range(height)]

    # 第 4–8 步:极大同类型连续块,按块先后输出;超预算立刻回退。
    ops = []
    try:
        for kind, y0, y1 in _blocks(kinds):
            if kind == RUNNER:
                _append_runner_ops(ops, slots, width, y0, y1)
            else:
                _append_grid_op(ops, slots, width, y0, y1, encoding)
            if len(ops) > OPS_CAP:
                raise _OverBudget()
    except _OverBudget:
        # 回退是纯函数(只取决于像素值),因此仍满足"同输入同输出"。
        return raw_ops(slots, width, height, encoding)
    return ops


def raw_ops(slots, width, height, encoding):
    """逐像素基准:整图一条 ``grid``。

    与附录 A 第 8 步的全局回退产物**同形**——这也是"压缩版与逐像素版渲染结果
    必须逐像素相同"这条判定的对照基准。
    """
    if len(slots) != width * height:
        raise ValueError(
            "slot 表长度 %d 与 %dx%d 不符" % (len(slots), width, height)
        )
    ops = []
    _append_grid_op(ops, slots, width, 0, height - 1, encoding)
    return ops


def _row_kind(slots, width, y):
    """一行是 RUNNER(所有段长 >= MIN_RUN)还是 GRID(存在更短的段)。"""
    base = y * width
    current = slots[base]
    run = 1
    for x in range(1, width):
        value = slots[base + x]
        if value == current:
            run += 1
            continue
        if run < MIN_RUN:
            return GRID
        current = value
        run = 1
    return RUNNER if run >= MIN_RUN else GRID


def _row_segments(slots, width, y):
    """返回该行的段列表 ``[(x, w, slot), ...]``,行内极大同色区间。"""
    base = y * width
    segments = []
    start = 0
    current = slots[base]
    for x in range(1, width):
        value = slots[base + x]
        if value != current:
            segments.append((start, x - start, current))
            start = x
            current = value
    segments.append((start, width - start, current))
    return segments


def _blocks(kinds):
    """把行分类序列切成极大同类型连续块,返回 ``[(kind, y0, y1), ...]``。"""
    blocks = []
    start = 0
    current = kinds[0]
    for y in range(1, len(kinds)):
        if kinds[y] != current:
            blocks.append((current, start, y - 1))
            start = y
            current = kinds[y]
    blocks.append((current, start, len(kinds) - 1))
    return blocks


def _append_runner_ops(ops, slots, width, y0, y1):
    """RUNNER 块:逐行取段,块内与紧邻上一行的相同段合并成更高的 rect。"""
    previous = {}
    for y in range(y0, y1 + 1):
        current = {}
        for x, width_of_segment, slot in _row_segments(slots, width, y):
            key = (x, width_of_segment, slot)
            merged = previous.get(key)
            if merged is not None:
                merged["h"] += 1
                current[key] = merged
            else:
                op = {
                    "op": "rect",
                    "x": x,
                    "y": y,
                    "w": width_of_segment,
                    "h": 1,
                    "slot": slot,
                }
                ops.append(op)
                current[key] = op
                # 第 8 步在构造过程中生效:再攒下去的内存本来就要被丢弃。
                if len(ops) > OPS_CAP:
                    raise _OverBudget()
        previous = current


def _append_grid_op(ops, slots, width, y0, y1, encoding):
    """GRID 块:一条 grid,x=0、y=y0,每行一个 token 串。"""
    lines = []
    for y in range(y0, y1 + 1):
        parts = []
        for _x, width_of_segment, slot in _row_segments(slots, width, y):
            parts.append(encode_token(encoding, slot) * width_of_segment)
        lines.append("".join(parts))
    ops.append({"op": "grid", "x": 0, "y": y0, "grid": lines})
