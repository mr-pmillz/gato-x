"""Turns an App ID and private key into ready-to-use ``Api`` clients.

This is the piece that makes "pass the App credentials and Gato-X does the
rest" true. It owns the JWT-authenticated client used for ``/app/*`` calls,
resolves a target to the installation that covers it, and hands back an
``Api`` whose installation token renews itself for the life of the run.
"""

from __future__ import annotations

import logging

from gatox.cli.output import Output
from gatox.github.api import Api
from gatox.github.app_auth import GitHubAppAuth
from gatox.github.credentials import (
    AppAuthError,
    AppJwtProvider,
    InstallationTokenProvider,
)

logger = logging.getLogger(__name__)


class AppSession:
    """Owns the App-level JWT client and every per-installation client."""

    def __init__(
        self,
        app_id: str | int,
        private_key: str,
        socks_proxy: str | None = None,
        http_proxy: str | None = None,
        github_url: str | None = None,
    ) -> None:
        """
        Args:
            app_id: The GitHub App ID.
            private_key: Path to the PEM file, or the PEM text itself.
            socks_proxy: Optional SOCKS5 proxy as ``host:port``.
            http_proxy: Optional HTTP proxy as ``host:port``.
            github_url: Optional API base for GHES / data residency.
        """
        self.app_id = str(app_id)
        self.app_auth = GitHubAppAuth(app_id, private_key)
        self.socks_proxy = socks_proxy
        self.http_proxy = http_proxy
        self.github_url = github_url

        self._app_api: Api | None = None
        self._installation_apis: dict[str, Api] = {}
        self._resolved: dict[str, dict] = {}
        self._app_permissions: list[str] | None = None

    @property
    def app_permissions(self) -> list[str] | None:
        """Permissions from ``GET /app``, as ``name:level`` strings."""
        return self._app_permissions

    def _api_kwargs(self) -> dict:
        """Proxy / endpoint settings shared by every client this session makes."""
        kwargs: dict = {
            "socks_proxy": self.socks_proxy,
            "http_proxy": self.http_proxy,
        }
        if self.github_url is not None:
            kwargs["github_url"] = self.github_url
        return kwargs

    async def app_api(self) -> Api:
        """Return the JWT-authenticated client for ``/app/*`` endpoints.

        The JWT is the only credential GitHub accepts on those routes, and
        it cannot read repositories -- that is what the installation
        clients below are for.
        """
        if self._app_api is None:
            self._app_api = Api(
                credentials=AppJwtProvider(self.app_auth), **self._api_kwargs()
            )
        return self._app_api

    async def validate(self) -> dict:
        """Confirm the credentials work and cache the App's permissions.

        Returns:
            The ``GET /app`` payload.

        Raises:
            AppAuthError: If GitHub does not recognise the App.
        """
        api = await self.app_api()
        app_info = await api.app.get_app_info()
        if not app_info:
            raise AppAuthError(
                "Failed to authenticate as a GitHub App. Check that the App ID "
                "and private key belong to the same App."
            )

        self._app_permissions = [
            f"{name}:{level}" for name, level in app_info.get("permissions", {}).items()
        ]

        Output.info(
            f"Authenticated as GitHub App: {Output.bright(app_info['name'])} "
            f"(ID {app_info['id']})"
        )
        Output.info(f"Permissions: {Output.yellow(', '.join(self._app_permissions))}")
        return app_info

    async def resolve_installation(self, target: str) -> dict:
        """Find the installation covering ``target``.

        Args:
            target: ``"org"``, ``"owner/repo"``, or a user login. A bare
                name is tried as an organisation first, then as a user.

        Returns:
            The installation payload.

        Raises:
            AppAuthError: If the App is not installed on the target.
        """
        if target in self._resolved:
            return self._resolved[target]

        api = await self.app_api()

        if "/" in target:
            owner, repo = target.split("/", 1)
            installation = await api.app.get_repo_installation(owner, repo)
        else:
            installation = await api.app.get_org_installation(target)
            if not installation:
                installation = await api.app.get_user_installation(target)

        if not installation:
            raise AppAuthError(
                f"The GitHub App is not installed on {target}. Run "
                "`gatox app --installations` to see where it is installed."
            )

        Output.info(
            f"Resolved {Output.bright(target)} to installation "
            f"{Output.bright(str(installation['id']))}"
        )
        self._resolved[target] = installation
        return installation

    async def api_for_installation(self, installation_id: str | int) -> Api:
        """Return a client authenticated as the given installation.

        Clients are cached per installation so repeated targets in one run
        share a token rather than minting a fresh one each time.
        """
        key = str(installation_id)
        if key not in self._installation_apis:
            app_api = await self.app_api()
            provider = InstallationTokenProvider(
                app_api.app.get_installation_access_token, key
            )
            self._installation_apis[key] = Api(
                credentials=provider,
                app_permissions=self._app_permissions,
                **self._api_kwargs(),
            )
        return self._installation_apis[key]

    async def api_for_target(self, target: str) -> Api:
        """Resolve ``target`` to an installation and return its client."""
        installation = await self.resolve_installation(target)
        return await self.api_for_installation(installation["id"])

    @staticmethod
    def check_installation_covers(installation: dict, targets: list[str]) -> None:
        """Reject targets that fall outside the resolved installation.

        An installation token is scoped to one account. A repository list
        spanning several accounts would silently 404 for everything outside
        the resolved installation, which reads as "no findings" rather than
        as an authentication problem -- so say so explicitly instead.

        Args:
            installation: The installation payload that was resolved.
            targets: Repository slugs (``owner/repo``) or account names.

        Raises:
            AppAuthError: If any target belongs to a different account.
        """
        account = installation.get("account", {}).get("login", "")
        outside = sorted(
            {
                target.split("/", 1)[0]
                for target in targets
                if target.split("/", 1)[0].lower() != account.lower()
            }
        )
        if outside:
            raise AppAuthError(
                f"Installation {installation.get('id')} covers {account}, but this "
                f"run also targets {', '.join(outside)}. An installation token is "
                "scoped to one account -- run Gato-X once per account, or use "
                "`gatox app --installations` to enumerate them all."
            )

    async def close(self) -> None:
        """Close every client this session opened."""
        for api in self._installation_apis.values():
            await api.close()
        self._installation_apis.clear()
        if self._app_api is not None:
            await self._app_api.close()
            self._app_api = None
