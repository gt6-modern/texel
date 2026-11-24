# Texel compiler for Python

[简体中文](https://github.com/gt6-modern/texel/blob/master/README.zh-CN.md) · [Repository](https://github.com/gt6-modern/texel) · [Normative PXL specification](https://github.com/gt6-modern/texel/blob/master/spec/PXL.md) · [English specification](https://github.com/gt6-modern/texel/blob/master/spec/PXL.en.md)

Texel compiles PXL — a text format that mirrors an RGBA8 bitmap token by token — back to deterministic PNG. This package is the *compiling* half of that mirror: [`texel-importer`](https://pypi.org/project/texel-importer/) turns a PNG into reviewable PXL source, and `texel-compiler` turns PXL source into the PNG you ship. A pixel change then reads as a diff at a coordinate instead of an opaque binary blob, and PNG stays the deployable artifact.

This is an independent Python 3.8+ implementation of PXL wire version 1. It shares no production code with the Java compiler: the two align through the normative specification and the shared conformance corpus, never through each other. The redundancy is deliberate — a single implementation cannot catch its own misreading of the format.

> **Release status:** 0.1.0 is the first public, pre-1.0 release. PXL wire version 1 is the only supported language version.

## Components

| Component | Purpose | Runtime |
| --- | --- | --- |
| [`texel-importer`](https://pypi.org/project/texel-importer/) | PNG → PXL migration and round-trip import | Python 3.8+, standard library only |
| `texel-compiler` | Independent Python PXL compiler (this package) | Python 3.8+, standard library only |
| [`texel-core`](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-core) | Embeddable Java PXL compiler | Java 8 bytecode, `java.base` only |
| [`texel-cli`](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-cli) | Java command-line compiler | Java 8+ |

## Install and use

```console
python -m pip install texel-compiler
python -m texel_compiler capabilities --json
python -m texel_compiler compile entry.pxl --asset-root . -o output.png --deps dependencies.tsv
python -m texel_compiler check entry.pxl --asset-root .
```

The `texel-compiler-python` console script runs the same entry point. `check` validates a document and reports the first failing rule with its stable `PXL001`–`PXL012` code; `--deps` writes the dependency manifest for every external asset the document selected.

Library entry points are `compile_target`, `encode_png`, `capabilities_json` and `PxlError`, with `Compiled` and `Rendered` carrying results. `render_document` is a convenience for a self-contained version 1 document; a document that stamps external assets requires an explicit resolver through `compile_target`.

A minimal PXL document — a 1×1 red image:

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
- 16-bit and Adam7 PNG input are rejected explicitly rather than guessed at.
- Three encodings cover the palette space without an escape hatch: `char` up to 61 colors, `index` up to 255, `index4` up to 65,535.

## Verify

From a checkout of the repository (the tests and the corpus are not shipped in the wheel):

```console
python compiler/python/tools/conformance.py
python -m unittest discover -s tests
```

The conformance runner selects the `pxl-v1` profile, exercises base pixel semantics and component/dependency cases, asserts exact error codes and dependency manifests, and compares repeated runs for determinism. Against an installed wheel, `python -m texel_compiler capabilities --json` reports the profile and the `PXL001`–`PXL012` code range it implements.

## Registries

| Coordinate | Registry | Purpose |
| --- | --- | --- |
| `texel-importer` | [PyPI](https://pypi.org/project/texel-importer/) | PNG → PXL migration and round-trip import |
| `texel-compiler` | [PyPI](https://pypi.org/project/texel-compiler/) | Independent Python PXL compiler |
| `io.github.gt6-modern:texel-core` | [Maven Central](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-core) | Embeddable Java PXL compiler |
| `io.github.gt6-modern:texel-cli` | [Maven Central](https://central.sonatype.com/artifact/io.github.gt6-modern/texel-cli) | Java command-line compiler |

## Community and security

- Questions: [SUPPORT.md](https://github.com/gt6-modern/texel/blob/master/SUPPORT.md)
- Contributions: [CONTRIBUTING.md](https://github.com/gt6-modern/texel/blob/master/CONTRIBUTING.md)
- Vulnerabilities: [SECURITY.md](https://github.com/gt6-modern/texel/blob/master/SECURITY.md)
- Conduct: [CODE_OF_CONDUCT.md](https://github.com/gt6-modern/texel/blob/master/CODE_OF_CONDUCT.md)

Code is licensed under the [Apache License 2.0](https://github.com/gt6-modern/texel/blob/master/LICENSE); attribution notices are in [NOTICE](https://github.com/gt6-modern/texel/blob/master/NOTICE).
