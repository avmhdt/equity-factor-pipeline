"""Bulk downloads: capped, one link, and no secret anywhere it should not be."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eqpit.providers.sharadar import SharadarClient, SharadarDownloadError
from eqpit.throttle import ByteThrottle

KEY = "not-a-real-key"
SIGNED = "https://storage.example.com/stocks.csv.zip?signature=secret-link"
BODY = b"z" * 2_000_000


def handler(seen: list[httpx.Request], body: bytes = BODY):  # type: ignore[no-untyped-def]
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.sharadar.com":
            if request.url.params.get("status") == "True":
                return httpx.Response(200, json={"table": "stocks", "size": len(body)})
            return httpx.Response(302, headers={"location": SIGNED})
        return httpx.Response(200, content=body)

    return respond


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_key_stays_in_the_api_header_and_off_the_storage_host(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(handler(seen))
    with SharadarClient(KEY, transport=transport) as client:
        digest = client.download("stocks", tmp_path / "stocks.csv.zip")
    api, storage = seen
    assert api.headers["x-api-key"] == KEY
    assert KEY not in str(api.url)
    assert api.url.params["years"] == "full"
    assert storage.url.host == "storage.example.com"
    assert "x-api-key" not in storage.headers
    assert KEY not in str(storage.url)
    assert (tmp_path / "stocks.csv.zip").read_bytes() == BODY
    assert len(digest) == 64


def test_download_respects_the_byte_cap(tmp_path: Path) -> None:
    fake = FakeTime()
    throttle = ByteThrottle(10_000_000, clock=fake.clock, sleep=fake.sleep)
    transport = httpx.MockTransport(handler([]))
    with SharadarClient(KEY, transport=transport, throttle=throttle) as client:
        client.download("stocks", tmp_path / "file.zip")
    assert fake.now >= 0.2 - 1e-9


def test_status_reports_the_file(tmp_path: Path) -> None:
    transport = httpx.MockTransport(handler([]))
    with SharadarClient(KEY, transport=transport) as client:
        assert client.status("stocks")["size"] == len(BODY)


def test_a_refused_request_names_no_link(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    with (
        SharadarClient(KEY, transport=transport) as client,
        pytest.raises(SharadarDownloadError, match="HTTP 403"),
    ):
        client.download("stocks", tmp_path / "file.zip")


def test_a_storage_failure_never_shows_the_signed_link(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.sharadar.com":
            return httpx.Response(302, headers={"location": SIGNED})
        return httpx.Response(500)

    with (
        SharadarClient(KEY, transport=httpx.MockTransport(respond)) as client,
        pytest.raises(SharadarDownloadError) as caught,
    ):
        client.download("stocks", tmp_path / "file.zip")
    assert "secret-link" not in str(caught.value)


def test_a_network_error_never_shows_the_signed_link(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.sharadar.com":
            return httpx.Response(302, headers={"location": SIGNED})
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with (
        SharadarClient(KEY, transport=httpx.MockTransport(respond)) as client,
        pytest.raises(SharadarDownloadError) as caught,
    ):
        client.download("stocks", tmp_path / "file.zip")
    assert "secret-link" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_missing_key_is_refused() -> None:
    with pytest.raises(ValueError, match="SHARADAR_API_KEY"):
        SharadarClient("")


@settings(max_examples=100, deadline=None)
@given(
    rate=st.floats(min_value=1e3, max_value=1e8),
    chunks=st.lists(st.integers(min_value=1, max_value=1 << 20), min_size=1, max_size=40),
)
def test_the_running_average_never_exceeds_the_cap(rate: float, chunks: list[int]) -> None:
    fake = FakeTime()
    throttle = ByteThrottle(rate, clock=fake.clock, sleep=fake.sleep)
    total = 0
    for chunk in chunks:
        total += chunk
        throttle.consumed(chunk)
        assert total / rate <= fake.now + 1e-9


def test_the_throttle_needs_a_positive_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        ByteThrottle(0)
