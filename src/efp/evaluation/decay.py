"""Decay curves (FR-20): IC by horizon, and IC of signals held back by a lag."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from efp.evaluation.ic import rank_ic


def decay_curve(
    signals: pl.DataFrame, fwd: pl.DataFrame, horizons: Sequence[int], score: str = "z"
) -> pl.DataFrame:
    """Mean rank IC per signal at each forward horizon."""
    frames: list[pl.DataFrame] = []
    for horizon in horizons:
        ic = rank_ic(signals, fwd, horizon, score)
        frames.append(
            ic.group_by("signal", maintain_order=True)
            .agg(pl.col("ic").mean().alias("mean_ic"), pl.len().alias("months"))
            .with_columns(pl.lit(horizon, dtype=pl.Int16).alias("horizon"))
        )
    return pl.concat(frames, how="vertical").select("signal", "horizon", "mean_ic", "months")


def lagged_ic(
    signals: pl.DataFrame, fwd: pl.DataFrame, lags: Sequence[int], score: str = "z"
) -> pl.DataFrame:
    """Mean one-month rank IC of the signal formed ``lag`` rebalances earlier."""
    dates = signals["asof"].unique().sort().to_list()
    frames: list[pl.DataFrame] = []
    for lag in lags:
        shifted = pl.DataFrame(
            {"asof": dates[:-lag] if lag else dates, "later": dates[lag:]},
            schema={"asof": pl.Date, "later": pl.Date},
        )
        moved = signals.join(shifted, on="asof", how="inner").drop("asof").rename({"later": "asof"})
        ic = rank_ic(moved, fwd, 1, score)
        frames.append(
            ic.group_by("signal", maintain_order=True)
            .agg(pl.col("ic").mean().alias("mean_ic"), pl.len().alias("months"))
            .with_columns(pl.lit(lag, dtype=pl.Int16).alias("lag"))
        )
    return pl.concat(frames, how="vertical").select("signal", "lag", "mean_ic", "months")
