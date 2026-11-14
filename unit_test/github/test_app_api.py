"""Installation-resolution endpoints, which require JWT authentication."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gatox.cli.output import Output
from gatox.github.api import Api
from gatox.github.app_api import AppApi

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


async def test_app_installations_pagination():
    """App discovery must include installations beyond GitHub's first page."""
    base = MagicMock()
    base.call_get = AsyncMock(
        side_effect=[
            _response(200, [{"id": i} for i in range(100)]),
            _response(200, [{"id": 100}]),
        ]
    )
    result = await AppApi(base).get_app_installations()
    assert len(result) == 101
    assert result[-1]["id"] == 100
    assert base.call_get.await_args_list[1].kwargs["params"]["page"] == 2


@pytest.mark.parametrize("specific_installation", [False, True])
@pytest.mark.parametrize("second_status", [200, 403])
async def test_installation_repository_pagination(specific_installation, second_status):
    """All repositories are collected, and failures never return a partial list."""
    base = MagicMock()
    first = [{"full_name": f"owner/repo{i}"} for i in range(100)]
    base.call_get = AsyncMock(
        side_effect=[
            _response(200, {"total_count": 101, "repositories": first}),
            _response(
                second_status,
                {"total_count": 101, "repositories": [{"full_name": "owner/last"}]},
            ),
        ]
    )
    api = AppApi(base)
    result = (
        await api.get_installation_repositories("1")
        if specific_installation
        else await api.get_installation_repos()
    )
    if second_status != 200:
        assert result is None
    else:
        assert len(result["repositories"]) == 101
        assert result["repositories"][-1]["full_name"] == "owner/last"


async def test_app_empty_installations_are_not_an_api_failure():
    """Successful empty lists remain distinct from error responses."""
    base = MagicMock()
    base.call_get = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: []))
    assert await AppApi(base).get_app_installations() == []


async def test_installation_later_page_failure():
    """A failed installation page invalidates the earlier successful page."""
    base = MagicMock()
    base.call_get = AsyncMock(
        side_effect=[_response(200, [{"id": i} for i in range(100)]), _response(500)]
    )
    assert await AppApi(base).get_app_installations() is None
