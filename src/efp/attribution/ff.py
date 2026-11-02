"""FF5 plus momentum attribution with Newey-West errors (FR-25)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
import statsmodels.api as sm

from efp.evaluation.ic import default_lags

FACTORS = ("mkt_rf", "smb", "hml", "rmw", "cma", "umd")


@dataclass(frozen=True)
class AttributionResult:
    """Alpha and loadings of one return series."""

    alpha_annual: float
    alpha_t: float
    loadings: dict[str, float]
    loading_t: dict[str, float]
    r_squared: float
    months: int
    lags: int


def attribute(
    returns: pl.DataFrame,
    factors: pl.DataFrame,
    nw_lags: int | None = None,
    column: str = "ret",
) -> AttributionResult:
    """Regress a long-short series on the six factors.

    Args:
        returns: ``month`` and the return column, in decimals.
        factors: ``month`` and the six factor columns, in decimals.
        nw_lags: HAC lags; by default ``floor(4 (T / 100) ^ (2 / 9))``.
        column: the series to explain.
    """
    joined = (
        returns.select("month", pl.col(column).alias("target"))
        .join(factors.select("month", *FACTORS), on="month", how="inner")
        .drop_nulls()
        .sort("month")
    )
    months = joined.height
    lags = default_lags(months) if nw_lags is None else nw_lags
    design = np.column_stack([np.ones(months), *[joined[name].to_numpy() for name in FACTORS]])
    fitted = sm.OLS(joined["target"].to_numpy(), design).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags, "use_correction": False}
    )
    params = np.asarray(fitted.params)
    tvalues = np.asarray(fitted.tvalues)
    return AttributionResult(
        alpha_annual=float(params[0]) * 12.0,
        alpha_t=float(tvalues[0]),
        loadings={name: float(params[index + 1]) for index, name in enumerate(FACTORS)},
        loading_t={name: float(tvalues[index + 1]) for index, name in enumerate(FACTORS)},
        r_squared=float(fitted.rsquared),
        months=months,
        lags=lags,
    )
