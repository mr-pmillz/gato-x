"""Unit tests for the run log parser (_process_run_log)."""

import asyncio
import os

import pytest

from gatox.github.api import Api

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "runlogs")


def _read_fixture(name: str) -> bytes:
    """Read a fixture zip file as bytes."""
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def api():
    """Create an API instance with an invalid PAT for testing."""
    return Api("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "2022-11-28")


def test_parse_github_hosted_returns_none(api):
    """GitHub-hosted build zip should return None (not self-hosted)."""
    log_content = _read_fixture("github-hosted-build.zip")
    run_info = {"id": 12345, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is None, f"GitHub-hosted build should return None, got: {result}"


def test_parse_self_hosted_extracts_runner(api):
    """Self-hosted autobuild zip should extract correct runner metadata."""
    log_content = _read_fixture("self-hosted-autobuild.zip")
    run_info = {"id": 67890, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is not None, "Self-hosted run should return metadata"
    assert result["runner_name"] == "production-isolate-rabby-build-linux-intel-1"
    assert result["machine_name"] == "production-rabby-builder"
    assert result["runner_group"] == "default"
    assert result["run_id"] == 67890
    assert result["run_attempt"] == 1
    assert "token_permissions" in result
    assert result["non_ephemeral"] is True  # Self-hosted runner uses git clean


def test_parse_github_hosted_prscan_returns_none(api):
    """GitHub-hosted PR scan zip should return None."""
    log_content = _read_fixture("github-hosted-prscan.zip")
    run_info = {"id": 11111, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is None, f"GitHub-hosted PR scan should return None, got: {result}"


def test_handles_utf8_bom(api):
    """Verify BOM is stripped properly from run log files.

    The fixture files contain a UTF-8 BOM (U+FEFF) at the start which
    should be stripped by the utf-8-sig decoder without corrupting values.
    """
    log_content = _read_fixture("self-hosted-autobuild.zip")
    run_info = {"id": 123, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is not None
    # The runner name should NOT start with a BOM character
    assert not result["runner_name"].startswith("\ufeff")
    # The first character should be a normal ASCII letter
    assert result["runner_name"][0] == "p"


def test_no_trailing_whitespace_in_values(api):
    """Extracted values should have no trailing \\r or spaces."""
    log_content = _read_fixture("self-hosted-autobuild.zip")
    run_info = {"id": 123, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is not None
    assert result["runner_name"] == result["runner_name"].strip()
    assert result["machine_name"] == result["machine_name"].strip()
    assert result["runner_group"] == result["runner_group"].strip()
    assert "\r" not in result["runner_name"]
    assert "\r" not in result["machine_name"]
    assert "\r" not in result["runner_group"]


def test_handles_directory_format(api):
    """Verify directory-based zip files (jobname/1_Set up job.txt) are parsed.

    The self-hosted-autobuild fixture contains both flat (0_*.txt) and
    directory (jobname/1_Set up job.txt) formats. The parser should handle
    both and return the same result regardless of which format is found
    first in the zip iteration.
    """
    log_content = _read_fixture("self-hosted-autobuild.zip")
    run_info = {"id": 456, "run_attempt": 1}

    result = asyncio.run(api._process_run_log(log_content, run_info))

    assert result is not None
    assert result["runner_name"] == "production-isolate-rabby-build-linux-intel-1"
    assert result["machine_name"] == "production-rabby-builder"
    assert result["runner_group"] == "default"


def test_get_full_runlog_directory_format(api):
    """_get_full_runlog should find logs in directory-format zips."""
    log_content = _read_fixture("self-hosted-autobuild.zip")

    # The flat format file (0_build debug.txt) should be found
    result = asyncio.run(api._get_full_runlog(log_content, "build debug"))
    assert result is not None
    assert "production-isolate-rabby-build-linux-intel-1" in result

    # Directory format fallback: job with no flat file in the zip
    result2 = asyncio.run(api._get_full_runlog(log_content, "prepare build"))
    assert result2 is not None
    assert "prepare build" in result2.lower() or "production-isolate" in result2
