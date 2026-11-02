"""Data-quality checks behind ``efp data verify`` (DQ-01 to DQ-17)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from eqpit import calendar
from eqpit.calendar import local_date
from eqpit.pit.build import read_pit_manifest
from eqpit.pit.corpactions import CANONICAL_ACTIONS
from eqpit.pit.delisting import REASONS
from eqpit.pit.prices import EXTREME_RETURN, FLAG_EXTREME
from eqpit.schemas import GATED_TABLES

Status = Literal["pass", "fail", "info"]


@dataclass(frozen=True)
class DqResult:
    """One line of the DQ report."""

    check: str
    status: Status
    detail: str
    hard: bool = True


class _Tables:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __getitem__(self, table: str) -> pl.DataFrame:
        return pl.read_parquet(self.root / table / "**" / "*.parquet")


def _count(check: str, bad: int, what: str) -> DqResult:
    return DqResult(check, "fail" if bad else "pass", f"{bad} {what}")


def _dq01_gate(tables: _Tables) -> list[DqResult]:
    results: list[DqResult] = []
    for table in GATED_TABLES:
        frame = tables[table]
        if "available_at" not in frame.columns:
            results.append(DqResult("DQ-01", "fail", f"{table} has no available_at"))
            continue
        nulls = frame["available_at"].null_count()
        results.append(_count("DQ-01", nulls, f"null available_at in {table}"))
    return results


def _dq02_acceptance(tables: _Tables) -> list[DqResult]:
    filings = tables["filings"].filter(pl.col("pit_method").eq("acceptance"))
    early = filings.filter(pl.col("available_at") < pl.col("acceptance_dt")).height
    fundamentals = tables["fundamentals_pit"].filter(pl.col("pit_method").eq("acceptance"))
    joined = fundamentals.join(
        tables["filings"].select("accession", "acceptance_dt"), on="accession", how="inner"
    )
    leaked = joined.filter(pl.col("available_at") < pl.col("acceptance_dt")).height
    return [
        _count("DQ-02", early, "filings available before acceptance"),
        _count("DQ-02", leaked, "fundamentals available before acceptance"),
    ]


def _dq03_period_end(tables: _Tables) -> list[DqResult]:
    results = []
    for table, end in (("filings", "period_end"), ("fundamentals_pit", "period_end")):
        frame = tables[table].drop_nulls(end)
        bad = frame.filter(local_date(pl.col("available_at")) <= pl.col(end)).height
        results.append(_count("DQ-03", bad, f"{table} rows available by their period end"))
    return results


def _dq04_link(tables: _Tables) -> list[DqResult]:
    fundamentals = tables["fundamentals_pit"]
    total = fundamentals.height
    linked = fundamentals.filter(pl.col("pit_method").eq("acceptance")).height
    share = linked / total if total else 0.0
    return [DqResult("DQ-04", "info", f"{share:.4f} of fundamentals linked to acceptance", False)]


def _dq05_first_reported(tables: _Tables) -> list[DqResult]:
    key = ["company_id", "item", "period_end", "period_type"]
    ordered = tables["fundamentals_pit"].sort(*key, "available_at", "accession")
    wrong = ordered.filter(
        (pl.int_range(pl.len()).over(key) == 0) & pl.col("is_restatement")
    ).height
    repeated = ordered.filter(
        (pl.int_range(pl.len()).over(key) > 0) & ~pl.col("is_restatement")
    ).height
    restated = ordered["is_restatement"].sum()
    return [
        _count("DQ-05", wrong, "first reports marked as restatements"),
        _count("DQ-05", repeated, "later reports not marked as restatements"),
        DqResult("DQ-05", "info", f"{restated} restated values kept as new rows", False),
    ]


def _dq07_universe(tables: _Tables) -> list[DqResult]:
    universe = tables["universe_monthly"]
    doubled = universe.group_by("asof", "company_id").len().filter(pl.col("len") > 1).height
    gaps = (
        universe.group_by("asof")
        .agg((pl.col("rank").max() - pl.len()).alias("gap"))
        .filter(pl.col("gap") != 0)
        .height
    )
    ends = set(universe["asof"].unique().to_list())
    first, last = min(ends), max(ends)
    not_ends = len(ends - set(calendar.month_ends(first, last)))
    delisted = universe.filter(pl.col("delisted_after").is_not_null())
    per_year = delisted.group_by(pl.col("asof").dt.year()).len().height
    return [
        _count("DQ-07", doubled, "company-months with two classes"),
        _count("DQ-07", gaps, "months with broken ranks"),
        _count("DQ-07", not_ends, "universe dates that are not month ends"),
        DqResult(
            "DQ-07",
            "info",
            f"{delisted.height} universe-months of names that later delisted, over "
            f"{per_year} years",
            False,
        ),
    ]


def _dq08_delistings(tables: _Tables) -> list[DqResult]:
    delistings = tables["delistings"]
    bad_reason = delistings.filter(~pl.col("reason").is_in(REASONS)).height
    null_return = delistings["dlret"].null_count()
    rules = delistings.group_by("imputation_rule").len().sort("imputation_rule")
    counts = ", ".join(f"{rule}={count}" for rule, count in rules.iter_rows())
    return [
        _count("DQ-08", bad_reason, "delistings with an unknown reason"),
        _count("DQ-08", null_return, "delistings with no return"),
        DqResult("DQ-08", "info", f"imputation rules: {counts or 'none'}", False),
    ]


def _dq09_prices(tables: _Tables) -> list[DqResult]:
    prices = tables["prices_daily"]
    wrong = prices.filter(
        (pl.col("close") * pl.col("shares_out") - pl.col("mcap")).abs() > 1e-6 * pl.col("mcap")
    ).height
    actions = tables["corp_actions"]
    unmapped = actions.filter(~pl.col("action").is_in(CANONICAL_ACTIONS)).height
    return [
        _count("DQ-09", wrong, "rows where mcap is not close times shares"),
        _count("DQ-09", unmapped, "unmapped corporate action types"),
    ]


def _dq10_flags(tables: _Tables) -> list[DqResult]:
    prices = tables["prices_daily"]
    actions = (
        tables["corp_actions"]
        .select("security_id", pl.col("ex_date").alias("date"), pl.lit(True).alias("acted"))
        .unique()
    )
    unflagged = (
        prices.filter(
            pl.col("ret").abs() > EXTREME_RETURN, pl.col("price_flag") & FLAG_EXTREME == 0
        )
        .join(actions, on=["security_id", "date"], how="anti")
        .height
    )
    flagged = prices.filter(pl.col("price_flag") > 0).height
    return [
        _count("DQ-10", unflagged, "extreme returns left unflagged"),
        DqResult("DQ-10", "info", f"{flagged} rows flagged, none dropped", False),
    ]


def _dq12_labels(tables: _Tables) -> list[DqResult]:
    gated = {name for name in GATED_TABLES}
    return [_count("DQ-12", int("forward_returns" in gated), "label tables behind the gate")]


def _dq16_label(root: Path) -> list[DqResult]:
    manifest = read_pit_manifest(root)
    labelled = (not manifest["survivorship_limited"]) or bool(manifest["label"])
    limited = manifest["provider"] == "edgar_free"
    consistent = labelled and manifest["survivorship_limited"] == limited
    return [
        DqResult(
            "DQ-16",
            "pass" if consistent else "fail",
            f"provider {manifest['provider']}, survivorship_limited "
            f"{manifest['survivorship_limited']}",
        )
    ]


def _dq17_calendar(tables: _Tables) -> list[DqResult]:
    dates = tables["prices_daily"]["date"].unique().sort()
    first, last = dates[0], dates[-1]
    assert isinstance(first, date)
    assert isinstance(last, date)
    known = set(calendar.sessions(first, last))
    return [_count("DQ-17", len(set(dates.to_list()) - known), "price dates off the calendar")]


_CHECKS: tuple[Callable[[_Tables], list[DqResult]], ...] = (
    _dq01_gate,
    _dq02_acceptance,
    _dq03_period_end,
    _dq04_link,
    _dq05_first_reported,
    _dq07_universe,
    _dq08_delistings,
    _dq09_prices,
    _dq10_flags,
    _dq12_labels,
    _dq17_calendar,
)


def run_checks(root: Path) -> list[DqResult]:
    """Run every check available from the PIT tables at ``root``.

    DQ-06 coverage is written by ``item_coverage``. DQ-11, DQ-13 and DQ-14 check signals,
    combination and benchmarks, which arrive in M3 to M6.
    """
    tables = _Tables(root)
    results: list[DqResult] = []
    for check in _CHECKS:
        results.extend(check(tables))
    results.extend(_dq16_label(root))
    return results


def pit_method_coverage(root: Path) -> pl.DataFrame:
    """Share of fundamentals rows per ``pit_method``."""
    fundamentals = _Tables(root)["fundamentals_pit"]
    return (
        fundamentals.group_by("pit_method")
        .agg(pl.len().alias("rows"))
        .with_columns((pl.col("rows") / pl.col("rows").sum()).alias("share"))
        .sort("pit_method")
    )


def item_coverage(root: Path) -> pl.DataFrame:
    """Companies reporting each item per calendar year (DQ-06)."""
    fundamentals = _Tables(root)["fundamentals_pit"]
    return (
        fundamentals.group_by(pl.col("period_end").dt.year().alias("year"), "item")
        .agg(pl.col("company_id").n_unique().alias("companies"))
        .sort("year", "item")
    )


def write_reports(root: Path, out: Path) -> list[DqResult]:
    """Run the checks and write ``dq_report.csv``, ``pit_method_coverage.csv`` and coverage."""
    results = run_checks(root)
    out.mkdir(parents=True, exist_ok=True)
    report = pl.DataFrame(
        {
            "check": [item.check for item in results],
            "status": [item.status for item in results],
            "hard": [item.hard for item in results],
            "detail": [item.detail for item in results],
        },
        schema={"check": pl.String, "status": pl.String, "hard": pl.Boolean, "detail": pl.String},
    )
    report.write_csv(out / "dq_report.csv", line_terminator="\n")
    pit_method_coverage(root).write_csv(out / "pit_method_coverage.csv", line_terminator="\n")
    item_coverage(root).write_csv(out / "item_coverage.csv", line_terminator="\n")
    return results
