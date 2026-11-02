"""Signal definitions on hand-computed values, through a real PitStore."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest
import yaml

import efp.signals  # noqa: F401
from efp.panel import PanelConfig, apply_coverage, build_panel, read_panel, write_panel
from efp.signals import common, investment, lowrisk, momentum, quality, size, value
from efp.signals.registry import registered, spec_of
from eqpit.calendar import month_ends, session_close, sessions
from eqpit.pit.aggregates import build_monthly_prices
from eqpit.pit.corpactions import build_corp_actions
from eqpit.pit.policy import PitPolicy
from eqpit.pit.prices import build_prices
from eqpit.schemas import (
    FUNDAMENTALS_PIT,
    MARKET_DAILY,
    PRICES_MONTHLY,
    SECURITIES,
    UNIVERSE_MONTHLY,
    conform,
)
from eqpit.store import PitStore

ROOT = Path(__file__).parents[2]
ASOF = date(2021, 6, 30)
OLD = datetime(2021, 5, 14, 21, tzinfo=UTC)
BEFORE = datetime(2020, 5, 14, 21, tzinfo=UTC)


def fact(
    company: str,
    item: str,
    end: date,
    kind: str,
    value_: float,
    available: datetime = OLD,
    period: str = "Q1",
) -> dict[str, object]:
    return {
        "company_id": company,
        "cik": 1,
        "item": item,
        "period_end": end,
        "fiscal_period": period,
        "period_type": kind,
        "value": value_,
        "unit": "USD",
        "accession": "a",
        "form": "10-Q",
        "available_at": available,
        "pit_method": "acceptance",
        "is_restatement": False,
        "source_tag": item,
    }


QUARTERS = [date(2020, 6, 30), date(2020, 9, 30), date(2020, 12, 31), date(2021, 3, 31)]


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> PitStore:
    """Two companies, 30 months of prices, four quarters of hand-set fundamentals."""
    root = tmp_path_factory.mktemp("signals") / "pit"
    days = sessions(date(2018, 12, 31), ASOF)
    rows = []
    for name, start, step in (("A", 10.0, 0.001), ("B", 20.0, -0.0005)):
        close = start
        for day in days:
            close *= 1.0 + step
            rows.append((name, day, close, 100.0))
    frame = pl.DataFrame(
        rows,
        schema={
            "security_id": pl.String,
            "date": pl.Date,
            "close": pl.Float64,
            "shares_out": pl.Float64,
        },
        orient="row",
    ).with_columns(
        pl.col("close").alias("open"),
        pl.col("close").alias("high"),
        pl.col("close").alias("low"),
        pl.lit(1000.0).alias("volume"),
    )
    actions = build_corp_actions(
        pl.DataFrame(
            schema={
                "security_id": pl.String,
                "ex_date": pl.Date,
                "action": pl.String,
                "value": pl.Float64,
                "new_ticker": pl.String,
            }
        )
    )
    prices = build_prices(frame, actions).with_columns(
        pl.when(pl.col("security_id").eq("A") & (pl.col("date") >= date(2021, 1, 4)))
        .then(pl.col("shares_out") * 1.1)
        .otherwise(pl.col("shares_out"))
        .alias("shares_out")
    )
    path = root / "prices_daily" / "year=2021"
    for year in (2018, 2019, 2020, 2021):
        target = root / "prices_daily" / f"year={year}"
        target.mkdir(parents=True)
        prices.filter(pl.col("date").dt.year().eq(year)).write_parquet(target / "part-0.parquet")
    assert path.is_dir()
    ends = month_ends(days[0], ASOF)
    monthly = build_monthly_prices(prices, ends)
    (root / "prices_monthly").mkdir()
    conform(monthly, PRICES_MONTHLY).write_parquet(root / "prices_monthly" / "part-0.parquet")
    (root / "market_daily").mkdir()
    market = pl.DataFrame(
        {"date": days, "ret": [0.001 * ((index % 7) - 3) for index in range(len(days))]}
    ).with_columns(
        pl.lit(2, dtype=pl.Int32).alias("names"),
        pl.Series(
            "available_at", [session_close(day) for day in days], dtype=pl.Datetime("ns", "UTC")
        ),
    )
    conform(market, MARKET_DAILY).write_parquet(root / "market_daily" / "part-0.parquet")
    universe_rows = []
    for end in ends:
        universe_rows.append(
            {
                "asof": end,
                "security_id": "A",
                "company_id": "A",
                "company_mcap": 2000.0,
                "mcap": 2000.0,
                "rank": 1,
                "exchange": "NYSE",
                "ff49": 1,
                "available_at": session_close(end),
            }
        )
        universe_rows.append(
            {
                "asof": end,
                "security_id": "B",
                "company_id": "B",
                "company_mcap": 1000.0,
                "mcap": 1000.0,
                "rank": 2,
                "exchange": "NYSE",
                "ff49": 2,
                "available_at": session_close(end),
            }
        )
    (root / "universe_monthly").mkdir()
    conform(pl.DataFrame(universe_rows), UNIVERSE_MONTHLY).write_parquet(
        root / "universe_monthly" / "part-0.parquet"
    )
    (root / "securities").mkdir()
    conform(
        pl.DataFrame(
            {
                "security_id": ["A", "B"],
                "company_id": ["A", "B"],
                "valid_from": [days[0], days[0]],
                "available_at": [datetime(2018, 1, 1, tzinfo=UTC)] * 2,
            }
        ),
        SECURITIES,
    ).write_parquet(root / "securities" / "part-0.parquet")
    facts: list[dict[str, object]] = []
    for company, scale in (("A", 1.0), ("B", 2.0)):
        for quarter, day in enumerate(QUARTERS):
            for item, amount in (
                ("revenue", 100.0),
                ("cogs", 60.0),
                ("income_before_extra", 10.0),
                ("depreciation", 5.0),
                ("operating_income", 20.0),
                ("eps_diluted", 1.0 + quarter * 0.1),
            ):
                facts.append(fact(company, item, day, "3m", amount * scale))
        for item, amount, lagged in (
            ("total_assets", 1000.0, 800.0),
            ("book_equity", 500.0, 400.0),
            ("stockholders_equity", 500.0, 400.0),
            ("cash", 100.0, 90.0),
            ("short_term_debt", 50.0, 40.0),
            ("long_term_debt", 150.0, 140.0),
            ("current_assets", 300.0, 250.0),
            ("current_liabilities", 200.0, 170.0),
            ("ppe_net", 300.0, 280.0),
            ("inventory", 60.0, 50.0),
        ):
            facts.append(fact(company, item, QUARTERS[3], "instant", amount * scale))
            facts.append(fact(company, item, date(2020, 12, 31), "instant", amount * scale, OLD))
            facts.append(fact(company, item, date(2020, 3, 31), "instant", lagged * scale, BEFORE))
    (root / "fundamentals_pit").mkdir()
    conform(pl.DataFrame(facts), FUNDAMENTALS_PIT).sort(
        "company_id", "item", "period_end", "period_type"
    ).write_parquet(root / "fundamentals_pit" / "part-0.parquet")
    return PitStore(root)


def values(frame: pl.DataFrame) -> dict[str, float]:
    return dict(zip(frame["security_id"].to_list(), frame["raw"].to_list(), strict=True))


def view(store: PitStore) -> PitStore:
    return store.for_signals(ASOF)


def test_registry_covers_every_family() -> None:
    ids = registered()
    assert len(ids) == 26
    counts: dict[str, int] = {}
    for name in ids:
        family = spec_of(name).family
        counts[family] = counts.get(family, 0) + 1
    for family in ("value", "momentum", "quality", "investment", "low_risk"):
        assert counts[family] >= 3
    assert counts["size"] == 1


def test_the_yaml_registry_matches_the_code() -> None:
    loaded = yaml.safe_load((ROOT / "configs" / "signals.yaml").read_text(encoding="utf-8"))
    assert set(loaded) == set(registered())
    for name, entry in loaded.items():
        spec = spec_of(name)
        assert entry["family"] == spec.family
        assert entry["direction"] == spec.direction
        assert tuple(entry["items"]) == spec.items
        assert entry["price_lookback_days"] == spec.price_lookback_days
        assert entry["reference"] == spec.reference
        assert entry["osap_target"] == spec.osap_target


def test_bm(store: PitStore) -> None:
    assert values(value.bm(view(store), ASOF)) == {"A": 0.25, "B": 1.0}


def test_ep(store: PitStore) -> None:
    assert values(value.ep(view(store), ASOF)) == {"A": 40.0 / 2000.0, "B": 80.0 / 1000.0}


def test_cfp(store: PitStore) -> None:
    assert values(value.cfp(view(store), ASOF))["A"] == pytest.approx(60.0 / 2000.0)


def test_sp(store: PitStore) -> None:
    assert values(value.sp(view(store), ASOF))["A"] == pytest.approx(400.0 / 2000.0)


def test_ebitda_ev(store: PitStore) -> None:
    assert values(value.ebitda_ev(view(store), ASOF))["A"] == pytest.approx(100.0 / 2100.0)


def test_gp_at(store: PitStore) -> None:
    assert values(quality.gp_at(view(store), ASOF))["A"] == pytest.approx(160.0 / 1000.0)


def test_roa_uses_the_prior_quarters_assets(store: PitStore) -> None:
    assert values(quality.roa(view(store), ASOF))["A"] == pytest.approx(10.0 / 1000.0)


def test_roe_uses_the_prior_quarters_book_equity(store: PitStore) -> None:
    assert values(quality.roe(view(store), ASOF))["A"] == pytest.approx(10.0 / 500.0)


def test_asset_growth(store: PitStore) -> None:
    assert values(investment.asset_growth(view(store), ASOF))["A"] == pytest.approx(0.25)


def test_inv_at_falls_back_on_net_ppe(store: PitStore) -> None:
    expected = (20.0 + 10.0) / 800.0
    assert values(investment.inv_at(view(store), ASOF))["A"] == pytest.approx(expected)


def test_noa(store: PitStore) -> None:
    expected = ((1000.0 - 100.0) - (1000.0 - 50.0 - 150.0 - 500.0)) / 800.0
    assert values(investment.noa(view(store), ASOF))["A"] == pytest.approx(expected)


def test_accruals(store: PitStore) -> None:
    expected = ((50.0 - 10.0) - (30.0 - 10.0) - 20.0) / 900.0
    assert values(quality.accruals(view(store), ASOF))["A"] == pytest.approx(expected)


def test_nsi(store: PitStore) -> None:
    result = values(investment.nsi(view(store), ASOF))
    assert result["A"] == pytest.approx(0.09531018, abs=1e-6)
    assert result["B"] == pytest.approx(0.0, abs=1e-12)


def test_size_is_log_market_equity(store: PitStore) -> None:
    assert values(size.size(view(store), ASOF))["A"] == pytest.approx(7.600902459)


def test_str_1m_is_the_latest_month(store: PitStore) -> None:
    sessions_in_june = len(sessions(date(2021, 6, 1), ASOF))
    expected = 1.001**sessions_in_june - 1.0
    assert values(momentum.str_1m(view(store), ASOF))["A"] == pytest.approx(expected)


def test_mom_12_1_skips_the_latest_month(store: PitStore) -> None:
    days = len(sessions(date(2020, 7, 1), date(2021, 5, 31)))
    assert values(momentum.mom_12_1(view(store), ASOF))["A"] == pytest.approx(1.001**days - 1.0)


def test_mom_6_1(store: PitStore) -> None:
    days = len(sessions(date(2021, 1, 1), date(2021, 5, 31)))
    assert values(momentum.mom_6_1(view(store), ASOF))["A"] == pytest.approx(1.001**days - 1.0)


def test_ltr_needs_its_whole_window(store: PitStore) -> None:
    days = len(sessions(date(2019, 1, 1), date(2020, 6, 30)))
    frame = momentum.ltr_36_13(view(store), ASOF)
    assert frame.is_empty() or days > 0


def test_high_52w(store: PitStore) -> None:
    result = values(momentum.high_52w(view(store), ASOF))
    assert result["A"] == pytest.approx(1.0)
    assert result["B"] == pytest.approx(0.9995**251, rel=1e-9)


def test_industry_momentum_is_the_stocks_own_industry(store: PitStore) -> None:
    result = values(momentum.ind_mom(view(store), ASOF))
    days = len(sessions(date(2021, 1, 1), ASOF))
    assert result["A"] == pytest.approx(1.001**days - 1.0)


def test_sue_needs_a_history(store: PitStore) -> None:
    assert momentum.sue(view(store), ASOF).is_empty()


def test_fscore_needs_two_fiscal_years(store: PitStore) -> None:
    assert quality.fscore(view(store), ASOF).is_empty()


def test_beta_of_constant_returns_is_zero(store: PitStore) -> None:
    result = values(lowrisk.beta(view(store), ASOF))
    assert result["A"] == pytest.approx(0.0, abs=1e-9)


def test_vol_of_constant_returns_is_zero(store: PitStore) -> None:
    assert values(lowrisk.vol_12m(view(store), ASOF))["A"] == pytest.approx(0.0, abs=1e-9)


def test_ivol_of_constant_returns_is_zero(store: PitStore) -> None:
    assert values(lowrisk.ivol(view(store), ASOF))["A"] == pytest.approx(0.0, abs=1e-7)


def test_max_ret(store: PitStore) -> None:
    assert values(lowrisk.max_ret(view(store), ASOF))["A"] == pytest.approx(0.001)


def test_stale_fundamentals_are_ignored(store: PitStore) -> None:
    later = date(2021, 6, 30)
    assert common.latest(store.for_signals(later), later, ["total_assets"]).height == 2


def test_ttm_needs_four_consecutive_quarters(store: PitStore) -> None:
    earlier = date(2020, 12, 31)
    frame = common.ttm(store.for_signals(earlier), earlier, ["revenue"])
    assert frame.is_empty()


def test_coverage_drops_thin_dates() -> None:
    day = date(2020, 1, 31)
    raw = pl.DataFrame(
        {
            "signal": ["a"] * 60 + ["b"] * 150,
            "asof": [day] * 210,
            "security_id": [f"S{index}" for index in range(60)]
            + [f"S{index}" for index in range(150)],
            "raw": [1.0] * 210,
        }
    )
    universe = pl.DataFrame(
        {"asof": [day] * 200, "security_id": [f"S{index}" for index in range(200)]}
    )
    kept, dropped = apply_coverage(raw, universe, PanelConfig())
    assert kept["signal"].unique().to_list() == ["b"]
    assert dropped["signal"].to_list() == ["a"]


def test_panel_round_trip_on_the_synthetic_market(small_store: PitStore, tmp_path: Path) -> None:
    dates = small_store.month_ends(date(2014, 1, 1), date(2014, 6, 30))
    panel, _ = build_panel(
        small_store, ["size", "mom_6_1", "beta"], dates, PanelConfig(min_names=50)
    )
    assert {"raw", "winsorized", "z", "z_neutral"} <= set(panel.columns)
    written = tmp_path / "signals"
    write_panel(panel, written)
    write_panel(panel, written)
    assert read_panel(written).equals(panel)
    assert read_panel(written, ["size"])["signal"].unique().to_list() == ["size"]
    momentum_rows = panel.filter(pl.col("signal").eq("mom_6_1"), pl.col("asof").eq(dates[-1]))
    assert abs(float(momentum_rows["z"].mean())) < 1e-10
    assert abs(float(momentum_rows["z"].std()) - 1.0) < 1e-10
    size_rows = panel.filter(pl.col("signal").eq("size"), pl.col("asof").eq(dates[-1]))
    assert size_rows.select(pl.corr("raw", "z")).item() < 0


def test_policy_default_is_unchanged() -> None:
    assert PitPolicy().extra_lag_trading_days == 1
