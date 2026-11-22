"""Validate the complete upload set before an irreversible Release publication."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


class AssetVerificationError(ValueError):
    pass


def required_assets(version):
    """The asset set ``docs/releasing.md`` promises for ``version`` (issue #19).

    These are *fixed names*: they are what the build job produces, what PyPI and
    Central carry, and what a consumer of the release expects to download. A
    verifier that accepts "`SHA256SUMS` plus any one regular file" passes a
    directory holding nothing but a stray `payload.bin`, and that directory then
    becomes a published release.
    """
    return (
        'texel-cli-' + version + '.tar.gz',
        'texel-compiler-' + version + '.zip',
        'texel-' + version + '-central.zip',
        'texel_importer-' + version + '-py3-none-any.whl',
        'texel_importer-' + version + '.tar.gz',
        'texel_compiler-' + version + '-py3-none-any.whl',
        'texel_compiler-' + version + '.tar.gz',
        'SHA256SUMS',
        'texel-' + version + '.spdx.json',
    )


def _check_sbom(path):
    """The SBOM must be a parseable SPDX document, not a renamed empty file."""
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AssetVerificationError('SBOM is not parseable JSON: %s' % exc)
    if not isinstance(document, dict):
        raise AssetVerificationError('SBOM must be a JSON object')
    if not str(document.get('spdxVersion', '')).startswith('SPDX-'):
        raise AssetVerificationError('SBOM has no spdxVersion')
    packages = document.get('packages')
    if not isinstance(packages, list) or not packages:
        raise AssetVerificationError('SBOM lists no packages')
    return document


def require_release_set(directory, version):
    """Enforce the release contract on top of the generic upload-set checks (#19).

    Called from ``release_state.publish`` before any remote mutation and from the
    build job before any artifact is uploaded: an incomplete set must fail while
    failing is still free.
    """
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version or ''):
        raise AssetVerificationError('release version must be MAJOR.MINOR.PATCH')
    assets = local_assets(directory)
    expected = set(required_assets(version))
    missing = sorted(expected - set(assets))
    if missing:
        raise AssetVerificationError('release set is incomplete, missing: ' + ', '.join(missing))
    extra = sorted(set(assets) - expected)
    if extra:
        raise AssetVerificationError('unexpected files in the release set: ' + ', '.join(extra))
    _check_sbom(Path(directory) / ('texel-' + version + '.spdx.json'))
    return assets


def local_assets(directory):
    directory = Path(directory)
    paths = sorted(directory.iterdir())
    if not paths or any(p.is_symlink() or not p.is_file() for p in paths):
        raise AssetVerificationError('assets must be a nonempty directory of regular files')
    result = {}
    for path in paths:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', path.name):
            raise AssetVerificationError('unsafe asset name: ' + path.name)
        data = path.read_bytes()
        result[path.name] = {'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
    if 'SHA256SUMS' not in result or len(result) < 2:
        raise AssetVerificationError('SHA256SUMS and at least one payload are required')
    entries = {}
    try:
        for line in (directory / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)', line)
            if not match or match[2] in entries or match[2] == 'SHA256SUMS':
                raise AssetVerificationError('invalid or duplicate SHA256SUMS entry')
            entries[match[2]] = 'sha256:' + match[1]
    except UnicodeError as exc:
        raise AssetVerificationError('SHA256SUMS must be ASCII') from exc
    if set(entries) != set(result) - {'SHA256SUMS'}:
        raise AssetVerificationError('SHA256SUMS must cover every payload exactly once')
    if any(entries[name] != result[name]['digest'] for name in entries):
        raise AssetVerificationError('local checksum mismatch')
    return result


def verify_assets(directory, remote_assets):
    expected = local_assets(directory)
    if not isinstance(remote_assets, list):
        raise AssetVerificationError('remote assets must be a list')
    observed = {}
    for asset in remote_assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
            raise AssetVerificationError('malformed remote asset')
        name = asset['name']
        if name in observed:
            raise AssetVerificationError('duplicate remote asset: ' + name)
        observed[name] = asset
    if set(observed) != set(expected):
        raise AssetVerificationError('remote asset names do not match the local upload set')
    for name, value in expected.items():
        asset = observed[name]
        if (asset.get('state') != 'uploaded' or type(asset.get('size')) is not int
                or asset['size'] != value['size'] or asset.get('digest') != value['digest']):
            raise AssetVerificationError('remote size, state or SHA-256 mismatch: ' + name)
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('assets_json', nargs='?',
                        help='remote asset metadata to cross-check; omit with --version')
    parser.add_argument('--version', default=None,
                        help='also enforce the release contract (fixed asset set + SPDX SBOM)')
    args = parser.parse_args()
    if args.assets_json is None and args.version is None:
        parser.error('give assets_json, --version, or both')
    try:
        if args.assets_json is not None:
            verify_assets(args.directory,
                          json.loads(Path(args.assets_json).read_text(encoding='utf-8')))
        if args.version is not None:
            require_release_set(args.directory, args.version)
    except (AssetVerificationError, OSError, ValueError) as exc:
        parser.exit(1, 'release-assets: %s\n' % exc)
    print('release-assets: ok')


if __name__ == '__main__':
    main()
