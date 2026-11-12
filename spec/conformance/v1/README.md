# Component conformance suite

This indexed partition covers current PXL assets, parameters, bindings,
logical-path resolution, dependency graphs, session budgets, and error
precedence. `CASES.tsv` is authoritative and every indexed case maps to one
directory under `cases/`.

The partition name is retained as a stable filesystem location. It is selected
as the `components` suite by `../profiles.tsv`; there is no compatibility or
historical-version suite.

Each case uses `entry.pxl`. Positive cases declare pixel and/or dependency
expectations. Negative cases declare one `PXLnnn` code. Binding cases provide
`bindings.json` and named expectations. Generated inputs must be reproducible
with the tools in `../tools/`.
