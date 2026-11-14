"""Prepare reproducible checksums and reviewed notes for release artifacts."""

import hashlib
from pathlib import Path


def checksums(directory: Path) -> str:
    """Hash every wheel and source archive without including unrelated files."""
    artifacts = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
    if not artifacts or not any(p.suffix == ".whl" for p in artifacts):
        raise ValueError("Release artifacts must include a wheel and source archive")
    if not any(p.name.endswith(".tar.gz") for p in artifacts):
        raise ValueError("Missing source archive")
    return "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in artifacts
    )


def main() -> None:
    """Write SHA256SUMS and release notes from the checked-out version tag."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    contents = Path("CHANGELOG.md").read_text()
    section = f"## v{version} "
    if section not in contents:
        raise ValueError("Release changelog does not contain the package version")
    notes = section + contents.split(section, 1)[1].split("\n## ", 1)[0]
    notes += f"\nContainer: `ghcr.io/mr-pmillz/gato-x:{version}`\n"
    notes += "\nVerify downloads with `sha256sum --check SHA256SUMS`.\n"
    Path("dist/SHA256SUMS").write_text(checksums(Path("dist")))
    Path("dist/RELEASE_NOTES.md").write_text(notes)


if __name__ == "__main__":
    main()
