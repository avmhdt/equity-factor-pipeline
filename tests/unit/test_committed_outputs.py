"""NFR-07, D-13: what's committed is aggregate. A review checklist as a test."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SECURITY_COLUMNS = {"security_id", "company_id", "ticker", "cik", "permaticker", "symbol"}
TABLES = sorted((ROOT / "results" / "tables").glob("*.csv"))


def tracked() -> list[str]:
    try:
        done = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return done.stdout.splitlines()


@pytest.mark.parametrize("path", TABLES, ids=lambda path: path.stem)
def test_no_table_has_a_security_column(path: Path) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert not SECURITY_COLUMNS & {column.lower() for column in header}


def test_no_return_series_is_tracked() -> None:
    assert not [name for name in tracked() if name.endswith((".parquet", ".feather", ".gz"))]


def test_nothing_under_data_is_tracked() -> None:
    assert not [name for name in tracked() if name.startswith("data/")]
