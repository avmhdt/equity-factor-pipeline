"""Execution timing, as an adjustment to close-to-close holding returns.

Let ``g`` be the total return from the close on rebalance date T to the execution price on the
next session. Holding from one execution to the next earns

    (1 + close-to-close return) * g(T + 1 month) / g(T)

so every mode shares the same monthly labels, delisting returns included. ``close`` has g = 1.
The VWAP mode is a proxy, (O + H + L + C) / 4 of the next session: there is no intraday data.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl

from eqpit import calendar

Execution = Literal["close", "next_open", "next_close", "next_vwap_proxy"]
MODES: tuple[Execution, ...] = ("close", "next_open", "next_close", "next_vwap_proxy")


def next_session_rows(prices: pl.DataFrame, ends: list[date]) -> pl.DataFrame:
    """Rows on the session after each month end, keyed by that month end as ``asof``."""
    following = pl.DataFrame(
        {"asof": ends, "date": [calendar.next_session(end) for end in ends]},
        schema={"asof": pl.Date, "date": pl.Date},
    )
    return prices.join(following, on="date", how="inner")


def timing_factors(next_rows: pl.DataFrame, mode: Execution) -> pl.DataFrame:
    """``asof``, ``security_id``, ``factor``: g for each name; a missing row means g = 1."""
    if mode == "close":
        return next_rows.select("asof", "security_id", pl.lit(1.0).alias("factor"))
    if mode == "next_close":
        fraction = pl.lit(1.0)
    elif mode == "next_open":
        fraction = pl.col("open") / pl.col("close")
    else:
        fraction = (pl.col("open") + pl.col("high") + pl.col("low") + pl.col("close")) / (
            4.0 * pl.col("close")
        )
    return next_rows.filter(pl.col("close") > 0).select(
        "asof",
        "security_id",
        ((1.0 + pl.col("ret").fill_null(0.0)) * fraction).alias("factor"),
    )


def holding_returns(labels: pl.DataFrame, factors: pl.DataFrame, ends: list[date]) -> pl.DataFrame:
    """Return per (``asof``, ``security_id``) from this execution to the next.

    Args:
        labels: ``asof``, ``security_id``, ``fwd_ret``, one month ahead from the close.
        factors: from ``timing_factors``.
        ends: month ends, sorted.
    """
    following = pl.DataFrame(
        {"asof": ends[:-1], "next_asof": ends[1:]},
        schema={"asof": pl.Date, "next_asof": pl.Date},
    )
    start = factors.rename({"factor": "start"})
    finish = factors.rename({"asof": "next_asof", "factor": "finish"})
    return (
        labels.join(following, on="asof", how="inner")
        .join(start, on=["asof", "security_id"], how="left")
        .join(finish, on=["next_asof", "security_id"], how="left")
        .select(
            "asof",
            "security_id",
            (
                (1.0 + pl.col("fwd_ret"))
                * pl.col("finish").fill_null(1.0)
                / pl.col("start").fill_null(1.0)
                - 1.0
            ).alias("ret"),
        )
    )
