"""Sub-API: GitHub App installation endpoints used by ``--machine`` mode."""

from __future__ import annotations

import logging

from gatox.github.api_base import ApiBase, SubApi

logger = logging.getLogger(__name__)


class AppApi(SubApi):
    """Endpoints rooted at ``/app`` and ``/installation`` / ``/app/installations/...``."""

    def __init__(self, base: ApiBase) -> None:
        super().__init__(base)

    async def get_installation_repos(self) -> dict | None:
        """List repositories accessible to the current installation token."""
        return await self._repository_pages("/installation/repositories")

    async def get_app_installations(self) -> list | None:
        """Return all installations for the GitHub App."""
        installations = []
        page = 1
        while True:
            response = await self._base.call_get(
                "/app/installations", params={"per_page": 100, "page": page}
            )
            if response.status_code != 200:
                return None
            batch = response.json()
            installations.extend(batch)
            if len(batch) < 100:
                return installations
            page += 1

    async def get_installation_access_token(self, installation_id: str) -> dict | None:
        """Mint an installation access token for ``installation_id``."""
        response = await self._base.call_post(
            f"/app/installations/{installation_id}/access_tokens"
        )
        if response.status_code == 201:
            return response.json()
        return None

    async def get_installation_info(self, installation_id: str) -> dict | None:
        """Return metadata about a specific installation."""
        response = await self._base.call_get(f"/app/installations/{installation_id}")
        if response.status_code == 200:
            return response.json()
        return None

    async def get_installation_repositories(self, installation_id: str) -> dict | None:
        """Return repositories visible to a specific installation."""
        return await self._repository_pages(
            f"/app/installations/{installation_id}/repositories"
        )

    async def _repository_pages(self, path: str) -> dict | None:
        """Collect all repository pages; a failed page invalidates the result."""
        combined = None
        page = 1
        while True:
            response = await self._base.call_get(
                path, params={"per_page": 100, "page": page}
            )
            if response.status_code != 200:
                return None
            data = response.json()
            repositories = data.get("repositories", [])
            if combined is None:
                combined = {**data, "repositories": list(repositories)}
            else:
                combined["repositories"].extend(repositories)
            if len(repositories) < 100:
                return combined
            page += 1

    async def get_app_info(self) -> dict | None:
        """Return ``GET /app`` — GitHub App identity / permissions block."""
        response = await self._base.call_get("/app")
        if response.status_code == 200:
            return response.json()
        return None

    async def get_org_installation(self, org: str) -> dict | None:
        """Return the App's installation on ``org``, or ``None`` if absent.

        Requires JWT authentication.
        """
        response = await self._base.call_get(f"/orgs/{org}/installation")
        if response.status_code == 200:
            return response.json()
        return None

    async def get_repo_installation(self, owner: str, repo: str) -> dict | None:
        """Return the App's installation covering ``owner/repo``.

        Requires JWT authentication.
        """
        response = await self._base.call_get(f"/repos/{owner}/{repo}/installation")
        if response.status_code == 200:
            return response.json()
        return None

    async def get_user_installation(self, user: str) -> dict | None:
        """Return the App's installation on a user account.

        Requires JWT authentication.
        """
        response = await self._base.call_get(f"/users/{user}/installation")
        if response.status_code == 200:
            return response.json()
        return None
