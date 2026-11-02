"""Cross-sectional transforms (FR-15, DQ-11)."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl


def winsorize(
    df: pl.DataFrame,
    col: str,
    lower: float = 0.01,
    upper: float = 0.99,
    by: str | Sequence[str] = "asof",
) -> pl.DataFrame:
    """Add ``winsorized``: ``col`` clipped to its percentiles within each group.

    The bounds are sample values, not interpolations between them. An interpolated bound
    moves again on a second pass, so winsorizing twice would differ from once.
    """
    keys = [by] if isinstance(by, str) else list(by)
    low = pl.col(col).quantile(lower, interpolation="lower").over(keys)
    high = pl.col(col).quantile(upper, interpolation="higher").over(keys)
    return df.with_columns(pl.col(col).clip(low, high).alias("winsorized"))


def zscore(
    df: pl.DataFrame, col: str, by: str | Sequence[str] = "asof", alias: str = "z"
) -> pl.DataFrame:
    """Add a cross-sectional z-score with unit sample standard deviation."""
    keys = [by] if isinstance(by, str) else list(by)
    deviation = pl.col(col).std().over(keys)
    return df.with_columns(
        pl.when(deviation > 0)
        .then((pl.col(col) - pl.col(col).mean().over(keys)) / deviation)
        .alias(alias)
    )
