"""Exercise Git Flow against real temporary histories and mocked GitHub writes."""

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import gitflow


def pr(head="release/v1.5.0", base="main", **overrides):
    """Build the minimal GitHub PR payload for policy tests."""
    return {
        "number": 2,
        "base": {"ref": base},
        "head": {"ref": head, "repo": {"full_name": gitflow.REPOSITORY}},
        "merged": True,
        **overrides,
    }


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Create divergent main and develop branches without external repositories."""
    monkeypatch.chdir(tmp_path)

    def run(*args):
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.PIPE
        ).strip()

    run("init", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Release Test")
    Path("pyproject.toml").write_text('[project]\nname = "gato-x"\nversion = "1.4.0"\n')
    run("add", ".")
    run("commit", "-m", "initial")
    run("tag", "v1.4.0")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    run("checkout", "-b", "develop")
    Path("feature.txt").write_text("develop-only feature")
    run("add", ".")
    run("commit", "-m", "feat: new <feature>")
    run("update-ref", "refs/remotes/origin/develop", "HEAD")
    return run


@pytest.mark.parametrize(
    "version", ["1.2", "01.2.3", "1.2.3\nx=y", "$(id)", "v1.2.3", "1.2.3-rc1"]
)
def test_bad_versions(version):
    """Reject shell/output/ref injection and unsupported versions before Git/API calls."""
    with pytest.raises(ValueError):
        gitflow.validate_version(version)


def test_branch_policy():
    """Features enter develop; only trusted release routes enter main."""
    assert gitflow.validate_pr(pr("feature/test", "develop"))
    assert gitflow.validate_pr(pr("main", "develop"))
    assert gitflow.validate_pr(pr("develop"))
    assert gitflow.validate_pr(pr(), "1.5.0")
    assert gitflow.validate_pr(pr("hotfix/v1.4.1"), "1.4.1")
    assert gitflow.validate_pr(pr("feat/github-app-auth", number=1))
    foreign = pr()
    foreign["head"]["repo"]["full_name"] = "outsider/fork"
    for payload in [
        pr("feature/test"),
        pr("release/1.5.0"),
        pr("feat/github-app-auth"),
        foreign,
    ]:
        with pytest.raises(ValueError):
            gitflow.validate_pr(payload)
    with pytest.raises(ValueError, match="does not match"):
        gitflow.validate_pr(pr(), "1.4.0")


@pytest.mark.parametrize("kind,source", [("release", "develop"), ("hotfix", "main")])
def test_prepare_uses_correct_source(history, kind, source):
    """Release branches include develop changes; hotfixes begin at production."""
    plan = gitflow.prepare_plan(kind, "1.5.0")
    assert plan["source_sha"] == history("rev-parse", f"origin/{source}")
    assert gitflow.project_version(plan["files"]["pyproject.toml"]) == "1.5.0"
    assert "## v1.5.0 " in plan["files"]["CHANGELOG.md"]
    assert ("feat: new &lt;feature&gt;" in plan["files"]["CHANGELOG.md"]) == (
        kind == "release"
    )
    assert history("status", "--porcelain") == ""


def test_prepare_refuses_existing_versions_and_branches(history):
    """Preparing a release cannot overwrite a tag/branch or downgrade metadata."""
    with pytest.raises(ValueError, match="already tagged"):
        gitflow.prepare_plan("release", "1.4.0")
    with pytest.raises(ValueError, match="lower"):
        gitflow.prepare_plan("release", "1.3.9")
    history("update-ref", "refs/remotes/origin/release/v1.5.0", "HEAD")
    with pytest.raises(ValueError, match="already exists"):
        gitflow.prepare_plan("release", "1.5.0")


def test_prepare_api_creates_new_branch_and_dispatches_ci(history, monkeypatch):
    """GitHub writes preserve the selected parent and never force-update a ref."""
    plan = gitflow.prepare_plan("release", "1.5.0")
    api = Mock(return_value={"sha": "a" * 40})
    monkeypatch.setattr(gitflow, "github", api)
    gitflow.create_release_branch(plan)
    calls = api.call_args_list
    assert json.loads(json.dumps(calls[0].args[2]))["encoding"] == "base64"
    assert (
        base64.b64decode(calls[0].args[2]["content"]).decode()
        == plan["files"]["pyproject.toml"]
    )
    commit = next(call.args[2] for call in calls if call.args[1] == "git/commits")
    assert commit["parents"] == [plan["source_sha"]]
    assert calls[-2].args == (
        "POST",
        "git/refs",
        {"ref": "refs/heads/release/v1.5.0", "sha": "a" * 40},
    )
    assert calls[-1].args == (
        "POST",
        "actions/workflows/pytest.yaml/dispatches",
        {"ref": "release/v1.5.0"},
    )


def test_tag_exact_merge_even_when_main_advances(history, monkeypatch):
    """Never tag newer unreviewed changes; retries accept only the original SHA."""
    plan = gitflow.prepare_plan("release", "1.5.0")
    for name, contents in plan["files"].items():
        Path(name).write_text(contents)
    history("add", ".")
    history("commit", "-m", "release preparation")
    history("checkout", "main")
    history("merge", "--no-ff", "develop", "-m", "Merge release")
    merge = history("rev-parse", "HEAD")
    history("commit", "--allow-empty", "-m", "advance main after merge")
    history("update-ref", "refs/remotes/origin/main", "HEAD")
    payload = pr(merge_commit_sha=merge)
    tagged = gitflow.tag_plan(payload)
    assert tagged["sha"] == merge != history("rev-parse", "HEAD")
    api = Mock(return_value={"sha": "b" * 40})
    monkeypatch.setattr(gitflow, "github", api)
    gitflow.tag_and_dispatch(tagged)
    assert api.call_args_list[0].args[2]["object"] == merge
    assert api.call_args_list[-1].args == (
        "POST",
        "actions/workflows/publish_release.yaml/dispatches",
        {"ref": "v1.5.0"},
    )
    history("tag", "-a", "v1.5.0", merge, "-m", "release")
    api.reset_mock()
    gitflow.tag_and_dispatch(gitflow.tag_plan(payload))
    assert api.call_count == 1  # Dispatch retry only; tag is immutable.
    history("tag", "-f", "v1.5.0", "HEAD")
    with pytest.raises(ValueError, match="different commit"):
        gitflow.tag_plan(payload)


def test_tag_rejects_unmerged_or_mismatched_release(history):
    """A closed PR and a branch name alone cannot authorize publication."""
    with pytest.raises(ValueError, match="merged"):
        gitflow.tag_plan(pr(merged=False))
    with pytest.raises(ValueError, match="package version"):
        gitflow.tag_plan(pr(merge_commit_sha=history("rev-parse", "origin/main")))
    with pytest.raises(subprocess.CalledProcessError):
        gitflow.tag_plan(pr(merge_commit_sha=history("rev-parse", "develop")))
