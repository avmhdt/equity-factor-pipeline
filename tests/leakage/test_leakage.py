"""FR-12, AC-W-02: the leakage test. CI's ``leakage`` job is a required check on main."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import probes
import pytest

from efp.leakage import (
    LeakageError,
    assert_identical,
    assert_no_feature_after_cutoff,
    import_violations,
    late_rows,
)
from efp.signals import registry
from efp.signals.registry import SignalSpec, compute_signals, register, unregister
from eqpit.calendar import local_date, session_close
from eqpit.pit.build import build_pit
from eqpit.pit.filings import stamp_availability
from eqpit.pit.policy import PitPolicy
from eqpit.providers.synthetic import SyntheticProvider
from eqpit.store import PitStore

pytestmark = pytest.mark.leakage

BOUNDARY = date(2014, 6, 30)
DATES = [date(2013, 12, 31), date(2014, 3, 31), BOUNDARY]
SIGNALS_DIR = Path(registry.__file__).parent
PROBES = {"p_mom": probes.mom_probe, "p_high": probes.high_probe, "p_gp": probes.gp_probe}
CANARIES = {"canary_honest": probes.canary_honest, "canary_liar": probes.canary_liar}


def spec(signal_id: str) -> SignalSpec:
    return SignalSpec(signal_id, "momentum", 1, (), 63, "test", None)


@pytest.fixture(scope="module", autouse=True)
def registered() -> Iterator[None]:
    for name, fn in {**PROBES, **CANARIES}.items():
        register(spec(name))(fn)
    yield
    for name in {**PROBES, **CANARIES}:
        unregister(name)


class CorruptedProvider(SyntheticProvider):
    """Every raw row not yet public at ``cutoff`` is garbage; everything else is untouched."""

    def __init__(self, base: SyntheticProvider, boundary: date) -> None:
        super().__init__(base.root)
        self.boundary = boundary
        self.cutoff = session_close(boundary)
        self.noise = np.random.default_rng(99)

    def _scramble(self, frame: pl.DataFrame, late: pl.Series, columns: list[str]) -> pl.DataFrame:
        factors = pl.Series(self.noise.uniform(0.2, 5.0, frame.height))
        return frame.with_columns(
            pl.when(late).then(pl.col(name) * factors).otherwise(pl.col(name)).alias(name)
            for name in columns
        )

    def raw_prices(self) -> pl.DataFrame:
        frame = super().raw_prices()
        late = frame["date"] > self.boundary
        return self._scramble(frame, late, ["open", "high", "low", "close", "volume", "shares_out"])

    def raw_actions(self) -> pl.DataFrame:
        frame = super().raw_actions()
        return self._scramble(frame, frame["ex_date"] > self.boundary, ["value"])

    def raw_delistings(self) -> pl.DataFrame:
        frame = super().raw_delistings()
        return self._scramble(frame, frame["delist_date"] > self.boundary, ["dlret_raw"])

    def _late_accessions(self) -> pl.Series:
        stamped = stamp_availability(super().raw_filings(), PitPolicy())
        return stamped.filter(pl.col("available_at") > self.cutoff)["accession"]

    def raw_facts(self) -> pl.DataFrame:
        frame = super().raw_facts()
        late = frame["accession"].is_in(self._late_accessions().to_list())
        assert late.any()
        return self._scramble(frame, late, ["value"])


@pytest.fixture(scope="module")
def baseline(small_store: PitStore) -> pl.DataFrame:
    return compute_signals(small_store, list(PROBES), DATES)


@pytest.fixture(scope="module")
def library_ids() -> list[str]:
    return [name for name in registry.registered() if not name.startswith(("p_", "canary"))]


@pytest.fixture(scope="module")
def library(small_store: PitStore, library_ids: list[str]) -> pl.DataFrame:
    return compute_signals(small_store, library_ids, DATES)


def test_every_library_signal_is_public_at_its_cutoff(library: pl.DataFrame) -> None:
    assert library["signal"].n_unique() == 26
    assert_no_feature_after_cutoff(library)


def test_the_library_ignores_every_later_row(
    small_provider: SyntheticProvider,
    library: pl.DataFrame,
    library_ids: list[str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("corrupted-library") / "pit"
    build_pit(CorruptedProvider(small_provider, BOUNDARY), root)
    assert_identical(library, compute_signals(PitStore.open(root), library_ids, DATES))


def test_no_feature_after_cutoff(baseline: pl.DataFrame) -> None:
    assert baseline.height > 100
    assert set(baseline["signal"].unique().to_list()) == set(PROBES)
    assert_no_feature_after_cutoff(baseline)


def test_perturbation_invariance(
    small_provider: SyntheticProvider,
    baseline: pl.DataFrame,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("corrupted") / "pit"
    build_pit(CorruptedProvider(small_provider, BOUNDARY), root)
    corrupted = PitStore.open(root)
    assert_identical(baseline, compute_signals(corrupted, list(PROBES), DATES))


def test_canary_leaky_signal_is_caught(
    small_store: PitStore,
    small_provider: SyntheticProvider,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    honest = compute_signals(small_store, ["canary_honest"], DATES)
    assert not late_rows(honest).is_empty()
    with pytest.raises(LeakageError, match="after the cutoff"):
        assert_no_feature_after_cutoff(honest)

    liar = compute_signals(small_store, ["canary_liar"], DATES)
    assert_no_feature_after_cutoff(liar)
    root = tmp_path_factory.mktemp("canary") / "pit"
    build_pit(CorruptedProvider(small_provider, BOUNDARY), root)
    perturbed = compute_signals(PitStore.open(root), ["canary_liar"], DATES)
    with pytest.raises(LeakageError, match="changed"):
        assert_identical(liar, perturbed)


def test_signals_import_only_pitstore() -> None:
    modules = sorted(SIGNALS_DIR.glob("*.py"))
    assert modules
    found = [issue for path in modules for issue in import_violations(path)]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "import polars as pl\npl.read_parquet('x')\n",
        "import polars as pl\npl.scan_parquet('x')\n",
        "from eqpit.store import LabelStore\n",
        "import os\n",
        "from pathlib import Path\n",
        "from eqpit.pit.labels import monthly_returns\n",
        "def f(store):\n    return store.labels().forward_returns()\n",
        "def f(frame):\n    return frame.group_by('a').sum()\n",
        "def f(frame):\n    return frame.group_by('a', maintain_order=True).agg(col('x').sum())\n",
    ],
)
def test_the_import_check_catches_bypasses(tmp_path: Path, source: str) -> None:
    module = tmp_path / "bad.py"
    module.write_text(source, encoding="utf-8")
    assert import_violations(module)


def test_fundamentals_never_before_acceptance(small_store: PitStore) -> None:
    fundamentals = pl.read_parquet(small_store.root / "fundamentals_pit" / "**" / "*.parquet")
    filings = pl.read_parquet(small_store.root / "filings" / "**" / "*.parquet")
    joined = fundamentals.filter(pl.col("pit_method").eq("acceptance")).join(
        filings.select("accession", "acceptance_dt", "filed"), on="accession"
    )
    assert joined.height == fundamentals.height
    assert joined.filter(pl.col("available_at") < pl.col("acceptance_dt")).is_empty()
    assert fundamentals.filter(
        local_date(pl.col("available_at")) <= pl.col("period_end")
    ).is_empty()
    next_day = joined.filter(
        local_date(pl.col("available_at")) <= local_date(pl.col("acceptance_dt"))
    )
    assert next_day.is_empty()


def test_identical_panels_pass_and_row_changes_fail(baseline: pl.DataFrame) -> None:
    assert_identical(baseline, baseline)
    with pytest.raises(LeakageError, match="which rows exist"):
        assert_identical(baseline, baseline.head(10))
