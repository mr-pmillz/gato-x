"""Credential providers that keep an ``Api`` supplied with a valid token.

Gato-X historically captured a PAT at construction and used it forever.
GitHub App authentication cannot work that way: the JWT it signs is valid
for at most 10 minutes and the installation token it buys is valid for one
hour, while a single enumeration run routinely outlives both.

A provider owns one credential and the clock that governs it. ``ApiBase``
awaits :meth:`CredentialProvider.get_token` on every request, so a renewal
lands on the very next call with no coordination from call sites.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class AppAuthError(Exception):
    """Raised when a GitHub App credential cannot be obtained."""


class CredentialProvider(ABC):
    """Supplies a currently-valid token for the ``Authorization`` header."""

    @property
    def is_refreshable(self) -> bool:
        """Whether a 401 is worth retrying after a forced renewal."""
        return False

    @abstractmethod
    async def get_token(self) -> str:
        """Return a token that is valid right now, renewing if needed."""

    async def refresh(self, stale_token: str | None = None) -> str:
        """Force a renewal.

        Args:
            stale_token: The token the caller saw rejected. When another
                coroutine has already replaced it, the replacement is
                returned instead of minting again.

        Returns:
            A token to retry with.
        """
        return await self.get_token()

    @abstractmethod
    def describe(self) -> str:
        """Short human-readable description for operator-facing output."""


class StaticTokenProvider(CredentialProvider):
    """Wraps a PAT. This is the pre-existing behaviour, expressed as a provider."""

    def __init__(self, token: str) -> None:
        """
        Args:
            token: The personal access token to use for every request.

        Raises:
            ValueError: If ``token`` is empty.
        """
        if not token:
            raise ValueError("A valid GitHub token must be provided!")
        self._token = token

    async def get_token(self) -> str:
        return self._token

    def describe(self) -> str:
        return "static token"


class RefreshingProvider(CredentialProvider):
    """Base for credentials that expire and must be re-minted.

    Subclasses implement :meth:`_mint`. Expiry is tracked as an absolute
    instant rather than elapsed time so a slow run cannot drift past it,
    and renewal is serialised by a lock so a concurrent burst of requests
    produces one mint rather than dozens.
    """

    #: Renew this far ahead of the stated expiry.
    REFRESH_MARGIN = timedelta(seconds=60)

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def is_refreshable(self) -> bool:
        return True

    @property
    def expires_at(self) -> datetime | None:
        """When the cached token expires, or ``None`` if nothing is cached."""
        return self._expires_at

    @abstractmethod
    async def _mint(self) -> tuple[str, datetime]:
        """Obtain a new token and its absolute expiry."""

    def _is_usable(self) -> bool:
        """Whether the cached token is still safely inside its lifetime."""
        if self._token is None or self._expires_at is None:
            return False
        return datetime.now(timezone.utc) < self._expires_at - self.REFRESH_MARGIN

    async def get_token(self) -> str:
        if self._is_usable():
            return self._token  # type: ignore[return-value]

        async with self._lock:
            # Another coroutine may have minted while we waited for the lock.
            if self._is_usable():
                return self._token  # type: ignore[return-value]
            return await self._mint_locked()

    async def refresh(self, stale_token: str | None = None) -> str:
        async with self._lock:
            if (
                stale_token is not None
                and self._token is not None
                and self._token != stale_token
            ):
                # Someone already replaced the token the caller saw rejected.
                return self._token
            return await self._mint_locked()

    async def _mint_locked(self) -> str:
        """Mint and cache a token. Callers must hold ``self._lock``."""
        token, expires_at = await self._mint()
        self._token = token
        self._expires_at = expires_at
        logger.debug(
            "Renewed %s; expires at %s", self.describe(), expires_at.isoformat()
        )
        return token


class AppJwtProvider(RefreshingProvider):
    """Signs and caches the App-level JWT.

    GitHub caps the JWT at 10 minutes, so this turns over far more often
    than the installation token it is used to buy. Signing is local and
    cheap -- there is no network call behind this provider.
    """

    REFRESH_MARGIN = timedelta(seconds=60)

    def __init__(self, app_auth) -> None:
        """
        Args:
            app_auth: A :class:`gatox.github.app_auth.GitHubAppAuth`. It is
                taken as a parameter rather than imported to keep this
                module free of a circular import.
        """
        super().__init__()
        self._app_auth = app_auth

    async def _mint(self) -> tuple[str, datetime]:
        token = self._app_auth.generate_jwt()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self._app_auth.JWT_MAX_MINUTES
        )
        return token, expires_at

    def describe(self) -> str:
        return f"GitHub App JWT (app id {self._app_auth.app_id})"


class InstallationTokenProvider(RefreshingProvider):
    """Buys and renews the ``ghs_`` installation access token.

    This is the credential that actually reads repositories. GitHub gives
    it a one-hour life and returns 401 the moment it lapses, so it is
    renewed five minutes ahead of the stated expiry.
    """

    #: Installation tokens last an hour; five minutes of headroom keeps a
    #: long-running paginated call from expiring mid-flight.
    REFRESH_MARGIN = timedelta(minutes=5)

    #: Used only when GitHub omits ``expires_at`` from the response.
    DEFAULT_LIFETIME = timedelta(hours=1)

    def __init__(self, token_minter, installation_id: str | int) -> None:
        """
        Args:
            token_minter: Awaitable taking an installation id and returning
                GitHub's ``access_tokens`` response dict, or ``None`` on
                failure. Taking a callable rather than an ``Api`` keeps this
                module free of a circular import and makes it easy to test.
            installation_id: The installation to mint tokens for.
        """
        super().__init__()
        self._token_minter = token_minter
        self.installation_id = str(installation_id)

    @classmethod
    def _parse_expiry(cls, raw: str | None) -> datetime:
        """Turn GitHub's ISO-8601 ``expires_at`` into an aware datetime."""
        fallback = datetime.now(timezone.utc) + cls.DEFAULT_LIFETIME
        if not raw:
            return fallback
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse installation token expiry %r", raw)
            return fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    async def _mint(self) -> tuple[str, datetime]:
        response = await self._token_minter(self.installation_id)
        if not response or not response.get("token"):
            raise AppAuthError(
                "Failed to mint an installation access token for installation "
                f"{self.installation_id}. Confirm the App is installed there and "
                "that the private key and App ID match."
            )
        return response["token"], self._parse_expiry(response.get("expires_at"))

    def describe(self) -> str:
        return f"GitHub App installation token (installation {self.installation_id})"
