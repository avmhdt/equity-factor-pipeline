"""AC-W-05, DQ-08: delisting returns and their imputation."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from market_fixture import delistings, ends, prices

from eqpit.pit.delisting import build_delistings
from eqpit.pit.labels import build_forward_returns, monthly_returns
from eqpit.pit.policy import PitPolicy


def one(reason: str | None, exchange: str, raw: float | None, code: int | None = None) -> dict:
    frame = pl.DataFrame(
        {
            "security_id": ["A"],
            "delist_date": [date(2024, 2, 14)],
            "delist_code": [code],
            "reason": [reason],
            "exchange_at_delist": [exchange],
            "dlret_raw": [raw],
        },
        schema_overrides={
            "delist_code": pl.Int16,
            "reason": pl.String,
            "dlret_raw": pl.Float64,
        },
    )
    return build_delistings(frame, PitPolicy()).to_dicts()[0]


@pytest.mark.parametrize(
    ("reason", "exchange", "dlret", "rule"),
    [
        ("performance", "NYSE", -0.30, "shumway_nyse_amex"),
        ("performance", "AMEX", -0.30, "shumway_nyse_amex"),
        ("performance", "NASDAQ", -0.55, "shumway_nasdaq"),
        ("liquidation", "NASDAQ", -0.55, "shumway_nasdaq"),
        ("merger", "NYSE", 0.0, "zero"),
        ("exchange", "NASDAQ", 0.0, "zero"),
        ("unknown", "NASDAQ", 0.0, "zero"),
    ],
)
def test_imputation_rules(reason: str, exchange: str, dlret: float, rule: str) -> None:
    row = one(reason, exchange, None)
    assert row["dlret"] == dlret
    assert row["imputation_rule"] == rule


def test_a_vendor_return_is_kept() -> None:
    row = one("performance", "NASDAQ", -0.12)
    assert row["dlret"] == -0.12
    assert row["imputation_rule"] == "none"


@pytest.mark.parametrize("code", [500, 520, 574, 584])
def test_crsp_performance_codes(code: int) -> None:
    assert one("unknown", "NASDAQ", None, code)["imputation_rule"] == "shumway_nasdaq"


@pytest.mark.parametrize("code", [100, 233, 519, 585])
def test_other_crsp_codes(code: int) -> None:
    assert one("unknown", "NASDAQ", None, code)["imputation_rule"] == "zero"


def test_zero_imputation_sensitivity() -> None:
    built = delistings(PitPolicy(zero_imputation=True)).sort("security_id")
    assert built["dlret"].to_list() == [0.0, 0.25]
    assert built["imputation_rule"].to_list() == ["zero", "none"]


def test_unknown_reason_fails() -> None:
    with pytest.raises(ValueError, match="normalised set"):
        one("bankrupt", "NYSE", None)


def test_the_delisting_month_compounds_both_returns() -> None:
    returns = monthly_returns(prices(), delistings(), ends())
    row = returns.filter(pl.col("security_id").eq("SYN:8"), pl.col("month").eq(date(2024, 2, 29)))
    days = 10
    assert row["ret"][0] == pytest.approx(1.001**days * 0.70 - 1.0, rel=1e-5)


def test_without_delisting_the_month_has_only_the_partial_return() -> None:
    returns = monthly_returns(prices(), delistings(), ends(), include_delisting=False)
    row = returns.filter(pl.col("security_id").eq("SYN:8"), pl.col("month").eq(date(2024, 2, 29)))
    assert row["ret"][0] == pytest.approx(1.001**10 - 1.0, rel=1e-5)


def test_a_vendor_delisting_return_compounds() -> None:
    returns = monthly_returns(prices(), delistings(), ends())
    row = returns.filter(pl.col("security_id").eq("SYN:9"), pl.col("month").eq(date(2024, 2, 29)))
    assert row["ret"][0] == pytest.approx(1.001**13 * 1.25 - 1.0, rel=1e-5)


def test_a_delisted_name_stays_in_forward_returns() -> None:
    forward = build_forward_returns(prices(), delistings(), ends(), (1,))
    row = forward.filter(pl.col("security_id").eq("SYN:8"), pl.col("asof").eq(date(2024, 1, 31)))
    assert row.height == 1
    assert row["fwd_ret"][0] == pytest.approx(1.001**10 * 0.70 - 1.0, rel=1e-5)


def test_a_delisting_on_a_month_end_falls_in_the_next_month() -> None:
    frame = delistings().with_columns(pl.lit(date(2024, 1, 31)).alias("delist_date"))
    only = frame.filter(pl.col("security_id").eq("SYN:8"))
    series = prices().filter(pl.col("security_id").eq("SYN:8"), pl.col("date") <= date(2024, 1, 31))
    extra = series.tail(1).with_columns(pl.lit(date(2024, 2, 29)).alias("date"))
    returns = monthly_returns(pl.concat([series, extra]), only, ends())
    february = returns.filter(pl.col("month").eq(date(2024, 2, 29)))
    assert february["ret"][0] == pytest.approx((1.0 + float(extra["ret"][0])) * 0.70 - 1.0)


def test_a_horizon_longer_than_the_sample_gives_no_rows() -> None:
    assert build_forward_returns(prices(), delistings(), ends(), (24,)).is_empty()


def test_excess_returns_subtract_compounded_risk_free() -> None:
    risk_free = pl.DataFrame({"month": [date(2024, 1, 31), date(2024, 2, 29)], "rf": [0.01, 0.02]})
    forward = build_forward_returns(prices(), delistings(), ends(), (1,), risk_free)
    row = forward.filter(pl.col("security_id").eq("SYN:0"))
    assert row["fwd_ret_excess"][0] == pytest.approx(row["fwd_ret"][0] - 0.02)
