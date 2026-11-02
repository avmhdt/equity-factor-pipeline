"""EDGAR HTTP client: User-Agent on every request, rate cap, retries and a disk cache."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import structlog

from eqpit.edgar.ratelimit import TokenBucket
from eqpit.settings import Settings, get_settings
from eqpit.throttle import ByteThrottle

log = structlog.get_logger(__name__)

SEC_PUBLISHED_LIMIT_RPS = 10.0
"""SEC's published fair-access maximum; the client refuses to run at or above it."""

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class EdgarConfigError(ValueError):
    """The client was configured in a way SEC's fair-access policy does not allow."""


def validate_user_agent(user_agent: str) -> str:
    """Return ``user_agent`` if it holds a name and a contact email, else raise."""
    candidate = user_agent.strip()
    if not candidate:
        raise EdgarConfigError("SEC_USER_AGENT is not set")
    if not _EMAIL.search(candidate):
        raise EdgarConfigError("SEC_USER_AGENT must contain a contact email address")
    return candidate


@dataclass
class ClientStats:
    """Counters for observability (NFR-08)."""

    requests: int = 0
    retries: int = 0
    cache_hits: int = 0
    throttled_seconds: float = 0.0


class EdgarClient:
    """Rate-limited EDGAR client.

    Args:
        user_agent: name plus contact email, sent on every request.
        max_rps: client-side cap in requests per second, below SEC's published limit.
        cache_dir: directory for cached responses; None disables the cache.
        transport: httpx transport override, used by tests.
        max_retries: retries after the first attempt for 429 and 5xx responses.
        backoff_base: first backoff in seconds; doubles on each retry.
        clock: monotonic clock in seconds.
        sleep: sleep function in seconds.
    """

    def __init__(
        self,
        user_agent: str,
        max_rps: float = 8.0,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = validate_user_agent(user_agent)
        if max_rps >= SEC_PUBLISHED_LIMIT_RPS:
            raise EdgarConfigError(
                f"max_rps={max_rps} must stay below SEC's limit of {SEC_PUBLISHED_LIMIT_RPS}"
            )
        self.max_rps = max_rps
        self.stats = ClientStats()
        self._bucket = TokenBucket(max_rps, clock=clock, sleep=sleep)
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            transport=transport,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **kwargs: Any) -> Self:
        """Build a client from ``SEC_USER_AGENT`` and ``SEC_MAX_RPS``."""
        resolved = settings or get_settings()
        return cls(resolved.sec_user_agent, max_rps=resolved.sec_max_rps, **kwargs)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the connection pool and log the session counters."""
        self._http.close()
        log.info(
            "edgar_client_closed",
            requests=self.stats.requests,
            retries=self.stats.retries,
            cache_hits=self.stats.cache_hits,
            throttled_seconds=round(self.stats.throttled_seconds, 3),
        )

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"

    def _send(self, url: str) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self.stats.throttled_seconds += self._bucket.acquire()
            self.stats.requests += 1
            response = self._http.get(url)
            if response.status_code not in _RETRYABLE or attempt == self._max_retries:
                break
            self.stats.retries += 1
            delay = self._backoff_base * 2**attempt
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning("edgar_retry", url=url, status=response.status_code, delay=delay)
            self._sleep(delay)
        response.raise_for_status()
        return response

    def get_bytes(self, url: str) -> bytes:
        """Fetch ``url``, serving a disk-cached copy when one exists."""
        cache_path = self._cache_path(url)
        if cache_path is not None and cache_path.exists():
            self.stats.cache_hits += 1
            return cache_path.read_bytes()
        content = self._send(url).content
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(cache_path)
        return content

    def get_json(self, url: str) -> Any:
        """Fetch ``url`` and decode it as JSON."""
        return json.loads(self.get_bytes(url))

    def download(self, url: str, dest: Path, throttle: ByteThrottle | None = None) -> str:
        """Stream ``url`` to ``dest`` and return its sha256; used for bulk zips."""
        self.stats.throttled_seconds += self._bucket.acquire()
        self.stats.requests += 1
        digest = hashlib.sha256()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1 << 18):
                    handle.write(chunk)
                    digest.update(chunk)
                    if throttle is not None:
                        throttle.consumed(len(chunk))
        return digest.hexdigest()
