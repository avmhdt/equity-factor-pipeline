"""AC-H-01, AC-H-02, AC-H-03 and AC-H-08 on the licensed tables, read from the committed results."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

pytestmark = pytest.mark.data

TABLES = Path(__file__).parents[2] / "results" / "tables"
MIN_OVERLAP_MONTHS = 240
HEADLINE_REPLICATED = 20


def _read(name: str) -> pl.DataFrame:
    path = TABLES / f"{name}.csv"
    if not path.is_file():
        pytest.skip(f"run efp validate first: {name}")
    return pl.read_csv(path)


def test_twenty_anomalies_replicate() -> None:
    table = _read("osap_replication")
    passed = table.filter(pl.col("passed"))
    assert passed.height >= HEADLINE_REPLICATED
    assert passed["correlation"].min() >= 0.70  # type: ignore[operator]
    assert passed["same_sign"].all()


def test_a_pass_rests_on_a_long_overlap() -> None:
    table = _read("osap_replication").filter(pl.col("passed"))
    assert table["months"].min() >= MIN_OVERLAP_MONTHS  # type: ignore[operator]


def test_every_failure_has_a_reason() -> None:
    failures = _read("osap_replication").filter(~pl.col("passed"))
    assert failures["reason"].str.contains("likely cause").all()


def test_decay_sits_beside_the_paper() -> None:
    samples = _read("post_publication")["sample"].to_list()
    assert samples[0] == "McLean and Pontiff (2016)"
    assert "OSAP published series" in samples


def test_every_trial_has_the_hurdle_flags() -> None:
    table = _read("multiple_testing")
    assert {"t_nw", "p_bhy", "t_over_2", "t_over_3"} <= set(table.columns)
    assert table.height > 0


def test_value_gap_walks_from_hml_to_ours() -> None:
    steps = _read("value_gap_decomposition")["step"].to_list()
    assert steps[0] == "hml_ff_convention"
    assert steps[-1] == "neutralized"
