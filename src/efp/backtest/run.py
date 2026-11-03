"""Run composites through the backtest: market data, every execution mode, summary rows."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

import polars as pl

from efp.backtest.capacity import CapacityConfig
from efp.backtest.costs import CostConfig, borrow_rates, trading_inputs
from efp.backtest.engine import (
    BacktestResult,
    Returns,
    SimulationConfig,
    Trading,
    return_lookup,
    simulate,
    trading_lookup,
)
from efp.backtest.execution import (
    MODES,
    Execution,
    holding_returns,
    next_session_rows,
    timing_factors,
)
from efp.backtest.portfolio import Construction, target_weights
from efp.evaluation.ic import newey_west_t
from eqpit.store import PitStore

PRICE_COLUMNS = (
    "security_id",
    "date",
    "open",
    "high",
    "low",
    "close",
    "dollar_volume",
    "ret",
    "split_factor",
    "mcap",
)
WARMUP_DAYS = 60


@dataclass(frozen=True)
class BacktestConfig:
    """Choices from the experiment's ``backtest`` section."""

    construction: Construction = "decile"
    gross: float = 2.0
    quantiles: int = 10
    executions: tuple[Execution, ...] = MODES
    headline_execution: Execution = "next_vwap_proxy"
    aum: float = 1.0e8
    costs: CostConfig = field(default_factory=CostConfig)
    impact_multipliers: tuple[float, ...] = (0.5, 1.0, 2.0)
    capacity: CapacityConfig = field(default_factory=CapacityConfig)


@dataclass(frozen=True)
class Market:
    """What every simulation reads."""

    ends: list[date]
    trading: Trading
    returns: dict[str, Returns] = field(default_factory=dict)


def load_market(store: PitStore, ends: list[date], config: BacktestConfig) -> Market:
    """Trading inputs and holding returns over ``ends``, for names ever in the universe."""
    labels = store.labels()
    history = labels.universe_history().filter(pl.col("asof").is_in(ends))
    first = date.fromordinal(ends[0].toordinal() - WARMUP_DAYS)
    prices = labels.daily_prices(PRICE_COLUMNS, history["security_id"].unique().to_list(), first)
    inputs = trading_inputs(prices, ends, config.costs)
    ranks = history.select(
        "asof",
        "security_id",
        "rank",
        borrow_rates(pl.col("rank"), config.costs).alias("borrow"),
    )
    forward = labels.forward_returns((1,)).select("asof", "security_id", "fwd_ret")
    following = next_session_rows(prices, ends)
    returns: dict[str, Returns] = {
        mode: return_lookup(holding_returns(forward, timing_factors(following, mode), ends))
        for mode in config.executions
    }
    return Market(ends, trading_lookup(inputs, ranks), returns)


def run_book(
    scores: pl.DataFrame,
    market: Market,
    mode: str,
    simulation: SimulationConfig,
    config: BacktestConfig,
) -> BacktestResult:
    """Simulate one composite under one execution mode."""
    targets = target_weights(scores, config.construction, config.gross, config.quantiles)
    return simulate(targets, market.returns[mode], market.trading, market.ends, simulation)


def frictionless(simulation: SimulationConfig) -> SimulationConfig:
    """The same run with every cost removed."""
    return replace(simulation, aum=0.0, spread_scale=0.0, impact_scale=0.0, borrow_scale=0.0)


def _sharpe(values: pl.Series) -> float:
    deviation = float(values.std())  # type: ignore[arg-type]
    return float(values.mean()) / deviation * math.sqrt(12.0) if deviation > 0 else 0.0  # type: ignore[arg-type]


def summary_row(name: str, mode: str, result: BacktestResult, aum: float) -> dict[str, object]:
    """One ``backtest_summary`` row: per-year means, Sharpe, NW t of the net series."""
    series = result.series
    costs = {
        column: float(series[column].mean()) * 12.0  # type: ignore[arg-type]
        for column in ("spread_cost", "impact_cost", "borrow_cost")
    }
    return {
        "composite": name,
        "execution": mode,
        "months": series.height,
        "aum_usd": aum,
        "gross_mean_annual": float(series["ret_gross"].mean()) * 12.0,  # type: ignore[arg-type]
        "net_mean_annual": float(series["ret"].mean()) * 12.0,  # type: ignore[arg-type]
        "gross_sharpe": _sharpe(series["ret_gross"]),
        "net_sharpe": _sharpe(series["ret"]),
        "net_t_nw": newey_west_t(series["ret"].to_numpy()),
        "turnover_monthly": float(series["turnover"].mean()),  # type: ignore[arg-type]
        **{f"{column}_annual": value for column, value in costs.items()},
        "worst_net_exposure": result.neutrality,
    }
