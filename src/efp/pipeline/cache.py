"""Content-addressed stage cache: ``data/interim/<stage>/<cache_key>/``."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

from efp.config import canonical_json
from efp.pipeline.manifest import read_manifest

MANIFEST_NAME = "manifest.json"


def cache_key(
    code_hash: str,
    config_subset: object,
    input_keys: Iterable[str],
    raw_snapshots: Mapping[str, str],
) -> str:
    """sha256 of stage code, config subset, input cache keys and raw snapshot ids."""
    payload = canonical_json(
        {
            "code": code_hash,
            "config": config_subset,
            "inputs": sorted(input_keys),
            "raw": dict(sorted(raw_snapshots.items())),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class StageCache:
    """Interim outputs on disk, one directory per stage and cache key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, stage: str, key: str) -> Path:
        """Directory for one cached output."""
        return self.root / stage / key

    def hit(self, stage: str, key: str) -> bool:
        """True when a complete output exists; the manifest is written last."""
        return (self.path(stage, key) / MANIFEST_NAME).is_file()

    def entries(self) -> list[tuple[str, str]]:
        """All complete entries as (stage, key), sorted."""
        if not self.root.is_dir():
            return []
        found = [
            (stage.name, entry.name)
            for stage in self.root.iterdir()
            if stage.is_dir()
            for entry in stage.iterdir()
            if (entry / MANIFEST_NAME).is_file()
        ]
        return sorted(found)

    def prune(self, keep_last: int) -> list[Path]:
        """Keep the newest ``keep_last`` entries per stage and delete the rest."""
        if keep_last < 1:
            raise ValueError("keep_last must be at least 1")
        by_stage: dict[str, list[str]] = {}
        for stage, key in self.entries():
            by_stage.setdefault(stage, []).append(key)
        removed: list[Path] = []
        for stage, keys in by_stage.items():
            ordered = sorted(
                keys,
                key=lambda item: read_manifest(self.path(stage, item) / MANIFEST_NAME).created_at,
            )
            for stale in ordered[:-keep_last]:
                path = self.path(stage, stale)
                shutil.rmtree(path)
                removed.append(path)
        return removed
