# Texel compiler for Python

[Repository documentation](../../README.md) · [Normative PXL specification](../../spec/PXL.md)

This is an independent Python 3.8+ implementation of the PXL wire version 1 compiler. It uses only the Python standard library and shares no production code with the Java compiler.

## Install and use

```console
python -m pip install texel-compiler
python -m texel_compiler capabilities --json
python -m texel_compiler compile entry.pxl --asset-root . -o output.png --deps dependencies.tsv
python -m texel_compiler check entry.pxl --asset-root .
```

Library entry points are `compile_target`, `encode_png`, `capabilities_json`, and `PxlError`. `render_document` is a convenience for a self-contained version 1 document; a document that selects external assets requires an explicit resolver through `compile_target`.

## Verify

```console
python -m unittest discover -s tests
python compiler/python/tools/conformance.py
```

The conformance runner selects the `pxl-v1` profile, exercises base pixel semantics and component/dependency cases, asserts exact error codes and dependency manifests, and checks repeated output for determinism.
