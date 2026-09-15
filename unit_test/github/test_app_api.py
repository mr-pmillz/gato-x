"""Installation-resolution endpoints, which require JWT authentication."""

from unittest.mock import AsyncMock, MagicMock

from gatox.cli.output import Output
from gatox.github.api import Api

Output(False)

TEST_PAT = "ghp_" + "A" * 36


def _response(status, payload=None):
    response = MagicMock()
    response.status_code = status
    response.headers = {}
    response.json.return_value = payload or {}
    return response


async def test_get_org_installation_returns_payload():
    client = AsyncMock()
    client.get.return_value = _response(200, {"id": 98765})
    api = Api(TEST_PAT, client=client)

    result = await api.app.get_org_installation("acme")

    assert result["id"] == 98765
    assert client.get.call_args.args[0].endswith("/orgs/acme/installation")


async def test_get_org_installation_returns_none_when_not_installed():
    client = AsyncMock()
    client.get.return_value = _response(404, {"message": "Not Found"})
    api = Api(TEST_PAT, client=client)

    assert await api.app.get_org_installation("acme") is None


async def test_get_repo_installation_uses_owner_and_repo():
    client = AsyncMock()
    client.get.return_value = _response(200, {"id": 42})
    api = Api(TEST_PAT, client=client)

    result = await api.app.get_repo_installation("acme", "widgets")

    assert result["id"] == 42
    assert client.get.call_args.args[0].endswith("/repos/acme/widgets/installation")


async def test_get_repo_installation_returns_none_on_404():
    client = AsyncMock()
    client.get.return_value = _response(404)
    api = Api(TEST_PAT, client=client)

    assert await api.app.get_repo_installation("acme", "widgets") is None


async def test_get_user_installation_uses_user_route():
    client = AsyncMock()
    client.get.return_value = _response(200, {"id": 7})
    api = Api(TEST_PAT, client=client)

    result = await api.app.get_user_installation("octocat")

    assert result["id"] == 7
    assert client.get.call_args.args[0].endswith("/users/octocat/installation")


async def test_get_user_installation_returns_none_on_404():
    client = AsyncMock()
    client.get.return_value = _response(404)
    api = Api(TEST_PAT, client=client)

    assert await api.app.get_user_installation("octocat") is None
