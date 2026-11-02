"""FR-29, NFR-03: the synthetic rebuild, end to end through the CLI."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from efp.cli import app
from eqpit.pit.build import build_pit, read_pit_manifest
from eqpit.providers.base import ProviderUnavailableError, get_provider
from eqpit.providers.synthetic import SyntheticProvider, write_market
from eqpit.snapshot import list_snapshots, verify_snapshot
from eqpit.store import PitStore
from eqpit.synthetic import SyntheticConfig

runner = CliRunner()
CONFIG = """
run: {name: tiny, seed: 3, provider: synthetic, start: 2012-01-31, end: 2016-12-31}
synthetic: {n_securities: 60, years: 5}
"""


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EFP_LOG_JSON", "0")
    (tmp_path / "tiny.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def table_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.parquet"))
    }


def test_rebuild_verify_and_cache(workspace: Path) -> None:
    first = runner.invoke(app, ["rebuild", "--config", "tiny.yaml"])
    assert first.exit_code == 0, first.output
    assert first.output.count(" ran ") == 3
    second = runner.invoke(app, ["rebuild", "--config", "tiny.yaml"])
    assert second.output.count(" cached ") == 3

    root = workspace / "data" / "processed" / "pit"
    store = PitStore.open(root)
    assert not store.universe(store.month_ends(date(2014, 1, 1), date(2014, 3, 1))[0]).is_empty()
    tables = workspace / "data" / "processed" / "tables" / "tiny"
    assert (tables / "dq_report.csv").is_file()
    assert (tables / "pit_method_coverage.csv").is_file()
    assert not (workspace / "results" / "tables").exists()

    verify = runner.invoke(app, ["data", "verify", "--config", "tiny.yaml"])
    assert verify.exit_code == 0, verify.output
    report = pl.read_csv(tables / "dq_report.csv")
    assert report.filter(pl.col("status").eq("fail")).is_empty()

    rebuilt = runner.invoke(app, ["universe", "build", "--config", "tiny.yaml"])
    assert rebuilt.exit_code == 0, rebuilt.output


def test_generate_then_build_through_the_cli(workspace: Path) -> None:
    raw = workspace / "data" / "raw" / "synthetic"
    generated = runner.invoke(
        app,
        [
            "synth",
            "generate",
            "--out",
            str(raw),
            "--seed",
            "3",
            "--n-securities",
            "60",
            "--years",
            "5",
        ],
    )
    assert generated.exit_code == 0, generated.output
    built = runner.invoke(app, ["data", "build-pit", "--provider", "synthetic"])
    assert built.exit_code == 0, built.output
    assert (workspace / "data" / "processed" / "pit" / "manifest.json").is_file()
    missing = runner.invoke(
        app, ["data", "build-pit", "--provider", "synthetic", "--snapshot", "19000101T000000Z"]
    )
    assert isinstance(missing.exception, FileNotFoundError)


def test_a_pinned_snapshot_is_the_one_read(tmp_path: Path) -> None:
    write_market(SyntheticConfig(seed=5, n_securities=40, years=2), tmp_path)
    write_market(SyntheticConfig(seed=6, n_securities=40, years=2), tmp_path)
    provider = SyntheticProvider(tmp_path)
    latest = provider.raw_prices()
    older = sorted(path.name for path in (tmp_path / "prices").iterdir())[0].split("=")[1]
    provider.pin(older)
    assert provider.snapshot_ids()["prices"] == older
    assert not provider.raw_prices().equals(latest)
    provider.pin(f"prices={older}")
    assert provider.snapshot_ids()["prices"] == older
    assert provider.snapshot_ids()["facts"] != older


def test_same_seed_gives_byte_identical_tables(tmp_path: Path) -> None:
    config = SyntheticConfig(seed=5, n_securities=60, years=4)
    hashes: list[dict[str, str]] = []
    for name in ("one", "two"):
        write_market(config, tmp_path / name / "raw")
        build_pit(SyntheticProvider(tmp_path / name / "raw"), tmp_path / name / "pit")
        hashes.append(table_hashes(tmp_path / name / "pit"))
    assert hashes[0] == hashes[1]
    assert len(hashes[0]) > 10


def test_a_different_seed_gives_a_different_market(tmp_path: Path) -> None:
    write_market(SyntheticConfig(seed=5, n_securities=60, years=2), tmp_path / "a")
    write_market(SyntheticConfig(seed=6, n_securities=60, years=2), tmp_path / "b")
    left = SyntheticProvider(tmp_path / "a").raw_prices()
    right = SyntheticProvider(tmp_path / "b").raw_prices()
    assert not left.equals(right)


def test_regenerating_a_seed_reuses_its_snapshots(tmp_path: Path) -> None:
    config = SyntheticConfig(seed=5, n_securities=60, years=2)
    first = write_market(config, tmp_path)
    second = write_market(config, tmp_path)
    assert [item.files for item in first] == [item.files for item in second]
    for dataset in ("prices", "facts"):
        snapshots = list_snapshots(tmp_path, dataset)
        assert len(snapshots) == 1
        verify_snapshot(snapshots[0])


def test_provider_pull_generates(tmp_path: Path) -> None:
    provider = SyntheticProvider(tmp_path)
    manifests = provider.pull([], date(2015, 1, 1), date(2016, 12, 31), tmp_path)
    assert manifests[0].provider == "synthetic"
    assert set(provider.snapshot_ids()) >= {"prices", "facts", "truth"}


def test_provider_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EFP_DATA_DIR", str(tmp_path))
    assert get_provider("synthetic").name == "synthetic"
    assert get_provider("edgar_free").survivorship_free is False
    with pytest.raises(ProviderUnavailableError, match="M3"):
        get_provider("sharadar")
    with pytest.raises(ProviderUnavailableError, match="WRDS"):
        get_provider("wrds")


def test_manifest_records_the_source(small_store: PitStore) -> None:
    manifest = read_pit_manifest(small_store.root)
    assert manifest["provider"] == "synthetic"
    assert manifest["survivorship_limited"] is False
    assert manifest["label"] == ""
    assert manifest["row_counts"]["prices_daily"] > 0
    assert "prices" in manifest["raw_snapshots"]
