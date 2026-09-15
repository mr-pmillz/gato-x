"""GitHub App JWT generation.

The JWT proves possession of the App's private key. It is short-lived by
mandate -- GitHub rejects any ``exp`` more than 10 minutes out -- and it is
only accepted on ``/app/*`` endpoints. Repository traffic runs on an
installation token bought with it; see
:class:`gatox.github.credentials.InstallationTokenProvider`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from gatox.github.credentials import AppAuthError

logger = logging.getLogger(__name__)


class AppKeyNotFoundError(AppAuthError, FileNotFoundError):
    """The private key file does not exist.

    Inherits from both so callers can catch either the App-auth family or
    the ordinary filesystem error.
    """


class GitHubAppAuth:
    """Handles GitHub App authentication and JWT generation."""

    #: GitHub refuses a JWT whose lifetime exceeds this.
    JWT_MAX_MINUTES = 10

    #: Backdate ``iat`` by this much so a fast clock on our side does not
    #: produce a token GitHub considers issued in the future.
    CLOCK_SKEW_SECONDS = 60

    def __init__(self, app_id: str | int, private_key: str):
        """Initialize GitHub App authentication.

        Args:
            app_id: The GitHub App ID.
            private_key: Path to the private key PEM file, or the PEM text
                itself. Inline PEM lets the key come from an environment
                variable or a secret manager without touching disk.
        """
        self.app_id = str(app_id)
        self._inline_key = private_key if self._looks_like_pem(private_key) else None
        self.private_key_path = None if self._inline_key else Path(private_key)
        self._private_key: str | None = None

    @staticmethod
    def _looks_like_pem(value: str) -> bool:
        """Whether ``value`` is PEM text rather than a path to one."""
        return isinstance(value, str) and "-----BEGIN" in value

    def _load_private_key(self) -> str:
        """Load the private key, reading it from disk at most once."""
        if self._private_key is not None:
            return self._private_key

        if self._inline_key is not None:
            self._private_key = self._inline_key
            return self._private_key

        assert self.private_key_path is not None
        if not self.private_key_path.exists():
            raise AppKeyNotFoundError(
                f"Private key file not found: {self.private_key_path}"
            )

        try:
            with open(self.private_key_path) as handle:
                self._private_key = handle.read()
        except OSError as exc:
            raise AppAuthError(
                f"Could not read the private key file {self.private_key_path}: {exc}"
            ) from exc

        if not self._looks_like_pem(self._private_key):
            raise AppAuthError(
                f"{self.private_key_path} does not look like a PEM private key. "
                "Download the key from the GitHub App's settings page."
            )

        return self._private_key

    def generate_jwt(self, expiration_minutes: int = JWT_MAX_MINUTES) -> str:
        """Generate a JWT for GitHub App authentication.

        Args:
            expiration_minutes: JWT expiration time in minutes (max 10).

        Returns:
            The generated JWT token.

        Raises:
            ValueError: If ``expiration_minutes`` exceeds GitHub's cap.
            AppAuthError: If the private key cannot be read or used.
        """
        if expiration_minutes > self.JWT_MAX_MINUTES:
            raise ValueError(
                f"JWT expiration cannot exceed {self.JWT_MAX_MINUTES} minutes"
            )

        private_key = self._load_private_key()
        now = datetime.now(timezone.utc)
        payload = {
            "iat": int((now - timedelta(seconds=self.CLOCK_SKEW_SECONDS)).timestamp()),
            "exp": int((now + timedelta(minutes=expiration_minutes)).timestamp()),
            "iss": self.app_id,
        }

        try:
            token = jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as exc:  # noqa: BLE001 -- re-raised with guidance below
            raise AppAuthError(self._explain_key_failure(exc)) from exc

        logger.debug("Generated JWT for App ID %s", self.app_id)
        return token

    def _explain_key_failure(self, exc: Exception) -> str:
        """Turn a signing failure into something the operator can act on.

        The diagnosis runs only after signing has already failed, so a key
        is never rejected for failing a check that PyJWT itself would have
        accepted.
        """
        pem = (self._private_key or "").encode()

        try:
            key = load_pem_private_key(pem, password=None)
        except TypeError:
            return (
                "The private key is encrypted. Gato-X cannot use a passphrase "
                "protected key; decrypt it first with "
                "`openssl rsa -in key.pem -out key-decrypted.pem`."
            )
        except ValueError as load_exc:
            return (
                "The private key could not be parsed as a PEM private key "
                f"({load_exc}). Download a fresh key from the GitHub App's "
                "settings page."
            )
        except Exception:  # noqa: BLE001 -- fall through to the generic message
            return f"Could not sign a JWT with the supplied private key: {exc}"

        if not isinstance(key, RSAPrivateKey):
            return (
                "GitHub Apps require an RSA private key, and the supplied key is "
                f"a {type(key).__name__.lstrip('_')} instead."
            )

        return f"Could not sign a JWT with the supplied private key: {exc}"
