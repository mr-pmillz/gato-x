"""Tests for the credential providers that keep tokens fresh.

These cover the clock behaviour (when a token is considered usable) and
the concurrency behaviour (that a burst of requests produces one mint),
which is where the GitHub App auth path is easiest to get wrong.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from gatox.github.credentials import (
    AppAuthError,
    InstallationTokenProvider,
    RefreshingProvider,
    StaticTokenProvider,
)


async def test_static_provider_returns_token_forever():
    provider = StaticTokenProvider("ghp_" + "A" * 36)
    assert await provider.get_token() == "ghp_" + "A" * 36
    assert await provider.get_token() == "ghp_" + "A" * 36
    assert provider.is_refreshable is False


async def test_static_provider_rejects_empty_token():
    with pytest.raises(ValueError):
        StaticTokenProvider("")


async def test_static_provider_refresh_is_a_noop():
    provider = StaticTokenProvider("ghp_token")
    assert await provider.refresh() == "ghp_token"
    assert provider.describe() == "static token"


class CountingProvider(RefreshingProvider):
    """Test double that hands out sequential tokens with a controllable expiry."""

    REFRESH_MARGIN = timedelta(seconds=60)

    def __init__(self, lifetime_seconds=3600):
        super().__init__()
        self.mint_count = 0
        self.lifetime_seconds = lifetime_seconds

    async def _mint(self):
        self.mint_count += 1
        expires = datetime.now(timezone.utc) + timedelta(seconds=self.lifetime_seconds)
        return f"token-{self.mint_count}", expires

    def describe(self):
        return "counting provider"


async def test_refreshing_provider_mints_once_while_valid():
    provider = CountingProvider()
    assert await provider.get_token() == "token-1"
    assert await provider.get_token() == "token-1"
    assert provider.mint_count == 1
    assert provider.is_refreshable is True


async def test_refreshing_provider_remints_inside_margin():
    # A lifetime shorter than the refresh margin means the token is never
    # considered usable, so every call re-mints.
    provider = CountingProvider(lifetime_seconds=30)
    assert await provider.get_token() == "token-1"
    assert await provider.get_token() == "token-2"
    assert provider.mint_count == 2


async def test_concurrent_gets_collapse_into_one_mint():
    provider = CountingProvider()
    tokens = await asyncio.gather(*(provider.get_token() for _ in range(25)))
    assert provider.mint_count == 1
    assert set(tokens) == {"token-1"}


async def test_refresh_forces_a_new_token():
    provider = CountingProvider()
    first = await provider.get_token()
    assert await provider.refresh() == "token-2"
    assert first == "token-1"


async def test_concurrent_refresh_of_same_stale_token_mints_once():
    provider = CountingProvider()
    stale = await provider.get_token()
    results = await asyncio.gather(
        *(provider.refresh(stale_token=stale) for _ in range(10))
    )
    # One mint for the whole burst; every waiter sees the same replacement.
    assert provider.mint_count == 2
    assert set(results) == {"token-2"}


async def test_expires_at_is_none_before_first_mint():
    assert CountingProvider().expires_at is None


async def test_mint_failure_surfaces_as_app_auth_error():
    class FailingProvider(RefreshingProvider):
        async def _mint(self):
            raise AppAuthError("mint failed")

        def describe(self):
            return "failing"

    with pytest.raises(AppAuthError):
        await FailingProvider().get_token()


def _token_response(token="ghs_abc", minutes=60):
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"token": token, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}


async def test_installation_token_is_minted_and_cached():
    calls = []

    async def minter(installation_id):
        calls.append(installation_id)
        return _token_response(f"ghs_{len(calls)}")

    provider = InstallationTokenProvider(minter, "98765")
    assert await provider.get_token() == "ghs_1"
    assert await provider.get_token() == "ghs_1"
    assert calls == ["98765"]
    assert provider.installation_id == "98765"


async def test_installation_id_is_stringified():
    async def minter(installation_id):
        return _token_response()

    assert InstallationTokenProvider(minter, 98765).installation_id == "98765"


async def test_installation_token_expiry_is_read_from_the_response():
    async def minter(installation_id):
        return _token_response(minutes=60)

    provider = InstallationTokenProvider(minter, "1")
    await provider.get_token()
    remaining = provider.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=55) < remaining <= timedelta(minutes=60)


async def test_short_lived_installation_token_is_reminted_each_call():
    calls = []

    async def minter(installation_id):
        calls.append(installation_id)
        # Two minutes is inside the five-minute refresh margin.
        return _token_response(f"ghs_{len(calls)}", minutes=2)

    provider = InstallationTokenProvider(minter, "1")
    assert await provider.get_token() == "ghs_1"
    assert await provider.get_token() == "ghs_2"


async def test_missing_expiry_falls_back_to_one_hour():
    async def minter(installation_id):
        return {"token": "ghs_noexpiry"}

    provider = InstallationTokenProvider(minter, "1")
    assert await provider.get_token() == "ghs_noexpiry"
    remaining = provider.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=55) < remaining <= timedelta(hours=1)


async def test_unparseable_expiry_falls_back_to_one_hour():
    async def minter(installation_id):
        return {"token": "ghs_x", "expires_at": "not-a-date"}

    provider = InstallationTokenProvider(minter, "1")
    assert await provider.get_token() == "ghs_x"
    assert provider.expires_at is not None


async def test_naive_expiry_is_treated_as_utc():
    async def minter(installation_id):
        naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        return {"token": "ghs_x", "expires_at": naive.isoformat()}

    provider = InstallationTokenProvider(minter, "1")
    await provider.get_token()
    assert provider.expires_at.tzinfo is not None


async def test_failed_mint_raises_app_auth_error_naming_the_installation():
    async def minter(installation_id):
        return None

    provider = InstallationTokenProvider(minter, "424242")
    with pytest.raises(AppAuthError, match="424242"):
        await provider.get_token()


async def test_response_without_token_key_is_an_error():
    async def minter(installation_id):
        return {"message": "Bad credentials"}

    with pytest.raises(AppAuthError):
        await InstallationTokenProvider(minter, "1").get_token()


async def test_describe_names_the_installation():
    async def minter(installation_id):
        return _token_response()

    assert "77" in InstallationTokenProvider(minter, "77").describe()
