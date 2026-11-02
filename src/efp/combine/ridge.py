"""Ridge composite: expanding window, purged, tuned by time-series cross-validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import TimeSeriesSplit


@dataclass(frozen=True)
class RidgeFit:
    """One training run."""

    asof: date
    trained_through: date
    alpha: float
    coefficients: tuple[float, ...]


def _matrix(frame: pl.DataFrame, names: list[str]) -> NDArray[np.float64]:
    return frame.select(pl.col(name).fill_null(0.0) for name in names).to_numpy()


def fit_at(
    wide: pl.DataFrame,
    names: list[str],
    months: list[date],
    index: int,
    alphas: tuple[float, ...],
    purge: int,
    min_train: int,
) -> RidgeFit | None:
    """Fit on pairs whose one-month return had ended by ``months[index]``.

    Args:
        wide: ``asof``, one column per signal and ``target``, the return from ``asof`` to the
            next month end, demeaned per date.
        names: signal columns.
        months: rebalance dates, sorted.
        index: position of the fitting date.
        alphas: ridge penalties.
        purge: months dropped before the fitting date (DQ-13).
        min_train: months needed.
    """
    last = index - purge
    if last + 1 < min_train:
        return None
    window = months[: last + 1]
    train = wide.filter(pl.col("asof").is_in(window)).sort("asof", "security_id")
    groups = train["asof"].to_physical().to_numpy()
    unique, sizes = np.unique(groups, return_counts=True)
    edges = np.cumsum(sizes)
    splitter = TimeSeriesSplit(n_splits=5)
    folds = [
        (
            np.arange(edges[train_index[-1]]),
            np.arange(edges[test_index[0] - 1], edges[test_index[-1]]),
        )
        for train_index, test_index in splitter.split(unique)
    ]
    model = RidgeCV(alphas=alphas, fit_intercept=False, cv=folds)
    model.fit(_matrix(train, names), train["target"].to_numpy())
    return RidgeFit(
        months[index],
        window[-1],
        float(model.alpha_),
        tuple(float(value) for value in model.coef_),
    )


def score_with(frame: pl.DataFrame, names: list[str], fit: RidgeFit) -> pl.DataFrame:
    """Apply a fit to one date's rows."""
    values = _matrix(frame, names) @ np.array(fit.coefficients)
    return frame.select("asof", "security_id").with_columns(pl.Series("score", values))
