# Public repository governance implementation

Recorded on 2026-11-21 (Asia/Shanghai). This record distinguishes actual results
from remaining approval requirements; `github-settings.json` is normalized live API readback.

## Merged implementation and required checks

PR #6 merged as `66d1b47d4250ab4b86f4144ef77a9b78da713f11` after CI run
`44881668541` and CodeQL run `44881667356` passed. The real `CI required` check
summarized both Python versions, the JDK 21 build, four JVM runtime jobs and the
Windows gates. Local verification passed all required AGENTS.md commands:
427 Python tests (one Windows-inapplicable test skipped), 11,307 reference assertions,
907 compiler checks (50 importer-only cases skipped), and JDK 21 `clean check`.
Actionlint v1.7.12 passed with external ShellCheck/Pyflakes disabled.
Final verification for the second PR repeated all six Python gates and JDK 21 clean check:
435 unit tests passed (one platform skip), including six settings-snapshot tests
and two added visibility-delay tests; reference/compiler counts stayed unchanged.

Secret scanning historical alerts were paginated and reviewed again before the
PR push: no alerts were returned. No credential rotation was needed based on that
point-in-time result. The final paginated secret-alert read and open code-scanning
alert read were also empty. Push protection, Dependabot security updates and private
vulnerability reporting were enabled. CodeQL default setup for Python and Actions
was configured before PR #6; setup run `44805979057` and both actual analyses passed.

## Applied settings and recovery evidence

- Default branch ruleset `27047929`: no bypass; existing Copilot review preserved;
  exactly one approving CODEOWNER, stale-review dismissal, last-push approval and
  conversation resolution; `CI required` bound to Actions App `15368`, strict true.
- Tag ruleset `27786706`: `refs/tags/v*`, deletion and non-fast-forward protections,
  no bypass and no restriction on creating new tags.
- Squash/Rebase enabled, merge commits disabled; update-branch, auto-merge and
  delete-branch-on-merge enabled. Actions require full SHA pins; default token
  permissions remain read-only and Actions cannot approve PRs.
- All four used environments were configured with complete PUT bodies and verified
  through GET. `release` requires either User `31504595` or User `308664957`, permits
  deployment self-review and forbids administrator bypass. Its policies are branch
  `master` and tag `v*`. Registry environments have no additional approval and only
  tag `v*` policies. The pre-existing unused `pypi` environment was left unchanged;
  the workflow references the two separate package environments instead.
- Full pre-change settings were saved locally. Break-glass removed only the
  required-status rule; strict freshness disappeared with it. Readback confirmed
  the other rules stayed unchanged. Restoration compared every configurable field,
  including App ID, strict, approvals and Copilot, and matched the saved ruleset.
- `probe-tag-delete-me` was created and deleted with `git push`; the local tag was
  removed and remote absence verified. It never received a Release.

The REST schema omitted `can_admins_bypass`, but actual fork PUT/GET accepted and
persisted false. This was tested before applying it to the main repository.

## Main dry run and fork rehearsal

Main dry run `44882402544` succeeded through the normal release-environment approval.
It ran build and skipped all four tag-only publication jobs. Its eight assets were
downloaded: exact manifest coverage and local digests passed, including the SBOM.
`gh attestation verify SHA256SUMS --repo gt6-modern/texel --format json` succeeded,
proving the checksum file itself is covered by signed build provenance.

The public `huangsijun17/texel` fork was created, Actions explicitly enabled, and
all four environments created before runs. Three registry jobs were replaced with
artifact-byte comparisons; no PyPI/Central credentials or trusted publishers were
copied. No GPG key was needed. Fork immutability and tag protection were enabled
before publication. Provenance was retained; no attestation bypass was needed.

Consumed/protected rehearsal slots (never reuse them):

| Version | Run | Outcome |
| --- | --- | --- |
| 0.999.1 | 44882365889 | Failed before publishing: missed test_capabilities.py version assertion. |
| 0.999.2 | 44882581777 | Same assertion failure; deliberately incomplete draft remained unpublished. |
| 0.999.3 | 44883000570 | Draft creation succeeded, immediate listing omitted it; stopped with an empty unpublished draft. |
| 0.999.4 | 44883096011 | Old draft 453535257 rebuilt as 453537069; all eight assets verified, immutable publication succeeded. |
| 0.999.5 | 44883837125 | Final missing path passed; immutable release 453543909, eight assets verified. |
| 0.999.6 | 44884023763 | Final draft path passed; old draft 453540952 rebuilt as immutable release 453543707, eight assets verified. |

Both final runs were then rerun for the github-release job only (attempt 2):
published no-op passed, with identical release title/body and asset IDs, names,
sizes, digests, states and creation/update timestamps. Only after these results
was main-repository immutability enabled and verified by GET. A full comparison
confirmed production v0.1.0 and its assets remained unchanged and mutable.

The real failures improved the implementation and runbook. In addition to the eight
version surfaces and three public-release assertions, test_capabilities.py must be
updated for a rehearsal version. Newly created drafts can briefly be absent from
listing: the final executor permits five readbacks, two seconds apart, only for
confirmed missing state. Query errors or timeout still stop before upload/publish;
no mutation is automatically repeated. Two regression tests verify delay recovery
and timeout without unsafe writes, and a separate code review found no issues.

The v0.999.4 published assets were downloaded and matched against API names, sizes,
states and SHA-256 digests. Replacing SHA256SUMS with identical bytes was rejected
HTTP 422 (`Cannot delete asset from an immutable release`). Tag deletion was rejected
by ruleset GH013. Readback confirmed unchanged asset IDs/digests and the same tag
commit. No destructive probe was attempted on production v0.1.0.

## External contributor compatibility

External fork PR #7 was based on the clean upstream commit with only a Python
comment change. CI run checks all passed, including `CI required`, but no CodeQL
workflow or analysis appeared for the fork PR. The analyses endpoint returned an
empty list. Therefore **CodeQL remains advisory**: no code_scanning rule was added.
The probe PR was closed without merging and its remote branch deleted. This proves
the fork CI path; it does not claim that fork CodeQL works. Requiring code scanning
remains conditional on a later successful external-fork analysis.

## Existing release and implementation limits

Production v0.1.0 was not edited, deleted or moved. Its annotated tag object is
`6516a9e24d0687999a7565a8b53f7cee1affbdb2`, peeled commit
`56b345db4078a28cbcbd4597c46d68c7d4eb3e0a`. New tag rules protect it; enabling
immutability does not retroactively freeze its existing assets. Prefer restoring
the original tag object in any separately authorized recovery; re-creating an
annotation would produce a different object SHA even at the same commit.

Fork rehearsal verifies GitHub publication and registry artifact identity, not real
PyPI/Central uploads. If registries succeed and GitHub remains draft, rerun failed
jobs from the original run so its artifacts are reused. Never rebuild
non-deterministic SBOM/sdist/signature data and assume registry byte equality.
Expired original artifacts require reconciliation or a new patch version.

Gradle Dependabot may produce no dependency PRs: production has no external runtime
dependencies (contract 7). The workflow and wrapper pins still need routine upkeep.
Every production merge needs the other CODEOWNER; base updates can dismiss approval.
There is no bypass to replace that human review.

## Original workspace fingerprints

The implementation used a separate worktree. No original-worktree files were
stashed, restored, cleaned or committed by this task; staging was path-specific.
Initial byte SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| git diff --binary | a91238662132cda4d518719b7c4627d1944ef64762c0fca4b78cd71dcdfbc596 |
| git diff --cached --binary | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| git status --porcelain=v1 --untracked-files=all | e2ee2f8253503b5fbd966c9be2b5bc51fafc4cc1cffebf7fde89f66bb7ad1ba2 |
| git ls-files -o --exclude-standard | 4a3f115e35b7ef0a96ea66ce842de13a18ad3ac876806514ed10b69b02fae719 |
| tests/test_cli_identity.py content | 9f68324312ad0397d8620d1cb67ae45728a9f8fc79d2bb365449097870438e3e |

Concurrent CLI work and temporary files appeared in the original workspace during
implementation, so exact opening/closing fingerprint equality cannot be claimed.
The first follow-up comparison showed an unchanged empty index and unchanged
untracked test content, but larger CLI diffs and additional temporary files.
These changes were preserved. Final read-only hashes follow below.

## Delivery gate

PR #8 contains actual settings evidence, snapshot assertions, updated
version-bump instructions, and the bounded visibility-read fix validated in the
fork. It must pass CI and receive the other CODEOWNER approval before merging.
Until that merge, master retains the fail-closed immediate readback behavior from
PR #6; a transient invisible draft can require rerunning the original failed job.
No production version was released as part of this rollout.

Final read-only comparison on 2026-11-21:

| Input | SHA-256 | Bytes |
| --- | --- | --- |
| git diff --binary | 1165a956c9092bdfcb81320646f0e1dfde4d82a19cd70594d0dd9f0403510129 | 148360 |
| git diff --cached --binary | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 |
| git status --porcelain=v1 --untracked-files=all | e2ee2f8253503b5fbd966c9be2b5bc51fafc4cc1cffebf7fde89f66bb7ad1ba2 | 411 |
| git ls-files -o --exclude-standard | 4a3f115e35b7ef0a96ea66ce842de13a18ad3ac876806514ed10b69b02fae719 | 27 |
| tests/test_cli_identity.py content | 9f68324312ad0397d8620d1cb67ae45728a9f8fc79d2bb365449097870438e3e | 12549 |

The closing status and untracked-list hashes returned to their opening values;
the index and untracked test content also match. Only the tracked diff hash differs,
reflecting the concurrent CLI work; no equality claim is made for that diff.
