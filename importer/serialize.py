"""规范序列化(规范 §3.7):逐字节可判定,不允许依赖 ``json.dumps``。

``json.dumps`` 表达不了 §3.7 的排版——``[`` 要紧跟在该成员的 ``": "`` 之后、
元素缩进 +2、空数组写 ``[]``、行尾不许有空白。因此这里自己拼字节。

抽取器只产出 ``fill`` / ``rect`` / ``grid``,单图层 ``name="base"``、
``blend="normal"``,且**不含** ``sprites``(附录 A 第 9、10 条)。
"""

INDENT = "  "

_OP_MEMBERS = {
    "fill": ("op", "slot"),
    "rect": ("op", "x", "y", "w", "h", "slot"),
    "grid": ("op", "x", "y", "grid"),
}


def serialize(width, height, encoding, palette, ops):
    """返回规范形式全文,末尾恰一个 LF。"""
    lines = [
        "{",
        '%s"format": "PXL",' % INDENT,
        '%s"version": 1,' % INDENT,
        '%s"encoding": "%s",' % (INDENT, encoding),
        '%s"width": %d,' % (INDENT, width),
        '%s"height": %d,' % (INDENT, height),
    ]

    if palette:
        lines.append('%s"palette": [' % INDENT)
        last = len(palette) - 1
        for index, entry in enumerate(palette):
            lines.append('%s"%s"%s'
                         % (INDENT * 2, entry, "" if index == last else ","))
        lines.append("%s]," % INDENT)
    else:
        lines.append('%s"palette": [],' % INDENT)

    lines.append('%s"layers": [' % INDENT)
    lines.append("%s{" % (INDENT * 2))
    lines.append('%s"name": "base",' % (INDENT * 3))
    lines.append('%s"blend": "normal",' % (INDENT * 3))

    if ops:
        lines.append('%s"ops": [' % (INDENT * 3))
        last_op = len(ops) - 1
        for index, op in enumerate(ops):
            lines.extend(_op_lines(op, INDENT * 4, index == last_op))
        lines.append("%s]" % (INDENT * 3))
    else:
        lines.append('%s"ops": []' % (INDENT * 3))

    lines.append("%s}" % (INDENT * 2))
    lines.append("%s]" % INDENT)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _op_lines(op, indent, is_last):
    """渲染一个 op 对象;``indent`` 是它的 ``{`` 所在缩进。"""
    kind = op["op"]
    members = _OP_MEMBERS.get(kind)
    if members is None:
        raise ValueError("抽取器不得产出 op %r(附录 A 第 9 条)" % (kind,))

    inner = indent + INDENT
    lines = [indent + "{"]
    last_member = len(members) - 1
    for position, name in enumerate(members):
        trailing = "" if position == last_member else ","
        if name == "op":
            lines.append('%s"op": "%s"%s' % (inner, kind, trailing))
        elif name == "grid":
            # 非空数组:`[` 紧跟 `": "` 留在同一行,元素缩进 +2,`]` 与键同列。
            lines.append('%s"grid": [' % inner)
            rows = op["grid"]
            last_row = len(rows) - 1
            for index, row in enumerate(rows):
                lines.append(
                    '%s"%s"%s' % (inner + INDENT, row, "" if index == last_row else ",")
                )
            lines.append("%s]%s" % (inner, trailing))
        else:
            lines.append('%s"%s": %d%s' % (inner, name, op[name], trailing))
    lines.append(indent + "}" + ("" if is_last else ","))
    return lines
