"""Shared HTTP infrastructure for the GitHub :class:`Api` and its sub-APIs.

The original ``Api`` class was a 1900+ line monolith. To make things
testable and easier to navigate it has been split into one orchestrator
class plus a handful of grouped sub-APIs (see ``repo_api``, ``org_api``,
``commit_api``, ``user_api``, ``action_api``, ``app_api``).

Everything in this module is the *shared* state and behaviour that every
sub-API needs:

* the configured :class:`httpx.AsyncClient`,
* the request headers / proxy / SSL setup,
* the rate-limit guard,
* the small set of binary-log / raw-file helpers,
* the typed ``call_get`` / ``call_post`` / ... HTTP wrappers, and
* the ``__aenter__`` / ``__aexit__`` / ``close`` lifecycle.

``Api`` itself extends :class:`ApiBase`. Sub-APIs hold a reference to the
shared ``ApiBase`` instance via their constructor and reach the wire by
calling ``self._base.call_get(...)`` etc. There is exactly one HTTP
client per ``Api`` orchestrator, regardless of how many sub-APIs are
constructed.
"""

from __future__ import annotations

import asyncio
import copy
import io
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from gatox.cli.output import Output
from gatox.github.credentials import (
    AppAuthError,
    CredentialProvider,
    StaticTokenProvider,
)

if TYPE_CHECKING:
    from gatox.github.action_api import ActionApi
    from gatox.github.app_api import AppApi
    from gatox.github.commit_api import CommitApi
    from gatox.github.org_api import OrgApi
    from gatox.github.repo_api import RepoApi
    from gatox.github.user_api import UserApi

logger = logging.getLogger(__name__)


#: GitHub Enterprise Cloud with data residency hands every enterprise a
#: dedicated subdomain of this zone. The web interface lives at
#: ``SUBDOMAIN.ghe.com`` while the API lives at ``api.SUBDOMAIN.ghe.com``.
GHE_COM_SUFFIX = ".ghe.com"

PUBLIC_REST_BASE = "https://api.github.com"
PUBLIC_GRAPHQL_URL = "https://api.github.com/graphql"


def resolve_api_endpoints(github_url: str | None) -> tuple[str, str, str | None]:
    """Derive the REST and GraphQL endpoints from whatever URL the operator has.

    An operator should be able to paste the URL they see in the browser and
    have it work. The three deployment shapes put their API in three
    different places, and none of them is "the browser URL":

    ======================  ==========================  ===========================
    Browser URL             REST base                   GraphQL endpoint
    ======================  ==========================  ===========================
    ``github.com``          ``api.github.com``          ``api.github.com/graphql``
    ``SUB.ghe.com``         ``api.SUB.ghe.com``         ``api.SUB.ghe.com/graphql``
    ``ghes.corp.example``   ``ghes.corp.example``       ``ghes.corp.example``
                            ``/api/v3``                 ``/api/graphql``
    ======================  ==========================  ===========================

    Note that GHES puts REST under ``/api/v3`` but GraphQL under
    ``/api/graphql`` -- ``/api/v3/graphql`` is not a route, so the two cannot
    be derived from one another by suffixing.

    Refs:
      https://docs.github.com/en/enterprise-cloud@latest/admin/data-residency/getting-started-with-data-residency-for-github-enterprise-cloud
      https://docs.github.com/en/enterprise-server@latest/rest/quickstart
      https://docs.github.com/en/enterprise-server@latest/graphql/guides/forming-calls-with-graphql

    Args:
        github_url: Anything from a bare hostname to a full REST base. An
            empty value selects public GitHub.

    Returns:
        ``(rest_base, graphql_url, rewritten_from)``. ``rewritten_from`` is
        the normalised input when it did not already name the REST base, so
        the caller can tell the operator what was derived; ``None`` when the
        input was already correct.

    Raises:
        ValueError: If no hostname can be parsed out of ``github_url``.
    """
    raw = (github_url or "").strip()
    if not raw:
        return PUBLIC_REST_BASE, PUBLIC_GRAPHQL_URL, None

    # Accept "octocorp.ghe.com" as readily as "https://octocorp.ghe.com".
    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(
            f"Could not parse a hostname out of the API URL: {github_url!r}"
        )

    scheme = parsed.scheme or "https"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")

    if hostname in ("github.com", "www.github.com", "api.github.com"):
        rest, graphql = PUBLIC_REST_BASE, PUBLIC_GRAPHQL_URL
    elif hostname.endswith(GHE_COM_SUFFIX) or hostname.startswith("api."):
        # Data residency subdomains -- and api.* hosts generally -- serve REST
        # from the host root. The GHES /api/v3 suffix 404s on most routes
        # there, so it is dropped rather than honoured.
        api_host = hostname if hostname.startswith("api.") else f"api.{hostname}"
        rest = f"{scheme}://{api_host}{port}"
        graphql = f"{rest}/graphql"
    else:
        # Anything else is assumed to be GitHub Enterprise Server.
        root = f"{scheme}://{hostname}{port}"
        rest = f"{root}/api/v3"
        graphql = f"{root}/api/graphql"

    supplied = f"{scheme}://{hostname}{port}{path}"
    return rest, graphql, None if supplied == rest else supplied


class ApiBase:
    """Shared HTTP / rate-limit infrastructure used by every sub-API.

    This is a deliberate separation: ``ApiBase`` knows nothing about
    repos, orgs, commits, runs, etc. It only owns the plumbing every
    sub-API leans on. Sub-API classes take an :class:`ApiBase` instance
    via their constructor and use it to make wire calls.
    """

    RUNNER_RE = re.compile(r"Runner name: \'([\w+-.]+)\'")
    MACHINE_RE = re.compile(r"Machine name: \'([\w+-.]+)\'")
    RUNNERGROUP_RE = re.compile(r"Runner group name: \'([\w+-.]+)\'")
    RUNNERTYPE_RE = re.compile(r"([\w+-.]+)")

    RUN_THRESHOLD = 90

    # Forward declarations for the sub-API attributes that ``Api.__init__``
    # populates. Declaring them here lets sub-API code refer to sibling
    # sub-APIs (``self._base.repo.get_repository(...)``) and keeps pyright
    # happy without introducing a runtime import cycle.
    repo: RepoApi
    org: OrgApi
    user: UserApi
    commit: CommitApi
    action: ActionApi
    app: AppApi

    def __init__(
        self,
        pat: str | None = None,
        version: str = "2022-11-28",
        http_proxy: str | None = None,
        socks_proxy: str | None = None,
        github_url: str | None = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
        app_permissions: list | None = None,
        credentials: CredentialProvider | None = None,
    ) -> None:
        """Initialise the shared HTTP infrastructure.

        Args:
            pat: GitHub personal access token used for API calls.
            version: API version sent in ``X-GitHub-Api-Version``.
            http_proxy: Optional ``host:port`` for an HTTP proxy.
            socks_proxy: Optional ``host:port`` for a SOCKS5 proxy.
            github_url: Base URL for the GitHub API. Anything other than
                the public endpoint disables certificate verification (we
                are typically pointed at a GitHub Enterprise instance or
                a local intercepting proxy).
            client: Optional pre-built async client (used by the unit
                tests so that they can inject a mock transport).
            app_permissions: Optional permissions list for GitHub App
                tokens (purely informational, surfaced for callers).
            credentials: Optional credential provider. When supplied it
                replaces ``pat`` and is consulted before every request,
                which is how GitHub App tokens are renewed mid-run.
        """
        if credentials is None:
            if not pat:
                raise ValueError("A valid GitHub token must be provided!")
            credentials = StaticTokenProvider(pat)

        self.credentials = credentials
        #: Last token the provider handed out. Kept as a plain attribute
        #: because ``is_app_token`` and the CLI's token checks read it.
        self.pat = pat or ""
        self.transport: str | None = None
        self.verify_ssl = True
        self.headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": version,
        }
        if self.pat:
            # Preserved so callers that inspect ``headers`` still see auth.
            # The value actually sent is resolved per request in _auth_headers.
            self.headers["Authorization"] = f"Bearer {self.pat}"
        self.github_url, self.graphql_url, rewritten_from = resolve_api_endpoints(
            github_url
        )
        if rewritten_from:
            # The operator pasted the web URL (or a bare hostname). Say what we
            # derived so a wrong guess is debuggable from the console alone.
            Output.info(
                f"Resolved {Output.bright(rewritten_from)} to the REST API at "
                f"{Output.bright(self.github_url)} and the GraphQL API at "
                f"{Output.bright(self.graphql_url)}."
            )

        self.is_public_github = self.github_url == PUBLIC_REST_BASE

        # GitHub operated hosts always present a publicly trusted certificate,
        # so there is no reason to send the PAT over an unverified connection.
        # GHES is commonly fronted by a private CA, where we do relax it.
        api_hostname = urlparse(self.github_url).hostname or ""
        self.is_github_cloud = self.is_public_github or api_hostname.endswith(
            GHE_COM_SUFFIX
        )

        if http_proxy and socks_proxy:
            raise ValueError(
                "A SOCKS & HTTP proxy cannot be used at the same "
                "time! Please pass only one!"
            )

        if http_proxy:
            # We are likely using BURP, so disable SSL.
            self.verify_ssl = False
            self.transport = f"http://{http_proxy}"
        elif socks_proxy:
            self.transport = f"socks5://{socks_proxy}"

        if not self.is_github_cloud:
            self.verify_ssl = False

        if client:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                headers=self.headers,
                http2=True,
                proxy=self.transport,
                verify=self.verify_ssl,
                follow_redirects=True,
                timeout=30.0,
            )
        self.app_permissions = app_permissions

    # ---------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self) -> None:
        """Close the underlying async client."""
        await self.client.aclose()

    def is_app_token(self) -> bool:
        """Return ``True`` if the configured token is a GitHub App token."""
        return self.pat.startswith("ghs_")

    @staticmethod
    def describe_failure(response: httpx.Response) -> str:
        """Return the API's own explanation for a rejected request.

        GitHub puts the operator-actionable reason in the response body: the
        IP allow list message, the SAML SSO message, the bad-credentials
        message. Falls back to describing the body when it is not the JSON
        error object we expect -- an HTML body in particular is the signature
        of having reached the web interface instead of the API.
        """
        try:
            body = response.json()
        except Exception:
            body = None

        if isinstance(body, dict):
            parts = []
            if body.get("message"):
                parts.append(str(body["message"]))
            for err in body.get("errors") or []:
                if isinstance(err, dict) and err.get("message"):
                    parts.append(str(err["message"]))
                elif isinstance(err, str):
                    parts.append(err)
            if body.get("documentation_url"):
                parts.append(f"See {body['documentation_url']}")
            if parts:
                return " | ".join(parts)

        text = response.text.strip()
        if text.startswith(("<", "\ufeff<")):
            return (
                "the server returned HTML rather than a JSON API response, which "
                "usually means the URL points at the web interface instead of the "
                "API endpoint"
            )
        if text:
            return text[:200]
        return "no error detail was returned"

    def warn_failure(self, response: httpx.Response, subject: str) -> None:
        """Surface a rejected request along with the API's own explanation.

        Callers that fall back to an empty result must use this: without it a
        403 from an IP allow list or SSO enforcement is indistinguishable from
        a genuinely empty response.
        """
        Output.warn(
            f"Failed to query {subject}: "
            f"{Output.bright(str(response.request.url))} returned "
            f"{Output.bright(str(response.status_code))} - "
            f"{self.describe_failure(response)}"
        )
        logger.warning(
            f"Request for {subject} failed with {response.status_code}: "
            f"{response.text[:512]}"
        )

    # ---------------------------------------------------------------
    # HTTP wrappers
    # ---------------------------------------------------------------
    def _build_url(self, url: str) -> str:
        """Join a path onto the configured API base.

        Absolute URLs (the API hands them back for artifact / log downloads)
        are used as-is. ``/graphql`` is resolved separately because it does not
        live under the REST base on GitHub Enterprise Server.
        """
        if url.startswith(("http://", "https://")):
            return url
        if url == "/graphql":
            return self.graphql_url
        return self.github_url + url

    async def _auth_headers(self, strip_auth: bool = False) -> dict[str, str]:
        """Build request headers carrying a token that is valid right now.

        The token is resolved per request rather than captured at
        construction, so a renewal by the credential provider takes effect
        on the very next call without call sites knowing about it.
        """
        headers = copy.deepcopy(self.headers)
        if strip_auth:
            headers.pop("Authorization", None)
            return headers

        token = await self.credentials.get_token()
        if token != self.pat:
            self.pat = token
        headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _send(self, method: str, request_url: str, **kwargs) -> httpx.Response:
        """Send a request, renewing the credential once if it is rejected.

        A 401 on a renewable credential is almost always an expiry that beat
        the refresh margin -- a revoked installation, or clock skew. One
        forced renewal and one retry covers it. A second 401 is returned as
        it is, so the caller sees GitHub's own explanation rather than us
        looping on a credential that is genuinely bad.
        """
        headers = await self._auth_headers()
        send = getattr(self.client, method)
        response = await send(request_url, headers=headers, **kwargs)

        if response.status_code == 401 and self.credentials.is_refreshable:
            logger.debug(
                "Got 401 for %s; renewing credential and retrying once", request_url
            )
            token = await self.credentials.refresh(stale_token=self.pat)
            self.pat = token
            headers["Authorization"] = f"Bearer {token}"
            response = await send(request_url, headers=headers, **kwargs)

        return response

    async def call_get(
        self, url: str, params: dict | None = None, strip_auth: bool = False
    ) -> httpx.Response:
        """Issue a GET request relative to ``github_url``.

        Args:
            url: Path portion of the URL (joined onto ``github_url``).
            params: Query parameters.
            strip_auth: If ``True``, drop the ``Authorization`` header
                (used for unauthenticated raw-content fetches).

        Returns:
            The :class:`httpx.Response` from the underlying client.
        """
        request_url = self._build_url(url)

        api_response: httpx.Response | None = None
        for _ in range(0, 5):
            try:
                logger.debug(f"Making GET API request to {request_url}!")
                if strip_auth:
                    api_response = await self.client.get(
                        request_url,
                        params=params,
                        headers=await self._auth_headers(strip_auth=True),
                    )
                else:
                    api_response = await self._send("get", request_url, params=params)
                break
            except AppAuthError:
                # A bad App ID or unusable private key is not a transport
                # problem. Retrying it five times just buries the real
                # explanation under a generic "failed after 5 attempts".
                raise
            except Exception as e:
                logger.warning(
                    f"GET request {request_url} failed due to transport error re-trying",
                    exc_info=e,
                )
                continue

        if api_response is None:
            raise Exception(f"GET request {request_url} failed after 5 attempts")

        if not strip_auth:
            await self._check_rate_limit(api_response.headers)

        return api_response

    async def call_post(self, url: str, params: dict | None = None) -> httpx.Response:
        """Issue a POST request relative to ``github_url`` with a JSON body."""
        request_url = self._build_url(url)
        logger.debug(f"Making POST API request to {request_url}!")

        api_response = await self._send("post", request_url, json=params, timeout=30)
        logger.debug(
            f"The POST request to {request_url} returned a {api_response.status_code}!"
        )

        await self._check_rate_limit(api_response.headers)

        return api_response

    async def call_patch(self, url: str, params: dict | None = None) -> httpx.Response:
        """Issue a PATCH request relative to ``github_url`` with a JSON body."""
        request_url = self._build_url(url)
        logger.debug(f"Making PATCH API request to {request_url}!")

        api_response = await self._send("patch", request_url, json=params)
        logger.debug(
            f"The PATCH request to {request_url} returned a {api_response.status_code}!"
        )

        await self._check_rate_limit(api_response.headers)

        return api_response

    async def call_put(self, url: str, params: dict | None = None) -> httpx.Response:
        """Issue a PUT request relative to ``github_url`` with a JSON body."""
        request_url = self._build_url(url)
        logger.debug(f"Making PUT API request to {request_url}!")

        api_response = await self._send("put", request_url, json=params)

        await self._check_rate_limit(api_response.headers)

        return api_response

    async def call_delete(self, url: str) -> httpx.Response:
        """Issue a DELETE request relative to ``github_url``."""
        request_url = self._build_url(url)
        logger.debug(f"Making DELETE API request to {request_url}!")

        api_response = await self._send("delete", request_url)
        logger.debug(
            f"The POST request to {request_url} returned a {api_response.status_code}!"
        )

        await self._check_rate_limit(api_response.headers)

        return api_response

    # ---------------------------------------------------------------
    # Internal helpers (single underscore so sub-APIs can call them)
    # ---------------------------------------------------------------
    async def _check_rate_limit(self, headers) -> None:
        """Sleep until the rate-limit window resets if we are running low.

        The trigger is intentionally conservative: when fewer than 5% of
        the bucket remains we pause execution until the documented reset
        time. Yes, printing from API code is unusual; the alternative is
        propagating a rate-limit exception out of every call site which
        is much worse.
        """
        if (
            "X-Ratelimit-Remaining" in headers
            and int(headers["X-Ratelimit-Remaining"])
            < int(headers["X-RateLimit-Limit"]) // 20
            and headers["X-Ratelimit-Resource"] == "core"
        ):
            gh_date = headers["Date"]
            reset_utc = int(headers["X-Ratelimit-Reset"])
            date = datetime.strptime(gh_date, "%a, %d %b %Y %H:%M:%S %Z")
            date = date.replace(tzinfo=timezone.utc)
            reset_time = date.fromtimestamp(reset_utc, tz=timezone.utc)

            sleep_time = (reset_time - date).seconds
            sleep_time_mins = str(sleep_time // 60)

            Output.warn(
                f"Sleeping for {Output.bright(sleep_time_mins + ' minutes')} "
                "to prevent rate limit exhaustion!"
            )

            await asyncio.sleep(sleep_time + 1)

    @staticmethod
    def _verify_result(response: httpx.Response, expected_code: int) -> bool:
        """Log + return ``False`` if ``response`` does not carry the expected status."""
        if response.status_code != expected_code:
            logger.warning(
                f"Expected status code {expected_code}, but got {response.status_code}!"
            )
            logger.debug(response.text)
            return False
        return True

    async def _process_run_log(self, log_content: bytes, run_info: dict) -> dict | None:
        """Parse a workflow-run log archive and extract runner metadata.

        The zip uploaded by GitHub contains numbered text files; the
        first ``[0-9]_*`` entry holds the setup output, which is where
        the runner name / machine / labels / token permissions appear.
        """
        log_package: dict = {}
        token_permissions: dict = {}
        runner_type = None
        non_ephemeral = False
        labels: list = []
        runner_name = None
        machine_name = None
        runner_group = None

        with zipfile.ZipFile(io.BytesIO(log_content)) as runres:
            for zipinfo in runres.infolist():
                # Match both directory-format (jobname/1_Set up job.txt)
                # and flat-format (0_jobname.txt) setup files.
                is_setup_file = (
                    zipinfo.filename.endswith("/1_Set up job.txt")
                    or zipinfo.filename == "1_Set up job.txt"
                    or (
                        re.match(r"[0-9]+_.*\.txt$", zipinfo.filename)
                        and "/" not in zipinfo.filename
                    )
                )
                if is_setup_file:
                    with runres.open(zipinfo) as run_setup:
                        content = run_setup.read().decode("utf-8-sig")
                        content_lines = content.splitlines()
                        if (
                            "Image Release: https://github.com/actions/runner-images"
                            in content
                            or "Job is about to start running on the hosted runner:"
                            in content
                            or "Runner Image Provisioner" in content
                        ) and "1ES.Pool" not in content:
                            # Larger runners will appear to be self-hosted, but
                            # they will have the image name. Skip if we see this.
                            # If the log contains "job is about to start running on hosted runner",
                            # or "Runner Image Provisioner", the runner is a GitHub hosted runner
                            # so we can skip it.
                            continue
                        elif (
                            "Self-hosted runners in the repository are disabled"
                            in content
                        ):
                            break
                        index = 0
                        while index < len(content_lines):
                            line = content_lines[index]
                            if not line:
                                index += 1
                                continue

                            if "Requested labels: " in line:
                                labels = [
                                    lbl.strip()
                                    for lbl in line.split("Requested labels: ")[1]
                                    .strip()
                                    .split(", ")
                                ]

                            if "Runner name: " in line:
                                runner_name = (
                                    line.split("Runner name: ")[1]
                                    .replace("'", "")
                                    .strip()
                                )

                            if "Machine name: " in line:
                                machine_name = (
                                    line.split("Machine name: ")[1]
                                    .replace("'", "")
                                    .strip()
                                )

                            if "Runner group name:" in line:
                                runner_group = (
                                    line.split("Runner group name: ")[1]
                                    .replace("'", "")
                                    .strip()
                                )

                            if "Job is about to start running on" in line:
                                runner_type = line.split()[-1].strip()
                                matches = ApiBase.RUNNERTYPE_RE.search(runner_type)
                                if matches:
                                    runner_type = matches.group(1)

                            if "GITHUB_TOKEN Permission" in line:
                                while "[endgroup]" not in content_lines[index + 1]:
                                    index += 1
                                    scope = (
                                        content_lines[index].split()[1].replace(":", "")
                                    )
                                    permission = content_lines[index].split()[2]
                                    token_permissions[scope] = permission
                                log_package["token_permissions"] = token_permissions

                            if "Cleaning the repository" in line:
                                non_ephemeral = True
                            log_package["non_ephemeral"] = non_ephemeral

                            index += 1

                        # No runner name → we picked up a pending workflow.
                        if not runner_name:
                            continue

                        log_package = {
                            "requested_labels": labels,
                            "runner_name": runner_name,
                            "machine_name": machine_name,
                            "runner_group": runner_group,
                            "runner_type": runner_type,
                            "run_id": run_info["id"],
                            "run_attempt": run_info["run_attempt"],
                            "non_ephemeral": non_ephemeral,
                            "token_permissions": token_permissions,
                        }

                    return log_package
        return None

    async def _get_full_runlog(self, log_content: bytes, run_name: str) -> str | None:
        """Return the full text of a run log entry matching ``run_name``.

        Handles both flat format (0_jobname.txt) and directory format
        (jobname/system.txt or jobname/*.txt).
        """
        with zipfile.ZipFile(io.BytesIO(log_content)) as runres:
            for zipinfo in runres.infolist():
                # Match flat format: 0_jobname.txt or 1_jobname.txt
                # or directory format: jobname/system.txt containing the run log
                if (
                    f"_{run_name}" in zipinfo.filename
                    or f"{run_name}/" in zipinfo.filename
                ):
                    # In directory format, prefer the concatenated flat file
                    # (0_run_name) if it exists, otherwise any file inside the
                    # job directory works.
                    if (
                        f"0_{run_name}" in zipinfo.filename
                        or f"{run_name}/" in zipinfo.filename
                    ):
                        with runres.open(zipinfo) as run_log:
                            content = run_log.read().decode("utf-8-sig")
                            return content
        return None

    async def _get_raw_file(self, repo: str, file_path: str, ref: str) -> str | None:
        """Fetch a raw file directly from ``raw.githubusercontent.com``.

        Used to dodge the API rate-limit when the target repo is public.
        Retries a small handful of times to ride out any transient
        ``RemoteProtocolError`` chunks that occur in flaky network paths.

        Returns ``None`` when targeting a GitHub Enterprise instance: the
        content does not live on the public raw host, and requesting it there
        would disclose internal repository names to github.com. Callers fall
        back to the authenticated contents API.
        """
        if not self.is_public_github:
            return None

        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{file_path}"
        headers = {
            "Authorization": "None",
            "Accept": "text/plain",
        }
        attempt = 0
        resp: httpx.Response | None = None
        while attempt < 3:
            try:
                resp = await self.client.get(url, headers=headers)
                break
            except httpx.RemoteProtocolError:
                attempt += 1
                await asyncio.sleep(1)
        else:
            return None

        if resp is None:
            return None
        if resp.status_code == 404:
            return None
        elif resp.status_code == 200:
            return resp.text
        return None


class SubApi:
    """Mixin-ish base class for grouped sub-APIs.

    The whole class is here to formalise the shape every sub-API has —
    a single :class:`ApiBase` reference stored on ``self._base`` — and to
    give the sub-APIs a typed home so pyright can narrow.
    """

    __slots__ = ("_base",)

    def __init__(self, base: ApiBase) -> None:
        self._base = base
