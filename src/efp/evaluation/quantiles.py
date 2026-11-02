"""Quantile portfolios and top-minus-bottom spreads (FR-18)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import polars as pl


def quantile_spreads(
    signals: pl.DataFrame,
    fwd: pl.DataFrame,
    q: int = 10,
    weighting: Literal["ew", "vw"] = "ew",
    breakpoints: Literal["nyse", "all"] = "nyse",
    score: str = "z",
) -> pl.DataFrame:
    """Return per quantile and the spread, one month after each rebalance.

    Args:
        signals: ``asof``, ``security_id``, ``signal``, the score, ``exchange`` and ``mcap``.
        fwd: forward returns with ``h`` = 1.
        q: number of quantiles.
        weighting: equal or by market cap at the rebalance.
        breakpoints: NYSE names only, or every name.
        score: column to sort.

    Returns:
        ``signal``, ``asof``, ``quantile`` and ``ret``. Quantile 0 holds top minus bottom. A
        discrete score such as the F-score leaves some quantiles empty: only filled ones appear,
        and the spread runs from the lowest filled quantile to the highest.
    """
    labels = fwd.filter(pl.col("h").eq(1)).select("asof", "security_id", "fwd_ret")
    joined = (
        signals.select(
            "signal", "asof", "security_id", pl.col(score).alias("score"), "exchange", "mcap"
        )
        .drop_nulls("score")
        .join(labels, on=["asof", "security_id"], how="inner")
        .drop_nulls("fwd_ret")
        .sort("signal", "asof", "security_id")
    )
    edges = np.linspace(0.0, 1.0, q + 1)[1:-1]
    frames: list[pl.DataFrame] = []
    for (name, asof), part in joined.group_by(["signal", "asof"], maintain_order=True):
        reference = part if breakpoints == "all" else part.filter(pl.col("exchange").eq("NYSE"))
        if reference.height < q:
            continue
        cuts = np.quantile(reference["score"].to_numpy(), edges)
        buckets = np.searchsorted(cuts, part["score"].to_numpy(), side="right") + 1
        weights = (
            np.ones(part.height) if weighting == "ew" else part["mcap"].fill_null(0.0).to_numpy()
        )
        returns = part["fwd_ret"].to_numpy()
        totals = np.bincount(buckets, weights=weights, minlength=q + 1)[1:]
        earned = np.bincount(buckets, weights=weights * returns, minlength=q + 1)[1:]
        filled = np.flatnonzero(totals > 0)
        if len(filled) < 2:
            continue
        means = earned[filled] / totals[filled]
        count = len(filled) + 1
        frames.append(
            pl.DataFrame(
                {
                    "signal": [name] * count,
                    "asof": [asof] * count,
                    "quantile": [0, *(filled + 1).tolist()],
                    "ret": [float(means[-1] - means[0]), *means.tolist()],
                },
                schema={
                    "signal": pl.String,
                    "asof": pl.Date,
                    "quantile": pl.Int16,
                    "ret": pl.Float64,
                },
            )
        )
    if not frames:
        return pl.DataFrame(
            schema={
                "signal": pl.String,
                "asof": pl.Date,
                "quantile": pl.Int16,
                "ret": pl.Float64,
            }
        )
    return pl.concat(frames, how="vertical")
