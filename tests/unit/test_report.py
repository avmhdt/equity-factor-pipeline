"""FR-31: numbers come from tables, and a run skips what its tables don't support."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from efp.reports import figures
from efp.reports.numbers import FAMILIES, FIXED, build_numbers, write_numbers
from efp.reports.report import ReportPaths, write_report
from efp.reports.tables import write_table

HEADLINE = "equal_sector_size_beta"
MONTHS = pl.date_range(date(2010, 1, 31), date(2012, 12, 31), "1mo", eager=True).to_list()


@pytest.fixture
def tables(tmp_path: Path) -> Path:
    root = tmp_path / "tables"
    write_table(
        pl.DataFrame({"factor": ["umd"], "correlation": [0.9963], "passed": [True]}),
        root,
        "french_replication",
    )
    write_table(
        pl.DataFrame(
            {
                "composite": [HEADLINE],
                "execution": ["next_vwap_proxy"],
                "months": [100],
                "gross_mean_annual": [0.158],
                "net_mean_annual": [-0.311],
                "capped_net_mean_annual": [-0.098],
                "gross_sharpe": [1.066],
                "net_sharpe": [-1.601],
                "capped_net_sharpe": [-0.677],
                "turnover_monthly": [0.78],
                "spread_cost_annual": [0.11],
                "impact_cost_annual": [0.32],
                "borrow_cost_annual": [0.04],
            }
        ),
        root,
        "backtest_summary",
    )
    return root


def test_numbers_are_formatted_as_printed(tables: Path) -> None:
    numbers = build_numbers(tables, tables / "no_log.csv", HEADLINE, "next_vwap_proxy")
    assert numbers["french.umd"] == "0.996"
    assert numbers["headline.gross_sharpe"] == "1.07"
    assert numbers["headline.net_mean"] == "-31.1%"
    assert numbers["headline.turnover"] == "39%"
    assert numbers["headline.total_cost"] == "47.0%"
    assert numbers["timing.next_vwap_proxy.net"] == "-1.60"


def test_fixed_values_are_always_present(tmp_path: Path) -> None:
    numbers = build_numbers(tmp_path, tmp_path / "no_log.csv", HEADLINE, "close")
    assert FIXED.items() <= numbers.items()


def test_trial_counts_read_the_log(tables: Path, tmp_path: Path) -> None:
    log = tmp_path / "trial_log.csv"
    log.write_text(
        "run_id,git_sha,strategy_family,sharpe_annual,x_note\n"
        "a,1,value,0.1,\nb,2-dirty,value,0.2,\nc,3,composite,0.3,\n",
        encoding="utf-8",
    )
    numbers = build_numbers(tables, log, HEADLINE, "close")
    assert numbers["trials.total"] == "3"
    assert numbers["trials.value"] == "2"
    assert numbers["trials.dirty"] == "1"


def test_writing_numbers_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "numbers.json"
    write_numbers({"b": "2", "a": "1"}, path)
    assert path.read_text(encoding="utf-8") == '{\n  "a": "1",\n  "b": "2"\n}\n'


def test_a_report_without_templates_writes_numbers_only(tables: Path, tmp_path: Path) -> None:
    paths = ReportPaths(tables, tmp_path / "none", tmp_path / "figures", tmp_path / "n.json")
    numbers = write_report(paths, tmp_path / "no_log.csv", HEADLINE, "next_vwap_proxy")
    assert (tmp_path / "n.json").is_file()
    assert "headline.gross_sharpe" in numbers
    assert not (tmp_path / "memos").exists()


def test_family_figures_draw(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    ic = pl.DataFrame(
        {
            "signal": ["a"] * len(MONTHS) + ["b"] * len(MONTHS),
            "asof": MONTHS * 2,
            "ic": rng.normal(0.0, 0.05, len(MONTHS) * 2),
        }
    )
    figures.family_ic(ic, "low_risk", tmp_path / "ic.png")
    decay = pl.DataFrame(
        {
            "signal": ["a", "a"],
            "curve": ["horizon", "horizon"],
            "neutralize": ["none", "none"],
            "step": [1, 2],
            "mean_ic": [0.02, 0.01],
        }
    )
    figures.family_decay(decay, "value", tmp_path / "decay.png")
    assert (tmp_path / "ic.png").stat().st_size > 1000
    assert (tmp_path / "decay.png").stat().st_size > 1000


def test_book_figures_draw(tmp_path: Path) -> None:
    timing = pl.DataFrame(
        {
            "composite": [HEADLINE, f"family_{FAMILIES[0]}_sector_size_beta"] * 4,
            "execution": [
                mode
                for mode in ("close", "next_open", "next_close", "next_vwap_proxy")
                for _ in range(2)
            ],
            "gross_sharpe": [1.0] * 8,
            "net_sharpe": [-1.0] * 8,
        }
    )
    figures.execution_bars(timing, tmp_path / "timing.png")
    series = pl.DataFrame(
        {"month": MONTHS, "ret": [0.01] * len(MONTHS), "ret_gross": [0.02] * len(MONTHS)}
    )
    figures.gross_and_net(series, series, tmp_path / "gross.png")
    summary = pl.DataFrame(
        {
            "signal": ["a", "a", "b"],
            "universe": [3000, 3000, 3000],
            "neutralize": ["none", "sector_size_beta", "none"],
            "mean_ic": [0.02, 0.01, 0.03],
        }
    )
    figures.neutralization_ics(summary, tmp_path / "neutral.png")
    joined = pl.DataFrame(
        {
            "month": MONTHS * 2,
            "factor": ["hml"] * len(MONTHS) + ["umd"] * len(MONTHS),
            "ret": np.linspace(-0.05, 0.05, len(MONTHS) * 2),
            "theirs": np.linspace(-0.04, 0.06, len(MONTHS) * 2),
        }
    )
    figures.french_scatter(joined, tmp_path / "scatter.png")
    figures.french_cumulative(joined, tmp_path / "cumulative.png")
    assert len(list(tmp_path.glob("*.png"))) == 5


def test_spread_figure_draws(tmp_path: Path) -> None:
    models = ("corwin_schultz", "abdi_ranaldo", "size_bucket")
    checks = pl.DataFrame(
        {
            "model": [model for model in models for _ in range(2)],
            "band": ["<$100M", "top 500, late 2015"] * 3,
            "benchmark_bps": [80.0, 1.5] * 3,
            "estimate_bps": [50.0, 20.0, 60.0, 25.0, 80.0, 1.5],
        }
    )
    table = pl.DataFrame(
        {
            "spread_model": [model for model in models for _ in range(2)],
            "period": ["full", "2010_on"] * 3,
            "net_sharpe": [-1.0] * 6,
            "capped_net_sharpe": [-0.5] * 6,
        }
    )
    figures.spread_model_bars(checks, table, tmp_path / "spreads.png")
    assert (tmp_path / "spreads.png").stat().st_size > 1000


def test_spread_numbers_name_each_model(tmp_path: Path) -> None:
    write_table(
        pl.DataFrame(
            {
                "composite": [HEADLINE],
                "spread_model": ["size_bucket"],
                "period": ["2010_on"],
                "net_sharpe": [-1.18],
                "capped_net_sharpe": [-0.19],
                "net_mean_annual": [-0.225],
                "capped_net_mean_annual": [-0.027],
                "net_alpha_annual": [-0.245],
                "net_alpha_t": [-5.4],
                "capped_alpha_annual": [-0.036],
                "capped_alpha_t": [-1.9],
                "spread_cost_annual": [0.025],
                "impact_cost_annual": [0.32],
            }
        ),
        tmp_path,
        "spread_models",
    )
    write_table(
        pl.DataFrame(
            {
                "composite": [f"{HEADLINE}_top1000", HEADLINE],
                "spread_model": ["size_bucket", "abdi_ranaldo"],
                "impact_multiplier": [1.0, 2.0],
                "capacity_usd": [1.99e7, None],
                "status": ["estimated", "below grid min"],
            }
        ),
        tmp_path,
        "spread_model_capacity",
    )
    numbers = build_numbers(tmp_path, tmp_path / "no_log.csv", HEADLINE, "next_vwap_proxy")
    assert numbers["spread.table.headline.recent.capped_sharpe"] == "-0.19"
    assert numbers["spread.table.top1000.capacity_1"] == "about $20M"
    assert numbers["spread.ar.headline.capacity_2"] == "below the grid"
