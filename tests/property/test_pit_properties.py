"""Properties of the point-in-time layer."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from eqpit import calendar
from eqpit.pit.corpactions import build_corp_actions
from eqpit.pit.prices import build_prices
from eqpit.store import PitStore

START = date(2024, 3, 1)


def frame(closes: list[float], split_day: int | None, ratio: float) -> pl.DataFrame:
    """Daily rows; prices from ``split_day`` on are divided by ``ratio``."""
    adjusted = [
        close / ratio if split_day is not None and index >= split_day else close
        for index, close in enumerate(closes)
    ]
    count = len(closes)
    days = [START + timedelta(days=index) for index in range(count)]
    return pl.DataFrame(
        {
            "security_id": ["A"] * count,
            "date": days,
            "open": adjusted,
            "high": adjusted,
            "low": adjusted,
            "close": adjusted,
            "volume": [1.0] * count,
            "shares_out": [1.0] * count,
        }
    )


def split_actions(day: int | None, ratio: float) -> pl.DataFrame:
    rows = [("A", START + timedelta(days=day), "split", ratio, None)] if day is not None else []
    return build_corp_actions(
        pl.DataFrame(
            rows,
            schema={
                "security_id": pl.String,
                "ex_date": pl.Date,
                "action": pl.String,
                "value": pl.Float64,
                "new_ticker": pl.String,
            },
            orient="row",
        )
    )


@settings(max_examples=60, deadline=None)
@given(
    closes=st.lists(st.floats(min_value=1.0, max_value=1000.0), min_size=3, max_size=30),
    split_fraction=st.floats(min_value=0.0, max_value=1.0),
    ratio=st.sampled_from([0.1, 1.5, 2.0, 3.0, 4.0, 10.0, 20.0]),
)
def test_splits_never_change_returns(
    closes: list[float], split_fraction: float, ratio: float
) -> None:
    day = 1 + int(split_fraction * (len(closes) - 2))
    plain = build_prices(frame(closes, None, 1.0), split_actions(None, 1.0))
    split = build_prices(frame(closes, day, ratio), split_actions(day, ratio))
    for left, right in zip(plain["ret"].to_list()[1:], split["ret"].to_list()[1:], strict=True):
        assert abs(left - right) <= 1e-9 * (1.0 + abs(left))


@settings(max_examples=25, deadline=None)
@given(
    first=st.dates(min_value=date(2010, 3, 1), max_value=date(2017, 11, 1)),
    gap=st.integers(min_value=1, max_value=120),
)
def test_a_later_cutoff_sees_a_superset(first: date, gap: int, small_store: PitStore) -> None:
    early = calendar.session_on_or_after(first)
    late = calendar.session_on_or_after(early + timedelta(days=gap))
    items = ["total_assets"]
    seen_early = pl.read_parquet(small_store.root / "fundamentals_pit" / "**" / "*.parquet").filter(
        pl.col("item").is_in(items),
        pl.col("available_at") <= small_store.cutoff(early),
    )
    seen_late = pl.read_parquet(small_store.root / "fundamentals_pit" / "**" / "*.parquet").filter(
        pl.col("item").is_in(items),
        pl.col("available_at") <= small_store.cutoff(late),
    )
    key = ["company_id", "period_end", "period_type", "available_at"]
    assert seen_early.join(seen_late, on=key, how="anti").is_empty()
    prices_early = small_store.prices(early, 5).select("security_id", "date")
    prices_late = small_store.prices(late, 5 + gap).select("security_id", "date")
    assert prices_early.join(prices_late, on=["security_id", "date"], how="anti").is_empty()
