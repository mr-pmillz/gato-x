"""Release identity checks prevent version injection and mismatched artifacts."""

import pytest

from scripts.release_metadata import release_metadata


@pytest.mark.parametrize("ref", ["refs/tags/v1.4.0"])
def test_release_version(ref):
    """Matching tags produce the package version."""
    assert release_metadata("1.4.0", ref, "push", "abc", None) == {
        "version": "1.4.0",
        "publish": "true",
    }


@pytest.mark.parametrize(
    "version", ["1.2.3\npublish=true", "1.2", "01.2.3", "1.2.3-rc1", "$(id)"]
)
def test_invalid_release_version(version):
    """Only stable version strings can reach workflow output and release tags."""
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        release_metadata(version, "refs/heads/main", "push", "abc", None)


@pytest.mark.parametrize(
    "ref", ["refs/heads/main", "refs/heads/feature", "refs/tags/v1.5.0"]
)
def test_mismatched_ref(ref):
    """A tag cannot publish a different version than the package metadata."""
    with pytest.raises(ValueError, match="Release ref"):
        release_metadata("1.4.0", ref, "push", "abc", None)


def test_manual_republish_from_different_commit_fails():
    """Manual runs cannot replace existing release artifacts with newer code."""
    with pytest.raises(ValueError, match="different commit"):
        release_metadata("1.4.0", "refs/tags/v1.4.0", "workflow_dispatch", "new", "old")


def test_retry_same_commit():
    """A failed release can be resumed from its original immutable commit."""
    assert (
        release_metadata(
            "1.4.0", "refs/tags/v1.4.0", "workflow_dispatch", "abc", "abc"
        )["publish"]
        == "true"
    )
