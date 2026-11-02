"""Low-risk signals, all from daily returns against the cap-weighted universe return."""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.signals.common import members, to_signal
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore

BETA_DAYS = 252
BETA_MIN_DAYS = 126
IVOL_DAYS = 21
IVOL_MIN_DAYS = 15


def _daily(store: PitStore, asof: date, days: int) -> pl.DataFrame:
    universe = members(store, asof).select("security_id")
    return (
        store.prices(asof, days)
        .join(universe, on="security_id", how="semi")
        .select("security_id", "date", "ret", "available_at")
        .drop_nulls("ret")
        .filter(pl.col("ret").is_finite())
    )


def _with_market(store: PitStore, asof: date, days: int) -> pl.DataFrame:
    market = store.market(asof, days).select("date", pl.col("ret").alias("market"))
    return _daily(store, asof, days).join(market, on="date", how="inner")


@register(SignalSpec("beta", "low_risk", -1, (), 252, "Frazzini & Pedersen 2014", None))
def beta(store: PitStore, asof: date) -> pl.DataFrame:
    """252-day market beta against the cap-weighted universe return."""
    estimates = (
        _with_market(store, asof, BETA_DAYS)
        .group_by("security_id", maintain_order=True)
        .agg(
            (pl.cov("ret", "market") / pl.col("market").var()).alias("beta"),
            pl.len().alias("days"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("days") >= BETA_MIN_DAYS)
    )
    return to_signal(estimates, pl.col("beta"))


@register(SignalSpec("ivol", "low_risk", -1, (), 21, "Ang, Hodrick, Xing & Zhang 2006", None))
def ivol(store: PitStore, asof: date) -> pl.DataFrame:
    """Std of daily market-model residuals over the last 21 sessions."""
    estimates = (
        _with_market(store, asof, IVOL_DAYS)
        .group_by("security_id", maintain_order=True)
        .agg(
            pl.col("ret").var().alias("total"),
            pl.cov("ret", "market").alias("covariance"),
            pl.col("market").var().alias("market_variance"),
            pl.len().alias("days"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("days") >= IVOL_MIN_DAYS)
    )
    residual = (
        (pl.col("total") - pl.col("covariance") ** 2 / pl.col("market_variance"))
        * (pl.col("days") - 1)
        / (pl.col("days") - 2)
    )
    return to_signal(estimates, residual.clip(0.0).sqrt())


@register(SignalSpec("vol_12m", "low_risk", -1, (), 252, "Ang et al. 2006", None))
def vol_12m(store: PitStore, asof: date) -> pl.DataFrame:
    """Std of daily returns over 252 sessions."""
    estimates = (
        _daily(store, asof, BETA_DAYS)
        .group_by("security_id", maintain_order=True)
        .agg(
            pl.col("ret").std().alias("deviation"),
            pl.len().alias("days"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("days") >= BETA_MIN_DAYS)
    )
    return to_signal(estimates, pl.col("deviation"))


@register(SignalSpec("max_ret", "low_risk", -1, (), 21, "Bali, Cakici & Whitelaw 2011", "MaxRet"))
def max_ret(store: PitStore, asof: date) -> pl.DataFrame:
    """Largest daily return in the latest month."""
    frame = _daily(store, asof, 25).filter(
        pl.col("date").dt.month().eq(asof.month), pl.col("date").dt.year().eq(asof.year)
    )
    highest = frame.group_by("security_id", maintain_order=True).agg(
        pl.col("ret").max().alias("highest"), pl.col("available_at").max()
    )
    return to_signal(highest, pl.col("highest"))
