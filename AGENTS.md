# AGENTS.md — Texel contributor instructions

Texel is an engine-independent PNG ⇄ PXL toolchain. The public release supports **PXL wire version 1 only**. The normative language text is `spec/PXL.md`; `spec/PXL.en.md` is a non-authoritative English translation.

## Repository map

- `spec/PXL.md`, `spec/PXL.en.md`: language definition.
- `spec/conformance/`: shared corpus and independent reference tools.
- `importer/`: Python 3.8+ PNG → PXL importer, standard library only.
- `compiler/python/`: independent Python PXL compiler, standard library only.
- `compiler/java/`: Java compiler and CLI; Java 8 bytecode, `java.base` only.
- `integrations/`: thin build-system adapters; language semantics never live here.
- `samples/`: CC0 fixtures with `MANIFEST.tsv` provenance.

## Non-negotiable contracts

1. Equivalence means identical dimensions and row-major RGBA8 bytes, not identical imported PNG bytes.
2. Output is deterministic. Do not depend on clocks, randomness, locale, hash iteration, or filesystem enumeration order.
3. Palette assignment is row-major first occurrence. Only exact `00000000` uses transparent slot 0; alpha-zero pixels with non-zero RGB stay distinct.
4. Encodings are `char` (≤61 colors), `index` (≤255), and `index4` (≤65,535), with transparent tokens `.`, `00`, and `0000`.
5. The eight operations are `fill`, `rect`, `pixel`, `line`, `grid`, `mirror`, `erase`, and `stamp`; all are integer-pixel operations without anti-aliasing.
6. PNG input is 8-bit and non-interlaced. Every chunk CRC is validated. Unsupported bit depth, Adam7, or malformed input is rejected explicitly.
7. Java production code targets class major 52 and may use only `java.base`. Python production code supports 3.8+ and uses only the standard library. Add no runtime dependency.

## Change workflow

- Specification first: update the normative Chinese text and its English translation before behavior.
- Corpus first: add positive and negative conformance cases before implementation.
- Keep Java and Python semantically aligned without sharing production code.
- Do not perform unrelated refactors. Preserve existing uncommitted work unless the task explicitly owns it.
- Report tests honestly: list skipped or unavailable checks and never describe an unrun check as passing.

## Required verification

From the repository root:

```console
python spec/conformance/tools/gen_cases.py --all
python spec/conformance/tools/check_spec_sync.py
python tools/check_release_metadata.py
python -m unittest discover -s tests
python spec/conformance/tools/python_ref_verify.py
python compiler/python/tools/conformance.py
```

Then with JDK 21:

```console
cd compiler/java
./gradlew --no-daemon clean check -Ppython=python
```

The Java discipline gate checks every class major version and production module dependency. Platform-specific symlink, junction, hardlink, file-lock, and atomic-write gates must run on the platforms to which their claims apply.
