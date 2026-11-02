"""FR-03, DQ-15: raw snapshots are immutable and carry manifests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from eqpit.snapshot import (
    SnapshotExistsError,
    SnapshotIntegrityError,
    latest_snapshot,
    list_snapshots,
    open_snapshot,
    prune_snapshots,
    read_manifest,
    sha256_file,
    verify_snapshot,
)

WHEN = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def publish(root: Path, moment: datetime = WHEN) -> Path:
    with open_snapshot(
        root,
        "edgar_free",
        "submissions",
        True,
        source="https://data.sec.gov/",
        package_version="1.2.3",
        created_at=moment,
        row_counts={"a.csv": 2},
    ) as staging:
        (staging / "a.csv").write_text("x\n1\n2\n", encoding="utf-8")
        (staging / "nested").mkdir()
        (staging / "nested" / "b.json").write_text("{}", encoding="utf-8")
    return root / "submissions" / "snapshot=20261101T120000Z"


def test_manifest_records_provenance_and_hashes(tmp_path: Path) -> None:
    path = publish(tmp_path)
    manifest = read_manifest(path)
    assert manifest.provider == "edgar_free"
    assert manifest.snapshot_id == "20261101T120000Z"
    assert manifest.created_at == WHEN
    assert manifest.source == "https://data.sec.gov/"
    assert manifest.package_version == "1.2.3"
    assert manifest.survivorship_limited is True
    assert manifest.row_counts == {"a.csv": 2}
    assert manifest.files["a.csv"] == sha256_file(path / "a.csv")
    assert "nested/b.json" in manifest.files
    assert verify_snapshot(path) == manifest


def test_a_snapshot_is_never_rewritten(tmp_path: Path) -> None:
    publish(tmp_path)
    with pytest.raises(SnapshotExistsError):
        publish(tmp_path)


def test_a_failed_pull_publishes_nothing(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError),
        open_snapshot(tmp_path, "synthetic", "prices", False, created_at=WHEN) as staging,
    ):
        (staging / "half.csv").write_text("x", encoding="utf-8")
        raise RuntimeError("network died")
    assert list_snapshots(tmp_path, "prices") == []
    assert list((tmp_path / "prices").iterdir()) == []


def test_tampering_is_detected(tmp_path: Path) -> None:
    path = publish(tmp_path)
    (path / "a.csv").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError, match=r"a\.csv"):
        verify_snapshot(path)


def test_an_added_file_is_detected(tmp_path: Path) -> None:
    path = publish(tmp_path)
    (path / "extra.csv").write_text("x", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError, match="file list"):
        verify_snapshot(path)


def test_latest_and_prune(tmp_path: Path) -> None:
    root = tmp_path / "edgar_free"
    moments = [WHEN.replace(hour=hour) for hour in (1, 2, 3)]
    for moment in moments:
        publish(root, moment)
    assert latest_snapshot(root, "submissions").name == "snapshot=20261101T030000Z"
    removed = prune_snapshots(tmp_path, keep=2)
    assert [path.name for path in removed] == ["snapshot=20261101T010000Z"]
    assert len(list_snapshots(root, "submissions")) == 2


def test_latest_snapshot_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        latest_snapshot(tmp_path, "nothing")


def test_prune_needs_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="keep"):
        prune_snapshots(tmp_path, keep=0)
