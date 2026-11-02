"""The ``efp`` command line."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import polars as pl
import polars.selectors
import typer

from efp.backtest.run import load_market
from efp.config import (
    Experiment,
    backtest_config,
    capacity_composites,
    combine_config,
    combine_methods,
    config_hash,
    evaluation_config,
    headline_composite,
    headline_method,
    load_config,
    load_xbrl_map,
    panel_config,
    pit_policy,
    trial_log_path,
)
from efp.doctor import run_doctor
from efp.evaluation.run import evaluate_panel, merge_rows, signals_argument
from efp.explog import export_log, summarize_log
from efp.logs import configure_logging
from efp.panel import ALL_ELIGIBLE, all_signals, build_panel, read_panel, write_panel
from efp.pipeline.cache import StageCache
from efp.pipeline.research_stages import (
    benchmark_ids,
    publish_research,
    research_stages,
    write_timings,
)
from efp.pipeline.stages import RunContext, build_stages, publish, run_stages
from efp.reports.tables import write_table
from efp.research import (
    LICENSED,
    Sink,
    attribution_task,
    backtest_task,
    capacity_task,
    combine_task,
    factor_table,
    french_task,
    multiple_testing_task,
    osap_task,
    postpub_task,
    rebalance_dates,
    survivorship_task,
    value_gap_task,
)
from efp.settings import ProviderName, Settings, get_settings
from eqpit.benchmarks import pull_french, pull_osap
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, XbrlMap
from eqpit.pit.build import build_pit, read_pit_manifest, rebuild_universe
from eqpit.pit.policy import PitPolicy
from eqpit.pit.verify import write_reports
from eqpit.providers.base import get_provider
from eqpit.providers.synthetic import write_market
from eqpit.snapshot import prune_snapshots
from eqpit.store import PitStore
from eqpit.synthetic import SyntheticConfig

app = typer.Typer(no_args_is_help=True, add_completion=False)
data_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Data ingest and PIT build")
synth_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Synthetic market")
universe_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Monthly universe")
signals_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Signal library")
app.add_typer(data_app, name="data")
app.add_typer(synth_app, name="synth")
app.add_typer(universe_app, name="universe")
app.add_typer(signals_app, name="signals")

ConfigOption = Annotated[
    Path | None, typer.Option("--config", help="Experiment YAML", exists=True, dir_okay=False)
]
LogLevelOption = Annotated[str | None, typer.Option("--log-level", help="DEBUG, INFO or WARNING")]


def _start(config: Path | None, log_level: str | None) -> tuple[Settings, Experiment | None]:
    """Load settings and the optional experiment, then configure logging."""
    settings = get_settings()
    experiment = load_config(config) if config is not None else None
    json_dir = settings.data_dir / "logs" if settings.efp_log_json else None
    configure_logging((log_level or settings.efp_log_level).upper(), json_dir)
    return settings, experiment


def _data_dir(settings: Settings, experiment: Experiment | None) -> Path:
    """Environment overrides win over ``data.root`` in the experiment file."""
    if settings.efp_data_dir or settings.data_root or experiment is None:
        return settings.data_dir
    return Path(experiment.data.root)


def _pending(milestone: str) -> NoReturn:
    typer.echo(f"not implemented yet: arrives in {milestone}", err=True)
    raise typer.Exit(code=2)


@app.command()
def doctor(
    network: Annotated[bool, typer.Option("--network", help="Run live access checks")] = False,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Check environment variables, provider access, disk space and tool versions."""
    settings, _ = _start(config, log_level)
    checks = run_doctor(settings, network=network)
    for check in checks:
        typer.echo(f"{check.status.upper():<5} {check.name:<20} {check.detail}")
    if any(check.status == "fail" for check in checks):
        raise typer.Exit(code=1)


def _xbrl_map() -> XbrlMap:
    path = Path("configs/xbrl_map.yaml")
    return load_xbrl_map(path) if path.is_file() else DEFAULT_XBRL_MAP


def _max_companies(experiment: Experiment | None) -> int | None:
    if experiment is None:
        return None
    limit = experiment.edgar_free.get("max_companies")
    return int(limit) if limit is not None else None


def _tables_dir(data_dir: Path, provider: str, run_name: str) -> Path:
    """Only licensed runs write to ``results/tables``, the directory the repo commits."""
    if provider in ("sharadar", "wrds"):
        return Path("results") / "tables"
    return data_dir / "processed" / "tables" / run_name


@data_app.command("pull")
def data_pull(
    provider: Annotated[ProviderName, typer.Option("--provider")],
    datasets: Annotated[str | None, typer.Option("--datasets")] = None,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Write raw snapshots for a provider."""
    settings, experiment = _start(config, log_level)
    raw_root = _data_dir(settings, experiment) / "raw" / provider
    source = get_provider(provider, root=raw_root, max_companies=_max_companies(experiment))
    names = [item.strip() for item in datasets.split(",")] if datasets else []
    first = date.fromisoformat(start) if start else date(2001, 1, 1)
    last = date.fromisoformat(end) if end else date(2020, 12, 31)
    for manifest in source.pull(names, first, last, raw_root):
        typer.echo(f"{manifest.dataset:<16} snapshot={manifest.snapshot_id}")


@data_app.command("pull-benchmarks")
def data_pull_benchmarks(
    sources: Annotated[str, typer.Option("--sources")] = "french",
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Download external benchmark files."""
    settings, experiment = _start(config, log_level)
    root = _data_dir(settings, experiment) / "external"
    for source in (item.strip() for item in sources.split(",")):
        if source == "french":
            manifest = pull_french(root / "french")
        elif source == "osap":
            manifest = pull_osap(root / "osap")
        else:
            typer.echo(f"{source}: unknown source", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"{source} snapshot={manifest.snapshot_id}")


@data_app.command("build-pit")
def data_build_pit(
    provider: Annotated[ProviderName | None, typer.Option("--provider")] = None,
    snapshot: Annotated[str | None, typer.Option("--snapshot")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Build all point-in-time tables."""
    settings, experiment = _start(config, log_level)
    name = provider or (experiment.run.provider if experiment else settings.efp_provider)
    data_dir = _data_dir(settings, experiment)
    policy = pit_policy(experiment) if experiment is not None else None
    source = get_provider(name, root=data_dir / "raw" / name, policy=policy, xbrl_map=_xbrl_map())
    pinned = snapshot or (experiment.data.snapshot if experiment is not None else "latest")
    if pinned != "latest":
        source.pin(pinned)
    build_pit(source, data_dir / "processed" / "pit", policy)
    typer.echo(f"built {data_dir / 'processed' / 'pit'} from {name}")


@data_app.command("verify")
def data_verify(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Run the DQ checks and write the DQ report; exit 1 on hard failures."""
    settings, experiment = _start(config, log_level)
    data_dir = _data_dir(settings, experiment)
    root = data_dir / "processed" / "pit"
    manifest = read_pit_manifest(root)
    if manifest["label"]:
        typer.echo(manifest["label"])
    provider = str(manifest["provider"])
    name = experiment.run.name if experiment is not None else provider
    results = write_reports(root, _tables_dir(data_dir, provider, name))
    for result in results:
        typer.echo(f"{result.status.upper():<5} {result.check:<6} {result.detail}")
    if any(result.status == "fail" for result in results):
        raise typer.Exit(code=1)


@synth_app.command("generate")
def synth_generate(
    out: Annotated[Path, typer.Option("--out")],
    seed: Annotated[int, typer.Option("--seed")],
    n_securities: Annotated[int, typer.Option("--n-securities")] = 500,
    years: Annotated[int, typer.Option("--years")] = 20,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Generate the known-truth synthetic market in raw provider format."""
    _start(config, log_level)
    manifests = write_market(
        SyntheticConfig(seed=seed, n_securities=n_securities, years=years), out
    )
    for manifest in manifests:
        typer.echo(f"{manifest.dataset:<12} {sum(manifest.row_counts.values()):>10,} rows")


@universe_app.command("build")
def universe_build(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Rebuild universe_monthly from the stored tables."""
    settings, experiment = _start(config, log_level)
    root = _data_dir(settings, experiment) / "processed" / "pit"
    policy = pit_policy(experiment) if experiment is not None else PitPolicy()
    rows = rebuild_universe(root, policy)
    typer.echo(f"universe_monthly: {rows:,} rows")


def _dates(store: PitStore, experiment: Experiment | None) -> list[date]:
    """Rebalance dates: the experiment's window, else the whole sample."""
    if experiment is None:
        return rebalance_dates(store, None, None)
    return rebalance_dates(store, experiment.run.start, experiment.run.end)


def _sink(data_dir: Path, provider: str, experiment: Experiment | None) -> Sink:
    """Licensed runs publish figures to ``reports/``; the free path logs no trials."""
    name = experiment.run.name if experiment is not None else provider
    tables = _tables_dir(data_dir, provider, name)
    figures = Path("reports") / "figures" if provider in LICENSED else tables / "figures"
    log_path = None if provider == "edgar_free" else trial_log_path(experiment, provider, data_dir)
    return Sink(tables, figures, log_path)


def _experiment(experiment: Experiment | None) -> Experiment:
    if experiment is None:
        typer.echo("--config is required", err=True)
        raise typer.Exit(code=2)
    return experiment


@signals_app.command("compute")
def signals_compute(
    only: Annotated[str | None, typer.Option("--only")] = None,
    all_eligible: Annotated[
        bool, typer.Option("--all-eligible", help="Every eligible name, for OSAP replication")
    ] = False,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Compute the signal panel."""
    settings, experiment = _start(config, log_level)
    data_dir = _data_dir(settings, experiment)
    store = PitStore.open(data_dir / "processed" / "pit")
    ids = signals_argument(only) or list(all_signals())
    rules = panel_config(experiment)
    if all_eligible:
        rules = replace(rules, universe=ALL_ELIGIBLE, neutral_mode="none")
    panel, dropped = build_panel(store, ids, _dates(store, experiment), rules)
    write_panel(panel, data_dir / "processed" / ("signals_all" if all_eligible else "signals"))
    provider = str(read_pit_manifest(store.root)["provider"])
    name = experiment.run.name if experiment is not None else provider
    if not all_eligible:
        write_table(dropped, _tables_dir(data_dir, provider, name), "signal_coverage_drops")
    typer.echo(f"signals: {panel.height:,} rows, {dropped.height} signal-dates dropped")


@app.command()
def evaluate(
    signals: Annotated[str | None, typer.Option("--signals")] = None,
    neutralize: Annotated[str, typer.Option("--neutralize")] = "both",
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """IC, spreads, Fama-MacBeth, turnover and decay tables; logs trials."""
    settings, experiment = _start(config, log_level)
    data_dir = _data_dir(settings, experiment)
    store = PitStore.open(data_dir / "processed" / "pit")
    panel = read_panel(data_dir / "processed" / "signals", signals_argument(signals))
    provider = str(read_pit_manifest(store.root)["provider"])
    name = experiment.run.name if experiment is not None else provider
    rules = evaluation_config(experiment)
    if neutralize != "both":
        rules = replace(rules, neutralize=(neutralize,))
    sink = _sink(data_dir, provider, experiment)
    tables = evaluate_panel(panel, store, rules, provider, sink.record)
    directory = _tables_dir(data_dir, provider, name)
    chosen = signals_argument(signals)
    for table_name, table in tables.items():
        existing = directory / f"{table_name}.csv"
        merged = table
        if chosen is not None and existing.is_file():
            merged = merge_rows(pl.read_csv(existing), table, chosen)
        write_table(merged, directory, table_name)
    typer.echo(f"wrote {len(tables)} tables to {directory}")


@app.command()
def validate(
    target: Annotated[
        Literal[
            "french", "osap", "survivorship", "postpub", "value-gap", "multiple-testing", "jkp"
        ],
        typer.Argument(),
    ],
    signals: Annotated[str | None, typer.Option("--signals", help="OSAP only: these")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Validation tables."""
    settings, experiment = _start(config, log_level)
    if target == "jkp":
        typer.echo("jkp needs D-10 to be yes; the default is no", err=True)
        raise typer.Exit(code=2)
    data_dir = _data_dir(settings, experiment)
    root = data_dir / "processed" / "pit"
    provider = str(read_pit_manifest(root)["provider"])
    sink = _sink(data_dir, provider, experiment)
    store = PitStore.open(root)
    external = data_dir / "external"
    processed = data_dir / "processed"
    series = processed / "osap_series.parquet"
    if target == "french":
        table = french_task(store, external, sink, provider)
        typer.echo(table)
        if not table.filter(pl.col("passed").eq(False)).is_empty():
            raise typer.Exit(code=1)
    elif target == "survivorship":
        typer.echo(survivorship_task(store, sink, provider))
    elif target == "osap":
        chosen = signals_argument(signals)
        panel = read_panel(processed / "signals_all", chosen)
        replicated = osap_task(store, panel, external, series, sink, provider, chosen)
        typer.echo(f"replicated {replicated['passed'].sum()} of {replicated.height} at 0.70")
    elif target == "postpub":
        typer.echo(postpub_task(series, external, sink, provider == "edgar_free"))
    elif target == "multiple-testing":
        hurdle = multiple_testing_task(Path("results") / "trial_log.csv", sink)
        typer.echo(
            f"{hurdle.height} trials: {hurdle['t_over_2'].sum()} over t=2, "
            f"{hurdle['t_over_3'].sum()} over t=3, {hurdle['bhy_5pct'].sum()} under 5% BHY"
        )
    else:
        typer.echo(value_gap_task(store, read_panel(processed / "signals", ["bm"]), external, sink))


def _open_run(
    settings: Settings, experiment: Experiment | None
) -> tuple[Experiment, Path, PitStore, Sink]:
    chosen = _experiment(experiment)
    data_dir = _data_dir(settings, chosen)
    store = PitStore.open(data_dir / "processed" / "pit")
    provider = str(read_pit_manifest(store.root)["provider"])
    return chosen, data_dir, store, _sink(data_dir, provider, chosen)


@app.command()
def combine(
    method: Annotated[
        Literal["equal", "ic_weighted", "ridge"] | None, typer.Option("--method")
    ] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Composite panels; logs trials."""
    settings, loaded = _start(config, log_level)
    experiment, data_dir, store, sink = _open_run(settings, loaded)
    _, family_mode = headline_composite(experiment)
    built = combine_task(
        read_panel(data_dir / "processed" / "signals"),
        store.labels().forward_returns((1,)),
        store.labels().universe_history().select("asof", "security_id", "rank"),
        (method,) if method is not None else combine_methods(experiment),
        tuple(experiment.signals.get("neutralize", ("none", "sector_size_beta"))),
        (headline_method(experiment), family_mode),
        tuple(experiment.universe.variants),
        combine_config(experiment),
        data_dir / "processed" / "composites",
        sink,
        experiment.run.provider,
        experiment.universe.size,
    )
    typer.echo(f"built {len(built)} composites")


@app.command()
def backtest(
    execution: Annotated[
        Literal["close", "next_open", "next_close", "next_vwap_proxy"] | None,
        typer.Option("--execution"),
    ] = None,
    gross_only: Annotated[bool, typer.Option("--gross-only")] = False,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Backtest series; logs trials."""
    settings, loaded = _start(config, log_level)
    experiment, data_dir, store, sink = _open_run(settings, loaded)
    rules = backtest_config(experiment)
    if execution is not None:
        rules = replace(rules, executions=(execution,))
    market = load_market(store, _dates(store, experiment), rules)
    summary = backtest_task(
        data_dir / "processed" / "composites",
        market,
        rules,
        capacity_composites(experiment),
        sink,
        data_dir / "processed" / "backtest",
        experiment.run.provider,
        gross_only,
    )
    typer.echo(summary)


@app.command()
def capacity(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Capacity curve and estimate."""
    settings, loaded = _start(config, log_level)
    experiment, data_dir, store, sink = _open_run(settings, loaded)
    rules = backtest_config(experiment)
    rules = replace(rules, executions=(rules.headline_execution,))
    provider = experiment.run.provider
    root = data_dir / "processed" / "composites"
    scores = {
        name: pl.read_parquet(root / f"{name}.parquet")
        for name in capacity_composites(experiment)
        if (root / f"{name}.parquet").is_file()
    }
    typer.echo(
        capacity_task(
            scores,
            load_market(store, _dates(store, experiment), rules),
            factor_table(store, data_dir / "external", provider),
            rules,
            sink,
            provider == "edgar_free",
        )
    )


@app.command()
def attribute(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """FF5 + UMD attribution."""
    settings, loaded = _start(config, log_level)
    experiment, data_dir, store, sink = _open_run(settings, loaded)
    factors = factor_table(store, data_dir / "external", experiment.run.provider)
    typer.echo(
        attribution_task(
            data_dir / "processed" / "backtest", factors, backtest_config(experiment), sink
        )
    )


@app.command()
def report(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Tables, figures, memos and results/readme_numbers.json."""
    _start(config, log_level)
    _pending("M7")


@app.command()
def rebuild(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)],
    force: Annotated[bool, typer.Option("--force")] = False,
    from_stage: Annotated[str | None, typer.Option("--from-stage")] = None,
    log_level: LogLevelOption = None,
) -> None:
    """Run every stage in order through the cache."""
    settings, experiment = _start(config, log_level)
    assert experiment is not None
    data_dir = _data_dir(settings, experiment)
    provider = experiment.run.provider
    raw_root = data_dir / "raw" / provider
    snapshots = (
        {} if provider == "synthetic" else get_provider(provider, root=raw_root).snapshot_ids()
    )
    external = data_dir / "external"
    if provider in LICENSED:
        snapshots = {**snapshots, **benchmark_ids(external)}
    log_path = trial_log_path(experiment, provider, data_dir)
    runs = run_stages(
        [*build_stages(experiment, raw_root), *research_stages(experiment, external, log_path)],
        StageCache(data_dir / "interim"),
        RunContext(
            config_hash=config_hash(experiment),
            provider=provider,
            survivorship_limited=provider == "edgar_free",
            raw_snapshots=snapshots,
        ),
        force=force,
        from_stage=from_stage,
    )
    sink = _sink(data_dir, provider, experiment)
    publish(runs, data_dir, sink.tables)
    publish_research(runs, sink.tables, sink.figures)
    write_timings(runs, sink.tables)
    for run in runs:
        state = "cached" if run.cached else "ran"
        typer.echo(f"{run.stage:<20} {state:<7} {run.seconds:8.3f}s {run.key[:12]}")


@app.command()
def cache(
    action: Annotated[Literal["ls", "prune"], typer.Argument()],
    keep_last: Annotated[int, typer.Option("--keep-last")] = 3,
    raw: Annotated[bool, typer.Option("--raw")] = False,
    keep: Annotated[int, typer.Option("--keep")] = 2,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """List or prune cached outputs and, with ``--raw``, old raw snapshots."""
    settings, experiment = _start(config, log_level)
    data_dir = _data_dir(settings, experiment)
    stage_cache = StageCache(data_dir / "interim")
    if action == "ls":
        for stage, key in stage_cache.entries():
            typer.echo(f"{stage:<20} {key}")
        return
    for path in stage_cache.prune(keep_last):
        typer.echo(f"removed {path}")
    raw_root = data_dir / "raw"
    if raw and raw_root.is_dir():
        for path in prune_snapshots(raw_root, keep):
            typer.echo(f"removed {path}")


@app.command()
def explog(
    action: Annotated[Literal["show", "export"], typer.Argument()],
    dest: Annotated[Path | None, typer.Option("--dest")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Inspect or export the trial log."""
    _start(config, log_level)
    log_path = Path("results") / "trial_log.csv"
    if action == "show":
        summary = summarize_log(log_path)
        for family, rows, failed in summary.iter_rows():
            typer.echo(f"{family:<14} {rows:>6} trials {failed:>4} failed")
        return
    if dest is None:
        typer.echo("--dest is required for export", err=True)
        raise typer.Exit(code=2)
    copied = export_log(log_path, dest)
    typer.echo(f"exported {copied} return files to {dest}")


if __name__ == "__main__":
    app()
