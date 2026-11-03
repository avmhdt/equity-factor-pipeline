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
from efp.backtest.costs import SPREAD_MODELS
from efp.backtest.engine import BacktestResult, SimulationConfig
from efp.backtest.impact_study import (
    LAWS,
    SPREADS,
    cost_checks,
    simulation_for,
    trade_profile,
)
from efp.backtest.portfolio import target_weights
from efp.backtest.run import (
    BacktestConfig,
    Market,
    frictionless,
    load_market,
    run_book,
    summary_row,
)
from efp.backtest.spread_study import (
    benchmark_checks,
    capacity_rows,
    model_rows,
    run_model,
    spread_estimates,
)
from efp.combine.walkforward import (
    CombineConfig,
    Method,
    build_composites,
    variant_composites,
)
from efp.evaluation.french import compare, load_panels, replicate
from efp.evaluation.multiple_testing import hurdle_table, trial_statistics
from efp.evaluation.osap import replicate_all
from efp.evaluation.postpub import per_signal, pooled_regression, summary_rows, tag_periods
from efp.evaluation.run import merge_rows
from efp.evaluation.survivorship import bias_table, delisted_names_by_year, series_by_mode
from efp.evaluation.value_gap import decompose
from efp.explog import TrialRecord, log_trial
from efp.panel import all_signals
from efp.reports.figures import (
    capacity_lines,
    impact_model_bars,
    post_publication_bars,
    spread_model_bars,
)
from efp.reports.tables import write_table
from efp.signals.registry import spec_of
from eqpit import calendar
from eqpit.benchmarks import load_french, load_osap_returns, load_signal_doc
from eqpit.store import PitStore

LICENSED = ("sharadar", "wrds")
LATE_SPREAD_NOTE = "spread model run after the Corwin-Schultz net failure"
LATE_IMPACT_NOTE = "impact model run after the net failure, second round"
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


def french_task(
    store: PitStore, external: Path, sink: Sink, provider: str, series_path: Path | None = None
) -> pl.DataFrame:
    """FF factors from our data against French's; ``series_path`` keeps ours for figures."""
    ours = replicate(store)
    table = compare(ours, load_french(external / "french"))
    write_table(table, sink.tables, "french_replication")
    if series_path is not None:
        series_path.parent.mkdir(parents=True, exist_ok=True)
        ours.write_parquet(series_path, compression="zstd")
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
    ranks: pl.DataFrame,
    methods: Sequence[Method],
    modes: Sequence[str],
    headline: tuple[Method, str],
    sizes: Sequence[int],
    config: CombineConfig,
    target: Path,
    sink: Sink,
    provider: str,
    size: int,
) -> dict[str, pl.DataFrame]:
    """Write every composite to ``target`` and log one trial for each.

    Args:
        panel: the signal panel.
        fwd: forward returns.
        ranks: ``asof``, ``security_id``, ``rank``.
        methods: composite methods.
        modes: neutralization modes.
        headline: the headline method and its mode, which families use too.
        sizes: smaller universes for the headline composite.
        config: composite choices.
        target: output directory.
        sink: tables and trial log.
        provider: data provider.
        size: headline universe size.
    """
    method, mode = headline
    built = build_composites(panel, fwd, methods, modes, mode, config)
    built |= variant_composites(panel, fwd, ranks, sizes, method, mode, config)
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
    name: str,
    mode: str,
    series: pl.DataFrame,
    simulation: SimulationConfig,
    config: BacktestConfig,
    provider: str,
    net: bool,
    impact_law: str = "",
) -> TrialRecord:
    costs = asdict(config.costs)
    model = costs.pop("spread_model")
    params: dict[str, object] = {
        "composite": name,
        "construction": config.construction,
        "gross_leverage": config.gross,
        "execution": mode,
        "aum_usd": config.aum,
        "max_adv_participation": simulation.max_participation,
        "cost_scale": simulation.spread_scale,
        "costs": json.loads(json.dumps(costs)) if net else "none",
        "provider": provider,
        "freq": "M",
        "net": net,
    }
    note = ""
    if model != SPREAD_MODELS[0]:
        params["spread_model"] = model
        note = LATE_SPREAD_NOTE
    if impact_law:
        params["impact_model"] = impact_law
        note = LATE_IMPACT_NOTE
    return TrialRecord(
        "composite",
        params,
        series.select(pl.col("month").alias("date"), "ret", "ret_gross"),
        "M",
        note,
    )


def _cost_rows(
    name: str,
    scores: pl.DataFrame,
    market: Market,
    config: BacktestConfig,
    sink: Sink,
    provider: str,
) -> list[dict[str, object]]:
    """D-06's sensitivity: every cost scaled together, with and without the ADV cap."""
    mode = config.headline_execution
    limit = config.capacity.max_participation
    rows: list[dict[str, object]] = []
    for scale in config.impact_multipliers:
        row: dict[str, object] = {"composite": name, "execution": mode, "cost_multiplier": scale}
        for label, participation in (("uncapped", None), ("capped", limit)):
            simulation = SimulationConfig(
                aum=config.aum,
                max_participation=participation,
                spread_scale=scale,
                impact_scale=scale,
                borrow_scale=scale,
                costs=config.costs,
            )
            result = run_book(scores, market, mode, simulation, config)
            summary = summary_row(name, mode, result, config.aum)
            row[f"{label}_net_mean_annual"] = summary["net_mean_annual"]
            row[f"{label}_net_sharpe"] = summary["net_sharpe"]
            if scale != 1.0:
                sink.record(
                    _backtest_trial(name, mode, result.series, simulation, config, provider, True)
                )
        rows.append(row)
    return rows


def backtest_task(
    composites: Path,
    market: Market,
    config: BacktestConfig,
    names: Sequence[str],
    sink: Sink,
    series_dir: Path,
    provider: str,
    gross_only: bool = False,
) -> pl.DataFrame:
    """Every composite in every execution mode; writes the summary, timing and cost tables.

    Args:
        composites: directory of composite score files.
        market: from ``load_market``.
        config: backtest choices.
        names: the headline composite, then its universe variants.
        sink: tables and trial log.
        series_dir: where monthly series go.
        provider: data provider.
        gross_only: remove every cost.
    """
    series_dir.mkdir(parents=True, exist_ok=True)
    simulation = _simulation(config, gross_only)
    limit = config.capacity.max_participation
    capped_run = replace(simulation, max_participation=limit)
    rows: list[dict[str, object]] = []
    sensitivity: list[dict[str, object]] = []
    for path in sorted(composites.glob("*.parquet")):
        scores = pl.read_parquet(path)
        for mode in config.executions:
            result = run_book(scores, market, mode, simulation, config)
            capped = run_book(scores, market, mode, capped_run, config)
            result.series.write_parquet(series_dir / f"{path.stem}__{mode}.parquet")
            capped.series.write_parquet(series_dir / f"{path.stem}__{mode}__capped.parquet")
            row = summary_row(path.stem, mode, result, simulation.aum)
            capped_row = summary_row(path.stem, mode, capped, simulation.aum)
            rows.append(
                row
                | {
                    "capped_net_mean_annual": capped_row["net_mean_annual"],
                    "capped_net_sharpe": capped_row["net_sharpe"],
                    "capped_turnover_monthly": capped_row["turnover_monthly"],
                    "capped_worst_net_exposure": capped_row["worst_net_exposure"],
                }
            )
            net = not gross_only
            sink.record(
                _backtest_trial(path.stem, mode, result.series, simulation, config, provider, net)
            )
            sink.record(
                _backtest_trial(path.stem, mode, capped.series, capped_run, config, provider, net)
            )
        if path.stem in names and not gross_only:
            sensitivity += _cost_rows(path.stem, scores, market, config, sink, provider)
    summary = pl.DataFrame(rows, infer_schema_length=None).sort("composite", "execution")
    write_table(summary, sink.tables, "backtest_summary")
    timing = summary.filter(
        pl.col("composite").eq(names[0]) | pl.col("composite").str.starts_with("family_")
    ).select(
        "composite",
        "execution",
        pl.col("execution").replace_strict(EXECUTION_LABELS, return_dtype=pl.String).alias("price"),
        "gross_sharpe",
        "net_sharpe",
        "capped_net_sharpe",
    )
    write_table(timing, sink.tables, "execution_timing")
    if sensitivity:
        write_table(pl.DataFrame(sensitivity), sink.tables, "cost_sensitivity")
    return summary


def spread_model_task(
    store: PitStore,
    ends: list[date],
    composites: Path,
    names: Sequence[str],
    factors: pl.DataFrame,
    config: BacktestConfig,
    sink: Sink,
    provider: str,
    dev_only: bool,
) -> pl.DataFrame:
    """Every spread model side by side: net results, capacity and a check against published data.

    Args:
        store: the point-in-time store.
        ends: month ends.
        composites: directory of composite score files.
        names: the composites that get a capacity test.
        factors: monthly factors.
        config: backtest choices, on the headline spread model.
        sink: tables and trial log.
        provider: data provider.
        dev_only: watermark figures.
    """
    mode = config.headline_execution
    rows: list[dict[str, object]] = []
    capacity: list[dict[str, object]] = []
    paths = [
        path
        for path in sorted(composites.glob("*.parquet"))
        if path.stem in names or path.stem.startswith("family_")
    ]
    for model in SPREAD_MODELS:
        chosen = replace(
            config, costs=replace(config.costs, spread_model=model), executions=(mode,)
        )
        market = load_market(store, ends, chosen)
        for path in paths:
            scores = pl.read_parquet(path)
            run = run_model(path.stem, model, scores, market, chosen)
            rows += model_rows(run, factors, chosen.aum)
            if model != SPREAD_MODELS[0]:
                limit = chosen.capacity.max_participation
                base = SimulationConfig(aum=chosen.aum, costs=chosen.costs)
                capped = replace(base, max_participation=limit)
                for result, simulation in ((run.uncapped, base), (run.capped, capped)):
                    sink.record(
                        _backtest_trial(
                            path.stem, mode, result.series, simulation, chosen, provider, True
                        )
                    )
            if path.stem in names:

                def book(
                    simulation: SimulationConfig,
                    scores: pl.DataFrame = scores,
                    market: Market = market,
                    chosen: BacktestConfig = chosen,
                ) -> BacktestResult:
                    return run_book(scores, market, mode, simulation, chosen)

                capacity += capacity_rows(path.stem, model, book, factors, chosen)
    table = pl.DataFrame(rows, infer_schema_length=None)
    write_table(table, sink.tables, "spread_models")
    write_table(
        pl.DataFrame(capacity, infer_schema_length=None), sink.tables, "spread_model_capacity"
    )
    estimates = spread_estimates(store, ends, SPREAD_MODELS, config.costs)
    checks = benchmark_checks(estimates)
    write_table(checks, sink.tables, "spread_model_checks")
    spread_model_bars(
        checks,
        table.filter(pl.col("composite").eq(names[0])),
        sink.figures / "spread_models.png",
        dev_only,
    )
    return table


def impact_model_task(
    store: PitStore,
    ends: list[date],
    composites: Path,
    names: Sequence[str],
    factors: pl.DataFrame,
    config: BacktestConfig,
    sink: Sink,
    provider: str,
    dev_only: bool,
) -> pl.DataFrame:
    """Each published impact law with each spread model, capped and uncapped, with capacity.

    Args:
        store: the point-in-time store.
        ends: month ends.
        composites: directory of composite score files.
        names: the composites that get a capacity test.
        factors: monthly factors.
        config: backtest choices, on the headline models.
        sink: tables and trial log.
        provider: data provider.
        dev_only: watermark figures.
    """
    mode = config.headline_execution
    rows: list[dict[str, object]] = []
    capacity: list[dict[str, object]] = []
    profiles: list[dict[str, object]] = []
    paths = [
        path
        for path in sorted(composites.glob("*.parquet"))
        if path.stem in names or path.stem.startswith("family_")
    ]
    for spread in SPREADS:
        chosen = replace(
            config, costs=replace(config.costs, spread_model=spread), executions=(mode,)
        )
        market = load_market(store, ends, chosen)
        for path in paths:
            scores = pl.read_parquet(path)
            if spread == SPREADS[0] and path.stem in names:
                profiles.append(trade_profile(path.stem, scores, market, chosen))
            for law in LAWS:
                base = simulation_for(law, chosen)
                run = run_model(path.stem, spread, scores, market, chosen, base)
                rows += [
                    row | {"impact_model": law.name} for row in model_rows(run, factors, chosen.aum)
                ]
                if law is not LAWS[0]:
                    limit = chosen.capacity.max_participation
                    capped = replace(base, max_participation=limit)
                    for result, simulation in ((run.uncapped, base), (run.capped, capped)):
                        sink.record(
                            _backtest_trial(
                                path.stem,
                                mode,
                                result.series,
                                simulation,
                                chosen,
                                provider,
                                True,
                                law.name,
                            )
                        )
                if path.stem in names:

                    def book(
                        simulation: SimulationConfig,
                        scores: pl.DataFrame = scores,
                        market: Market = market,
                        chosen: BacktestConfig = chosen,
                    ) -> BacktestResult:
                        return run_book(scores, market, mode, simulation, chosen)

                    found = capacity_rows(path.stem, spread, book, factors, chosen, base, (1.0,))
                    capacity += [row | {"impact_model": law.name} for row in found]
    table = pl.DataFrame(rows, infer_schema_length=None)
    leading = ("composite", "impact_model", "spread_model")
    table = table.select(*leading, pl.exclude(*leading))
    write_table(table, sink.tables, "impact_models")
    limits = pl.DataFrame(capacity, infer_schema_length=None)
    limits = limits.select(*leading, pl.exclude(*leading))
    write_table(limits, sink.tables, "impact_model_capacity")
    checks = cost_checks()
    write_table(checks, sink.tables, "impact_model_checks")
    write_table(pl.DataFrame(profiles), sink.tables, "impact_trade_profile")
    impact_model_bars(
        checks,
        table.filter(pl.col("composite").eq(names[0])),
        sink.figures / "impact_models.png",
        dev_only,
    )
    return table


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
    composites: dict[str, pl.DataFrame],
    market: Market,
    factors: pl.DataFrame,
    config: BacktestConfig,
    sink: Sink,
    dev_only: bool,
) -> pl.DataFrame:
    """Capacity of each composite at each impact multiplier (FR-24, AC-H-05)."""
    mode = config.headline_execution
    base = SimulationConfig(aum=config.aum, costs=config.costs)
    rows: list[dict[str, object]] = []
    curves: list[pl.DataFrame] = []
    for name, scores in composites.items():

        def run(simulation: SimulationConfig, scores: pl.DataFrame = scores) -> BacktestResult:
            return run_book(scores, market, mode, simulation, config)

        target = 0.0
        lines: list[pl.DataFrame] = []
        for multiplier in config.impact_multipliers:
            result = capacity_curve(
                run, factors, replace(base, impact_scale=multiplier), config.capacity
            )
            target = result.zero_cost_alpha / 2.0
            rows.append(
                {
                    "composite": name,
                    "impact_multiplier": multiplier,
                    "execution": mode,
                    "max_adv_participation": config.capacity.max_participation,
                    "zero_cost_alpha_annual": result.zero_cost_alpha,
                    "capacity_usd": result.capacity,
                    "status": result.status,
                }
            )
            lines.append(
                result.curve.with_columns(
                    pl.lit(name).alias("composite"), pl.lit(multiplier).alias("impact_multiplier")
                )
            )
        curve = pl.concat(lines, how="vertical")
        curves.append(curve)
        stem = name.removeprefix(next(iter(composites))).lstrip("_")
        figure = f"capacity_curve_{stem}.png" if stem else "capacity_curve.png"
        capacity_lines(curve, target, sink.figures / figure, dev_only)
    table = pl.DataFrame(rows, infer_schema_length=None)
    write_table(table, sink.tables, "capacity")
    write_table(
        pl.concat(curves, how="vertical").select(
            "composite",
            "impact_multiplier",
            "aum",
            "net_alpha_annual",
            "net_mean_annual",
            "turnover",
        ),
        sink.tables,
        "capacity_curve",
    )
    return table


def attribution_task(
    series_dir: Path, factors: pl.DataFrame, config: BacktestConfig, sink: Sink
) -> pl.DataFrame:
    """FF5 + UMD alpha and loadings, gross and net, at the headline execution (AC-H-04)."""
    rows: list[dict[str, object]] = []
    for path in sorted(series_dir.glob(f"*__{config.headline_execution}.parquet")):
        series = pl.read_parquet(path)
        capped = pl.read_parquet(path.with_name(f"{path.stem}__capped.parquet"))
        for basis, frame, column in (
            ("gross", series, "ret_gross"),
            ("net", series, "ret"),
            ("net_capped", capped, "ret"),
        ):
            fitted = attribute(frame, factors, column=column)
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
