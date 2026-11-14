# Git Flow and Releases

This fork maintains source and GitHub Releases at `mr-pmillz/gato-x`, and images
at `ghcr.io/mr-pmillz/gato-x`. The Python distribution remains `gato-x` and its
import package remains `gatox`. We do not publish to upstream's PyPI project.

## Branch model

- Feature and fix PRs target `develop`.
- `release/vX.Y.Z` branches start from `develop` and target `main`.
- `hotfix/vX.Y.Z` branches start from `main` and target `main`.
- After each release or hotfix, open a `main` → `develop` sync PR.
- `develop` → `main` is allowed for integration synchronization, but does not
  create a release. A versioned release/hotfix branch is required for auto-tagging.

The Branch Policy check rejects other routes into `main`, foreign-repository
release branches, and release/package version mismatches. PR #1 from
`feat/github-app-auth` has a narrowly scoped bootstrap exception. Merge that PR
into `main` first to activate the manual and merged-PR workflows. If it is squash
merged, synchronize `main` back into `develop` before the first release.

Configure branch protection for `main` and `develop` with required reviews and
status checks after bootstrap. Workflows enforce checks; protection makes those
checks mandatory. No protection settings are changed by this setup.

## Prepare a release

Once the desired feature PRs are merged into `develop`:

```bash
gh workflow run bump_version.yaml --repo mr-pmillz/gato-x --ref main \
  -f kind=release -f new_version=1.5.0
```

**Prepare Release Branch** snapshots `develop`, updates `project.version`, and
prepends commit-linked notes to `CHANGELOG.md` on a new `release/v1.5.0` branch.
It refuses existing branches/tags and version downgrades. It explicitly starts
CI and prints a compare URL in the job summary. Open the PR yourself:

```bash
gh pr create --repo mr-pmillz/gato-x --base main --head release/v1.5.0 \
  --title 'Release v1.5.0' --body 'Release notes are in CHANGELOG.md.'
```

Review/edit the changelog on that branch before merging. The workflow does not
require an App secret, a PAT, or permission for Actions to create PRs. For a
hotfix, use `-f kind=hotfix -f new_version=1.4.1`; preparation starts from `main`.
A version must be stable `MAJOR.MINOR.PATCH`, without a `v` prefix in the input.

## Publish the reviewed merge

Merging a release/hotfix PR into `main` invokes **Tag Release**. It re-reads the
merged PR, validates the merged version/changelog and main ancestry, creates an
annotated `vX.Y.Z` tag at the exact merge SHA, and explicitly dispatches the
publisher on that tag. Advancing `main` does not change what gets released.

An explicit dispatch is necessary because tags created with `GITHUB_TOKEN` do
not trigger push workflows; `workflow_dispatch` is an exception to that token's
recursion protection. See [GitHub's token documentation](https://docs.github.com/en/actions/concepts/security/github_token).
Only the publishing/preparation jobs receive write scopes. PR builds use read
permissions; publishing does not restore caches populated by untrusted builds.

The publisher validates tag/package identity, builds and checks the wheel and
source archive, runs the test matrix and container smoke tests, publishes the
multi-platform image, signs distributions and `SHA256SUMS`, attests builds, and
creates a GitHub Release with the reviewed changelog. Verify downloaded files:

```bash
sha256sum --check SHA256SUMS
```

A maintainer-pushed matching tag can also trigger publication, provided its
commit includes the version's changelog. To retry a partial release, rerun the
failed run or dispatch the publisher at its original tag:

```bash
gh workflow run publish_release.yaml --repo mr-pmillz/gato-x --ref v1.5.0
```

Never move an existing tag to new code. Dispatching on `main` does not publish.
After a successful release, open a sync PR from `main` into `develop`.

## Container channels

Pushes to `main` and `develop` publish their respective branch tags and
`sha-<full-commit-sha>` after tests. Stable releases publish `latest`, `X.Y.Z`,
`vX.Y.Z`, and `X.Y`. PRs and release/hotfix branch pushes build/test only.
Linux amd64 and arm64 images include SBOM and provenance metadata.

```bash
gh workflow run docker_publish.yaml --repo mr-pmillz/gato-x --ref develop
```

Repository Actions settings must permit declared write scopes. If a GHCR package
already exists, grant this repository Actions access. Make the package public
in its package settings if anonymous pulls are desired.

## Plumber and validation evidence

The pinned Plumber action scans PRs and `develop` without score publication or
write permissions. Trusted `main` publishes the hosted score badge and SARIF.
JSON, SARIF, PBOM and CycloneDX artifacts are retained. Findings are advisory
(`soft-fail: true`) while branch protections are being configured; operational
errors still fail the job. The README score badge becomes available after the
first successful main-branch score publication.

CI retains JUnit and XML/HTML coverage artifacts for 14 days across Python
3.10–3.12 on Linux/macOS. Coverage must meet 70%. Local verification reports,
release simulations, and tool outputs belong in gitignored `.verification/`:

```bash
uv pip install -e '.[test,mcp,docs]'
uv run pytest --junitxml=.verification/junit.xml \
  --cov-report=html:.verification/coverage --cov-report=xml:.verification/coverage.xml
uv run black --check .
uv run ruff check .
uv run isort --check-only gatox/
uv run pyright
uv run mkdocs build --strict
docker build -t gato-x:local .
docker run --rm gato-x:local --help
```

The inherited live integration harness requires its own provisioned test
organization and credentials. It remains opt-in through
`ENABLE_LIVE_INTEGRATION=true` and runs only from this repository's main branch.
