"""Capacity: the fund size at which costs erase half the alpha (FR-24)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
import polars as pl

from efp.attribution.ff import attribute
from efp.backtest.engine import BacktestResult, SimulationConfig


@dataclass(frozen=True)
class CapacityConfig:
    """The AUM grid (FR-24)."""

    aum_min: float = 1.0e7
    aum_max: float = 1.0e10
    points: int = 25
    max_participation: float = 0.10


@dataclass(frozen=True)
class CapacityResult:
    """The curve and the estimate; ``capacity`` is None unless ``status`` is ``estimated``."""

    curve: pl.DataFrame
    zero_cost_alpha: float
    capacity: float | None
    status: str


def crossing(aums: list[float], alphas: list[float], level: float) -> float | None:
    """First AUM where alpha falls to ``level``, interpolating on log AUM."""
    for index in range(1, len(aums)):
        if alphas[index - 1] >= level >= alphas[index]:
            drop = alphas[index - 1] - alphas[index]
            weight = 0.0 if drop == 0 else (alphas[index - 1] - level) / drop
            low, high = math.log(aums[index - 1]), math.log(aums[index])
            return math.exp(low + weight * (high - low))
    return None


def capacity_curve(
    run: Callable[[SimulationConfig], BacktestResult],
    factors: pl.DataFrame,
    base: SimulationConfig,
    config: CapacityConfig,
) -> CapacityResult:
    """Run the book across the grid and find where net alpha hits half the gross alpha.

    Args:
        run: simulates one configuration.
        factors: monthly factors for attribution.
        base: the settings every grid point shares.
        config: the grid.
    """
    frictionless = run(replace(base, aum=0.0, spread_scale=0.0, impact_scale=0.0, borrow_scale=0.0))
    gross_alpha = attribute(frictionless.series, factors, column="ret").alpha_annual
    grid = np.logspace(math.log10(config.aum_min), math.log10(config.aum_max), config.points)
    rows: list[dict[str, float]] = []
    for aum in grid.tolist():
        result = run(replace(base, aum=aum, max_participation=config.max_participation))
        fitted = attribute(result.series, factors, column="ret")
        rows.append(
            {
                "aum": aum,
                "net_alpha_annual": fitted.alpha_annual,
                "net_mean_annual": float(result.series["ret"].mean()) * 12.0,  # type: ignore[arg-type]
                "turnover": float(result.series["turnover"].mean()),  # type: ignore[arg-type]
            }
        )
    curve = pl.DataFrame(rows)
    target = gross_alpha / 2.0
    alphas = curve["net_alpha_annual"].to_list()
    found = crossing(curve["aum"].to_list(), alphas, target)
    if gross_alpha <= 0:
        return CapacityResult(curve, gross_alpha, None, "no gross alpha")
    if found is not None:
        return CapacityResult(curve, gross_alpha, found, "estimated")
    status = "below grid min" if alphas[0] < target else "above grid max"
    return CapacityResult(curve, gross_alpha, None, status)
