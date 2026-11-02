"""FR-29 skeleton: content-addressed cache and stage runner."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from efp.config import config_hash, load_config
from efp.pipeline.cache import StageCache, cache_key
from efp.pipeline.manifest import git_sha, read_manifest
from efp.pipeline.stages import RunContext, Stage, run_stages

CONTEXT = RunContext("abc", "synthetic", False, {"prices": "20200101T000000Z"})


def first(path: Path, inputs: Mapping[str, Path]) -> int:
    (path / "out.txt").write_text("one", encoding="utf-8")
    return 1


def second(path: Path, inputs: Mapping[str, Path]) -> int:
    text = (inputs["first"] / "out.txt").read_text(encoding="utf-8")
    (path / "out.txt").write_text(text + "two", encoding="utf-8")
    return 2


def stages(first_config: int = 1, second_config: int = 1) -> list[Stage]:
    return [
        Stage("first", first, first_config),
        Stage("second", second, second_config, ("first",)),
    ]


def test_cache_key_depends_on_each_ingredient() -> None:
    base = cache_key("code", {"a": 1}, ["x"], {"p": "s"})
    assert base == cache_key("code", {"a": 1}, ["x"], {"p": "s"})
    assert base != cache_key("other", {"a": 1}, ["x"], {"p": "s"})
    assert base != cache_key("code", {"a": 2}, ["x"], {"p": "s"})
    assert base != cache_key("code", {"a": 1}, ["y"], {"p": "s"})
    assert base != cache_key("code", {"a": 1}, ["x"], {"p": "t"})


def test_second_run_is_all_cache_hits(tmp_path: Path) -> None:
    cache = StageCache(tmp_path)
    assert not any(run.cached for run in run_stages(stages(), cache, CONTEXT))
    runs = run_stages(stages(), cache, CONTEXT)
    assert all(run.cached for run in runs)
    assert (runs[1].path / "out.txt").read_text(encoding="utf-8") == "onetwo"


def test_changing_a_stage_config_invalidates_it_and_downstream_only(tmp_path: Path) -> None:
    cache = StageCache(tmp_path)
    run_stages(stages(), cache, CONTEXT)
    runs = run_stages(stages(second_config=2), cache, CONTEXT)
    assert [run.cached for run in runs] == [True, False]
    runs = run_stages(stages(first_config=2), cache, CONTEXT)
    assert [run.cached for run in runs] == [False, False]


def test_force_and_from_stage(tmp_path: Path) -> None:
    cache = StageCache(tmp_path)
    run_stages(stages(), cache, CONTEXT)
    assert not any(run.cached for run in run_stages(stages(), cache, CONTEXT, force=True))
    runs = run_stages(stages(), cache, CONTEXT, from_stage="second")
    assert [run.cached for run in runs] == [True, False]
    with pytest.raises(ValueError, match="unknown stage"):
        run_stages(stages(), cache, CONTEXT, from_stage="nope")


def test_a_stage_cannot_read_a_later_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="to run first"):
        run_stages(
            [Stage("second", second, 1, ("first",)), Stage("first", first, 1)],
            StageCache(tmp_path),
            CONTEXT,
        )


def test_every_output_has_a_provenance_manifest(tmp_path: Path) -> None:
    runs = run_stages(stages(), StageCache(tmp_path), CONTEXT)
    manifest = read_manifest(runs[0].path / "manifest.json")
    assert manifest.config_hash == "abc"
    assert manifest.provider == "synthetic"
    assert manifest.raw_snapshots == {"prices": "20200101T000000Z"}
    assert manifest.survivorship_limited is False
    assert manifest.rows == 1


def test_prune_keeps_the_newest(tmp_path: Path) -> None:
    cache = StageCache(tmp_path)
    for value in (1, 2, 3):
        run_stages(stages(first_config=value), cache, CONTEXT)
    removed = cache.prune(keep_last=1)
    assert len(removed) == 4
    assert len(cache.entries()) == 2
    with pytest.raises(ValueError, match="keep_last"):
        cache.prune(keep_last=0)


def test_git_sha_outside_a_repository(tmp_path: Path) -> None:
    assert git_sha(tmp_path) == "unknown"


def test_committed_configs_validate() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("headline", "synthetic", "dev_free"):
        config = load_config(root / "configs" / f"{name}.yaml")
        assert config.run.name == name
        assert len(config_hash(config)) == 64


def test_borrow_schedule_matches_the_owner_decision() -> None:
    root = Path(__file__).resolve().parents[2]
    borrow = load_config(root / "configs" / "headline.yaml").backtest["costs"]["borrow"]
    assert borrow["tiers_pct_annual"] == [
        {"max_rank": 500, "rate": 0.25},
        {"max_rank": 1000, "rate": 1.0},
        {"rate": 5.0},
    ]
