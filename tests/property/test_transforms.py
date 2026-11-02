"""AC-W-07: winsorize and z-score, for any cross-section."""

from __future__ import annotations

import itertools
from datetime import date

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from efp.signals.transforms import winsorize, zscore

DAY = date(2020, 1, 31)
values = st.lists(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    min_size=5,
    max_size=200,
)


def frame(items: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"asof": [DAY] * len(items), "raw": items})


@settings(max_examples=150, deadline=None)
@given(items=values)
def test_zscore_has_zero_mean_and_unit_std(items: list[float]) -> None:
    if max(items) - min(items) < 1e-3:
        return
    scored = zscore(frame(items), "raw")["z"]
    assert abs(scored.mean()) < 1e-10
    assert abs(scored.std() - 1.0) < 1e-10


def test_a_constant_cross_section_has_no_score() -> None:
    assert zscore(frame([3.0] * 6), "raw")["z"].null_count() == 6


@settings(max_examples=150, deadline=None)
@given(items=values)
def test_winsorize_is_idempotent(items: list[float]) -> None:
    once = winsorize(frame(items), "raw")["winsorized"].to_list()
    twice = winsorize(frame(once), "raw")["winsorized"].to_list()
    assert all(abs(a - b) <= 1e-9 * (1.0 + abs(a)) for a, b in zip(once, twice, strict=True))


@settings(max_examples=150, deadline=None)
@given(items=values)
def test_winsorize_keeps_order(items: list[float]) -> None:
    clipped = winsorize(frame(items), "raw")["winsorized"].to_list()
    pairs = sorted(zip(items, clipped, strict=True))
    assert all(a[1] <= b[1] + 1e-12 for a, b in itertools.pairwise(pairs))


def test_winsorize_clips_to_percentiles() -> None:
    items = [float(index) for index in range(101)]
    clipped = winsorize(frame(items), "raw")["winsorized"]
    assert clipped.min() == 1.0
    assert clipped.max() == 99.0


def test_groups_are_separate() -> None:
    scored = zscore(
        pl.DataFrame(
            {
                "signal": ["a"] * 4 + ["b"] * 4,
                "asof": [DAY] * 8,
                "raw": [1.0, 2.0, 3.0, 4.0, 100.0, 200.0, 300.0, 400.0],
            }
        ),
        "raw",
        by=["signal", "asof"],
    )
    assert scored["z"].to_list()[:4] == pytest.approx(scored["z"].to_list()[4:])
