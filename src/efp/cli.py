"""The ``efp`` command line."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import polars as pl
import polars.selectors
import typer

from efp.config import Experiment, config_hash, load_config, load_xbrl_map, pit_policy
from efp.doctor import run_doctor
from efp.evaluation.french import compare, replicate
from efp.evaluation.survivorship import bias_table, delisted_names_by_year, series_by_mode
from efp.explog import TrialRecord, log_trial
from efp.logs import configure_logging
from efp.pipeline.cache import StageCache
from efp.pipeline.stages import RunContext, build_stages, publish, run_stages
from efp.settings import ProviderName, Settings, get_settings
from eqpit.benchmarks import load_french, pull_french
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
        if source != "french":
            typer.echo(f"{source}: arrives in M5", err=True)
            raise typer.Exit(code=2)
        manifest = pull_french(root / "french")
        typer.echo(f"french snapshot={manifest.snapshot_id}")


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


@signals_app.command("compute")
def signals_compute(
    only: Annotated[str | None, typer.Option("--only")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Compute the signal panel."""
    _start(config, log_level)
    _pending("M4")


@app.command()
def evaluate(
    signals: Annotated[str | None, typer.Option("--signals")] = None,
    neutralize: Annotated[str, typer.Option("--neutralize")] = "none",
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """IC, spreads, Fama-MacBeth, turnover and decay tables; logs trials."""
    _start(config, log_level)
    _pending("M4")


def _write_table(frame: pl.DataFrame, directory: Path, name: str) -> None:
    """Write an aggregate table as CSV and Markdown."""
    directory.mkdir(parents=True, exist_ok=True)
    frame.write_csv(directory / f"{name}.csv", line_terminator="\n", float_precision=6)
    header = "| " + " | ".join(frame.columns) + " |"
    rule = "|" + "---|" * frame.width
    rows = [
        "| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |"
        for row in frame.with_columns(pl.selectors.float().round(6)).iter_rows()
    ]
    text = "\n".join([header, rule, *rows]) + "\n"
    (directory / f"{name}.md").write_text(text, encoding="utf-8", newline="\n")


def _log_series(
    series: pl.DataFrame, name: str, provider: str, universe: str, log_path: Path
) -> None:
    """One contract row per factor series. ``ret`` is already a long-short excess return."""
    record = TrialRecord(
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
    log_trial(record, log_path, log_path.parent / "runs")


@app.command()
def validate(
    target: Annotated[
        Literal["french", "osap", "survivorship", "postpub", "value-gap", "jkp"],
        typer.Argument(),
    ],
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Validation tables."""
    settings, experiment = _start(config, log_level)
    if target not in ("french", "survivorship"):
        _pending("M5")
    data_dir = _data_dir(settings, experiment)
    root = data_dir / "processed" / "pit"
    provider = str(read_pit_manifest(root)["provider"])
    run_name = experiment.run.name if experiment is not None else provider
    tables = _tables_dir(data_dir, provider, run_name)
    store = PitStore.open(root)
    log_path = Path("results") / "trial_log.csv"
    if target == "french":
        ours = replicate(store)
        table = compare(ours, load_french(data_dir / "external" / "french"))
        _write_table(table, tables, "french_replication")
        if provider in ("sharadar", "wrds"):
            for (name,), part in ours.group_by(["factor"], maintain_order=True):
                _log_series(part.sort("month"), str(name), provider, "all_eligible", log_path)
        typer.echo(table)
        failed = table.filter(pl.col("passed").eq(False))
        if not failed.is_empty():
            raise typer.Exit(code=1)
        return
    series = series_by_mode(store)
    _write_table(bias_table(series), tables, "survivorship_bias")
    _write_table(delisted_names_by_year(store), tables, "universe_delisted_by_year")
    if provider in ("sharadar", "wrds"):
        for (name, mode), part in series.group_by(["series", "mode"], maintain_order=True):
            _log_series(part.sort("month"), str(name), provider, str(mode), log_path)
    typer.echo(bias_table(series))


@app.command()
def combine(
    method: Annotated[str | None, typer.Option("--method")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Composite panels; logs trials."""
    _start(config, log_level)
    _pending("M6")


@app.command()
def backtest(
    execution: Annotated[str | None, typer.Option("--execution")] = None,
    gross_only: Annotated[bool, typer.Option("--gross-only")] = False,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Backtest series; logs trials."""
    _start(config, log_level)
    _pending("M6")


@app.command()
def capacity(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Capacity curve and estimate."""
    _start(config, log_level)
    _pending("M6")


@app.command()
def attribute(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """FF5 + UMD attribution."""
    _start(config, log_level)
    _pending("M6")


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
    runs = run_stages(
        build_stages(experiment, raw_root),
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
    publish(runs, data_dir, _tables_dir(data_dir, provider, experiment.run.name))
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
    _pending("M4")


if __name__ == "__main__":
    app()
