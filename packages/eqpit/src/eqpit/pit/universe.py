"""Monthly point-in-time universe (FR-09, DQ-07)."""

from __future__ import annotations

from datetime import date

import polars as pl

from eqpit.calendar import closes_on
from eqpit.pit.policy import PitPolicy
from eqpit.schemas import UNIVERSE_MONTHLY, conform


def build_universe(
    prices: pl.DataFrame,
    securities: pl.DataFrame,
    delistings: pl.DataFrame,
    ends: list[date],
    policy: PitPolicy,
) -> pl.DataFrame:
    """Rank eligible securities by company market cap at each month end.

    Nothing here looks past the as-of date except ``delisted_after``, a diagnostic that
    ``PitStore`` never serves. Names that later delist stay in every month they qualified.

    Args:
        prices: ``prices_daily``.
        securities: ``securities``.
        delistings: ``delistings``.
        ends: month-end sessions to build.
        policy: price floor, exchanges and share types.
    """
    at_month_end = prices.filter(pl.col("date").is_in(ends)).select(
        pl.col("date").alias("asof"), "security_id", "close", "mcap"
    )
    alive = at_month_end.join(
        securities.select(
            "security_id",
            "company_id",
            "exchange",
            "share_type",
            "ff49",
            "valid_from",
            "valid_to",
        ),
        on="security_id",
        how="inner",
    ).filter(
        pl.col("asof") >= pl.col("valid_from"),
        pl.col("valid_to").is_null() | (pl.col("asof") <= pl.col("valid_to")),
    )
    eligible = alive.filter(
        pl.col("close") >= policy.min_price_usd,
        pl.col("mcap") > 0,
        pl.col("exchange").is_in(policy.exchanges),
        pl.col("share_type").is_in(policy.share_types),
    )
    ordered = eligible.sort("asof", "company_id", "security_id")
    caps = ordered.group_by("asof", "company_id", maintain_order=True).agg(
        pl.col("mcap").sum().alias("company_mcap")
    )
    largest = (
        ordered.join(caps, on=["asof", "company_id"])
        .sort("mcap", "security_id", descending=[True, False])
        .unique(subset=["asof", "company_id"], keep="first", maintain_order=True)
    )
    ranked = largest.sort(
        ["asof", "company_mcap", "security_id"], descending=[False, True, False]
    ).with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int32).over("asof").alias("rank"))
    later = delistings.select("security_id", pl.col("delist_date").alias("delisted_after"))
    joined = ranked.join(later, on="security_id", how="left")
    built = joined.with_columns(
        pl.when(pl.col("delisted_after") > pl.col("asof")).then(pl.col("delisted_after")),
        (pl.col("rank") <= 1000).alias("in_top_1000"),
        (pl.col("rank") <= 3000).alias("in_top_3000"),
        closes_on(joined["asof"]).alias("available_at"),
    )
    return conform(built, UNIVERSE_MONTHLY).sort("asof", "rank")
