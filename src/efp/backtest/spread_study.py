"""The headline book under each spread model, and each model against published spreads.

Added after the Corwin-Schultz result failed net of costs, at the owner's request. Corwin-Schultz
stays the headline. Nothing here feeds back into a composite or a signal.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date

import polars as pl

from efp.attribution.ff import attribute
from efp.backtest.capacity import capacity_curve
from efp.backtest.costs import CostConfig, SpreadModel, trading_inputs
from efp.backtest.engine import BacktestResult, SimulationConfig
from efp.backtest.run import (
    PRICE_COLUMNS,
    WARMUP_DAYS,
    BacktestConfig,
    Market,
    run_book,
    summary_row,
)
from efp.backtest.spread_table import (
    BAND_NAMES,
    CAP_BANDS_MUSD,
    LARGE_CAP_HALF_SPREAD_BPS,
    band_half_spreads_bps,
)
from eqpit.store import PitStore

RECENT_START = date(2010, 1, 1)
"""Both size-bucket sources describe the 2010s, so results are also cut from here on."""
PERIODS: tuple[tuple[str, date | None], ...] = (("full", None), ("2010_on", RECENT_START))
BENCHMARK_YEAR = 2013
LARGE_CAP_MONTHS = (date(2015, 11, 30), date(2015, 12, 31))
MIN_BENCHMARK_PRICE = 2.0
"""The SEC study leaves out stocks under $2."""
TOP_RANK = 500


@dataclass(frozen=True)
class ModelRun:
    """One composite under one spread model."""

    name: str
    model: str
    uncapped: BacktestResult
    capped: BacktestResult


def spread_estimates(
    store: PitStore, ends: list[date], models: Sequence[SpreadModel], config: CostConfig
) -> pl.DataFrame:
    """Unfloored half-spreads per model: ``asof``, ``security_id``, ``model``, ``half_spread``.

    Args:
        store: the point-in-time store.
        ends: month ends.
        models: spread models.
        config: cost choices; the floor is ignored, since these check the estimators.
    """
    labels = store.labels()
    history = labels.universe_history().filter(pl.col("asof").is_in(ends))
    first = date.fromordinal(ends[0].toordinal() - WARMUP_DAYS)
    prices = labels.daily_prices(PRICE_COLUMNS, history["security_id"].unique().to_list(), first)
    frames = [
        trading_inputs(prices, ends, replace(config, floor_bps=0.0, spread_model=model)).select(
            "asof", "security_id", pl.lit(model).alias("model"), "half_spread"
        )
        for model in models
    ]
    framed = prices.filter(pl.col("date").is_in(ends)).select(
        pl.col("date").alias("asof"), "security_id", "close", "mcap"
    )
    ranks = history.select("asof", "security_id", "rank")
    return (
        pl.concat(frames, how="vertical")
        .join(framed, on=["asof", "security_id"], how="inner")
        .join(ranks, on=["asof", "security_id"], how="left")
    )


def _check(
    frame: pl.DataFrame, model: str, band: str, benchmark: float, source: str
) -> dict[str, object]:
    estimate = float(frame["half_spread"].mean()) * 10_000.0 if frame.height else float("nan")  # type: ignore[arg-type]
    return {
        "model": model,
        "band": band,
        "benchmark_bps": benchmark,
        "estimate_bps": estimate,
        "ratio": estimate / benchmark,
        "stock_months": frame.height,
        "benchmark_source": source,
    }


def benchmark_checks(estimates: pl.DataFrame) -> pl.DataFrame:
    """Each model's mean half-spread against published effective spreads for the same stocks.

    ``size_bucket`` is built from these benchmarks, so its rows show its own table applied to
    this universe, not independent evidence.
    """
    rows: list[dict[str, object]] = []
    sec = f"SEC staff, {BENCHMARK_YEAR}"
    year = estimates.filter(
        pl.col("asof").dt.year().eq(BENCHMARK_YEAR), pl.col("close") >= MIN_BENCHMARK_PRICE
    )
    late = estimates.filter(pl.col("asof").is_in(LARGE_CAP_MONTHS), pl.col("rank") <= TOP_RANK)
    bounds = (0.0, *CAP_BANDS_MUSD)
    for model in sorted(estimates["model"].unique().to_list()):
        chosen = year.filter(pl.col("model").eq(model))
        for index, (name, benchmark) in enumerate(
            zip(BAND_NAMES, band_half_spreads_bps(), strict=True)
        ):
            band = chosen.filter(
                pl.col("mcap") >= bounds[index] * 1.0e6, pl.col("mcap") < bounds[index + 1] * 1.0e6
            )
            rows.append(_check(band, model, name, benchmark, sec))
        rows.append(
            _check(
                late.filter(pl.col("model").eq(model)),
                model,
                f"top {TOP_RANK}, late 2015",
                LARGE_CAP_HALF_SPREAD_BPS,
                "Hagstromer, S&P 500, 2015",
            )
        )
    return pl.DataFrame(rows, infer_schema_length=None)


def _period(series: pl.DataFrame, start: date | None) -> pl.DataFrame:
    return series if start is None else series.filter(pl.col("month") >= start)


def model_rows(run: ModelRun, factors: pl.DataFrame, aum: float) -> list[dict[str, object]]:
    """One row per period for one composite under one spread model."""
    rows: list[dict[str, object]] = []
    for period, start in PERIODS:
        uncapped = BacktestResult(_period(run.uncapped.series, start), run.uncapped.neutrality)
        capped = BacktestResult(_period(run.capped.series, start), run.capped.neutrality)
        summary = summary_row(run.name, "", uncapped, aum)
        limited = summary_row(run.name, "", capped, aum)
        net = attribute(uncapped.series, factors, column="ret")
        limited_fit = attribute(capped.series, factors, column="ret")
        rows.append(
            {
                "composite": run.name,
                "spread_model": run.model,
                "period": period,
                "months": summary["months"],
                "gross_sharpe": summary["gross_sharpe"],
                "net_mean_annual": summary["net_mean_annual"],
                "net_sharpe": summary["net_sharpe"],
                "net_alpha_annual": net.alpha_annual,
                "net_alpha_t": net.alpha_t,
                "capped_net_mean_annual": limited["net_mean_annual"],
                "capped_net_sharpe": limited["net_sharpe"],
                "capped_alpha_annual": limited_fit.alpha_annual,
                "capped_alpha_t": limited_fit.alpha_t,
                "spread_cost_annual": summary["spread_cost_annual"],
                "impact_cost_annual": summary["impact_cost_annual"],
                "borrow_cost_annual": summary["borrow_cost_annual"],
            }
        )
    return rows


def capacity_rows(
    name: str,
    model: str,
    run: Callable[[SimulationConfig], BacktestResult],
    factors: pl.DataFrame,
    config: BacktestConfig,
    simulation: SimulationConfig | None = None,
    multipliers: Sequence[float] | None = None,
) -> list[dict[str, object]]:
    """FR-24's test at each impact multiplier, under one spread model."""
    base = simulation or SimulationConfig(aum=config.aum, costs=config.costs)
    rows: list[dict[str, object]] = []
    for multiplier in multipliers or config.impact_multipliers:
        result = capacity_curve(
            run,
            factors,
            replace(base, impact_scale=base.impact_scale * multiplier),
            config.capacity,
        )
        alphas = result.curve["net_alpha_annual"]
        rows.append(
            {
                "composite": name,
                "spread_model": model,
                "impact_multiplier": multiplier,
                "zero_cost_alpha_annual": result.zero_cost_alpha,
                "half_alpha_annual": result.zero_cost_alpha / 2.0,
                "net_alpha_at_grid_min": float(alphas[0]),
                "best_net_alpha": float(alphas.max()),  # type: ignore[arg-type]
                "capacity_usd": result.capacity,
                "status": result.status,
            }
        )
    return rows


def run_model(
    name: str,
    model: str,
    scores: pl.DataFrame,
    market: Market,
    config: BacktestConfig,
    base: SimulationConfig | None = None,
) -> ModelRun:
    """The composite with and without the ADV cap."""
    mode = config.headline_execution
    simulation = base or SimulationConfig(aum=config.aum, costs=config.costs)
    capped = replace(simulation, max_participation=config.capacity.max_participation)
    return ModelRun(
        name,
        model,
        run_book(scores, market, mode, simulation, config),
        run_book(scores, market, mode, capped, config),
    )
