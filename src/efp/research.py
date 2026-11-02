"""Research tasks: the CLI commands and the rebuild stages run the same code."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import polars as pl

from efp.attribution.ff import FACTORS, attribute
from efp.backtest.capacity import capacity_curve
from efp.backtest.engine import BacktestResult, SimulationConfig
from efp.backtest.portfolio import target_weights
from efp.backtest.run import BacktestConfig, Market, frictionless, run_book, summary_row
from efp.combine.walkforward import CombineConfig, Method, build_composites
from efp.evaluation.french import compare, load_panels, replicate
from efp.evaluation.multiple_testing import hurdle_table, trial_statistics
from efp.evaluation.osap import replicate_all
from efp.evaluation.postpub import per_signal, pooled_regression, summary_rows, tag_periods
from efp.evaluation.run import merge_rows
from efp.evaluation.survivorship import bias_table, delisted_names_by_year, series_by_mode
from efp.evaluation.value_gap import decompose
from efp.explog import TrialRecord, log_trial
from efp.panel import all_signals
from efp.reports.figures import capacity_lines, post_publication_bars
from efp.reports.tables import write_table
from efp.signals.registry import spec_of
from eqpit import calendar
from eqpit.benchmarks import load_french, load_osap_returns, load_signal_doc
from eqpit.store import PitStore

LICENSED = ("sharadar", "wrds")
EXECUTION_LABELS = {
    "close": "same close",
    "next_open": "next open",
    "next_close": "next close",
    "next_vwap_proxy": "next-day VWAP proxy, (O+H+L+C)/4",
}


@dataclass(frozen=True)
class Sink:
    """Where a task writes.

    Args:
        tables: directory for CSV and Markdown tables.
        figures: directory for PNG figures.
        log_path: the trial log, or None to log nothing.
        once: skip trials the log already holds (a rebuild repeating them).
    """

    tables: Path
    figures: Path
    log_path: Path | None = None
    once: bool = False

    def record(self, trial: TrialRecord) -> str:
        """Log one trial; returns its run id, empty when logging is off."""
        if self.log_path is None:
            return ""
        return log_trial(trial, self.log_path, self.log_path.parent / "runs", self.once)


def rebalance_dates(store: PitStore, start: date | None, end: date | None) -> list[date]:
    """Month ends with prices, inside the experiment's window when one is given."""
    prices = pl.scan_parquet(store.root / "prices_monthly" / "**" / "*.parquet")
    bounds = prices.select(
        pl.col("month").min().alias("first"), pl.col("month").max().alias("last")
    ).collect()
    first: date = bounds["first"][0]
    last: date = bounds["last"][0]
    if start is not None:
        first = max(first, start)
    if end is not None:
        last = min(last, end)
    return store.month_ends(first, last)


def _log_series(series: pl.DataFrame, name: str, provider: str, universe: str, sink: Sink) -> None:
    """One contract row per factor series. ``ret`` is already a long-short excess return."""
    sink.record(
        TrialRecord(
            "replication",
            {
                "factor": name,
                "construction": "ff_2x3_nyse",
                "universe": universe,
                "provider": provider,
                "freq": "M",
                "net": False,
            },
            series.select(pl.col("month").alias("date"), "ret"),
            "M",
        )
    )


def french_task(store: PitStore, external: Path, sink: Sink, provider: str) -> pl.DataFrame:
    """FF factors from our data against French's."""
    ours = replicate(store)
    table = compare(ours, load_french(external / "french"))
    write_table(table, sink.tables, "french_replication")
    for (name,), part in ours.group_by(["factor"], maintain_order=True):
        _log_series(part.sort("month"), str(name), provider, "all_eligible", sink)
    return table


def survivorship_task(store: PitStore, sink: Sink, provider: str) -> pl.DataFrame:
    """With delisted names against survivors only."""
    series = series_by_mode(store)
    table = bias_table(series)
    write_table(table, sink.tables, "survivorship_bias")
    write_table(delisted_names_by_year(store), sink.tables, "universe_delisted_by_year")
    for (name, mode), part in series.group_by(["series", "mode"], maintain_order=True):
        _log_series(part.sort("month"), str(name), provider, str(mode), sink)
    return table


def osap_task(
    store: PitStore,
    panel: pl.DataFrame,
    external: Path,
    series_path: Path,
    sink: Sink,
    provider: str,
    only: Sequence[str] | None = None,
) -> pl.DataFrame:
    """OSAP replication; with ``only``, replace those signals' rows and keep the rest."""
    root = external / "osap"
    table, series = replicate_all(
        panel, load_panels(store), load_signal_doc(root), load_osap_returns(root), store
    )
    full = series
    if only is not None and series_path.is_file():
        kept = pl.read_parquet(series_path).filter(~pl.col("signal").is_in(list(only)))
        full = pl.concat([kept, series], how="vertical")
        existing = sink.tables / "osap_replication.csv"
        table = merge_rows(pl.read_csv(existing), table, only) if existing.is_file() else table
    write_table(table, sink.tables, "osap_replication")
    series_path.parent.mkdir(parents=True, exist_ok=True)
    full.write_parquet(series_path, compression="zstd")
    for (name,), part in series.group_by(["signal"], maintain_order=True):
        _log_series(part.sort("month"), f"osap_{name}", provider, "all_eligible", sink)
    return table


def postpub_task(series_path: Path, external: Path, sink: Sink, dev_only: bool) -> pl.DataFrame:
    """McLean-Pontiff regressions on our series and on OSAP's."""
    root = external / "osap"
    doc = load_signal_doc(root)
    targets = pl.DataFrame(
        {
            "signal": list(all_signals()),
            "target": [spec_of(name).osap_target for name in all_signals()],
        }
    ).drop_nulls("target")
    ours = pl.read_parquet(series_path).join(targets, on="signal")
    published = load_osap_returns(root).join(targets, on="target")
    tagged_ours = tag_periods(ours, doc)
    tagged_published = tag_periods(published, doc)
    summary = summary_rows(
        {
            "Ours, Sharadar 1998 on": pooled_regression(tagged_ours),
            "OSAP published series": pooled_regression(tagged_published),
        }
    )
    write_table(summary, sink.tables, "post_publication")
    by_signal = per_signal(tagged_published)
    write_table(by_signal, sink.tables, "post_publication_by_signal")
    if not dev_only:
        post_publication_bars(by_signal, sink.figures / "post_publication.png")
    return summary


def multiple_testing_task(log_path: Path, sink: Sink) -> pl.DataFrame:
    """Hurdle flags and BHY across every logged trial."""
    table = hurdle_table(trial_statistics(log_path))
    write_table(table, sink.tables, "multiple_testing")
    return table


def value_gap_task(
    store: PitStore, panel: pl.DataFrame, external: Path, sink: Sink
) -> pl.DataFrame:
    """Steps from HML to our value factor. ``panel`` holds ``bm``."""
    table = decompose(
        store,
        load_panels(store),
        panel,
        load_french(external / "french"),
        store.labels().forward_returns((1,)),
    )
    write_table(table, sink.tables, "value_gap_decomposition")
    return table


def _composite_trial(
    name: str,
    scores: pl.DataFrame,
    labels: pl.DataFrame,
    signals: list[str],
    provider: str,
    size: int,
) -> TrialRecord:
    """A composite's own trial: equal-weight deciles, close to close, no costs."""
    weights = target_weights(scores)
    spread = (
        weights.join(labels, on=["asof", "security_id"], how="inner")
        .sort("asof", "security_id")
        .group_by("asof", maintain_order=True)
        .agg((pl.col("weight") * pl.col("fwd_ret")).sum().alias("ret"))
        .sort("asof")
    )
    return TrialRecord(
        "composite",
        {
            "composite": name,
            "signals": signals,
            "construction": "q10_top_minus_bottom",
            "weighting": "ew",
            "execution": "close",
            "costs": "none",
            "universe": size,
            "provider": provider,
            "freq": "M",
            "net": False,
        },
        spread.select(pl.col("asof").alias("date"), "ret"),
        "M",
    )


def combine_task(
    panel: pl.DataFrame,
    fwd: pl.DataFrame,
    methods: Sequence[Method],
    modes: Sequence[str],
    family_mode: str,
    config: CombineConfig,
    target: Path,
    sink: Sink,
    provider: str,
    size: int,
) -> dict[str, pl.DataFrame]:
    """Write every composite to ``target`` and log one trial for each."""
    built = build_composites(panel, fwd, methods, modes, family_mode, config)
    target.mkdir(parents=True, exist_ok=True)
    labels = fwd.filter(pl.col("h").eq(1)).select("asof", "security_id", "fwd_ret")
    signals = sorted(panel["signal"].unique().to_list())
    for name, scores in built.items():
        scores.write_parquet(target / f"{name}.parquet", compression="zstd")
        sink.record(_composite_trial(name, scores, labels, signals, provider, size))
    return built


def _simulation(config: BacktestConfig, gross_only: bool) -> SimulationConfig:
    base = SimulationConfig(aum=config.aum, costs=config.costs)
    return frictionless(base) if gross_only else base


def _backtest_trial(
    name: str, mode: str, series: pl.DataFrame, config: BacktestConfig, provider: str, net: bool
) -> TrialRecord:
    return TrialRecord(
        "composite",
        {
            "composite": name,
            "construction": config.construction,
            "gross_leverage": config.gross,
            "execution": mode,
            "aum_usd": config.aum,
            "costs": json.loads(json.dumps(asdict(config.costs))) if net else "none",
            "provider": provider,
            "freq": "M",
            "net": net,
        },
        series.select(pl.col("month").alias("date"), "ret", "ret_gross"),
        "M",
    )


def backtest_task(
    composites: Path,
    market: Market,
    config: BacktestConfig,
    headline: str,
    sink: Sink,
    series_dir: Path,
    provider: str,
    gross_only: bool = False,
) -> pl.DataFrame:
    """Every composite in every execution mode; writes the summary and timing tables."""
    series_dir.mkdir(parents=True, exist_ok=True)
    simulation = _simulation(config, gross_only)
    rows: list[dict[str, object]] = []
    for path in sorted(composites.glob("*.parquet")):
        scores = pl.read_parquet(path)
        for mode in config.executions:
            result = run_book(scores, market, mode, simulation, config)
            result.series.write_parquet(series_dir / f"{path.stem}__{mode}.parquet")
            rows.append(summary_row(path.stem, mode, result, simulation.aum))
            sink.record(
                _backtest_trial(path.stem, mode, result.series, config, provider, not gross_only)
            )
    summary = pl.DataFrame(rows, infer_schema_length=None).sort("composite", "execution")
    write_table(summary, sink.tables, "backtest_summary")
    timing = summary.filter(
        pl.col("composite").eq(headline) | pl.col("composite").str.starts_with("family_")
    ).select(
        "composite",
        "execution",
        pl.col("execution").replace_strict(EXECUTION_LABELS, return_dtype=pl.String).alias("price"),
        "gross_sharpe",
        "net_sharpe",
    )
    write_table(timing, sink.tables, "execution_timing")
    return summary


def own_factors(store: PitStore) -> pl.DataFrame:
    """Factors built from the store, where no benchmark file exists (synthetic runs)."""
    wide = replicate(store).pivot(on="factor", index="month", values="ret").sort("month")
    daily = pl.read_parquet(store.root / "market_daily" / "**" / "*.parquet").sort("date")
    ends = wide["month"].to_list()
    market = (
        daily.with_columns(calendar.month_end_of(daily["date"], ends).alias("month"))
        .drop_nulls("month")
        .group_by("month", maintain_order=True)
        .agg(((1.0 + pl.col("ret")).product() - 1.0).alias("mkt_rf"))
    )
    return wide.join(market, on="month", how="inner")


def factor_table(store: PitStore, external: Path, provider: str) -> pl.DataFrame:
    """Monthly FF5 + UMD: French's published files, or our own build on synthetic data."""
    frame = load_french(external / "french") if provider in LICENSED else own_factors(store)
    return frame.select("month", *FACTORS).drop_nulls().sort("month")


def capacity_task(
    scores: pl.DataFrame,
    market: Market,
    factors: pl.DataFrame,
    config: BacktestConfig,
    sink: Sink,
    dev_only: bool,
) -> pl.DataFrame:
    """Capacity of the headline composite at each impact multiplier (FR-24, AC-H-05)."""
    mode = config.headline_execution
    base = SimulationConfig(aum=config.aum, costs=config.costs)

    def run(simulation: SimulationConfig) -> BacktestResult:
        return run_book(scores, market, mode, simulation, config)

    rows: list[dict[str, object]] = []
    curves: list[pl.DataFrame] = []
    target = 0.0
    for multiplier in config.impact_multipliers:
        result = capacity_curve(
            run, factors, replace(base, impact_scale=multiplier), config.capacity
        )
        target = result.zero_cost_alpha / 2.0
        rows.append(
            {
                "impact_multiplier": multiplier,
                "execution": mode,
                "max_adv_participation": config.capacity.max_participation,
                "zero_cost_alpha_annual": result.zero_cost_alpha,
                "capacity_usd": result.capacity,
                "status": result.status,
            }
        )
        curves.append(result.curve.with_columns(pl.lit(multiplier).alias("impact_multiplier")))
    table = pl.DataFrame(rows, infer_schema_length=None)
    curve = pl.concat(curves, how="vertical").select(
        "impact_multiplier", "aum", "net_alpha_annual", "net_mean_annual", "turnover"
    )
    write_table(table, sink.tables, "capacity")
    write_table(curve, sink.tables, "capacity_curve")
    capacity_lines(curve, target, sink.figures / "capacity_curve.png", dev_only)
    return table


def attribution_task(
    series_dir: Path, factors: pl.DataFrame, config: BacktestConfig, sink: Sink
) -> pl.DataFrame:
    """FF5 + UMD alpha and loadings, gross and net, at the headline execution (AC-H-04)."""
    rows: list[dict[str, object]] = []
    for path in sorted(series_dir.glob(f"*__{config.headline_execution}.parquet")):
        series = pl.read_parquet(path)
        for basis, column in (("gross", "ret_gross"), ("net", "ret")):
            fitted = attribute(series, factors, column=column)
            rows.append(
                {
                    "composite": path.stem.split("__")[0],
                    "basis": basis,
                    "execution": config.headline_execution,
                    "alpha_annual": fitted.alpha_annual,
                    "alpha_t": fitted.alpha_t,
                    **fitted.loadings,
                    **{f"{name}_t": value for name, value in fitted.loading_t.items()},
                    "r_squared": fitted.r_squared,
                    "months": fitted.months,
                    "nw_lags": fitted.lags,
                }
            )
    table = pl.DataFrame(rows, infer_schema_length=None).sort("composite", "basis")
    write_table(table, sink.tables, "attribution")
    return table
