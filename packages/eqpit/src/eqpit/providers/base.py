"""The provider interface and the shared route from raw frames to PIT frames."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

import polars as pl

from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, XbrlMap
from eqpit.pit.corpactions import build_corp_actions
from eqpit.pit.delisting import build_delistings
from eqpit.pit.filings import build_filings
from eqpit.pit.fundamentals import build_fundamentals
from eqpit.pit.policy import PitPolicy
from eqpit.pit.prices import build_prices
from eqpit.pit.securities import build_securities
from eqpit.settings import ProviderName, get_settings
from eqpit.snapshot import SnapshotManifest, latest_snapshot, snapshot_dir


class ProviderUnavailableError(RuntimeError):
    """The chosen provider cannot run in this environment."""


class DataProvider(Protocol):
    """Everything downstream code needs from a data source."""

    name: ProviderName
    survivorship_free: bool

    def pull(
        self, datasets: Sequence[str], start: date, end: date, dest: Path
    ) -> list[SnapshotManifest]: ...
    def securities(self) -> pl.LazyFrame: ...
    def prices_daily(self) -> pl.LazyFrame: ...
    def delistings(self) -> pl.LazyFrame: ...
    def corp_actions(self) -> pl.LazyFrame: ...
    def filings(self) -> pl.LazyFrame: ...
    def fundamentals(self) -> pl.LazyFrame: ...
    def risk_free(self) -> pl.LazyFrame | None: ...
    def snapshot_ids(self) -> dict[str, str]: ...
    def pin(self, spec: str) -> None: ...


class RawProvider(ABC):
    """A provider supplies normalised raw frames; the shared builders apply the rules."""

    name: ProviderName
    survivorship_free: bool

    def __init__(
        self,
        root: Path,
        policy: PitPolicy | None = None,
        xbrl_map: XbrlMap = DEFAULT_XBRL_MAP,
    ) -> None:
        self.root = root
        self.policy = policy or PitPolicy()
        self.xbrl_map = xbrl_map
        self.pinned: dict[str, str] = {}

    def pin(self, spec: str) -> None:
        """Read specific snapshots: one id for all datasets, or ``dataset=id,dataset=id``."""
        self.pinned = (
            {"*": spec} if "=" not in spec else dict(item.split("=", 1) for item in spec.split(","))
        )

    def snapshot_path(self, dataset: str) -> Path:
        """Directory of the snapshot to read: a pinned one, else the latest."""
        chosen = self.pinned.get(dataset) or self.pinned.get("*")
        if chosen is None:
            return latest_snapshot(self.root, dataset)
        path = snapshot_dir(self.root, dataset, chosen)
        if not path.is_dir():
            raise FileNotFoundError(f"no snapshot {chosen} of {dataset} under {self.root}")
        return path

    @abstractmethod
    def pull(
        self, datasets: Sequence[str], start: date, end: date, dest: Path
    ) -> list[SnapshotManifest]:
        """Write new raw snapshots under ``dest``."""

    @abstractmethod
    def raw_securities(self) -> pl.DataFrame:
        """Rows for ``build_securities``."""

    @abstractmethod
    def raw_prices(self) -> pl.DataFrame:
        """Rows for ``build_prices``."""

    @abstractmethod
    def raw_delistings(self) -> pl.DataFrame:
        """Rows for ``build_delistings``."""

    @abstractmethod
    def raw_actions(self) -> pl.DataFrame:
        """Rows for ``build_corp_actions``."""

    @abstractmethod
    def raw_filings(self) -> pl.DataFrame:
        """Rows for ``build_filings``."""

    @abstractmethod
    def raw_facts(self) -> pl.DataFrame:
        """Rows for ``build_fundamentals``."""

    @abstractmethod
    def snapshot_ids(self) -> dict[str, str]:
        """Raw snapshot id per dataset behind these tables."""

    def risk_free(self) -> pl.LazyFrame | None:
        """Monthly ``month`` and ``rf``, when the provider has one."""
        return None

    def securities(self) -> pl.LazyFrame:
        """The ``securities`` table."""
        return build_securities(self.raw_securities()).lazy()

    def corp_actions(self) -> pl.LazyFrame:
        """The ``corp_actions`` table."""
        return build_corp_actions(self.raw_actions()).lazy()

    def prices_daily(self) -> pl.LazyFrame:
        """The ``prices_daily`` table."""
        return build_prices(self.raw_prices(), self.corp_actions().collect()).lazy()

    def delistings(self) -> pl.LazyFrame:
        """The ``delistings`` table."""
        return build_delistings(self.raw_delistings(), self.policy).lazy()

    def filings(self) -> pl.LazyFrame:
        """The ``filings`` table."""
        return build_filings(self.raw_filings(), self.policy).lazy()

    def fundamentals(self) -> pl.LazyFrame:
        """The ``fundamentals_pit`` table."""
        filings = self.filings().collect()
        return build_fundamentals(self.raw_facts(), filings, self.policy, self.xbrl_map).lazy()


def get_provider(
    name: ProviderName | None = None,
    root: Path | None = None,
    policy: PitPolicy | None = None,
    xbrl_map: XbrlMap = DEFAULT_XBRL_MAP,
    max_companies: int | None = None,
) -> DataProvider:
    """Provider ``name`` or ``EFP_PROVIDER``, reading raw data under ``root``."""
    settings = get_settings()
    chosen = name or settings.efp_provider
    base = root if root is not None else settings.data_dir / "raw" / chosen
    if chosen == "synthetic":
        from eqpit.providers.synthetic import SyntheticProvider

        return SyntheticProvider(base, policy, xbrl_map)
    if chosen == "edgar_free":
        from eqpit.providers.edgar_free import EdgarFreeProvider

        return EdgarFreeProvider(base, policy, xbrl_map, max_companies)
    if chosen == "sharadar":
        raise ProviderUnavailableError("the sharadar provider arrives in M3")
    raise ProviderUnavailableError("the wrds provider is off: the owner has no WRDS access")
