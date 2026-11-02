"""Quantile spreads, turnover, decay and the evaluate command."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from efp.cli import app
from efp.evaluation.decay import decay_curve, lagged_ic
from efp.evaluation.quantiles import quantile_spreads
from efp.evaluation.run import EvaluationConfig, evaluate_panel
from efp.evaluation.turnover import decile_weights, rank_autocorrelation, turnover
from efp.explog import TrialRecord, export_log, log_trial, summarize_log
from efp.panel import PanelConfig, build_panel
from eqpit.store import PitStore

JANUARY = date(2020, 1, 31)
FEBRUARY = date(2020, 2, 28)
MARCH = date(2020, 3, 31)


def cross_section(day: date) -> tuple[pl.DataFrame, pl.DataFrame]:
    names = [f"S{index:02d}" for index in range(20)]
    signals = pl.DataFrame(
        {
            "signal": ["a"] * 20,
            "asof": [day] * 20,
            "security_id": names,
            "z": [float(index) for index in range(20)],
            "exchange": ["NYSE" if index % 2 == 0 else "NASDAQ" for index in range(20)],
            "mcap": [float(index + 1) for index in range(20)],
        }
    )
    fwd = pl.DataFrame(
        {
            "asof": [day] * 20,
            "security_id": names,
            "h": [1] * 20,
            "fwd_ret": [index / 100.0 for index in range(20)],
        },
        schema_overrides={"h": pl.Int8},
    )
    return signals, fwd


def test_top_minus_bottom_with_all_breakpoints() -> None:
    signals, fwd = cross_section(JANUARY)
    table = quantile_spreads(signals, fwd, q=4, weighting="ew", breakpoints="all")
    spread = table.filter(pl.col("quantile").eq(0))["ret"][0]
    assert spread == pytest.approx(0.175 - 0.025)
    assert table.height == 5


def test_value_weighting_uses_market_cap() -> None:
    signals, fwd = cross_section(JANUARY)
    table = quantile_spreads(signals, fwd, q=2, weighting="vw", breakpoints="all")
    top = table.filter(pl.col("quantile").eq(2))["ret"][0]
    caps = [float(index + 1) for index in range(10, 20)]
    returns = [index / 100.0 for index in range(10, 20)]
    assert top == pytest.approx(sum(c * r for c, r in zip(caps, returns, strict=True)) / sum(caps))


def test_nyse_breakpoints_ignore_other_exchanges() -> None:
    signals, fwd = cross_section(JANUARY)
    moved = signals.with_columns(
        pl.when(pl.col("exchange").eq("NASDAQ"))
        .then(pl.col("z") + 100.0)
        .otherwise(pl.col("z"))
        .alias("z")
    )
    every = quantile_spreads(moved, fwd, q=4, breakpoints="all")
    nyse = quantile_spreads(moved, fwd, q=4, breakpoints="nyse")
    assert not every.equals(nyse)


def test_too_few_breakpoint_names_gives_no_row() -> None:
    signals, fwd = cross_section(JANUARY)
    thin = signals.with_columns(pl.lit("NASDAQ").alias("exchange"))
    assert quantile_spreads(thin, fwd, q=4).is_empty()


def weights_at(day: date, held: dict[str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "signal": ["a"] * len(held),
            "asof": [day] * len(held),
            "security_id": list(held),
            "weight": list(held.values()),
        }
    )


def test_turnover_is_half_the_absolute_change() -> None:
    frame = pl.concat(
        [
            weights_at(JANUARY, {"A": 0.5, "B": 0.5}),
            weights_at(FEBRUARY, {"A": 0.5, "C": 0.5}),
        ]
    )
    result = turnover(frame)
    assert result["turnover"].to_list() == [0.5]


def test_turnover_drifts_weights_with_returns() -> None:
    frame = pl.concat(
        [
            weights_at(JANUARY, {"A": 0.5, "B": 0.5}),
            weights_at(FEBRUARY, {"A": 0.5, "B": 0.5}),
        ]
    )
    returns = pl.DataFrame(
        {"asof": [JANUARY, JANUARY], "security_id": ["A", "B"], "fwd_ret": [1.0, 0.0]}
    )
    assert turnover(frame, returns)["turnover"][0] == pytest.approx(0.5 * (1 / 6 + 1 / 6))


def test_unchanged_weights_with_no_returns_do_not_turn_over() -> None:
    frame = pl.concat([weights_at(JANUARY, {"A": 1.0}), weights_at(FEBRUARY, {"A": 1.0})])
    assert turnover(frame)["turnover"].to_list() == [0.0]


def test_decile_weights_balance_each_leg() -> None:
    signals, _ = cross_section(JANUARY)
    weights = decile_weights(signals, q=4)
    assert weights.filter(pl.col("weight") > 0)["weight"].sum() == pytest.approx(1.0)
    assert weights.filter(pl.col("weight") < 0)["weight"].sum() == pytest.approx(-1.0)


def test_a_signal_unchanged_between_dates_has_full_autocorrelation() -> None:
    first, _ = cross_section(JANUARY)
    second, _ = cross_section(FEBRUARY)
    result = rank_autocorrelation(pl.concat([first, second]))
    assert result["autocorrelation"][0] == pytest.approx(1.0)


def test_decay_curve_reads_each_horizon() -> None:
    signals, fwd = cross_section(JANUARY)
    second = fwd.with_columns(
        pl.lit(2, dtype=pl.Int8).alias("h"), (-pl.col("fwd_ret")).alias("fwd_ret")
    )
    curve = decay_curve(signals, pl.concat([fwd, second]), (1, 2)).sort("horizon")
    assert curve["mean_ic"].to_list() == pytest.approx([1.0, -1.0])


def test_lagged_ic_uses_the_earlier_signal() -> None:
    first, _ = cross_section(JANUARY)
    second, fwd = cross_section(FEBRUARY)
    reversed_first = first.with_columns((-pl.col("z")).alias("z"))
    table = lagged_ic(pl.concat([reversed_first, second]), fwd, (0, 1)).sort("lag")
    assert table["mean_ic"].to_list() == pytest.approx([1.0, -1.0])


@pytest.fixture(scope="module")
def small_panel(small_store: PitStore) -> pl.DataFrame:
    dates = small_store.month_ends(date(2013, 1, 1), date(2015, 12, 31))
    ids = ["size", "beta", "mom_6_1", "str_1m", "gp_at", "asset_growth"]
    panel, _ = build_panel(small_store, ids, dates, PanelConfig(min_names=50))
    return panel


def test_evaluation_tables_cover_both_modes_and_log_trials(
    small_store: PitStore, small_panel: pl.DataFrame, tmp_path: Path
) -> None:
    trials: list[TrialRecord] = []

    def logger(record: TrialRecord) -> str:
        trials.append(record)
        return log_trial(record, tmp_path / "trial_log.csv", tmp_path / "runs")

    config = EvaluationConfig(quantiles=5, breakpoints="all", universes=(3000,))
    tables = evaluate_panel(small_panel, small_store, config, "synthetic", logger)
    assert set(tables) == {"ic_summary", "quantile_spreads", "fama_macbeth", "turnover", "decay"}
    ic = tables["ic_summary"]
    assert set(ic["neutralize"].to_list()) == {"none", "sector_size_beta"}
    assert ic["signal"].n_unique() == 6
    assert {"mean_ic", "icir", "icir_annual", "t_nw", "hit_rate"} <= set(ic.columns)
    assert tables["quantile_spreads"].height == 6 * 2 * 2
    assert len(trials) == 6 * 2 * 2
    assert {"univariate", "multivariate_quality"}.isdisjoint(set()) is True
    assert "univariate" in tables["fama_macbeth"]["model"].to_list()
    assert set(tables["decay"]["curve"].to_list()) == {"horizon", "lag"}
    assert tables["turnover"]["monthly_turnover"].drop_nulls().min() >= 0
    summary = summarize_log(tmp_path / "trial_log.csv")
    assert summary["trials"].sum() == 24
    run_dirs = list((tmp_path / "runs").iterdir())
    assert (run_dirs[0] / "config.yaml").is_file()
    assert (run_dirs[0] / "manifest.json").is_file()
    copied = export_log(tmp_path / "trial_log.csv", tmp_path / "exported")
    assert copied == 24
    assert (tmp_path / "exported" / "trial_log.csv").is_file()


def test_cli_signals_then_evaluate_then_explog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, small_store: PitStore
) -> None:
    import shutil

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    shutil.copytree(small_store.root, data / "processed" / "pit")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(data))
    monkeypatch.setenv("EFP_LOG_JSON", "0")
    config = tmp_path / "tiny.yaml"
    config.write_text(
        "run: {name: tiny, seed: 1, provider: synthetic, start: 2014-01-31, end: 2014-12-31}\n"
        "signals: {min_names: 50}\n"
        "evaluation: {breakpoints: all, quantiles: 5, weighting: [ew]}\n"
        "universe: {size: 3000, variants: []}\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    computed = runner.invoke(
        app, ["signals", "compute", "--only", "size,mom_6_1,beta", "--config", str(config)]
    )
    assert computed.exit_code == 0, computed.output
    evaluated = runner.invoke(app, ["evaluate", "--config", str(config), "--neutralize", "none"])
    assert evaluated.exit_code == 0, evaluated.output
    tables = data / "processed" / "tables" / "tiny"
    assert (tables / "ic_summary.csv").is_file()
    assert (tables / "signal_coverage_drops.csv").is_file()
    log_path = data / "processed" / "trials" / "tiny" / "trial_log.csv"
    assert log_path.is_file()
    assert not (tmp_path / "results").exists()
    (tmp_path / "results").mkdir()
    shutil.copy(log_path, tmp_path / "results" / "trial_log.csv")
    shown = runner.invoke(app, ["explog", "show"])
    assert "trials" in shown.output
    missing = runner.invoke(app, ["explog", "export"])
    assert missing.exit_code == 2
    exported = runner.invoke(app, ["explog", "export", "--dest", str(tmp_path / "out")])
    assert exported.exit_code == 0
