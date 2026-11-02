"""Neutralization to sector, size and beta by cross-sectional OLS residuals (FR-16)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import polars as pl

Mode = Literal["none", "sector", "sector_size_beta"]
SPANNED = 1e-8
"""Below this share of the input's deviation, residuals are rounding error, not a signal. An
industry-level signal such as ``ind_mom`` is spanned by the sector dummies."""


def _residuals(y: np.ndarray, sectors: np.ndarray, others: list[np.ndarray]) -> np.ndarray:
    """Residuals of ``y`` on sector dummies and other exposures, then rescaled to unit std."""
    labels = np.unique(sectors)
    dummies = (sectors[:, None] == labels[None, :]).astype(np.float64)
    design = np.column_stack([dummies, *others]) if others else dummies
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = y - design @ coefficients
    deviation = float(fitted.std(ddof=1))
    if deviation <= SPANNED * float(y.std(ddof=1)):
        return np.full(len(y), np.nan)
    standardised: np.ndarray = (fitted - fitted.mean()) / deviation
    return standardised


def neutralize(df: pl.DataFrame, col: str, mode: Mode, exempt_size: bool = False) -> pl.DataFrame:
    """Add ``z_neutral``, re-standardised residuals per ``asof``.

    Args:
        df: rows with ``asof``, ``col``, and for the wanted mode ``ff49``, ``log_size``, ``beta``.
        col: column to neutralize, usually ``z``.
        mode: ``none`` copies the column; ``sector`` removes FF-49 dummies; ``sector_size_beta``
            also removes log market cap and beta.
        exempt_size: leave log market cap out; the size signal is its own exposure.

    Rows missing an exposure get a null, as does a date whose exposures span the signal.
    Output order follows ``asof`` then the input order.
    """
    if mode == "none":
        return df.with_columns(pl.col(col).alias("z_neutral"))
    needed = [col, "ff49"]
    exposures: list[str] = []
    if mode == "sector_size_beta":
        exposures = ["beta"] if exempt_size else ["log_size", "beta"]
    needed += exposures
    numbered = df.with_row_index("_row")
    usable = numbered.drop_nulls(needed).filter(
        pl.all_horizontal(pl.col(name).is_finite() for name in [col, *exposures])
    )
    rows: list[np.ndarray] = []
    values: list[np.ndarray] = []
    for _, part in usable.group_by("asof", maintain_order=True):
        if part.height <= part["ff49"].n_unique() + len(exposures) + 1:
            continue
        residuals = _residuals(
            part[col].to_numpy(),
            part["ff49"].to_numpy(),
            [part[name].to_numpy() for name in exposures],
        )
        rows.append(part["_row"].to_numpy())
        values.append(residuals)
    filled = pl.DataFrame(
        {
            "_row": np.concatenate(rows) if rows else np.array([], dtype=np.uint32),
            "z_neutral": np.concatenate(values) if values else np.array([], dtype=np.float64),
        },
        schema={"_row": pl.UInt32, "z_neutral": pl.Float64},
    )
    return (
        numbered.join(filled, on="_row", how="left")
        .sort("_row")
        .drop("_row")
        .with_columns(pl.col("z_neutral").fill_nan(None))
    )
