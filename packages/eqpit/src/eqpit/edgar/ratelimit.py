"""Client-side request pacing for SEC fair access."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Token bucket with a capacity of one token.

    A larger bucket would let a burst plus the refill exceed the rate inside a one-second
    window, so tokens are handed out one at a time. Grants are spaced so that no closed
    one-second window ever holds more than ``rate`` of them.

    Args:
        rate: maximum grants per second.
        clock: monotonic clock in seconds.
        sleep: sleep function in seconds.
    """

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        whole = math.floor(rate) if rate >= 1 else rate
        self._interval = (1.0 / whole) * (1.0 + 1e-9)
        self._clock = clock
        self._sleep = sleep
        self._next_free = -math.inf
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a token is free and return the seconds spent waiting."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                wait = self._next_free - now
                if wait <= 0:
                    self._next_free = now + self._interval
                    return waited
            self._sleep(wait)
            waited += wait
