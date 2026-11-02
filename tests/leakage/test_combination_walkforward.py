"""AC-W-10: composites at T use nothing from a return that ends after T."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from efp.combine.ic_weighted import trailing_weights
from efp.combine.walkforward import CombineConfig, combine, variant_composites
from efp.evaluation.ic import rank_ic

MONTHS = pl.date_range(date(2005, 1, 31), date(2015, 12, 31), "1mo", eager=True).to_list()
NAMES = [f"s{index:02d}" for index in range(40)]
SIGNALS = ("a", "b", "c")
CONFIG = CombineConfig(min_train_months=24, refit_months=6, ic_window_months=12)
CHECK = MONTHS[60]


@pytest.fixture(scope="module")
def market() -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(5)
    rows = len(MONTHS) * len(NAMES)
    base = pl.DataFrame(
        {
            "asof": np.repeat(np.array(MONTHS, dtype="datetime64[D]"), len(NAMES)),
            "security_id": NAMES * len(MONTHS),
        }
    ).with_columns(pl.col("asof").cast(pl.Date))
    scores = {name: rng.normal(size=rows) for name in SIGNALS}
    panel = pl.concat(
        [
            base.with_columns(pl.lit(name).alias("signal"), pl.Series("z", values))
            for name, values in scores.items()
        ],
        how="vertical",
    )
    noise = rng.normal(0.0, 0.05, rows)
    fwd = base.with_columns(
        pl.Series("fwd_ret", 0.01 * scores["a"] - 0.005 * scores["b"] + noise),
        pl.lit(1, dtype=pl.Int8).alias("h"),
    )
    return panel, fwd


def scrambled_after(fwd: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """Every return whose period ends after ``cutoff`` becomes garbage."""
    rng = np.random.default_rng(9)
    late = fwd["asof"] >= cutoff
    garbage = pl.Series(rng.normal(0.0, 5.0, fwd.height))
    return fwd.with_columns(
        pl.when(late).then(garbage).otherwise(pl.col("fwd_ret")).alias("fwd_ret")
    )


@pytest.mark.parametrize("method", ["equal", "ic_weighted", "ridge"])
def test_later_returns_leave_scores_at_t_unchanged(
    market: tuple[pl.DataFrame, pl.DataFrame], method: str
) -> None:
    panel, fwd = market
    before = combine(panel, fwd, method, CONFIG)  # type: ignore[arg-type]
    after = combine(panel, scrambled_after(fwd, CHECK), method, CONFIG)  # type: ignore[arg-type]
    upto = pl.col("asof") <= CHECK
    assert before.filter(upto).height > 0
    assert before.filter(upto).equals(after.filter(upto))


@pytest.mark.parametrize("method", ["ic_weighted", "ridge"])
def test_the_scramble_does_reach_later_scores(
    market: tuple[pl.DataFrame, pl.DataFrame], method: str
) -> None:
    panel, fwd = market
    before = combine(panel, fwd, method, CONFIG)  # type: ignore[arg-type]
    after = combine(panel, scrambled_after(fwd, CHECK), method, CONFIG)  # type: ignore[arg-type]
    later = pl.col("asof") > MONTHS[80]
    assert not before.filter(later).equals(after.filter(later))


def test_ridge_trains_through_t_minus_the_purge(
    market: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    panel, fwd = market
    config = CombineConfig(min_train_months=24, refit_months=6, purge_months=1, diagnostics={})
    combine(panel, fwd, "ridge", config)
    fits = config.diagnostics["fits"]
    assert len(fits) > 5  # type: ignore[arg-type]
    position = {month: index for index, month in enumerate(MONTHS)}
    for fit in fits:  # type: ignore[attr-defined]
        assert position[fit.trained_through] <= position[fit.asof] - config.purge_months


def test_ic_weights_at_t_use_ics_up_to_the_purge(
    market: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    panel, fwd = market
    ic = rank_ic(panel, fwd, 1, "z")
    index = 60
    weights = trailing_weights(ic, list(SIGNALS), MONTHS, 12, 1)
    window = MONTHS[index - 12 : index]
    means = (
        ic.filter(pl.col("asof").is_in(window))
        .group_by("signal")
        .agg(pl.col("ic").mean())
        .with_columns(pl.col("ic").clip(0.0))
    )
    total = means["ic"].sum()
    for row in means.iter_rows(named=True):
        got = weights.filter(pl.col("asof").eq(MONTHS[index]), pl.col("signal").eq(row["signal"]))[
            "weight"
        ].item()
        assert got == pytest.approx(row["ic"] / total)


def test_ridge_recovers_the_planted_signs(market: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    panel, fwd = market
    config = CombineConfig(min_train_months=24, refit_months=6, diagnostics={})
    combine(panel, fwd, "ridge", config)
    last = config.diagnostics["fits"][-1]  # type: ignore[index]
    assert last.coefficients[0] > 0 > last.coefficients[1]


def test_a_variant_keeps_only_the_largest_names(
    market: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    panel, fwd = market
    ranks = panel.filter(pl.col("signal").eq("a")).select(
        "asof", "security_id", (pl.col("security_id").str.slice(1).cast(pl.Int32) + 1).alias("rank")
    )
    built = variant_composites(panel, fwd, ranks, [10], "equal", "none", CombineConfig())
    scores = built["equal_none_top10"]
    assert scores.group_by("asof").len()["len"].max() == 10


def test_equal_weight_averages_what_is_there() -> None:
    panel = pl.DataFrame(
        {
            "asof": [MONTHS[0]] * 3,
            "security_id": ["x", "x", "y"],
            "signal": ["a", "b", "a"],
            "z": [1.0, 3.0, 5.0],
        }
    )
    fwd = pl.DataFrame(schema={"asof": pl.Date, "security_id": pl.String})
    scores = combine(panel, fwd, "equal", CombineConfig())
    assert scores["score"].to_list() == [2.0, 5.0]
