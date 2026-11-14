"""Validate release identity before building or publishing fork artifacts."""

import os
import re
import subprocess
from pathlib import Path


def release_metadata(
    version: str, ref: str, event: str, sha: str, existing_sha: str | None
) -> dict[str, str]:
    """Reject mismatched tags and prevent republishing a version from new code."""
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Release version must be stable MAJOR.MINOR.PATCH")
    if ref != "refs/heads/main" and ref != f"refs/tags/v{version}":
        raise ValueError("Release ref must be main or match the project version tag")
    if existing_sha and existing_sha != sha:
        if event == "push" and ref == "refs/heads/main":
            return {"version": version, "publish": "false"}
        raise ValueError("This version is already tagged at a different commit")
    return {"version": version, "publish": "true"}


def main() -> None:
    """Read metadata under Python 3.12 and emit safe GitHub Actions outputs."""
    import tomllib

    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    # Validate before embedding the version into a git ref argument.
    metadata = release_metadata(
        version,
        os.environ["GITHUB_REF"],
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_SHA"],
        None,
    )
    tag = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{version}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if tag.returncode not in (0, 1):
        raise RuntimeError("Unable to inspect existing release tag")
    metadata = release_metadata(
        version,
        os.environ["GITHUB_REF"],
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_SHA"],
        tag.stdout.strip() or None,
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in metadata.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
