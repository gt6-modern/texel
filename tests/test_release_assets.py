import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.check_release_assets import AssetVerificationError, verify_assets, local_assets


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


if __name__ == '__main__':
    unittest.main()
