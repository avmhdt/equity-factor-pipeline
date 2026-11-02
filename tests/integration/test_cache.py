"""AC-W-12: a second rebuild is all cache hits; one signal's change stays downstream of it."""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from efp.cli import app

runner = CliRunner()
STARTUP_SECONDS = 10.0
"""Start-up and report time, which dwarf a tiny run. The 5% is measured on the headline run."""
CONFIG = """
run: {name: tiny, seed: 3, provider: synthetic, start: 2008-01-31, end: 2017-12-31}
synthetic: {n_securities: 120, years: 10}
signals:
  include: [gp_at, mom_12_1, str_1m]
  min_names: 20
OVERRIDE
combination:
  methods: [equal, ic_weighted]
  ridge: {min_train_months: 36}
backtest:
  capacity: {points: 3}
"""


def states(output: str) -> dict[str, str]:
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[1] in ("ran", "cached"):
            found[parts[0]] = parts[1]
    return found


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str], float]:
    root = tmp_path_factory.mktemp("cache")
    patch = pytest.MonkeyPatch()
    patch.chdir(root)
    patch.setenv("EFP_DATA_DIR", str(root / "data"))
    patch.setenv("EFP_LOG_JSON", "0")
    (root / "tiny.yaml").write_text(CONFIG.replace("OVERRIDE", ""), encoding="utf-8")
    started = time.perf_counter()
    result = runner.invoke(app, ["rebuild", "--config", "tiny.yaml"])
    seconds = time.perf_counter() - started
    assert result.exit_code == 0, result.output
    yield root, states(result.output), seconds
    patch.undo()


def test_first_rebuild_runs_every_stage(built: tuple[Path, dict[str, str], float]) -> None:
    _, first, _ = built
    assert set(first.values()) == {"ran"}
    assert {"panel_gp_at", "evaluate_momentum", "composites", "backtest", "capacity"} <= set(first)


def test_second_rebuild_is_all_hits_and_quick(built: tuple[Path, dict[str, str], float]) -> None:
    _, first, seconds = built
    started = time.perf_counter()
    result = runner.invoke(app, ["rebuild", "--config", "tiny.yaml"])
    assert result.exit_code == 0, result.output
    assert time.perf_counter() - started <= 0.05 * seconds + STARTUP_SECONDS
    assert set(states(result.output).values()) == {"cached"}
    assert states(result.output).keys() == first.keys()


def test_one_signal_change_stays_downstream(built: tuple[Path, dict[str, str], float]) -> None:
    root, _, _ = built
    override = "  overrides: {mom_12_1: {winsorize: {lower: 0.05, upper: 0.95}}}"
    (root / "tiny.yaml").write_text(CONFIG.replace("OVERRIDE", override), encoding="utf-8")
    result = runner.invoke(app, ["rebuild", "--config", "tiny.yaml"])
    (root / "tiny.yaml").write_text(CONFIG.replace("OVERRIDE", ""), encoding="utf-8")
    assert result.exit_code == 0, result.output
    found = states(result.output)
    assert found["panel_mom_12_1"] == "ran"
    assert found["evaluate_momentum"] == "ran"
    assert found["composites"] == "ran"
    assert found["backtest"] == "ran"
    for stage in ("synthetic_raw", "pit", "raw_signals", "panel_gp_at", "evaluate_quality"):
        assert found[stage] == "cached"


def test_tables_arrive_and_the_log_holds_composites(
    built: tuple[Path, dict[str, str], float],
) -> None:
    root, _, _ = built
    tables = root / "data" / "processed" / "tables" / "tiny"
    expected = ("ic_summary", "backtest_summary", "execution_timing", "cost_sensitivity")
    assert (tables / "spread_models.csv").is_file()
    assert (tables / "spread_model_capacity.csv").is_file()
    assert (tables / "spread_model_checks.csv").is_file()
    for name in (*expected, "capacity", "attribution"):
        assert (tables / f"{name}.csv").is_file(), name
    assert (tables / "numbers.json").is_file()
    assert (tables / "figures" / "composite_gross_net.png").is_file()
    assert (tables / "figures" / "ic_momentum.png").is_file()
    log = pl.read_csv(root / "data" / "processed" / "trials" / "tiny" / "trial_log.csv")
    assert log.filter(pl.col("strategy_family").eq("composite")).height > 0


def test_a_rebuild_does_not_log_a_trial_twice(
    built: tuple[Path, dict[str, str], float],
) -> None:
    root, _, _ = built
    log = pl.read_csv(root / "data" / "processed" / "trials" / "tiny" / "trial_log.csv")
    repeats = log.group_by("params_json", "sharpe_annual").len().filter(pl.col("len") > 1)
    assert repeats.is_empty()


def test_no_stage_touches_the_committed_results(
    built: tuple[Path, dict[str, str], float],
) -> None:
    root, _, _ = built
    assert not (root / "results").exists()
    assert not (root / "reports").exists()
