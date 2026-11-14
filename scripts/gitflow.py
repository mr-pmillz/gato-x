"""Prepare release branches and tag reviewed merge commits without App secrets."""

import argparse
import base64
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "mr-pmillz/gato-x"
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def validate_version(value: str) -> str:
    """Accept stable semantic versions suitable for Python metadata and Git tags."""
    if not re.fullmatch(VERSION, value):
        raise ValueError("Expected stable MAJOR.MINOR.PATCH without a v prefix")
    return value


def branch_version(branch: str) -> str:
    """Return the version of a release/vX.Y.Z or hotfix/vX.Y.Z branch."""
    match = re.fullmatch(rf"(?:release|hotfix)/v({VERSION})", branch)
    if not match:
        raise ValueError("Use release/vX.Y.Z or hotfix/vX.Y.Z")
    return match[1]


def project_version(contents: str) -> str:
    """Read the package version without evaluating repository code."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    return validate_version(tomllib.loads(contents)["project"]["version"])


def validate_pr(pr: dict, version: str | None = None) -> str:
    """Enforce the branch model, allowing only PR #1 to bootstrap main."""
    base, head = pr["base"]["ref"], pr["head"]["ref"]
    same_repo = pr["head"].get("repo", {}).get("full_name") == REPOSITORY
    if base != "main":
        return f"Allowed: {head} -> {base}"
    if pr["number"] == 1 and head == "feat/github-app-auth" and same_repo:
        return "Allowed: bootstrap PR #1 -> main (one-time exception)"
    if not same_repo:
        raise ValueError("Main accepts release branches from this repository only")
    if head == "develop":
        return "Allowed: develop -> main (integration sync; no automatic release)"
    expected = branch_version(head)
    if version is not None and version != expected:
        raise ValueError(f"Branch version {expected} does not match package {version}")
    return f"Allowed: {head} -> main"


def git(*args: str, optional: bool = False) -> str:
    """Run a Git read command, distinguishing absent refs from other failures."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if optional and result.returncode in (1, 128):
        return ""
    result.check_returncode()
    return result.stdout.strip()


def github(method: str, endpoint: str, payload: dict | None = None) -> dict:
    """Call GitHub with structured JSON; credentials stay in gh's environment."""
    command = ["gh", "api", "--method", method, f"repos/{REPOSITORY}/{endpoint}"]
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def prepare_plan(kind: str, version: str) -> dict:
    """Plan a version/changelog commit based on an immutable develop or main SHA."""
    validate_version(version)
    if kind not in {"release", "hotfix"}:
        raise ValueError("Kind must be release or hotfix")
    source = "develop" if kind == "release" else "main"
    source_sha = git(
        "rev-parse", "--verify", f"refs/remotes/origin/{source}^{{commit}}"
    )
    branch = f"{kind}/v{version}"
    if git("rev-parse", "--verify", f"refs/remotes/origin/{branch}", optional=True):
        raise ValueError(f"Branch {branch} already exists; update it through its PR")
    if git("rev-parse", "--verify", f"refs/tags/v{version}", optional=True):
        raise ValueError(f"Version v{version} is already tagged")
    original = git("show", f"{source_sha}:pyproject.toml")
    current = project_version(original)
    if tuple(map(int, version.split("."))) < tuple(map(int, current.split("."))):
        raise ValueError("Release version cannot be lower than the source version")
    before, project = original.split("[project]", 1)
    project, count = re.subn(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        project,
        count=1,
        flags=re.M,
    )
    if count != 1:
        raise ValueError("Could not update project.version")
    updated = before + "[project]" + project + "\n"
    if project_version(updated) != version:
        raise ValueError("Version update failed verification")
    previous = git(
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        "v[0-9]*",
        source_sha,
        optional=True,
    )
    revision_range = f"{previous}..{source_sha}" if previous else source_sha
    history = git("log", "--format=%h%x09%s", revision_range)
    entries = []
    for line in history.splitlines():
        sha, subject = line.split("\t", 1)
        subject = subject.replace("<", "&lt;").replace(">", "&gt;")
        entries.append(
            f"- {subject} ([{sha}](https://github.com/{REPOSITORY}/commit/{sha}))"
        )
    existing = git("show", f"{source_sha}:CHANGELOG.md", optional=True)
    existing = existing.removeprefix("# Changelog").lstrip()
    date = datetime.now(timezone.utc).date().isoformat()
    section = f"## v{version} — {date}\n\n" + (
        "\n".join(entries) or "- Release preparation."
    )
    changelog = f"# Changelog\n\n{section}\n\n{existing}".rstrip() + "\n"
    return {
        "branch": branch,
        "version": version,
        "source": source,
        "source_sha": source_sha,
        "files": {"pyproject.toml": updated, "CHANGELOG.md": changelog},
        "compare_url": f"https://github.com/{REPOSITORY}/compare/main...{branch}?expand=1",
    }


def create_release_branch(plan: dict) -> str:
    """Create a new branch atomically; never overwrite an existing branch."""
    tree = []
    for path, contents in plan["files"].items():
        blob = github(
            "POST",
            "git/blobs",
            {
                "content": base64.b64encode(contents.encode()).decode(),
                "encoding": "base64",
            },
        )
        tree.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    base_tree = git("rev-parse", f"{plan['source_sha']}^{{tree}}")
    new_tree = github("POST", "git/trees", {"base_tree": base_tree, "tree": tree})
    commit = github(
        "POST",
        "git/commits",
        {
            "message": f"chore(release): prepare v{plan['version']}",
            "tree": new_tree["sha"],
            "parents": [plan["source_sha"]],
        },
    )
    github(
        "POST",
        "git/refs",
        {"ref": f"refs/heads/{plan['branch']}", "sha": commit["sha"]},
    )
    # GITHUB_TOKEN ref creation does not trigger push CI. Dispatch it explicitly.
    github("POST", "actions/workflows/pytest.yaml/dispatches", {"ref": plan["branch"]})
    return commit["sha"]


def tag_plan(pr: dict) -> dict:
    """Validate the exact reviewed merge, including ancestry and existing tags."""
    validate_pr(pr)
    if not pr.get("merged") or pr["base"]["ref"] != "main":
        raise ValueError("Only a merged release/hotfix PR into main can be tagged")
    version = branch_version(pr["head"]["ref"])
    sha = pr.get("merge_commit_sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Missing immutable merge commit SHA")
    git("merge-base", "--is-ancestor", sha, "refs/remotes/origin/main")
    if project_version(git("show", f"{sha}:pyproject.toml")) != version:
        raise ValueError("Merge commit package version does not match release branch")
    if f"## v{version} " not in git("show", f"{sha}:CHANGELOG.md"):
        raise ValueError(
            "The release changelog must be reviewed and merged with the code"
        )
    tag = f"v{version}"
    existing = git(
        "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}", optional=True
    )
    if existing and existing != sha:
        raise ValueError("Existing release tag points at a different commit")
    return {"tag": tag, "sha": sha, "existing": bool(existing), "pr": pr["number"]}


def tag_and_dispatch(plan: dict) -> None:
    """Create an annotated tag and explicitly trigger the publisher on that tag."""
    if not plan["existing"]:
        tag = github(
            "POST",
            "git/tags",
            {
                "tag": plan["tag"],
                "message": f"Release {plan['tag']} (PR #{plan['pr']})",
                "object": plan["sha"],
                "type": "commit",
            },
        )
        github(
            "POST", "git/refs", {"ref": f"refs/tags/{plan['tag']}", "sha": tag["sha"]}
        )
    github(
        "POST",
        "actions/workflows/publish_release.yaml/dispatches",
        {"ref": plan["tag"]},
    )


def main() -> None:
    """Drive read-only policy checks, release preparation, or merged-PR tagging."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-pr")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--kind", choices=["release", "hotfix"], default="release")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--dry-run", action="store_true")
    tag = commands.add_parser("tag")
    tag.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare_plan(args.kind, args.version)
        if not args.dry_run:
            plan["commit"] = create_release_branch(plan)
    else:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        pr = event["pull_request"]
        pr.setdefault("number", event.get("number"))
        if args.command == "check-pr":
            print(validate_pr(pr, project_version(Path("pyproject.toml").read_text())))
            return
        # Re-read merged state from GitHub; never tag based on a stale PR snapshot.
        pr = github("GET", f"pulls/{int(pr['number'])}")
        plan = tag_plan(pr)
        if not args.dry_run:
            tag_and_dispatch(plan)
    print(json.dumps(plan, indent=2))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as out:
            out.write(
                f"```json\n{json.dumps({k:v for k,v in plan.items() if k != 'files'}, indent=2)}\n```\n"
            )


if __name__ == "__main__":
    main()
