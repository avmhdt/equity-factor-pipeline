"""Monthly and forward returns (DQ-08, DQ-12). Labels, never features."""

from __future__ import annotations

from datetime import date

import polars as pl

from eqpit.calendar import close_after, local_date, month_end_of
from eqpit.schemas import FORWARD_RETURNS, conform


def monthly_gross(
    prices: pl.DataFrame,
    delistings: pl.DataFrame,
    ends: list[date],
    include_delisting: bool = True,
) -> pl.DataFrame:
    """Gross return ``1 + r`` per (security, month), with month keyed by its last session.

    The delisting month compounds the partial-month return with the delisting return:
    ``(1 + r)(1 + dlret) - 1``. A delisting on a month-end session falls in the next month,
    because that session's close is the last price a holder could still be marked at.
    """
    daily = prices.select(
        "security_id",
        (1.0 + pl.col("ret").fill_null(0.0)).alias("gross"),
        month_end_of(prices["date"], ends).alias("month"),
    )
    gross = (
        daily.drop_nulls("month")
        .group_by("security_id", "month", maintain_order=True)
        .agg(pl.col("gross").product())
    )
    if not include_delisting or delistings.is_empty():
        return gross.sort("security_id", "month")
    next_day = close_after(delistings["delist_date"], 1).rename("after")
    months = month_end_of(
        delistings.with_columns(next_day).select(local_date(pl.col("after"))).to_series(), ends
    )
    final = delistings.select(
        "security_id",
        months.alias("month"),
        (1.0 + pl.col("dlret")).alias("delisting"),
    ).drop_nulls("month")
    joined = gross.join(final, on=["security_id", "month"], how="full", coalesce=True)
    return joined.select(
        "security_id",
        "month",
        (pl.col("gross").fill_null(1.0) * pl.col("delisting").fill_null(1.0)).alias("gross"),
    ).sort("security_id", "month")


def monthly_returns(
    prices: pl.DataFrame,
    delistings: pl.DataFrame,
    ends: list[date],
    include_delisting: bool = True,
) -> pl.DataFrame:
    """Total return per (security, month) as ``ret``."""
    gross = monthly_gross(prices, delistings, ends, include_delisting)
    return gross.select("security_id", "month", (pl.col("gross") - 1.0).alias("ret"))


def build_forward_returns(
    prices: pl.DataFrame,
    delistings: pl.DataFrame,
    ends: list[date],
    horizons: tuple[int, ...],
    risk_free: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Compound total return from the as-of close to the month end ``h`` months later.

    After a delisting the proceeds sit flat at zero return, so a delisted name keeps its
    delisting loss in every longer horizon rather than dropping out of the sample.

    Args:
        prices: ``prices_daily``.
        delistings: ``delistings``.
        ends: month-end sessions.
        horizons: months ahead.
        risk_free: optional ``month`` and ``rf`` (decimal per month).
    """
    gross = monthly_gross(prices, delistings, ends).with_columns(
        pl.col("gross").cum_prod().over("security_id").alias("index")
    )
    indexed = gross.select("security_id", "month", "index").sort("month")
    base = (
        prices.filter(pl.col("date").is_in(ends))
        .select(pl.col("date").alias("month"), "security_id")
        .join(indexed, on=["security_id", "month"], how="inner")
        .rename({"index": "base", "month": "asof"})
    )
    position = {end: index for index, end in enumerate(ends)}
    frames: list[pl.DataFrame] = []
    for horizon in horizons:
        pairs = pl.DataFrame(
            [(end, ends[position[end] + horizon]) for end in ends[: max(len(ends) - horizon, 0)]],
            schema={"asof": pl.Date, "target": pl.Date},
            orient="row",
        )
        ahead = (
            base.join(pairs, on="asof", how="inner")
            .sort("target")
            .join_asof(
                indexed,
                left_on="target",
                right_on="month",
                by="security_id",
                strategy="backward",
                check_sortedness=False,
            )
        )
        frames.append(
            ahead.select(
                "asof",
                "security_id",
                "target",
                pl.lit(horizon, dtype=pl.Int8).alias("h"),
                (pl.col("index") / pl.col("base") - 1.0).alias("fwd_ret"),
            )
        )
    forward = pl.concat(frames, how="vertical")
    if risk_free is None:
        return conform(forward, FORWARD_RETURNS).sort("asof", "security_id", "h")
    index_rf = risk_free.sort("month").select(
        pl.col("month"), (1.0 + pl.col("rf")).cum_prod().alias("rf_index")
    )
    excess = (
        forward.join(index_rf.rename({"month": "asof", "rf_index": "rf_base"}), on="asof")
        .join(index_rf.rename({"month": "target", "rf_index": "rf_target"}), on="target")
        .with_columns(
            (pl.col("fwd_ret") - (pl.col("rf_target") / pl.col("rf_base") - 1.0)).alias(
                "fwd_ret_excess"
            )
        )
    )
    return conform(excess, FORWARD_RETURNS).sort("asof", "security_id", "h")
