
# Getting started

Install the two Python tools with `python -m pip install texel-importer texel-compiler`. Run `texel-import image.png` to create `image.pxl`, then compile it with `python -m texel_compiler compile image.pxl --asset-root . -o image.png`. The importer is a migration path; normal builds compile checked-in PXL sources.

The Java CLI is distributed in GitHub Releases. Extract it and run `bin/texel-compiler capabilities --json` to inspect its supported profile. Libraries may depend on `io.github.gt6-modern:texel-core:0.1.0`; the command-line application is `io.github.gt6-modern:texel-cli:0.1.0`.

PXL paths are logical, slash-separated paths below an explicit asset root. Use `--deps dependencies.tsv` to write the deterministic dependency manifest used by incremental builds. See [architecture](architecture.md) and the [normative specification](../spec/PXL.md).
