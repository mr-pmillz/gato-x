# Upstream Comparison

Compared `/home/phil/repos/gato-x` at upstream commit
`6d5628c1971b592d790c5e0567bd632605248563` with this fork's PR #1 branch, starting
at `46d47319e071563386b0678a8c6d17b257fe203f`. The local upstream checkout matched
GitHub's `AdnaneKhan/gato-x` main branch during the comparison.

| Upstream change | Fork assessment |
| --- | --- |
| `d593466`, new GitHub Actions run-log parser | Already incorporated, including real log fixtures and parser regression tests. |
| `6d5628c`, deterministic pwn-request trigger exclusion | Equivalent implementation and regression tests already present in the fork. No additional port needed. |
| Workflow/dependency changes preceding those commits | Already in the fork's history. |

The upstream-only commit therefore adds no missing feature to port. Replacing
files wholesale would discard fork improvements: renewable App authentication,
configuration files, isolated YAML loading, expression fixes, API robustness,
local scanning, and fork-specific packaging. Preserve those changes when bringing
in future upstream commits.

The useful gaps identified were in the fork itself: reliable release/image
coordination, publication permissions, immutable release identity, documented
CLI exit statuses, bounded search retries, and reporting failed or incomplete
operations. These are addressed by the accompanying changes.

The review also fixed truncated App installation/repository pagination, empty
installation reporting, App client cleanup, workflow export path traversal, and
deploy-key file permissions and overwrite protection. Regression tests cover the
success and failure paths of these changes.

Keep upstream author/license attribution and links to external research. Install,
documentation, issue, release and image destinations use `mr-pmillz/gato-x`.
The Python module name stays `gatox` so existing imports remain compatible.
