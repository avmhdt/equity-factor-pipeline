"""Value signals: fundamentals over market equity."""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.signals.common import latest, members, to_signal, ttm
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore


def _over_me(store: PitStore, asof: date, frame: pl.DataFrame, numerator: pl.Expr) -> pl.DataFrame:
    joined = members(store, asof).join(frame, on="company_id", how="inner")
    return to_signal(joined, numerator / pl.col("me"), ("available_at", "me_at"))


@register(SignalSpec("bm", "value", 1, ("book_equity",), 0, "Fama & French 1992", "BMdec"))
def bm(store: PitStore, asof: date) -> pl.DataFrame:
    """Book equity over market equity at the as-of date; negative book equity gives no value."""
    frame = latest(store, asof, ["book_equity"]).filter(pl.col("book_equity") > 0)
    return _over_me(store, asof, frame, pl.col("book_equity"))


@register(SignalSpec("ep", "value", 1, ("income_before_extra",), 0, "Basu 1977", "EP"))
def ep(store: PitStore, asof: date) -> pl.DataFrame:
    """TTM income before extraordinary items over market equity."""
    frame = ttm(store, asof, ["income_before_extra"])
    return _over_me(store, asof, frame, pl.col("income_before_extra"))


@register(
    SignalSpec(
        "cfp",
        "value",
        1,
        ("income_before_extra", "depreciation"),
        0,
        "Lakonishok, Shleifer & Vishny 1994",
        "CF",
    )
)
def cfp(store: PitStore, asof: date) -> pl.DataFrame:
    """TTM income before extraordinary items plus D&A, over market equity."""
    frame = ttm(store, asof, ["income_before_extra", "depreciation"])
    return _over_me(store, asof, frame, pl.col("income_before_extra") + pl.col("depreciation"))


@register(SignalSpec("sp", "value", 1, ("revenue",), 0, "Barbee, Mukherji & Raines 1996", "SP"))
def sp(store: PitStore, asof: date) -> pl.DataFrame:
    """TTM sales over market equity."""
    return _over_me(store, asof, ttm(store, asof, ["revenue"]), pl.col("revenue"))


@register(
    SignalSpec(
        "ebitda_ev",
        "value",
        1,
        ("operating_income", "depreciation", "short_term_debt", "long_term_debt", "cash"),
        0,
        "Loughran & Wellman 2011",
        "EntMult",
    )
)
def ebitda_ev(store: PitStore, asof: date) -> pl.DataFrame:
    """TTM EBITDA over enterprise value: market equity plus total debt minus cash."""
    flows = ttm(store, asof, ["operating_income", "depreciation"]).rename(
        {"available_at": "flow_at"}
    )
    balance = latest(store, asof, ["short_term_debt", "long_term_debt", "cash"])
    joined = (
        members(store, asof)
        .join(flows, on="company_id", how="inner")
        .join(balance, on="company_id", how="inner")
    )
    value = (
        pl.col("me")
        + pl.col("short_term_debt").fill_null(0.0)
        + pl.col("long_term_debt").fill_null(0.0)
        - pl.col("cash").fill_null(0.0)
    )
    usable = joined.filter(value > 0)
    return to_signal(
        usable,
        (pl.col("operating_income") + pl.col("depreciation")) / value,
        ("flow_at", "available_at", "me_at"),
    )
