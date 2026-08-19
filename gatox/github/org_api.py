"""Sub-API: GitHub organisation endpoints."""

from __future__ import annotations

import logging

from gatox.cli.output import Output
from gatox.enumerate.ingest.ingest import DataIngestor
from gatox.github.api_base import ApiBase, SubApi
from gatox.github.gql_queries import GqlQueries

logger = logging.getLogger(__name__)


class OrgApi(SubApi):
    """Endpoints rooted at ``/orgs/{org}``."""

    def __init__(self, base: ApiBase) -> None:
        super().__init__(base)

    async def get_organization_details(self, org: str) -> dict | None:
        """Return ``GET /orgs/{org}``. ``None`` for 404 / permission issues."""
        result = await self._base.call_get(f"/orgs/{org}")

        if result.status_code == 200:
            return result.json()

        # Surfaced rather than logged: without the API's own message the
        # operator cannot tell a missing org from an IP allow list, an
        # SSO-unauthorized token or a wrong --api-url.
        self._base.warn_failure(result, f"the {org} organization")
        return None

    async def validate_sso(self, org: str, repository: str) -> bool:
        """Return ``True`` if the PAT has SSO-validated access to ``org``.

        We probe ``/orgs/{org}/repos`` and a known repo. A 403 with the
        SAML enforcement message means the PAT is not authorised against
        this org.
        """
        org_repos = await self._base.call_get(f"/orgs/{org}/repos")

        if org_repos.status_code != 200:
            # Indexing .json()['message'] here used to raise when the body was
            # not the expected JSON error object, masking the real failure.
            Output.warn(
                "SSO does not seem to be enabled for this PAT! Error message: "
                f"{self._base.describe_failure(org_repos)}"
            )
            return False

        result = await self._base.call_get(f"/repos/{repository}")
        if result.status_code == 403:
            Output.warn(
                "SSO does not seem to be enabled for this PAT! However, this "
                "PAT does have some access to the GitHub Enterprise. Error "
                f"message: {self._base.describe_failure(result)}"
            )
            return False
        else:
            return True

    async def check_org_runners(self, org: str) -> dict | None:
        """Return runner-listing JSON for the org (requires ``admin:org``)."""
        result = await self._base.call_get(f"/orgs/{org}/actions/runners")

        if result.status_code == 200:
            runner_info = result.json()
            if runner_info["total_count"] > 0:
                return runner_info
        else:
            self._base.warn_failure(result, f"self-hosted runners for {org}")
        return None

    async def get_org_repo_names_graphql(self, org: str, type: str) -> list[str]:
        """Return repository names within an organisation via GraphQL."""
        repo_names: list[str] = []
        if type not in ["PUBLIC", "PRIVATE"]:
            raise ValueError("Unsupported type!")

        cursor: str | None = None
        while True:
            query = {
                "query": GqlQueries.GET_ORG_REPOS,
                "variables": {"orgName": org, "repoTypes": type, "cursor": cursor},
            }

            response = await self._base.call_post("/graphql", query)
            if response.status_code == 200:
                data = response.json()
                repos = [
                    edge["node"]["name"]
                    for edge in data["data"]["organization"]["repositories"]["edges"]
                ]
                repo_names.extend(repos)

                pageInfo = data["data"]["organization"]["repositories"]["pageInfo"]
                cursor = pageInfo["endCursor"] if pageInfo["hasNextPage"] else None

                if not pageInfo["hasNextPage"]:
                    break
            else:
                break

        return repo_names

    async def check_org_repos(self, org: str, repo_type: str) -> list | None:
        """Return non-archived org repos.

        For ``public`` repos a fast GraphQL parallel ingest is used; for
        every other type the REST listing is paginated.
        """
        if repo_type not in [
            "all",
            "public",
            "private",
            "forks",
            "sources",
            "member",
            "internal",
        ]:
            raise ValueError("Unsupported type!")
        repos: list = []

        org_details = await self._base.call_get(f"/orgs/{org}")
        if org_details.status_code == 200 and repo_type == "public":
            repo_count = org_details.json()["public_repos"]
            pub_repos = await DataIngestor.perform_parallel_repo_ingest(
                self._base, org, repo_count
            )
            repos.extend([repo for repo in pub_repos if not repo["archived"]])
            return repos

        get_params: dict = {"type": repo_type, "per_page": 100, "page": 1}

        org_repos = await self._base.call_get(f"/orgs/{org}/repos", params=get_params)

        if org_repos.status_code == 200:
            listing = org_repos.json()

            repos.extend([repo for repo in listing if not repo["archived"]])
            while len(listing) == 100:
                get_params["page"] += 1
                org_repos = await self._base.call_get(
                    f"/orgs/{org}/repos", params=get_params
                )
                if org_repos.status_code == 200:
                    listing = org_repos.json()
                    repos.extend([repo for repo in listing if not repo["archived"]])
        else:
            logger.info(f"[-] {org} requires SSO!")
            return None

        return repos

    async def get_org_secrets(self, org_name: str) -> list[dict]:
        """List all org-level Actions secrets (with selected-repo expansion)."""
        secrets: list[dict] = []
        page = 1
        per_page = 100

        while True:
            params = {"page": page, "per_page": per_page}
            resp = await self._base.call_get(
                f"/orgs/{org_name}/actions/secrets", params=params
            )
            if resp.status_code == 200:
                secrets_response = resp.json()
                page_secrets = secrets_response["secrets"]
                if not page_secrets:
                    break

                for secret in page_secrets:
                    if secret["visibility"] == "selected":
                        repos_resp = await self._base.call_get(
                            f"/orgs/{org_name}/actions/secrets/"
                            f"{secret['name']}/repositories"
                        )

                        if repos_resp.status_code == 200:
                            repos_json = repos_resp.json()
                            repo_names = [
                                repo["full_name"] for repo in repos_json["repositories"]
                            ]

                        secret["repos"] = repo_names

                    secrets.append(secret)

                if len(page_secrets) < per_page:
                    break
                page += 1
            else:
                break

        return secrets
