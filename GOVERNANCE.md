# Governance

Texel is maintained by the GT6 Modern organization. Decisions are made in public
issues and pull requests whenever confidentiality does not require otherwise.
Language changes require normative specification changes, conformance cases, and
agreement between both independent compiler implementations. The project is
pre-1.0 and follows semantic versioning.

Default-branch changes require a pull request, all required checks, resolved review
conversations and **one approval, which must be from a CODEOWNER**. Both maintainers
are owners of the whole repository. The author's own approval cannot count, so
every merge requires the other maintainer's participation. Required status checks
must run against an up-to-date base; updating a branch dismisses stale reviews and
may require another approval. Last-push approval is also required. Squash and Rebase
are supported; merge commits are disabled. No routine ruleset bypass is configured.

A release requires a single `release` environment approval from either maintainer.
Self-review is allowed for deployment approval; administrator bypass is disabled.
There is no second approval on the registry environments. See
[GitHub settings](docs/github-settings.md) for ref restrictions and exact API fields.

## Recovering a blocked required check

If workflow syntax, an Action pinning policy, or missing CodeQL results prevents a
required check from appearing, an administrator must repair the ruleset. Save its
complete current JSON first, record the incident, and temporarily remove only the
blocking `required_status_checks` or `code_scanning` rule. Keep PR, approval,
conversation, deletion and force-push protection active. Removing
`required_status_checks` also removes its strict/up-to-date-base requirement.

Merge the repair through a reviewed PR, confirm real checks, restore the saved rule
including integration ID and strict policy, and compare the complete rule fields.
A fork PR stuck waiting for CodeQL requires removing `code_scanning` immediately;
keep scanning advisory until a successful fork validation justifies restoring it.

For an environment emergency, PUT a full environment configuration with only
`can_admins_bypass` temporarily changed to `true`; preserve reviewers, self-review
policy and deployment restrictions, then restore `false`. Record every relaxation
and restoration. Ruleset and deployment recovery are separate controls.
