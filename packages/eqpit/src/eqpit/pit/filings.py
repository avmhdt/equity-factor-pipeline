"""Filings and the instant each became public (FR-07, DQ-02, DQ-03).

``available_at`` is the only availability clock. A fiscal period end never is one.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from eqpit.calendar import close_after, closes_on, local_date
from eqpit.pit.policy import PitPolicy
from eqpit.schemas import FILINGS, INSTANT, conform

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F")


class PeriodEndAvailabilityError(ValueError):
    """A row would be available on or before its fiscal period end (DQ-03)."""


def stamp_availability(frame: pl.DataFrame, policy: PitPolicy) -> pl.DataFrame:
    """Add ``available_at`` and ``pit_method`` per DQ-02 and DQ-03.

    Args:
        frame: rows with ``form``, ``period_end`` and, when known, ``acceptance_dt`` (UTC),
            ``filed`` and ``rdq``.
        policy: the extra lag and fallback lags.

    Returns:
        ``frame`` plus both columns. Fallback order: acceptance, filing date, report date,
        conservative lag after the period end.
    """
    columns: dict[str, Any] = {
        "acceptance_dt": INSTANT,
        "filed": pl.Date,
        "rdq": pl.Date,
    }
    work = frame.with_columns(
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in columns.items()
        if name not in frame.columns
    )
    lag = policy.extra_lag_trading_days
    accepted_on = work.select(local_date(pl.col("acceptance_dt")).alias("day")).to_series()
    annual = work["form"].is_in(ANNUAL_FORMS).fill_null(False)
    months = pl.Series(
        "months",
        [
            policy.annual_lag_months if flag else policy.quarterly_lag_months
            for flag in annual.to_list()
        ],
        dtype=pl.Int32,
    )
    lagged_end = work.select(
        pl.col("period_end").dt.offset_by(pl.format("{}mo", pl.lit(months))).alias("day")
    ).to_series()
    work = work.with_columns(
        (
            pl.col("acceptance_dt")
            if lag == 0
            else close_after(accepted_on, lag).alias("acceptance_dt")
        ).alias("_by_acceptance"),
        close_after(work["filed"], max(lag, 1)).alias("_by_filed"),
        close_after(work["rdq"], max(lag, 1)).alias("_by_rdq"),
        closes_on(lagged_end).alias("_by_lag"),
    )
    return work.with_columns(
        pl.coalesce("_by_acceptance", "_by_filed", "_by_rdq", "_by_lag").alias("available_at"),
        pl.when(pl.col("_by_acceptance").is_not_null())
        .then(pl.lit("acceptance"))
        .when(pl.col("_by_filed").is_not_null())
        .then(pl.lit("filedate"))
        .when(pl.col("_by_rdq").is_not_null())
        .then(pl.lit("rdq"))
        .otherwise(pl.lit("conservative_lag"))
        .alias("pit_method"),
    ).drop("_by_acceptance", "_by_filed", "_by_rdq", "_by_lag")


def assert_after_period_end(frame: pl.DataFrame) -> None:
    """Raise if any row's ``available_at`` falls on or before its ``period_end``."""
    bad = frame.filter(local_date(pl.col("available_at")) <= pl.col("period_end"))
    if not bad.is_empty():
        raise PeriodEndAvailabilityError(f"{bad.height} rows available by their period end")


def build_filings(raw: pl.DataFrame, policy: PitPolicy) -> pl.DataFrame:
    """Build the ``filings`` table from normalised filing rows."""
    wanted = raw.filter(pl.col("form").is_in(FORMS)).unique(subset=["accession"], keep="first")
    stamped = stamp_availability(wanted, policy)
    assert_after_period_end(stamped.drop_nulls("period_end"))
    return conform(stamped, FILINGS).sort("cik", "accession")
