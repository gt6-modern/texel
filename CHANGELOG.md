
# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `expected.error_detail`: an optional negative-case file (Core §13.1, §13.2c; Extension §13.3.4)
  holding one substring per line. When present, a failing implementation must match at least one
  line, so "rejected with the right code for the wrong reason" no longer passes. Applied to the
  38 PNG rejects that all share `PXL006`; `python_ref_verify` and the Java PNG gate both enforce it.

### Fixed
- Core §8.5 now states that a value check depending on another chunk must first establish that the
  chunk has appeared. Concretely: a palette image whose `tRNS` precedes `PLTE` is an ordering
  rejection, not a "tRNS longer than PLTE entries" rejection. The Java decoder compared against a
  fallback of zero entries and reported the length rule, while the importer reported the ordering
  rule - same code, different first-failing rule. Java now checks ordering first, and a new
  `neg-png-trns-length-over-plte` case keeps the length rule covered on its own.
- The normative text claimed `PXL001` for every non-zero `version` while the wire format has
  been version 1 throughout. `spec/PXL.md` now states one rule in Core §3.2, §11 and §12:
  an in-domain version the compiler does not support is `PXL007`, and only a missing,
  non-integer, negative, out-of-domain or unrepresentable value is `PXL001`.
- `version` is no longer described as a reserved field next to `variant`; `bindings` is
  described as a compile-session input (Extension §6.3) rather than a document field.
- The core scope statement no longer claims that PXL never enters a build pipeline.
- Added conformance coverage for the version dispatch: `neg-version-zero` and
  `neg-version-unsupported` (`PXL007`), `neg-version-not-integer` (`PXL001`) in the base
  suite, and `neg-version-in-domain-unsupported` in the components suite. Generator and
  reference oracles now reject in-domain versions with `PXL007` instead of `PXL001`.
- `check_spec_sync.py` takes section numbers in full and ignores `PRD Extension §x`
  provenance, and the English section map now lists exactly the identifiers the Chinese
  text carries instead of seven identifiers that existed nowhere in the specification.
- `ci.yml` runs `check_spec_sync.py` and `check_release_metadata.py`; both were required by
  `AGENTS.md` but never executed by CI. README and CONTRIBUTING list the same gates as
  `AGENTS.md`.
- 45 stale `AGENTS.md §x.y` citations across the spec, tools, tests, Java sources and the
  corpus pointed at a numbered section layout that no longer exists; they now cite the
  seven contracts, and historical references are marked as such.

- The Python compiler's batch entry (`texel-compiler-python PATH...`) now resolves assets
  relative to the source file's directory, like the Java CLI's same-shaped entry already did.
  It used to render through the in-memory API, which has no asset root, so any document that
  stamped a sibling `parts/*.pxl` failed with `PXL009` while `texel-compiler` compiled the
  same file.
- `compile --deps FILE` writes the manifest only after the PNG lands. The Python CLI wrote it
  first, so a failed PNG write left a manifest updated for an image that was never produced -
  exactly the half-state the module promises never to leave. Java already ordered it this way.
- The legacy batch entry rejects an empty `-o` and a `-o` that names an existing directory as
  usage errors (exit 2, `CLI004`), as Java does. Python used to run into the temporary file
  rename, exit 1 and print the internal temporary file name.
- Directory-mode collection no longer follows junctions. `os.walk(followlinks=False)` only
  consults `islink`, and on Windows a junction is not one, so both the Python compiler and the
  importer walked out of the given directory and produced outputs for files outside it. Both
  now prune symlinks, junctions and reparse points while walking.
- The importer collects `*.png` with the same rules as the compilers' batch entries: literal
  extension only (`X.PNG` is reported as skipped instead of being imported on hosts whose file
  name matching ignores case) and one stderr note per skipped candidate.
- The release page's `**Full Changelog**` line is no longer silently dropped: the
  `github-release` job checked out with the default shallow clone, so `git describe` could never
  find the previous `v*` tag and `|| true` hid the failure. The checkout now fetches full
  history and all tags, and a failed `git describe` warns instead of disappearing.

- `compile` publishes the PNG and the `--deps` manifest as one transaction in both
  compilers: each artifact is staged to a sibling temporary file, and only when both are
  written are they renamed into place - a rename that fails rolls the PNG back. Existing
  targets are *copied* aside for that rollback, never moved: a target path is never absent,
  and a single output is still exactly one atomic replace (the first cut of this fix moved the
  old artifact away first, which left the path missing and a lone hidden backup if the process
  died). Python and Java each used to run two independent writes, so a failing manifest left a
  *new* PNG with a stale manifest behind (exit 4, "new product, old manifest"), and
  `--deps -` printed the manifest before the PNG existed. A target that already exists but is
  not a regular file (a directory) is now a usage error instead of a failed rename.
- "Output and manifest are the same path" now asks the filesystem too, in both compilers:
  Python gained the `os.path.samefile` half of the check the Java CLI already had
  (`Files.isSameFile`). `-o out.png --deps alias.tsv` with `alias.tsv` a hard link to
  `out.png` used to exit 0 and silently leave only the PNG, because `resolve()` cannot see
  hard links.
- Rolling back a failed `compile` now restores the *directory entry*, not just the bytes, in both
  compilers: a hard-linked output comes back as the same inode, and a symbolic-link output comes
  back as a link with the same target. A backup is a second hard link for a regular file, a second
  link for a symbolic-link product, and only falls back to a streamed copy when the target provably
  has no other name (`nlink == 1`, or a filesystem without hard links) - so no old artifact is
  read into memory while the newly compiled one is live.
- The Python publish transaction cleans up on **every** failure, not only `OSError`: an
  injected `MemoryError` used to leave `.texel-*.tmp` files in the output directory. Its
  fallback backup copy streams in chunks instead of `read_bytes()`.
- The Java backup copy is staged into a temporary file and renamed into place, so an aborted
  copy (disk full, read error) can no longer leave a partial `.prev` that nothing tracks. A
  staged or rolled-back file is now cleaned from a `finally`, so an `OutOfMemoryError` in the
  write, the flush or a restore no longer skips the remaining cleanup and the "backup kept at
  <path>" report.
- Python compiler diagnostics escape control characters and the code points `splitlines()`
  treats as line breaks (U+000B, U+000C, U+001C-U+001E, U+0085, U+2028, U+2029), the same
  form the Java CLI's `escape()` uses. A Windows file name containing U+2028 used to split one
  error into four lines, three of which had no command prefix at all. All Python compiler
  stderr now goes through a single writing point, and `tests/test_cli_identity.py` pins that.

- The batch entries of both compilers now refuse an output that is the same *file* as one of the
  inputs, not merely the same path. `texel-compiler -o entry.png entry.pxl`, where `entry.png` is a
  hard link of `entry.pxl`, used to exit 0 and truncate the entry into a PNG; two outputs that are
  hard links of each other were silently collapsed into one. Python compares device + inode,
  Java asks `Files.isSameFile` - `BasicFileAttributes.fileKey()` is always null on Windows, so the
  identity check cannot be built on it.

- A rollback that itself fails no longer deletes the backup it could not use. Both compilers used
  to put the failed record back into the cleanup set, so a double failure (commit failed, restore
  failed) returned exit 4 with the *new* artifact in place **and** the only copy of the old one
  gone. The backup is now left in the directory and its path is printed.
- A backup that cannot be created as a hard link now aborts the publish when the target still has
  other names (`nlink > 1`) instead of silently degrading to a content copy: the copy would come
  back as a new inode and break the links between the existing names for good. The copy fallback is
  only used when the target provably has no other name (`nlink == 1`, or a filesystem without
  hard links at all). A failed command must not change the filesystem.

- A symbolic-link product is backed up as another link on disk, not as text held in memory, and
  restoring it is a single atomic rename. The old form had to delete the target before recreating
  the link; when that failed the target was gone and the "backup" only existed inside the process.
  Publishing over a symbolic link now aborts if that backup link cannot be created.
- Java `check ENTRY --asset-root ROOT --deps -` prints the dependency manifest. It returned 0 with
  an empty stdout, while the Python CLI printed it. `check --deps FILE` now also publishes that
  file only after the PNG encoding step succeeds: writing it first left a fresh manifest behind
  when encoding ran out of memory and the command failed.
- A batch root whose path contains `..` or `.` is no longer mistaken for a junction by the Java
  CLI. The "real path" comparison normalised the left side only, so `compiler/java/..` was refused
  as a link; both sides are normalised now.
- An unreadable directory in directory mode is an I/O failure (exit 1, one line naming the input)
  instead of a silently skipped subtree. The Python compiler used to skip it and could exit 0 with
  an incomplete output set; the importer did the same.
- A directory given as a batch root that is itself a symbolic link or junction is refused by all
  three CLIs. Pruning inside the walk only looked at children, so naming a link as the root still
  walked the link target.
- `texel-compiler-python` (the console script) now converts a top-level `MemoryError` into
  `CLI001` with exit 4. The guard lived only in `__main__.py`, which the console script never
  went through, so the released command leaked a traceback.
- Importer diagnostics escape control characters and the code points `splitlines()` treats as line
  breaks, like the compiler does: `--bad<LF>option` or an OS error quoting a path with U+2028 used
  to break the "one line per diagnostic" contract.

- Both PNG encoders now refuse a size the decoder rejects. `encode_png(0, 0, ...)`,
  `encode_png(65537, 1, ...)` and anything whose `width * height` exceeds 2^22 used to be accepted
  and produced a PNG that Texel's own reader rejects with `PXL006`: the length check looked at the
  RGBA buffer only, and Java checked each side but not the area (2026-11-21 review #9/#10).
- A lone surrogate in a JSON string is `PXL001` in both compilers. Python rejected it while Java
  returned the half code point and carried on to the next stage, so the same document was
  `PXL001: 孤立的低代理项` on one side and `PXL004: 未知 op` on the other (#11).
- `capabilities --json` from the Python CLI is now byte-identical to
  `spec/conformance/capabilities/pxl-v1.json`. It reported `compiler_id
  io.github.texel.python` and a one-line `language_versions`, so the two compilers and the CLI
  disagreed on the engine-independent profile they all claim to publish (#12).
- Concurrent in-process compiles no longer share one temporary file name. The staged name was
  `.texel-<pid>-<tag>.tmp`, so two threads writing different outputs into the same directory
  failed with `CLI003 ... File exists`; the name now carries a per-call sequence number and
  `O_EXCL` arbitrates collisions (#13).
- A fully offscreen `stamp` no longer copies the asset. Python built one slice per asset row
  before clipping, so a 2048x2048 asset stamped outside a 1x1 canvas copied 16 MiB per op while
  the budgeted clipped write area stayed 0; only the rows that can be written are sliced now (#18).
- `-o` with a name longer than the filesystem allows is a one-line diagnostic with exit 1 -
  `写出失败` when the failure surfaces at write time, `无法解析输入/输出路径` when it surfaces
  while planning - instead of a traceback. The directory probe ran during argument parsing,
  before the guard that turns `OSError` into a diagnostic, and `ENAMETOOLONG` escaped it (#26).
- A POSIX file name that is not valid UTF-8 is imported instead of failing the batch: the sort key
  is `os.fsencode(...)`, the reversible form, instead of `str.encode("utf-8")` on a string that
  carries surrogates (#25). The Python compiler's batch entry had the same sort key and the same
  defect, and is fixed with it.
- The release workflow only treats a confirmed HTTP 404 as "this version is not published", and
  requires the whole asset set before calling a version published: PyPI's wheel *and* sdist,
  Central's `texel-core` *and* `texel-cli`. TLS/DNS/403/429/5xx and half-published versions now
  fail the job instead of sending it on to a non-idempotent upload (#20/#21). The probes live in
  `tools/release_probes.py` so the rules are testable without the network.
- A release asset directory must contain the fixed asset set - CLI archive, application
  distribution, Central bundle, both Python distributions (wheel and sdist each), `SHA256SUMS` and
  a parseable SPDX SBOM covered by that manifest - and nothing else. Previously any directory with
  `SHA256SUMS` and one regular file passed, so a stray `payload.bin` would have been published as
  a release (#19). `tools/check_release_assets.py` gained `require_release_set`, enforced by the
  build job before any artifact is uploaded and again by `tools/release_state.py` before the
  Release page is created or modified.
- A rollback never deletes a backup it did not create. Backup names are now
  allocated exclusively (`.prev`, `.prev.1`, …): a `.prev` left behind by an earlier
  "commit failed, restore failed" run may be the only copy of the old artifact, and
  creating the backup failing used to delete it.
- A rollback that cannot delete the artifact this run created now says so, naming the
  path it left on disk. Failure to unlink was swallowed, so the command reported only
  the manifest failure while a new PNG stayed behind.
- The batch entry refuses an output that would overwrite a dependency it just compiled:
  `-o parts/blade.pxl` used to truncate the referenced sub-document into a PNG and
  still exit 0. The pre-flight can only see the inputs, so this is checked once each
  entry's dependency list is known, before its artifact is written.
- `tools/check_release_metadata.py` reads `texel_compiler.__version__` too, so a bump that forgets
  the package's own `__version__` fails the gate instead of shipping a wheel whose metadata and
  runtime version disagree (#22). The bump checklist in `docs/releasing.md` names all seven files
  the gate reads, and no test hardcodes the version any more: `tests/test_capabilities.py` and
  `tests/test_public_release.py` derive the expected value from `VERSION` (#23).

### Changed
- `--help` now looks the same in all three CLIs (`texel-compiler`, `texel-compiler-python`,
  `texel-import`): a `usage:` first line with aligned synopsis lines, an indented option
  table with a description per option, and a closing note paragraph. Each one names its real
  command rather than `python -m ...`, and the Python compiler help finally lists its
  options at all.
- Every diagnostic line now starts with the real command name. The Python compiler used to
  say `texel:`, the importer said `importer:` and left its per-file failures unprefixed; both
  now say `texel-compiler-python:` / `texel-import:` like the Java CLI says
  `texel-compiler:`, and the importer's two English messages are Chinese like the rest.
  `tests/test_cli_identity.py` pins the help layout, diffs the Java text against the Python
  one, and asserts each implementation still has exactly one prefixed stderr exit, so none of
  this can drift apart again.

## [0.1.0] - 2026-11-12

### Added
- First public PXL version 1 specification and conformance corpus.
- Deterministic Java and Python compilers, PNG importer, and Gradle integration.
- PyPI, Maven Central, and GitHub Release packaging metadata.

[Unreleased]: https://github.com/gt6-modern/texel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gt6-modern/texel/releases/tag/v0.1.0
