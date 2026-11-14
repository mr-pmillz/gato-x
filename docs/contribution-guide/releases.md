# Maintaining and Releasing this Fork

Source and releases belong to `github.com/mr-pmillz/gato-x`; container images
belong to `ghcr.io/mr-pmillz/gato-x`. This fork does not publish to the upstream
`gato-x` PyPI project. Wheels retain the distribution name `gato-x` and import
package `gatox` for compatibility.

## Container builds

PRs targeting `main` build the image and smoke-test its CLI, MCP imports, package
data, and empty-result status using a read-only GitHub token. Pushes to `main`
run the unit suite and build verification before publishing `latest`, `main`,
and `sha-<full-commit-sha>` for Linux amd64 and arm64. Published images carry
SBOM and provenance metadata.

To publish a fresh main-branch image manually:

```bash
gh workflow run docker_publish.yaml --repo mr-pmillz/gato-x --ref main
```

## GitHub releases

1. Update `project.version` in `pyproject.toml` to a stable `MAJOR.MINOR.PATCH`
   version, or use the **Bump Gato-X Version** workflow to prepare a PR.
2. Review and merge the version change into `main`.
3. The release workflow validates the version/tag identity, builds and checks the
   wheel and source distribution, runs the test matrix, publishes the matching
   container image, signs/attests the distributions, and creates the GitHub Release.

The release tag points to the exact workflow commit, even if `main` advances
while the build runs. The release's container gets `1.4.0`, `v1.4.0` and `1.4`
style tags. It also gets `latest` when built from `main`; a tag-triggered release
does not replace the main-branch `latest` image.

A pushed version tag can also trigger a release; its version must match
`pyproject.toml`. For an initial release without a version change, run:

```bash
gh workflow run publish_release.yaml --repo mr-pmillz/gato-x --ref main
```

The image workflow is called directly by the release workflow, because tags
created with `GITHUB_TOKEN` do not cause another push-triggered workflow run.
Both builds use the caller's immutable commit. Only publication jobs receive
package-write, release-write, or signing permissions.

A version already tagged at a different commit cannot be manually republished.
Ordinary main-branch changes without a version bump skip that existing release.
To retry a partially failed release, dispatch the workflow on its original tag
or rerun the original workflow run. Do not move the tag to new code.

The repository's Actions settings must permit the workflow's declared write
permissions. For a pre-existing GHCR package, grant this repository Actions
access to that package. A first GHCR publication may require making the package
public in its package settings for anonymous pulls.

## Validation and live integration

```bash
uv pip install -e '.[test,mcp,docs]'
uv run pytest
uv run black --check .
uv run ruff check .
uv run isort --check-only gatox/
uv run pyright
uv run mkdocs build --strict
docker build -t gato-x:local .
docker run --rm gato-x:local --help
```

Coverage is enforced at 70% locally and in CI. The inherited live integration
harness uses its own provisioned test organization and credentials; it is opt-in
through `ENABLE_LIVE_INTEGRATION=true`, and only runs from this repository's main
branch. Configure that dedicated environment before enabling it. The manual
fork-testing workflow runs unit tests without integration credentials or a
self-hosted runner. Never pass live test credentials into arbitrary fork code.
