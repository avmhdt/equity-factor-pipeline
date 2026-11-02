"""Quality signals."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from efp.signals.common import (
    STALE_DAYS,
    annual_pair,
    latest,
    members,
    to_signal,
    ttm,
    with_year_ago,
)
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore

_FSCORE_ITEMS = (
    "net_income",
    "cfo",
    "total_assets",
    "long_term_debt",
    "current_assets",
    "current_liabilities",
    "shares_out",
    "revenue",
    "cogs",
)


@register(
    SignalSpec(
        "gp_at", "quality", 1, ("revenue", "cogs", "total_assets"), 0, "Novy-Marx 2013", "GP"
    )
)
def gp_at(store: PitStore, asof: date) -> pl.DataFrame:
    """TTM gross profit over latest total assets."""
    flows = ttm(store, asof, ["revenue", "cogs"]).rename({"available_at": "flow_at"})
    assets = latest(store, asof, ["total_assets"]).filter(pl.col("total_assets") > 0)
    joined = (
        members(store, asof)
        .join(flows, on="company_id", how="inner")
        .join(assets, on="company_id", how="inner")
    )
    return to_signal(
        joined,
        (pl.col("revenue") - pl.col("cogs")) / pl.col("total_assets"),
        ("flow_at", "available_at"),
    )


def _quarterly_return(store: PitStore, asof: date, base: str) -> pl.DataFrame:
    """Latest quarter's income before extraordinary items over the prior quarter's ``base``."""
    income = store.fundamentals(asof, ["income_before_extra"]).filter(
        pl.col("period_type").eq("3m"),
        pl.col("period_end") >= asof - timedelta(days=STALE_DAYS),
    )
    bases = store.fundamentals(asof, [base], history_periods=3).filter(
        pl.col("period_type").eq("instant")
    )
    gap = (pl.col("income_end") - pl.col("period_end")).dt.total_days()
    lagged = (
        bases.join(
            income.select(
                "company_id",
                pl.col("period_end").alias("income_end"),
                pl.col("value").alias("income"),
                pl.col("available_at").alias("income_at"),
            ),
            on="company_id",
        )
        .filter(gap.is_between(70, 110), pl.col("value") > 0)
        .group_by("company_id", maintain_order=True)
        .first()
    )
    joined = members(store, asof).join(lagged, on="company_id", how="inner")
    return to_signal(joined, pl.col("income") / pl.col("value"), ("available_at", "income_at"))


@register(
    SignalSpec(
        "roe",
        "quality",
        1,
        ("income_before_extra", "book_equity"),
        0,
        "Hou, Xue & Zhang 2015",
        None,
    )
)
def roe(store: PitStore, asof: date) -> pl.DataFrame:
    """Quarterly income before extraordinary items over lagged book equity."""
    return _quarterly_return(store, asof, "book_equity")


@register(
    SignalSpec(
        "roa",
        "quality",
        1,
        ("income_before_extra", "total_assets"),
        0,
        "Balakrishnan, Bartov & Faurel 2010",
        "roaq",
    )
)
def roa(store: PitStore, asof: date) -> pl.DataFrame:
    """Quarterly income before extraordinary items over lagged total assets."""
    return _quarterly_return(store, asof, "total_assets")


@register(
    SignalSpec(
        "accruals",
        "quality",
        -1,
        (
            "current_assets",
            "cash",
            "current_liabilities",
            "short_term_debt",
            "income_taxes_payable",
            "total_assets",
            "depreciation",
        ),
        0,
        "Sloan 1996",
        "Accruals",
    )
)
def accruals(store: PitStore, asof: date) -> pl.DataFrame:
    """Balance-sheet operating accruals over average total assets, year over year."""
    balance = with_year_ago(
        store,
        asof,
        [
            "current_assets",
            "cash",
            "current_liabilities",
            "short_term_debt",
            "income_taxes_payable",
            "total_assets",
        ],
    ).rename({"available_at": "balance_at"})
    depreciation = ttm(store, asof, ["depreciation"])

    def change(name: str) -> pl.Expr:
        return (pl.col(name) - pl.col(f"{name}_lag")).fill_null(0.0)

    joined = (
        members(store, asof)
        .join(balance, on="company_id", how="inner")
        .join(depreciation, on="company_id", how="inner")
        .filter(
            pl.col("current_assets").is_not_null(),
            pl.col("current_assets_lag").is_not_null(),
            pl.col("total_assets_lag") > 0,
        )
    )
    operating = (
        change("current_assets")
        - change("cash")
        - (
            change("current_liabilities")
            - change("short_term_debt")
            - change("income_taxes_payable")
        )
        - pl.col("depreciation")
    )
    average = (pl.col("total_assets") + pl.col("total_assets_lag")) / 2.0
    return to_signal(joined, operating / average, ("balance_at", "available_at"))


@register(SignalSpec("fscore", "quality", 1, _FSCORE_ITEMS, 0, "Piotroski 2000", "PS"))
def fscore(store: PitStore, asof: date) -> pl.DataFrame:
    """Piotroski's nine points from the last two fiscal years."""
    frame = annual_pair(store, asof, _FSCORE_ITEMS).filter(
        pl.col("total_assets_lag") > 0, pl.col("total_assets") > 0
    )
    roa_now = pl.col("net_income") / pl.col("total_assets")
    roa_then = pl.col("net_income_lag") / pl.col("total_assets_lag")
    leverage_now = pl.col("long_term_debt").fill_null(0.0) / pl.col("total_assets")
    leverage_then = pl.col("long_term_debt_lag").fill_null(0.0) / pl.col("total_assets_lag")
    ratio_now = pl.col("current_assets") / pl.col("current_liabilities")
    ratio_then = pl.col("current_assets_lag") / pl.col("current_liabilities_lag")
    margin_now = 1.0 - pl.col("cogs") / pl.col("revenue")
    margin_then = 1.0 - pl.col("cogs_lag") / pl.col("revenue_lag")
    turnover_now = pl.col("revenue") / pl.col("total_assets")
    turnover_then = pl.col("revenue_lag") / pl.col("total_assets_lag")
    points = [
        roa_now > 0,
        pl.col("cfo") > 0,
        roa_now > roa_then,
        pl.col("cfo") > pl.col("net_income"),
        leverage_now < leverage_then,
        ratio_now > ratio_then,
        pl.col("shares_out") <= pl.col("shares_out_lag"),
        margin_now > margin_then,
        turnover_now > turnover_then,
    ]
    joined = members(store, asof).join(frame, on="company_id", how="inner")
    score = pl.sum_horizontal(point.fill_null(False).cast(pl.Float64) for point in points)
    return to_signal(joined, score)
