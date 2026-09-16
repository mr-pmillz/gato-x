# syntax=docker/dockerfile:1

# Gato-X container image.
#
# Two stages: the builder resolves dependencies into a self-contained
# virtualenv with uv, and the runtime carries only that venv plus the
# interpreter. uv is never present in the final image.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.15

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/opt/venv

WORKDIR /src

# Resolve dependencies before copying the source so that editing Gato-X does
# not invalidate the dependency layer. Only the metadata is needed for this.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv "${VIRTUAL_ENV}" \
    && mkdir -p gatox \
    && touch gatox/__init__.py \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" ".[mcp]"

# Now install Gato-X itself on top of the cached dependency layer.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python "${VIRTUAL_ENV}/bin/python" --no-deps .

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# git is used by the local enumeration and attack paths; ca-certificates is
# required for TLS verification against github.com.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# Gato-X reaches out to the network and parses untrusted repository content,
# so it does not run as root.
RUN useradd --create-home --uid 1000 --shell /bin/bash gatox

COPY --from=builder --chown=gatox:gatox /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv

USER gatox
WORKDIR /home/gatox

# Mount a config here to avoid repeating flags:
#   docker run --rm -e GH_TOKEN \
#     -v "$HOME/.config/gato-x:/home/gatox/.config/gato-x:ro" \
#     ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
VOLUME ["/home/gatox/.config/gato-x"]

ENTRYPOINT ["gato-x"]
CMD ["--help"]
