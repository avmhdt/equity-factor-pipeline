"""Monthly and market tables derived from daily prices. Both stay behind the as-of gate."""

from __future__ import annotations

from datetime import date

import polars as pl

from eqpit.calendar import closes_on, month_end_of
from eqpit.schemas import MARKET_DAILY, PRICES_MONTHLY, conform


def build_monthly_prices(prices: pl.DataFrame, ends: list[date]) -> pl.DataFrame:
    """Compound daily total returns into months, public at each month-end close.

    Unlike labels, this carries no delisting return: a name that delists has no feature value
    to lose, and a delisting return is not public before the delisting.
    """
    sorted_prices = prices.sort("security_id", "date")
    months = sorted_prices.select(
        "security_id",
        (1.0 + pl.col("ret").fill_null(0.0)).alias("gross"),
        month_end_of(sorted_prices["date"], ends).alias("month"),
    ).drop_nulls("month")
    built = months.group_by("security_id", "month", maintain_order=True).agg(
        (pl.col("gross").product() - 1.0).alias("ret"),
        pl.len().cast(pl.Int32).alias("days"),
    )
    return conform(
        built.with_columns(closes_on(built["month"]).alias("available_at")), PRICES_MONTHLY
    ).sort("security_id", "month")


def build_market_daily(
    prices: pl.DataFrame, universe: pl.DataFrame, ends: list[date], top: int = 3000
) -> pl.DataFrame:
    """Cap-weighted daily return of the prior month-end's top ``top`` names.

    Each day uses the membership fixed at the last month end and the previous day's market
    cap, so nothing about that day's return can move its own weight.
    """
    held_from = pl.DataFrame({"asof": ends}, schema={"asof": pl.Date}).with_columns(
        pl.col("asof").dt.offset_by("1d").alias("from")
    )
    days = (
        prices.select(pl.col("date").unique().sort())
        .join_asof(held_from, left_on="date", right_on="from", strategy="backward")
        .select("date", "asof")
    )
    members = universe.filter(pl.col("rank") <= top).select("asof", "security_id")
    joined = (
        prices.select("security_id", "date", "ret", "mcap")
        .sort("security_id", "date")
        .with_columns(pl.col("mcap").shift(1).over("security_id").alias("weight"))
        .join(days, on="date", how="left")
        .join(members, on=["asof", "security_id"], how="inner")
        .filter(pl.col("weight") > 0, pl.col("ret").is_not_null())
        .sort("date", "security_id")
    )
    built = joined.group_by("date", maintain_order=True).agg(
        ((pl.col("ret") * pl.col("weight")).sort().sum() / pl.col("weight").sort().sum()).alias(
            "ret"
        ),
        pl.len().cast(pl.Int32).alias("names"),
    )
    return conform(built.with_columns(closes_on(built["date"]).alias("available_at")), MARKET_DAILY)
