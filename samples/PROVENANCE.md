# Sample provenance

The PNG files in this directory are byte-for-byte test fixtures derived from [`gt6-modern/gt6-modern`](https://github.com/gt6-modern/gt6-modern) commit `0d8c76109f46b52e4fd4f30b705f8407d01574ab`, under **CC0 1.0**. Their upstream root is `neoforge/src/main/resources/assets/gt6modern/textures/`.

`MANIFEST.tsv` records every copied path, byte length, SHA-256, license, and upstream commit. Recreate or verify real cases by setting `PXL_TEXTURE_ROOT` to a checkout's texture root (or passing `--samples-root`) and running `python spec/conformance/tools/gen_real_cases.py --check`. No maintainer-local absolute path is part of the repository contract.
