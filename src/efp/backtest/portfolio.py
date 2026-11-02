"""Target weights from composite scores: dollar neutral, gross leverage 2.0."""

from __future__ import annotations

from typing import Literal

import polars as pl

Construction = Literal["decile", "signal_weighted"]


def target_weights(
    scores: pl.DataFrame,
    construction: Construction = "decile",
    gross: float = 2.0,
    quantiles: int = 10,
) -> pl.DataFrame:
    """Weights per (``asof``, ``security_id``) summing to zero with ``gross`` total size.

    ``decile`` holds the top and bottom deciles, equal weight within each leg.
    ``signal_weighted`` holds every name in proportion to its cross-sectionally demeaned score.
    """
    side = gross / 2.0
    if construction == "signal_weighted":
        centred = scores.with_columns(
            (pl.col("score") - pl.col("score").mean().over("asof")).alias("tilt")
        )
        size = pl.when(pl.col("tilt") > 0).then(pl.col("tilt")).otherwise(0.0).sum().over("asof")
        return centred.filter(pl.col("tilt").ne(0.0)).select(
            "asof", "security_id", (pl.col("tilt") / size * side).alias("weight")
        )
    ranked = scores.with_columns(
        (pl.col("score").rank("ordinal").over("asof") - 1).alias("position"),
        pl.len().over("asof").alias("names"),
    )
    cut = (pl.col("names") / quantiles).floor().cast(pl.Int64).clip(1)
    long = ranked.filter(pl.col("position") >= pl.col("names") - cut).with_columns(
        (side / pl.len().over("asof")).alias("weight")
    )
    short = ranked.filter(pl.col("position") < cut).with_columns(
        (-side / pl.len().over("asof")).alias("weight")
    )
    return (
        pl.concat([long, short], how="vertical")
        .select("asof", "security_id", "weight")
        .sort("asof", "security_id")
    )
