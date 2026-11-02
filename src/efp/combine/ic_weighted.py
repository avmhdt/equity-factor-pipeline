"""IC-weighted composite: trailing mean IC, floored at zero."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl


def trailing_weights(
    ic: pl.DataFrame,
    names: list[str],
    months: list[date],
    window: int,
    purge: int,
) -> pl.DataFrame:
    """Signal weights per date from ICs whose return period had ended by then.

    An IC dated ``s`` measures the return from ``s`` to ``s + 1``, so at date ``T`` it is known
    once ``s <= T - purge`` months. Negative means floor at zero. With no positive mean the
    weights fall back to equal.

    Args:
        ic: ``signal``, ``asof``, ``ic``.
        names: signals, in column order.
        months: every rebalance date, sorted.
        window: trailing months.
        purge: months between the last IC used and ``T``.
    """
    position = {month: index for index, month in enumerate(months)}
    matrix = np.full((len(months), len(names)), np.nan)
    column = {name: index for index, name in enumerate(names)}
    for row in ic.iter_rows(named=True):
        if row["signal"] in column and row["asof"] in position:
            matrix[position[row["asof"]], column[row["signal"]]] = row["ic"]
    rows: list[dict[str, object]] = []
    for index, month in enumerate(months):
        last = index - purge
        first = max(last - window + 1, 0)
        history = matrix[first : last + 1] if last >= 0 else matrix[:0]
        if history.size == 0 or np.isnan(history).all():
            continue
        counts = (~np.isnan(history)).sum(axis=0)
        totals = np.nansum(history, axis=0)
        means = np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)
        floored = np.maximum(means, 0.0)
        total = float(floored.sum())
        weights = floored / total if total > 0 else np.full(len(names), 1.0 / len(names))
        rows.extend(
            {"asof": month, "signal": name, "weight": float(weights[column[name]])}
            for name in names
        )
    return pl.DataFrame(rows, schema={"asof": pl.Date, "signal": pl.String, "weight": pl.Float64})


def apply_weights(panel: pl.DataFrame, weights: pl.DataFrame, score: str = "z") -> pl.DataFrame:
    """Weighted mean of the available scores per stock, weights renormalised over those."""
    joined = (
        panel.select("asof", "security_id", "signal", pl.col(score).alias("value"))
        .drop_nulls("value")
        .join(weights, on=["asof", "signal"], how="inner")
        .sort("asof", "security_id", "signal")
    )
    return (
        joined.group_by("asof", "security_id", maintain_order=True)
        .agg(
            (pl.col("value") * pl.col("weight")).sum().alias("numerator"),
            pl.col("weight").sum().alias("denominator"),
        )
        .filter(pl.col("denominator") > 0)
        .select("asof", "security_id", (pl.col("numerator") / pl.col("denominator")).alias("score"))
    )
