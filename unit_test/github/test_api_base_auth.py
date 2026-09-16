"""The Authorization header must be resolved per request, not per client.

Capturing the token at construction is what made GitHub App auth fail an
hour into a run. These tests pin the replacement behaviour.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from gatox.cli.output import Output
from gatox.github.api import Api
from gatox.github.credentials import RefreshingProvider, StaticTokenProvider

Output(False)

TEST_PAT = "ghp_" + "A" * 36


class RotatingProvider(RefreshingProvider):
    """Hands out ghs_1, ghs_2, ... with a controllable lifetime."""

    REFRESH_MARGIN = timedelta(seconds=0)

    def __init__(self, lifetime_seconds=3600):
        super().__init__()
        self.mint_count = 0
        self.lifetime_seconds = lifetime_seconds

    async def _mint(self):
        self.mint_count += 1
        return (
            f"ghs_{self.mint_count}",
            datetime.now(timezone.utc) + timedelta(seconds=self.lifetime_seconds),
        )

    def describe(self):
        return "rotating"


def _response(status=200):
    response = MagicMock()
    response.status_code = status
    response.headers = {}
    response.json.return_value = {}
    return response


async def test_pat_still_works_positionally():
    client = AsyncMock()
    client.get.return_value = _response()
    api = Api(TEST_PAT, "2022-11-28", client=client)

    assert api.pat == TEST_PAT
    assert isinstance(api.credentials, StaticTokenProvider)

    await api.call_get("/user")
    assert (
        client.get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TEST_PAT}"
    )


async def test_construction_time_headers_still_carry_auth():
    api = Api(TEST_PAT, client=AsyncMock())
    assert api.headers["Authorization"] == f"Bearer {TEST_PAT}"


async def test_provider_token_is_used_and_pat_attribute_tracks_it():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.return_value = _response()
    api = Api(credentials=provider, client=client)

    await api.call_get("/user")

    assert client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer ghs_1"
    assert api.pat == "ghs_1"
    assert api.is_app_token() is True


async def test_expired_token_is_renewed_before_the_next_request():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.return_value = _response()
    api = Api(credentials=provider, client=client)

    await api.call_get("/user")
    # Age the credential out, as elapsed time would.
    provider._renew_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await api.call_get("/user")

    assert provider.mint_count == 2
    assert client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer ghs_2"


async def test_valid_token_is_not_reminted():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.return_value = _response()
    api = Api(credentials=provider, client=client)

    await api.call_get("/user")
    await api.call_get("/user")

    assert provider.mint_count == 1


async def test_401_triggers_one_refresh_and_one_retry():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.side_effect = [_response(401), _response(200)]
    api = Api(credentials=provider, client=client)

    response = await api.call_get("/user")

    assert response.status_code == 200
    assert provider.mint_count == 2
    assert client.get.call_count == 2
    assert client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer ghs_2"


async def test_401_is_not_retried_for_a_static_pat():
    client = AsyncMock()
    client.get.return_value = _response(401)
    api = Api(TEST_PAT, client=client)

    response = await api.call_get("/user")

    assert response.status_code == 401
    assert client.get.call_count == 1


async def test_repeated_401_after_refresh_is_returned_not_looped():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.return_value = _response(401)
    api = Api(credentials=provider, client=client)

    response = await api.call_get("/user")

    assert response.status_code == 401
    assert client.get.call_count == 2


async def test_post_also_refreshes_on_401():
    provider = RotatingProvider()
    client = AsyncMock()
    client.post.side_effect = [_response(401), _response(201)]
    api = Api(credentials=provider, client=client)

    response = await api.call_post("/repos/o/r/issues", {"title": "x"})

    assert response.status_code == 201
    assert client.post.call_count == 2


async def test_delete_also_refreshes_on_401():
    provider = RotatingProvider()
    client = AsyncMock()
    client.delete.side_effect = [_response(401), _response(204)]
    api = Api(credentials=provider, client=client)

    response = await api.call_delete("/repos/o/r/keys/1")

    assert response.status_code == 204
    assert client.delete.call_count == 2


async def test_strip_auth_sends_no_authorization_header():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.return_value = _response()
    api = Api(credentials=provider, client=client)

    await api.call_get("https://raw.githubusercontent.com/o/r/main/f", strip_auth=True)

    assert "Authorization" not in client.get.call_args.kwargs["headers"]
    # An unauthenticated fetch must not burn a token mint.
    assert provider.mint_count == 0


async def test_constructing_with_neither_pat_nor_credentials_raises():
    with pytest.raises(ValueError):
        Api(client=AsyncMock())


async def test_credential_failure_is_not_retried_as_a_transport_error():
    """A bad key must surface immediately, not after five retries.

    call_get retries transport errors five times. A credential failure is
    not one, and retrying it buried the real explanation under a generic
    "failed after 5 attempts".
    """
    from gatox.github.credentials import AppAuthError

    class BrokenProvider(RefreshingProvider):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def _mint(self):
            self.attempts += 1
            raise AppAuthError("the private key could not be parsed")

        def describe(self):
            return "broken"

    provider = BrokenProvider()
    client = AsyncMock()
    api = Api(credentials=provider, client=client)

    with pytest.raises(AppAuthError, match="could not be parsed"):
        await api.call_get("/user")

    assert provider.attempts == 1
    client.get.assert_not_called()


async def test_transport_errors_are_still_retried():
    provider = RotatingProvider()
    client = AsyncMock()
    client.get.side_effect = [
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
        _response(200),
    ]
    api = Api(credentials=provider, client=client)

    response = await api.call_get("/user")

    assert response.status_code == 200
    assert client.get.call_count == 3
