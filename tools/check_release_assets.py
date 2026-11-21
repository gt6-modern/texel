"""Validate the complete upload set before an irreversible Release publication."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


class AssetVerificationError(ValueError):
    pass


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
    parser.add_argument('assets_json')
    args = parser.parse_args()
    try:
        verify_assets(args.directory, json.loads(Path(args.assets_json).read_text(encoding='utf-8')))
    except (AssetVerificationError, OSError, ValueError) as exc:
        parser.exit(1, 'release-assets: %s\n' % exc)


if __name__ == '__main__':
    main()
