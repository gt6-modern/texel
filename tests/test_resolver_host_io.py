"""FS resolver host failures must reach the real CLI as CLI001 (PXL-v1 8.4)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "compiler/python"))

from texel_compiler import cli


class ResolverHostIoTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="texel-resolver-host-io-")
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.parts = self.root / "parts"
        self.parts.mkdir()
        self.source = self.parts / "tile.pxl"
        leaf = {
            "format": "PXL", "version": 1, "width": 1, "height": 1,
            "encoding": "char", "palette": ["112233FF"],
            "layers": [{"name": "base", "blend": "normal",
                        "ops": [{"op": "fill", "slot": 1}]}],
        }
        self.source.write_text(json.dumps(leaf), encoding="utf-8")
        entry = dict(leaf, version=1,
                     assets={"tile": {"kind": "pxl", "path": "parts/tile.pxl"}},
                     layers=[{"name": "base", "blend": "normal",
                              "ops": [{"op": "stamp", "asset": "tile", "x": 0, "y": 0}]}])
        (self.root / "entry.pxl").write_text(json.dumps(entry), encoding="utf-8")
        self.output = self.root / "out.png"
        self.manifest = self.root / "deps.tsv"

    def run_cli(self, entry="entry.pxl"):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["compile", entry, "--asset-root", str(self.root),
                             "-o", str(self.output), "--deps", str(self.manifest)])
        return code, out.getvalue(), err.getvalue()

    def assert_failure(self, code, diagnostic, entry="entry.pxl"):
        actual, out, err = self.run_cli(entry)
        self.assertEqual(code, actual, err)
        self.assertIn(diagnostic, err)
        self.assertNotIn("Traceback", err)
        if diagnostic == "CLI001":
            self.assertNotIn("PXL", err)
        self.assertEqual("", out)
        self.assertFalse(self.output.exists(), "failed compilation must not write PNG")
        self.assertFalse(self.manifest.exists(), "failed compilation must not write dependencies")

    def boundary(self, method, target, failure, strict=None):
        original = getattr(Path, method)

        def injected(path, *args, **kwargs):
            if path.name == target.name and (strict is None or kwargs.get("strict", False) == strict):
                raise failure
            return original(path, *args, **kwargs)

        return patch.object(Path, method, injected)

    def swallowed_predicate(self, method, target):
        # Model 3.14 predicates returning False when the underlying stat fails.
        original = getattr(Path, method)

        def injected(path, *args, **kwargs):
            if path.name == target.name:
                return False
            return original(path, *args, **kwargs)

        return patch.object(Path, method, injected)

    def test_asset_root_stat_failure_is_cli001(self):
        with self.boundary("stat", self.root, PermissionError(13, "root stat denied")):
            try:
                self.assert_failure(4, "CLI001")
            except OSError as failure:
                self.fail("asset root I/O must reach CLI001: " + str(failure))

    def test_iterdir_permission_failure_is_cli001(self):
        with self.boundary("iterdir", self.parts, PermissionError(13, "denied listing source")):
            self.assert_failure(4, "CLI001")

    def test_iterdir_failure_after_partial_enumeration_is_cli001(self):
        original = Path.iterdir

        def injected(path):
            if path.name == self.parts.name:
                def failing_entries():
                    yield self.source
                    raise OSError(5, "directory read failed")
                return failing_entries()
            return original(path)

        with patch.object(Path, "iterdir", injected):
            self.assert_failure(4, "CLI001")

    def test_read_permission_failure_is_cli001(self):
        with self.boundary("read_bytes", self.source, PermissionError(13, "source read denied")):
            self.assert_failure(4, "CLI001")

    def test_read_sharing_violation_is_cli001(self):
        failure = OSError(13, "source sharing violation")
        failure.winerror = 32
        with self.boundary("read_bytes", self.source, failure):
            self.assert_failure(4, "CLI001")

    def test_read_io_failure_is_cli001(self):
        with self.boundary("read_bytes", self.source, OSError(5, "source read failed")):
            self.assert_failure(4, "CLI001")

    def test_non_strict_resolve_failure_is_cli001(self):
        with self.boundary("resolve", self.source, PermissionError(13, "real path denied"), strict=False):
            self.assert_failure(4, "CLI001")

    def test_strict_resolve_failure_is_cli001(self):
        with self.boundary("resolve", self.source, OSError(5, "real path failed"), strict=True):
            self.assert_failure(4, "CLI001")

    def test_directory_stat_failure_is_cli001(self):
        with self.boundary("stat", self.parts, PermissionError(13, "directory stat denied")):
            with self.swallowed_predicate("is_dir", self.parts):
                self.assert_failure(4, "CLI001")

    def test_file_stat_failure_is_cli001(self):
        with self.boundary("stat", self.source, PermissionError(13, "file stat denied")):
            with self.swallowed_predicate("is_file", self.source):
                self.assert_failure(4, "CLI001")

    def test_dangling_readlink_failure_is_cli001(self):
        original = Path.lstat
        fields = list(self.source.lstat())
        fields[0] = 0o120777
        link_stat = type(self.source.lstat())(fields)

        def injected(path, *args, **kwargs):
            if path.name == self.source.name:
                return link_stat
            return original(path, *args, **kwargs)

        with self.boundary("resolve", self.source, FileNotFoundError(2, "dangling link"), strict=True):
            with patch.object(Path, "lstat", injected):
                with patch("texel_compiler.resolver.os.readlink", side_effect=PermissionError(13, "link read denied")):
                    self.assert_failure(4, "CLI001")

    def test_missing_entry_is_pxl009(self):
        self.assert_failure(3, "PXL009", entry="missing.pxl")

    def test_missing_dependency_is_pxl009(self):
        self.source.unlink()
        self.assert_failure(3, "PXL009")

    def test_not_directory_source_is_pxl009(self):
        self.assert_failure(3, "PXL009", entry="parts/tile.pxl/child.pxl")

    def test_disappearing_source_boundaries_are_pxl009(self):
        for method, target, strict in (("iterdir", self.parts, None),
                                       ("resolve", self.source, True),
                                       ("read_bytes", self.source, None)):
            for error in (FileNotFoundError, NotADirectoryError):
                with self.subTest(method=method, error=error.__name__):
                    with self.boundary(method, target, error(2, "source disappeared"), strict):
                        self.assert_failure(3, "PXL009")

    def test_source_resolve_runtime_loop_stays_controlled(self):
        with self.boundary("resolve", self.source, RuntimeError("Symlink loop from source")):
            self.assert_failure(3, "PXL009")

    def test_regular_sources_still_compile(self):
        code, out, err = self.run_cli()
        self.assertEqual(0, code, err)
        self.assertEqual("", out)
        self.assertEqual("", err)
        self.assertTrue(self.output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("parts/tile.pxl", self.manifest.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
