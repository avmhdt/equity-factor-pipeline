"""Momentum and reversal signals.

Month 0 is the month ending at the rebalance date. ``mom_12_1`` covers months 11 to 1, which are
months t-12 to t-2 of holding month t, so it skips the latest month as French's UMD does.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.signals.common import members, to_signal, window_return
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore

MIN_HIGH_DAYS = 126
SUE_QUARTERS = 12
SUE_CHANGES = 8
SUE_MIN_CHANGES = 6
QUARTER_LOW_DAYS = 80
QUARTER_HIGH_DAYS = 100


def _past_return(store: PitStore, asof: date, first: int, last: int) -> pl.DataFrame:
    joined = members(store, asof).join(
        window_return(store, asof, first, last), on="security_id", how="inner"
    )
    return to_signal(joined, pl.col("window"))


@register(SignalSpec("mom_12_1", "momentum", 1, (), 252, "Jegadeesh & Titman 1993", "Mom12m"))
def mom_12_1(store: PitStore, asof: date) -> pl.DataFrame:
    """Cumulative return over months t-12 to t-2."""
    return _past_return(store, asof, 11, 1)


@register(SignalSpec("mom_6_1", "momentum", 1, (), 126, "Jegadeesh & Titman 1993", "Mom6m"))
def mom_6_1(store: PitStore, asof: date) -> pl.DataFrame:
    """Cumulative return over months t-6 to t-2."""
    return _past_return(store, asof, 5, 1)


@register(SignalSpec("str_1m", "momentum", -1, (), 21, "Jegadeesh 1990", "STreversal"))
def str_1m(store: PitStore, asof: date) -> pl.DataFrame:
    """Return of the latest month."""
    return _past_return(store, asof, 0, 0)


@register(SignalSpec("ltr_36_13", "momentum", -1, (), 756, "De Bondt & Thaler 1985", "LRreversal"))
def ltr_36_13(store: PitStore, asof: date) -> pl.DataFrame:
    """Cumulative return over months t-36 to t-13."""
    return _past_return(store, asof, 35, 12)


@register(SignalSpec("high_52w", "momentum", 1, (), 252, "George & Hwang 2004", "High52"))
def high_52w(store: PitStore, asof: date) -> pl.DataFrame:
    """Split-adjusted close over its 52-week high."""
    universe = members(store, asof).select("security_id")
    prices = store.prices(asof, 252).join(universe, on="security_id", how="semi")
    highs = (
        prices.drop_nulls("adj_close")
        .group_by("security_id", maintain_order=True)
        .agg(
            (pl.col("adj_close").last() / pl.col("adj_close").max()).alias("ratio"),
            pl.len().alias("days"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("days") >= MIN_HIGH_DAYS)
    )
    return to_signal(highs, pl.col("ratio"))


@register(SignalSpec("ind_mom", "momentum", 1, (), 126, "Moskowitz & Grinblatt 1999", "IndMom"))
def ind_mom(store: PitStore, asof: date) -> pl.DataFrame:
    """Value-weighted FF-49 industry return over the last six months."""
    now = members(store, asof)
    ends = store.month_ends(date(asof.year - 1, 1, 1), asof)
    if len(ends) < 7:
        return pl.DataFrame(
            schema={
                "security_id": pl.String,
                "raw": pl.Float64,
                "input_available_at": pl.Datetime("ns", "UTC"),
            }
        )
    start = store.universe(ends[-7], store.signal_universe).select(
        "security_id", "ff49", pl.col("company_mcap").alias("weight")
    )
    returns = window_return(store, asof, 5, 0)
    joined = start.join(returns, on="security_id", how="inner").drop_nulls(["ff49", "weight"])
    industries = joined.group_by("ff49", maintain_order=True).agg(
        ((pl.col("window") * pl.col("weight")).sum() / pl.col("weight").sum()).alias("industry"),
        pl.col("available_at").max(),
    )
    assigned = now.join(industries, on="ff49", how="inner")
    return to_signal(assigned, pl.col("industry"))


@register(
    SignalSpec(
        "sue",
        "momentum",
        1,
        ("eps_diluted",),
        0,
        "Foster, Olsen & Shevlin 1984; Bernard & Thomas 1989",
        "EarningsSurprise",
    )
)
def sue(store: PitStore, asof: date) -> pl.DataFrame:
    """Latest year-over-year EPS change over the std of the last eight such changes."""
    rows = (
        store.fundamentals(asof, ["eps_diluted"], history_periods=SUE_QUARTERS)
        .filter(pl.col("period_type").eq("3m"))
        .sort("company_id", "period_end")
    )
    gaps = (pl.col("period_end") - pl.col("period_end").shift(4)).dt.total_days().over("company_id")
    changes = rows.with_columns(
        (pl.col("value") - pl.col("value").shift(4).over("company_id")).alias("change"),
        gaps.alias("gap"),
    ).filter(pl.col("gap").is_between(335, 395))
    summary = (
        changes.group_by("company_id", maintain_order=True)
        .agg(
            pl.col("change").tail(SUE_CHANGES).last().alias("latest"),
            pl.col("change").tail(SUE_CHANGES).std().alias("spread"),
            pl.col("change").tail(SUE_CHANGES).len().alias("count"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("count") >= SUE_MIN_CHANGES, pl.col("spread") > 0)
    )
    joined = members(store, asof).join(summary, on="company_id", how="inner")
    return to_signal(joined, pl.col("latest") / pl.col("spread"))
