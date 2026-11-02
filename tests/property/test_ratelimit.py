"""The token bucket never exceeds its rate, however requests arrive."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from eqpit.edgar.ratelimit import TokenBucket


@settings(max_examples=200, deadline=None)
@given(
    rate=st.floats(min_value=0.25, max_value=9.5),
    gaps=st.lists(st.floats(min_value=0.0, max_value=0.5), min_size=1, max_size=60),
)
def test_no_one_second_window_exceeds_the_rate(rate: float, gaps: list[float]) -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    bucket = TokenBucket(rate, clock=clock, sleep=sleep)
    grants: list[float] = []
    for gap in gaps:
        now[0] += gap
        bucket.acquire()
        grants.append(now[0])
    start = 0
    for end, stamp in enumerate(grants):
        while stamp - grants[start] > 1.0:
            start += 1
        assert end - start + 1 <= max(rate, 1.0)
