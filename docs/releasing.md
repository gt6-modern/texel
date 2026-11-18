
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
token). Add required reviewers to `release` and `maven-central` if the build should
wait for a human.

## 2. Bump the version

The same version appears in six places, all checked by
`python tools/check_release_metadata.py`:

- `VERSION`
- `pyproject.toml`, `compiler/python/pyproject.toml`
- `compiler/python/texel_compiler/capabilities.py` (`COMPILER_VERSION`)
- `compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java`
- `spec/conformance/capabilities/pxl-v1.json`

Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`; the GitHub Release body
is generated from exactly that section, so it must be self-contained and readable
on its own.

## 3. Pre-flight checks (local)

Run the full verification from `AGENTS.md`, on Linux and Windows, plus:

```console
python tools/release_notes.py                     # the body that will be published
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
is configured — and publishes nothing.

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

Re-run the failed jobs of the same run rather than moving the tag. Both registry
uploads are idempotent:

- Central: the job probes `repo1` for `texel-core-X.Y.Z.pom` and skips the upload
  when the version is already published.
- PyPI: each job probes `https://pypi.org/pypi/<project>/X.Y.Z/json` and skips the
  upload when the version is already there.

If the tag itself must move (the tagged commit was wrong and nothing has been
published yet), delete and re-create the tag. Once a version is published, moving
its tag only creates a lie: publish a new patch version.

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

`python tools/release_notes.py` prints the `CHANGELOG.md` section for the current
version and the release job uses it as `body_path`; `generate_release_notes: true`
then appends the full changelog link. Without the curated section a first release
gets a body consisting of one line.
