"""AC-W-11, AC-H-04, AC-H-05 and AC-H-07 on the licensed tables, read from the committed results."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

pytestmark = pytest.mark.data

TABLES = Path(__file__).parents[2] / "results" / "tables"
HEADLINE = "equal_sector_size_beta"
EXECUTIONS = {"close", "next_open", "next_close", "next_vwap_proxy"}
FAMILIES = {"value", "momentum", "quality", "investment", "low_risk"}


def _read(name: str) -> pl.DataFrame:
    path = TABLES / f"{name}.csv"
    if not path.is_file():
        pytest.skip(f"run efp rebuild first: {name}")
    return pl.read_csv(path)


def test_net_is_below_gross_and_every_book_is_neutral() -> None:
    table = _read("backtest_summary")
    assert (table["net_mean_annual"] <= table["gross_mean_annual"]).all()
    assert table["worst_net_exposure"].max() < 1e-6  # type: ignore[operator]


def test_timing_covers_the_composite_and_each_family() -> None:
    table = _read("execution_timing")
    for name in (HEADLINE, *[f"family_{family}_sector_size_beta" for family in FAMILIES]):
        assert set(table.filter(pl.col("composite").eq(name))["execution"]) == EXECUTIONS
    proxy = table.filter(pl.col("execution").eq("next_vwap_proxy"))["price"].to_list()
    assert all("proxy" in label for label in proxy)


def test_attribution_reports_gross_and_net() -> None:
    table = _read("attribution").filter(pl.col("composite").eq(HEADLINE))
    assert set(table["basis"]) == {"gross", "net"}
    assert {"alpha_annual", "alpha_t", "umd", "r_squared"} <= set(table.columns)


def test_capacity_has_each_impact_multiplier() -> None:
    table = _read("capacity").filter(pl.col("composite").eq(HEADLINE))
    assert set(table["impact_multiplier"]) == {0.5, 1.0, 2.0}
    known = {"estimated", "above grid max", "below grid min", "no gross alpha"}
    assert set(table["status"]) <= known


def test_cost_sensitivity_has_both_sides_of_d06() -> None:
    table = (
        _read("cost_sensitivity").filter(pl.col("composite").eq(HEADLINE)).sort("cost_multiplier")
    )
    assert table["cost_multiplier"].to_list() == [0.5, 1.0, 2.0]
    assert table["uncapped_net_mean_annual"].is_sorted(descending=True)


def test_composites_are_in_the_multiple_testing_table() -> None:
    table = _read("multiple_testing")
    assert table.filter(pl.col("strategy_family").eq("composite")).height > 0
