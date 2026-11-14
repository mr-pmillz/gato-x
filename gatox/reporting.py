"""Offline, deterministic exports of native enumeration reports."""

import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import quote

PROJECT_URL = "https://github.com/mr-pmillz/gato-x"
MITIGATION = (
    "Treat pull request content as untrusted. Separate unprivileged builds from "
    "privileged jobs, minimize token permissions, pass expressions through quoted "
    "environment variables, and validate immutable commit and artifact identities."
)


def load_report(contents: str) -> dict | list:
    """Read a native JSON document or the repository JSONL emitted by this module."""
    if not contents.strip():
        return []
    try:
        value = json.loads(contents)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in contents.splitlines() if line.strip()]
    if not isinstance(value, (dict, list)):
        raise ValueError("Expected an enumeration object or list of repositories")
    return value


def repositories(data: dict | list, repository: str | None = None) -> list[dict]:
    """Flatten CLI/MCP results without silently accepting an error as an empty scan."""
    records: list[dict] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "error" in value:
                raise ValueError("Cannot export a failed enumeration")
            if "enumeration" in value:
                visit(value["enumeration"])
            elif "risks" in value:
                if not isinstance(value.get("name"), str) or not value["name"]:
                    raise ValueError("Repository name must be a nonempty string")
                risks = value["risks"]
                if not isinstance(risks, list) or any(
                    not isinstance(risk, dict)
                    or not isinstance(risk.get("issue_type"), str)
                    or not risk["issue_type"]
                    for risk in risks
                ):
                    raise ValueError("Repository risks must be a list of findings")
                if any(
                    r.get("repository_name", value["name"]) != value["name"]
                    for r in risks
                ):
                    raise ValueError("Finding repository does not match its report")
                records.append(value)
            else:
                keys = {
                    "organizations",
                    "repositories",
                    "public_repos",
                    "private_repos",
                } & value.keys()
                if not keys:
                    # Limited-access organization records contain only their name.
                    if set(value) == {"name"} and isinstance(value["name"], str):
                        return
                    raise ValueError("Unrecognized enumeration report")
                for key in sorted(keys):
                    if not isinstance(value[key], list):
                        raise ValueError(f"{key} must be a list")
                    visit(value[key])
        else:
            raise ValueError("Report entries must be objects")

    visit(data)
    selected = [r for r in records if repository is None or r["name"] == repository]
    # Preserve differing records of the same repository; collapse only exact duplicates.
    unique = {json.dumps(record, sort_keys=True): record for record in selected}
    return [unique[key] for key in sorted(unique)]


def workflow_uri(workflow: object) -> str | None:
    """Return a repository-relative workflow URI, refusing traversal and schemes."""
    if not isinstance(workflow, str) or not workflow:
        return None
    if any(ord(char) < 32 for char in workflow) or "\\" in workflow or ":" in workflow:
        return None
    path = PurePosixPath(workflow)
    if path.is_absolute() or ".." in path.parts:
        return None
    if len(path.parts) == 1:
        path = PurePosixPath(".github/workflows") / path
    if path.parts[:2] != (".github", "workflows") or path.suffix not in {
        ".yml",
        ".yaml",
    }:
        return None
    return quote(path.as_posix(), safe="/")


def sarif_report(records: list[dict]) -> dict:
    """Export workflow findings as SARIF 2.1.0, with separate runs per repository."""
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["name"], []).extend(record["risks"])
    runs = []
    for name, findings in sorted(groups.items()):
        rules = sorted({finding["issue_type"] for finding in findings})
        results = {}
        for finding in findings:
            identity = json.dumps(
                {
                    key: finding.get(key)
                    for key in (
                        "issue_type",
                        "initial_workflow",
                        "path",
                        "injectable_context",
                        "sink",
                    )
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(f"{name}\n{identity}".encode()).hexdigest()
            result = {
                "ruleId": finding["issue_type"],
                "ruleIndex": rules.index(finding["issue_type"]),
                "level": "warning",
                "message": {
                    "text": f"{finding['issue_type']} in {name}. "
                    + str(
                        finding.get("explanation")
                        or "Review the workflow trust boundary."
                    )
                },
                "partialFingerprints": {"gatoXFinding/v1": fingerprint},
                "properties": {
                    "repository": name,
                    "confidence": finding.get("confidence"),
                    "attackComplexity": finding.get("attack_complexity"),
                },
            }
            if uri := workflow_uri(finding.get("initial_workflow")):
                result["locations"] = [
                    {"physicalLocation": {"artifactLocation": {"uri": uri}}}
                ]
            results[fingerprint] = result
        runs.append(
            {
                "automationDetails": {"id": f"gato-x/{name}/"},
                "tool": {
                    "driver": {
                        "name": "Gato-X",
                        "informationUri": PROJECT_URL,
                        "rules": [
                            {
                                "id": rule,
                                "shortDescription": {"text": rule},
                                "helpUri": PROJECT_URL
                                + "/blob/main/docs/user-guide/advanced/vulnerabilities.md",
                                "help": {"text": MITIGATION},
                            }
                            for rule in rules
                        ],
                    }
                },
                "results": [results[key] for key in sorted(results)],
                "properties": {"repository": name},
            }
        )
    if not runs:
        runs = [
            {
                "tool": {"driver": {"name": "Gato-X", "informationUri": PROJECT_URL}},
                "results": [],
            }
        ]
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": runs,
    }


def export_report(data: dict | list, format: str, repository: str | None = None) -> str:
    """Convert native CLI/MCP data to SARIF or one repository JSON object per line."""
    records = repositories(data, repository)
    if format == "jsonl":
        return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    if format == "sarif":
        return json.dumps(sarif_report(records), indent=2) + "\n"
    raise ValueError("Report format must be sarif or jsonl")
