# Real gh HTTP fixtures

Captured 2026-11-21 with gh version 2.98.0 (2026-08-20).

Commands: `gh api repos/gt6-modern/texel/releases/tags/v0.1.0 --include`
and the same command with tag `v-codex-fixture-not-found`.
Stdout is retained with only credential-related response headers removed.
Status/header/body delimiters and public release data are unchanged.
200 exits 0. 404 exits 1; stderr contained `gh: Not Found (HTTP 404)` in this
capture, but may be empty with other gh versions. Stderr never determines state.
Rate-limit and malformed responses are derived in tests, not claimed as captures.
