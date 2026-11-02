"""FR-13, AC-W-09 groundwork: planted premia come back through the PIT tables."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from efp.evaluation.ic import rank_ic
from efp.signals.registry import compute_signals
from eqpit.pit.build import build_pit
from eqpit.providers.synthetic import SyntheticProvider, write_market
from eqpit.store import PitStore
from eqpit.synthetic import PLANTED_SIGNALS, SyntheticConfig

TARGET = 0.05
TOLERANCE = 0.01
MIN_NAMES = 100
SIGNS = {"gp_at": 1.0, "asset_growth": -1.0, "mom_12_1": 1.0}


@pytest.fixture(scope="module")
def full_market(tmp_path_factory: pytest.TempPathFactory) -> tuple[SyntheticProvider, PitStore]:
    root = tmp_path_factory.mktemp("full")
    write_market(SyntheticConfig(seed=7, target_ic=TARGET), root / "raw")
    provider = SyntheticProvider(root / "raw")
    build_pit(provider, root / "pit")
    return provider, PitStore.open(root / "pit")


def mean_ic(truth: pl.DataFrame, labels: pl.DataFrame, signal: str) -> tuple[float, int]:
    joined = (
        truth.with_columns(pl.col(signal).fill_nan(None))
        .drop_nulls(signal)
        .join(labels, on=["asof", "security_id"])
    )
    monthly = (
        joined.group_by("asof", maintain_order=True)
        .agg(
            pl.corr(pl.col(signal).rank(), pl.col("fwd_ret").rank()).alias("ic"),
            pl.len().alias("names"),
        )
        .filter(pl.col("names") >= MIN_NAMES)
    )
    return float(monthly["ic"].mean()), monthly.height


@pytest.mark.parametrize("signal", PLANTED_SIGNALS)
def test_planted_ic_is_recovered(
    full_market: tuple[SyntheticProvider, PitStore], signal: str
) -> None:
    provider, store = full_market
    truth = pl.read_parquet(provider.root / "truth" / "**" / "*.parquet")
    labels = store.labels().forward_returns((1,)).select("asof", "security_id", "fwd_ret")
    measured, months = mean_ic(truth, labels, signal)
    assert months >= 220
    assert abs(measured - SIGNS[signal] * TARGET) <= TOLERANCE


@pytest.fixture(scope="module")
def signal_panel(full_market: tuple[SyntheticProvider, PitStore]) -> pl.DataFrame:
    _, store = full_market
    dates = store.month_ends(date(2003, 1, 1), date(2020, 11, 30))
    return compute_signals(store, list(PLANTED_SIGNALS), dates)


@pytest.mark.parametrize("signal", PLANTED_SIGNALS)
def test_the_signal_library_recovers_the_planted_ic(
    full_market: tuple[SyntheticProvider, PitStore], signal_panel: pl.DataFrame, signal: str
) -> None:
    """AC-W-09: the real signal code, through PitStore, finds what the generator planted."""
    _, store = full_market
    fwd = store.labels().forward_returns((1,))
    ic = rank_ic(signal_panel.filter(pl.col("signal").eq(signal)), fwd, 1, "raw")
    assert ic.height >= 200
    assert abs(float(ic["ic"].mean()) - SIGNS[signal] * TARGET) <= TOLERANCE


@pytest.mark.parametrize("signal", PLANTED_SIGNALS)
def test_signal_values_match_the_generators_truth(
    full_market: tuple[SyntheticProvider, PitStore], signal_panel: pl.DataFrame, signal: str
) -> None:
    provider, _ = full_market
    truth = pl.read_parquet(provider.root / "truth" / "**" / "*.parquet").select(
        "asof", "security_id", pl.col(signal).fill_nan(None).alias("truth")
    )
    joined = (
        signal_panel.filter(pl.col("signal").eq(signal))
        .join(truth, on=["asof", "security_id"], how="inner")
        .drop_nulls(["raw", "truth"])
    )
    assert joined.height > 50_000
    assert joined.select(pl.corr(pl.col("raw").rank(), pl.col("truth").rank())).item() > 0.97


def test_the_market_meets_the_size_requirement(
    full_market: tuple[SyntheticProvider, PitStore],
) -> None:
    _, store = full_market
    root = Path(store.root)
    securities = pl.read_parquet(root / "securities" / "**" / "*.parquet")
    delistings = pl.read_parquet(root / "delistings" / "**" / "*.parquet")
    actions = pl.read_parquet(root / "corp_actions" / "**" / "*.parquet")
    filings = pl.read_parquet(root / "filings" / "**" / "*.parquet")
    fundamentals = pl.read_parquet(root / "fundamentals_pit" / "**" / "*.parquet")
    prices = pl.read_parquet(root / "prices_daily" / "**" / "*.parquet")
    assert securities["security_id"].n_unique() >= 500
    assert prices["date"].dt.year().n_unique() >= 20
    assert set(delistings["reason"].unique().to_list()) == {
        "merger",
        "exchange",
        "liquidation",
        "performance",
        "unknown",
    }
    assert {"split", "cash_dividend", "ticker_change", "stock_dividend", "spinoff"} <= set(
        actions["action"].unique().to_list()
    )
    after_hours = filings.with_columns(
        pl.col("acceptance_dt").dt.convert_time_zone("America/New_York").dt.hour().alias("hour")
    ).filter(pl.col("hour") >= 16)
    assert after_hours.height > 1000
    assert fundamentals["is_restatement"].sum() > 100
    assert securities.filter(pl.col("valid_to").is_not_null()).height > 10


def test_every_year_with_delistings_has_later_delisted_names_in_the_universe(
    full_market: tuple[SyntheticProvider, PitStore],
) -> None:
    _, store = full_market
    universe = pl.read_parquet(store.root / "universe_monthly" / "**" / "*.parquet")
    delistings = pl.read_parquet(store.root / "delistings" / "**" / "*.parquet")
    years = universe.filter(pl.col("delisted_after").is_not_null())["asof"].dt.year().unique()
    wanted = delistings["delist_date"].dt.year().unique() - 1
    assert set(wanted.filter(wanted >= 2001).to_list()) <= set(years.to_list())
