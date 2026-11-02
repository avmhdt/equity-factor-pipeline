"""Investment signals."""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.signals.common import members, to_signal, with_year_ago
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore

NSI_SESSIONS = 253
NSI_MIN_DAYS = 200


@register(
    SignalSpec(
        "asset_growth",
        "investment",
        -1,
        ("total_assets",),
        0,
        "Cooper, Gulen & Schill 2008",
        "AssetGrowth",
    )
)
def asset_growth(store: PitStore, asof: date) -> pl.DataFrame:
    """Year-over-year growth in total assets."""
    frame = with_year_ago(store, asof, ["total_assets"]).filter(pl.col("total_assets_lag") > 0)
    joined = members(store, asof).join(frame, on="company_id", how="inner")
    return to_signal(joined, pl.col("total_assets") / pl.col("total_assets_lag") - 1.0)


@register(
    SignalSpec(
        "inv_at",
        "investment",
        -1,
        ("ppe_gross", "ppe_net", "inventory", "total_assets"),
        0,
        "Lyandres, Sun & Zhang 2008",
        "InvestPPEInv",
    )
)
def inv_at(store: PitStore, asof: date) -> pl.DataFrame:
    """Change in gross PP&E plus inventory over lagged total assets.

    Sharadar carries net PP&E only, so net stands in where gross is missing.
    """
    frame = with_year_ago(
        store, asof, ["ppe_gross", "ppe_net", "inventory", "total_assets"]
    ).filter(pl.col("total_assets_lag") > 0)
    plant = pl.coalesce("ppe_gross", "ppe_net") - pl.coalesce("ppe_gross_lag", "ppe_net_lag")
    stock = (pl.col("inventory") - pl.col("inventory_lag")).fill_null(0.0)
    joined = members(store, asof).join(frame, on="company_id", how="inner")
    return to_signal(joined, (plant + stock) / pl.col("total_assets_lag"))


@register(SignalSpec("nsi", "investment", -1, (), 253, "Pontiff & Woodgate 2008", "ShareIss1Y"))
def nsi(store: PitStore, asof: date) -> pl.DataFrame:
    """Twelve-month log change in split-adjusted shares outstanding."""
    universe = members(store, asof).select("security_id")
    prices = store.prices(asof, NSI_SESSIONS).join(universe, on="security_id", how="semi")
    shares = prices.drop_nulls("shares_out").filter(pl.col("shares_out") > 0)
    adjusted = pl.col("shares_out") / pl.col("split_factor")
    changes = (
        shares.group_by("security_id", maintain_order=True)
        .agg(
            (adjusted.last().log() - adjusted.first().log()).alias("change"),
            pl.len().alias("days"),
            pl.col("available_at").max(),
        )
        .filter(pl.col("days") >= NSI_MIN_DAYS)
    )
    return to_signal(changes, pl.col("change"))


@register(
    SignalSpec(
        "noa",
        "investment",
        -1,
        (
            "total_assets",
            "cash",
            "short_term_debt",
            "long_term_debt",
            "stockholders_equity",
            "minority_interest",
            "preferred_stock",
        ),
        0,
        "Hirshleifer, Hou, Teoh & Zhang 2004",
        "NOA",
    )
)
def noa(store: PitStore, asof: date) -> pl.DataFrame:
    """Operating assets minus operating liabilities, over total assets a year earlier."""
    frame = with_year_ago(
        store,
        asof,
        [
            "total_assets",
            "cash",
            "short_term_debt",
            "long_term_debt",
            "stockholders_equity",
            "minority_interest",
            "preferred_stock",
        ],
    ).filter(pl.col("total_assets_lag") > 0, pl.col("stockholders_equity").is_not_null())
    operating_assets = pl.col("total_assets") - pl.col("cash").fill_null(0.0)
    operating_liabilities = (
        pl.col("total_assets")
        - pl.col("short_term_debt").fill_null(0.0)
        - pl.col("long_term_debt").fill_null(0.0)
        - pl.col("minority_interest").fill_null(0.0)
        - pl.col("preferred_stock").fill_null(0.0)
        - pl.col("stockholders_equity")
    )
    joined = members(store, asof).join(frame, on="company_id", how="inner")
    return to_signal(
        joined, (operating_assets - operating_liabilities) / pl.col("total_assets_lag")
    )
