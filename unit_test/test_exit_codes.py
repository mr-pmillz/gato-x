"""Regression coverage for command completion, empty results and failures."""

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatox import main
from gatox.cli import cli
from gatox.cli.exit_codes import ExitCode
from gatox.cli.output import Output
from gatox.enumerate.app_enumerate import AppEnumerator
from gatox.github.search import Search
from gatox.models.execution import Execution


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    """Keep operator credentials and local configuration out of these tests."""
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "A" * 36)
    for name in ("GH_APP_ID", "GH_APP_KEY", "GH_APP_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (set(), ExitCode.NO_RESULTS),
        ({"owner/repo"}, ExitCode.SUCCESS),
        (None, ExitCode.ERROR),
    ],
)
@pytest.mark.parametrize("command", ["search", "s"])
async def test_search_exit_status(results, expected, command):
    """Empty, successful and failed searches remain distinguishable through aliases."""
    with patch("gatox.cli.cli.Searcher") as factory:
        runner = factory.return_value
        runner.use_search_api = AsyncMock(return_value=results)
        assert await cli.cli(["--no-config", command, "-t", "owner"]) == expected
        if results is not None:
            runner.present_results.assert_called_once_with(results, None)


@pytest.mark.parametrize(
    ("results", "failed", "expected"),
    [([], False, 3), ([MagicMock()], False, 0), ([MagicMock()], True, 1)],
)
async def test_local_exit_status(results, failed, expected):
    """Partial local scans must fail even when some repositories were loaded."""
    with patch("gatox.enumerate.local_enumerate.LocalEnumerator") as factory:
        runner = factory.return_value
        runner.enumerate = AsyncMock(return_value=results)
        runner.failed = failed
        assert await cli.cli(["--no-config", "enum", "--local", "."]) == expected


@pytest.mark.parametrize(
    "failure", [PermissionError("read only"), ValueError("bad output")]
)
async def test_output_failure_is_not_success(failure):
    """A completed scan cannot mask an unsuccessful requested output write."""
    with (
        patch("gatox.enumerate.local_enumerate.LocalEnumerator") as factory,
        patch("gatox.cli.cli.Output.write_json", side_effect=failure),
    ):
        factory.return_value.enumerate = AsyncMock(return_value=[])
        assert (
            await cli.cli(["--no-config", "e", "--local", ".", "-oJ", "out.json"])
            == ExitCode.ERROR
        )


@pytest.mark.parametrize(
    ("mode", "user", "repos", "failed", "expected"),
    [
        (["--validate"], {"user": "ok", "scopes": []}, [], False, 0),
        (["--validate"], None, [], False, 1),
        (["-r", "owner/repo"], {"user": "ok", "scopes": []}, [], True, 1),
        (["-t", "owner"], {"user": "ok", "scopes": []}, [], False, 3),
    ],
)
async def test_remote_exit_status(mode, user, repos, failed, expected):
    """Validation is successful without findings; inaccessible repos are failures."""
    with patch("gatox.cli.cli.Enumerator") as factory:
        runner = factory.return_value
        runner.user_perms = user
        runner.failed = failed
        runner.validate_only = AsyncMock(return_value=[])
        runner.enumerate_repos = AsyncMock(return_value=repos)
        runner.enumerate_user = AsyncMock(return_value=repos)
        runner.api.user.get_user_type = AsyncMock(return_value="User")
        assert await cli.cli(["--no-config", "e", *mode]) == expected


async def test_usage_exit_status():
    """Argument errors retain argparse's established status of two."""
    with pytest.raises(SystemExit) as exc:
        await cli.cli(["--no-config", "e"])
    assert exc.value.code == ExitCode.USAGE


def test_entry_propagates_status():
    """Installed console scripts receive the command's nonzero completion status."""
    with patch("gatox.main.cli.cli", new=AsyncMock(return_value=ExitCode.NO_RESULTS)):
        assert main.entry() == ExitCode.NO_RESULTS


def test_entry_interrupt_status():
    """Ctrl-C follows the conventional shell status instead of returning success."""

    async def interrupted(_args):
        raise KeyboardInterrupt

    with patch("gatox.main.cli.cli", interrupted):
        assert main.entry() == ExitCode.INTERRUPTED


@pytest.mark.parametrize("code", [401, 403, 422, 500])
async def test_search_http_failure_never_becomes_empty_success(code):
    """Permission-denied 403 responses must fail without an infinite retry loop."""
    api = MagicMock()
    api.call_get = AsyncMock(
        return_value=MagicMock(
            status_code=code, headers={}, json=lambda: {"message": "error"}, links={}
        )
    )
    with pytest.raises(RuntimeError, match=f"response code {code}"):
        await Search(api).search_enumeration("owner")
    assert api.call_get.await_count == 1


@pytest.mark.parametrize("code", [403, 429])
async def test_search_rate_limit_has_bounded_retries(code):
    """A real rate limit retries only three times, with a bounded delay."""
    api = MagicMock()
    api.call_get = AsyncMock(
        return_value=MagicMock(
            status_code=code,
            headers={"retry-after": "999999"},
            json=lambda: {},
            links={},
        )
    )
    with patch("gatox.github.search.asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(RuntimeError, match="three retries"):
            await Search(api).search_enumeration("owner")
        assert api.call_get.await_count == 4
        assert sleep.await_count == 3
        assert all(call.args == (300,) for call in sleep.await_args_list)


async def test_incomplete_search_is_failure():
    """GitHub's incomplete_results signal must not look like a clean scan."""
    api = MagicMock()
    api.call_get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            headers={},
            json=lambda: {"items": [], "incomplete_results": True},
            links={},
        )
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        await Search(api).search_enumeration("owner")


async def test_persistence_closes_app_session_on_failure():
    """Failed persistence closes its token-renewing App session too."""
    args = argparse.Namespace(
        deploy_key=False,
        key_path=None,
        collaborator=["user"],
        permission="pull",
        app_credentials=("1", "key"),
        target="owner/repo",
        gh_token=None,
        author_email=None,
        author_name=None,
        socks_proxy=None,
        http_proxy=None,
        api_url=None,
    )
    session = AsyncMock()
    with (
        patch(
            "gatox.cli.cli.build_app_api",
            new=AsyncMock(return_value=(session, MagicMock())),
        ),
        patch("gatox.cli.cli.PersistenceAttack") as factory,
    ):
        factory.return_value.invite_collaborators = AsyncMock(return_value=False)
        assert await cli.persistence(args, MagicMock()) == ExitCode.ERROR
        session.close.assert_awaited_once()


async def test_reported_errors_propagate_from_child_tasks_and_reset():
    """Legacy errors in concurrent tasks fail only the command that emitted them."""

    async def failing_handler(_args, _parser):
        async def fail():
            Output.error("Partial operation failed")

        await asyncio.create_task(fail())

    with patch("gatox.cli.cli.search", failing_handler):
        assert await cli.cli(["--no-config", "s", "-t", "owner"]) == ExitCode.ERROR
    with patch("gatox.cli.cli.search", new=AsyncMock(return_value=ExitCode.SUCCESS)):
        assert await cli.cli(["--no-config", "s", "-t", "owner"]) == ExitCode.SUCCESS


async def test_suppressed_errors_still_fail_command():
    """Output suppression must never change an operation's exit status."""

    async def failing_handler(_args, _parser):
        with patch.object(Output(), "suppress", True):
            Output.error("Failed operation")

    with patch("gatox.cli.cli.search", failing_handler):
        assert await cli.cli(["--no-config", "s", "-t", "owner"]) == ExitCode.ERROR


@pytest.mark.parametrize("installations", [[], [{"id": 123}]])
async def test_app_list_status_and_json(installations):
    """App listing propagates empty status and includes the records in JSON."""
    with (
        patch("gatox.cli.cli.AppSession") as sessions,
        patch("gatox.cli.cli.AppEnumerator") as enumerators,
        patch("gatox.cli.cli.Output.write_json") as write,
    ):
        sessions.return_value.close = AsyncMock()
        enumerators.return_value.validate_app = AsyncMock(return_value={"slug": "app"})
        enumerators.return_value.list_installations = AsyncMock(
            return_value=installations
        )
        result = await cli.cli(
            [
                "--no-config",
                "app",
                "--app-id",
                "1",
                "--app-key",
                "key.pem",
                "--installations",
                "-oJ",
                "app.json",
            ]
        )
        assert result == (ExitCode.SUCCESS if installations else ExitCode.NO_RESULTS)
        execution: Execution = write.call_args.args[0]
        assert execution.toJSON()["enumeration"]["installations"] == installations
        sessions.return_value.close.assert_awaited_once()


async def test_app_real_empty_list_does_not_report_failure():
    """An App installed nowhere should produce a clean empty result."""
    runner = AppEnumerator("1", "unused.pem")
    runner.api = MagicMock()
    runner.api.app.get_app_installations = AsyncMock(return_value=[])
    with patch("gatox.enumerate.app_enumerate.Output.error") as error:
        assert await runner.list_installations() == []
        error.assert_not_called()
