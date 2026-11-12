"""F-R6-02 回归：缓存命中不能跳过深度校验（两侧都必须拒绝 65 层、接受 64 层）。

形状：b.pxl -> b2 -> b3 -> b4 -> b5（height=5）先在浅处（a_cache）预热缓存，
再由一条 58/59 层的链在尾部复用 b.pxl。命中点 stack=60/61，总深度 = stack+height-1 = 64/65。
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import resolver, pxlcompile  # noqa: E402
from texel_compiler.errors import PxlError  # noqa: E402

WORK = REPO / "tests" / "_tmp" / "cache-depth"


def _doc(assets, ops):
    return {"format": "PXL", "version": 1, "encoding": "char", "width": 1, "height": 1,
            "palette": ["FFFFFFFF"], "assets": assets,
            "layers": [{"name": "b", "blend": "normal", "ops": ops}]}


def _stamp(name):
    return {"op": "stamp", "asset": name, "x": 0, "y": 0}


def build(links):
    d = WORK / str(links)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "entry.pxl").write_text(json.dumps(_doc(
        {"a_cache": {"kind": "pxl", "path": "b.pxl"},
         "z_head": {"kind": "pxl", "path": "a1.pxl"}},
        [_stamp("a_cache"), _stamp("z_head")])), encoding="utf-8")
    for index in range(1, 6):
        name = "b.pxl" if index == 1 else "b%d.pxl" % index
        if index == 5:
            leaf = {"format": "PXL", "version": 1, "encoding": "char", "width": 1,
                    "height": 1, "palette": ["112233FF"],
                    "layers": [{"name": "b", "blend": "normal",
                                "ops": [{"op": "fill", "slot": 1}]}]}
            (d / name).write_text(json.dumps(leaf), encoding="utf-8")
        else:
            (d / name).write_text(json.dumps(_doc(
                {"n": {"kind": "pxl", "path": "b%d.pxl" % (index + 1)}}, [_stamp("n")])),
                encoding="utf-8")
    for index in range(1, links + 1):
        target = "b.pxl" if index == links else "a%d.pxl" % (index + 1)
        (d / ("a%d.pxl" % index)).write_text(json.dumps(_doc(
            {"n": {"kind": "pxl", "path": target}}, [_stamp("n")])), encoding="utf-8")
    return d


class CacheDepthTest(unittest.TestCase):
    def test_64_levels_through_cache_is_legal(self):
        root = resolver.build_memory_resolver(str(build(58)))
        pxlcompile.compile_target("entry.pxl", root, {})  # 不抛即为合法

    def test_65_levels_through_cache_is_rejected(self):
        root = resolver.build_memory_resolver(str(build(59)))
        with self.assertRaises(PxlError) as caught:
            pxlcompile.compile_target("entry.pxl", root, {})
        self.assertEqual("PXL011", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
