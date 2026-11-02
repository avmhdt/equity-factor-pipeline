"""AC-W-03 on the licensed tables, read from the committed aggregate tables."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

pytestmark = pytest.mark.data

TABLES = Path(__file__).parents[2] / "results" / "tables"


def test_every_year_holds_names_that_later_delisted() -> None:
    path = TABLES / "universe_delisted_by_year.csv"
    if not path.is_file():
        pytest.skip("run efp validate survivorship first")
    table = pl.read_csv(path).filter(pl.col("year") < 2026)
    assert table.height >= 25
    assert (table["later_delisted_names"] > 0).all()


def test_the_bias_table_has_each_series_in_both_modes() -> None:
    path = TABLES / "survivorship_bias.csv"
    if not path.is_file():
        pytest.skip("run efp validate survivorship first")
    table = pl.read_csv(path)
    assert set(table["series"].to_list()) == {
        "ew_universe",
        "hml",
        "umd",
        "rmw",
        "cma",
        "smb",
        "low_risk",
    }
    assert table["bias_mean"].null_count() == 0
    assert table["sharpe_full"].null_count() == 0
    assert table["sharpe_survivors_only"].null_count() == 0
