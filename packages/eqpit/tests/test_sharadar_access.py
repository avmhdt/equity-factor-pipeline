"""The Sharadar key goes in a header and stays out of URLs."""

from __future__ import annotations

import httpx
import pytest

from eqpit.providers.sharadar import fetch_one_row


def test_key_is_sent_in_the_header_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ticker,date\nAAPL,2026-10-30\n")

    body = fetch_one_row("stocks", "secret-key", transport=httpx.MockTransport(handler))
    assert body.startswith("ticker,date")
    request = seen[0]
    assert request.headers["x-api-key"] == "secret-key"
    assert "secret-key" not in str(request.url)
    assert request.url.path == "/v1.0/data/stocks"
    assert request.url.params["limit"] == "1"


def test_http_errors_are_raised() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_one_row("stocks", "key", transport=transport)
