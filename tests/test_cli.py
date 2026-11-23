"""命令行入口的单测(`python -m importer`)。

CLI 是本步的交付物之一,三条性质必须钉住:输出文件与 API 一致、目录模式按路径
字节序处理、失败时不产出半份文件(§8.3)。
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from importer import __main__ as main_mod
from importer import extract_png
from importer.__main__ import collect_inputs
from pxl_oracle import render_pxl_text

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "spec" / "conformance" / "cases" / "syn-16x16-tricolor" / "input.png"

#: 临时目录放在仓库内(`.gitignore` 已忽略):系统 temp 在本机的沙箱下删除会被拒。
#: 名字带 pid,所以**同一份检出上并发跑两次测试不会互删**(`setUp` 会整目录清空);
#: 也只在自己的子目录里搅动,不碰 `tests/_sandbox/` 下别人放的东西。
SANDBOX = ROOT / "tests" / "_sandbox" / ("cli-%d" % os.getpid())


def run_cli(*args, **kwargs):
    # 子进程默认按控制台代码页写 stderr(本机是 cp936),断言中文时按 UTF-8 解会乱码;
    # 显式钉住子进程的流编码,让断言与平台无关。
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    # cwd 指向沙箱(不在仓库根)时,`-m importer` 找不到包 —— 子进程会死在 import,
    # 于是"返回码 1 + 源文件没变"对"模块根本没跑起来"同样成立(假通过)。
    # 显式把仓库根放进 PYTHONPATH,并在这里断言它确实跑起来了。
    environment["PYTHONPATH"] = str(ROOT)
    cwd = kwargs.get("cwd", ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "importer"] + list(args),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    stderr = completed.stderr.decode("utf-8", "replace")
    if "No module named" in stderr or "Traceback" in stderr:
        raise AssertionError(
            "CLI 子进程没有正常执行(cwd=%s):\n%s" % (cwd, stderr)
        )
    return completed


def make_junction(link, target):
    """建一个指向目录的链接（Windows 用 junction，不需要管理员）。建不出来返回 False。"""
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0 and Path(link).exists()
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    return True


class ResolveFailureTest(unittest.TestCase):
    """符号链接环在本机造不出来(需管理员),改用"让 resolve() 抛"钉同一批代码路径。

    这条洞的形态是:`Path.resolve()` 在自引用符号链接上抛 `OSError`(WinError 1921)
    或 `RuntimeError`。**正确处理是让它在写入前中止整批**(单行说明 + 退出 1),
    而不是把它降级成"普通目标"——降级会让 `Path.replace` 直接把那个链接换掉。
    """

    def setUp(self):
        # 用不存在于磁盘的路径,确保 `samefile` 先失败、走到 resolve 分支。
        self.left = Path("no-such-a.png")
        self.right = Path("no-such-a.png")

    def test_same_file_degrades_instead_of_raising(self):
        with _resolve_raises(OSError("模拟符号链接环")):
            self.assertTrue(main_mod._same_file(self.left, self.right))
        with _resolve_raises(RuntimeError("模拟符号链接环")):
            self.assertFalse(main_mod._same_file(self.left, Path("other.png")))

    def test_identity_propagates_instead_of_degrading(self):
        with _resolve_raises():
            with self.assertRaises(OSError):
                main_mod._identity(self.left)

    def test_dedupe_propagates_instead_of_degrading(self):
        with _resolve_raises():
            with self.assertRaises(OSError):
                main_mod._dedupe([self.left])

    def test_write_conflicts_propagates_instead_of_degrading(self):
        # 源必须**存在**(不存在的输入会被跳过,按"读取失败"报),才走得到目标解析。
        with _resolve_raises():
            with self.assertRaises(OSError):
                main_mod._write_conflicts([(FIXTURE, Path("no-such-out.pxl"))])

    def test_main_reports_single_line_when_planning_fails(self):
        original = main_mod.collect_inputs

        def boom(_paths, _skipped=None):
            raise OSError("模拟目录枚举失败")

        main_mod.collect_inputs = boom
        stream = io.StringIO()
        try:
            with contextlib.redirect_stderr(stream):
                code = main_mod.main(["whatever.png"])
        finally:
            main_mod.collect_inputs = original
        text = stream.getvalue()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", text)
        self.assertIn("无法解析输入/输出路径", text)

    def test_main_refuses_when_the_target_cannot_be_resolved(self):
        """输出目标解析不了(符号链接环):退出 1、单行、**一个文件都不写**。"""
        sandbox = ROOT / "tests" / "_sandbox" / ("target-%d" % os.getpid())
        shutil.rmtree(str(sandbox), ignore_errors=True)
        sandbox.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(sandbox), True)
        source = sandbox / "src.png"
        shutil.copyfile(str(FIXTURE), str(source))
        target = sandbox / "loop.pxl"

        original = main_mod._identity

        def boom(path):
            if path.name.endswith(".pxl"):
                raise OSError("模拟符号链接环")
            return original(path)

        main_mod._identity = boom
        stream = io.StringIO()
        try:
            with contextlib.redirect_stderr(stream):
                code = main_mod.main(["-o", str(target), str(source)])
        finally:
            main_mod._identity = original

        text = stream.getvalue()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", text)
        self.assertIn("无法解析输入/输出路径", text)
        self.assertFalse(target.exists(), "目标被写出来了")
        self.assertEqual(FIXTURE.read_bytes(), source.read_bytes(), "源 PNG 被改写了")
        self.assertEqual([], sorted(item.name for item in sandbox.glob("*.tmp")))


def _resolve_raises(error=None):
    """上下文管理器:把 ``Path.resolve`` 换成一抛就炸的实现。"""
    @contextlib.contextmanager
    def manager():
        original = Path.resolve

        def boom(_self, *args, **kwargs):
            raise error if error is not None else OSError("模拟符号链接环")

        Path.resolve = boom
        try:
            yield
        finally:
            Path.resolve = original

    return manager()


class CliTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(str(SANDBOX), ignore_errors=True)
        SANDBOX.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(SANDBOX), True)
        self.workspace = SANDBOX / "work"
        self.workspace.mkdir()
        self.source = self.workspace / "sample.png"
        shutil.copyfile(str(FIXTURE), str(self.source))

    def test_writes_sibling_pxl_matching_the_api(self):
        completed = run_cli(str(self.source))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        target = self.source.with_suffix(".pxl")
        self.assertTrue(target.is_file())
        self.assertEqual(
            extract_png(self.source.read_bytes()),
            target.read_bytes().decode("utf-8"),
        )

    def test_out_option_writes_the_given_path(self):
        target = self.workspace / "nested" / "out.pxl"
        target.parent.mkdir()
        completed = run_cli("-o", str(target), str(self.source))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            extract_png(self.source.read_bytes()),
            target.read_bytes().decode("utf-8"),
        )

    def test_out_with_multiple_inputs_is_a_usage_error(self):
        second = self.workspace / "second.png"
        shutil.copyfile(str(FIXTURE), str(second))
        completed = run_cli("-o", str(self.workspace / "x.pxl"), str(self.source), str(second))
        self.assertEqual(2, completed.returncode)

    def test_directory_mode_processes_every_png(self):
        folder = self.workspace / "many"
        folder.mkdir()
        for name in ("b.png", "a.png", "c.png"):
            shutil.copyfile(str(FIXTURE), str(folder / name))
        completed = run_cli(str(folder))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        produced = sorted(item.name for item in folder.glob("*.pxl"))
        self.assertEqual(["a.pxl", "b.pxl", "c.pxl"], produced)
        for name in ("a", "b", "c"):
            self.assertEqual(
                extract_png((folder / (name + ".png")).read_bytes()),
                (folder / (name + ".pxl")).read_bytes().decode("utf-8"),
            )

    def test_output_has_lf_endings_and_no_bom(self):
        self.assertEqual(0, run_cli(str(self.source)).returncode)
        payload = self.source.with_suffix(".pxl").read_bytes()
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", payload)

    def test_raw_flag_changes_bytes_but_not_pixels(self):
        self.assertEqual(0, run_cli("--raw", str(self.source)).returncode)
        raw_text = self.source.with_suffix(".pxl").read_bytes().decode("utf-8")
        compressed_text = extract_png(self.source.read_bytes())
        self.assertNotEqual(compressed_text, raw_text)
        self.assertEqual(
            render_pxl_text(compressed_text),
            render_pxl_text(raw_text),
        )

    def test_rejected_input_leaves_no_output_file(self):
        broken = self.workspace / "broken.png"
        broken.write_bytes(b"\x89PNG\r\n\x1a\nnot a real png")
        completed = run_cli(str(broken))
        self.assertEqual(1, completed.returncode)
        self.assertFalse(broken.with_suffix(".pxl").exists())
        self.assertIn(b"PXL006", completed.stderr)

    def test_unknown_option_is_a_usage_error(self):
        completed = run_cli("--nope", str(self.source))
        self.assertEqual(2, completed.returncode)

    def test_missing_input_reports_instead_of_traceback(self):
        missing = self.workspace / "no-such-file.png"
        completed = run_cli(str(missing))
        self.assertEqual(1, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr)
        self.assertIn("no-such-file.png", stderr)
        self.assertIn("读取失败", stderr)

    def test_unwritable_output_reports_instead_of_traceback(self):
        target = self.workspace / "no-such-dir" / "out.pxl"
        completed = run_cli("-o", str(target), str(self.source))
        self.assertEqual(1, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr)
        self.assertIn("写出失败", stderr)
        self.assertFalse(target.exists())

    def test_one_bad_file_does_not_stop_the_batch(self):
        folder = self.workspace / "mixed"
        folder.mkdir()
        shutil.copyfile(str(FIXTURE), str(folder / "good.png"))
        (folder / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\nnope")
        completed = run_cli(str(folder))
        self.assertEqual(1, completed.returncode)
        self.assertTrue((folder / "good.pxl").is_file())
        self.assertFalse((folder / "bad.pxl").exists())

    def test_refuses_to_overwrite_the_input(self):
        """`-o` 指到输入文件本身:整批不写,按用法错误退出(2)。

        这属于"请求本身无法执行"(与"某个文件抽取失败"不同),所以在任何写入之前
        就判掉,退出码是 2。
        """
        original = self.source.read_bytes()
        completed = run_cli("-o", str(self.source), str(self.source))
        self.assertEqual(2, completed.returncode)
        self.assertIn("拒绝覆盖源位图", completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(original, self.source.read_bytes(), "源 PNG 被改写了")

    def test_refuses_overwrite_through_a_relative_alias(self):
        """别名路径(`work/./sample.png`)也必须被判成同一个文件。

        注意必须同时断言"守卫真的报了那句话":若子进程压根没跑起来(例如 cwd
        不在仓库根、`-m importer` 找不到包),返回码同样是 1、源文件同样没变,
        这条用例就会变成空转。
        """
        original = self.source.read_bytes()
        completed = run_cli(
            "-o", "work/./sample.png", "work/sample.png", cwd=str(SANDBOX)
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn(
            "拒绝覆盖源位图",
            completed.stderr.decode("utf-8", "replace"),
            "守卫没有生效(可能是子进程根本没执行到被测代码)",
        )
        self.assertEqual(original, self.source.read_bytes(), "源 PNG 被改写了")

    def test_warns_when_overwriting_another_existing_png(self):
        """`-o` 指向别的已存在 PNG:允许(使用者明确指定),但必须出警告。"""
        other = self.workspace / "other.png"
        shutil.copyfile(str(FIXTURE), str(other))
        completed = run_cli("-o", str(other), str(self.source))
        self.assertEqual(0, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertIn("警告", stderr)
        self.assertIn("other.png", stderr)
        self.assertTrue(other.read_bytes().startswith(b"{\n  \"format\": \"PXL\""))

    def test_no_warning_when_target_is_new(self):
        completed = run_cli("-o", str(self.workspace / "fresh.pxl"), str(self.source))
        self.assertEqual(0, completed.returncode)
        self.assertEqual("", completed.stderr.decode("utf-8", "replace").strip())

    def test_leaves_no_temp_file_behind(self):
        self.assertEqual(0, run_cli(str(self.source)).returncode)
        self.assertEqual([], sorted(item.name for item in self.workspace.glob("*.tmp")))

    def test_existing_tmp_lookalike_file_is_not_clobbered(self):
        """临时名必须独占创建:用户已有的 `*.tmp` 不能被静默覆盖。"""
        sentinel = b"user-owned bytes, not ours\n"
        bystander = self.workspace / "sample.pxl.tmp"
        bystander.write_bytes(sentinel)
        completed = run_cli(str(self.source))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(sentinel, bystander.read_bytes(), "用户的 *.tmp 被改写了")
        self.assertTrue((self.workspace / "sample.pxl").is_file())

    def test_input_named_like_the_old_temp_pattern_is_not_deleted(self):
        """输入恰好叫 `<输出>.tmp` 时,旧实现会把它当中间产物覆盖掉。"""
        tricky = self.workspace / "out.pxl.tmp"
        shutil.copyfile(str(FIXTURE), str(tricky))
        original = tricky.read_bytes()
        completed = run_cli("-o", str(self.workspace / "out.pxl"), str(tricky))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(original, tricky.read_bytes(), "输入文件被当成临时产物删掉了")
        self.assertTrue((self.workspace / "out.pxl").is_file())

    def test_collect_inputs_sorts_by_path_bytes(self):
        """§9:处理顺序必须由**路径的 UTF-8 字节序**决定,不得依赖目录枚举顺序。

        `__main__.py` 里那行 `sort(key=...)` 删掉实现照样能过其余用例,所以这里直接
        断言展开顺序。字节序:`C.png`(0x43) < `a.png`(0x61) < `b.png`(0x62)。
        """
        folder = self.workspace / "order"
        folder.mkdir()
        for name in ("b.png", "a.png", "C.png"):
            shutil.copyfile(str(FIXTURE), str(folder / name))
        ordered = [item.name for item in collect_inputs([str(folder)])]
        self.assertEqual(["C.png", "a.png", "b.png"], ordered)

    def test_directory_mode_does_not_follow_a_junction(self):
        """§9 确定性：同一棵树不能因为本机有 junction 就多导入树外的 PNG。

        Windows 的 junction **不算** `os.path.islink`，于是 `os.walk(followlinks=False)`
        照样走进去，把给定目录之外的位图也导成 .pxl（写回树外）。compiler 的批量入口
        有同一个洞，两边都要剪枝。
        """
        outside = self.workspace / "outside"
        outside.mkdir()
        shutil.copyfile(str(FIXTURE), str(outside / "z.png"))
        folder = self.workspace / "tree"
        folder.mkdir()
        shutil.copyfile(str(FIXTURE), str(folder / "a.png"))
        if not make_junction(folder / "link", outside):
            self.skipTest("本机无法创建 junction/目录符号链接")
        completed = run_cli(str(folder))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertTrue((folder / "a.pxl").is_file())
        self.assertFalse((outside / "z.pxl").exists(), "不得导入给定目录之外的 PNG")

    def test_case_mismatched_extension_is_reported_not_silently_imported(self):
        """`X.PNG` 在 Windows 上曾被 rglob 当成 `*.png` 收下，在 POSIX 上不会。

        与 compiler 的批量入口一致：只认字面 `.png`，其余逐条报出来。
        """
        folder = self.workspace / "mixed"
        folder.mkdir()
        shutil.copyfile(str(FIXTURE), str(folder / "a.png"))
        shutil.copyfile(str(FIXTURE), str(folder / "b.PNG"))
        completed = run_cli(str(folder))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertTrue((folder / "a.pxl").is_file())
        self.assertFalse((folder / "b.pxl").exists(), "大小写不符的扩展名不得被导入")
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertIn("b.PNG", stderr)
        self.assertIn("大小写", stderr)

    def test_diagnostics_escape_control_characters(self):
        """一条诊断只能占一行：参数里的换行/U+2028 必须被转义，否则后续行没有前缀。"""
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = main_mod.main(["--bad" + chr(10) + "option"])
        stderr = stream.getvalue()
        self.assertEqual(2, code)
        self.assertEqual(1, len(stderr.splitlines()), "必须恰好一行: %r" % stderr)
        self.assertTrue(stderr.startswith("texel-import: "), stderr)
        self.assertNotIn(chr(0x2028), stderr)

    def test_link_like_directory_root_is_refused(self):
        """输入根是 junction/符号链接目录时拒绝，而不是跟着链接走。"""
        outside = self.workspace / "outside"
        outside.mkdir()
        shutil.copyfile(str(FIXTURE), str(outside / "z.png"))
        link = self.workspace / "rootlink"
        if not make_junction(link, outside):
            self.skipTest("本机无法创建 junction/目录符号链接")
        completed = run_cli(str(link))
        self.assertEqual(1, completed.returncode)
        self.assertFalse((outside / "z.pxl").exists(), "不得导入链接目标下的 PNG")
        self.assertIn("junction", completed.stderr.decode("utf-8", "replace"))

    def test_refuses_overwrite_through_a_hardlink(self):
        """硬链接指向同一个 inode:`samefile` 必须认出来,否则源图会被改写。"""
        alias = self.workspace / "alias.png"
        try:
            os.link(str(self.source), str(alias))
        except (OSError, AttributeError, NotImplementedError):
            self.skipTest("当前文件系统不支持硬链接")
        original = self.source.read_bytes()
        completed = run_cli("-o", str(alias), str(self.source))
        self.assertEqual(2, completed.returncode)
        self.assertIn("拒绝覆盖源位图", completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(original, self.source.read_bytes(), "源 PNG 被改写了")

    def test_two_inputs_sharing_one_output_are_refused(self):
        """同主干的两种扩展名(`x.png` / `x.tif`)会算出同一个 `x.pxl`,整批拒写。"""
        png = self.workspace / "x.png"
        tif = self.workspace / "x.tif"
        shutil.copyfile(str(FIXTURE), str(png))
        shutil.copyfile(str(FIXTURE), str(tif))
        before = (png.read_bytes(), tif.read_bytes())
        completed = run_cli(str(png), str(tif))
        self.assertEqual(2, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertIn("输出冲突", stderr)
        self.assertIn("输出路径相同", stderr)
        self.assertEqual(before, (png.read_bytes(), tif.read_bytes()))
        self.assertFalse((self.workspace / "x.pxl").exists(), "冲突时不得写出任何文件")

    def test_multi_input_does_not_overwrite_a_pending_input(self):
        """批量模式:一个文件的输出若撞上**同批另一个输入**,整批不许写。

        复现原来的漏洞:`victim.png` 与 `victim.pxl` 都是合法 PNG,一起传进去,
        `victim.png` 的输出正是 `victim.pxl` —— 先写就毁掉了后面那个输入。
        """
        victim_png = self.workspace / "victim.png"
        victim_pxl = self.workspace / "victim.pxl"
        shutil.copyfile(str(FIXTURE), str(victim_png))
        shutil.copyfile(str(FIXTURE), str(victim_pxl))
        before_png = victim_png.read_bytes()
        before_pxl = victim_pxl.read_bytes()

        completed = run_cli(str(victim_png), str(victim_pxl))
        self.assertEqual(2, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertIn("输出冲突", stderr)
        self.assertIn("覆盖同批的输入文件", stderr)
        # 冲突是"每处一行 + 一行汇总",但**不许**把 USAGE 全文铺在后面
        # (那是另一类"错误说明变成半篇帮助文档"的毛病)。
        self.assertFalse(
            [line for line in stderr.splitlines() if line.startswith("usage:")],
            "错误信息里混进了 USAGE:\n%s" % stderr,
        )
        self.assertEqual(before_png, victim_png.read_bytes(), "victim.png 被改写了")
        self.assertEqual(before_pxl, victim_pxl.read_bytes(), "victim.pxl 被改写了")

    def test_duplicate_input_is_deduplicated(self):
        completed = run_cli(str(self.source), str(self.source))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertTrue(self.source.with_suffix(".pxl").is_file())

    def test_symlink_loop_input_reports_instead_of_traceback(self):
        """自引用符号链接会让 `resolve()` 抛 OSError,必须变成单行说明。

        Windows 上创建符号链接需要开发者模式/管理员;做不到就跳过。
        """
        loop = self.workspace / "loop.png"
        try:
            os.symlink(str(loop), str(loop))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("当前环境不允许创建符号链接")
        completed = run_cli(str(loop))
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr, "泄漏了 traceback:\n%s" % stderr)
        self.assertEqual(1, completed.returncode)
        self.assertIn("无法解析输入/输出路径", stderr)
        self.assertTrue(loop.is_symlink(), "符号链接被换成了普通文件")

    def test_symlink_loop_output_is_refused_and_link_kept(self):
        """输出目标是符号链接环:退出 1、单行、链接**仍在**、目标没被写成 PXL。

        这条守的是"降级成普通目标"那个错误修法:一旦降级,`Path.replace` 会把链接
        目录项直接换掉(退出 0、无提示),链接就此消失。
        """
        loop = self.workspace / "loop.pxl"
        try:
            os.symlink(str(loop), str(loop))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("当前环境不允许创建符号链接")
        completed = run_cli("-o", str(loop), str(self.source))
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr, "泄漏了 traceback:\n%s" % stderr)
        self.assertEqual(1, completed.returncode, stderr)
        self.assertIn("无法解析输入/输出路径", stderr)
        self.assertTrue(loop.is_symlink(), "符号链接被替换成了普通文件")
        self.assertEqual(FIXTURE.read_bytes(), self.source.read_bytes(), "源 PNG 被改写了")
        self.assertEqual([], sorted(item.name for item in self.workspace.glob("*.tmp")))

    def test_out_pointing_at_a_directory_is_a_usage_error(self):
        """`-o .` / `-o ""` / `-o <目录>` 必须按用法错误拒掉。

        否则会走到 `Path.with_name()`,那里抛的是 **ValueError**(`WindowsPath('.')
        has an empty name`),而写入侧只接 `OSError` —— 结果就是 traceback。
        """
        for value in (".", "", str(self.workspace)):
            with self.subTest(out=value):
                completed = run_cli("-o", value, str(self.source))
                stderr = completed.stderr.decode("utf-8", "replace")
                self.assertEqual(2, completed.returncode, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertIn("需要一个文件路径", stderr)
                self.assertEqual(0, len(list(self.workspace.glob("*.pxl"))))

    def test_same_basename_in_two_directories_both_processed(self):
        """`a/same.png` 与 `b/same.png` 是两份输入,不能被去重逻辑合并掉。"""
        first = self.workspace / "a"
        second = self.workspace / "b"
        first.mkdir()
        second.mkdir()
        shutil.copyfile(str(FIXTURE), str(first / "same.png"))
        shutil.copyfile(str(FIXTURE), str(second / "same.png"))
        completed = run_cli(str(first / "same.png"), str(second / "same.png"))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertTrue((first / "same.pxl").is_file())
        self.assertTrue((second / "same.pxl").is_file())

    def test_out_equal_to_a_missing_input_reports_read_failure(self):
        """输出与**不存在**的输入同路径:应按"文件不存在"退出 1,而不是报冲突。"""
        missing = self.workspace / "missing.png"
        completed = run_cli("-o", str(missing), str(missing))
        self.assertEqual(1, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr)
        self.assertIn("读取失败", stderr)
        self.assertFalse(missing.exists())

    def test_option_value_that_looks_like_an_option_is_refused(self):
        """`-o -o` 会把输出写进名为 `-o` 的文件——那几乎总是把选项当成了路径。

        cwd 指到沙箱再跑:万一校验失效而真写出了 `-o`,也只会落在本次测试的
        临时目录里(随 tearDown 清掉),不会污染仓库根——顺带也就断言了"没写出
        任何文件"。别在这里断言 `Path.cwd()`:那是仓库根,别人留下的同名文件
        会让这条用例假失败。
        """
        completed = run_cli("-o", "-o", str(self.source), cwd=str(self.workspace))
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertEqual(2, completed.returncode, stderr)
        self.assertIn("不能以 '-' 开头", stderr)
        self.assertEqual([], sorted(item.name for item in self.workspace.iterdir()
                                    if item.name == "-o"))

    def test_usage_errors_are_exactly_one_line(self):
        """所有错误说明必须**恰好一行**——文件头承诺的就是"单行说明"。

        把"错误行数"变成可判定的:任何一条错误再顺手把 `USAGE` 全文接在后面,都会
        立刻被这条抓住(此前 `-o .` 那几个分支就是 9 行)。完整用法留给 `--help`。

        每条给一组**可接受**的关键字而不是一句写死的措辞:同一种输入在不同解释器上
        可能落在不同分支(例如文件名带换行时,3.8/3.9 的 `resolve()` 直接抛
        `WinError 123`,3.12 则走到写出失败),但"单行 + 退出非 0"这个契约不变。
        """
        # PxlError 那一行也要走 `_show`:文件名带 U+0085、内容不是 PNG。
        broken = self.workspace / "bad\u0085.png"
        broken.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
        cases = [
            ([], ("没有输入",)),
            (["--nope"], ("未知选项",)),
            ([self.source, "-o"], ("需要一个值",)),
            ([self.source, "-o", "."], ("需要一个文件路径",)),
            ([self.source, "-o", "-o"], ("不能以 '-' 开头",)),
            ([self.workspace / "nope.png"], ("读取失败",)),
            ([broken], ("PXL006",)),
            # 文件名里带换行符 / 行分隔符(Windows 允许):控制字符必须被转义,否则
            # "一行说明"自己就断成两行。路径用**沙箱内的绝对路径**——用相对名跑在
            # 仓库根上,POSIX 下会真的在仓库里写出一个带换行的文件。
            ([self.workspace / "no\u0085pe.png"], ("读取失败", "无法解析输入/输出路径")),
        ]
        for args, expected in cases:
            with self.subTest(args=[str(item) for item in args]):
                completed = run_cli(*[str(item) for item in args])
                stderr = completed.stderr.decode("utf-8", "replace")
                lines = [line for line in stderr.splitlines() if line.strip()]
                self.assertEqual(1, len(lines), "错误信息不是一行:\n%s" % stderr)
                self.assertNotEqual(0, completed.returncode)
                self.assertTrue(
                    any(word in lines[0] for word in expected),
                    "说明里没有预期关键字 %r:%s" % (expected, lines[0]),
                )

    def test_newline_output_name_is_legal_on_posix(self):
        """换行只在 Windows 上非法：POSIX 允许文件名带换行，必须正常写出且不报错。

        发布态复核（Linux 腿）指出的三个失败之一就是这条：原用例把「换行文件名」一律
        当成必失败输入，在 Linux 上这是**合法**的，于是"期望 1 行错误"变成期望了不存在的东西。
        """
        if os.name == "nt":
            self.skipTest("Windows 的文件名不允许换行，这条只适用于 POSIX")
        target = self.workspace / "a\nb.pxl"
        completed = run_cli(str(self.source), "-o", str(target))
        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(b"", completed.stderr)
        self.assertTrue(target.is_file(), "换行文件名应当真的被写出来")

    def test_show_escapes_control_characters(self):
        """`_show()` 直接测:控制字符转义成 `\\xNN`,非 ASCII 原样。

        为什么要单测:整条"文件名带换行"的错误路径在不同解释器上落到不同分支
        (3.8/3.9 的 `resolve()` 先扑街,3.12 才走到写出失败),所以端到端用例在
        3.8 上其实碰不到 `_show`。这里把转义规则本身钉住,不依赖 OS 走哪条路。
        """
        self.assertEqual("a\\x0ab.pxl", main_mod._show(Path("a\nb.pxl")))
        self.assertEqual("a\\x09b.pxl", main_mod._show(Path("a\tb.pxl")))
        self.assertEqual("a\\x0d\\x0ab.pxl", main_mod._show(Path("a\r\nb.pxl")))
        self.assertEqual("a\\x00b.pxl", main_mod._show(Path("a\x00b.pxl")))
        # 这三个不是"控制字符",但 str.splitlines() 会当换行——必须一起转义,
        # 否则按行解析时"一行说明"又变成两行(U+0085 在 Windows 文件名里合法)。
        self.assertEqual("a\\x85b.pxl", main_mod._show(Path("a\u0085b.pxl")))
        self.assertEqual("a\\u2028b.pxl", main_mod._show(Path("a\u2028b.pxl")))
        self.assertEqual("a\\u2029b.pxl", main_mod._show(Path("a\u2029b.pxl")))
        # 非 ASCII 必须原样保留,别把中文路径转成 \uXXXX。
        self.assertEqual("中文.png", main_mod._show(Path("中文.png")))
        for payload in ("a\nb.pxl", "a\tb.pxl", "a\r\nb.pxl", "a\u0085b.pxl",
                        "a\u2028b.pxl", "a\u2029b.pxl", "中文.png"):
            self.assertEqual(1, len(main_mod._show(Path(payload)).splitlines()), payload)

    def test_warning_line_escapes_control_characters(self):
        """覆盖警告行也要走 `_show`:目标名带 U+0085 时说明仍是一行。

        用 `U+0085` 而不是 `\\n`:Windows 允许前者进文件名,却拒绝后者(`open` 直接
        Errno 22),所以 `\\n` 造不出"已存在的 PNG"这种前置条件。
        """
        target = self.workspace / "w\u0085arn.png"
        shutil.copyfile(str(FIXTURE), str(target))
        completed = run_cli("-o", str(target), str(self.source))
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertEqual(0, completed.returncode, stderr)
        lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), "警告不是一行:\n%s" % stderr)
        self.assertIn("警告", lines[0])
        self.assertIn("\\x85", lines[0])

    def test_conflict_line_escapes_control_characters(self):
        """覆盖冲突行(含"与…同路径"原因)也要走 `_show`。"""
        tricky = self.workspace / "x\u0085y.pxl"
        shutil.copyfile(str(FIXTURE), str(tricky))
        completed = run_cli("-o", str(tricky), str(tricky))
        self.assertEqual(2, completed.returncode)
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertIn("拒绝覆盖源位图", stderr)
        self.assertIn("\\x85", stderr)
        for line in stderr.splitlines():
            self.assertNotIn("\u0085", line)

    def test_cleanup_failure_does_not_mask_the_original_error(self):
        """清理临时文件失败(哪怕抛 `ValueError`)不许盖住原始错误。"""
        sandbox = ROOT / "tests" / "_sandbox" / ("cleanup-%d" % os.getpid())
        shutil.rmtree(str(sandbox), ignore_errors=True)
        sandbox.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(sandbox), True)
        target = sandbox / "adir"
        target.mkdir()                      # 目标是目录 → `replace` 必然失败

        original_unlink = Path.unlink

        def boom(_self, *args, **kwargs):
            raise ValueError("模拟清理失败")

        Path.unlink = boom
        try:
            with self.assertRaises(OSError):
                main_mod._write_atomic(target, b"x")
        finally:
            Path.unlink = original_unlink
        for leftover in sandbox.iterdir():
            if leftover.is_file():
                original_unlink(leftover)

    def test_no_input_is_a_usage_error(self):
        self.assertEqual(2, run_cli().returncode)


if __name__ == "__main__":
    unittest.main()
