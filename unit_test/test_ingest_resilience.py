"""A malformed GraphQL node must not end an enumeration.

GitHub returns partial nodes when a repository becomes inaccessible during
a query -- SAML enforcement, deletion mid-scan, a per-node GraphQL error.
Indexing a missing field raised KeyError out of the whole enumeration, so
one odd repository could kill a scan of thousands.
"""

import pytest

from gatox.caching.cache_manager import CacheManager
from gatox.cli.output import Output
from gatox.enumerate.ingest.ingest import DataIngestor

Output(False)


def _complete_node(name="acme/ok"):
    return {
        "nameWithOwner": name,
        "defaultBranchRef": {"name": "main"},
        "viewerPermission": "READ",
        "url": f"https://github.com/{name}",
        "isPrivate": False,
        "isFork": False,
        "isArchived": False,
        "stargazers": {"totalCount": 1},
        "pushedAt": "2026-01-01T00:00:00Z",
        "object": None,
        "environments": None,
        "forkingAllowed": True,
    }


async def test_partial_node_does_not_raise():
    await DataIngestor.construct_workflow_cache([{"nameWithOwner": "acme/partial"}])


async def test_repos_after_a_partial_node_are_still_cached():
    await DataIngestor.construct_workflow_cache(
        [{"nameWithOwner": "acme/partial"}, _complete_node("acme/survivor")]
    )
    assert CacheManager().is_repo_cached("acme/survivor")


@pytest.mark.parametrize(
    "dropped",
    [
        "defaultBranchRef",
        "viewerPermission",
        "url",
        "isPrivate",
        "isFork",
        "isArchived",
        "stargazers",
        "pushedAt",
        "object",
        "forkingAllowed",
    ],
)
async def test_any_single_missing_field_is_survivable(dropped):
    node = _complete_node(f"acme/missing-{dropped}")
    node.pop(dropped)
    # Must not raise, whichever field GitHub omitted.
    await DataIngestor.construct_workflow_cache([node, _complete_node("acme/after")])
    assert CacheManager().is_repo_cached("acme/after")


async def test_none_and_empty_results_are_ignored():
    await DataIngestor.construct_workflow_cache(None)
    await DataIngestor.construct_workflow_cache([])
    await DataIngestor.construct_workflow_cache([None, {}])
