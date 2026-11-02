"""Turnover (FR-20): rank autocorrelation of a signal and one-sided portfolio turnover."""

from __future__ import annotations

import polars as pl


def rank_autocorrelation(signals: pl.DataFrame, score: str = "z") -> pl.DataFrame:
    """Spearman correlation of each signal with itself one rebalance earlier."""
    dates = signals.select(pl.col("asof").unique().sort())
    previous = dates.with_columns(pl.col("asof").shift(1).alias("before")).drop_nulls("before")
    earlier = signals.select(
        "signal",
        pl.col("asof").alias("before"),
        "security_id",
        pl.col(score).alias("earlier"),
    )
    joined = (
        signals.select("signal", "asof", "security_id", pl.col(score).alias("now"))
        .join(previous, on="asof", how="inner")
        .join(earlier, on=["signal", "before", "security_id"], how="inner")
        .drop_nulls(["now", "earlier"])
        .sort("signal", "asof", "security_id")
    )
    return joined.group_by("signal", "asof", maintain_order=True).agg(
        pl.corr(pl.col("now").rank("average"), pl.col("earlier").rank("average")).alias(
            "autocorrelation"
        )
    )


def decile_weights(signals: pl.DataFrame, score: str = "z", q: int = 10) -> pl.DataFrame:
    """Equal weights in the top and bottom quantile, each leg summing to one in size."""
    ranked = signals.drop_nulls(score).with_columns(
        (pl.col(score).rank("ordinal").over("signal", "asof") - 1)
        .truediv(pl.len().over("signal", "asof"))
        .alias("percentile")
    )
    long = ranked.filter(pl.col("percentile") >= 1.0 - 1.0 / q).with_columns(
        (1.0 / pl.len().over("signal", "asof")).alias("weight")
    )
    short = ranked.filter(pl.col("percentile") < 1.0 / q).with_columns(
        (-1.0 / pl.len().over("signal", "asof")).alias("weight")
    )
    return pl.concat([long, short], how="vertical").select(
        "signal", "asof", "security_id", "weight"
    )


def turnover(weights: pl.DataFrame, returns: pl.DataFrame | None = None) -> pl.DataFrame:
    """One-sided turnover ``0.5 * sum |w_t - drifted w_(t-1)|`` per rebalance.

    Args:
        weights: ``signal``, ``asof``, ``security_id``, ``weight``.
        returns: optional ``asof``, ``security_id``, ``fwd_ret`` over the month after ``asof``.
            With them, last month's weights drift with their returns before the comparison.
    """
    dates = weights.select(pl.col("asof").unique().sort())
    following = dates.with_columns(pl.col("asof").shift(-1).alias("next")).drop_nulls("next")
    held = weights.join(following, on="asof", how="inner")
    if returns is None:
        drifted = held.with_columns(pl.col("weight").alias("carried"))
    else:
        earned = held.join(
            returns.select("asof", "security_id", "fwd_ret"),
            on=["asof", "security_id"],
            how="left",
        ).with_columns((pl.col("weight") * (1.0 + pl.col("fwd_ret").fill_null(0.0))).alias("grown"))
        drifted = earned.with_columns(
            (
                pl.col("grown")
                / pl.col("grown").abs().sum().over("signal", "asof")
                * pl.col("weight").abs().sum().over("signal", "asof")
            ).alias("carried")
        )
    last = drifted.select("signal", pl.col("next").alias("asof"), "security_id", "carried")
    both = weights.join(last, on=["signal", "asof", "security_id"], how="full", coalesce=True)
    changes = both.with_columns(
        (pl.col("weight").fill_null(0.0) - pl.col("carried").fill_null(0.0)).abs().alias("change")
    )
    first = weights.group_by("signal").agg(pl.col("asof").min().alias("first"))
    return (
        changes.join(first, on="signal")
        .filter(pl.col("asof") > pl.col("first"))
        .sort("signal", "asof", "security_id")
        .group_by("signal", "asof", maintain_order=True)
        .agg((0.5 * pl.col("change").sum()).alias("turnover"))
    )
