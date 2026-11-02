"""Immutable raw snapshots and their manifests (DQ-15)."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from eqpit.settings import ProviderName

MANIFEST_NAME = "manifest.json"
SNAPSHOT_FORMAT = "%Y%m%dT%H%M%SZ"


class SnapshotExistsError(FileExistsError):
    """A snapshot directory already exists; snapshots are never rewritten."""


class SnapshotIntegrityError(ValueError):
    """A snapshot's files no longer match its manifest."""


class SnapshotManifest(BaseModel):
    """Provenance record for one raw snapshot."""

    provider: ProviderName
    dataset: str
    snapshot_id: str
    created_at: datetime
    files: dict[str, str]
    row_counts: dict[str, int]
    survivorship_limited: bool
    source: str = ""
    package_version: str = ""
    metadata: dict[str, str] = {}


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_id(moment: datetime | None = None) -> str:
    """Format a UTC instant as a snapshot id, ``YYYYMMDDTHHMMSSZ``."""
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime(SNAPSHOT_FORMAT)


def snapshot_dir(root: Path, dataset: str, snapshot: str) -> Path:
    """Directory of one snapshot under a provider root."""
    return root / dataset / f"snapshot={snapshot}"


@contextmanager
def open_snapshot(
    root: Path,
    provider: ProviderName,
    dataset: str,
    survivorship_limited: bool,
    *,
    source: str = "",
    package_version: str = "",
    created_at: datetime | None = None,
    row_counts: dict[str, int] | None = None,
    metadata: dict[str, str] | None = None,
) -> Iterator[Path]:
    """Yield a staging directory and publish it as a new immutable snapshot on clean exit.

    Files appear under ``snapshot=<id>/`` only once complete, with a manifest listing the
    sha256 of each file. If the body raises, nothing is published. ``row_counts`` and
    ``metadata`` may be filled in by the body.
    """
    moment = (created_at or datetime.now(UTC)).astimezone(UTC)
    identifier = snapshot_id(moment)
    target = snapshot_dir(root, dataset, identifier)
    if target.exists():
        raise SnapshotExistsError(str(target))
    staging = target.with_name(f".staging-{identifier}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    counts = row_counts if row_counts is not None else {}
    extra = metadata if metadata is not None else {}
    try:
        yield staging
        files = {
            path.relative_to(staging).as_posix(): sha256_file(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest = SnapshotManifest(
            provider=provider,
            dataset=dataset,
            snapshot_id=identifier,
            created_at=moment,
            files=files,
            row_counts=dict(sorted(counts.items())),
            survivorship_limited=survivorship_limited,
            source=source,
            package_version=package_version,
            metadata=dict(sorted(extra.items())),
        )
        payload = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
        (staging / MANIFEST_NAME).write_text(payload + "\n", encoding="utf-8", newline="\n")
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def read_manifest(path: Path) -> SnapshotManifest:
    """Read the manifest of a snapshot directory."""
    return SnapshotManifest.model_validate_json((path / MANIFEST_NAME).read_text(encoding="utf-8"))


def verify_snapshot(path: Path) -> SnapshotManifest:
    """Check a snapshot's files against its manifest, raising on any difference."""
    manifest = read_manifest(path)
    present = {
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if item.is_file() and item.name != MANIFEST_NAME
    }
    if present != set(manifest.files):
        raise SnapshotIntegrityError(f"{path}: file list differs from manifest")
    for name, expected in manifest.files.items():
        if sha256_file(path / name) != expected:
            raise SnapshotIntegrityError(f"{path}: {name} differs from manifest")
    return manifest


def list_snapshots(root: Path, dataset: str) -> list[Path]:
    """Published snapshots of a dataset, oldest first."""
    base = root / dataset
    if not base.is_dir():
        return []
    return sorted(item for item in base.glob("snapshot=*") if (item / MANIFEST_NAME).is_file())


def latest_snapshot(root: Path, dataset: str) -> Path:
    """Most recent published snapshot of a dataset."""
    found = list_snapshots(root, dataset)
    if not found:
        raise FileNotFoundError(f"no snapshot of {dataset} under {root}")
    return found[-1]


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    """Delete all but the latest ``keep`` snapshots of every dataset; return what went."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    removed: list[Path] = []
    for provider in sorted(item for item in root.iterdir() if item.is_dir()):
        for dataset in sorted(item for item in provider.iterdir() if item.is_dir()):
            snapshots = list_snapshots(provider, dataset.name)
            for stale in snapshots[:-keep]:
                shutil.rmtree(stale)
                removed.append(stale)
    return removed
