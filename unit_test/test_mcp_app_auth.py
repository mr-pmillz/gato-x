"""The MCP server must accept GitHub App credentials, not only GH_TOKEN."""

from unittest.mock import AsyncMock, MagicMock

import pytest

fastmcp = pytest.importorskip(
    "fastmcp", reason="fastmcp is an optional extra ('pip install gato-x[mcp]')"
)

from gatox.mcp import mcp_server  # noqa: E402
from gatox.mcp.mcp_server import MCPAuthParams, enumerator_for  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GH_TOKEN", "GH_APP_ID", "GH_APP_KEY", "GH_APP_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pem_file(tmp_path):
    path = tmp_path / "app.pem"
    path.write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----")
    return str(path)


def test_app_credentials_are_read_from_env(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_KEY", pem_file)

    assert MCPAuthParams().app_credentials == ("12345", pem_file)


def test_explicit_fields_win_over_env(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "999")
    monkeypatch.setenv("GH_APP_KEY", "/env/key.pem")

    params = MCPAuthParams(app_id="12345", app_key=pem_file)
    assert params.app_credentials == ("12345", pem_file)


def test_partial_app_credentials_are_ignored(monkeypatch):
    monkeypatch.setenv("GH_APP_ID", "12345")
    assert MCPAuthParams().app_credentials is None


def test_pat_is_still_used_when_no_app_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "A" * 36)

    params = MCPAuthParams()
    assert params.app_credentials is None
    assert params.pat == "ghp_" + "A" * 36


def test_missing_both_raises_and_mentions_app_auth():
    params = MCPAuthParams()
    with pytest.raises(ValueError, match="GH_APP_ID"):
        assert params.pat


def test_installation_id_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "4242")
    assert MCPAuthParams().resolved_installation_id == "4242"


async def test_enumerator_for_uses_pat_when_no_app_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "A" * 36)
    captured = {}

    def fake_enumerator(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(mcp_server, "Enumerator", fake_enumerator)

    async with enumerator_for(MCPAuthParams(), "acme"):
        pass

    assert captured["pat"] == "ghp_" + "A" * 36
    assert captured["api_client"] is None


async def test_enumerator_for_builds_an_app_session(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_KEY", pem_file)

    fake_api = MagicMock()
    fake_session = AsyncMock()
    fake_session.api_for_target.return_value = fake_api
    fake_session.app_permissions = ["contents:read"]
    monkeypatch.setattr(mcp_server, "AppSession", MagicMock(return_value=fake_session))

    captured = {}

    def fake_enumerator(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(mcp_server, "Enumerator", fake_enumerator)

    async with enumerator_for(MCPAuthParams(), "acme"):
        pass

    assert captured["api_client"] is fake_api
    # No PAT is consulted, so an unset GH_TOKEN must not raise.
    assert captured["pat"] is None
    assert captured["finegrained_permisions"] == {"contents:read"}
    fake_session.api_for_target.assert_awaited_once_with("acme")
    fake_session.close.assert_awaited_once()


async def test_enumerator_for_prefers_explicit_installation(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_KEY", pem_file)
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "98765")

    fake_session = AsyncMock()
    fake_session.app_permissions = None
    monkeypatch.setattr(mcp_server, "AppSession", MagicMock(return_value=fake_session))
    monkeypatch.setattr(mcp_server, "Enumerator", lambda **kwargs: MagicMock())

    async with enumerator_for(MCPAuthParams(), "acme"):
        pass

    fake_session.api_for_installation.assert_awaited_once_with("98765")
    fake_session.api_for_target.assert_not_awaited()


async def test_app_auth_without_target_or_installation_errors(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_KEY", pem_file)

    fake_session = AsyncMock()
    monkeypatch.setattr(mcp_server, "AppSession", MagicMock(return_value=fake_session))

    with pytest.raises(ValueError, match="GH_APP_INSTALLATION_ID"):
        async with enumerator_for(MCPAuthParams()):
            pass

    fake_session.close.assert_awaited_once()


async def test_session_is_closed_when_the_body_raises(monkeypatch, pem_file):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_KEY", pem_file)

    fake_session = AsyncMock()
    fake_session.app_permissions = None
    monkeypatch.setattr(mcp_server, "AppSession", MagicMock(return_value=fake_session))
    monkeypatch.setattr(mcp_server, "Enumerator", lambda **kwargs: MagicMock())

    with pytest.raises(RuntimeError):
        async with enumerator_for(MCPAuthParams(), "acme"):
            raise RuntimeError("enumeration blew up")

    fake_session.close.assert_awaited_once()
