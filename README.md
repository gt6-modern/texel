
# Texel

[简体中文](README.zh-CN.md) · [PXL specification (English)](spec/PXL.en.md) · [Normative specification (中文)](spec/PXL.md)

Texel is an engine-independent toolchain for losslessly mirroring RGBA8 bitmaps as readable, reviewable PXL source files and compiling PXL back to deterministic PNG files.

PXL makes a pixel change reviewable as a token at a coordinate instead of an opaque binary diff. It can be used as a text mirror for existing images or as the source format for composable pixel assets. PNG remains the deployable artifact.

> **Release status:** 0.1.0 is the first public, pre-1.0 release. PXL wire version 1 is the only supported language version.

## Components

| Component | Purpose | Runtime |
| --- | --- | --- |
| `texel-importer` | PNG → PXL migration and round-trip import | Python 3.8+, standard library only |
| `texel-compiler` | Independent Python PXL compiler | Python 3.8+, standard library only |
| `texel-core` | Embeddable Java PXL compiler | Java 8 bytecode, `java.base` only |
| `texel-cli` | Java command-line compiler | Java 8+ |

## Five-minute workflow

```console
python -m pip install texel-importer texel-compiler
texel-import texture.png
python -m texel_compiler compile texture.pxl --asset-root . -o rebuilt.png
```

The importer writes a self-contained `version: 1` PXL document next to the PNG. For build integrations and the Java CLI, see [Getting started](docs/getting-started.md).

A minimal PXL document:

```json
{
  "format": "PXL",
  "version": 1,
  "encoding": "char",
  "width": 1,
  "height": 1,
  "palette": ["FF0000FF"],
  "layers": [{
    "name": "base",
    "blend": "normal",
    "ops": [{"op": "fill", "slot": 1}]
  }]
}
```

## Guarantees

- Round trips are compared by raw row-major RGBA8 pixels, not PNG file bytes.
- Palette allocation, rendering, dependency manifests, and PNG output are deterministic.
- Color arithmetic is integer-only; operations never create partial pixel coverage.
- Only exact `00000000` uses the transparent slot. RGB values with alpha 0 remain distinct colors.
- 16-bit and Adam7 PNG input are rejected explicitly.

## Build and test

```console
python spec/conformance/tools/gen_cases.py --all
python spec/conformance/tools/check_spec_sync.py
python tools/check_release_metadata.py
python -m unittest discover -s tests
python spec/conformance/tools/python_ref_verify.py
python compiler/python/tools/conformance.py
cd compiler/java
./gradlew --no-daemon clean check -Ppython=python
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete cross-platform gates. The normative language definition is [spec/PXL.md](spec/PXL.md); the two compiler implementations align through that specification and the shared conformance corpus, not shared production code.

## Community and security

- Questions: [SUPPORT.md](SUPPORT.md)
- Contributions: [CONTRIBUTING.md](CONTRIBUTING.md)
- Vulnerabilities: [SECURITY.md](SECURITY.md)
- Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

Code is licensed under the [Apache License 2.0](LICENSE). Sample assets have separate CC0 provenance recorded in [`samples/PROVENANCE.md`](samples/PROVENANCE.md) and [`samples/MANIFEST.tsv`](samples/MANIFEST.tsv).
