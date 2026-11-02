"""The synthetic provider: known truth, no licences, runs anywhere."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl

from eqpit import __version__
from eqpit.providers.base import RawProvider
from eqpit.settings import ProviderName
from eqpit.snapshot import (
    SnapshotExistsError,
    SnapshotManifest,
    open_snapshot,
    read_manifest,
    snapshot_dir,
    snapshot_id,
)
from eqpit.synthetic.generator import SyntheticConfig, generate_market, snapshot_time

DATASETS = (
    "securities",
    "prices",
    "actions",
    "delistings",
    "filings",
    "facts",
    "risk_free",
    "truth",
)


def write_market(config: SyntheticConfig, out: Path) -> list[SnapshotManifest]:
    """Generate the market into snapshots under ``out``; a rerun of a seed reuses them."""
    moment = snapshot_time(config.seed)
    identifier = snapshot_id(moment)
    if all(snapshot_dir(out, name, identifier).is_dir() for name in DATASETS):
        return [read_manifest(snapshot_dir(out, name, identifier)) for name in DATASETS]
    market = generate_market(config)
    manifests: list[SnapshotManifest] = []
    source = (
        f"eqpit.synthetic seed={config.seed} n_securities={config.n_securities} "
        f"years={config.years} target_ic={config.target_ic}"
    )
    for name in DATASETS:
        target = snapshot_dir(out, name, identifier)
        if target.is_dir():
            manifests.append(read_manifest(target))
            continue
        try:
            with open_snapshot(
                out,
                "synthetic",
                name,
                False,
                source=source,
                package_version=__version__,
                created_at=moment,
                row_counts={f"{name}.parquet": market[name].height},
            ) as staging:
                market[name].write_parquet(staging / f"{name}.parquet", compression="zstd")
        except SnapshotExistsError:
            pass
        manifests.append(read_manifest(target))
    return manifests


class SyntheticProvider(RawProvider):
    """Reads the latest generated snapshots."""

    name: ProviderName = "synthetic"
    survivorship_free = True

    def pull(
        self, datasets: Sequence[str], start: date, end: date, dest: Path
    ) -> list[SnapshotManifest]:
        """Generate the default market; ``start`` sets the first year."""
        years = max(end.year - start.year + 1, 1)
        return write_market(SyntheticConfig(start_year=start.year, years=years), dest)

    def _dataset(self, name: str) -> pl.DataFrame:
        return pl.read_parquet(self.snapshot_path(name) / f"{name}.parquet")

    def raw_securities(self) -> pl.DataFrame:
        return self._dataset("securities")

    def raw_prices(self) -> pl.DataFrame:
        return self._dataset("prices")

    def raw_delistings(self) -> pl.DataFrame:
        return self._dataset("delistings")

    def raw_actions(self) -> pl.DataFrame:
        return self._dataset("actions")

    def raw_filings(self) -> pl.DataFrame:
        return self._dataset("filings")

    def raw_facts(self) -> pl.DataFrame:
        return self._dataset("facts")

    def risk_free(self) -> pl.LazyFrame | None:
        return self._dataset("risk_free").lazy()

    def snapshot_ids(self) -> dict[str, str]:
        return {name: self.snapshot_path(name).name.split("=")[1] for name in DATASETS}
