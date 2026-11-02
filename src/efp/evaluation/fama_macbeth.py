"""Fama-MacBeth regressions with Newey-West errors (FR-19)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import polars as pl
from linearmodels import FamaMacBeth

from efp.evaluation.ic import default_lags


@dataclass(frozen=True)
class FMResult:
    """Time-series means of cross-sectional slopes."""

    names: tuple[str, ...]
    coefficients: tuple[float, ...]
    t_stats: tuple[float, ...]
    periods: int
    lags: int


def _complete(panel: pl.DataFrame, y: str, x: Sequence[str]) -> pl.DataFrame:
    """Rows with every variable, in dates that can identify every slope."""
    needed = [y, *x]
    rows = panel.select("asof", "security_id", *needed).drop_nulls(needed)
    usable = rows.filter(pl.all_horizontal(pl.col(name).is_finite() for name in needed))
    counts = usable.group_by("asof").agg(pl.len().alias("names"))
    enough = counts.filter(pl.col("names") > len(x) + 1).select("asof")
    return usable.join(enough, on="asof", how="inner").sort("asof", "security_id")


def fama_macbeth(
    panel: pl.DataFrame, y: str, x: Sequence[str], nw_lags: int | None = None
) -> FMResult:
    """Regress ``y`` on ``x`` per date through ``linearmodels``.

    Args:
        panel: ``asof``, ``security_id``, ``y`` and each ``x``.
        y: dependent column.
        x: regressors; a constant is added.
        nw_lags: Bartlett bandwidth; by default ``floor(4 (T / 100) ^ (2 / 9))``.
    """
    rows = _complete(panel, y, x)
    periods = rows["asof"].n_unique()
    lags = default_lags(periods) if nw_lags is None else nw_lags
    frame = rows.to_pandas()
    frame["asof"] = pd.to_datetime(frame["asof"])
    indexed = frame.set_index(["security_id", "asof"])
    design = indexed[list(x)].assign(const=1.0)
    fitted = FamaMacBeth(indexed[y], design).fit(
        cov_type="kernel", kernel="bartlett", bandwidth=lags
    )
    names = ("const", *x)
    return FMResult(
        names=names,
        coefficients=tuple(float(fitted.params[name]) for name in names),
        t_stats=tuple(float(fitted.tstats[name]) for name in names),
        periods=periods,
        lags=lags,
    )


def fama_macbeth_manual(
    panel: pl.DataFrame, y: str, x: Sequence[str], nw_lags: int | None = None
) -> FMResult:
    """The same estimator by hand: two passes, then Newey-West on the slope series."""
    rows = _complete(panel, y, x)
    slopes: list[np.ndarray] = []
    for _, part in rows.group_by("asof", maintain_order=True):
        design = np.column_stack([np.ones(part.height), *[part[name].to_numpy() for name in x]])
        solved, *_ = np.linalg.lstsq(design, part[y].to_numpy(), rcond=None)
        slopes.append(solved)
    matrix = np.vstack(slopes)
    periods = matrix.shape[0]
    lags = default_lags(periods) if nw_lags is None else nw_lags
    means = matrix.mean(axis=0)
    centred = matrix - means
    long_run = centred.T @ centred / periods
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma = centred[lag:].T @ centred[:-lag] / periods
        long_run += weight * (gamma + gamma.T)
    errors = np.sqrt(np.diag(long_run) / (periods - 1))
    return FMResult(
        names=("const", *x),
        coefficients=tuple(means.tolist()),
        t_stats=tuple((means / errors).tolist()),
        periods=periods,
        lags=lags,
    )
