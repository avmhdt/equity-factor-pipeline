"""Delisting returns, imputed when missing (FR-06, DQ-08)."""

from __future__ import annotations

import polars as pl

from eqpit.calendar import closes_on
from eqpit.pit.policy import PitPolicy
from eqpit.schemas import DELISTINGS, conform

REASONS = frozenset({"merger", "exchange", "liquidation", "performance", "unknown"})
_PERFORMANCE_REASONS = ("performance", "liquidation")


def is_performance_code(code: pl.Expr) -> pl.Expr:
    """CRSP delisting codes 500 and 520 to 584 are performance-related."""
    return code.eq(500) | code.is_between(520, 584)


def build_delistings(raw: pl.DataFrame, policy: PitPolicy) -> pl.DataFrame:
    """Normalise delistings and fill ``dlret``.

    Args:
        raw: rows with ``security_id``, ``delist_date``, ``delist_code``, ``reason``,
            ``exchange_at_delist`` and ``dlret_raw``.
        policy: imputation returns and the zero-imputation switch.

    Returns:
        ``delistings``, with ``dlret`` taken from ``dlret_raw`` when present and otherwise
        imputed, and ``imputation_rule`` naming which rule applied.
    """
    unknown = sorted(set(raw["reason"].drop_nulls().to_list()) - REASONS)
    if unknown:
        raise ValueError(f"delisting reasons outside the normalised set: {unknown}")
    performance = pl.col("reason").is_in(_PERFORMANCE_REASONS).fill_null(False) | (
        is_performance_code(pl.col("delist_code")).fill_null(False)
    )
    nyse_amex = pl.col("exchange_at_delist").is_in(["NYSE", "AMEX"]).fill_null(False)
    missing = pl.col("dlret_raw").is_null()
    if policy.zero_imputation:
        rule = pl.when(missing).then(pl.lit("zero")).otherwise(pl.lit("none"))
        value = pl.col("dlret_raw").fill_null(0.0)
    else:
        rule = (
            pl.when(~missing)
            .then(pl.lit("none"))
            .when(performance & nyse_amex)
            .then(pl.lit("shumway_nyse_amex"))
            .when(performance)
            .then(pl.lit("shumway_nasdaq"))
            .otherwise(pl.lit("zero"))
        )
        value = (
            pl.when(~missing)
            .then(pl.col("dlret_raw"))
            .when(performance & nyse_amex)
            .then(policy.imputation_nyse_amex)
            .when(performance)
            .then(policy.imputation_nasdaq)
            .otherwise(policy.imputation_other)
        )
    built = raw.with_columns(
        value.alias("dlret"),
        rule.alias("imputation_rule"),
        closes_on(raw["delist_date"]).alias("available_at"),
    )
    return conform(built, DELISTINGS).sort("security_id", "delist_date")
