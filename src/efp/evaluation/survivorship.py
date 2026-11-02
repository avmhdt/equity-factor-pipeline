"""Survivorship bias: the same results with and without names that later delisted (FR-10)."""

from __future__ import annotations

import math

import polars as pl

from efp.evaluation.french import Panels, load_panels, replicate
from eqpit.store import PitStore

FAMILIES = {
    "hml": "value",
    "umd": "momentum",
    "rmw": "quality",
    "cma": "investment",
    "smb": "size",
    "low_risk": "low_risk",
}


def equal_weight_universe(panels: Panels) -> pl.DataFrame:
    """Equal-weighted return of each month's universe over the next month."""
    months = panels.caps["month"].unique().sort().to_list()
    following = pl.DataFrame(
        {"asof": months[:-1], "month": months[1:]}, schema={"asof": pl.Date, "month": pl.Date}
    )
    held = (
        panels.universe.select("asof", "security_id")
        .join(following, on="asof")
        .join(panels.returns, on=["month", "security_id"], how="inner")
        .sort("month", "security_id")
    )
    return (
        held.group_by("month", maintain_order=True)
        .agg(pl.col("ret").mean())
        .select("month", pl.lit("ew_universe").alias("series"), "ret")
    )


def series_by_mode(store: PitStore) -> pl.DataFrame:
    """Monthly ``month``, ``series``, ``mode``, ``ret`` in both universes."""
    frames: list[pl.DataFrame] = []
    for mode, survivors_only in (("full", False), ("survivors_only", True)):
        panels = load_panels(store, survivors_only)
        factors = replicate(store, survivors_only).rename({"factor": "series"})
        frames.append(
            pl.concat([factors, equal_weight_universe(panels)], how="vertical").with_columns(
                pl.lit(mode).alias("mode")
            )
        )
    return pl.concat(frames, how="vertical").filter(
        pl.col("series").is_in(["ew_universe", *FAMILIES])
    )


def bias_table(series: pl.DataFrame) -> pl.DataFrame:
    """Annualised mean, vol and Sharpe per series and mode, with survivors minus full."""
    summary = (
        series.sort("series", "mode", "month")
        .group_by("series", "mode", maintain_order=True)
        .agg(
            (pl.col("ret").mean() * 12.0).alias("mean_annual"),
            (pl.col("ret").std() * math.sqrt(12.0)).alias("vol_annual"),
            pl.len().alias("months"),
        )
        .with_columns((pl.col("mean_annual") / pl.col("vol_annual")).alias("sharpe"))
    )
    wide = summary.pivot(
        on="mode",
        index="series",
        values=["mean_annual", "vol_annual", "sharpe", "months"],
    )
    return wide.with_columns(
        (pl.col("mean_annual_survivors_only") - pl.col("mean_annual_full")).alias("bias_mean"),
        (pl.col("sharpe_survivors_only") - pl.col("sharpe_full")).alias("bias_sharpe"),
        pl.col("series").replace(FAMILIES).alias("family"),
    ).sort("series")


def delisted_names_by_year(store: PitStore) -> pl.DataFrame:
    """AC-W-03: universe-months per year held by names that later delisted."""
    universe = store.labels().universe_history()
    return (
        universe.with_columns(pl.col("asof").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("universe_months"),
            pl.col("delisted_after").is_not_null().sum().alias("later_delisted_months"),
            pl.col("security_id")
            .filter(pl.col("delisted_after").is_not_null())
            .n_unique()
            .alias("later_delisted_names"),
        )
        .sort("year")
    )
