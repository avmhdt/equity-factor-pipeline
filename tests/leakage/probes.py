"""Probe signals. The real library arrives in M4; these exercise each read path now."""

from __future__ import annotations

from datetime import date

import polars as pl

from eqpit.calendar import month_ends, next_session
from eqpit.store import PitStore


def mom_probe(store: PitStore, asof: date) -> pl.DataFrame:
    """Return over the last 21 sessions, from prices."""
    prices = store.prices(asof, 21, store.universe(asof)["security_id"].to_list())
    return prices.group_by("security_id", maintain_order=True).agg(
        ((1.0 + pl.col("ret").fill_null(0.0)).product() - 1.0).alias("raw"),
        pl.col("available_at").max().alias("input_available_at"),
    )


def high_probe(store: PitStore, asof: date) -> pl.DataFrame:
    """Split-adjusted close over its 63-day high, the path a split could leak through."""
    prices = store.prices(asof, 63, store.universe(asof)["security_id"].to_list())
    return prices.group_by("security_id", maintain_order=True).agg(
        (pl.col("adj_close").last() / pl.col("adj_close").max()).alias("raw"),
        pl.col("available_at").max().alias("input_available_at"),
    )


def gp_probe(store: PitStore, asof: date) -> pl.DataFrame:
    """Gross profit over assets, from fundamentals, restatements and derived quarters."""
    universe = store.universe(asof).select("security_id", "company_id")
    flows = store.fundamentals(asof, ["revenue", "cogs"], history_periods=4).filter(
        pl.col("period_type").eq("3m")
    )
    assets = store.fundamentals(asof, ["total_assets"]).filter(pl.col("period_type").eq("instant"))
    signed = pl.when(pl.col("item").eq("revenue")).then(pl.col("value")).otherwise(-pl.col("value"))
    gross = flows.group_by("company_id", maintain_order=True).agg(
        signed.sort().sum().alias("gross"),
        pl.col("available_at").max().alias("flow_at"),
        pl.len().alias("rows"),
    )
    joined = (
        gross.filter(pl.col("rows").eq(8))
        .join(assets.select("company_id", "value", "available_at"), on="company_id")
        .join(universe, on="company_id")
    )
    return joined.select(
        "security_id",
        (pl.col("gross") / pl.col("value")).alias("raw"),
        pl.max_horizontal("flow_at", "available_at").alias("input_available_at"),
    )


def next_month_end(asof: date) -> date:
    """The month end after ``asof``."""
    return month_ends(next_session(asof), next_session(asof, 25))[0]


def _peek_next_month(store: PitStore, asof: date) -> pl.DataFrame:
    """A careless signal: it opens the tables itself and reads next month's returns."""
    table = pl.read_parquet(store.root / "prices_daily" / "**" / "*.parquet")
    target = next_month_end(asof)
    return (
        table.filter(pl.col("date") > asof, pl.col("date") <= target)
        .group_by("security_id", maintain_order=True)
        .agg(
            ((1.0 + pl.col("ret").fill_null(0.0)).product() - 1.0).alias("raw"),
            pl.col("available_at").max().alias("input_available_at"),
        )
    )


def canary_honest(store: PitStore, asof: date) -> pl.DataFrame:
    """Leaks and stamps its rows truthfully; the static check catches it."""
    return _peek_next_month(store, asof)


def canary_liar(store: PitStore, asof: date) -> pl.DataFrame:
    """Leaks and stamps its rows with the cutoff; only perturbation can catch it."""
    return _peek_next_month(store, asof).with_columns(
        pl.lit(store.cutoff(asof)).cast(pl.Datetime("ns", "UTC")).alias("input_available_at")
    )
