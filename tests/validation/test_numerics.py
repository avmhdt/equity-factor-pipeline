"""AC-W-09, NFR-04: evaluation numbers agree with reference implementations."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
import statsmodels.api as sm
from scipy import stats

from efp.evaluation.fama_macbeth import fama_macbeth, fama_macbeth_manual
from efp.evaluation.ic import default_lags, ic_summary, newey_west_t, rank_ic


def panel(seed: int = 3, months: int = 60, names: int = 300) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[pl.DataFrame] = []
    for month in range(months):
        first = rng.normal(size=names)
        second = 0.3 * first + rng.normal(size=names)
        target = 0.02 * first - 0.01 * second + rng.normal(0.0, 0.1, names)
        rows.append(
            pl.DataFrame(
                {
                    "asof": [date(2000, 1, 31) + timedelta(days=30 * month)] * names,
                    "security_id": [f"S{index:04d}" for index in range(names)],
                    "x1": first,
                    "x2": second,
                    "y": target,
                }
            )
        )
    return pl.concat(rows, how="vertical")


def test_rank_ic_equals_scipy_within_tolerance() -> None:
    frame = panel(months=5)
    signals = frame.select(
        "asof", "security_id", pl.lit("a").alias("signal"), pl.col("x1").alias("z")
    )
    fwd = frame.select(
        "asof", "security_id", pl.lit(1, dtype=pl.Int8).alias("h"), pl.col("y").alias("fwd_ret")
    )
    ic = rank_ic(signals, fwd).sort("asof")
    for row, (_, part) in zip(
        ic.iter_rows(named=True), frame.group_by(["asof"], maintain_order=True), strict=True
    ):
        expected = stats.spearmanr(part["x1"].to_numpy(), part["y"].to_numpy()).statistic
        assert row["ic"] == pytest.approx(expected, abs=1e-12)


def test_rank_ic_handles_ties_like_scipy() -> None:
    values = [1.0, 1.0, 2.0, 3.0, 3.0, 4.0, 5.0, 6.0]
    returns = [0.1, 0.2, 0.2, 0.3, -0.1, 0.0, 0.5, 0.4]
    day = date(2020, 1, 31)
    signals = pl.DataFrame(
        {
            "asof": [day] * 8,
            "security_id": list("abcdefgh"),
            "signal": ["s"] * 8,
            "z": values,
        }
    )
    fwd = pl.DataFrame(
        {
            "asof": [day] * 8,
            "security_id": list("abcdefgh"),
            "h": [1] * 8,
            "fwd_ret": returns,
        },
        schema_overrides={"h": pl.Int8},
    )
    expected = stats.spearmanr(values, returns).statistic
    assert rank_ic(signals, fwd)["ic"][0] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("lags", [0, 1, 4, 12])
def test_newey_west_t_equals_statsmodels_hac(lags: int) -> None:
    series = np.random.default_rng(1).normal(0.01, 0.05, 240)
    fitted = sm.OLS(series, np.ones(len(series))).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags, "use_correction": False}
    )
    assert newey_west_t(series, lags) == pytest.approx(float(fitted.tvalues[0]), abs=1e-8)


def test_default_lag_rule() -> None:
    assert default_lags(100) == 4
    assert default_lags(240) == 4
    assert default_lags(340) == 5


def test_ic_summary_fields() -> None:
    ic = pl.DataFrame(
        {
            "asof": [date(2020, month, 28) for month in range(1, 13)],
            "ic": [0.02, 0.05, -0.01, 0.03, 0.04, 0.0, 0.02, -0.02, 0.06, 0.01, 0.03, 0.02],
        }
    )
    summary = ic_summary(ic, nw_lags=1)
    series = ic["ic"].to_numpy()
    assert summary.mean_ic == pytest.approx(series.mean())
    assert summary.icir == pytest.approx(series.mean() / series.std(ddof=1))
    assert summary.icir_annual == pytest.approx(summary.icir * 12**0.5)
    assert summary.hit_rate == pytest.approx(9 / 12)
    assert summary.months == 12


@pytest.mark.parametrize("regressors", [["x1"], ["x1", "x2"]])
def test_manual_fama_macbeth_equals_linearmodels(regressors: list[str]) -> None:
    frame = panel()
    library = fama_macbeth(frame, "y", regressors)
    manual = fama_macbeth_manual(frame, "y", regressors)
    assert library.names == manual.names
    assert library.periods == manual.periods == 60
    assert library.lags == manual.lags == default_lags(60)
    assert library.coefficients == pytest.approx(manual.coefficients, abs=1e-8)
    assert library.t_stats == pytest.approx(manual.t_stats, abs=1e-8)


def test_fama_macbeth_recovers_known_slopes() -> None:
    result = fama_macbeth(panel(months=120), "y", ["x1", "x2"])
    assert result.coefficients[1] == pytest.approx(0.02, abs=0.002)
    assert result.coefficients[2] == pytest.approx(-0.01, abs=0.002)
    assert result.t_stats[1] > 3


def test_dates_too_small_to_identify_slopes_are_left_out() -> None:
    frame = panel(months=4)
    thin = frame.filter(
        pl.col("asof").ne(frame["asof"].min()) | pl.col("security_id").is_in(["S0000", "S0001"])
    )
    assert fama_macbeth_manual(thin, "y", ["x1", "x2"], nw_lags=1).periods == 3
