import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from gatox.cli.output import Output
from gatox.github.api import Api
from gatox.github.api_base import ApiBase, resolve_api_endpoints

logging.root.setLevel(logging.DEBUG)

output = Output(False)


def test_initialize():
    """Test initialization of API abstraction layer."""
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    abstraction_layer = Api(test_pat, "2022-11-28")

    assert abstraction_layer.pat == test_pat
    assert abstraction_layer.verify_ssl is True


def test_socks():
    """Test that we can successfully configure a SOCKS proxy."""
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    abstraction_layer = Api(test_pat, "2022-11-28", socks_proxy="localhost:9090")

    assert abstraction_layer.transport == "socks5://localhost:9090"


def test_http_proxy():
    """Test that we can successfully configure an HTTP proxy."""
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    abstraction_layer = Api(test_pat, "2022-11-28", http_proxy="localhost:1080")

    assert abstraction_layer.transport == "http://localhost:1080"


def test_socks_and_http():
    """Test initializing API abstraction layer with SOCKS and HTTP proxy,
    which should raise a valueerror.
    """
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    with pytest.raises(ValueError):
        Api(
            test_pat,
            "2022-11-28",
            socks_proxy="localhost:1090",
            http_proxy="localhost:8080",
        )


async def test_invalid_pat():
    """Test calling a request with an invalid PAT"""
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    mock_client = AsyncMock()

    mock_response = MagicMock()
    mock_client.get.return_value = mock_response
    mock_response.status_code = 401

    abstraction_layer = Api(test_pat, "2022-11-28", client=mock_client)
    assert await abstraction_layer.user.check_user() is None


@patch("gatox.github.api_base.asyncio.sleep")
async def test_handle_ratelimit(mock_time):
    """Test rate limit handling"""
    test_pat = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    mock_client = AsyncMock()
    api = Api(test_pat, "2022-11-28", client=mock_client)

    test_headers = {
        "X-Ratelimit-Remaining": 100,
        "Date": "Fri, 09 Jun 2023 22:12:41 GMT",
        "X-Ratelimit-Reset": 1686351401,
        "X-Ratelimit-Resource": "core",
        "X-RateLimit-Limit": 5000,
    }

    await api._check_rate_limit(test_headers)

    mock_time.assert_called_once()


@pytest.mark.parametrize(
    "github_url,expected_rest,expected_graphql",
    [
        (None, "https://api.github.com", "https://api.github.com/graphql"),
        (
            "https://api.github.com",
            "https://api.github.com",
            "https://api.github.com/graphql",
        ),
        (
            "https://ghe.example.com/api/v3",
            "https://ghe.example.com/api/v3",
            "https://ghe.example.com/api/graphql",
        ),
        (
            "https://ghe.example.com/api/v3/",
            "https://ghe.example.com/api/v3",
            "https://ghe.example.com/api/graphql",
        ),
        (
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        # api.* hosts serve REST from the root: the GHES /api/v3 suffix is
        # dropped rather than 404ing every route below it.
        (
            "https://api.sub.ghe.com/api/v3",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        (
            "https://api.sub.ghe.com/api/v3/",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        # The URL an operator actually has is the one in their browser. Each
        # deployment shape must resolve to its own API host from that alone.
        (
            "https://sub.ghe.com",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        (
            "https://sub.ghe.com/orgs/testOrg",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        (
            "sub.ghe.com",
            "https://api.sub.ghe.com",
            "https://api.sub.ghe.com/graphql",
        ),
        (
            "https://github.com",
            "https://api.github.com",
            "https://api.github.com/graphql",
        ),
        (
            "https://github.com/testOrg/testRepo",
            "https://api.github.com",
            "https://api.github.com/graphql",
        ),
        (
            "https://api.github.com",
            "https://api.github.com",
            "https://api.github.com/graphql",
        ),
        # A bare GHES hostname must gain /api/v3 for REST and /api/graphql for
        # GraphQL -- /api/v3/graphql is not a route.
        (
            "https://ghes.example.com",
            "https://ghes.example.com/api/v3",
            "https://ghes.example.com/api/graphql",
        ),
        (
            "https://ghes.example.com:8443",
            "https://ghes.example.com:8443/api/v3",
            "https://ghes.example.com:8443/api/graphql",
        ),
        (
            "http://ghes.example.com",
            "http://ghes.example.com/api/v3",
            "http://ghes.example.com/api/graphql",
        ),
    ],
)
def test_graphql_url_derived_from_api_url(github_url, expected_rest, expected_graphql):
    """GHES serves GraphQL from /api/graphql rather than under the REST base."""
    api = Api("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", github_url=github_url)

    assert api.github_url == expected_rest
    assert api.graphql_url == expected_graphql
    assert api._build_url("/graphql") == expected_graphql
    assert api._build_url("/user") == f"{expected_rest}/user"


async def test_graphql_post_targets_enterprise_endpoint():
    """A /graphql POST must go to the enterprise GraphQL URL."""
    mock_client = AsyncMock()
    mock_client.post.return_value = MagicMock(status_code=200)

    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghe.example.com/api/v3",
        client=mock_client,
    )

    await api.call_post("/graphql", {"query": "{}"})

    assert mock_client.post.call_args.args[0] == "https://ghe.example.com/api/graphql"


async def test_raw_file_not_fetched_from_public_host_for_enterprise():
    """Enterprise repo names must never be sent to raw.githubusercontent.com."""
    mock_client = AsyncMock()

    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghe.example.com/api/v3",
        client=mock_client,
    )

    assert await api._get_raw_file("internal/repo", "action.yml", "main") is None
    mock_client.get.assert_not_called()


async def test_public_repo_file_uses_contents_api_on_enterprise():
    """retrieve_repo_file falls back to the contents API on enterprise."""
    mock_client = AsyncMock()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        headers={},
        json=MagicMock(return_value={"content": "b2s6IHRydWU="}),
    )

    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghe.example.com/api/v3",
        client=mock_client,
    )

    workflow = await api.repo.retrieve_repo_file(
        "internal/repo", ".github/workflows/ci.yml", "main", public=True
    )

    assert workflow is not None
    assert (
        mock_client.get.call_args.args[0]
        == "https://ghe.example.com/api/v3/repos/internal/repo/contents/"
        ".github/workflows/ci.yml"
    )


def test_build_url_passes_absolute_urls_through():
    """Artifact/log download URLs come back absolute from the API."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghe.example.com/api/v3",
    )

    download_url = "https://ghe.example.com/api/v3/repos/o/r/actions/artifacts/1/zip"

    assert api._build_url(download_url) == download_url


def test_ghe_com_api_url_keeps_rest_paths_at_host_root():
    """A /api/v3 suffix on an api.* host is dropped, not appended to routes."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://api.sub.ghe.com/api/v3",
    )

    assert api._build_url("/orgs/testOrg") == "https://api.sub.ghe.com/orgs/testOrg"
    assert api._build_url("/user") == "https://api.sub.ghe.com/user"
    assert api.graphql_url == "https://api.sub.ghe.com/graphql"


def test_ghes_api_url_keeps_api_v3_prefix():
    """A GHES hostname keeps /api/v3 for REST and uses /api/graphql."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghes.corp.local/api/v3",
    )

    assert (
        api._build_url("/orgs/testOrg") == "https://ghes.corp.local/api/v3/orgs/testOrg"
    )
    assert api.graphql_url == "https://ghes.corp.local/api/graphql"


def test_browser_url_reaches_the_ghe_com_api_host():
    """The web host serves HTML: routes must be built against api.SUB.ghe.com."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://sub.ghe.com",
    )

    assert api._build_url("/orgs/testOrg") == "https://api.sub.ghe.com/orgs/testOrg"
    assert api._build_url("/user") == "https://api.sub.ghe.com/user"
    assert api._build_url("/graphql") == "https://api.sub.ghe.com/graphql"


def test_browser_url_for_ghes_gains_api_v3():
    """A bare GHES hostname is not the REST base; /api/v3 must be appended."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghes.example.com",
    )

    assert (
        api._build_url("/orgs/testOrg")
        == "https://ghes.example.com/api/v3/orgs/testOrg"
    )
    assert api._build_url("/graphql") == "https://ghes.example.com/api/graphql"


def test_ghe_com_keeps_certificate_verification():
    """ghe.com is GitHub operated with a public CA: never send the PAT blind."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://sub.ghe.com",
    )

    assert api.verify_ssl is True
    assert api.is_github_cloud is True
    assert api.is_public_github is False


def test_ghes_relaxes_certificate_verification():
    """GHES is commonly fronted by a private CA, so verification is relaxed."""
    api = Api(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        github_url="https://ghes.example.com",
    )

    assert api.verify_ssl is False
    assert api.is_github_cloud is False


def test_resolve_api_endpoints_rejects_unparseable_url():
    with pytest.raises(ValueError):
        resolve_api_endpoints("https:///nohost")


@pytest.mark.parametrize(
    "status,body,headers,expected",
    [
        (
            403,
            json.dumps(
                {
                    "message": (
                        "Although you appear to have the correct authorization "
                        "credentials, the `acme` organization has an IP allow "
                        "list enabled, and 203.0.113.5 is not permitted to "
                        "access this resource."
                    ),
                    "documentation_url": "https://docs.github.com/rest",
                }
            ),
            {"Content-Type": "application/json"},
            "IP allow list enabled",
        ),
        (
            401,
            json.dumps({"message": "Bad credentials"}),
            {"Content-Type": "application/json"},
            "Bad credentials",
        ),
        # Hitting the web host instead of the API returns a rendered page.
        (
            404,
            "<!DOCTYPE html><html><body>Not Found</body></html>",
            {"Content-Type": "text/html"},
            "returned HTML rather than a JSON API response",
        ),
        (500, "", {}, "no error detail was returned"),
    ],
)
def test_describe_failure_surfaces_the_api_explanation(status, body, headers, expected):
    """The operator needs GitHub's own message, not a guess at the cause."""
    response = httpx.Response(
        status_code=status,
        content=body,
        headers=headers,
        request=httpx.Request("GET", "https://api.sub.ghe.com/orgs/acme"),
    )

    assert expected in ApiBase.describe_failure(response)
