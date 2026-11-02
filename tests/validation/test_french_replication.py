"""AC-W-01 on the licensed tables, read from the committed aggregate table."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

pytestmark = pytest.mark.data

TABLE = Path(__file__).parents[2] / "results" / "tables" / "french_replication.csv"
THRESHOLDS = {"umd": 0.90, "smb": 0.90, "hml": 0.80}


@pytest.mark.parametrize(("factor", "threshold"), THRESHOLDS.items())
def test_correlation_with_french_clears_the_bar(factor: str, threshold: float) -> None:
    if not TABLE.is_file():
        pytest.skip("run efp validate french first")
    row = pl.read_csv(TABLE).filter(pl.col("factor").eq(factor)).row(0, named=True)
    assert row["months"] > 300
    assert row["correlation"] >= threshold


def test_rmw_and_cma_are_reported() -> None:
    if not TABLE.is_file():
        pytest.skip("run efp validate french first")
    table = pl.read_csv(TABLE)
    assert table.filter(pl.col("factor").is_in(["rmw", "cma"]))["correlation"].null_count() == 0
