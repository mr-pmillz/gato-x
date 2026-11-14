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

## Features adopted from gogatoz

Compared `/home/phil/projects/gogatoz` at
`36df433351c73b37b0be94f81848228309a310be` for portable maintenance and reporting
features. The implementations here use Gato-X's existing Python models.

| gogatoz feature | Adoption in this fork |
| --- | --- |
| Git Flow and branch-policy checks | Feature PRs to develop, release branches from develop, hotfixes from main; merged versioned PRs auto-tag and publish. |
| Automated changelog preparation | Commit-linked changelog and package-version changes are reviewed on the release branch. |
| Plumber configuration and hosted score badge | Pinned current action, advisory findings, retained JSON/SARIF/PBOM/CycloneDX artifacts; main publishes the badge. |
| SARIF and JSONL reporting | Offline CLI conversion and an MCP export tool, with repository filtering and stable finding fingerprints. |
| Release checksums and retained CI evidence | SHA256SUMS alongside signed distributions; JUnit and XML/HTML coverage artifacts from the test matrix. |
| No untrusted build-cache restore during publishing | Publishing jobs build independently of the PR build cache. |
| Go-specific release tooling, GitLab scanning, attack modules, databases and notifications | Not transplanted: they require different runtimes/providers or substantial new product scope. |

Two release details improve on the reference: the tag uses the exact PR merge
SHA instead of the latest main tip, and an explicit workflow dispatch handles
GITHUB_TOKEN event suppression without requiring an additional App secret.
Regression tests exercise divergent develop/main histories, preparation,
version rejection, immutable tags, retries, report conversion and MCP parity.
The review also repaired MCP proxy validation for Pydantic's current validator
API, blocked report output from overwriting a hard-linked input file, and
prevented local workflow/action symlinks from reading outside their repository.
The Plumber policy was migrated to the current cache-control schema, and PR
jobs no longer write dependency/build caches.
