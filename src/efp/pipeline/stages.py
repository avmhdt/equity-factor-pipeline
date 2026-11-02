"""Stage runner: each stage runs through the cache in dependency order."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

from efp.config import Experiment, pit_policy
from efp.pipeline.cache import MANIFEST_NAME, StageCache, cache_key
from efp.pipeline.manifest import OutputManifest, git_sha, now_utc, write_manifest
from eqpit.pit.build import build_pit, read_pit_manifest
from eqpit.pit.verify import write_reports
from eqpit.providers.base import get_provider
from eqpit.providers.synthetic import write_market
from eqpit.synthetic import SyntheticConfig

log = structlog.get_logger(__name__)

StageFn = Callable[[Path, Mapping[str, Path]], int]
"""A stage writes into its output directory, reads its inputs, and returns a row count."""


@dataclass(frozen=True)
class Stage:
    """One node of the pipeline DAG."""

    name: str
    fn: StageFn
    config: object = None
    inputs: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageRun:
    """Outcome of running one stage."""

    stage: str
    key: str
    path: Path
    cached: bool
    seconds: float


@dataclass(frozen=True)
class RunContext:
    """What every stage's manifest records."""

    config_hash: str
    provider: str
    survivorship_limited: bool
    raw_snapshots: Mapping[str, str] = field(default_factory=dict)


def build_stages(experiment: Experiment, raw_root: Path) -> list[Stage]:
    """The stage list for an experiment, in dependency order.

    The synthetic run generates its own raw data inside the cache; other providers read the
    snapshots under ``raw_root``.
    """
    run = experiment.run
    policy = pit_policy(experiment)
    pit_config = {
        "pit": experiment.pit.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "universe": experiment.universe.model_dump(mode="json"),
    }
    stages: list[Stage] = []
    inputs: tuple[str, ...] = ()
    if run.provider == "synthetic":
        synthetic = SyntheticConfig(
            seed=run.seed,
            n_securities=int(experiment.synthetic.get("n_securities", 500)),
            years=int(experiment.synthetic.get("years", 20)),
            start_year=run.start.year,
        )

        def generate(path: Path, inputs: Mapping[str, Path]) -> int:
            manifests = write_market(synthetic, path / "raw")
            return sum(sum(item.row_counts.values()) for item in manifests)

        stages.append(Stage("synthetic_raw", generate, asdict(synthetic), modules=("eqpit",)))
        inputs = ("synthetic_raw",)

    def build(path: Path, inputs: Mapping[str, Path]) -> int:
        root = inputs["synthetic_raw"] / "raw" if "synthetic_raw" in inputs else raw_root
        build_pit(get_provider(run.provider, root=root, policy=policy), path / "pit", policy)
        return int(sum(read_pit_manifest(path / "pit")["row_counts"].values()))

    def verify(path: Path, inputs: Mapping[str, Path]) -> int:
        results = write_reports(inputs["pit"] / "pit", path / "tables")
        failed = [item for item in results if item.status == "fail"]
        if failed:
            raise ValueError(f"hard data-quality failures: {failed}")
        return len(results)

    stages.append(Stage("pit", build, pit_config, inputs, modules=("eqpit",)))
    stages.append(Stage("dq", verify, None, ("pit",), modules=("eqpit",)))
    return stages


def publish(runs: Sequence[StageRun], data_dir: Path, tables_dir: Path) -> None:
    """Copy the finished PIT tables and reports to their stable locations."""
    by_name = {run.stage: run for run in runs}
    target = data_dir / "processed" / "pit"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(by_name["pit"].path / "pit", target)
    shutil.copytree(by_name["dq"].path / "tables", tables_dir, dirs_exist_ok=True)


def _package_hash(name: str) -> str:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        raise ValueError(f"cannot locate package {name}")
    if spec.submodule_search_locations is None:
        return hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
    digest = hashlib.sha256()
    package = Path(spec.origin).parent
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def code_hash(stage: Stage) -> str:
    """Hash a stage's source and its packages, so editing either invalidates the stage."""
    digest = hashlib.sha256(inspect.getsource(stage.fn).encode())
    for module in stage.modules:
        digest.update(_package_hash(module).encode())
    return digest.hexdigest()


def run_stages(
    stages: Sequence[Stage],
    cache: StageCache,
    context: RunContext,
    force: bool = False,
    from_stage: str | None = None,
) -> list[StageRun]:
    """Run stages in order, reusing cached outputs; a stage may only read earlier ones.

    ``force`` re-runs everything; ``from_stage`` re-runs that stage and all after it.
    """
    names = [stage.name for stage in stages]
    if from_stage is not None and from_stage not in names:
        raise ValueError(f"unknown stage {from_stage!r}; stages are {names}")
    rerun_from = names.index(from_stage) if from_stage is not None else len(names)
    runs: dict[str, StageRun] = {}
    for index, stage in enumerate(stages):
        missing = [name for name in stage.inputs if name not in runs]
        if missing:
            raise ValueError(f"stage {stage.name} needs {missing} to run first")
        key = cache_key(
            code_hash(stage),
            stage.config,
            [runs[name].key for name in stage.inputs],
            context.raw_snapshots,
        )
        path = cache.path(stage.name, key)
        started = time.perf_counter()
        if cache.hit(stage.name, key) and not force and index < rerun_from:
            runs[stage.name] = StageRun(stage.name, key, path, True, time.perf_counter() - started)
            log.info("stage_cached", stage=stage.name, key=key[:12])
            continue
        log.info("stage_start", stage=stage.name, key=key[:12])
        path.mkdir(parents=True, exist_ok=True)
        rows = stage.fn(path, {name: runs[name].path for name in stage.inputs})
        seconds = time.perf_counter() - started
        write_manifest(
            path / MANIFEST_NAME,
            OutputManifest(
                stage=stage.name,
                cache_key=key,
                git_sha=git_sha(),
                config_hash=context.config_hash,
                raw_snapshots=dict(context.raw_snapshots),
                provider=context.provider,
                survivorship_limited=context.survivorship_limited,
                created_at=now_utc(),
                duration_seconds=seconds,
                rows=rows,
            ),
        )
        runs[stage.name] = StageRun(stage.name, key, path, False, seconds)
        log.info("stage_end", stage=stage.name, rows=rows, seconds=round(seconds, 3))
    return [runs[name] for name in names]
