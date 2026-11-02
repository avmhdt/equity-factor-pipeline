"""Published impact laws, checked against the numbers their own papers print."""

from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from efp.backtest.engine import SimulationConfig, Trading, simulate, trading_lookup
from efp.backtest.impact_models import (
    ALMGREN_ETA,
    ALMGREN_EXPONENT,
    ALMGREN_GAMMA,
    ALMGREN_LIQUIDITY_EXPONENT,
    PEAK_TO_AVERAGE,
    almgren_cost,
    square_root_cost,
    zarinelli_cost,
)
from efp.backtest.impact_study import LAWS, cost_checks


@pytest.mark.parametrize(("inverse_turnover", "expected"), [(263.0, 0.126), (87.0, 0.096)])
def test_permanent_impact_matches_almgren_table_3(inverse_turnover: float, expected: float) -> None:
    normalized = ALMGREN_GAMMA * 0.1 * inverse_turnover**ALMGREN_LIQUIDITY_EXPONENT
    assert normalized == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize(("duration", "expected"), [(0.1, 0.142), (0.2, 0.094), (0.5, 0.054)])
def test_temporary_impact_matches_almgren_table_3(duration: float, expected: float) -> None:
    normalized = ALMGREN_ETA * (0.1 / duration) ** ALMGREN_EXPONENT
    assert normalized == pytest.approx(expected, abs=0.001)


def test_almgren_realized_cost_adds_half_the_permanent_part() -> None:
    vol = 0.0157
    permanent = ALMGREN_GAMMA * vol * 0.1 * 263.0**ALMGREN_LIQUIDITY_EXPONENT
    expected = permanent / 2.0 + vol * ALMGREN_ETA * 0.1**ALMGREN_EXPONENT
    assert almgren_cost(0.1, vol, 263.0) == pytest.approx(expected)


def test_the_mean_along_a_square_root_path_is_two_thirds() -> None:
    steps = 200_000
    mean = sum(math.sqrt((index + 0.5) / steps) for index in range(steps)) / steps
    assert mean == pytest.approx(PEAK_TO_AVERAGE, abs=1e-6)
    assert square_root_cost(0.01, 0.02, PEAK_TO_AVERAGE) == pytest.approx(0.02 * 0.1 * 2.0 / 3.0)


@pytest.mark.parametrize(
    ("rank", "scale", "delta"),
    [
        (10, 0.19, 0.51),
        (500, 0.19, 0.51),
        (501, 0.15, 0.46),
        (1000, 0.15, 0.46),
        (1001, 0.12, 0.42),
    ],
)
def test_zarinelli_uses_the_fit_for_the_size_class(rank: int, scale: float, delta: float) -> None:
    expected = 0.03 * scale * 0.01**delta / (1.0 + delta)
    assert zarinelli_cost(0.01, 0.03, rank) == pytest.approx(expected)


def test_a_name_outside_the_universe_is_small() -> None:
    assert zarinelli_cost(0.01, 0.03, None) == zarinelli_cost(0.01, 0.03, 2000)


def test_published_laws_cost_less_than_the_headline_at_a_typical_trade() -> None:
    vol, daily_range, x = 0.02, 0.032, 0.01
    headline = square_root_cost(x, vol)
    assert almgren_cost(x, vol, 263.0) < headline
    assert zarinelli_cost(x, daily_range, 400) < headline


def _book(impact_form: str, scale: float = 1.0) -> SimulationConfig:
    return SimulationConfig(aum=1.0e7, impact_form=impact_form, impact_scale=scale)  # type: ignore[arg-type]


def _lookup() -> tuple[pl.DataFrame, Trading]:
    ends = [date(2020, 1, 31), date(2020, 2, 28)]
    targets = pl.DataFrame(
        {"asof": [ends[0]] * 2, "security_id": ["a", "b"], "weight": [1.0, -1.0]}
    )
    inputs = pl.DataFrame(
        {
            "asof": [ends[0]] * 2,
            "security_id": ["a", "b"],
            "half_spread": [0.0, 0.0],
            "daily_vol": [0.02, 0.02],
            "adv": [1.0e9, 1.0e9],
            "mcap": [2.63e11, 2.63e11],
            "daily_range": [0.032, 0.032],
        }
    )
    ranks = inputs.select(
        "asof", "security_id", pl.lit(0.0).alias("borrow"), pl.lit(100).alias("rank")
    )
    return targets, trading_lookup(inputs, ranks)


ENDS = [date(2020, 1, 31), date(2020, 2, 28)]


def test_the_engine_charges_almgren() -> None:
    targets, trading = _lookup()
    series = simulate(targets, {}, trading, ENDS, _book("almgren_2005")).series
    assert series["impact_cost"].item() == pytest.approx(2.0 * almgren_cost(0.01, 0.02, 263.0))


def test_the_engine_charges_zarinelli_on_the_daily_range() -> None:
    targets, trading = _lookup()
    series = simulate(targets, {}, trading, ENDS, _book("zarinelli_2015")).series
    assert series["impact_cost"].item() == pytest.approx(2.0 * zarinelli_cost(0.01, 0.032, 100))


def test_a_coefficient_scales_the_square_root() -> None:
    targets, trading = _lookup()
    one = simulate(targets, {}, trading, ENDS, _book("square_root")).series["impact_cost"].item()
    two = simulate(targets, {}, trading, ENDS, _book("square_root", PEAK_TO_AVERAGE)).series
    assert two["impact_cost"].item() == pytest.approx(one * PEAK_TO_AVERAGE)


def test_the_headline_default_stays_the_square_root_at_one() -> None:
    config = SimulationConfig()
    assert config.impact_form == "square_root"
    assert config.impact_scale == 1.0
    assert LAWS[0].form == "square_root"
    assert LAWS[0].coefficient == 1.0


def test_cost_checks_rise_with_size_and_cover_each_law() -> None:
    table = cost_checks()
    assert table.height == 5
    for column in table.columns:
        assert table[column].is_sorted()


def test_frazzini_terms_match_table_v() -> None:
    row = cost_checks().filter(pl.col("share_of_daily_volume").eq(0.01)).row(0, named=True)
    assert row["frazzini_size_terms"] == pytest.approx(1.30 + 6.03)
