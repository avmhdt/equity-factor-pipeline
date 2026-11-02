"""Building blocks shared by signal definitions. Everything reads through PitStore."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import polars as pl

from eqpit.store import PitStore

STALE_DAYS = 550
"""A filing older than this at the as-of date is too old to stand for the company today."""
YEAR_LOW_DAYS = 335
YEAR_HIGH_DAYS = 395
TTM_LOW_DAYS = 255
TTM_HIGH_DAYS = 295


def members(store: PitStore, asof: date) -> pl.DataFrame:
    """Universe rows with the columns signals join on."""
    return store.universe(asof, store.signal_universe).select(
        "security_id",
        "company_id",
        "ff49",
        pl.col("company_mcap").alias("me"),
        pl.col("available_at").alias("me_at"),
    )


def to_signal(
    frame: pl.DataFrame, value: pl.Expr, stamps: Sequence[str] = ("available_at",)
) -> pl.DataFrame:
    """Shape a frame into ``security_id``, ``raw``, ``input_available_at``."""
    return frame.select(
        "security_id",
        value.alias("raw"),
        pl.max_horizontal(*stamps).alias("input_available_at"),
    ).filter(pl.col("raw").is_finite())


def ttm(store: PitStore, asof: date, items: Sequence[str]) -> pl.DataFrame:
    """Trailing sums per company as ``company_id``, one column per item, ``available_at``.

    A value needs four consecutive quarters, all public at the cutoff (DQ-06), ending recently.
    """
    rows = store.fundamentals(asof, items, history_periods=4).filter(pl.col("period_type").eq("3m"))
    oldest = asof - timedelta(days=STALE_DAYS)
    span = (pl.col("period_end").max() - pl.col("period_end").min()).dt.total_days()
    summed = (
        rows.group_by("company_id", "item", maintain_order=True)
        .agg(
            pl.col("value").sort().sum(),
            pl.len().alias("quarters"),
            span.alias("span"),
            pl.col("period_end").max().alias("end"),
            pl.col("available_at").max(),
        )
        .filter(
            pl.col("quarters").eq(4),
            pl.col("span").is_between(TTM_LOW_DAYS, TTM_HIGH_DAYS),
            pl.col("end") >= oldest,
        )
    )
    return _wide(summed, items)


def _wide(rows: pl.DataFrame, items: Sequence[str]) -> pl.DataFrame:
    """One row per company with a column per item and the latest stamp among them."""
    stamps = rows.group_by("company_id", maintain_order=True).agg(pl.col("available_at").max())
    if rows.is_empty():
        wide = stamps.select("company_id")
    else:
        wide = rows.pivot(on="item", index="company_id", values="value")
    for item in items:
        if item not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(item))
    return wide.join(stamps, on="company_id", how="inner")


def latest(store: PitStore, asof: date, items: Sequence[str]) -> pl.DataFrame:
    """Most recent balance-sheet values, one row per company."""
    rows = store.fundamentals(asof, items, history_periods=1).filter(
        pl.col("period_type").eq("instant"),
        pl.col("period_end") >= asof - timedelta(days=STALE_DAYS),
    )
    return _wide(rows.select("company_id", "item", "value", "available_at"), items)


def with_year_ago(store: PitStore, asof: date, items: Sequence[str]) -> pl.DataFrame:
    """Latest balance-sheet values plus ``<item>_lag`` from about a year before them."""
    rows = store.fundamentals(asof, items, history_periods=6).filter(
        pl.col("period_type").eq("instant")
    )
    newest = rows.group_by("company_id", "item", maintain_order=True).first()
    fresh = newest.filter(pl.col("period_end") >= asof - timedelta(days=STALE_DAYS))
    gap = (pl.col("period_end_now") - pl.col("period_end")).dt.total_days()
    earlier = (
        rows.join(
            fresh.select("company_id", "item", pl.col("period_end").alias("period_end_now")),
            on=["company_id", "item"],
        )
        .filter(gap.is_between(YEAR_LOW_DAYS, YEAR_HIGH_DAYS))
        .group_by("company_id", "item", maintain_order=True)
        .first()
        .select(
            "company_id",
            pl.format("{}_lag", "item").alias("item"),
            "value",
            "available_at",
        )
    )
    columns = [*items, *[f"{item}_lag" for item in items]]
    both = pl.concat(
        [fresh.select("company_id", "item", "value", "available_at"), earlier], how="vertical"
    )
    return _wide(both, columns)


def annual_pair(store: PitStore, asof: date, items: Sequence[str]) -> pl.DataFrame:
    """Last two fiscal years per company: ``<item>`` and ``<item>_lag`` for each item."""
    rows = store.fundamentals(asof, items, history_periods=12).filter(
        pl.col("fiscal_period").eq("FY")
    )
    newest = rows.group_by("company_id", "item", maintain_order=True).first()
    fresh = newest.filter(pl.col("period_end") >= asof - timedelta(days=STALE_DAYS))
    gap = (pl.col("period_end_now") - pl.col("period_end")).dt.total_days()
    earlier = (
        rows.join(
            fresh.select("company_id", "item", pl.col("period_end").alias("period_end_now")),
            on=["company_id", "item"],
        )
        .filter(gap.is_between(YEAR_LOW_DAYS, YEAR_HIGH_DAYS))
        .group_by("company_id", "item", maintain_order=True)
        .first()
        .select("company_id", pl.format("{}_lag", "item").alias("item"), "value", "available_at")
    )
    columns = [*items, *[f"{item}_lag" for item in items]]
    both = pl.concat(
        [fresh.select("company_id", "item", "value", "available_at"), earlier], how="vertical"
    )
    return _wide(both, columns)


def window_return(store: PitStore, asof: date, first_back: int, last_back: int) -> pl.DataFrame:
    """Compound return over months ``first_back`` to ``last_back`` before the as-of month.

    Month 0 is the month ending at ``asof``. Every month in the window must be present.
    """
    months = first_back + 1
    frame = store.monthly(asof, months)
    ends = frame["month"].unique().sort().to_list()
    if len(ends) < months:
        return frame.clear().select("security_id", pl.col("ret").alias("window"), "available_at")
    chosen = ends[: first_back - last_back + 1]
    return (
        frame.filter(pl.col("month").is_in(chosen))
        .group_by("security_id", maintain_order=True)
        .agg(
            ((1.0 + pl.col("ret")).product() - 1.0).alias("window"),
            pl.len().alias("months"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("months").eq(len(chosen)))
        .drop("months")
    )
