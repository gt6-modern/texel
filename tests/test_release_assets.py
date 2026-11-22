import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.check_release_assets import (AssetVerificationError, local_assets,
                                        require_release_set, required_assets,
                                        verify_assets)

VERSION = '0.2.0'


def write_release_set(root, version=VERSION):
    """Write the complete asset set `docs/releasing.md` promises.

    每一件都写成可区分的内容：验证器只看集合、大小与摘要，不需要真是压缩包。
    """
    for name in required_assets(version):
        if name == 'SHA256SUMS':
            continue
        if name.endswith('.spdx.json'):
            (root / name).write_text(json.dumps({
                'spdxVersion': 'SPDX-2.3',
                'name': 'texel-' + version,
                'packages': [{'name': 'texel', 'SPDXID': 'SPDXRef-Package-texel'}],
            }), encoding='utf-8')
        else:
            (root / name).write_bytes((name + '\n').encode('ascii') * 3)
    rewrite_sums(root)


def rewrite_sums(root, include=None):
    names = sorted(path.name for path in root.iterdir()
                   if path.is_file() and path.name != 'SHA256SUMS')
    lines = []
    for name in names:
        if include is not None and name not in include:
            continue
        lines.append(hashlib.sha256((root / name).read_bytes()).hexdigest() + '  ' + name)
    (root / 'SHA256SUMS').write_text('\n'.join(lines) + '\n', encoding='ascii')


def remote_metadata(root):
    return [dict(name=p.name, size=p.stat().st_size,
                 digest='sha256:' + hashlib.sha256(p.read_bytes()).hexdigest(),
                 state='uploaded') for p in sorted(root.iterdir())]


class ReleaseAssetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b'release payload\n'
        (self.root / 'payload.bin').write_bytes(self.payload)
        digest = hashlib.sha256(self.payload).hexdigest()
        (self.root / 'SHA256SUMS').write_text(digest + '  payload.bin\n', encoding='ascii')
        self.assets = [dict(name=p.name, size=p.stat().st_size,
                            digest='sha256:' + hashlib.sha256(p.read_bytes()).hexdigest(),
                            state='uploaded') for p in sorted(self.root.iterdir())]

    def test_exact_match(self):
        verify_assets(self.root, self.assets)

    def test_rejects_bad_remote_metadata(self):
        for key, value in [('digest', 'sha256:' + '0' * 64), ('digest', None),
                           ('size', 0), ('size', True), ('state', 'starter')]:
            with self.subTest(key=key, value=value):
                assets = copy.deepcopy(self.assets)
                assets[0][key] = value
                with self.assertRaises(AssetVerificationError):
                    verify_assets(self.root, assets)

    def test_rejects_missing_extra_and_duplicate_remote_assets(self):
        for assets in [self.assets[:1], self.assets + [self.assets[0]],
                       self.assets + [dict(self.assets[0], name='extra')]]:
            with self.subTest(assets=assets):
                with self.assertRaises(AssetVerificationError):
                    verify_assets(self.root, assets)

    def test_manifest_must_cover_exact_local_set_with_correct_hashes(self):
        digest = hashlib.sha256(self.payload).hexdigest()
        for text in ['', '0' * 64 + '  payload.bin\n', digest + '  ../payload.bin\n',
                     digest + '  payload.bin\n' + digest + '  payload.bin\n',
                     digest + '  SHA256SUMS\n', digest + '  absent.bin\n']:
            with self.subTest(text=text):
                (self.root / 'SHA256SUMS').write_text(text, encoding='ascii')
                with self.assertRaises(AssetVerificationError):
                    local_assets(self.root)

    def test_manifest_must_include_sbom(self):
        (self.root / 'sbom.spdx.json').write_bytes(b'{}')
        with self.assertRaises(AssetVerificationError):
            local_assets(self.root)

    def test_rejects_directory_and_missing_manifest(self):
        (self.root / 'nested').mkdir()
        with self.assertRaises(AssetVerificationError):
            local_assets(self.root)
        (self.root / 'nested').rmdir()
        (self.root / 'SHA256SUMS').unlink()
        with self.assertRaises(AssetVerificationError):
            local_assets(self.root)


#: 合成目录按本仓库惯例建在 `tests/_tmp/` 下：本机沙箱里 `tempfile` 造出来的目录
#: 既写不进也删不掉（`tests/test_python_compiler.py` 的 workspace() 有同样的注记）。
WORK = Path(__file__).resolve().parent / '_tmp' / 'release-assets'


class ReleaseContractTest(unittest.TestCase):
    """`require_release_set`：发布契约的固定资产集(审查 #19)。

    宽松的 upload-set 检查仍然必要(命名安全、清单覆盖、摘要相符)，但它把"任意两个文件"
    也当成合法输入：一份 `payload.bin` + 覆盖它的 `SHA256SUMS` 就能通过，然后就可能成为
    一个已发布的版本。这里把"完整集合"写成判据。
    """

    def setUp(self):
        import shutil
        self.root = WORK / self._testMethodName.replace('test_', '')
        shutil.rmtree(str(self.root), ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self.root), ignore_errors=True)

    def assert_rejected(self, fragment):
        with self.assertRaises(AssetVerificationError) as caught:
            require_release_set(self.root, VERSION)
        self.assertIn(fragment, str(caught.exception))

    def test_complete_set_passes(self):
        write_release_set(self.root)
        assets = require_release_set(self.root, VERSION)
        self.assertEqual(sorted(required_assets(VERSION)), sorted(assets))

    def test_payload_only_directory_is_rejected(self):
        # The issue's reproduction: a stray payload.bin with a covering manifest.
        payload = b'release payload\n'
        (self.root / 'payload.bin').write_bytes(payload)
        (self.root / 'SHA256SUMS').write_text(
            hashlib.sha256(payload).hexdigest() + '  payload.bin\n', encoding='ascii')
        self.assert_rejected('missing')

    def test_every_required_asset_is_required(self):
        for name in required_assets(VERSION):
            if name == 'SHA256SUMS':
                continue
            with self.subTest(missing=name):
                self.setUp()
                write_release_set(self.root)
                (self.root / name).unlink()
                rewrite_sums(self.root)
                self.assert_rejected(name)

    def test_unexpected_file_is_rejected(self):
        write_release_set(self.root)
        (self.root / 'stray.txt').write_bytes(b'x')
        rewrite_sums(self.root)
        self.assert_rejected('unexpected')

    def test_sbom_must_parse_and_list_packages(self):
        write_release_set(self.root)
        sbom = self.root / ('texel-' + VERSION + '.spdx.json')
        for text, fragment in ((b'not json\n', 'parseable'),
                               (json.dumps({'spdxVersion': 'SPDX-2.3', 'packages': []}), 'packages'),
                               (json.dumps({'packages': [{'name': 'x'}]}), 'spdxVersion')):
            with self.subTest(fragment=fragment):
                sbom.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))
                rewrite_sums(self.root)
                self.assert_rejected(fragment)

    def test_manifest_must_still_cover_the_set(self):
        write_release_set(self.root)
        victim = required_assets(VERSION)[0]
        rewrite_sums(self.root, include=[name for name in required_assets(VERSION)
                                         if name != victim])
        self.assert_rejected('SHA256SUMS')

    def test_tampered_asset_is_rejected(self):
        write_release_set(self.root)
        (self.root / required_assets(VERSION)[0]).write_bytes(b'tampered\n')
        self.assert_rejected('checksum')

    def test_remote_verification_also_needs_the_full_set(self):
        write_release_set(self.root)
        verify_assets(self.root, remote_metadata(self.root))
        truncated = [asset for asset in remote_metadata(self.root)
                     if asset['name'] != required_assets(VERSION)[0]]
        with self.assertRaises(AssetVerificationError):
            verify_assets(self.root, truncated)

    def test_version_must_be_a_release_version(self):
        write_release_set(self.root)
        for version in ('', 'v0.2.0', '0.2', 'latest'):
            with self.subTest(version=version):
                with self.assertRaises(AssetVerificationError):
                    require_release_set(self.root, version)

    def test_other_versions_are_a_different_set(self):
        write_release_set(self.root)
        with self.assertRaises(AssetVerificationError):
            require_release_set(self.root, '0.2.1')


if __name__ == '__main__':
    unittest.main()
