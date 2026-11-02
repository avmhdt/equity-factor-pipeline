"""Every number the README and memos quote, taken from the tables and the trial log (FR-31)."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from efp.signals.registry import registered, spec_of

FAMILIES = ("value", "momentum", "quality", "investment", "low_risk")
FIXED = {
    "spec.hurdle": "3.0",
    "spec.weak_hurdle": "2",
    "spec.universe": "3,000",
    "spec.variant": "1,000",
    "spec.signals": "26",
    "spec.families": "5",
    "spec.min_replicated": "20",
    "spec.min_correlation": "0.70",
    "spec.french_size_momentum": "0.9",
    "spec.french_value": "0.8",
    "spec.leverage": "2.0",
    "spec.aum": "$100M",
    "spec.adv_cap": "10%",
    "spec.floor_bps": "2",
    "spec.impact": "1.0",
    "spec.adv_days": "20",
    "spec.borrow_top": "0.25%",
    "spec.borrow_mid": "1.0%",
    "spec.borrow_rest": "5.0%",
    "spec.tier_top": "500",
    "spec.tier_mid": "1,000",
    "spec.grid_low": "$10M",
    "spec.grid_high": "$10B",
    "spec.grid_points": "25",
    "spec.half": "50%",
    "spec.low_multiplier": "0.5",
    "spec.high_multiplier": "2",
    "spec.ic_window": "36",
    "spec.min_train": "120",
    "spec.purge": "1",
    "spec.start": "1998",
    "spec.end": "2025",
    "spec.paper_signals": "97",
    "spec.paper_post_sample": "26%",
    "spec.paper_post_publication": "58%",
    "spec.fiscal_lag": "6",
    "spec.extra_lag": "1",
    "spec.delist_nyse": "30%",
    "spec.delist_nasdaq": "55%",
    "spec.min_price": "$1",
    "spec.min_overlap": "240",
    "spec.spread_top_low": "16",
    "spec.spread_top_high": "25",
    "spec.position_adv": "3.7",
    "spec.capped_exposure": "0.38",
}
"""Settings the documents state: spec thresholds, D-03 to D-06 and measured diagnostics."""


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100.0:.{digits}f}%"


def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _usd(value: float) -> str:
    return f"${value / 1.0e9:.1f}B" if value >= 1.0e9 else f"${value / 1.0e6:.0f}M"


def _read(tables: Path, name: str) -> pl.DataFrame | None:
    path = tables / f"{name}.csv"
    return pl.read_csv(path) if path.is_file() else None


def _signals(tables: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    ic = _read(tables, "ic_summary")
    spreads = _read(tables, "quantile_spreads")
    osap = _read(tables, "osap_replication")
    decay = _read(tables, "post_publication_by_signal")
    if ic is not None:
        top = ic.filter(pl.col("universe").eq(ic["universe"].max()))
        for row in top.iter_rows(named=True):
            base = f"signal.{row['signal']}"
            suffix = "" if row["neutralize"] == "none" else "_neutral"
            found[f"{base}.ic{suffix}"] = _num(row["mean_ic"], 3)
            found[f"{base}.ic_t{suffix}"] = _num(row["t_nw"], 1)
    if spreads is not None:
        top = spreads.filter(pl.col("universe").eq(spreads["universe"].max()))
        for row in top.filter(pl.col("weighting").eq("vw")).iter_rows(named=True):
            base = f"signal.{row['signal']}"
            suffix = "" if row["neutralize"] == "none" else "_neutral"
            found[f"{base}.vw{suffix}"] = _pct(row["mean_monthly"] * 12.0)
            found[f"{base}.vw_t{suffix}"] = _num(row["t_nw"], 1)
    if osap is not None:
        for row in osap.iter_rows(named=True):
            found[f"signal.{row['signal']}.osap"] = _num(row["correlation"], 2)
    if decay is not None:
        for row in decay.iter_rows(named=True):
            base = f"signal.{row['signal']}"
            found[f"{base}.before"] = _pct(row["mean_in_sample"] * 12.0)
            found[f"{base}.after"] = _pct(row["mean_post_publication"] * 12.0)
            found[f"{base}.change"] = _pct(abs(row["decline"]), 0)
    return found


def _family_counts(tables: Path) -> dict[str, str]:
    ic = _read(tables, "ic_summary")
    if ic is None:
        return {}
    top = ic.filter(pl.col("universe").eq(ic["universe"].max()), pl.col("neutralize").eq("none"))
    found: dict[str, str] = {}
    for family in FAMILIES:
        part = top.filter(pl.col("family").eq(family))
        found[f"family.{family}.signals"] = str(part.height)
        found[f"family.{family}.over_hurdle"] = str(part.filter(pl.col("t_nw").abs() > 3.0).height)
    return found


def _book(row: dict[str, object], base: str) -> dict[str, str]:
    return {
        f"{base}.gross_mean": _pct(float(row["gross_mean_annual"])),  # type: ignore[arg-type]
        f"{base}.net_mean": _pct(float(row["net_mean_annual"])),  # type: ignore[arg-type]
        f"{base}.capped_mean": _pct(float(row["capped_net_mean_annual"])),  # type: ignore[arg-type]
        f"{base}.gross_sharpe": _num(float(row["gross_sharpe"])),  # type: ignore[arg-type]
        f"{base}.net_sharpe": _num(float(row["net_sharpe"])),  # type: ignore[arg-type]
        f"{base}.capped_sharpe": _num(float(row["capped_net_sharpe"])),  # type: ignore[arg-type]
        f"{base}.turnover": _pct(float(row["turnover_monthly"]) / 2.0, 0),  # type: ignore[arg-type]
        f"{base}.spread": _pct(float(row["spread_cost_annual"])),  # type: ignore[arg-type]
        f"{base}.impact": _pct(float(row["impact_cost_annual"])),  # type: ignore[arg-type]
        f"{base}.borrow": _pct(float(row["borrow_cost_annual"])),  # type: ignore[arg-type]
        f"{base}.total_cost": _pct(
            float(row["spread_cost_annual"])  # type: ignore[arg-type]
            + float(row["impact_cost_annual"])  # type: ignore[arg-type]
            + float(row["borrow_cost_annual"])  # type: ignore[arg-type]
        ),
        f"{base}.months": str(row["months"]),
    }


def _named_books(headline: str) -> dict[str, str]:
    books = {f"family_{family}_sector_size_beta": f"family.{family}" for family in FAMILIES}
    books[headline] = "headline"
    books[f"{headline}_top1000"] = "top1000"
    for method in ("ic_weighted", "ridge"):
        books[f"{method}_sector_size_beta"] = f"method.{method}"
    books["equal_none"] = "method.equal_raw"
    return books


def _portfolio(tables: Path, headline: str, execution: str) -> dict[str, str]:
    found: dict[str, str] = {}
    books = _named_books(headline)
    summary = _read(tables, "backtest_summary")
    if summary is not None:
        rows = summary.filter(pl.col("execution").eq(execution))
        for row in rows.iter_rows(named=True):
            if row["composite"] in books:
                found |= _book(row, books[row["composite"]])
        for row in summary.filter(pl.col("composite").eq(headline)).iter_rows(named=True):
            mode = row["execution"]
            found[f"timing.{mode}.gross"] = _num(row["gross_sharpe"])
            found[f"timing.{mode}.net"] = _num(row["net_sharpe"])
    attribution = _read(tables, "attribution")
    if attribution is not None:
        for row in attribution.iter_rows(named=True):
            if row["composite"] not in books:
                continue
            base = f"{books[row['composite']]}.{row['basis']}"
            found[f"{base}_alpha"] = _pct(row["alpha_annual"])
            found[f"{base}_alpha_t"] = _num(row["alpha_t"], 1)
            found[f"{base}_r2"] = _num(row["r_squared"], 2)
            for name in ("mkt_rf", "smb", "hml", "rmw", "cma", "umd"):
                found[f"{base}_{name}"] = _num(row[name], 2)
    sensitivity = _read(tables, "cost_sensitivity")
    if sensitivity is not None:
        for row in sensitivity.iter_rows(named=True):
            if row["composite"] in books:
                base = f"{books[row['composite']]}.cost_{row['cost_multiplier']:g}"
                found[f"{base}.uncapped_mean"] = _pct(row["uncapped_net_mean_annual"])
                found[f"{base}.uncapped_sharpe"] = _num(row["uncapped_net_sharpe"])
                found[f"{base}.capped_mean"] = _pct(row["capped_net_mean_annual"])
                found[f"{base}.capped_sharpe"] = _num(row["capped_net_sharpe"])
    capacity = _read(tables, "capacity")
    if capacity is not None:
        for row in capacity.filter(pl.col("impact_multiplier").eq(1.0)).iter_rows(named=True):
            if row["composite"] in books:
                base = books[row["composite"]]
                found[f"{base}.capacity"] = str(row["status"])
                found[f"{base}.zero_cost_alpha"] = _pct(row["zero_cost_alpha_annual"])
                found[f"{base}.half_alpha"] = _pct(row["zero_cost_alpha_annual"] / 2.0)
    curve = _read(tables, "capacity_curve")
    if curve is not None:
        for row in curve.filter(pl.col("impact_multiplier").eq(1.0)).iter_rows(named=True):
            if row["composite"] in books and row["aum"] in (1.0e7, 1.0e8, 1.0e9, 1.0e10):
                base = books[row["composite"]]
                found[f"{base}.alpha_at_{_usd(row['aum'])}"] = _pct(row["net_alpha_annual"])
    return found


def _validation(tables: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    french = _read(tables, "french_replication")
    if french is not None:
        for row in french.iter_rows(named=True):
            found[f"french.{row['factor']}"] = _num(row["correlation"], 3)
    osap = _read(tables, "osap_replication")
    if osap is not None:
        found["osap.passed"] = str(int(osap["passed"].sum()))
        found["osap.total"] = str(osap.height)
        found["osap.failed"] = str(osap.height - int(osap["passed"].sum()))
        kept = osap.filter(pl.col("passed"))
        found["osap.lowest"] = _num(float(kept["correlation"].min()), 2)  # type: ignore[arg-type]
        found["osap.shortest"] = str(kept["months"].min())
        found["osap.longest"] = str(kept["months"].max())
    post = _read(tables, "post_publication")
    if post is not None:
        for row in post.iter_rows(named=True):
            if row["sample"].startswith("OSAP"):
                found["postpub.signals"] = str(int(row["signals"]))
                found["postpub.post_sample"] = _pct(abs(row["post_sample"]), 0)
                found["postpub.post_sample_t"] = _num(row["post_sample_t"], 1)
                found["postpub.after"] = _pct(abs(row["post_publication"]), 0)
                found["postpub.after_t"] = _num(row["post_publication_t"], 1)
            if row["sample"].startswith("Ours"):
                found["postpub.ours_signals"] = str(int(row["signals"]))
                found["postpub.ours_after"] = _pct(row["post_publication"], 0)
                found["postpub.ours_after_t"] = _num(row["post_publication_t"], 1)
    bias = _read(tables, "survivorship_bias")
    if bias is not None:
        row = bias.filter(pl.col("series").eq("ew_universe")).row(0, named=True)
        found["survivorship.mean_full"] = _pct(row["mean_annual_full"])
        found["survivorship.mean_survivors"] = _pct(row["mean_annual_survivors_only"])
        found["survivorship.bias"] = _pct(row["bias_mean"])
        found["survivorship.sharpe_full"] = _num(row["sharpe_full"])
        found["survivorship.sharpe_survivors"] = _num(row["sharpe_survivors_only"])
    gap = _read(tables, "value_gap_decomposition")
    if gap is not None:
        for row in gap.iter_rows(named=True):
            found[f"gap.{row['step']}.correlation"] = _num(row["correlation_with_hml"], 2)
            found[f"gap.{row['step']}.mean"] = _pct(row["mean_monthly"] * 12.0)
    coverage = _read(tables, "pit_method_coverage")
    if coverage is not None:
        row = coverage.filter(pl.col("pit_method").eq("acceptance")).row(0, named=True)
        found["pit.acceptance"] = _pct(row["share"])
    return found


def _trials(tables: Path, log_path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    if log_path.is_file():
        log = pl.read_csv(
            log_path, schema_overrides={"sharpe_annual": pl.Float64, "x_note": pl.String}
        )
        found["trials.total"] = str(log.height)
        counts = log.group_by("strategy_family").len()
        for family, rows in counts.iter_rows():
            found[f"trials.{family}"] = str(rows)
        found["trials.dirty"] = str(log.filter(pl.col("git_sha").str.ends_with("-dirty")).height)
    table = _read(tables, "multiple_testing")
    if table is not None:
        positive = table.filter(pl.col("t_nw") > 0)
        for name, frame in (("all", table), ("positive", positive)):
            found[f"testing.{name}.over_2"] = str(frame.filter(pl.col("t_nw").abs() > 2.0).height)
            found[f"testing.{name}.over_3"] = str(frame.filter(pl.col("t_nw").abs() > 3.0).height)
            found[f"testing.{name}.bhy"] = str(int(frame["bhy_5pct"].sum()))
    return found


def build_numbers(tables: Path, log_path: Path, headline: str, execution: str) -> dict[str, str]:
    """Named strings, exactly as the documents print them.

    Args:
        tables: directory of CSV tables; a missing table leaves its names out.
        log_path: the trial log.
        headline: name of the headline composite.
        execution: the headline execution mode.
    """
    found = dict(FIXED)
    found |= _signals(tables)
    found |= _family_counts(tables)
    found |= _portfolio(tables, headline, execution)
    found |= _validation(tables)
    found |= _trials(tables, log_path)
    for name in registered():
        found.setdefault(f"signal.{name}.reference", spec_of(name).reference)
    return dict(sorted(found.items()))


def write_numbers(numbers: dict[str, str], path: Path) -> None:
    """Write the JSON the README cites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(numbers, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
