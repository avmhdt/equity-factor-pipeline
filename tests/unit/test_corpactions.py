"""AC-W-05: splits, dividends and ticker changes."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from market_fixture import actions, prices, read, securities

from eqpit.pit.corpactions import UnmappedActionError, build_corp_actions
from eqpit.pit.prices import FLAG_EXTREME, FLAG_MIDPOINT, FLAG_STALE, build_prices
from eqpit.schemas import INSTANT

SPLIT_DAY = date(2024, 2, 12)
DIVIDEND_DAY = date(2024, 2, 8)
SESSION = date(2024, 2, 9)


def row(security: str, day: date) -> dict[str, object]:
    frame = prices().filter(pl.col("security_id").eq(security), pl.col("date").eq(day))
    return frame.to_dicts()[0]


def previous_close(security: str, day: date) -> float:
    frame = prices().filter(pl.col("security_id").eq(security), pl.col("date") < day)
    return float(frame.sort("date")["close"][-1])


def test_a_split_leaves_the_return_alone() -> None:
    assert row("SYN:3", SPLIT_DAY)["ret"] == pytest.approx(0.001, abs=1e-6)


def test_a_dividend_is_in_the_total_return() -> None:
    today = row("SYN:4", DIVIDEND_DAY)
    expected = (float(today["close"]) + 0.5) / previous_close("SYN:4", DIVIDEND_DAY) - 1.0
    assert today["ret"] == pytest.approx(expected, abs=1e-12)
    assert today["ret"] == pytest.approx(0.001, abs=1e-6)


def test_market_cap_uses_the_unadjusted_price_and_same_date_shares() -> None:
    before = row("SYN:3", date(2024, 2, 9))
    after = row("SYN:3", SPLIT_DAY)
    assert before["mcap"] == float(before["close"]) * float(before["shares_out"])
    assert after["shares_out"] == 2.0 * float(before["shares_out"])
    assert float(after["mcap"]) == pytest.approx(float(before["mcap"]) * 1.001, rel=1e-5)


def test_split_factor_only_looks_back() -> None:
    assert row("SYN:3", date(2024, 2, 9))["split_factor"] == 1.0
    assert row("SYN:3", SPLIT_DAY)["split_factor"] == 2.0


def test_a_ticker_change_keeps_one_continuous_series() -> None:
    master = securities().filter(pl.col("security_id").eq("SYN:5")).sort("valid_from")
    assert master["ticker"].to_list() == ["AAA", "BBB"]
    series = prices().filter(pl.col("security_id").eq("SYN:5")).sort("date")
    assert series["ret"].drop_nulls().len() == series.height - 1
    assert series["ret"].drop_nulls().min() == pytest.approx(0.001, abs=1e-6)


def test_ex_date_without_a_price_rolls_to_the_next_price() -> None:
    raw = pl.DataFrame(
        {
            "security_id": ["A", "A"],
            "date": [date(2024, 2, 9), date(2024, 2, 13)],
            "open": [10.0, 5.0],
            "high": [10.0, 5.0],
            "low": [10.0, 5.0],
            "close": [10.0, 5.0],
            "volume": [1.0, 1.0],
            "shares_out": [1.0, 2.0],
        }
    )
    move = build_corp_actions(
        pl.DataFrame(
            {
                "security_id": ["A"],
                "ex_date": [date(2024, 2, 12)],
                "action": ["split"],
                "value": [2.0],
                "new_ticker": [None],
            },
            schema_overrides={"new_ticker": pl.String},
        )
    )
    built = build_prices(raw, move).sort("date")
    assert built["ret"][1] == pytest.approx(0.0, abs=1e-12)


def test_an_action_after_the_last_price_is_ignored() -> None:
    raw = read("prices_10sec.csv").filter(pl.col("security_id").eq("SYN:3"))
    late = (
        actions()
        .filter(pl.col("security_id").eq("SYN:3"))
        .with_columns(pl.lit(date(2030, 1, 2)).alias("ex_date"))
    )
    assert build_prices(raw, late)["split_factor"].max() == 1.0


def test_unmapped_action_types_fail_the_build() -> None:
    raw = pl.DataFrame(
        {
            "security_id": ["A"],
            "ex_date": [SESSION],
            "action": ["regulatoryaction"],
            "value": [None],
            "new_ticker": [None],
        },
        schema_overrides={"value": pl.Float64, "new_ticker": pl.String},
    )
    with pytest.raises(UnmappedActionError, match="regulatoryaction"):
        build_corp_actions(raw)
    with pytest.raises(UnmappedActionError, match="outside the vocabulary"):
        build_corp_actions(raw, {"regulatoryaction": "something_new"})


def test_vendor_values_map_to_the_vocabulary() -> None:
    raw = pl.DataFrame(
        {
            "security_id": ["A"],
            "ex_date": [SESSION],
            "action": ["dividend"],
            "value": [0.1],
            "new_ticker": [None],
        },
        schema_overrides={"new_ticker": pl.String},
    )
    built = build_corp_actions(raw, {"dividend": "cash_dividend"})
    assert built["action"].to_list() == ["cash_dividend"]
    assert built["available_at"].dtype == INSTANT


def sanity_frame(closes: list[float], volumes: list[float]) -> pl.DataFrame:
    count = len(closes)
    return pl.DataFrame(
        {
            "security_id": ["A"] * count,
            "date": pl.date_range(date(2024, 1, 2), date(2024, 1, count + 1), eager=True),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": volumes,
            "shares_out": [1.0] * count,
        }
    )


def no_actions() -> pl.DataFrame:
    return actions().clear()


def test_extreme_returns_are_flagged_and_kept() -> None:
    built = build_prices(sanity_frame([10.0, 25.0, 25.0], [1.0, 1.0, 1.0]), no_actions())
    assert built.height == 3
    assert built["price_flag"].to_list() == [0, FLAG_EXTREME, 0]
    assert built["ret"][1] == pytest.approx(1.5)


def test_stale_starts_on_the_fifth_zero_volume_day() -> None:
    frame = sanity_frame([10.0] * 7, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    flags = build_prices(frame, no_actions())["price_flag"].to_list()
    assert flags == [0, 0, 0, 0, 0, FLAG_STALE, 0]


def test_four_zero_volume_days_are_not_stale() -> None:
    frame = sanity_frame([10.0] * 6, [1.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert build_prices(frame, no_actions())["price_flag"].sum() == 0


def test_midpoint_prices_become_absolute_and_flagged() -> None:
    built = build_prices(sanity_frame([10.0, -11.0], [1.0, 1.0]), no_actions())
    assert built["close"].to_list() == [10.0, 11.0]
    assert built["price_flag"].to_list() == [0, FLAG_MIDPOINT]


def test_non_positive_close_is_flagged_with_no_return_after_it() -> None:
    built = build_prices(sanity_frame([10.0, 0.0, 5.0], [1.0, 1.0, 1.0]), no_actions())
    assert built["price_flag"][1] == FLAG_EXTREME
    assert built["ret"][2] is None
