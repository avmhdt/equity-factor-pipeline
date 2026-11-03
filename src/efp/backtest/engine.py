"""The monthly simulation: target weights, fills, drift and costs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

import numpy as np
import polars as pl

from efp.backtest.costs import CostConfig
from efp.backtest.impact_models import ImpactForm, almgren_cost, zarinelli_cost


@dataclass(frozen=True)
class SimulationConfig:
    """One run.

    Args:
        aum: fund size in USD; zero removes impact.
        max_participation: largest trade as a share of ADV; None is no limit.
        spread_scale: multiplier on half-spreads, zero for a cost-free run.
        impact_scale: multiplier on impact.
        impact_form: the impact law; D-06 sets the square root for headline results.
        borrow_scale: multiplier on borrow.
        costs: D-06 parameters.
    """

    aum: float = 1.0e8
    max_participation: float | None = None
    spread_scale: float = 1.0
    impact_scale: float = 1.0
    impact_form: ImpactForm = "square_root"
    borrow_scale: float = 1.0
    costs: CostConfig = field(default_factory=CostConfig)


@dataclass(frozen=True)
class BacktestResult:
    """Monthly series ``month``, ``ret`` (net), ``ret_gross``, ``turnover``, cost columns."""

    series: pl.DataFrame
    neutrality: float


Trading = dict[date, dict[str, tuple[float | None, ...]]]
"""Per rebalance and security: half-spread, daily deviation, ADV, borrow rate, market cap, rank
and daily range. The last three feed only the published impact laws."""
Returns = dict[date, dict[str, float]]
"""Per rebalance and security: the return to the next execution."""
_NO_INPUTS: tuple[float | None, ...] = (None,) * 7
_COLUMNS = ("half_spread", "daily_vol", "adv", "borrow", "mcap", "rank", "daily_range")


def trading_lookup(inputs: pl.DataFrame, ranks: pl.DataFrame) -> Trading:
    """Costs for every name on a date, held or not: a stock leaving the book trades too.

    Args:
        inputs: ``asof``, ``security_id``, ``half_spread``, ``daily_vol``, ``adv``, and
            optionally ``mcap`` and ``daily_range``.
        ranks: ``asof``, ``security_id``, ``borrow`` (annual decimal rate), optionally ``rank``.
    """
    joined = inputs.join(ranks, on=["asof", "security_id"], how="full", coalesce=True)
    missing = [name for name in _COLUMNS if name not in joined.columns]
    joined = joined.with_columns(pl.lit(None, dtype=pl.Float64).alias(name) for name in missing)
    return {
        key[0]: {row[0]: row[1:] for row in part.select("security_id", *_COLUMNS).iter_rows()}
        for key, part in joined.group_by(["asof"], maintain_order=True)
    }


def _published_cost(
    form: ImpactForm, share: float, vol: float, depth: float, inputs: tuple[float | None, ...]
) -> float:
    """Cost per dollar under a published law, for a trade of ``share`` of daily volume."""
    mcap, rank, daily_range = inputs[4], inputs[5], inputs[6]
    if form == "almgren_2005":
        return almgren_cost(share, vol, (mcap or depth) / depth)
    return zarinelli_cost(share, daily_range or 0.0, None if rank is None else int(rank))


def return_lookup(returns: pl.DataFrame) -> Returns:
    """``asof``, ``security_id``, ``ret``, execution to next execution, as a lookup."""
    rows = returns.filter(pl.col("ret").is_finite())
    return {
        key[0]: dict(zip(part["security_id"].to_list(), part["ret"].to_list(), strict=True))
        for key, part in rows.group_by(["asof"], maintain_order=True)
    }


def simulate(
    targets: pl.DataFrame,
    returns: Returns,
    trading: Trading,
    ends: list[date],
    config: SimulationConfig,
) -> BacktestResult:
    """Walk through the rebalances.

    Args:
        targets: ``asof``, ``security_id``, ``weight``.
        returns: from ``return_lookup``.
        trading: from ``trading_lookup``.
        ends: month ends, sorted.
        config: run settings.

    Returns:
        Series dated at the end of each holding month. A name with no return in a month earns
        zero: its proceeds sit flat, matching ``forward_returns`` after a delisting. A name with
        no trading inputs pays the spread floor and the top borrow rate.
    """
    following = dict(pairwise(ends))
    by_date = {
        key[0]: part
        for key, part in targets.sort("asof", "security_id").group_by(["asof"], maintain_order=True)
    }
    held: dict[str, float] = {}
    rows: list[dict[str, object]] = []
    worst = 0.0
    costs = config.costs
    floor = costs.floor_bps / 10_000.0
    fallback_borrow = costs.borrow_rest / 100.0
    k = costs.impact_coefficient * config.impact_scale
    for asof in sorted(by_date):
        if asof not in following:
            continue
        part = by_date[asof]
        target = dict(zip(part["security_id"].to_list(), part["weight"].to_list(), strict=True))
        today = trading.get(asof, {})
        spread_cost = 0.0
        impact_cost = 0.0
        borrow_cost = 0.0
        traded = 0.0
        after: dict[str, float] = {}
        for name in sorted(set(target) | set(held)):
            old = held.get(name, 0.0)
            change = target.get(name, 0.0) - old
            inputs = today.get(name, _NO_INPUTS)
            half_spread, vol, depth, borrow = inputs[0], inputs[1], inputs[2], inputs[3]
            if config.max_participation is not None and config.aum > 0 and depth:
                limit = config.max_participation * depth / config.aum
                change = float(np.clip(change, -limit, limit))
            size = abs(change)
            traded += size
            spread_cost += size * (floor if half_spread is None else half_spread)
            if config.aum > 0 and depth and vol and size > 0:
                if config.impact_form == "square_root":
                    impact_cost += size * k * vol * np.sqrt(size * config.aum / depth)
                else:
                    share = size * config.aum / depth
                    rate = _published_cost(config.impact_form, share, vol, depth, inputs)
                    impact_cost += size * config.impact_scale * rate
            position = old + change
            if position != 0.0:
                after[name] = position
                if position < 0:
                    rate = fallback_borrow if borrow is None else borrow
                    borrow_cost -= position * rate / 12.0
        worst = max(worst, abs(sum(after.values())))
        spread_cost *= config.spread_scale
        borrow_cost *= config.borrow_scale
        earned = returns.get(asof, {})
        gross = sum(position * earned.get(name, 0.0) for name, position in after.items())
        net = gross - spread_cost - impact_cost - borrow_cost
        base = 1.0 + gross
        held = {
            name: position * (1.0 + earned.get(name, 0.0)) / base
            for name, position in after.items()
        }
        rows.append(
            {
                "month": following[asof],
                "ret": net,
                "ret_gross": gross,
                "turnover": traded / 2.0,
                "spread_cost": spread_cost,
                "impact_cost": impact_cost,
                "borrow_cost": borrow_cost,
            }
        )
    series = pl.DataFrame(
        rows,
        schema={
            "month": pl.Date,
            "ret": pl.Float64,
            "ret_gross": pl.Float64,
            "turnover": pl.Float64,
            "spread_cost": pl.Float64,
            "impact_cost": pl.Float64,
            "borrow_cost": pl.Float64,
        },
    )
    return BacktestResult(series, worst)
