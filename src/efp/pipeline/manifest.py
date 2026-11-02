"""Output manifests: every artifact records how it was made (NFR-09)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class OutputManifest(BaseModel):
    """Provenance of one stage output."""

    stage: str
    cache_key: str
    git_sha: str
    config_hash: str
    raw_snapshots: dict[str, str]
    provider: str
    survivorship_limited: bool
    created_at: datetime
    duration_seconds: float
    rows: int


def git_sha(cwd: Path | None = None) -> str:
    """Current commit, suffixed ``-dirty`` for a dirty tree; ``unknown`` outside git."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def write_manifest(path: Path, manifest: OutputManifest) -> None:
    """Write a manifest as sorted JSON."""
    payload = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def read_manifest(path: Path) -> OutputManifest:
    """Read a manifest."""
    return OutputManifest.model_validate_json(path.read_text(encoding="utf-8"))


def now_utc() -> datetime:
    """Current UTC instant."""
    return datetime.now(UTC)
