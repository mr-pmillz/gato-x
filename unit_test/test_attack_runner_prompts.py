"""Interactive prompts in the attack paths must survive having no terminal.

Gato-X ships a container image and is run from CI and cron, where stdin is
at EOF. An unguarded input() there raises EOFError and ends the run in a
traceback -- and for the attack confirmation, an unanswered prompt must
never be read as consent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatox.attack.runner.c2_controller import C2Controller
from gatox.attack.runner.repository_manager import RepositoryManager
from gatox.attack.runner.webshell_utils import WebShellUtils
from gatox.cli.output import Output

Output(False)


def _eof(*_args, **_kwargs):
    raise EOFError


async def test_c2_shell_exits_cleanly_on_ctrl_d():
    """Ctrl+D is how you leave a shell; KeyboardInterrupt was already handled."""
    api = MagicMock()
    api.action.get_repo_runners = AsyncMock(return_value=[{"name": "runner-1"}])
    controller = C2Controller(
        api, user_perms={"user": "tester", "scopes": ["repo"]}, timeout=1
    )

    with patch("builtins.input", _eof):
        result = await controller.interact_webshell("acme/c2", runner_name="runner-1")

    assert result is True


async def test_branch_conflict_prompt_aborts_without_a_terminal():
    api = MagicMock()
    api.commit.get_repo_branch = AsyncMock(return_value=1)
    api.commit.delete_branch = AsyncMock(return_value=True)
    manager = RepositoryManager(api)

    with patch("builtins.input", _eof):
        result = await manager._handle_branch_conflicts("acme/fork", "feature")

    assert result is False
    # The branch must not be deleted on an unanswered prompt.
    api.commit.delete_branch.assert_not_awaited()


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------


async def test_poll_honours_its_timeout_as_seconds():
    """timeout ran the loop that many times, so a larger interval multiplied it."""
    import time

    condition = AsyncMock(return_value=None)
    started = time.monotonic()
    result = await WebShellUtils.poll_with_timeout(
        condition, timeout=2, sleep_interval=5
    )
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 2.5, f"waited {elapsed:.1f}s for a 2s timeout"


async def test_poll_returns_as_soon_as_the_condition_holds():
    condition = AsyncMock(return_value="found")
    assert await WebShellUtils.poll_with_timeout(condition, timeout=30) == "found"
    assert condition.await_count == 1


async def test_lookup_failure_is_not_reported_once_per_poll(capsys):
    """-1 printed the same error every second until the timeout."""
    api = MagicMock()
    api.action.get_recent_workflow = AsyncMock(return_value=-1)

    result = await WebShellUtils.wait_for_workflow(
        api, "acme/widgets", "sha", "wf", "t", timeout=3
    )

    assert result is None
    printed = capsys.readouterr().out.count("Failed to find the created workflow")
    assert printed <= 1, f"error printed {printed} times"


@pytest.mark.parametrize("found_id", [0, -1])
async def test_unfound_workflow_returns_none(found_id):
    api = MagicMock()
    api.action.get_recent_workflow = AsyncMock(return_value=found_id)
    assert (
        await WebShellUtils.wait_for_workflow(
            api, "acme/widgets", "sha", "wf", "t", timeout=1
        )
        is None
    )
