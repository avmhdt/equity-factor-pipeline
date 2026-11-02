"""Golden numbers for the synthetic build. Update only with ``--update-golden``."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from eqpit.pit.build import read_pit_manifest
from eqpit.store import PitStore

GOLDEN = Path(__file__).with_name("synthetic.json")
TOLERANCE = 1e-10


def summary(store: PitStore) -> dict[str, float]:
    root = store.root
    prices = pl.read_parquet(root / "prices_daily" / "**" / "*.parquet").sort("security_id", "date")
    fundamentals = pl.read_parquet(root / "fundamentals_pit" / "**" / "*.parquet")
    universe = pl.read_parquet(root / "universe_monthly" / "**" / "*.parquet")
    delistings = pl.read_parquet(root / "delistings" / "**" / "*.parquet")
    forward = pl.read_parquet(root / "forward_returns" / "**" / "*.parquet").sort(
        "asof", "security_id", "h"
    )
    numbers: dict[str, float] = {
        f"rows.{name}": float(count)
        for name, count in read_pit_manifest(root)["row_counts"].items()
    }
    numbers.update(
        {
            "prices.mean_ret": float(prices["ret"].mean()),
            "prices.std_ret": float(prices["ret"].std()),
            "prices.flagged": float((prices["price_flag"] > 0).sum()),
            "fundamentals.restatements": float(fundamentals["is_restatement"].sum()),
            "fundamentals.log_assets": float(
                fundamentals.filter(pl.col("item").eq("total_assets"))["value"].log().mean()
            ),
            "universe.mean_size": universe.height / universe["asof"].n_unique(),
            "delistings.mean_return": float(delistings["dlret"].mean()),
            "forward.mean_1m": float(forward.filter(pl.col("h").eq(1))["fwd_ret"].mean()),
            "forward.mean_12m": float(forward.filter(pl.col("h").eq(12))["fwd_ret"].mean()),
        }
    )
    return dict(sorted(numbers.items()))


def test_synthetic_numbers_match_golden(
    small_store: PitStore, request: pytest.FixtureRequest
) -> None:
    numbers = summary(small_store)
    if request.config.getoption("--update-golden"):
        GOLDEN.write_text(json.dumps(numbers, indent=2) + "\n", encoding="utf-8", newline="\n")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(numbers) == set(expected)
    for name, value in numbers.items():
        assert value == pytest.approx(expected[name], rel=TOLERANCE, abs=TOLERANCE), name
