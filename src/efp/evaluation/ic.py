"""Rank information coefficients (FR-17)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl
from numpy.typing import NDArray


def default_lags(periods: int) -> int:
    """Newey-West lag rule ``floor(4 (T / 100) ^ (2 / 9))``."""
    return int(4.0 * (periods / 100.0) ** (2.0 / 9.0))


def newey_west_se(series: NDArray[np.float64], lags: int) -> float:
    """Newey-West standard error of a sample mean, with Bartlett weights."""
    count = len(series)
    deviations = series - series.mean()
    long_run = float(deviations @ deviations) / count
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        long_run += 2.0 * weight * float(deviations[lag:] @ deviations[:-lag]) / count
    return math.sqrt(long_run / count)


def newey_west_t(series: NDArray[np.float64], lags: int | None = None) -> float:
    """t-statistic of a mean with Newey-West errors; automatic lags by default."""
    chosen = default_lags(len(series)) if lags is None else lags
    error = newey_west_se(series, chosen)
    return float(series.mean()) / error if error > 0 else float("nan")


def rank_ic(
    signals: pl.DataFrame, fwd: pl.DataFrame, horizon: int = 1, score: str = "z"
) -> pl.DataFrame:
    """Spearman correlation of score and forward return per (signal, asof).

    Args:
        signals: ``asof``, ``security_id``, ``signal`` and the score column.
        fwd: ``asof``, ``security_id``, ``h``, ``fwd_ret``.
        horizon: months ahead.
        score: column to rank.
    """
    labels = fwd.filter(pl.col("h").eq(horizon)).select("asof", "security_id", "fwd_ret")
    joined = (
        signals.select("signal", "asof", "security_id", pl.col(score).alias("score"))
        .drop_nulls("score")
        .join(labels, on=["asof", "security_id"], how="inner")
        .drop_nulls("fwd_ret")
        .sort("signal", "asof", "security_id")
    )
    return (
        joined.group_by("signal", "asof", maintain_order=True)
        .agg(
            pl.corr(pl.col("score").rank("average"), pl.col("fwd_ret").rank("average")).alias("ic"),
            pl.len().alias("names"),
        )
        .drop_nulls("ic")
        .filter(pl.col("ic").is_finite())
    )


@dataclass(frozen=True)
class ICSummary:
    """Summary of one signal's IC series."""

    mean_ic: float
    std_ic: float
    icir: float
    icir_annual: float
    t_nw: float
    hit_rate: float
    months: int


def ic_summary(ic: pl.DataFrame, nw_lags: int | None = None) -> ICSummary:
    """Mean, std, ICIR (monthly and annualised), Newey-West t and hit rate of one signal."""
    series = ic.sort("asof")["ic"].to_numpy()
    mean = float(series.mean())
    deviation = float(series.std(ddof=1)) if len(series) > 1 else float("nan")
    icir = mean / deviation if deviation > 0 else float("nan")
    return ICSummary(
        mean_ic=mean,
        std_ic=deviation,
        icir=icir,
        icir_annual=icir * math.sqrt(12.0),
        t_nw=newey_west_t(series, nw_lags),
        hit_rate=float((series > 0).mean()),
        months=len(series),
    )
