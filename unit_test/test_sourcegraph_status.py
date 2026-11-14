"""Sourcegraph transport failures must not appear as successful empty searches."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from gatox.search.search import Searcher

_SEND = httpx.AsyncClient.send


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('data: {"done": true}\n\n', set()),
        ('data: [{"repository": "github.com/owner/repo"}]\n\n', {"owner/repo"}),
        (
            'data: {"title": "Unable To Process Query", "description": "bad query"}\n\n',
            None,
        ),
    ],
)
async def test_sourcegraph_results(monkeypatch, body, expected):
    """Distinguish completed empty responses, matches, and explicit query errors."""
    monkeypatch.setattr(httpx.AsyncClient, "send", _SEND)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    )
    with patch("gatox.search.search.httpx.AsyncClient", return_value=client):
        assert (
            await Searcher(api_client=MagicMock(transport=None)).use_sourcegraph_api(
                "owner"
            )
            == expected
        )


async def test_sourcegraph_http_failure(monkeypatch):
    """HTTP errors must propagate instead of falling through to an empty set."""
    monkeypatch.setattr(httpx.AsyncClient, "send", _SEND)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )
    with patch("gatox.search.search.httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await Searcher(api_client=MagicMock(transport=None)).use_sourcegraph_api(
                "owner"
            )


async def test_sourcegraph_timeout(monkeypatch):
    """Timeouts return failure, not an empty or partial success result."""
    monkeypatch.setattr(httpx.AsyncClient, "send", _SEND)

    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    with patch("gatox.search.search.httpx.AsyncClient", return_value=client):
        assert (
            await Searcher(api_client=MagicMock(transport=None)).use_sourcegraph_api(
                "owner"
            )
            is None
        )
