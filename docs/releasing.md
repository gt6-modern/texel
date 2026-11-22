
# Release runbook

A release publishes the same version to three places from one tag: PyPI
(`texel-importer`, `texel-compiler`), Maven Central (`texel-core`, `texel-cli`),
and a GitHub Release with the CLI archive, the Central bundle, both Python
distributions, `SHA256SUMS` and an SPDX SBOM. `.github/workflows/release.yml` does
the work; this file is the part a human has to do once, and the order to do it in.

Nothing here may be built from a dirty tree, and a published version is never
replaced: ship a patch instead.

## 1. Publisher setup (once)

### Maven Central

1. Verify the `io.github.gt6-modern` namespace in the Central Portal (it is proved
   through ownership of the GitHub organisation).
2. Generate a Portal **user token** (not your login password) and store it as the
   `maven-central` environment secrets `CENTRAL_USERNAME` and `CENTRAL_PASSWORD`.
3. Create a signing key and store it as the `release` environment secrets
   `MAVEN_GPG_PRIVATE_KEY` (the whole armor, including the BEGIN/END lines) and
   `MAVEN_GPG_PASSPHRASE`.

Use an **RSA 4096** signing key. GPG 2.4+ defaults to ed25519, which Gradle's
in-memory signer handles poorly and Central accepts less readily:

```console
gpg --quick-generate-key "Name <email>" rsa4096 sign 2y
gpg --list-secret-keys --keyid-format=long          # note the KEY_ID
gpg --armor --export-secret-keys KEY_ID > signing.asc
gpg --armor --export KEY_ID > public.asc
gpg --send-keys KEY_ID                              # keys.openpgp.org needs e-mail confirmation
```

Export with `--export-secret-keys`, **never** `--export-secret-subkeys`. The latter
replaces the primary key with a `gnu-dummy` stub and exports only the subkeys; if
the subkey is encryption-only the file contains no signable private key and the
build dies in `:cli:signMavenJavaPublication` with
`Cannot invoke "PGPPrivateKey.getPublicKeyPacket()" because "<parameter1>" is null`.
Check before uploading:

```console
gpg --list-packets signing.asc | grep gnu-dummy    # must print nothing
```

The build also prints the key material's byte and line count, so a secret that
does not match the file you tested locally is visible at a glance.

### PyPI

PyPI needs no token: the workflow uses trusted publishing (OIDC). Both packages
come from `release.yml`, and PyPI requires the
`owner / repository / workflow / environment` combination to be **unique across
projects** — a second *pending* publisher with the same values is rejected with
`A pending trusted publisher matching this configuration has already been
registered for a different project name`. That is why each package has its own
environment:

| Field | Package 1 | Package 2 |
| --- | --- | --- |
| PyPI Project Name | `texel-importer` | `texel-compiler` |
| Owner | `gt6-modern` | `gt6-modern` |
| Repository name | `texel` | `texel` |
| Workflow name | `release.yml` | `release.yml` |
| Environment name | `pypi-importer` | `pypi-compiler` |

Register both as **pending** publishers (the projects do not exist before the
first upload). `Workflow name` is the file name only, and the environment name must
be filled in.

### GitHub environments

`release` (GPG secrets), `pypi-importer`, `pypi-compiler`, `maven-central` (Portal
token). Only `release` requires one approval from either maintainer, with
`can_admins_bypass: false` and `prevent_self_review: false`. The triggerer may
approve their own deployment. The three registry environments have no second
approval. `release` admits branch `master` and tags `v*`; registry environments
admit only tags `v*`. Branch and tag policies have distinct API types.
See [GitHub settings](github-settings.md) for the complete configuration.

## 2. Bump the version

The same version appears in seven files. `python tools/check_release_metadata.py`
reads every one of them and fails if any disagrees with `VERSION`:

- `VERSION` (the source of truth)
- `pyproject.toml`
- `compiler/python/pyproject.toml`
- `compiler/python/texel_compiler/capabilities.py` (`COMPILER_VERSION`)
- `compiler/python/texel_compiler/__init__.py` (`__version__`)
- `compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java`
- `spec/conformance/capabilities/pxl-v1.json`

Plus the `CHANGELOG.md` section, which the gate also requires.

No test needs editing for a bump: `tests/test_capabilities.py` and
`tests/test_public_release.py` derive the expected version from `VERSION`, and
`tests/test_release_metadata.py` runs the gate itself — including a check that
this list still names every file the gate reads.

Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`; the GitHub Release body
is generated from exactly that section, so it must be self-contained and readable
on its own.

## 3. Pre-flight checks (local)

Run the full verification from `AGENTS.md`, on Linux and Windows, plus:

```console
python tools/release_notes.py                     # the body that will be published
python tools/release_probes.py pypi --project texel-importer --version "$(cat VERSION)"
python tools/check_release_assets.py dist/release --version "$(cat VERSION)"  # the fixed asset set
python -m build --outdir dist/importer .
python -m build --outdir dist/compiler compiler/python
```

Prove the signing key works before spending a CI round trip (this is the command
CI runs, and it fails in seconds with a bad key):

```console
set MAVEN_GPG_PRIVATE_KEY / MAVEN_GPG_PASSPHRASE   (or the shell equivalent)
cd compiler/java && ./gradlew --no-daemon :core:signMavenJavaPublication
```

Optional dry run: run the workflow manually with `dry_run: true`. It builds and
verifies everything — including the Central bundle, which is unsigned when no key
is configured — and publishes nothing. Run it from `master` and approve the
`release` environment. It does not exercise the GitHub Release state machine:
all publishing jobs require a tag. Use the isolated fork rehearsal described in
[GitHub settings](github-settings.md) before enabling immutable releases.

## 4. Publish

Create an annotated tag on the commit you tested and push it:

```console
git tag -a vX.Y.Z -m "Texel X.Y.Z"
git push origin vX.Y.Z
```

The tag name must be `v` + `VERSION` or the workflow stops at the validation step.
The build job re-runs every gate, signs the artifacts, and only then do the three
publishers run; the GitHub Release is created last, after all of them succeed.

## 5. Verify

```console
https://pypi.org/pypi/texel-importer/X.Y.Z/json
https://pypi.org/pypi/texel-compiler/X.Y.Z/json
https://repo1.maven.org/maven2/io/github/gt6-modern/texel-core/X.Y.Z/
https://repo1.maven.org/maven2/io/github/gt6-modern/texel-cli/X.Y.Z/
https://github.com/gt6-modern/texel/releases/tag/vX.Y.Z
```

Central takes a few minutes to serve a new release. Check that the jars carry
`.asc` signatures and that the digests on the GitHub Release match the ones the
registries recorded — they must be the same bytes.

## 6. Re-running a failed release

Re-run the failed jobs of the same run rather than moving the tag. All three uploads
are idempotent:

- Central: the job probes `repo1` for `texel-core-X.Y.Z.pom` and skips the upload
  when the version is already published.
- PyPI: each job probes `https://pypi.org/pypi/<project>/X.Y.Z/json` and skips the
  upload when the version is already there.
- GitHub Release: HTTP 404 plus an authenticated, fully paginated listing with no matching release creates a draft; an existing draft is deleted without
  deleting its tag, then rebuilt from this run's artifacts. All asset names,
  sizes, upload states and SHA-256 digests must match before publication.
  An already published release is a complete no-op, including title and body.

The state machine resolves the actual remote tag commit and requires it to match
the workflow commit. HTTP failures other than the release endpoint's 404 stop the
run. The tag endpoint returns published releases only; drafts are discovered through
the authenticated releases listing and checked again by release ID. Any listing
failure also stops publication. A newly created draft may briefly be absent from
the listing: up to five readbacks, two seconds apart, are allowed before stopping.
Creation/upload/publication commands are never automatically retried. Stderr may be empty or nonempty and is never used to infer a missing release.
The SBOM is generated before SHA256SUMS; provenance covers all final assets,
including SHA256SUMS. SHA256SUMS covers every payload including the SBOM.

That last rule exists because a published version's bytes are final. Rebuilding the
same commit produces the same jars (Gradle archives are pinned with
`preserveFileTimestamps = false` and `reproducibleFileOrder = true`) and the same
wheels (`SOURCE_DATE_EPOCH` is the commit time), but the sdist and the SPDX SBOM
still carry build-time data, so replacing assets on a re-run could leave the release
page describing files that no longer match what PyPI and Central accepted.

Never move or delete a `v*` tag: the tag ruleset has no bypass. If the tagged
commit is wrong, publish a new patch version. Immutable releases lock assets and
the associated tag; title and body remain editable, although this workflow does
not update them on reruns. Enabling repository immutability does not retroactively
freeze existing releases. `v0.1.0` is deliberately left unchanged: its tag is
protected by the ruleset but its release assets remain mutable.

If PyPI/Central are already published and GitHub is still a draft, rerun only the
failed jobs of the original run. The draft is deleted and rebuilt from that run's
artifacts. Do not rebuild time-dependent signed bundles/SBOMs and assume they match
registries. If the original artifacts expired, stop and reconcile with the exact
registry bytes before recovering; otherwise ship a new patch version.

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `./gradlew: Permission denied` (exit 126) | `gradlew` lost its executable bit in the tagged commit | `git update-index --chmod=+x compiler/java/gradlew` |
| `Could not read PGP secret key` | secret missing, or key and passphrase do not match | re-export, re-paste the whole armor |
| `Cannot invoke "PGPPrivateKey.getPublicKeyPacket()"` | secret is a `--export-secret-subkeys` stub, or the wrong key | export with `--export-secret-keys`, check for `gnu-dummy` |
| `MAVEN_GPG_PRIVATE_KEY 不是 ASCII-armored **私钥**` | the secret holds a public key | export the private key |
| `Feature not available for the ... organization` at attestation | private repository on a plan without attestations | the step is skipped for private repositories; make the repo public for provenance |
| `invalid-publisher` at PyPI | pending publisher does not match the run's claims | compare the four fields and the environment in the log |
| `A pending trusted publisher matching this configuration has already been registered` | two packages sharing one environment | give each package its own environment |
| Central rejects the upload as already existing | re-run of a published version | the probe step should have skipped it; check the version parsed from the bundle name |
| `File already exists` at PyPI | re-run of a published version | the probe step should have skipped it |

## 8. The GitHub Release body

`python tools/release_notes.py` prints the current CHANGELOG section. The workflow
fetches full history and tags, appends its own compare link when a previous tag is
available, and supplies `--notes-file` when creating the draft. It does not use
`generate_release_notes`, which previously replaced the curated body. Failure to
find an earlier tag is logged; the first release can legitimately omit the link.
