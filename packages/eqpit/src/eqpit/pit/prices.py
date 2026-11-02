"""Daily prices and total returns (FR-05, DQ-09, DQ-10)."""

from __future__ import annotations

import polars as pl

from eqpit.calendar import closes_on
from eqpit.schemas import PRICES_DAILY, conform

FLAG_MIDPOINT = 1
FLAG_STALE = 2
FLAG_EXTREME = 4

STALE_ZERO_VOLUME_DAYS = 5
EXTREME_RETURN = 1.0

_SPLIT_ACTIONS = ("split", "stock_dividend")
_CASH_ACTIONS = ("cash_dividend", "spinoff")


def _daily_action_terms(prices: pl.DataFrame, actions: pl.DataFrame) -> pl.DataFrame:
    """Split ratio and cash per share by (security, trading date).

    An ex-date with no price row rolls forward to the security's next price date.
    """
    relevant = actions.filter(pl.col("action").is_in([*_SPLIT_ACTIONS, *_CASH_ACTIONS])).select(
        "security_id", "ex_date", "action", "value"
    )
    dates = prices.select("security_id", "date").sort("security_id", "date")
    if relevant.is_empty():
        return dates.with_columns(
            pl.lit(1.0).alias("split_ratio"),
            pl.lit(0.0).alias("cash"),
            pl.lit(False).alias("has_action"),
        )
    located = (
        relevant.sort("security_id", "ex_date")
        .join_asof(
            dates,
            left_on="ex_date",
            right_on="date",
            by="security_id",
            strategy="forward",
            check_sortedness=False,
        )
        .drop_nulls("date")
    )
    terms = located.group_by("security_id", "date", maintain_order=True).agg(
        pl.when(pl.col("action").is_in(_SPLIT_ACTIONS))
        .then(pl.col("value"))
        .otherwise(1.0)
        .product()
        .alias("split_ratio"),
        pl.when(pl.col("action").is_in(_CASH_ACTIONS))
        .then(pl.col("value"))
        .otherwise(0.0)
        .sort()
        .sum()
        .alias("cash"),
    )
    return dates.join(terms, on=["security_id", "date"], how="left").with_columns(
        pl.col("split_ratio").is_not_null().alias("has_action"),
        pl.col("split_ratio").fill_null(1.0),
        pl.col("cash").fill_null(0.0),
    )


def build_prices(raw: pl.DataFrame, actions: pl.DataFrame) -> pl.DataFrame:
    """Compute total returns, levels and flags from unadjusted prices.

    Args:
        raw: rows with ``security_id``, ``date``, unadjusted ``open``, ``high``, ``low``,
            ``close``, ``volume`` and same-date ``shares_out``. CRSP-style negative closes
            mean a bid/ask midpoint. An optional ``ret_vendor`` column overrides the computed
            return wherever it is not null.
        actions: canonical corporate actions.

    Returns:
        ``prices_daily``. ``ret = (close * ratio + cash) / previous close - 1``.
        ``split_factor`` is the cumulative split ratio up to and including each date, so it
        never depends on a later split; ``PitStore.prices`` rebases levels to the as-of date.
    """
    prices = raw.sort("security_id", "date").with_columns(
        pl.col("close").lt(0).fill_null(False).alias("is_midpoint"),
        pl.col("close").abs(),
    )
    terms = _daily_action_terms(prices, actions)
    frame = prices.join(terms, on=["security_id", "date"], how="left").sort("security_id", "date")
    previous = pl.col("close").shift(1).over("security_id")
    vendor = pl.col("ret_vendor") if "ret_vendor" in frame.columns else pl.lit(None, pl.Float64)
    frame = frame.with_columns(
        pl.coalesce(
            vendor,
            pl.when(previous > 0).then(
                (pl.col("close") * pl.col("split_ratio") + pl.col("cash")) / previous - 1.0
            ),
        ).alias("ret"),
        pl.col("split_ratio").cum_prod().over("security_id").alias("split_factor"),
        (pl.col("close") * pl.col("volume")).alias("dollar_volume"),
        (pl.col("close") * pl.col("shares_out")).alias("mcap"),
    )
    zero_volume = pl.col("volume").fill_null(0.0).eq(0.0)
    run_breaks = zero_volume.ne(zero_volume.shift(1)).fill_null(True).cum_sum().over("security_id")
    frame = frame.with_columns(run_breaks.alias("_run"), zero_volume.alias("_zero"))
    frame = frame.with_columns(
        (
            pl.col("_zero")
            & (pl.int_range(1, pl.len() + 1).over("security_id", "_run") >= STALE_ZERO_VOLUME_DAYS)
        ).alias("_stale")
    )
    extreme = (
        (pl.col("ret").abs() > EXTREME_RETURN).fill_null(False) & ~pl.col("has_action")
    ) | pl.col("close").le(0).fill_null(True)
    frame = frame.with_columns(
        (
            pl.when(pl.col("is_midpoint")).then(FLAG_MIDPOINT).otherwise(0)
            + pl.when(pl.col("_stale")).then(FLAG_STALE).otherwise(0)
            + pl.when(extreme).then(FLAG_EXTREME).otherwise(0)
        ).alias("price_flag"),
        closes_on(frame["date"]).alias("available_at"),
    )
    return conform(frame, PRICES_DAILY).sort("security_id", "date")
