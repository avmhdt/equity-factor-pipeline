"""The book under published impact laws, beside the coefficient of 1.0 D-06 set.

The second round after the net failure, at the owner's request. Coefficient 1.0 stays the
headline. Nothing here feeds back into a composite or a signal, and nothing is fitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from efp.backtest.costs import SpreadModel
from efp.backtest.engine import SimulationConfig
from efp.backtest.impact_models import (
    PEAK_TO_AVERAGE,
    ImpactForm,
    almgren_cost,
    square_root_cost,
    zarinelli_cost,
)
from efp.backtest.portfolio import target_weights
from efp.backtest.run import BacktestConfig, Market

SPREADS: tuple[SpreadModel, ...] = ("corwin_schultz", "size_bucket")
CHECK_VOL = 0.02
RANGE_OVER_VOL = 1.6
"""The expected daily range of a random walk is about 1.6 times its deviation."""
CAP_OVER_ADV = 263.0
"""Almgren's IBM example."""
FRAZZINI_LINEAR_BPS = 1.30
FRAZZINI_ROOT_BPS = 6.03
"""Table V, U.S. column: bps per percent of daily volume, and per root of that percent. The table
gives no intercept, and its measure includes spread, so these are a check and never a model."""
CHECK_SIZES = (0.005, 0.01, 0.02, 0.05, 0.10)
FEW_PERCENT = 0.02
CAP_SHARE = 0.10
TOP_SHARE_RANK = 1000


@dataclass(frozen=True)
class ImpactLaw:
    """One way to price impact."""

    name: str
    form: ImpactForm
    coefficient: float


LAWS = (
    ImpactLaw("square_root_k1", "square_root", 1.0),
    ImpactLaw("square_root_average", "square_root", PEAK_TO_AVERAGE),
    ImpactLaw("almgren_2005", "almgren_2005", 1.0),
    ImpactLaw("zarinelli_2015", "zarinelli_2015", 1.0),
)


def simulation_for(law: ImpactLaw, config: BacktestConfig) -> SimulationConfig:
    """The uncapped run for one law."""
    return SimulationConfig(
        aum=config.aum,
        costs=config.costs,
        impact_form=law.form,
        impact_scale=law.coefficient,
    )


def cost_checks() -> pl.DataFrame:
    """Cost per dollar in bps at set trade sizes, for one mid-sized stock under each law."""
    vol = CHECK_VOL
    daily_range = vol * RANGE_OVER_VOL
    rows: list[dict[str, float]] = []
    for size in CHECK_SIZES:
        percent = size * 100.0
        rows.append(
            {
                "share_of_daily_volume": size,
                "square_root_k1": square_root_cost(size, vol) * 10_000.0,
                "square_root_average": square_root_cost(size, vol, PEAK_TO_AVERAGE) * 10_000.0,
                "almgren_2005": almgren_cost(size, vol, CAP_OVER_ADV) * 10_000.0,
                "zarinelli_large": zarinelli_cost(size, daily_range, 400) * 10_000.0,
                "zarinelli_small": zarinelli_cost(size, daily_range, 2000) * 10_000.0,
                "frazzini_size_terms": FRAZZINI_LINEAR_BPS * percent
                + FRAZZINI_ROOT_BPS * percent**0.5,
            }
        )
    return pl.DataFrame(rows)


def trade_profile(
    name: str, scores: pl.DataFrame, market: Market, config: BacktestConfig
) -> dict[str, object]:
    """Where a book's trades sit against the sources' range, before any cap and ignoring drift."""
    targets = target_weights(scores, config.construction, config.gross, config.quantiles)
    by_date = {
        key[0]: dict(zip(part["security_id"].to_list(), part["weight"].to_list(), strict=True))
        for key, part in targets.sort("asof").group_by(["asof"], maintain_order=True)
    }
    held: dict[str, float] = {}
    total = few = capped = above_day = large = 0.0
    dates: list[date] = sorted(by_date)
    for asof in dates:
        today = market.trading.get(asof, {})
        target = by_date[asof]
        for security in set(target) | set(held):
            size = abs(target.get(security, 0.0) - held.get(security, 0.0))
            row = today.get(security)
            if size == 0.0 or row is None or not row[2]:
                continue
            share = size * config.aum / float(row[2])
            total += size
            few += size if share <= FEW_PERCENT else 0.0
            capped += size if share <= CAP_SHARE else 0.0
            above_day += size if share > 1.0 else 0.0
            rank = row[5]
            large += size if rank is not None and rank <= TOP_SHARE_RANK else 0.0
        held = target
    return {
        "composite": name,
        "aum_usd": config.aum,
        "traded_within_2pct_adv": few / total,
        "traded_within_10pct_adv": capped / total,
        "traded_above_one_day_volume": above_day / total,
        "traded_in_top_1000": large / total,
    }
