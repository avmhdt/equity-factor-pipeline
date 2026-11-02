"""Byte-rate cap for bulk downloads, which share a connection with live recorders."""

from __future__ import annotations

import time
from collections.abc import Callable

DEFAULT_MAX_BYTES_PER_SECOND = 10_000_000
"""10 MB/s: the owner's cap for every bulk zip."""


class ByteThrottle:
    """Delay a byte stream so its running average never exceeds ``rate``.

    Reading from the socket only as fast as this allows pushes the limit back to the sender
    through TCP flow control.

    Args:
        rate: bytes per second.
        clock: monotonic clock in seconds.
        sleep: sleep function in seconds.
    """

    def __init__(
        self,
        rate: float = DEFAULT_MAX_BYTES_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self._clock = clock
        self._sleep = sleep
        self._started: float | None = None
        self._bytes = 0

    def consumed(self, count: int) -> float:
        """Record ``count`` bytes received; sleep as needed and return the seconds slept."""
        if self._started is None:
            self._started = self._clock()
        self._bytes += count
        wait = self._bytes / self.rate - (self._clock() - self._started)
        if wait <= 0:
            return 0.0
        self._sleep(wait)
        return wait
