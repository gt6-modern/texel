# PXL — Pixel Asset Description Format

**Wire version: 1** · **First software release: 0.1.0** · **Status: non-authoritative English translation**

The normative text is [`PXL.md`](PXL.md). If this translation and the Chinese text differ, the Chinese specification controls. Public implementations accept only `"version": 1`. Other integer versions in `0..65535` fail with `PXL007`; missing, non-integral, negative, out-of-domain, or unrepresentable versions fail with `PXL001`.

`Core §` defines raster, PNG, and deterministic single-document semantics. `Extension §` defines the current document model, assets, typed parameters, resolvers, dependency graphs, and compilation-session budgets. Extension §3 replaces the narrower top-level member list in Core §3; every other Core rule remains applicable.

## Specification synchronization manifest

The following manifest is compared byte-for-byte by the bilingual specification gate. It summarizes constants but does not replace the prose.

```json pxl-spec-sync
{
  "wire_version": 1,
  "encodings": ["char", "index", "index4"],
  "non_transparent_limits": [61, 255, 65535],
  "transparent_tokens": [".", "00", "0000"],
  "operations": ["fill", "rect", "pixel", "line", "grid", "mirror", "erase", "stamp"],
  "blends": ["normal", "multiply", "mask"],
  "error_codes": ["PXL001", "PXL002", "PXL003", "PXL004", "PXL005", "PXL006", "PXL007", "PXL008", "PXL009", "PXL010", "PXL011", "PXL012"],
  "max_document_bytes": 67108864,
  "max_canvas_area": 4194304,
  "max_reference_depth": 64,
  "max_session_raster": 16777216
}
```

## Version dispatch

1. Decode strict UTF-8 and parse exactly one JSON object. Duplicate object members are `PXL001`.
2. `format` must be the string `PXL`.
3. `version` must be a representable decimal integer. Value 1 enters the complete validation pipeline; another value in the dispatch domain is `PXL007`; an invalid value is `PXL001`.
4. Implementations must not accept version 0 or silently migrate an input document.

# Part I — Core pixel semantics

## Core §1 Document and scalar model

A PXL file is UTF-8 JSON with an optional UTF-8 BOM, no duplicate members, and one top-level object. Canonical output uses LF, no BOM, two-space indentation, stable member order, and one final LF. Readers accept the explicitly documented non-canonical JSON forms but never accept duplicate members.

The top-level object contains `format`, `version`, `encoding`, `width`, `height`, optional `palette`, `layers`, and the Extension §3 members. Width and height are positive exact integers. Numeric validation is decimal and exact; binary floating-point rounding must not decide integrality or bounds.

## Core §2 Encodings and palette

Slot 0 is transparent. Only RGBA `00000000` maps to slot 0. A pixel with alpha 0 and non-zero RGB is a normal palette entry. Non-transparent colors are allocated in row-major first-occurrence order.

- `char` supports at most 61 non-transparent colors and uses one-character tokens; `.` is transparent.
- `index` supports at most 255 and uses two uppercase hexadecimal digits; `00` is transparent.
- `index4` supports at most 65,535 and uses four uppercase hexadecimal digits; `0000` is transparent.

One file uses one token width. A non-transparent token denotes its numeric palette slot, not a zero-based palette-array index. Lowercase hexadecimal is invalid.

## Core §3 Canvas, layers, sprites, and operations

The canvas is row-major RGBA8. Layers render in listed order. Each layer has a unique portable name, one blend mode, and an ordered operation list. Internal sprites use the document encoding and palette.

The eight operations are `fill`, `rect`, `pixel`, `line`, `grid`, `mirror`, `erase`, and `stamp`. Operations write whole pixels only and never anti-alias. `rect` uses half-open bounds. `line` is the specified inclusive integer Bresenham walk. Drawing is clipped to the canvas; clipping must not overflow. `mirror` reads a snapshot of already rendered content so overlapping source and destination are deterministic. `erase` writes transparent RGBA. `stamp` copies an internal sprite or an Extension asset, with the declared flips and clipping.

Unknown operations are `PXL004`. Invalid tokens or palette slots are `PXL005`. Structural errors are `PXL001`; numeric/resource bounds use `PXL002` or `PXL003` according to the normative phase table.

## Core §4 Integer blending and transparency

Blend modes are `normal`, `multiply`, and `mask`. Every channel calculation, alpha composition, division, and rounding step uses the integer formulas in the normative Chinese text. Floating-point color arithmetic is forbidden. Fully transparent source pixels preserve the underlying RGB where the formula specifies identity; the exact alpha-zero RGB distinction is never collapsed into slot 0.

## Core §5 PNG input profile

PNG decoding validates the signature, every chunk length and CRC, chunk ordering, critical-chunk set, zlib stream, Adler checksum, filter bytes, exact inflated length, color-type rules, palette indices, and `tRNS` length. Supported input is non-interlaced 8-bit PNG in the color types enumerated by the conformance corpus. 16-bit, sub-8-bit, Adam7, malformed, unknown-critical, and over-budget input is rejected as `PXL006`; no silent conversion is allowed. Unknown ancillary chunks are ignored only after their CRC is validated.

## Core §6 Deterministic PNG output and pixel equivalence

Equivalence means identical width, height, and row-major RGBA8 bytes. PNG file bytes need not match an imported file because ancillary chunks and deflate representation are not semantic pixels. Compiler output is deterministic: fixed chunk order, fixed filtering and compression parameters, no clock, randomness, locale, filesystem iteration order, or process-specific data.

## Core §7 Importer canonicalization

Importer palette allocation and compression follow the deterministic algorithm in the normative text. A solid image becomes one `fill`; eligible same-color runs become `rect`; remaining regions become canonical `grid` blocks. The operation budget fallback is global and deterministic. Canonical and raw-grid forms must render to identical pixels.

## Core §8 Single-document limits

The UTF-8 document limit is 64 MiB and canvas area is at most 2^22 pixels. Palette capacity follows the selected encoding. Layer, operation, sprite-row, token, and raster-work limits are the exact values in the normative text and corpus. Limits are evaluated with overflow-safe integer arithmetic and at the specified error-priority phase.

# Part II — Static assets, parameters, and compilation sessions

## Extension §3 Current document model

A current document requires `format`, `version`, `encoding`, `width`, `height`, and non-empty `layers`; it may contain `palette`, `sprites`, `assets`, and `params`. No other top-level member is allowed. `assets` and `params`, when present, are objects rather than null.

## Extension §4 Names and references

Portable names use the normative lowercase ASCII grammar and are unique in their namespace. A parameter reference is a JSON string in the exact `$name` form and is legal only at whitelisted typed positions. Literal strings outside those positions are not references. Reference syntax, undeclared references, type mismatch, and missing required values are `PXL012` in parameter-phase order.

## Extension §5 External assets

An asset declaration has `kind` (`pxl` or `png`), a logical `path`, and optional `params` only for PXL children. A `stamp` selects exactly one internal sprite or external asset. Only selected assets are resolved. A PXL child is compiled with its mapped typed parameters; a PNG child is decoded under Core §5. Declared kind and actual bytes must agree.

## Extension §6 Parameters and bindings

Parameters have one of the normative types (`color`, `integer`, `boolean`, `sprite`, or `asset`), an optional canonical default, and required/default semantics. Bindings are supplied by the host for the entry document; child mappings may forward literals or references. Canonical integer spelling, boolean spelling, RGBA color spelling, and symbol identity are part of cache identity and dependency determinism.

Validation order is declaration syntax, reference syntax, binding presence/name/type, default symbol validation, substitution, then validation of the resulting concrete document. Substitution occurs only at the whitelist positions and cannot manufacture object member names or arbitrary JSON.

## Extension §7 Logical paths

Logical paths are relative, slash-separated, lowercase portable paths. Empty segments, `.`/`..`, backslashes, absolute paths, drives, UNC forms, NUL, forbidden characters, overlong segments, and root escape are `PXL008`. A child path is resolved relative to the declaring PXL document's logical directory and normalized before resolver access.

## Extension §8 Asset root and resolver

Compilation receives one entry logical path, one explicit asset resolver/root, and entry bindings. Resolver results distinguish `OK`, `MISSING`, `CASE_MISMATCH`, and `ESCAPE`. Host I/O failures are not language-format errors and are mapped to stable CLI errors. The filesystem resolver must prevent symlink, junction, hardlink, case-folding, and canonical-path escapes without relying on directory traversal order.

## Extension §9 Dependency graph and manifest

The selected dependency graph is fixed before raster output. Cycles are `PXL010`; missing or kind-mismatched selected dependencies are `PXL009`. Identity includes normalized logical path and canonical parameter bindings. The manifest contains the entry and every selected dependency once, uses lowercase SHA-256 of original bytes, has no header, and sorts dependency rows by ASCII logical path after the entry row.

## Extension §10 Compilation-session budgets

The session limits reference depth, node count, distinct file count, cumulative input bytes, cumulative decoded pixels, and global raster work. Exceeding one is `PXL011` in the normative priority order; equality with each maximum is legal. Reusing a compiled node does not double-count its internal work, while each actual stamp contributes its clipped raster work.

Host resident memory is not a language acceptance limit. Exhaustion is reported as a controlled host/CLI failure, does not become `PXL011`, and must not leave partial output.

## Extension §11 Error codes and priority

`PXL001` structure/JSON; `PXL002` document numeric/resource bounds; `PXL003` encoding/numeric field domain; `PXL004` unknown operation; `PXL005` token/palette slot; `PXL006` PNG; `PXL007` unsupported wire version; `PXL008` path/resolver boundary; `PXL009` selected dependency/kind; `PXL010` cycle; `PXL011` session budget; `PXL012` parameters/bindings.

The full phase table in the Chinese specification is normative. Implementations validate by whole phases rather than object traversal accident, so simultaneous defects produce the same first code in every implementation.

## Extension §12 Determinism

Bindings, asset declarations, filesystem results, and manifest rows are processed in specified ASCII order. Cache state, hash-map order, locale, host separator, and warm/cold traversal must not change pixels, manifests, errors, or output bytes.

## Extension §13 Conformance

The repository corpus is the executable acceptance boundary. Positive cases assert dimensions, RGBA8 SHA-256, dependency manifests, and repeatability. Negative cases assert the exact first error and absence of partial output. Generated cases carry reproducible recipes; every checked-in expectation must be independently derivable rather than copied from one tested compiler.

## Extension §14 Reserved features

Unknown top-level fields, unknown operations, 16-bit channels, Adam7, color-management semantics, randomness, clocks, and engine-specific build behavior are outside this release and are rejected or ignored only as explicitly specified above.

## Normative section map

The Chinese specification in `spec/PXL.md` is the sole adjudicating text. This map lists
every normative section identifier so a citation resolves to the same clause in either
language; it carries no normative content of its own.

### Extension §3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §9.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### 附录 C

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §1.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### 附录 A

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §1.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §14

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §3.8

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §4.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.0

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.0.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.0.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §5.8

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §6.7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §8.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §9

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §10

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §11

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §11.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §11.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §11.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §12

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Core §13.7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### 附录 B

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §3.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §3.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §3.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §5.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §4.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §4.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §5.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §5.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §6.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §11.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §8

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §8.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §8.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §8.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §8.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §9

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §9.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §9.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §9.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10.2.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §10.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §11

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §12

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.1

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.2

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.3

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.6

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.3.7

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.4

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.

### Extension §13.5

The identically numbered clause in `spec/PXL.md` is authoritative; this entry keeps a
bilingual citation pointing at the same identifier.
