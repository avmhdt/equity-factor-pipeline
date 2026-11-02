"""AC-W-07, AC-W-08, AC-W-09 on the licensed tables, read from the committed aggregates."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

import efp.signals  # noqa: F401
from efp.signals.registry import registered

pytestmark = pytest.mark.data

TABLES = Path(__file__).parents[2] / "results" / "tables"
MODES = {"none", "sector_size_beta"}


def read(name: str) -> pl.DataFrame:
    path = TABLES / f"{name}.csv"
    if not path.is_file():
        pytest.skip("run efp evaluate first")
    return pl.read_csv(path)


@pytest.mark.parametrize("table", ["ic_summary", "quantile_spreads", "turnover", "decay"])
def test_every_signal_appears_in_both_modes(table: str) -> None:
    frame = read(table)
    assert set(frame["signal"].to_list()) == set(registered())
    pairs = frame.select("signal", "neutralize").unique()
    assert pairs.height == 2 * len(registered())
    assert set(pairs["neutralize"].to_list()) == MODES


def test_fama_macbeth_covers_every_signal_alone_in_both_modes() -> None:
    frame = read("fama_macbeth").filter(pl.col("model").eq("univariate"))
    assert frame.height == 2 * len(registered())


def test_ic_summary_has_a_full_sample() -> None:
    frame = read("ic_summary")
    assert frame["months"].min() >= 250
    assert set(frame["universe"].to_list()) == {1000, 3000}


def test_decay_has_every_horizon_and_lag() -> None:
    frame = read("decay")
    assert set(frame.filter(pl.col("curve").eq("horizon"))["step"].to_list()) == {1, 2, 3, 6, 9, 12}
    assert set(frame.filter(pl.col("curve").eq("lag"))["step"].to_list()) == {0, 1, 2, 3, 6, 9, 12}
