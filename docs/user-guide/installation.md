# Installation

Gato-X supports OS X and Linux with at least **Python 3.10**.

> **Note:** This is a fork of [AdnaneKhan/gato-x](https://github.com/AdnaneKhan/gato-x).
> It is **not** published to PyPI under the `gato-x` name -- `pip install gato-x`
> installs the upstream project, not this fork. Use the container image or
> install from source as below.

## Container Image

The quickest way to run this fork, with no Python setup at all:

```bash
docker run --rm -e GH_TOKEN ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

Multi-architecture images (`linux/amd64`, `linux/arm64`) are published to
`ghcr.io/mr-pmillz/gato-x`. See [Docker Usage](advanced/docker.md).

## Installation from Source

### Using uv (recommended)

[uv](https://github.com/astral-sh/uv) creates the virtual environment and
resolves dependencies considerably faster than pip, and it is what CI uses.

```bash
git clone https://github.com/mr-pmillz/gato-x
cd gato-x

uv venv                    # creates .venv/
uv pip install .           # or: uv pip install -e ".[test,mcp]" for development
```

Then either activate the environment or call the binary directly:

```bash
source .venv/bin/activate
gato-x --help

# ...or without activating:
uv run gato-x --help
```

To run the test suite and the lint gates the way CI does:

```bash
uv pip install -e ".[test,mcp]"
uv run pytest
uv run ruff check gatox/
uv run ruff format --check gatox/
```

If you do not have uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Using pip

```bash
git clone https://github.com/mr-pmillz/gato-x
cd gato-x
python3 -m venv venv
source venv/bin/activate
pip install .
```

If you need to make on-the-fly modifications, install it in editable mode:

```bash
pip install -e .
```

### Using pipx

Alternatively, you can use pipx:

```bash
git clone https://github.com/mr-pmillz/gato-x
cd gato-x
pipx install .
```

## GitHub Token Setup

The tool requires a GitHub classic Personal Access Token (PAT) to function. To create one:

1. Log in to GitHub
2. Go to [GitHub Developer Settings](https://github.com/settings/tokens)
3. Select `Generate New Token` and then `Generate new token (classic)`
4. Select the appropriate scopes based on your needs:
   - For basic scanning: `repo` scope
   - For attack features: `repo`, `workflow`, and `gist` scopes

After creating this token, set the `GH_TOKEN` environment variable within your shell:

```bash
export GH_TOKEN=<YOUR_CREATED_TOKEN>
```

Alternatively, you can enter it when the application prompts you.

## Verifying Installation

To verify that Gato-X is installed correctly, run:

```bash
gato-x --help
```

This should display the help menu with available commands and options.
