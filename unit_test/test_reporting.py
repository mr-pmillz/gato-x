"""Report exports preserve findings and cannot misattribute untrusted paths."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from gatox.cli.cli import cli
from gatox.reporting import export_report, load_report, repositories, workflow_uri


@pytest.fixture
def report_data():
    """Return a representative native enumeration containing a workflow finding."""
    return {
        "name": "acme/example",
        "risks": [
            {
                "repository_name": "acme/example",
                "issue_type": "InjectionResult",
                "initial_workflow": "build.yml",
                "confidence": "HIGH",
                "explanation": "Untrusted expression reaches a privileged step.",
                "path": [{"node": "StepNode('test')", "line_number": 18}],
                "injectable_context": ["github.event.issue.title"],
            }
        ],
        "repo_secrets": [{"name": "TOKEN", "value": None}],
    }


def test_jsonl_roundtrip_and_nested_enumeration(report_data):
    """CLI, organization and MCP records export consistently without losing metadata."""
    data = {
        "enumeration": {
            "repositories": [report_data],
            "organizations": [
                {"name": "acme", "public_repos": [report_data], "private_repos": []},
                {"name": "restricted"},
            ],
        }
    }
    result = export_report(data, "jsonl")
    assert result.count("\n") == 1
    assert json.loads(result) == report_data
    assert repositories(load_report(result)) == [report_data]
    other = {"name": "acme/other", "risks": []}
    assert len(repositories(load_report(result + json.dumps(other)))) == 2


def test_sarif_deduplication_and_repository_filter(report_data):
    """Stable fingerprints deduplicate repeated paths while isolating repositories."""
    other = deepcopy(report_data)
    other["name"] = other["risks"][0]["repository_name"] = "acme/other"
    data = [report_data, other, report_data]
    sarif = json.loads(export_report(data, "sarif"))
    assert sarif["version"] == "2.1.0" and len(sarif["runs"]) == 2
    result = sarif["runs"][0]["results"][0]
    assert (
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        == ".github/workflows/build.yml"
    )
    assert len(sarif["runs"][0]["results"]) == 1
    report_data["enum_time"] = "tomorrow"
    filtered = json.loads(export_report(data, "sarif", "acme/example"))
    assert (
        filtered["runs"][0]["results"][0]["partialFingerprints"]
        == result["partialFingerprints"]
    )
    assert len(filtered["runs"]) == 1
    assert "TOKEN" not in json.dumps(sarif)
    assert (
        "Treat pull request content"
        in sarif["runs"][0]["tool"]["driver"]["rules"][0]["help"]["text"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "../secret.yml",
        "/etc/a.yml",
        "https://evil/a.yml",
        "..\\a.yml",
        ".github/workflows/../../../x.yml",
        "bad\nfile.yml",
        "other/file.yml",
        "",
        None,
    ],
)
def test_unsafe_locations_are_not_emitted(path, report_data):
    """Untrusted workflow paths cannot become absolute or out-of-repository URIs."""
    report_data["risks"][0]["initial_workflow"] = path
    result = json.loads(export_report(report_data, "sarif"))["runs"][0]["results"][0]
    assert "locations" not in result


def test_uri_encoding():
    """Workflow names containing URI metacharacters remain paths, not URI fragments."""
    assert workflow_uri("odd #%.yml") == ".github/workflows/odd%20%23%25.yml"


@pytest.mark.parametrize(
    "data",
    [
        {"error": "denied"},
        {},
        {"repositories": None},
        {"name": "repo", "risks": {}},
        {"name": "repo", "risks": [{}]},
        [None],
        {"name": 12, "risks": []},
    ],
)
def test_malformed_data_fails(data):
    """Failed or unknown report shapes cannot masquerade as a clean scan."""
    with pytest.raises(ValueError):
        export_report(data, "sarif")


def test_repository_mismatch_fails(report_data):
    """A finding from one repository cannot be uploaded as another's finding."""
    report_data["risks"][0]["repository_name"] = "attacker/other"
    with pytest.raises(ValueError, match="does not match"):
        export_report(report_data, "sarif")


def test_empty_and_invalid_formats():
    """Empty scans remain valid reports, but malformed JSON/formats fail explicitly."""
    assert export_report([], "jsonl") == ""
    assert json.loads(export_report([], "sarif"))["runs"][0]["results"] == []
    assert load_report("") == []
    with pytest.raises(ValueError):
        load_report("null")
    with pytest.raises(ValueError):
        load_report("{bad}")
    with pytest.raises(ValueError, match="format"):
        export_report([], "xml")


async def test_cli_offline_conversion_and_statuses(tmp_path, report_data, monkeypatch):
    """Conversions require no token, preserve input, and distinguish errors/empty data."""
    source, output = tmp_path / "in.json", tmp_path / "out.sarif"
    source.write_text(json.dumps(report_data))
    monkeypatch.setenv("GH_APP_ID", "incomplete-credentials-must-not-matter")
    args = [
        "--no-config",
        "report",
        "--input",
        str(source),
        "--output",
        str(output),
        "--format",
        "sarif",
    ]
    with patch("builtins.input", side_effect=AssertionError("must not prompt")):
        assert await cli(args) == 0
        assert json.loads(output.read_text())["runs"][0]["results"]
        assert await cli(args + ["--repository", "missing/repo"]) == 3
        source.write_text('{"error":"denied"}')
        assert await cli(args) == 1
        source.write_text("[]")
        assert await cli(args) == 3
        args[args.index("--output") + 1] = str(source)
        assert await cli(args) == 1
        assert source.read_text() == "[]"


async def test_cli_rejects_hardlinked_output(tmp_path, report_data):
    """A hard link must not bypass the protection against overwriting the input."""
    source, destination = tmp_path / "source.json", tmp_path / "destination.jsonl"
    source.write_text(json.dumps(report_data))
    destination.hardlink_to(source)
    assert (
        await cli(
            [
                "--no-config",
                "report",
                "--input",
                str(source),
                "--output",
                str(destination),
                "--format",
                "jsonl",
            ]
        )
        == 1
    )
    assert json.loads(source.read_text()) == report_data


async def test_mcp_report_tool_parity(report_data):
    """MCP clients get the same offline representation as CLI clients."""
    Client = pytest.importorskip("fastmcp").Client

    from gatox.mcp.mcp_server import app

    async with Client(app) as client:
        tools = await client.list_tools()
        assert "export_enumeration_report" in {tool.name for tool in tools}
        result = await client.call_tool(
            "export_enumeration_report", {"data": report_data, "format": "jsonl"}
        )
    assert json.loads(result.content[0].text) == report_data
