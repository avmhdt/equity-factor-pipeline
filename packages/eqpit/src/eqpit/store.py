"""PitStore: the as-of gate and the only read path for downstream code (FR-11, DQ-01)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import polars as pl

from eqpit import calendar
from eqpit.pit.labels import monthly_returns
from eqpit.settings import get_settings

_HIDDEN_UNIVERSE_COLUMNS = ("delisted_after",)
_FUNDAMENTAL_KEY = ["company_id", "item", "period_end", "period_type"]


class LabelAccessError(PermissionError):
    """Signal code asked for forward returns (DQ-12)."""


class FutureAccessError(PermissionError):
    """Signal code asked for data as of a date past the one it is computing."""


class PitStore:
    """Read point-in-time tables as of a date.

    A row is visible at ``asof`` only if ``available_at <= cutoff(asof)``. Frames come back in
    one chunk: float sums depend on chunk layout, and the layout of a filtered scan depends on
    rows the filter removed, so chunked frames would let later rows reach the last bits.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_asof: date | None = None,
        labels_allowed: bool = True,
    ) -> None:
        self.root = root
        self._max_asof = max_asof
        self._labels_allowed = labels_allowed

    @classmethod
    def open(cls, root: Path | None = None) -> PitStore:
        """Open the store at ``root``, by default ``<data dir>/processed/pit``."""
        resolved = root if root is not None else get_settings().data_dir / "processed" / "pit"
        if not (resolved / "securities").is_dir():
            raise FileNotFoundError(f"no point-in-time tables at {resolved}")
        return cls(resolved)

    def for_signals(self, asof: date) -> PitStore:
        """The view handed to a signal function: no labels, and no date past ``asof``."""
        return PitStore(self.root, max_asof=asof, labels_allowed=False)

    def _scan(self, table: str) -> pl.LazyFrame:
        return pl.scan_parquet(self.root / table / "**" / "*.parquet")

    def _gate(self, asof: date) -> datetime:
        if self._max_asof is not None and asof > self._max_asof:
            raise FutureAccessError(f"asked for {asof} while computing {self._max_asof}")
        return self.cutoff(asof)

    def cutoff(self, asof: date) -> datetime:
        """UTC instant of the close on ``asof``: 16:00 New York, or the early close."""
        return calendar.session_close(asof)

    def month_ends(self, start: date, end: date) -> list[date]:
        """Month-end sessions in ``[start, end]``."""
        return calendar.month_ends(start, end)

    def universe(self, asof: date, n: int = 3000) -> pl.DataFrame:
        """Top ``n`` eligible securities at ``asof``, largest first."""
        cutoff = self._gate(asof)
        return (
            self._scan("universe_monthly")
            .filter(
                pl.col("asof") == asof,
                pl.col("available_at") <= cutoff,
                pl.col("rank") <= n,
            )
            .drop(_HIDDEN_UNIVERSE_COLUMNS)
            .sort("rank")
            .collect()
            .rechunk()
        )

    def prices(
        self, asof: date, lookback_days: int, ids: Sequence[str] | None = None
    ) -> pl.DataFrame:
        """Daily rows from the last ``lookback_days`` sessions through ``asof``.

        ``adj_close`` restates each close in the share basis of ``asof``. It is built only
        from splits visible at the cutoff, so a later split cannot reach it (DQ-09).
        """
        cutoff = self._gate(asof)
        first = calendar.previous_session(calendar.session_on_or_after(asof), lookback_days - 1)
        query = self._scan("prices_daily").filter(
            pl.col("date").is_between(first, asof),
            pl.col("available_at") <= cutoff,
        )
        if ids is not None:
            query = query.filter(pl.col("security_id").is_in(list(ids)))
        return (
            query.sort("security_id", "date")
            .with_columns(
                (
                    pl.col("close")
                    * pl.col("split_factor")
                    / pl.col("split_factor").last().over("security_id")
                ).alias("adj_close")
            )
            .collect()
            .rechunk()
        )

    def fundamentals(
        self,
        asof: date,
        items: Sequence[str],
        ids: Sequence[str] | None = None,
        history_periods: int = 1,
    ) -> pl.DataFrame:
        """Value per (company, item, period) known at the cutoff, latest periods first.

        For a period with several reports the latest public one wins. Args ``ids`` are
        ``company_id`` values, since fundamentals belong to issuers. ``history_periods``
        keeps that many most recent periods per (company, item, period_type).
        """
        cutoff = self._gate(asof)
        query = self._scan("fundamentals_pit").filter(
            pl.col("item").is_in(list(items)),
            pl.col("available_at") <= cutoff,
        )
        if ids is not None:
            query = query.filter(pl.col("company_id").is_in(list(ids)))
        latest = (
            query.sort(*_FUNDAMENTAL_KEY, "available_at", "accession")
            .unique(subset=_FUNDAMENTAL_KEY, keep="last", maintain_order=True)
            .sort(
                ["company_id", "item", "period_type", "period_end"],
                descending=[False, False, False, True],
            )
        )
        recent = pl.int_range(pl.len()).over("company_id", "item", "period_type")
        return latest.filter(recent < history_periods).collect().rechunk()

    def securities(self, asof: date) -> pl.DataFrame:
        """Master rows valid at ``asof``."""
        cutoff = self._gate(asof)
        return (
            self._scan("securities")
            .filter(
                pl.col("valid_from") <= asof,
                pl.col("valid_to").is_null() | (pl.col("valid_to") >= asof),
                pl.col("available_at") <= cutoff,
            )
            .sort("security_id")
            .collect()
            .rechunk()
        )

    def labels(self) -> LabelStore:
        """Forward returns, kept in a separate object that signal code never receives."""
        if not self._labels_allowed:
            raise LabelAccessError("signal functions may not read forward returns")
        return LabelStore(self.root)


class LabelStore:
    """Evaluation labels: forward and monthly returns (DQ-12)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _read(self, table: str) -> pl.DataFrame:
        return pl.read_parquet(self.root / table / "**" / "*.parquet")

    def forward_returns(self, horizons: Sequence[int] = (1,)) -> pl.DataFrame:
        """Forward returns for the given horizons, in months."""
        return self._read("forward_returns").filter(pl.col("h").is_in(list(horizons)))

    def monthly_returns(
        self, start: date, end: date, include_delisting: bool = True
    ) -> pl.DataFrame:
        """Total monthly returns for months ending in ``[start, end]``."""
        prices = self._read("prices_daily")
        last = prices["date"].max()
        assert isinstance(last, date)
        ends = calendar.month_ends(prices["date"].min(), last)  # type: ignore[arg-type]
        returns = monthly_returns(prices, self._read("delistings"), ends, include_delisting)
        return returns.filter(pl.col("month").is_between(start, end))
