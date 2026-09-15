"""App credentials must be accepted on every subcommand, from flags or env."""

import argparse

import pytest

from gatox.cli import cli as cli_module
from gatox.cli.output import Output

Output(False)

TEST_PAT = "ghp_" + "A" * 36


@pytest.fixture
def pem_file(tmp_path):
    path = tmp_path / "app.pem"
    path.write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----")
    return str(path)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """App auth must never be inherited from the developer's own shell."""
    for name in ("GH_TOKEN", "GH_APP_ID", "GH_APP_KEY", "GH_APP_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)


def _parse(argv):
    """Build the real shared parser and parse argv without running a command."""
    parser = argparse.ArgumentParser()
    cli_module.configure_parser_general(parser)
    return parser.parse_args(argv)


def _validatable(argv):
    """Parse argv and fill in the attributes validate_arguments reads."""
    args = _parse(argv)
    args.socks_proxy = None
    args.http_proxy = None
    args.log_level = "CRITICAL"
    return args


def test_app_flags_parse_on_the_shared_parser(pem_file):
    args = _parse(["--app-id", "12345", "--app-key", pem_file])
    assert args.app_id == "12345"
    assert args.app_key == pem_file


def test_installation_id_flag_parses():
    assert _parse(["--installation-id", "98765"]).installation_id == "98765"


def test_app_credentials_prefers_flags_over_env(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "999")
    monkeypatch.setenv("GH_APP_KEY", "/env/key.pem")
    args = _parse(["--app-id", "12345", "--app-key", pem_file])

    assert cli_module.app_credentials_from_args(args) == ("12345", pem_file)


def test_app_credentials_fall_back_to_env(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "999")
    monkeypatch.setenv("GH_APP_KEY", pem_file)

    assert cli_module.app_credentials_from_args(_parse([])) == ("999", pem_file)


def test_no_app_credentials_returns_none():
    assert cli_module.app_credentials_from_args(_parse([])) is None


def test_installation_id_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "4242")
    assert cli_module.installation_id_from_args(_parse([])) == "4242"


def test_app_id_without_key_is_an_error():
    args = _validatable(["--app-id", "12345"])

    with pytest.raises(SystemExit):
        cli_module.validate_arguments(args, argparse.ArgumentParser())


def test_app_key_without_id_is_an_error(pem_file):
    args = _validatable(["--app-key", pem_file])

    with pytest.raises(SystemExit):
        cli_module.validate_arguments(args, argparse.ArgumentParser())


def test_validate_arguments_skips_pat_checks_with_app_credentials(pem_file):
    args = _validatable(["--app-id", "12345", "--app-key", pem_file])

    # Must neither prompt for a PAT nor call parser.error().
    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.gh_token is None
    assert args.app_credentials == ("12345", pem_file)


def test_app_credentials_win_over_a_set_gh_token(monkeypatch, pem_file):
    monkeypatch.setenv("GH_TOKEN", TEST_PAT)
    args = _validatable(["--app-id", "12345", "--app-key", pem_file])

    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.gh_token is None


def test_installation_id_is_resolved_onto_the_namespace(pem_file):
    args = _validatable(
        ["--app-id", "12345", "--app-key", pem_file, "--installation-id", "98765"]
    )

    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.resolved_installation_id == "98765"


def test_gh_token_still_required_without_app_credentials(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: TEST_PAT)
    args = _validatable([])

    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.gh_token == TEST_PAT
    assert args.app_credentials is None


def test_malformed_pat_is_still_rejected(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "not-a-token")
    args = _validatable([])

    with pytest.raises(SystemExit):
        cli_module.validate_arguments(args, argparse.ArgumentParser())


# --------------------------------------------------------------------------
# Pre-authenticated client injection
# --------------------------------------------------------------------------


def test_attacker_accepts_a_prebuilt_api_client():
    from unittest.mock import MagicMock

    from gatox.attack.attack import Attacker

    injected = MagicMock()
    assert Attacker(api_client=injected).api is injected


def test_attacker_without_token_or_client_raises():
    from gatox.attack.attack import Attacker

    with pytest.raises(ValueError):
        Attacker()


def test_persistence_attack_inherits_client_injection():
    from unittest.mock import MagicMock

    from gatox.attack.persistence.persistence_attack import PersistenceAttack

    injected = MagicMock()
    assert PersistenceAttack(api_client=injected).api is injected


def test_searcher_accepts_a_prebuilt_api_client():
    from unittest.mock import MagicMock

    from gatox.search.search import Searcher

    injected = MagicMock()
    injected.transport = None
    assert Searcher(api_client=injected).api is injected


def test_searcher_without_token_or_client_raises():
    from gatox.search.search import Searcher

    with pytest.raises(ValueError):
        Searcher()


# --------------------------------------------------------------------------
# build_app_api
# --------------------------------------------------------------------------


def _app_args(pem_file, argv_extra=()):
    args = _validatable(["--app-id", "12345", "--app-key", pem_file, *argv_extra])
    args.api_url = None
    cli_module.validate_arguments(args, argparse.ArgumentParser())
    return args


async def test_build_app_api_uses_explicit_installation_id(monkeypatch, pem_file):
    from unittest.mock import AsyncMock, MagicMock

    args = _app_args(pem_file, ["--installation-id", "98765"])

    fake_api = MagicMock()
    fake_session = AsyncMock()
    fake_session.api_for_installation.return_value = fake_api
    monkeypatch.setattr(cli_module, "AppSession", MagicMock(return_value=fake_session))

    session, api = await cli_module.build_app_api(args, target="acme")

    assert api is fake_api and session is fake_session
    fake_session.api_for_installation.assert_awaited_once_with("98765")
    fake_session.api_for_target.assert_not_awaited()


async def test_build_app_api_resolves_from_target(monkeypatch, pem_file):
    from unittest.mock import AsyncMock, MagicMock

    args = _app_args(pem_file)

    fake_api = MagicMock()
    fake_session = AsyncMock()
    fake_session.api_for_target.return_value = fake_api
    monkeypatch.setattr(cli_module, "AppSession", MagicMock(return_value=fake_session))

    session, api = await cli_module.build_app_api(args, target="acme")

    assert api is fake_api
    fake_session.api_for_target.assert_awaited_once_with("acme")
    fake_session.validate.assert_awaited_once()


async def test_build_app_api_without_target_or_installation_errors(
    monkeypatch, pem_file
):
    from unittest.mock import AsyncMock, MagicMock

    from gatox.github.credentials import AppAuthError

    args = _app_args(pem_file)
    monkeypatch.setattr(cli_module, "AppSession", MagicMock(return_value=AsyncMock()))

    with pytest.raises(AppAuthError, match="--installation-id"):
        await cli_module.build_app_api(args, target=None)


# --------------------------------------------------------------------------
# Installation resolution target selection
# --------------------------------------------------------------------------


def test_enum_target_prefers_explicit_target():
    args = argparse.Namespace(target="acme", repository=None, repositories=None)
    assert cli_module._enum_target(args) == "acme"


def test_enum_target_falls_back_to_repository():
    args = argparse.Namespace(target=None, repository="acme/widgets", repositories=None)
    assert cli_module._enum_target(args) == "acme/widgets"


def test_enum_target_reads_first_entry_of_repository_file(tmp_path):
    listing = tmp_path / "repos.txt"
    listing.write_text("\n\nacme/one\nacme/two\n")
    args = argparse.Namespace(target=None, repository=None, repositories=str(listing))

    assert cli_module._enum_target(args) == "acme/one"


def test_enum_target_is_none_when_nothing_is_set():
    args = argparse.Namespace(target=None, repository=None, repositories=None)
    assert cli_module._enum_target(args) is None


def test_enum_target_survives_an_unreadable_repository_file(tmp_path):
    args = argparse.Namespace(
        target=None, repository=None, repositories=str(tmp_path / "missing.txt")
    )
    assert cli_module._enum_target(args) is None


# --------------------------------------------------------------------------
# End to end: App credentials through the real CLI entry point
# --------------------------------------------------------------------------


async def test_enumerate_with_app_credentials_end_to_end(monkeypatch, pem_file):
    """`gatox enumerate -t acme --app-id .. --app-key ..` must not need a PAT."""
    from unittest.mock import AsyncMock, MagicMock

    from gatox.cli import cli

    fake_api = MagicMock()
    fake_session = AsyncMock()
    fake_session.api_for_target.return_value = fake_api
    fake_session.app_permissions = ["contents:read", "metadata:read"]
    monkeypatch.setattr(cli, "AppSession", MagicMock(return_value=fake_session))

    enumerator = MagicMock()
    enumerator.api = MagicMock()
    enumerator.api.user.get_user_type = AsyncMock(return_value="Organization")
    enumerator.enumerate_organization = AsyncMock(return_value={"acme": "data"})
    enumerator.user_perms = {"user": "app", "scopes": []}
    mock_enumerator_cls = MagicMock(return_value=enumerator)
    monkeypatch.setattr(cli, "Enumerator", mock_enumerator_cls)

    await cli.cli(
        ["enumerate", "-t", "acme", "--app-id", "12345", "--app-key", pem_file]
    )

    # The App installation client was handed to the enumerator, and the App's
    # own permissions were forwarded in place of PAT scopes.
    kwargs = mock_enumerator_cls.call_args.kwargs
    assert kwargs["api_client"] is fake_api
    assert kwargs["finegrained_permisions"] == {"contents:read", "metadata:read"}
    assert mock_enumerator_cls.call_args.args[0] is None

    fake_session.validate.assert_awaited_once()
    fake_session.api_for_target.assert_awaited_once_with("acme")
    # The session must be closed even on the success path.
    fake_session.close.assert_awaited_once()


async def test_search_with_app_credentials_end_to_end(monkeypatch, pem_file):
    """App credentials must reach the Searcher too."""
    from unittest.mock import AsyncMock, MagicMock

    from gatox.cli import cli

    fake_api = MagicMock()
    fake_session = AsyncMock()
    fake_session.api_for_target.return_value = fake_api
    monkeypatch.setattr(cli, "AppSession", MagicMock(return_value=fake_session))

    searcher = MagicMock()
    searcher.use_search_api = AsyncMock(return_value=[])
    mock_searcher_cls = MagicMock(return_value=searcher)
    monkeypatch.setattr(cli, "Searcher", mock_searcher_cls)

    await cli.cli(["search", "-t", "acme", "--app-id", "12345", "--app-key", pem_file])

    assert mock_searcher_cls.call_args.kwargs["api_client"] is fake_api
    fake_session.close.assert_awaited_once()


async def test_enumerate_repositories_rejects_a_cross_account_list(
    monkeypatch, pem_file, tmp_path
):
    """One installation token cannot cover two accounts; say so loudly."""
    from unittest.mock import AsyncMock, MagicMock

    from gatox.cli import cli
    from gatox.github.app_session import AppSession
    from gatox.github.credentials import AppAuthError

    listing = tmp_path / "repos.txt"
    listing.write_text("acme/one\nothercorp/two\n")

    fake_session = AsyncMock()
    fake_session.api_for_target.return_value = MagicMock()
    fake_session.app_permissions = ["contents:read"]
    fake_session.resolve_installation.return_value = {
        "id": 98765,
        "account": {"login": "acme"},
    }
    # Use the real guard rather than a mock, so the behaviour under test is real.
    fake_session.check_installation_covers = AppSession.check_installation_covers
    monkeypatch.setattr(cli, "AppSession", MagicMock(return_value=fake_session))

    enumerator = MagicMock()
    enumerator.api = MagicMock()
    enumerator.enumerate_repos = AsyncMock(return_value=[])
    enumerator.user_perms = {}
    monkeypatch.setattr(cli, "Enumerator", MagicMock(return_value=enumerator))

    # cli() reports an AppAuthError and exits rather than surfacing a traceback.
    with pytest.raises(SystemExit):
        await cli.cli(
            [
                "enumerate",
                "-R",
                str(listing),
                "--app-id",
                "12345",
                "--app-key",
                pem_file,
            ]
        )

    assert AppAuthError is not None  # imported to document what cli() catches
    enumerator.enumerate_repos.assert_not_awaited()
    fake_session.close.assert_awaited_once()


# --------------------------------------------------------------------------
# Non-interactive and misconfigured runs must fail cleanly, not crash
# --------------------------------------------------------------------------


def test_missing_token_without_a_terminal_errors_instead_of_raising(monkeypatch):
    """Docker, cron and CI have no stdin; a traceback there helps nobody."""

    def no_terminal(*_):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    args = _validatable([])

    with pytest.raises(SystemExit):
        cli_module.validate_arguments(args, argparse.ArgumentParser())


def test_app_command_without_credentials_does_not_prompt_for_a_pat(monkeypatch):
    """The app command never uses a PAT, so it must not ask for one."""
    prompted = []
    monkeypatch.setattr("builtins.input", lambda *a: prompted.append(a) or "x")

    args = _validatable([])
    args.command = "app"
    args.app = None
    args.pem = None

    with pytest.raises(SystemExit):
        cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert prompted == []


def test_app_command_accepts_the_shared_app_flags(pem_file):
    args = _validatable(["--app-id", "12345", "--app-key", pem_file])
    args.command = "app"
    args.app = None
    args.pem = None

    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.app_credentials == ("12345", pem_file)


def test_app_parser_defines_every_option_its_handler_reads():
    """`gatox app` crashed with AttributeError because two flags were missing."""
    from gatox.cli.app.config import configure_parser_app

    parser = argparse.ArgumentParser()
    configure_parser_app(parser)
    defined = {action.dest for action in parser._actions}

    required = {
        "app",
        "pem",
        "installations",
        "installation",
        "skip_runners",
        "skip_secrets",
        "skip_admin_runners",
        "output_json",
    }
    assert required <= defined, f"missing: {sorted(required - defined)}"
