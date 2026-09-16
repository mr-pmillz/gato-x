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
| `latest` | The current `main` branch |
| `v1.4.0` | A specific release |
| `1.4` | The latest patch of that minor release |
| `sha-a1b2c3d` | One exact commit |

Pin to a release tag or a digest for anything reproducible.

## Running

```bash
docker run --rm -e GH_TOKEN ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

`-e GH_TOKEN` with no value passes the variable through from your shell, which
keeps the token out of your shell history and out of `docker inspect`.

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
