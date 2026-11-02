"""Sharadar's direct API. The key travels in the ``x-api-key`` header, never the URL."""

from __future__ import annotations

import httpx

SHARADAR_BASE_URL = "https://api.sharadar.com/v1.0"

TABLES = {
    "SEP": "stocks",
    "SF1": "fundamentals",
    "TICKERS": "tickers",
    "ACTIONS": "actions",
}
"""Spec dataset name to API table name."""

KEY_CHECK_TICKER = "GME"
"""Outside the free tier: AAPL and MSFT answer with no key at all, so they prove nothing."""


def fetch_one_row(
    table: str,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Return the CSV body of a one-row query on ``table``, raising on any HTTP error."""
    with httpx.Client(
        base_url=SHARADAR_BASE_URL,
        headers={"x-api-key": api_key},
        transport=transport,
        timeout=httpx.Timeout(30.0),
    ) as client:
        response = client.get(f"/data/{table}", params={"ticker": KEY_CHECK_TICKER, "limit": 1})
        response.raise_for_status()
        return response.text
