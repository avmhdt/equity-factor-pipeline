"""FR-09, DQ-07: the monthly universe."""

from __future__ import annotations

from datetime import date

import polars as pl
from market_fixture import delistings, ends, prices, securities

from eqpit.calendar import closes_on
from eqpit.pit.policy import PitPolicy
from eqpit.pit.universe import build_universe
from eqpit.store import PitStore

JANUARY = date(2024, 1, 31)
FEBRUARY = date(2024, 2, 29)


def universe(policy: PitPolicy | None = None, master: pl.DataFrame | None = None) -> pl.DataFrame:
    return build_universe(
        prices(),
        master if master is not None else securities(),
        delistings(),
        ends(),
        policy or PitPolicy(),
    )


def test_names_that_later_delist_stay_in_while_they_qualify() -> None:
    january = universe().filter(pl.col("asof").eq(JANUARY))
    assert {"SYN:8", "SYN:9"} <= set(january["security_id"].to_list())
    row = january.filter(pl.col("security_id").eq("SYN:8"))
    assert row["delisted_after"][0] == date(2024, 2, 14)


def test_gone_names_leave_after_they_delist() -> None:
    february = universe().filter(pl.col("asof").eq(FEBRUARY))
    assert "SYN:8" not in february["security_id"].to_list()


def test_ranks_run_from_one_by_market_cap() -> None:
    january = universe().filter(pl.col("asof").eq(JANUARY)).sort("rank")
    assert january["rank"].to_list() == list(range(1, 11))
    assert january["company_mcap"].is_sorted(descending=True)
    assert january["in_top_1000"].all()


def test_available_at_is_the_as_of_close() -> None:
    table = universe()
    assert table["available_at"].to_list() == closes_on(table["asof"]).to_list()


def test_available_at_stays_with_its_row_across_many_months(small_store: PitStore) -> None:
    table = pl.read_parquet(small_store.root / "universe_monthly" / "**" / "*.parquet")
    assert table["available_at"].to_list() == closes_on(table["asof"]).to_list()


def test_price_floor() -> None:
    table = universe(PitPolicy(min_price_usd=50.0)).filter(pl.col("asof").eq(JANUARY))
    assert table.height == 6


def test_exchange_and_share_type_filters() -> None:
    master = securities().with_columns(
        pl.when(pl.col("security_id").eq("SYN:1"))
        .then(pl.lit("etf"))
        .otherwise(pl.col("share_type"))
        .alias("share_type"),
        pl.when(pl.col("security_id").eq("SYN:2"))
        .then(pl.lit("OTHER"))
        .otherwise(pl.col("exchange"))
        .alias("exchange"),
    )
    names = universe(master=master)["security_id"].to_list()
    assert "SYN:1" not in names
    assert "SYN:2" not in names


def test_share_classes_sum_and_the_largest_stays() -> None:
    master = securities().with_columns(
        pl.when(pl.col("security_id").is_in(["SYN:6", "SYN:7"]))
        .then(pl.lit("CIK:67"))
        .otherwise(pl.col("company_id"))
        .alias("company_id")
    )
    january = universe(master=master).filter(pl.col("asof").eq(JANUARY))
    assert "SYN:6" not in january["security_id"].to_list()
    row = january.filter(pl.col("security_id").eq("SYN:7"))
    assert row["company_mcap"][0] > row["mcap"][0]
    assert row["rank"][0] == 1


def test_the_right_window_of_a_changed_ticker_applies() -> None:
    table = universe()
    assert table.filter(pl.col("security_id").eq("SYN:5")).height == 2
