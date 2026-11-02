"""Published impact laws, checked against the numbers their own papers print."""

from __future__ import annotations

import math

import pytest

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
