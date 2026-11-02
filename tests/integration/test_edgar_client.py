"""AC-W-15: the EDGAR client is compliant under a mock transport."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from eqpit.edgar.client import EdgarClient, EdgarConfigError

USER_AGENT = "Test Person test.person@example.com"
URL = "https://data.sec.gov/submissions/CIK0000320193.json"


class FakeTime:
    """Deterministic clock whose sleep advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def max_in_window(stamps: list[float], width: float = 1.0) -> int:
    """Largest number of stamps inside any closed window of ``width`` seconds."""
    best = 0
    start = 0
    for end, stamp in enumerate(stamps):
        while stamp - stamps[start] > width:
            start += 1
        best = max(best, end - start + 1)
    return best


def recording_transport(
    stamps: list[float],
    clock: Callable[[], float],
    agents: list[str],
    responses: list[int] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(clock())
        agents.append(request.headers.get("User-Agent", ""))
        status = responses.pop(0) if responses else 200
        return httpx.Response(status, json={"ok": True})

    return httpx.MockTransport(handler)


def test_burst_of_500_requests_stays_under_the_cap_with_ua_on_every_request() -> None:
    fake = FakeTime()
    stamps: list[float] = []
    agents: list[str] = []
    client = EdgarClient(
        USER_AGENT,
        max_rps=8,
        transport=recording_transport(stamps, fake.clock, agents),
        clock=fake.clock,
        sleep=fake.sleep,
    )
    with client:
        for index in range(500):
            client.get_json(f"{URL}?n={index}")
    assert len(stamps) == 500
    assert max_in_window(stamps) <= 8
    assert set(agents) == {USER_AGENT}
    assert client.stats.requests == 500
    assert client.stats.throttled_seconds > 0


def test_cap_holds_on_the_real_clock() -> None:
    stamps: list[float] = []
    agents: list[str] = []
    client = EdgarClient(
        USER_AGENT, max_rps=8, transport=recording_transport(stamps, time.monotonic, agents)
    )
    with client:
        for index in range(12):
            client.get_json(f"{URL}?n={index}")
    assert max_in_window(stamps) <= 8


def test_retries_count_against_the_rate_cap() -> None:
    fake = FakeTime()
    stamps: list[float] = []
    agents: list[str] = []
    responses = [429, 503, 200]
    client = EdgarClient(
        USER_AGENT,
        max_rps=8,
        transport=recording_transport(stamps, fake.clock, agents, responses),
        backoff_base=0.5,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    with client:
        assert client.get_json(URL) == {"ok": True}
    assert client.stats.requests == 3
    assert client.stats.retries == 2
    assert stamps[1] - stamps[0] >= 0.5
    assert stamps[2] - stamps[1] >= 1.0


def test_retry_after_header_is_respected() -> None:
    fake = FakeTime()
    stamps: list[float] = []
    responses = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})]

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(fake.clock())
        return responses.pop(0)

    client = EdgarClient(
        USER_AGENT,
        transport=httpx.MockTransport(handler),
        clock=fake.clock,
        sleep=fake.sleep,
    )
    with client:
        client.get_json(URL)
    assert stamps[1] - stamps[0] >= 7.0


def test_gives_up_after_max_retries() -> None:
    fake = FakeTime()
    stamps: list[float] = []
    client = EdgarClient(
        USER_AGENT,
        max_retries=2,
        transport=recording_transport(stamps, fake.clock, [], [500, 500, 500, 200]),
        clock=fake.clock,
        sleep=fake.sleep,
    )
    with client, pytest.raises(httpx.HTTPStatusError):
        client.get_json(URL)
    assert len(stamps) == 3


def test_404_is_not_retried() -> None:
    stamps: list[float] = []
    client = EdgarClient(
        USER_AGENT, transport=recording_transport(stamps, time.monotonic, [], [404])
    )
    with client, pytest.raises(httpx.HTTPStatusError):
        client.get_json(URL)
    assert len(stamps) == 1


def test_disk_cache_avoids_a_second_request(tmp_path: Path) -> None:
    stamps: list[float] = []
    transport = recording_transport(stamps, time.monotonic, [])
    with EdgarClient(USER_AGENT, cache_dir=tmp_path, transport=transport) as client:
        first = client.get_json(URL)
        second = client.get_json(URL)
        assert client.stats.cache_hits == 1
    assert first == second
    assert len(stamps) == 1


def test_download_streams_to_disk_with_the_user_agent(tmp_path: Path) -> None:
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["User-Agent"])
        return httpx.Response(200, content=b"zipbytes")

    dest = tmp_path / "bulk" / "companyfacts.zip"
    with EdgarClient(USER_AGENT, transport=httpx.MockTransport(handler)) as client:
        digest = client.download("https://www.sec.gov/x.zip", dest)
    assert dest.read_bytes() == b"zipbytes"
    assert len(digest) == 64
    assert agents == [USER_AGENT]


@pytest.mark.parametrize("agent", ["", "   ", "just a name", "name at example.com"])
def test_refuses_to_start_without_an_email(agent: str) -> None:
    with pytest.raises(EdgarConfigError):
        EdgarClient(agent)


@pytest.mark.parametrize("rate", [10.0, 10.5, 100.0])
def test_refuses_a_rate_at_or_above_the_published_limit(rate: float) -> None:
    with pytest.raises(EdgarConfigError):
        EdgarClient(USER_AGENT, max_rps=rate)
