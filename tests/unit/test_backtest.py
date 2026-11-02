"""AC-W-11: the backtest, its costs and its capacity search, on small hand-made books."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest

from efp.backtest.capacity import CapacityConfig, capacity_curve, crossing
from efp.backtest.costs import CostConfig, borrow_rates, corwin_schultz, trading_inputs
from efp.backtest.engine import (
    SimulationConfig,
    return_lookup,
    simulate,
    trading_lookup,
)
from efp.backtest.execution import holding_returns, next_session_rows, timing_factors
from efp.backtest.portfolio import target_weights
from efp.backtest.run import frictionless, summary_row

ENDS = [date(2020, 1, 31), date(2020, 2, 28), date(2020, 3, 31), date(2020, 4, 30)]
NAMES = [f"s{index}" for index in range(10)]


@pytest.fixture
def book() -> dict[str, pl.DataFrame]:
    rng = np.random.default_rng(11)
    scores = pl.DataFrame(
        {
            "asof": [end for end in ENDS for _ in NAMES],
            "security_id": NAMES * len(ENDS),
            "score": rng.normal(size=len(ENDS) * len(NAMES)),
        }
    )
    returns = scores.select(
        "asof", "security_id", pl.Series("ret", rng.normal(0.01, 0.05, scores.height))
    )
    inputs = scores.select(
        "asof",
        "security_id",
        pl.lit(0.001).alias("half_spread"),
        pl.lit(0.02).alias("daily_vol"),
        pl.lit(5.0e7).alias("adv"),
    )
    ranks = scores.select("asof", "security_id", pl.lit(0.0025).alias("borrow"))
    return {"scores": scores, "returns": returns, "inputs": inputs, "ranks": ranks}


def run(book: dict[str, pl.DataFrame], config: SimulationConfig):  # type: ignore[no-untyped-def]
    return simulate(
        target_weights(book["scores"], quantiles=5),
        return_lookup(book["returns"]),
        trading_lookup(book["inputs"], book["ranks"]),
        ENDS,
        config,
    )


def test_net_never_exceeds_gross(book: dict[str, pl.DataFrame]) -> None:
    series = run(book, SimulationConfig()).series
    assert series.height == 3
    assert (series["ret"] <= series["ret_gross"]).all()


def test_zero_cost_run_equals_gross(book: dict[str, pl.DataFrame]) -> None:
    series = run(book, frictionless(SimulationConfig())).series
    assert (series["ret"] - series["ret_gross"]).abs().max() < 1e-12


def test_every_rebalance_is_dollar_neutral(book: dict[str, pl.DataFrame]) -> None:
    assert run(book, SimulationConfig()).neutrality < 1e-9


def test_a_name_leaving_the_book_pays_to_sell() -> None:
    targets = pl.DataFrame(
        {
            "asof": [ENDS[0], ENDS[0], ENDS[1], ENDS[1]],
            "security_id": ["a", "b", "c", "d"],
            "weight": [1.0, -1.0, 1.0, -1.0],
        }
    )
    frame = pl.DataFrame(
        {
            "asof": [end for end in ENDS[:2] for _ in "abcd"],
            "security_id": list("abcd") * 2,
            "half_spread": [0.01, 0.01, 0.0, 0.0] * 2,
            "daily_vol": [0.0] * 8,
            "adv": [1.0e9] * 8,
        }
    )
    ranks = frame.select("asof", "security_id", pl.lit(0.0).alias("borrow"))
    result = simulate(targets, {}, trading_lookup(frame, ranks), ENDS, SimulationConfig(aum=0.0))
    assert result.series["spread_cost"].to_list() == pytest.approx([0.02, 0.02])


def test_costs_grow_with_fund_size(book: dict[str, pl.DataFrame]) -> None:
    small = run(book, SimulationConfig(aum=1.0e7)).series["impact_cost"].sum()
    large = run(book, SimulationConfig(aum=1.0e9)).series["impact_cost"].sum()
    assert large == pytest.approx(small * 10.0)


def test_scales_multiply_their_own_cost(book: dict[str, pl.DataFrame]) -> None:
    base = run(book, SimulationConfig()).series
    doubled = run(book, SimulationConfig(impact_scale=2.0)).series
    assert doubled["impact_cost"].sum() == pytest.approx(base["impact_cost"].sum() * 2.0)
    assert doubled["spread_cost"].sum() == pytest.approx(base["spread_cost"].sum())


def test_borrow_is_charged_on_shorts_only() -> None:
    targets = pl.DataFrame(
        {"asof": [ENDS[0]] * 2, "security_id": ["a", "b"], "weight": [1.0, -1.0]}
    )
    frame = pl.DataFrame(
        {
            "asof": [ENDS[0]] * 2,
            "security_id": ["a", "b"],
            "half_spread": [0.0, 0.0],
            "daily_vol": [0.0, 0.0],
            "adv": [1.0, 1.0],
        }
    )
    ranks = frame.select("asof", "security_id", pl.Series("borrow", [0.5, 0.12]))
    result = simulate(targets, {}, trading_lookup(frame, ranks), ENDS, SimulationConfig(aum=0.0))
    assert result.series["borrow_cost"].to_list() == pytest.approx([0.01])


def test_participation_caps_a_trade() -> None:
    targets = pl.DataFrame(
        {"asof": [ENDS[0]] * 2, "security_id": ["a", "b"], "weight": [1.0, -1.0]}
    )
    frame = pl.DataFrame(
        {
            "asof": [ENDS[0]] * 2,
            "security_id": ["a", "b"],
            "half_spread": [0.0, 0.0],
            "daily_vol": [0.0, 0.0],
            "adv": [1.0e6, 1.0e6],
        }
    )
    ranks = frame.select("asof", "security_id", pl.lit(0.0).alias("borrow"))
    config = SimulationConfig(aum=1.0e7, max_participation=0.1)
    result = simulate(targets, {}, trading_lookup(frame, ranks), ENDS, config)
    assert result.series["turnover"].to_list() == pytest.approx([0.01])


def test_weights_are_neutral_at_the_gross_asked_for(book: dict[str, pl.DataFrame]) -> None:
    for construction in ("decile", "signal_weighted"):
        weights = target_weights(book["scores"], construction, 2.0, 5)  # type: ignore[arg-type]
        totals = weights.group_by("asof").agg(
            pl.col("weight").sum().alias("net"), pl.col("weight").abs().sum().alias("gross")
        )
        assert totals["net"].abs().max() < 1e-12
        assert (totals["gross"] - 2.0).abs().max() < 1e-12


def test_corwin_schultz_is_zero_on_a_flat_range() -> None:
    high = np.array([10.5, 10.5, 10.5, 10.5])
    low = np.array([9.5, 9.5, 9.5, 9.5])
    assert corwin_schultz(high, low) > 0.0
    assert corwin_schultz(np.array([10.0, 11.0, 12.0]), np.array([9.9, 10.9, 11.9])) == 0.0


def test_trading_inputs_match_the_estimator_and_see_no_later_rows() -> None:
    dates = pl.date_range(date(2020, 1, 1), date(2020, 2, 28), "1d", eager=True).to_list()
    rng = np.random.default_rng(3)
    middle = 10.0 + rng.normal(0.0, 0.02, len(dates)).cumsum()
    prices = pl.DataFrame(
        {
            "security_id": ["a"] * len(dates),
            "date": dates,
            "high": middle * 1.01,
            "low": middle * 0.99,
            "ret": rng.normal(0.0, 0.01, len(dates)),
            "dollar_volume": [1.0e6] * len(dates),
            "split_factor": [1.0] * len(dates),
        }
    )
    end = date(2020, 1, 31)
    config = CostConfig(floor_bps=0.0)
    row = trading_inputs(prices, [end], config).row(0, named=True)
    upto = prices.filter(pl.col("date") <= end).tail(21)
    expected = corwin_schultz(upto["high"].to_numpy(), upto["low"].to_numpy()) / 2.0
    assert row["half_spread"] == pytest.approx(expected, rel=1e-9)
    assert row["adv"] == pytest.approx(1.0e6)
    later = prices.with_columns(
        pl.when(pl.col("date") > end).then(pl.col("high") * 3.0).otherwise(pl.col("high"))
    )
    assert trading_inputs(later, [end], config).row(0, named=True) == row


def test_borrow_schedule_is_d06() -> None:
    frame = pl.DataFrame({"rank": [1, 500, 501, 1000, 1001, 3000]})
    rates = frame.select(borrow_rates(pl.col("rank"), CostConfig()).alias("rate"))
    assert rates["rate"].to_list() == pytest.approx([0.0025, 0.0025, 0.01, 0.01, 0.05, 0.05])


def test_holding_return_runs_from_execution_to_execution() -> None:
    labels = pl.DataFrame(
        {"asof": [ENDS[0]], "security_id": ["a"], "fwd_ret": [0.10]},
        schema_overrides={"asof": pl.Date},
    )
    prices = pl.DataFrame(
        {
            "security_id": ["a", "a"],
            "date": [date(2020, 2, 3), date(2020, 3, 2)],
            "open": [100.0, 110.0],
            "high": [100.0, 110.0],
            "low": [100.0, 110.0],
            "close": [100.0, 110.0],
            "ret": [0.02, 0.05],
        }
    )
    following = next_session_rows(prices, ENDS)
    closes = holding_returns(labels, timing_factors(following, "next_close"), ENDS)
    assert closes["ret"].to_list() == pytest.approx([1.10 * 1.05 / 1.02 - 1.0])
    same = holding_returns(labels, timing_factors(following, "close"), ENDS)
    assert same["ret"].to_list() == pytest.approx([0.10])


def test_vwap_proxy_is_the_mean_of_the_four_prices() -> None:
    rows = pl.DataFrame(
        {
            "asof": [ENDS[0]],
            "security_id": ["a"],
            "open": [100.0],
            "high": [104.0],
            "low": [96.0],
            "close": [100.0],
            "ret": [0.0],
        }
    )
    factor = timing_factors(rows, "next_vwap_proxy")["factor"].to_list()
    assert factor == pytest.approx([1.0])


def test_crossing_interpolates_on_log_aum() -> None:
    found = crossing([1.0e7, 1.0e9], [0.10, 0.0], 0.05)
    assert found == pytest.approx(1.0e8)
    assert crossing([1.0e7, 1.0e9], [0.10, 0.08], 0.05) is None


@pytest.mark.parametrize(
    ("spread", "expected"),
    [(0.0, "estimated"), (0.5, "below grid min")],
)
def test_capacity_reports_where_it_stands(
    book: dict[str, pl.DataFrame], spread: float, expected: str
) -> None:
    def series(aum: float, drag: float) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "month": ENDS[1:] * 4,
                "ret": [0.01 - drag] * 12,
                "ret_gross": [0.01] * 12,
                "turnover": [0.5] * 12,
            }
        ).with_row_index("order")

    class Result:
        def __init__(self, frame: pl.DataFrame) -> None:
            self.series = frame

    def fake(config: SimulationConfig) -> Result:
        drag = 0.0 if config.aum == 0 else config.aum / 1.0e10 * 0.02 + spread / 12.0
        return Result(series(config.aum, drag))

    rng = np.random.default_rng(1)
    factors = pl.DataFrame(
        {
            "month": ENDS[1:] * 4,
            **{name: rng.normal(size=12) for name in ("mkt_rf", "smb", "hml", "rmw", "cma", "umd")},
        }
    )
    import efp.backtest.capacity as module

    fits: list[float] = []

    def fitted(frame: pl.DataFrame, _: pl.DataFrame, column: str = "ret") -> object:
        class Fit:
            alpha_annual = float(frame[column].mean()) * 12.0  # type: ignore[arg-type]

        fits.append(Fit.alpha_annual)
        return Fit

    original = module.attribute
    module.attribute = fitted  # type: ignore[assignment]
    try:
        result = capacity_curve(fake, factors, SimulationConfig(), CapacityConfig(points=5))  # type: ignore[arg-type]
    finally:
        module.attribute = original
    assert result.status == expected


def test_summary_row_annualises() -> None:
    series = pl.DataFrame(
        {
            "month": ENDS,
            "ret": [0.01, 0.02, 0.0, 0.01],
            "ret_gross": [0.02, 0.03, 0.01, 0.02],
            "turnover": [0.5] * 4,
            "spread_cost": [0.01] * 4,
            "impact_cost": [0.0] * 4,
            "borrow_cost": [0.0] * 4,
        }
    )

    class Result:
        neutrality = 0.0

    Result.series = series  # type: ignore[attr-defined]
    row = summary_row("x", "close", Result, 1.0e8)  # type: ignore[arg-type]
    assert row["net_mean_annual"] == pytest.approx(0.12)
    assert row["spread_cost_annual"] == pytest.approx(0.12)
    assert row["gross_sharpe"] > row["net_sharpe"] - 10


def test_replace_keeps_a_config_frozen() -> None:
    config = SimulationConfig()
    assert replace(config, aum=1.0).aum == 1.0
    assert config.aum == 1.0e8
