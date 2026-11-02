"""AC-H-04: the attribution regression."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from efp.attribution.ff import FACTORS, attribute


@pytest.fixture
def frames() -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(4)
    months = pl.date_range(date(2000, 1, 31), date(2019, 12, 31), "1mo", eager=True)
    factors = pl.DataFrame(
        {"month": months, **{name: rng.normal(0.0, 0.03, months.len()) for name in FACTORS}}
    )
    loadings = dict(zip(FACTORS, (0.2, -0.1, 0.4, 0.0, 0.3, -0.5), strict=True))
    explained = sum(factors[name].to_numpy() * value for name, value in loadings.items())
    returns = pl.DataFrame(
        {"month": months, "ret": 0.005 + explained + rng.normal(0.0, 0.001, months.len())}
    )
    return returns, factors


def test_alpha_and_loadings_are_recovered(frames: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    returns, factors = frames
    fitted = attribute(returns, factors)
    assert fitted.alpha_annual == pytest.approx(0.06, abs=0.002)
    assert fitted.loadings["umd"] == pytest.approx(-0.5, abs=0.01)
    assert fitted.loadings["hml"] == pytest.approx(0.4, abs=0.01)
    assert fitted.r_squared > 0.99
    assert fitted.months == 240
    assert fitted.lags == 4


def test_only_overlapping_months_count(frames: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    returns, factors = frames
    fitted = attribute(returns.head(120), factors)
    assert fitted.months == 120
