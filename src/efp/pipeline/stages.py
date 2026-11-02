"""Stage runner: each stage runs through the cache in dependency order."""

from __future__ import annotations

import hashlib
import inspect
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from efp.config import Experiment
from efp.pipeline.cache import MANIFEST_NAME, StageCache, cache_key
from efp.pipeline.manifest import OutputManifest, git_sha, now_utc, write_manifest

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


def _write_resolved_config(path: Path, inputs: Mapping[str, Path]) -> int:
    """Placeholder first stage until the PIT stages arrive in M2."""
    (path / "started.txt").write_text("rebuild skeleton\n", encoding="utf-8", newline="\n")
    return 1


def build_stages(experiment: Experiment) -> list[Stage]:
    """The stage list for an experiment, in dependency order."""
    return [Stage("resolve_config", _write_resolved_config, experiment.model_dump(mode="json"))]


def code_hash(fn: StageFn) -> str:
    """Hash a stage function's source, so editing a stage invalidates it."""
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()


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
            code_hash(stage.fn),
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
