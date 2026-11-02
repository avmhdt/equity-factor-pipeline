"""Fama-French convention factors, for replication against Ken French's series (FR-21).

SMB, HML, RMW and CMA re-form each June on 2x3 NYSE breakpoints. UMD re-forms monthly on returns
from months t-12 to t-2. Portfolios are value-weighted by the prior month-end market cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from eqpit.store import PitStore

FINANCIAL_ITEMS = (
    "book_equity",
    "total_assets",
    "revenue",
    "cogs",
    "sga",
    "interest_expense",
)
LOW_QUANTILE = 0.3
HIGH_QUANTILE = 0.7
THRESHOLDS = {"umd": 0.90, "smb": 0.90, "hml": 0.80}
"""AC-W-01. RMW and CMA are reported without a threshold."""


@dataclass(frozen=True)
class Panels:
    """Monthly inputs.

    Args:
        universe: ``asof``, ``security_id``, ``company_id``, ``company_mcap``, ``exchange``.
        caps: ``month``, ``security_id``, ``mcap``, every security's month-end market cap.
        returns: ``month``, ``security_id``, ``ret``, including delisting returns.
    """

    universe: pl.DataFrame
    caps: pl.DataFrame
    returns: pl.DataFrame


def load_panels(store: PitStore, survivors_only: bool = False) -> Panels:
    """Read the monthly inputs, optionally keeping only names alive at the sample end."""
    labels = store.labels()
    universe = labels.universe_history()
    caps = labels.month_end_caps()
    months = caps["month"]
    first, last = months.min(), months.max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    returns = labels.monthly_returns(first, last)
    if survivors_only:
        gone = labels.delisted_ids()
        universe = universe.filter(~pl.col("security_id").is_in(gone.to_list()))
    return Panels(universe, caps, returns)


def buckets(frame: pl.DataFrame, characteristic: str) -> pl.DataFrame:
    """Add ``bucket`` such as ``SL`` or ``BH``: size median, then 30/70 on the characteristic.

    Breakpoints come from NYSE names only, so small Nasdaq stocks do not move them. Rows
    without a value drop out.

    Args:
        frame: one formation date's rows with ``size``, ``exchange`` and the characteristic.
        characteristic: column to sort on, where high is the long side.
    """
    usable = frame.drop_nulls(["size", characteristic]).filter(pl.col(characteristic).is_finite())
    nyse = usable.filter(pl.col("exchange").eq("NYSE"))
    if nyse.height < 10:
        return usable.clear().with_columns(pl.lit("").alias("bucket"))
    median = nyse["size"].median()
    low = nyse[characteristic].quantile(LOW_QUANTILE, interpolation="linear")
    high = nyse[characteristic].quantile(HIGH_QUANTILE, interpolation="linear")
    return usable.with_columns(
        (
            pl.when(pl.col("size") <= median).then(pl.lit("S")).otherwise(pl.lit("B"))
            + pl.when(pl.col(characteristic) <= low)
            .then(pl.lit("L"))
            .when(pl.col(characteristic) >= high)
            .then(pl.lit("H"))
            .otherwise(pl.lit("M"))
        ).alias("bucket")
    )


def bucket_returns(assignments: pl.DataFrame, panels: Panels) -> pl.DataFrame:
    """Value-weighted return per (``month``, ``factor``, ``bucket``).

    Args:
        assignments: ``hold``, ``previous``, ``factor``, ``bucket``, ``security_id``, where
            ``hold`` is the month earned and ``previous`` the month end that sets the weight.
        panels: monthly inputs.
    """
    weights = panels.caps.rename({"month": "previous", "mcap": "weight"})
    held = (
        assignments.join(weights, on=["previous", "security_id"], how="inner")
        .join(panels.returns.rename({"month": "hold"}), on=["hold", "security_id"], how="inner")
        .filter(pl.col("weight") > 0)
        .sort("hold", "factor", "bucket", "security_id")
    )
    return (
        held.group_by("hold", "factor", "bucket", maintain_order=True)
        .agg(((pl.col("ret") * pl.col("weight")).sum() / pl.col("weight").sum()).alias("ret"))
        .rename({"hold": "month"})
    )


def _annual_characteristics(
    store: PitStore, june: date, december: date, panels: Panels
) -> pl.DataFrame:
    """Fiscal-year values public at the end of June, per company, in FF definitions.

    FF use the fiscal year ending in the prior calendar year. Going through ``PitStore`` keeps
    this honest: a company that has not filed by June simply has no value.
    """
    year = june.year - 1
    financials = store.fundamentals(june, FINANCIAL_ITEMS, history_periods=12).filter(
        pl.col("fiscal_period").eq("FY")
    )
    latest = financials.filter(pl.col("period_end").dt.year().eq(year)).sort("period_end")
    wide = (
        latest.group_by("company_id", "item", maintain_order=True)
        .agg(pl.col("value").last())
        .pivot(on="item", index="company_id", values="value")
    )
    for item in FINANCIAL_ITEMS:
        if item not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(item))
    prior = (
        financials.filter(
            pl.col("item").eq("total_assets"), pl.col("period_end").dt.year().eq(year - 1)
        )
        .sort("period_end")
        .group_by("company_id", maintain_order=True)
        .agg(pl.col("value").last().alias("assets_prior"))
    )
    december_caps = (
        panels.universe.filter(pl.col("asof").eq(december))
        .select("company_id", pl.col("company_mcap").alias("me_december"))
        .unique("company_id", keep="first", maintain_order=True)
    )
    return (
        wide.join(prior, on="company_id", how="left")
        .join(december_caps, on="company_id", how="left")
        .with_columns(
            pl.when(pl.col("book_equity") > 0)
            .then(pl.col("book_equity") / pl.col("me_december"))
            .alias("bm"),
            pl.when(pl.col("book_equity") > 0)
            .then(
                (
                    pl.col("revenue")
                    - pl.col("cogs").fill_null(0.0)
                    - pl.col("sga").fill_null(0.0)
                    - pl.col("interest_expense").fill_null(0.0)
                )
                / pl.col("book_equity")
            )
            .alias("op"),
            (pl.col("total_assets") / pl.col("assets_prior") - 1.0).alias("inv"),
        )
    )


def annual_assignments(store: PitStore, panels: Panels) -> pl.DataFrame:
    """Sort every June into BM, OP and INV 2x3 portfolios and hold for twelve months."""
    months = panels.caps["month"].unique().sort().to_list()
    position = {month: index for index, month in enumerate(months)}
    frames: list[pl.DataFrame] = []
    for june in (month for month in months if month.month == 6):
        index = position[june]
        if index < 6:
            continue
        december = months[index - 6]
        at_june = panels.universe.filter(pl.col("asof").eq(june)).select(
            "security_id", "company_id", "exchange", pl.col("company_mcap").alias("size")
        )
        characteristics = _annual_characteristics(store, june, december, panels)
        sorted_on = at_june.join(characteristics, on="company_id", how="inner")
        holds = months[index + 1 : index + 13]
        periods = pl.DataFrame(
            {"hold": holds, "previous": months[index : index + 12][: len(holds)]},
            schema={"hold": pl.Date, "previous": pl.Date},
        )
        for name, column, high_is_long in (
            ("bm", "bm", True),
            ("op", "op", True),
            ("inv", "inv", False),
        ):
            source = sorted_on if high_is_long else sorted_on.with_columns(-pl.col(column))
            assigned = buckets(source, column).select(
                pl.lit(name).alias("factor"), "bucket", "security_id"
            )
            frames.append(assigned.join(periods, how="cross"))
    return pl.concat(frames, how="vertical")


def momentum_assignments(panels: Panels) -> pl.DataFrame:
    """Sort every month on the return over months t-12 to t-2, for holding month t."""
    months = panels.caps["month"].unique().sort().to_list()
    positions = pl.DataFrame({"month": months, "index": list(range(len(months)))})
    indexed = panels.returns.join(positions, on="month").sort("security_id", "month")
    frames: list[pl.DataFrame] = []
    by_index = dict(enumerate(months))
    for index in range(12, len(months) - 1):
        formation = by_index[index]
        window = indexed.filter(pl.col("index").is_between(index - 11, index - 1))
        prior = (
            window.group_by("security_id", maintain_order=True)
            .agg(
                ((1.0 + pl.col("ret")).product() - 1.0).alias("prior"),
                pl.len().alias("months"),
            )
            .filter(pl.col("months").eq(11))
        )
        at_month = panels.universe.filter(pl.col("asof").eq(formation)).select(
            "security_id", "exchange", pl.col("company_mcap").alias("size")
        )
        assigned = buckets(at_month.join(prior, on="security_id", how="inner"), "prior")
        frames.append(
            assigned.select(
                pl.lit("umd").alias("factor"),
                "bucket",
                "security_id",
                pl.lit(by_index[index + 1], dtype=pl.Date).alias("hold"),
                pl.lit(formation, dtype=pl.Date).alias("previous"),
            )
        )
    return pl.concat(frames, how="vertical")


def low_risk_assignments(
    panels: Panels, months_back: int = 36, min_months: int = 24
) -> pl.DataFrame:
    """Sort every month on low trailing volatility of monthly returns, long the calm names.

    This stands in for the low-risk family in the survivorship table until the signal library
    arrives in M4.
    """
    months = panels.caps["month"].unique().sort().to_list()
    positions = pl.DataFrame({"month": months, "index": list(range(len(months)))})
    indexed = panels.returns.join(positions, on="month").sort("security_id", "month")
    frames: list[pl.DataFrame] = []
    for index in range(months_back, len(months) - 1):
        window = indexed.filter(pl.col("index").is_between(index - months_back + 1, index))
        volatility = (
            window.group_by("security_id", maintain_order=True)
            .agg((-pl.col("ret").std()).alias("calm"), pl.len().alias("months"))
            .filter(pl.col("months") >= min_months)
        )
        at_month = panels.universe.filter(pl.col("asof").eq(months[index])).select(
            "security_id", "exchange", pl.col("company_mcap").alias("size")
        )
        assigned = buckets(at_month.join(volatility, on="security_id", how="inner"), "calm")
        frames.append(
            assigned.select(
                pl.lit("low_risk").alias("factor"),
                "bucket",
                "security_id",
                pl.lit(months[index + 1], dtype=pl.Date).alias("hold"),
                pl.lit(months[index], dtype=pl.Date).alias("previous"),
            )
        )
    return pl.concat(frames, how="vertical")


def _long_short(wide: pl.DataFrame, long: list[str], short: list[str]) -> pl.Expr:
    present_long = [name for name in long if name in wide.columns]
    present_short = [name for name in short if name in wide.columns]
    return pl.mean_horizontal(present_long) - pl.mean_horizontal(present_short)


def factor_returns(portfolios: pl.DataFrame) -> pl.DataFrame:
    """Long-short series as ``month``, ``factor``, ``ret`` from bucket returns."""
    wide = portfolios.pivot(on="bucket", index=["month", "factor"], values="ret").sort(
        "month", "factor"
    )
    both = ["SH", "BH"]
    opposite = ["SL", "BL"]
    small = ["SL", "SM", "SH"]
    big = ["BL", "BM", "BH"]
    spreads = wide.with_columns(
        _long_short(wide, both, opposite).alias("spread"),
        _long_short(wide, small, big).alias("smb"),
    )
    named = spreads.filter(pl.col("factor").is_in(["bm", "op", "inv", "umd", "low_risk"]))
    rename = {"bm": "hml", "op": "rmw", "inv": "cma", "umd": "umd", "low_risk": "low_risk"}
    longs = named.select(
        "month",
        pl.col("factor").replace_strict(rename, return_dtype=pl.String),
        pl.col("spread").alias("ret"),
    )
    size = (
        spreads.filter(pl.col("factor").is_in(["bm", "op", "inv"]))
        .sort("month", "factor")
        .group_by("month", maintain_order=True)
        .agg(pl.col("smb").mean().alias("ret"))
        .select("month", pl.lit("smb").alias("factor"), "ret")
    )
    return pl.concat([longs, size], how="vertical").sort("factor", "month").drop_nulls("ret")


def replicate(store: PitStore, survivors_only: bool = False) -> pl.DataFrame:
    """All factor series from point-in-time data."""
    panels = load_panels(store, survivors_only)
    assignments = pl.concat(
        [
            annual_assignments(store, panels),
            momentum_assignments(panels),
            low_risk_assignments(panels),
        ],
        how="vertical",
    )
    return factor_returns(bucket_returns(assignments, panels))


def compare(ours: pl.DataFrame, french: pl.DataFrame) -> pl.DataFrame:
    """Monthly correlation with French's series over the overlap, one row per factor."""
    rows: list[dict[str, object]] = []
    for factor in ("smb", "hml", "umd", "rmw", "cma"):
        joined = (
            ours.filter(pl.col("factor").eq(factor))
            .join(french.select("month", pl.col(factor).alias("french")), on="month")
            .drop_nulls(["ret", "french"])
            .sort("month")
        )
        correlation = joined.select(pl.corr("ret", "french")).item() if joined.height > 2 else None
        threshold = THRESHOLDS.get(factor)
        rows.append(
            {
                "factor": factor,
                "months": joined.height,
                "first": joined["month"].min(),
                "last": joined["month"].max(),
                "correlation": correlation,
                "mean_ours": joined["ret"].mean(),
                "mean_french": joined["french"].mean(),
                "threshold": threshold,
                "passed": None
                if threshold is None or correlation is None
                else bool(correlation >= threshold),
            }
        )
    return pl.DataFrame(rows)
