"""
MCP server for Gato-X enumeration using FastMCP.
Exposes all enumerate functionality as LLM-friendly tools.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from gatox.cli.output import Output
from gatox.enumerate.enumerate import Enumerator
from gatox.github.app_session import AppSession

app = FastMCP(
    name="Gato-X MCP Server",
    instructions="MCP server exposing Gato-X GitHub enumeration tools.",
)


class MCPAuthParams(BaseModel):
    """
    Authentication and proxy options for GitHub enumeration.

    Authenticate either with a GitHub Personal Access Token via the GH_TOKEN
    environment variable, or as a GitHub App via GH_APP_ID and GH_APP_KEY
    (App credentials take precedence, and Gato-X renews their tokens itself).
    SOCKS and HTTP proxies are mutually exclusive.
    """

    socks_proxy: str | None = Field(
        None, description="SOCKS proxy in HOST:PORT format (optional)"
    )
    http_proxy: str | None = Field(
        None, description="HTTP proxy in HOST:PORT format (optional)"
    )
    github_url: str | None = Field(
        None,
        description="Custom GitHub API URL (optional, defaults to https://api.github.com)",
    )
    skip_runners: bool | None = Field(
        True,
        description="If true, skips runner enumeration via run-log analysis for speed, but may miss self-hosted runners for non-admin users.",
    )
    ignore_workflow_run: bool | None = Field(
        False,
        description="If true, ignores the workflow_run trigger when enumerating repositories. Useful if the org requires approval for all fork PRs.",
    )

    @field_validator("socks_proxy")
    @classmethod
    def validate_proxies(cls, v, values):
        if v and values.get("http_proxy"):
            raise ValueError("Cannot use both SOCKS and HTTP proxy at the same time.")
        return v

    app_id: str | None = Field(
        None,
        description="GitHub App ID to authenticate as (optional, defaults to GH_APP_ID)",
    )
    app_key: str | None = Field(
        None,
        description=(
            "Path to the GitHub App private key PEM file, or the PEM itself "
            "(optional, defaults to GH_APP_KEY)"
        ),
    )
    installation_id: str | None = Field(
        None,
        description=(
            "GitHub App installation to use instead of resolving one from the "
            "target (optional, defaults to GH_APP_INSTALLATION_ID)"
        ),
    )

    @property
    def app_credentials(self) -> tuple[str, str] | None:
        """Return ``(app_id, private_key)`` when App auth is configured."""
        app_id = self.app_id or os.environ.get("GH_APP_ID")
        app_key = self.app_key or os.environ.get("GH_APP_KEY")
        if app_id and app_key:
            return (app_id, app_key)
        return None

    @property
    def resolved_installation_id(self) -> str | None:
        """Explicitly requested App installation, if any."""
        return self.installation_id or os.environ.get("GH_APP_INSTALLATION_ID")

    @property
    def pat(self) -> str:
        pat = os.environ.get("GH_TOKEN")
        if not pat:
            raise ValueError(
                "No 'GH_TOKEN' environment variable set! Provide a GitHub PAT via "
                "GH_TOKEN, or GitHub App credentials via GH_APP_ID and GH_APP_KEY."
            )
        return pat


class EnumerateOrganizationInput(MCPAuthParams):
    """
    Enumerate a GitHub organization for self-hosted runner abuse, workflow security, and repository security posture. Returns detailed findings, recommendations, and a summary of all non-archived repositories in the organization. The organization name is required.
    """

    target: str = Field(
        ..., description="Organization name to enumerate (e.g., 'octocat')"
    )


class EnumerateRepositoryInput(MCPAuthParams):
    """
    Enumerate a single repository in org/repo format for self-hosted runners, workflow security, and secrets exposure. Returns findings, secrets, runner info, and attack recommendations for the repository.
    """

    repository: str = Field(
        ...,
        description="Repository in 'owner/repo' format to enumerate (e.g., 'octocat/Hello-World')",
    )
    single_commit: str | None = Field(
        None,
        description="Scan a single commit SHA (40 hex chars). Only compatible with single repository enumeration. Assumes commit is latest on default branch.",
    )


class EnumerateRepositoriesInput(MCPAuthParams):
    """
    Enumerate a list of repositories for self-hosted runners, workflow security, and secrets exposure. Returns findings and recommendations for each repository. Provide a list of repositories in 'owner/repo' format.
    """

    repositories: list[str] = Field(
        ...,
        description="List of repositories in 'owner/repo' format (e.g., ['octocat/Hello-World', ...])",
    )


class SelfEnumerationInput(MCPAuthParams):
    """
    Enumerate all organizations and repositories accessible to the authenticated user (the PAT owner). Returns a summary of organizations and repositories, including security findings and recommendations.
    """

    pass


class ValidatePATInput(MCPAuthParams):
    """
    Validate the provided GitHub PAT for enumeration access and print organization memberships. Returns a list of organizations if the PAT is valid, or an error message if not.
    """

    pass


# --- Tool Implementations ---


def get_enumerator(params: MCPAuthParams):
    """Build a PAT-authenticated enumerator.

    Kept for callers that only ever use a PAT; App authentication needs the
    async :func:`enumerator_for` because resolving an installation and
    minting its token are network calls.
    """
    Output(False, suppress=True)  # Suppress other stdout
    return Enumerator(
        pat=params.pat,
        socks_proxy=params.socks_proxy,
        http_proxy=params.http_proxy,
        skip_log=params.skip_runners or False,
        github_url=params.github_url,
        ignore_workflow_run=params.ignore_workflow_run or False,
    )


@asynccontextmanager
async def enumerator_for(params: MCPAuthParams, target: str | None = None):
    """Yield an enumerator authenticated by PAT or GitHub App.

    With App credentials the installation token renews itself for as long as
    the context is open, so an enumeration is not capped at one hour.

    Args:
        params: Auth and proxy options.
        target: Org, ``owner/repo`` or user used to resolve an App
            installation. Unused for PAT auth.
    """
    Output(False, suppress=True)  # Suppress other stdout

    session = None
    api_client = None
    app_permissions = None
    credentials = params.app_credentials

    if credentials:
        app_id, app_key = credentials
        session = AppSession(
            app_id,
            app_key,
            socks_proxy=params.socks_proxy,
            http_proxy=params.http_proxy,
            github_url=params.github_url,
        )
        await session.validate()

        installation_id = params.resolved_installation_id
        if installation_id:
            api_client = await session.api_for_installation(installation_id)
        elif target:
            api_client = await session.api_for_target(target)
        else:
            await session.close()
            raise ValueError(
                "GitHub App authentication needs a target to resolve an "
                "installation from; set GH_APP_INSTALLATION_ID for this tool."
            )
        app_permissions = session.app_permissions

    try:
        yield Enumerator(
            pat=None if api_client else params.pat,
            socks_proxy=params.socks_proxy,
            http_proxy=params.http_proxy,
            skip_log=params.skip_runners or False,
            github_url=params.github_url,
            ignore_workflow_run=params.ignore_workflow_run or False,
            finegrained_permisions=(set(app_permissions) if app_permissions else None),
            api_client=api_client,
        )
    finally:
        if session:
            await session.close()


@app.tool()
async def enumerate_organization(ctx: Context, params: EnumerateOrganizationInput):
    """
    Enumerate a GitHub organization for self-hosted runner abuse, workflow security, and repository security posture.

    This tool performs a comprehensive security enumeration of a specified GitHub organization. It analyzes all non-archived repositories within the organization for the following:
    - Self-hosted runner abuse potential
    - Workflow security issues (including Pwn Request and injection vulnerabilities)
    - Repository security posture and misconfigurations
    - Attack surface and actionable recommendations

    **Inputs:**
    - `target` (str, required): The organization name to enumerate (e.g., 'octocat').
    - All authentication and proxy options are inherited from MCPAuthParams (see class docstring for details).

    **Authentication:**
    - The GitHub Personal Access Token (PAT) must be provided via the `GH_TOKEN` environment variable.

    **Proxy/Advanced Options:**
    - See MCPAuthParams for SOCKS/HTTP proxy, custom GitHub API URL, and advanced scan options.

    **Returns:**
    - A dictionary (from `toJSON()`) with detailed findings, recommendations, and a summary of all non-archived repositories in the organization.

    **Typical Use:**
    - Use this tool to perform a deep security review of an entire GitHub organization, including all its repositories, for CI/CD and workflow-related risks.
    """
    with open("mcp_server_debug.log", "a") as debug_log:
        debug_log.write(f"enumerate_organization called with params: {params}\n")
    await ctx.info(f"Enumerating organization: {params.target}")
    try:
        async with enumerator_for(params, params.target) as enumerator:
            org = await enumerator.enumerate_organization(params.target)
        await ctx.info(f"Enumeration complete for org: {params.target}")
        return org.toJSON() if org else {"error": "Enumeration failed"}
    except Exception as e:
        await ctx.error(f"Failed to enumerate organization {params.target}: {e}")
        return {"error": str(e), "details": getattr(e, "args", [])}


@app.tool()
async def enumerate_repository(ctx: Context, params: EnumerateRepositoryInput):
    """
    Enumerate a single GitHub repository for self-hosted runners, workflow security, and secrets exposure.

    This tool analyzes a single repository in 'owner/repo' format. It checks for self-hosted runners, workflow security issues, and secrets exposure. It can also scan a specific commit if provided.

    **Inputs:**
    - `repository` (str, required): The repository to enumerate (e.g., 'octocat/Hello-World').
    - All authentication and proxy options are inherited from MCPAuthParams.

    **Authentication:**
    - The GitHub PAT must be provided via the `GH_TOKEN` environment variable.

    **Proxy/Advanced Options:**
    - See MCPAuthParams for details.

    **Returns:**
    - A dictionary (from `toJSON()`) with findings, secrets, runner info, and attack recommendations for the repository.

    **Typical Use:**
    - Use this tool to perform a deep security review of a single repository, or to scan a specific commit for security issues.
    """
    await ctx.info(f"Enumerating repository: {params.repository}")
    try:
        async with enumerator_for(params, params.repository) as enumerator:
            repo = await enumerator.enumerate_repo(params.repository)
        await ctx.info(f"Enumeration complete for repo: {params.repository}")
        return repo.toJSON() if repo else {"error": "Enumeration failed"}
    except Exception as e:
        await ctx.error(f"Failed to enumerate repository {params.repository}: {e}")
        return {"error": str(e), "details": getattr(e, "args", [])}


@app.tool()
async def enumerate_repositories(ctx: Context, params: EnumerateRepositoriesInput):
    """
    Enumerate a list of GitHub repositories for self-hosted runners, workflow security, and secrets exposure.

    This tool takes a list of repositories in 'owner/repo' format and performs security enumeration on each. It is ideal for batch analysis or for organizations with many repositories.

    **Inputs:**
    - `repositories` (List[str], required): List of repositories to enumerate (e.g., ['octocat/Hello-World', ...]).
    - All authentication and proxy options are inherited from MCPAuthParams.

    **Authentication:**
    - The GitHub PAT must be provided via the `GH_TOKEN` environment variable.

    **Proxy/Advanced Options:**
    - See MCPAuthParams for details.

    **Returns:**
    - A list of dictionaries (from `toJSON()`) with findings and recommendations for each repository.

    **Typical Use:**
    - Use this tool to enumerate multiple repositories at once, such as all repos in a file or list.
    """
    await ctx.info(f"Enumerating repositories: {params.repositories}")
    try:
        target = params.repositories[0] if params.repositories else None
        async with enumerator_for(params, target) as enumerator:
            repos = await enumerator.enumerate_repos(params.repositories)
        await ctx.info(f"Enumeration complete for repos: {params.repositories}")
        return [r.toJSON() for r in repos]
    except Exception as e:
        await ctx.error(f"Failed to enumerate repositories {params.repositories}: {e}")
        return {"error": str(e), "details": getattr(e, "args", [])}


@app.tool()
async def self_enumeration(ctx: Context, params: SelfEnumerationInput):
    """
    Enumerate all organizations and repositories accessible to the authenticated user (the PAT owner).

    This tool enumerates all organizations and repositories that the authenticated user (PAT owner) has access to. It is useful for understanding the full scope of access and potential attack surface for a given token.

    **Inputs:**
    - All authentication and proxy options are inherited from MCPAuthParams.

    **Authentication:**
    - The GitHub PAT must be provided via the `GH_TOKEN` environment variable.

    **Proxy/Advanced Options:**
    - See MCPAuthParams for details.

    **Returns:**
    - A dictionary with two keys: 'organizations' (list of orgs) and 'repositories' (list of repos), each as dictionaries from `toJSON()`.

    **Typical Use:**
    - Use this tool to enumerate everything the current token can access, for access reviews or attack surface mapping.
    """
    await ctx.info("Performing self-enumeration for authenticated user")
    try:
        async with enumerator_for(params) as enumerator:
            result = await enumerator.self_enumeration()
        if not result:
            return {"error": "Self-enumeration failed"}
        orgs, repos = result
        await ctx.info("Self-enumeration complete")
        return {
            "organizations": [o.toJSON() for o in orgs],
            "repositories": [r.toJSON() for r in repos],
        }
    except Exception as e:
        await ctx.error(f"Failed to perform self-enumeration: {e}")
        return {"error": str(e), "details": getattr(e, "args", [])}


@app.tool()
async def validate_pat(ctx: Context, params: ValidatePATInput):
    """
    Validate the provided GitHub PAT for enumeration access and print organization memberships.

    This tool checks if the provided GitHub PAT (from `GH_TOKEN`) is valid for enumeration and lists the organizations the token has access to. It is useful for validating credentials before running more expensive enumeration operations.

    **Inputs:**
    - All authentication and proxy options are inherited from MCPAuthParams.

    **Authentication:**
    - The GitHub PAT must be provided via the `GH_TOKEN` environment variable.

    **Returns:**
    - A list of organizations (from `toJSON()`) if the PAT is valid, or an error message if not.

    **Typical Use:**
    - Use this tool to check if a PAT is valid and to see which organizations it can enumerate.
    """
    await ctx.info("Validating GitHub PAT")
    try:
        async with enumerator_for(params) as enumerator:
            orgs = await enumerator.validate_only()
        await ctx.info("PAT validation complete")
        return [org.toJSON() for org in orgs] if orgs else []
    except Exception as e:
        await ctx.error(f"Failed to validate PAT: {e}")
        return {"error": str(e), "details": getattr(e, "args", [])}


async def main():
    await app.run_async(transport="stdio")


def entry():
    return asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
