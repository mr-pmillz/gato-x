"""Tests for the AppSession orchestrator.

``app_api`` is patched throughout so no JWT is ever signed; these tests are
about resolution, caching and scoping, not cryptography.
"""

from unittest.mock import AsyncMock, patch

import pytest

from gatox.cli.output import Output
from gatox.github.app_session import AppSession
from gatox.github.credentials import AppAuthError, InstallationTokenProvider

Output(False)

APP_INFO = {
    "id": 12345,
    "name": "Test App",
    "owner": {"login": "test-owner"},
    "permissions": {"contents": "read", "metadata": "read"},
}


def _session():
    # Inline PEM is accepted directly, so no file is needed.
    return AppSession("12345", "-----BEGIN RSA PRIVATE KEY-----\nx\n")


def _patched(session, fake_api):
    return patch.object(session, "app_api", AsyncMock(return_value=fake_api))


async def test_validate_reports_app_and_caches_permissions():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_app_info.return_value = APP_INFO

    with _patched(session, fake_api):
        info = await session.validate()

    assert info["name"] == "Test App"
    assert set(session.app_permissions) == {"contents:read", "metadata:read"}


async def test_validate_raises_when_app_info_unavailable():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_app_info.return_value = None

    with _patched(session, fake_api):
        with pytest.raises(AppAuthError, match="App ID"):
            await session.validate()


async def test_resolve_installation_prefers_org():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_org_installation.return_value = {"id": 98765}

    with _patched(session, fake_api):
        result = await session.resolve_installation("acme")

    assert result["id"] == 98765
    fake_api.app.get_org_installation.assert_awaited_once_with("acme")
    fake_api.app.get_user_installation.assert_not_awaited()


async def test_resolve_installation_falls_back_to_user():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_org_installation.return_value = None
    fake_api.app.get_user_installation.return_value = {"id": 555}

    with _patched(session, fake_api):
        result = await session.resolve_installation("octocat")

    assert result["id"] == 555


async def test_resolve_installation_uses_repo_route_for_owner_slash_repo():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_repo_installation.return_value = {"id": 42}

    with _patched(session, fake_api):
        result = await session.resolve_installation("acme/widgets")

    assert result["id"] == 42
    fake_api.app.get_repo_installation.assert_awaited_once_with("acme", "widgets")


async def test_unresolvable_target_names_the_target():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_org_installation.return_value = None
    fake_api.app.get_user_installation.return_value = None

    with _patched(session, fake_api):
        with pytest.raises(AppAuthError, match="ghost-org"):
            await session.resolve_installation("ghost-org")


async def test_api_for_installation_is_cached_per_installation():
    session = _session()
    fake_api = AsyncMock()

    with _patched(session, fake_api):
        first = await session.api_for_installation("98765")
        second = await session.api_for_installation("98765")
        other = await session.api_for_installation("11111")

    assert first is second
    assert other is not first
    assert isinstance(first.credentials, InstallationTokenProvider)
    assert first.credentials.installation_id == "98765"


async def test_api_for_target_resolves_then_builds():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_org_installation.return_value = {"id": 98765}

    with _patched(session, fake_api):
        api = await session.api_for_target("acme")

    assert api.credentials.installation_id == "98765"


async def test_installation_api_carries_app_permissions():
    session = _session()
    fake_api = AsyncMock()
    fake_api.app.get_app_info.return_value = APP_INFO

    with _patched(session, fake_api):
        await session.validate()
        api = await session.api_for_installation("1")

    assert set(api.app_permissions) == {"contents:read", "metadata:read"}


async def test_close_closes_every_client():
    session = _session()
    fake_app_api = AsyncMock()

    with _patched(session, fake_app_api):
        installation_api = await session.api_for_installation("1")
        installation_api.close = AsyncMock()
        session._app_api = fake_app_api
        await session.close()

    installation_api.close.assert_awaited_once()
    fake_app_api.close.assert_awaited_once()
    assert session._installation_apis == {}


def test_coverage_check_passes_for_matching_account():
    installation = {"id": 1, "account": {"login": "acme"}}
    # Must not raise.
    AppSession.check_installation_covers(installation, ["acme/one", "acme/two"])


def test_coverage_check_is_case_insensitive():
    installation = {"id": 1, "account": {"login": "Acme"}}
    AppSession.check_installation_covers(installation, ["acme/one", "ACME/two"])


def test_coverage_check_rejects_targets_from_another_account():
    installation = {"id": 98765, "account": {"login": "acme"}}

    with pytest.raises(AppAuthError) as excinfo:
        AppSession.check_installation_covers(
            installation, ["acme/one", "othercorp/two", "thirdco/three"]
        )

    message = str(excinfo.value)
    assert "othercorp" in message and "thirdco" in message
    assert "98765" in message


def test_api_kwargs_omit_github_url_when_unset():
    session = _session()
    assert "github_url" not in session._api_kwargs()


def test_api_kwargs_include_github_url_when_set():
    session = AppSession(
        "1",
        "-----BEGIN RSA PRIVATE KEY-----\nx\n",
        github_url="https://ghes.corp.example",
    )
    assert session._api_kwargs()["github_url"] == "https://ghes.corp.example"
