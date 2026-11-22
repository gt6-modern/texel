"""测试:同一进程内并发调用公开 `cli.main` 不得互相踩临时文件(审查 #13)。

临时文件名曾经只由 PID 决定(`.texel-<pid>-out.tmp`),于是 8 个线程各自编译到**不同**
输出时,仍然抢同一个临时文件:独占创建让其中几个报 `CLI003 ... File exists`,而那些
输出本来毫无冲突。这里把并发跑成门禁:全部成功、每个产物都在、目录里不留 `.texel-*.tmp`。
"""

import os
import shutil
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / "compiler" / "python"
SANDBOX = ROOT / "tests" / "_sandbox"

if str(COMPILER) not in sys.path:
    sys.path.insert(0, str(COMPILER))

from texel_compiler import cli as compiler_cli

ENTRY = (
    '{"format":"PXL","version":1,"encoding":"char","width":512,"height":512,'
    '"palette":["FF0000FF","00FF00FF"],"layers":[{"name":"base","blend":"normal",'
    '"ops":[{"op":"fill","slot":1},{"op":"rect","x":16,"y":16,"w":480,"h":480,"slot":2}]}]}'
)

THREADS = 8


class ConcurrentCompileTest(unittest.TestCase):

    def setUp(self):
        self.work = SANDBOX / "cli-concurrent"
        shutil.rmtree(str(self.work), ignore_errors=True)
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "entry.pxl").write_text(ENTRY, encoding="utf-8")
        # 入口按**逻辑路径**给("entry.pxl"),所以进程 cwd 必须在工作目录里。
        self.previous = os.getcwd()
        os.chdir(str(self.work))

    def tearDown(self):
        os.chdir(self.previous)
        shutil.rmtree(str(self.work), ignore_errors=True)

    def compile_one(self, index, start, results):
        output = self.work / ("out-%d.png" % index)
        start.wait()                                  # 同时进入编译:让暂存窗口真的重叠
        try:
            results.append(compiler_cli.main([
                "compile", "entry.pxl",
                "--asset-root", str(self.work),
                "-o", str(output),
            ]))
        except BaseException as failure:              # 线程里的异常不能静默
            results.append(failure)

    def test_staged_names_do_not_collide_in_one_directory(self):
        """确定性版:两个暂存文件同时挂在同一目录里,名字必须不同。

        这正是并发编译的真实交错 —— 线程 A 已经把临时文件建好、还没改名时,线程 B 在同一
        目录里开始暂存。顺序复现比 8 线程更容易可靠地咬住旧写法(只按 PID 起名时第二次
        `O_EXCL` 直接 `File exists`)。
        """
        target = self.work / "out.png"
        first = compiler_cli._stage_bytes(target, b"first", "png")
        second = compiler_cli._stage_bytes(target, b"second", "png")
        try:
            self.assertNotEqual(str(first), str(second), "两次暂存必须拿到不同的临时文件")
            self.assertEqual(b"first", first.read_bytes())
            self.assertEqual(b"second", second.read_bytes())
        finally:
            for path in (first, second):
                path.unlink()

    def test_parallel_compiles_into_one_directory(self):
        results = []
        start = threading.Barrier(THREADS)
        threads = [threading.Thread(target=self.compile_one, args=(index, start, results))
                   for index in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(THREADS, len(results), "每个线程都必须给出一个结果")
        failures = [result for result in results if result != 0]
        self.assertEqual([], failures, "并发编译不应失败: %r" % (failures,))
        for index in range(THREADS):
            self.assertTrue((self.work / ("out-%d.png" % index)).is_file(),
                            "第 %d 个输出缺失" % index)
        leftovers = sorted(path.name for path in self.work.glob(".texel-*"))
        self.assertEqual([], leftovers, "临时文件必须清干净: %r" % (leftovers,))


if __name__ == "__main__":
    unittest.main()
