# PXL conformance corpus

The corpus is the executable acceptance boundary for [`spec/PXL.md`](../PXL.md). `profiles.tsv` contains the sole public profile, `pxl-v1`, which selects two partitions:

- `CASES.tsv` + `cases/`: PNG import, encoding tiers, operations, blending, budgets, error priority, real samples, and batch suites.
- `v1/CASES.tsv` + `v1/cases/`: assets, typed parameters, logical paths, dependency graphs, manifests, session budgets, and resolver boundaries.

The directory name `v1` denotes the only current wire version; it is not a legacy compatibility suite. Every ordinary `.pxl` document in the corpus uses `"version": 1`.

Run:

```console
python spec/conformance/tools/gen_cases.py --all
python spec/conformance/tools/gen_cases.py --check
python spec/conformance/tools/gen_conformance_cases.py --check
python spec/conformance/tools/python_ref_verify.py
python spec/conformance/tools/profile_select.py --profile pxl-v1 --json
```

Generated inputs are described by deterministic recipes. Positive cases assert dimensions and row-major RGBA8 SHA-256; negative cases assert the exact first PXL error. Batch manifests are sorted by UTF-8/ASCII path rules stated in the specification. Missing expected artifacts are corpus failures, never implicit passes.
