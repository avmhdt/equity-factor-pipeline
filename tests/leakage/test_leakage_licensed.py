"""Leakage checks on the licensed tables: ``pytest -m "leakage and data"``.

Rebuilding all tables from corrupted raw files is proved on the synthetic market. Here later rows
are corrupted in the point-in-time tables, which tests the gate on real data at full size.
Working copies stay under the data root, because licensed data may not reach C:.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl
import probes
import pytest

from efp.leakage import assert_identical, assert_no_feature_after_cutoff
from efp.settings import get_settings
from efp.signals.registry import SignalSpec, compute_signals, register, unregister
from eqpit.calendar import local_date
from eqpit.pit.build import read_pit_manifest
from eqpit.store import PitStore

pytestmark = [pytest.mark.leakage, pytest.mark.data]

BOUNDARY = date(2015, 6, 30)
DATES = [date(2005, 6, 30), date(2015, 3, 31), BOUNDARY]
PROBES = {"lic_mom": probes.mom_probe, "lic_high": probes.high_probe, "lic_gp": probes.gp_probe}


def _licensed_root() -> Path:
    return get_settings().data_dir / "processed" / "pit"


@pytest.fixture(scope="module")
def store() -> PitStore:
    root = _licensed_root()
    if not (root / "manifest.json").is_file() or read_pit_manifest(root)["provider"] != "sharadar":
        pytest.skip("no Sharadar build in the data directory")
    return PitStore.open(root)


@pytest.fixture(scope="module", autouse=True)
def registered() -> Iterator[None]:
    for name, fn in PROBES.items():
        register(SignalSpec(name, "momentum", 1, (), 63, "test", None))(fn)
    yield
    for name in PROBES:
        unregister(name)


@pytest.fixture(scope="module")
def baseline(store: PitStore) -> pl.DataFrame:
    return compute_signals(store, list(PROBES), DATES)


def test_no_feature_after_cutoff(baseline: pl.DataFrame) -> None:
    assert baseline.height > 3000
    assert_no_feature_after_cutoff(baseline)


def _corrupt(store: PitStore, target: Path) -> None:
    cutoff = store.cutoff(BOUNDARY)
    for table, columns in (
        ("prices_daily", ["close", "ret", "mcap", "shares_out", "split_factor"]),
        ("fundamentals_pit", ["value"]),
        ("universe_monthly", ["mcap", "company_mcap"]),
    ):
        for source in sorted((store.root / table).rglob("*.parquet")):
            path = target / source.relative_to(store.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame = pl.read_parquet(source)
            late = pl.col("available_at") > cutoff
            frame.with_columns(
                pl.when(late).then(pl.col(name) * 3.7).otherwise(pl.col(name)).alias(name)
                for name in columns
            ).write_parquet(path, compression="zstd")
    for table in ("securities", "delistings", "corp_actions", "filings"):
        shutil.copytree(store.root / table, target / table)


def test_perturbation_invariance(store: PitStore, baseline: pl.DataFrame) -> None:
    target = store.root.parent.parent / "interim" / "leakage_check"
    shutil.rmtree(target, ignore_errors=True)
    try:
        _corrupt(store, target)
        assert_identical(baseline, compute_signals(PitStore(target), list(PROBES), DATES))
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_fundamentals_never_before_acceptance(store: PitStore) -> None:
    root = store.root
    fundamentals = pl.scan_parquet(root / "fundamentals_pit" / "**" / "*.parquet")
    filings = pl.scan_parquet(root / "filings" / "**" / "*.parquet").select(
        "accession", "acceptance_dt"
    )
    early = (
        fundamentals.filter(pl.col("pit_method").eq("acceptance"))
        .join(filings, on="accession")
        .filter(pl.col("available_at") < pl.col("acceptance_dt"))
        .select(pl.len())
        .collect()
        .item()
    )
    assert early == 0
    before_end = (
        fundamentals.filter(local_date(pl.col("available_at")) <= pl.col("period_end"))
        .select(pl.len())
        .collect()
        .item()
    )
    assert before_end == 0
