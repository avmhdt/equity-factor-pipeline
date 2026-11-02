"""From Fama-French HML to our research value factor, one change at a time (FR-35)."""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.evaluation.french import (
    Panels,
    annual_characteristics,
    bucket_returns,
    buckets,
    factor_returns,
)
from efp.evaluation.quantiles import quantile_spreads
from eqpit.store import PitStore

RESEARCH_UNIVERSE = 3000


def _june_sort(
    store: PitStore,
    panels: Panels,
    universe: pl.DataFrame,
    use_nyse: bool,
) -> pl.DataFrame:
    """Annual 2x3 sorts each June on FY book equity over December market equity."""
    months = panels.caps["month"].unique().sort().to_list()
    position = {month: index for index, month in enumerate(months)}
    frames: list[pl.DataFrame] = []
    for june in (month for month in months if month.month == 6):
        index = position[june]
        if index < 6:
            continue
        at_june = universe.filter(pl.col("asof").eq(june)).select(
            "security_id", "company_id", "exchange", pl.col("company_mcap").alias("size")
        )
        values = annual_characteristics(store, june, months[index - 6], panels)
        sorted_on = at_june.join(values, on="company_id", how="inner")
        if not use_nyse:
            sorted_on = sorted_on.with_columns(pl.lit("NYSE").alias("exchange"))
        holds = months[index + 1 : index + 13]
        periods = pl.DataFrame(
            {"hold": holds, "previous": months[index : index + 12][: len(holds)]},
            schema={"hold": pl.Date, "previous": pl.Date},
        )
        assigned = buckets(sorted_on, "bm").select(
            pl.lit("bm").alias("factor"), "bucket", "security_id"
        )
        frames.append(assigned.join(periods, how="cross"))
    return pl.concat(frames, how="vertical")


def _monthly_sort(
    store: PitStore,
    panels: Panels,
    universe: pl.DataFrame,
    panel: pl.DataFrame | None,
) -> pl.DataFrame:
    """Monthly 2x3 sorts: on FY book over current cap, or on the latest visible book."""
    months = panels.caps["month"].unique().sort().to_list()
    frames: list[pl.DataFrame] = []
    for index, month in enumerate(months[:-1]):
        at_month = universe.filter(pl.col("asof").eq(month)).select(
            "security_id", "company_id", "exchange", pl.col("company_mcap").alias("size")
        )
        if at_month.is_empty():
            continue
        if panel is not None:
            scores = panel.filter(pl.col("asof").eq(month)).select(
                "security_id", pl.col("raw").alias("bm")
            )
            sorted_on = at_month.join(scores, on="security_id", how="inner")
        else:
            sorted_on = _fy_book_over_current(store, month, at_month)
        assigned = buckets(sorted_on, "bm")
        frames.append(
            assigned.select(
                pl.lit("bm").alias("factor"),
                "bucket",
                "security_id",
                pl.lit(months[index + 1], dtype=pl.Date).alias("hold"),
                pl.lit(month, dtype=pl.Date).alias("previous"),
            )
        )
    return pl.concat(frames, how="vertical")


def _fy_book_over_current(store: PitStore, month: date, at_month: pl.DataFrame) -> pl.DataFrame:
    """Book equity from the last fiscal year closed before this calendar year, over cap now."""
    rows = store.fundamentals(month, ["book_equity"], history_periods=12).filter(
        pl.col("fiscal_period").eq("FY"), pl.col("period_end").dt.year().lt(month.year)
    )
    latest = (
        rows.sort("company_id", "period_end")
        .group_by("company_id", maintain_order=True)
        .agg(pl.col("value").last().alias("book"))
        .filter(pl.col("book") > 0)
    )
    return at_month.join(latest, on="company_id", how="inner").with_columns(
        (pl.col("book") / pl.col("size")).alias("bm")
    )


def _spread_series(framed: pl.DataFrame, fwd: pl.DataFrame, score: str) -> pl.DataFrame:
    spreads = quantile_spreads(framed, fwd, 10, "vw", "nyse", score)
    return spreads.filter(pl.col("quantile").eq(0)).select(pl.col("asof").alias("hold_from"), "ret")


def decompose(
    store: PitStore,
    panels: Panels,
    panel: pl.DataFrame,
    french: pl.DataFrame,
    fwd: pl.DataFrame,
) -> pl.DataFrame:
    """Series for each step, compared with French's HML and with the step before."""
    months = panels.caps["month"].unique().sort().to_list()
    next_month = pl.DataFrame(
        {"hold_from": months[:-1], "month": months[1:]},
        schema={"hold_from": pl.Date, "month": pl.Date},
    )
    research = panels.universe.join(
        panels.universe.filter(pl.col("rank") <= RESEARCH_UNIVERSE).select("asof", "security_id"),
        on=["asof", "security_id"],
        how="semi",
    )

    def two_by_three(assignments: pl.DataFrame) -> pl.DataFrame:
        returns = factor_returns(bucket_returns(assignments, panels))
        return returns.filter(pl.col("factor").eq("hml")).select("month", "ret")

    framed = panel.join(
        research.select("asof", "security_id", "exchange", "mcap"),
        on=["asof", "security_id"],
        how="inner",
    )
    steps: list[tuple[str, str, pl.DataFrame]] = [
        (
            "hml_ff_convention",
            "NYSE 2x3, June, FY book over December cap, all eligible names",
            two_by_three(_june_sort(store, panels, panels.universe, use_nyse=True)),
        ),
        (
            "universe_top_3000",
            "Same, on the top 3,000",
            two_by_three(_june_sort(store, panels, research, use_nyse=True)),
        ),
        (
            "breakpoints_all_names",
            "Breakpoints from every name, not NYSE only",
            two_by_three(_june_sort(store, panels, research, use_nyse=False)),
        ),
        (
            "rebalance_monthly",
            "Re-formed monthly with the current cap",
            two_by_three(_monthly_sort(store, panels, research, None)),
        ),
        (
            "pit_latest_quarter",
            "Latest book equity public at each month end, not the last FY",
            two_by_three(_monthly_sort(store, panels, research, panel)),
        ),
        (
            "decile_spread",
            "VW decile spread on NYSE breakpoints, our research construction",
            _spread_series(framed, fwd, "z")
            .join(next_month, on="hold_from")
            .select("month", "ret"),
        ),
        (
            "neutralized",
            "Sector, size and beta removed",
            _spread_series(framed, fwd, "z_neutral")
            .join(next_month, on="hold_from")
            .select("month", "ret"),
        ),
    ]
    benchmark = french.select("month", pl.col("hml").alias("french"))
    rows: list[dict[str, object]] = []
    previous: pl.DataFrame | None = None
    for name, change, series in steps:
        joined = series.join(benchmark, on="month", how="inner").drop_nulls().sort("month")
        with_previous = None
        if previous is not None:
            paired = series.join(previous.rename({"ret": "before"}), on="month", how="inner")
            with_previous = paired.select(pl.corr("ret", "before")).item()
        rows.append(
            {
                "step": name,
                "change": change,
                "months": joined.height,
                "correlation_with_hml": joined.select(pl.corr("ret", "french")).item(),
                "correlation_with_previous": with_previous,
                "mean_monthly": joined["ret"].mean(),
                "mean_monthly_hml": joined["french"].mean(),
            }
        )
        previous = series
    return pl.DataFrame(rows, infer_schema_length=None)
