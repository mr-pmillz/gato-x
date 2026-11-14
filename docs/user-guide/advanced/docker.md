# Docker Usage

Gato-X is published as a container image on the GitHub Container Registry:

```bash
docker pull ghcr.io/mr-pmillz/gato-x:latest
```

Images are built for `linux/amd64` and `linux/arm64`, so they run natively on
Apple Silicon and on ARM servers.

## Tags

| Tag | Points at |
|---|---|
| `latest` | The most recently published stable release |
| `main` | The current production branch |
| `develop` | The integration branch |
| `1.4.0`, `v1.4.0` | A specific stable release |
| `1.4` | The latest patch of that minor release |
| `sha-<full-commit-sha>` | One exact commit |

Pin to a release tag or a digest for anything reproducible.

## Running

```bash
docker run --rm -e GH_TOKEN ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

`-e GH_TOKEN` with no value passes the variable through from your shell, which
keeps the token value out of your shell history. Container environment values
are still visible to anyone with access to the Docker daemon or `docker inspect`.

Writing results back to the host needs the output directory mounted, and the
container runs as UID 1000 so it must be able to write there:

```bash
docker run --rm -e GH_TOKEN \
  -v "$PWD/out:/home/gatox/out" \
  ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp --output-json out/findings.json
```

## Configuration file

Mount your config directory read-only at the path Gato-X reads inside the
container:

```bash
docker run --rm -e GH_TOKEN \
  -v "$HOME/.config/gato-x:/home/gatox/.config/gato-x:ro" \
  ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

See [Configuration File](configuration-file.md).

## GitHub App authentication

The App's private key has to be readable inside the container:

```bash
docker run --rm \
  -e GH_APP_ID=12345 \
  -e GH_APP_KEY=/home/gatox/.config/gato-x/app.pem \
  -v "$HOME/.config/gato-x:/home/gatox/.config/gato-x:ro" \
  ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

See [GitHub App Authentication](github-app-auth.md).

## A shell alias

```bash
gato-x() {
  docker run --rm -i \
    -e GH_TOKEN -e GH_APP_ID -e GH_APP_KEY \
    -v "$HOME/.config/gato-x:/home/gatox/.config/gato-x:ro" \
    -v "$PWD:/home/gatox/work" -w /home/gatox/work \
    ghcr.io/mr-pmillz/gato-x "$@"
}
```

## Verifying the image

Images carry build provenance attestation. Verify one before running it in a
sensitive environment:

```bash
gh attestation verify \
  oci://ghcr.io/mr-pmillz/gato-x:latest \
  --repo mr-pmillz/gato-x
```

## Building locally

```bash
docker build -t gato-x:local .
docker run --rm gato-x:local --help
```

The build is two stages: a builder that resolves dependencies into a
virtualenv at `/opt/venv` with [uv](https://github.com/astral-sh/uv), and a
runtime carrying only that virtualenv. `uv` is not in the final image, and the
container runs as the unprivileged `gatox` user.

For a multi-architecture build matching CI:

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t gato-x:local .
```

## Notes

- The image includes `git`, which the local enumeration and attack paths use.
- Gato-X parses untrusted repository content, so the container runs as a
  non-root user. Do not add `--privileged` or run it as root.
- The MCP server is included; run it with
  `--entrypoint gato-x-mcp`.

## Publishing

Pushes to `main` and `develop` publish their branch tag and a full-SHA tag after
tests and a container smoke test pass. Stable releases publish version tags and
`latest` through the same workflow. PRs only build and test; they have no registry publishing permissions.
See [Releases](../../contribution-guide/releases.md) for release and manual-build steps.

Docker preserves Gato-X
[exit statuses](../command-reference/exit-statuses.md), including `3` for an empty
completed search or enumeration.
