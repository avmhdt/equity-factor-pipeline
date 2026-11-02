"""AC-W-08: neutralized scores are orthogonal to every exposure, per date."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from efp.signals.neutralize import neutralize


def cross_section(seed: int, names: int = 300, dates: int = 2) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    frames: list[pl.DataFrame] = []
    for index in range(dates):
        size = rng.normal(20.0, 2.0, names)
        beta = rng.normal(1.0, 0.4, names)
        sector = rng.integers(1, 12, names)
        score = 0.5 * size + beta - 0.1 * sector + rng.normal(size=names)
        frames.append(
            pl.DataFrame(
                {
                    "asof": [date(2020, 1 + index, 28)] * names,
                    "z": score,
                    "ff49": sector.astype(np.int16),
                    "log_size": size,
                    "beta": beta,
                }
            )
        )
    return pl.concat(frames, how="vertical")


@settings(max_examples=30, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_residuals_are_orthogonal_to_every_exposure(seed: int) -> None:
    frame = neutralize(cross_section(seed), "z", "sector_size_beta")
    for _, part in frame.group_by(["asof"], maintain_order=True):
        values = part["z_neutral"].to_numpy()
        assert abs(values.mean()) < 1e-8
        assert abs(values.std(ddof=1) - 1.0) < 1e-8
        assert abs(np.corrcoef(values, part["log_size"].to_numpy())[0, 1]) < 1e-8
        assert abs(np.corrcoef(values, part["beta"].to_numpy())[0, 1]) < 1e-8
        for sector in part["ff49"].unique().to_list():
            dummy = (part["ff49"].to_numpy() == sector).astype(np.float64)
            assert abs(np.corrcoef(values, dummy)[0, 1]) < 1e-8


def test_sector_mode_leaves_size_in() -> None:
    frame = neutralize(cross_section(1), "z", "sector")
    part = frame.filter(pl.col("asof").eq(date(2020, 1, 28)))
    assert abs(np.corrcoef(part["z_neutral"].to_numpy(), part["log_size"].to_numpy())[0, 1]) > 0.3


def test_none_copies_the_score() -> None:
    frame = cross_section(2)
    assert neutralize(frame, "z", "none")["z_neutral"].to_list() == frame["z"].to_list()


def test_the_size_signal_keeps_its_size_exposure() -> None:
    frame = cross_section(3).with_columns(pl.col("log_size").alias("z"))
    part = neutralize(frame, "z", "sector_size_beta", exempt_size=True)
    assert abs(np.corrcoef(part["z_neutral"].to_numpy(), part["log_size"].to_numpy())[0, 1]) > 0.9


def test_a_missing_exposure_gives_a_null_on_that_row_only() -> None:
    frame = cross_section(4, dates=1).with_columns(
        pl.when(pl.int_range(pl.len()).eq(0)).then(None).otherwise(pl.col("beta")).alias("beta")
    )
    result = neutralize(frame, "z", "sector_size_beta")["z_neutral"]
    assert result[0] is None
    assert result.null_count() == 1


def test_a_date_too_thin_for_the_regression_stays_null() -> None:
    frame = cross_section(5, names=20, dates=1).head(5)
    assert neutralize(frame, "z", "sector_size_beta")["z_neutral"].null_count() == 5


def test_row_order_is_kept() -> None:
    frame = cross_section(6).with_row_index("order")
    result = neutralize(frame, "z", "sector_size_beta")
    assert result["order"].to_list() == frame["order"].to_list()
