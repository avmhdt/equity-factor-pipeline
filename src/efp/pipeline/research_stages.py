"""The research half of ``efp rebuild``: signals through to capacity, one cached stage at a time.

Raw signals run together, because a month loop shares reads across signals. Everything after
them runs per signal or per family, so changing one signal's parameter leaves the others cached.
"""

from __future__ import annotations

import functools
import hashlib
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import polars as pl

from efp.backtest.run import BacktestConfig, Market, load_market
from efp.config import (
    Experiment,
    backtest_config,
    capacity_composites,
    combine_config,
    combine_methods,
    evaluation_config,
    headline_composite,
    headline_method,
    panel_config,
)
from efp.evaluation.run import TABLE_KEYS, evaluate_panel
from efp.panel import ALL_ELIGIBLE, all_signals, transform_panel, write_panel
from efp.pipeline.stages import Stage, StageRun
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
from efp.signals.registry import compute_signals, spec_of
from eqpit.store import PitStore

EXPOSURES = ("beta", "size")
RESEARCH_MODULES = ("efp.research", "efp.panel", "efp.explog", "efp.config")


@functools.cache
def _open(root: Path) -> PitStore:
    return PitStore.open(root)


def _store(inputs: Mapping[str, Path]) -> PitStore:
    return _open(inputs["pit"] / "pit")


def _market_for(store: PitStore, ends: tuple[date, ...], config: BacktestConfig) -> Market:
    key = hashlib.sha256(repr((store.root, ends, asdict(config))).encode()).hexdigest()
    if key not in _MARKETS:
        _MARKETS.clear()
        _MARKETS[key] = load_market(store, list(ends), config)
    return _MARKETS[key]


_MARKETS: dict[str, Market] = {}


def benchmark_ids(external: Path) -> dict[str, str]:
    """A short id per benchmark set, so new benchmark files invalidate what reads them."""
    ids: dict[str, str] = {}
    for name in ("french", "osap"):
        digest = hashlib.sha256()
        root = external / name
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            if path.is_file():
                digest.update(f"{path.relative_to(root).as_posix()}:{path.stat().st_size}".encode())
        ids[name] = digest.hexdigest()[:16]
    return ids


@dataclass(frozen=True)
class _Plan:
    """What the stage factories share."""

    experiment: Experiment
    external: Path
    log_path: Path
    ids: tuple[str, ...]

    @property
    def provider(self) -> str:
        return self.experiment.run.provider

    def sink(self, path: Path) -> Sink:
        return Sink(path / "tables", path / "figures", self.log_path, once=True)

    def dates(self, store: PitStore) -> list[date]:
        run = self.experiment.run
        return rebalance_dates(store, run.start, run.end)


def _chosen_signals(experiment: Experiment) -> tuple[str, ...]:
    include = experiment.signals.get("include", "all")
    return all_signals() if include == "all" else tuple(sorted(include))


def _raw_stage(plan: _Plan, name: str, universe: int) -> Stage:
    needed = sorted({*plan.ids, *EXPOSURES})

    def run(path: Path, inputs: Mapping[str, Path]) -> int:
        store = _store(inputs)
        raw = compute_signals(store, needed, plan.dates(store), universe)
        write_panel(raw.sort("signal", "asof", "security_id"), path / "raw")
        return raw.height

    return Stage(
        name,
        run,
        {
            "signals": needed,
            "universe": universe,
            "run": plan.experiment.run.model_dump(mode="json"),
        },
        ("pit",),
        modules=("eqpit", "efp.signals"),
    )


def _read_raw(root: Path, names: Sequence[str]) -> pl.DataFrame:
    files = [root / f"signal={name}" / "part-0.parquet" for name in names]
    return pl.concat([pl.read_parquet(file) for file in files if file.is_file()], how="vertical")


def _panel_stage(plan: _Plan, signal: str) -> Stage:
    rules = panel_config(plan.experiment, signal)

    def run(path: Path, inputs: Mapping[str, Path]) -> int:
        store = _store(inputs)
        raw = _read_raw(inputs["raw_signals"] / "raw", sorted({signal, *EXPOSURES}))
        panel, dropped = transform_panel(raw, store, [signal], plan.dates(store), rules)
        panel.write_parquet(path / "panel.parquet", compression="zstd")
        dropped.write_parquet(path / "dropped.parquet", compression="zstd")
        return panel.height

    return Stage(
        f"panel_{signal}",
        run,
        {"signal": signal, "rules": asdict(rules)},
        ("pit", "raw_signals"),
        modules=("efp.signals", *RESEARCH_MODULES),
    )


def _panel_all_stage(plan: _Plan) -> Stage:
    rules = replace(panel_config(plan.experiment), universe=ALL_ELIGIBLE, neutral_mode="none")

    def run(path: Path, inputs: Mapping[str, Path]) -> int:
        store = _store(inputs)
        raw = _read_raw(inputs["raw_signals_all"] / "raw", sorted({*plan.ids, *EXPOSURES}))
        panel, _ = transform_panel(raw, store, list(plan.ids), plan.dates(store), rules)
        panel.write_parquet(path / "panel.parquet", compression="zstd")
        return panel.height

    return Stage(
        "panel_all",
        run,
        {"signals": list(plan.ids), "rules": asdict(rules)},
        ("pit", "raw_signals_all"),
        modules=("efp.signals", *RESEARCH_MODULES),
    )


def _panels(inputs: Mapping[str, Path], ids: Sequence[str]) -> pl.DataFrame:
    frames = [pl.read_parquet(inputs[f"panel_{name}"] / "panel.parquet") for name in ids]
    return pl.concat(frames, how="vertical").sort("signal", "asof", "security_id")


def _evaluate_stage(plan: _Plan, family: str, members: list[str]) -> Stage:
    rules = evaluation_config(plan.experiment)

    def run(path: Path, inputs: Mapping[str, Path]) -> int:
        sink = plan.sink(path)
        tables = evaluate_panel(
            _panels(inputs, members), _store(inputs), rules, plan.provider, sink.record
        )
        (path / "parts").mkdir()
        for name, table in tables.items():
            table.write_parquet(path / "parts" / f"{name}.parquet")
        return sum(table.height for table in tables.values())

    return Stage(
        f"evaluate_{family}",
        run,
        {"family": family, "members": members, "rules": asdict(rules)},
        ("pit", *[f"panel_{name}" for name in members]),
        modules=("efp.evaluation", *RESEARCH_MODULES),
    )


def _evaluation_tables_stage(plan: _Plan, families: Sequence[str]) -> Stage:
    def run(path: Path, inputs: Mapping[str, Path]) -> int:
        rows = 0
        for name, keys in TABLE_KEYS.items():
            parts = [
                pl.read_parquet(inputs[f"evaluate_{family}"] / "parts" / f"{name}.parquet")
                for family in families
            ]
            table = pl.concat(parts, how="diagonal_relaxed").sort(*keys)
            write_table(table, path / "tables", name)
            rows += table.height
        dropped = pl.concat(
            [pl.read_parquet(inputs[f"panel_{name}"] / "dropped.parquet") for name in plan.ids],
            how="vertical",
        ).sort("signal", "asof")
        write_table(dropped, path / "tables", "signal_coverage_drops")
        return rows

    needed = [*[f"evaluate_{family}" for family in families]]
    needed += [f"panel_{name}" for name in plan.ids]
    return Stage("evaluation_tables", run, None, tuple(needed), modules=RESEARCH_MODULES)


def _validation_stages(plan: _Plan) -> list[Stage]:
    def french(path: Path, inputs: Mapping[str, Path]) -> int:
        table = french_task(_store(inputs), plan.external, plan.sink(path), plan.provider)
        if not table.filter(pl.col("passed").eq(False)).is_empty():
            raise ValueError(f"French replication failed:\n{table}")
        return table.height

    def survivorship(path: Path, inputs: Mapping[str, Path]) -> int:
        return survivorship_task(_store(inputs), plan.sink(path), plan.provider).height

    def osap(path: Path, inputs: Mapping[str, Path]) -> int:
        panel = pl.read_parquet(inputs["panel_all"] / "panel.parquet")
        return osap_task(
            _store(inputs),
            panel,
            plan.external,
            path / "osap_series.parquet",
            plan.sink(path),
            plan.provider,
        ).height

    def postpub(path: Path, inputs: Mapping[str, Path]) -> int:
        return postpub_task(
            inputs["osap"] / "osap_series.parquet",
            plan.external,
            plan.sink(path),
            plan.provider not in LICENSED,
        ).height

    def value_gap(path: Path, inputs: Mapping[str, Path]) -> int:
        panel = pl.read_parquet(inputs["panel_bm"] / "panel.parquet")
        return value_gap_task(_store(inputs), panel, plan.external, plan.sink(path)).height

    panels = ("pit", "panel_bm")
    return [
        Stage("french", french, None, ("pit",), modules=("efp.evaluation", *RESEARCH_MODULES)),
        Stage(
            "survivorship",
            survivorship,
            None,
            ("pit",),
            modules=("efp.evaluation", *RESEARCH_MODULES),
        ),
        Stage(
            "osap",
            osap,
            None,
            ("pit", "panel_all"),
            modules=("efp.evaluation", *RESEARCH_MODULES),
        ),
        Stage("postpub", postpub, None, ("osap",), modules=("efp.evaluation", *RESEARCH_MODULES)),
        Stage("value_gap", value_gap, None, panels, modules=("efp.evaluation", *RESEARCH_MODULES)),
    ]


def _portfolio_stages(plan: _Plan) -> list[Stage]:
    experiment = plan.experiment
    methods = combine_methods(experiment)
    modes = tuple(experiment.signals.get("neutralize", ("none", "sector_size_beta")))
    _, family_mode = headline_composite(experiment)
    method = headline_method(experiment)
    names = capacity_composites(experiment)
    sizes = tuple(experiment.universe.variants)
    rules = combine_config(experiment)
    config = backtest_config(experiment)
    size = experiment.universe.size

    def composites(path: Path, inputs: Mapping[str, Path]) -> int:
        store = _store(inputs)
        built = combine_task(
            _panels(inputs, plan.ids),
            store.labels().forward_returns((1,)),
            store.labels().universe_history().select("asof", "security_id", "rank"),
            methods,
            modes,
            (method, family_mode),
            sizes,
            rules,
            path / "composites",
            plan.sink(path),
            plan.provider,
            size,
        )
        return len(built)

    def market(inputs: Mapping[str, Path]) -> Market:
        store = _store(inputs)
        return _market_for(store, tuple(plan.dates(store)), config)

    def backtest(path: Path, inputs: Mapping[str, Path]) -> int:
        return backtest_task(
            inputs["composites"] / "composites",
            market(inputs),
            config,
            names,
            plan.sink(path),
            path / "series",
            plan.provider,
        ).height

    def capacity(path: Path, inputs: Mapping[str, Path]) -> int:
        root = inputs["composites"] / "composites"
        scores = {
            name: pl.read_parquet(root / f"{name}.parquet")
            for name in names
            if (root / f"{name}.parquet").is_file()
        }
        factors = factor_table(_store(inputs), plan.external, plan.provider)
        return capacity_task(
            scores,
            market(inputs),
            factors,
            config,
            plan.sink(path),
            plan.provider not in (*LICENSED, "synthetic"),
        ).height

    def attribution(path: Path, inputs: Mapping[str, Path]) -> int:
        factors = factor_table(_store(inputs), plan.external, plan.provider)
        return attribution_task(
            inputs["backtest"] / "series", factors, config, plan.sink(path)
        ).height

    settings = {
        "backtest": asdict(config),
        "composites": names,
        "methods": list(methods),
        "modes": list(modes),
    }
    panels = tuple(f"panel_{name}" for name in plan.ids)
    trading = ("efp.backtest", "efp.attribution", "efp.combine", *RESEARCH_MODULES)
    return [
        Stage(
            "composites",
            composites,
            {
                "rules": asdict(rules),
                "methods": list(methods),
                "modes": list(modes),
                "sizes": list(sizes),
                "headline": method,
            },
            ("pit", *panels),
            modules=trading,
        ),
        Stage("backtest", backtest, settings, ("pit", "composites"), modules=trading),
        Stage("capacity", capacity, settings, ("pit", "composites"), modules=trading),
        Stage("attribution", attribution, settings, ("pit", "backtest"), modules=trading),
    ]


def research_stages(experiment: Experiment, external: Path, log_path: Path) -> list[Stage]:
    """Stages after ``dq``; none when the experiment has no ``signals`` section."""
    if not experiment.signals:
        return []
    plan = _Plan(experiment, external, log_path, _chosen_signals(experiment))
    licensed = plan.provider in LICENSED
    size = experiment.universe.size
    members: dict[str, list[str]] = {}
    for name in plan.ids:
        members.setdefault(spec_of(name).family, []).append(name)
    families = sorted(members)
    stages = [_raw_stage(plan, "raw_signals", size)]
    if licensed:
        stages.append(_raw_stage(plan, "raw_signals_all", ALL_ELIGIBLE))
    stages += [_panel_stage(plan, name) for name in plan.ids]
    if licensed:
        stages.append(_panel_all_stage(plan))
    stages += [_evaluate_stage(plan, family, members[family]) for family in families]
    stages.append(_evaluation_tables_stage(plan, families))
    if licensed:
        stages += _validation_stages(plan)
    stages += _portfolio_stages(plan)

    def testing(path: Path, inputs: Mapping[str, Path]) -> int:
        return multiple_testing_task(log_path, plan.sink(path)).height

    logged = tuple(
        stage.name
        for stage in stages
        if stage.name.startswith("evaluate_")
        or stage.name in ("french", "survivorship", "osap", "composites", "backtest")
    )
    stages.append(Stage("multiple_testing", testing, None, logged, modules=RESEARCH_MODULES))
    return stages


def publish_research(runs: Sequence[StageRun], tables_dir: Path, figures_dir: Path) -> None:
    """Copy each stage's tables and figures to their stable locations."""
    for run in runs:
        if run.stage in ("pit", "dq"):
            continue
        if (run.path / "tables").is_dir():
            shutil.copytree(run.path / "tables", tables_dir, dirs_exist_ok=True)
        if (run.path / "figures").is_dir():
            shutil.copytree(run.path / "figures", figures_dir, dirs_exist_ok=True)


def write_timings(runs: Sequence[StageRun], tables_dir: Path) -> None:
    """``pipeline_timings``, from the last rebuild that ran a stage (NFR-01)."""
    if all(run.cached for run in runs):
        return
    table = pl.DataFrame(
        {
            "stage": [run.stage for run in runs],
            "cached": [run.cached for run in runs],
            "seconds": [run.seconds for run in runs],
        }
    )
    write_table(table, tables_dir, "pipeline_timings")
