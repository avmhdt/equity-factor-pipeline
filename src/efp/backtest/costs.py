"""Trading inputs per stock and rebalance: spread, volatility, ADV and borrow (D-06)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import polars as pl
from numpy.typing import NDArray

from efp.backtest.spread_table import size_bucket_half_spread

SpreadModel = Literal["corwin_schultz", "abdi_ranaldo", "size_bucket"]
SPREAD_MODELS: tuple[SpreadModel, ...] = ("corwin_schultz", "abdi_ranaldo", "size_bucket")
_SQRT2 = math.sqrt(2.0)
_DENOMINATOR = 3.0 - 2.0 * _SQRT2


@dataclass(frozen=True)
class CostConfig:
    """D-06: modelling assumptions, not measurements.

    Args:
        floor_bps: smallest half-spread.
        impact_coefficient: square-root impact coefficient.
        adv_days: window for average daily dollar volume.
        vol_days: window for daily volatility.
        borrow_tiers: ``(max_rank, rate per year)``, rates in percent, ascending rank.
        borrow_rest: rate per year in percent for every other rank.
        spread_model: half-spread estimator; D-06 sets Corwin-Schultz for headline results.
    """

    floor_bps: float = 2.0
    impact_coefficient: float = 1.0
    adv_days: int = 20
    vol_days: int = 20
    borrow_tiers: tuple[tuple[int, float], ...] = ((500, 0.25), (1000, 1.0))
    borrow_rest: float = 5.0
    spread_model: SpreadModel = "corwin_schultz"


def corwin_schultz(high: NDArray[np.float64], low: NDArray[np.float64]) -> float:
    """Mean two-day Corwin-Schultz spread for one stock, negative estimates set to zero.

    Args:
        high: split-adjusted daily highs, oldest first.
        low: split-adjusted daily lows.
    """
    usable = (high > 0) & (low > 0)
    if usable.sum() < 2:
        return float("nan")
    ratio = np.log(high[usable] / low[usable]) ** 2
    two_day_high = np.maximum(high[usable][1:], high[usable][:-1])
    two_day_low = np.minimum(low[usable][1:], low[usable][:-1])
    gamma = np.log(two_day_high / two_day_low) ** 2
    beta = ratio[1:] + ratio[:-1]
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / _DENOMINATOR - np.sqrt(gamma / _DENOMINATOR)
    spreads = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return float(np.maximum(spreads, 0.0).mean())


def _corwin_schultz_spread() -> pl.Expr:
    squared = (pl.col("_high") / pl.col("_low")).log() ** 2
    two_high = pl.max_horizontal(pl.col("_high"), pl.col("_high").shift(1).over("security_id"))
    two_low = pl.min_horizontal(pl.col("_low"), pl.col("_low").shift(1).over("security_id"))
    gamma = (two_high / two_low).log() ** 2
    beta = squared + squared.shift(1).over("security_id")
    alpha = ((2.0 * beta).sqrt() - beta.sqrt()) / _DENOMINATOR - (gamma / _DENOMINATOR).sqrt()
    return (2.0 * (alpha.exp() - 1.0) / (1.0 + alpha.exp())).clip(0.0)


def _abdi_ranaldo_spread() -> pl.Expr:
    """The two-day corrected estimator: negative two-day squares go to zero before the root.

    Each value is stamped on the second of its two days, so a month end sees no later price.
    """
    close = (pl.col("close") * pl.col("split_factor")).log()
    middle = (pl.col("_high").log() + pl.col("_low").log()) / 2.0
    prior_close = close.shift(1).over("security_id")
    prior_middle = middle.shift(1).over("security_id")
    squared = 4.0 * (prior_close - prior_middle) * (prior_close - middle)
    return squared.clip(0.0).sqrt()


def trading_inputs(prices: pl.DataFrame, ends: list[date], config: CostConfig) -> pl.DataFrame:
    """``asof``, ``security_id``, ``half_spread``, ``daily_vol``, ``adv`` at each month end.

    Each uses only the sessions up to and including that month end.
    """
    ordered = prices.sort("security_id", "date").with_columns(
        pl.col("ret")
        .rolling_std(config.vol_days, min_samples=10)
        .over("security_id")
        .alias("daily_vol"),
        pl.col("dollar_volume")
        .rolling_mean(config.adv_days, min_samples=10)
        .over("security_id")
        .alias("adv"),
        (pl.col("high") * pl.col("split_factor")).alias("_high"),
        (pl.col("low") * pl.col("split_factor")).alias("_low"),
    )
    floor = config.floor_bps / 10_000.0
    if config.spread_model == "size_bucket":
        half = size_bucket_half_spread(pl.col("mcap")).alias("half_spread")
        estimated = ordered.with_columns(half)
    else:
        daily = (
            _abdi_ranaldo_spread()
            if config.spread_model == "abdi_ranaldo"
            else _corwin_schultz_spread()
        )
        estimated = ordered.with_columns(
            (
                daily.rolling_mean(config.vol_days, min_samples=10)
                .over("security_id")
                .fill_null(0.0)
                .fill_nan(0.0)
                / 2.0
            ).alias("half_spread")
        )
    return estimated.filter(pl.col("date").is_in(ends)).select(
        pl.col("date").alias("asof"),
        "security_id",
        pl.col("half_spread").clip(floor),
        pl.col("daily_vol").fill_nan(None),
        "adv",
    )


def borrow_rates(rank: pl.Expr, config: CostConfig) -> pl.Expr:
    """Annual borrow fee in decimals for a universe rank (D-06)."""
    expression: pl.Expr = pl.lit(config.borrow_rest / 100.0)
    for limit, rate in reversed(config.borrow_tiers):
        expression = pl.when(rank <= limit).then(pl.lit(rate / 100.0)).otherwise(expression)
    return expression
