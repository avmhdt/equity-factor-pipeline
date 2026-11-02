"""The ``efp`` command line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer

from efp.config import Experiment, config_hash, load_config
from efp.doctor import run_doctor
from efp.logs import configure_logging
from efp.pipeline.cache import StageCache
from efp.pipeline.stages import RunContext, build_stages, run_stages
from efp.settings import Settings, get_settings
from eqpit.snapshot import prune_snapshots

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


@data_app.command("pull")
def data_pull(
    provider: Annotated[str, typer.Option("--provider")],
    datasets: Annotated[str | None, typer.Option("--datasets")] = None,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Write raw snapshots for a provider."""
    _start(config, log_level)
    _pending("M2")


@data_app.command("pull-benchmarks")
def data_pull_benchmarks(
    sources: Annotated[str, typer.Option("--sources")] = "french,osap",
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Download external benchmark files."""
    _start(config, log_level)
    _pending("M3")


@data_app.command("build-pit")
def data_build_pit(
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    snapshot: Annotated[str | None, typer.Option("--snapshot")] = None,
    config: ConfigOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Build all point-in-time tables."""
    _start(config, log_level)
    _pending("M2")


@data_app.command("verify")
def data_verify(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Run the DQ checks and write the DQ report; exit 1 on hard failures."""
    _start(config, log_level)
    _pending("M2")


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
    _pending("M2")


@universe_app.command("build")
def universe_build(config: ConfigOption = None, log_level: LogLevelOption = None) -> None:
    """Build universe_monthly."""
    _start(config, log_level)
    _pending("M2")


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
    _start(config, log_level)
    _pending("M3 or M5")


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
    runs = run_stages(
        build_stages(experiment),
        StageCache(data_dir / "interim"),
        RunContext(
            config_hash=config_hash(experiment),
            provider=experiment.run.provider,
            survivorship_limited=experiment.run.provider == "edgar_free",
        ),
        force=force,
        from_stage=from_stage,
    )
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
