# GitHub settings and rollout

This document defines the configuration and rollout procedure. Actual API readback
is recorded in `github-settings.json`; execution evidence and exceptions are in
`github-governance-implementation.md`. The snapshot tests check recorded settings,
not live drift.
Use `gh api` for settings, `gh pr` for pull requests and `gh workflow` / `gh run`
for workflows. Save complete pre-change JSON outside the checkout for rollback.

## Rollout gates

1. Enable secret scanning and push protection; paginate historical alerts. Stop
   deployment of this plan for any genuine credential that is not revoked/rotated.
   Do not print secret values or bypass push protection to commit them. An empty
   initial list is a point-in-time result; review again before the first PR push.
2. Enable Dependabot security updates and private vulnerability reporting. Enable
   CodeQL default setup for Actions and Python **before** opening PR 1. Wait for
   actual analyses; Java/Kotlin remains excluded to avoid Gradle autobuild surprises.
3. Merge PR 1 only after CI and CodeQL results pass. Configure repository merge
   options, environments and rulesets below. Keep the existing Copilot review rule.
4. Complete both fork release rehearsals below. Only then enable immutable releases
   on the main repository. Do not edit the existing `v0.1.0` release.
5. Exercise external-fork PR scanning before requiring it. If code scanning waits
   for missing results after enforcement, remove that rule immediately and leave
   CodeQL advisory. `alerts_threshold: errors` means error-severity alerts, not
   analysis failure or missing analysis results.
6. Exercise and restore break-glass, collect settings readback and evidence, and
   merge PR 2 through the actual one-CODEOWNER approval and strict CI gate.

The release parser classifies the `gh api --include` stdout status, headers and body.
The tag endpoint omits drafts: after its 404, exhaust the authenticated releases
listing and inspect any matching release by ID. Only confirmed absence permits
creation. After creation, retry only confirmed-missing readbacks up to five
times (two seconds between attempts); never retry a mutation automatically.
An invisible draft after that bound stops the run. Listing failures abort, and stderr never determines release state.

No formal version is published during this rollout. The first production release
still needs registry verification: the fork does not upload to PyPI or Central.
If registries have succeeded but GitHub remains draft, rerun failed jobs using the
original run's artifacts; the state machine deletes/rebuilds the draft. Expired
artifacts or rebuilt bytes require reconciliation, not automatic publication.

## Exact settings

Repository: Squash and Rebase enabled, merge commits disabled, auto-merge and update
branch enabled, delete branch on merge enabled. Actions default token stays read-only
and cannot approve PRs. Enable full-length SHA pinning after the pinned workflow PR
lands. Dependabot updates Actions monthly; Gradle updates may be absent because
production has no external dependencies (AGENTS.md contract 7).

Default branch ruleset: active, no bypass, `~DEFAULT_BRANCH` include; deletion,
non-fast-forward, required conversation resolution and PR rules. PR parameters:

```json
{
  "required_approving_review_count": 1,
  "require_code_owner_review": true,
  "dismiss_stale_reviews_on_push": true,
  "require_last_push_approval": true,
  "required_review_thread_resolution": true
}
```

Status check parameters:

```json
{
  "strict_required_status_checks_policy": true,
  "required_status_checks": [{"context": "CI required", "integration_id": 15368}]
}
```

A single CODEOWNER approval satisfies both approval conditions. Updating the branch
may invalidate that approval. No merge queue is introduced. CodeQL is conditional
on successful internal and external PR evidence, with `tool: CodeQL`,
`security_alerts_threshold: high_or_higher`, `alerts_threshold: errors`.
Copilot review is advisory; its extra-approval default for unattributed Copilot PRs
is retained. Human-created PRs do not acquire that special condition.

Tag ruleset: active, include `refs/tags/v*`, deletion and non-fast-forward only,
no bypass and no creation restriction. Do not use a temporary matching tag in the
main repository. Create and delete a nonmatching `probe-tag-delete-me` with
`git push`, then delete the local probe. Never create a Release for the probe.

### Environments

PUT the full intended environment body; never rely on omitted fields retaining
approval or ref restrictions. For `release`:

```json
{
  "wait_timer": 0,
  "reviewers": [
    {"type": "User", "id": 31504595},
    {"type": "User", "id": 308664957}
  ],
  "prevent_self_review": false,
  "can_admins_bypass": false,
  "deployment_branch_policy": {
    "protected_branches": false,
    "custom_branch_policies": true
  }
}
```

POST deployment-branch-policies `{ "name": "master", "type": "branch" }` and
`{ "name": "v*", "type": "tag" }`. For `pypi-importer`, `pypi-compiler` and
`maven-central`, PUT empty reviewers, wait timer zero, self-review false, admin
bypass true and the same custom policy flags; add **only** the `v*` tag policy.
No second approval is required. Remove unintended preexisting policies, paginate
readback and compare each type/name pair. On any 4xx stop and diagnose, never weaken
the target configuration as a silent fallback.

## Break-glass exercise

Save the complete default-branch ruleset. Temporarily remove only the required
status/code-scanning rule under test. Confirm PR/approval/conversation/deletion/
force-push protections remain; strict base freshness disappears with the status
rule. Restore the original parameters, including integration ID 15368 and
`strict_required_status_checks_policy: true`, and compare every field.
For release-environment recovery, a complete PUT may temporarily set
`can_admins_bypass: true`; preserve all other fields and policies, restore false,
and record the change. This is an emergency procedure, not a routine bypass.

## Fork rehearsal

Create the public personal fork and enable repository Actions and the Release
workflow explicitly. Before triggering it, create the four environments and their
ref policies; use the current maintainer as the fork release reviewer, allow
self-review and forbid admin bypass. Enable fork tag protection and immutability.
Do not copy production GPG keys, Central credentials or trusted-publisher bindings.

In a fork-only commit replace the three registry upload jobs with artifact download
and verification jobs, keeping their environment and dependency chain. No GPG key
is needed: Gradle can build an unsigned Central bundle for this rehearsal.

For each version change VERSION, both pyprojects, Python capability and __version__,
Java capability, capability JSON and CHANGELOG (eight files), plus the three pinned
version assertions in test_public_release.py and the compiler_version assertion
in test_capabilities.py. The latter was discovered by the first real fork run;
run the full test suite before creating a protected tag. Validate metadata/tests before tags.

- Original slot `0.999.5` (final successful slot recorded in implementation evidence): missing -> draft -> upload -> verify -> publish; rerun is a complete
  published no-op, including unchanged body, title and asset digests.
- Original slot `0.999.6` (final successful slot recorded in implementation evidence): disable the Release workflow while pushing the tag and preparing a
  partial old draft, then enable it and dispatch on that tag. Prove the old draft
  is deleted without deleting the tag, rebuilt and published only after validation.
- Attempt asset replacement and protected tag deletion in the fork; both must fail.
  Keep the fork and these releases. Register consumed versions in the implementation
  record; later rehearsals must use fresh, unoccupied version numbers.
- If fork attestations fail for platform permissions, retain evidence and disable
  that step only in the fork. This does not count as provenance validation: main
  repository dry-run provenance must pass independently.

The external CodeQL probe PR must use a separate clean upstream-based branch,
without the rehearsal changes. Close it and delete its ordinary branch afterward.

## Snapshot contract (PR 2)

Generate `docs/github-settings.json` from actual API readback, not intended values.
Whitelist repository merge/Actions/security settings; CodeQL state/languages;
immutable status; ruleset name/target/enforcement/conditions/bypass/rules with full
parameters; environment name/reviewer type and ID/wait timer/self-review/admin
bypass/custom policy; policy type/name. Sort objects and unordered arrays.
Omit resource IDs, node IDs, dates and URLs, **except reviewer IDs and integration
IDs**, which are security-critical configuration. Keep ruleset IDs in the prose
implementation record for recovery. The snapshot's tests are committed with it in
PR 2, never before. Tests validate the recorded configuration, not live drift.

Record the original worktree byte fingerprints, untracked-file content hashes,
PR and workflow evidence, secret-alert assessment, break-glass comparison, probe
cleanup and consumed fork versions outside private machine paths. For `v0.1.0`,
retain annotated tag object `6516a9e24d0687999a7565a8b53f7cee1affbdb2` and commit
`56b345db4078a28cbcbd4597c46d68c7d4eb3e0a`. Prefer restoring the original tag object
in a future authorized recovery; re-creating an annotated tag changes its object
SHA even if its target commit is identical.
