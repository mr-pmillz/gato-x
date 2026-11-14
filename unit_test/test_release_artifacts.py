"""Verify downloadable checksums and reviewed release notes."""

import hashlib
from pathlib import Path

import pytest

from scripts.release_artifacts import checksums, main


def test_artifact_checksums_and_notes(tmp_path, monkeypatch):
    """Every distribution is hashed and only this version's changelog is published."""
    monkeypatch.chdir(tmp_path)
    Path("dist").mkdir()
    for name in ("gato_x-1.5.0.whl", "gato_x-1.5.0.tar.gz"):
        Path("dist", name).write_bytes(name.encode())
    Path("pyproject.toml").write_text('[project]\nversion = "1.5.0"\n')
    Path("CHANGELOG.md").write_text(
        "# Changelog\n\n## v1.5.0 — today\n\n- Current\n\n## v1.4.0 — yesterday\n\n- Old"
    )
    main()
    for line in Path("dist/SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert digest == hashlib.sha256(Path("dist", name).read_bytes()).hexdigest()
    notes = Path("dist/RELEASE_NOTES.md").read_text()
    assert "Current" in notes and "Old" not in notes
    assert "ghcr.io/mr-pmillz/gato-x:1.5.0" in notes
    Path("CHANGELOG.md").write_text("## v1.4.0 ")
    with pytest.raises(ValueError, match="version"):
        main()


def test_missing_artifacts_fail(tmp_path):
    """An incomplete build cannot become a release with misleading checksums."""
    with pytest.raises(ValueError):
        checksums(tmp_path)
    (tmp_path / "only.whl").write_bytes(b"wheel")
    with pytest.raises(ValueError, match="source archive"):
        checksums(tmp_path)
