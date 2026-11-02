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
_YEARS_IN_MEMORY = 4
DEFAULT_UNIVERSE = 3000
_VIEW_YEARS = 6


class LabelAccessError(PermissionError):
    """Signal code asked for forward returns (DQ-12)."""


class FutureAccessError(PermissionError):
    """Signal code asked for data as of a date past the one it is computing."""


class _Tables:
    """Eager tables shared by a store and its signal views.

    Holding them here makes a month loop cheap. It widens nothing: every public read still
    filters on ``available_at`` before returning.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._frames: dict[str, pl.DataFrame] = {}
        self._years: dict[int, pl.DataFrame] = {}

    def table(self, name: str) -> pl.DataFrame:
        if name not in self._frames:
            self._frames[name] = pl.read_parquet(self.root / name / "**" / "*.parquet").rechunk()
        return self._frames[name]

    def fundamentals(self) -> pl.DataFrame:
        if "fundamentals_pit" not in self._frames:
            self._frames["fundamentals_pit"] = (
                pl.read_parquet(self.root / "fundamentals_pit" / "**" / "*.parquet")
                .sort(*_FUNDAMENTAL_KEY, "available_at", "accession", "value")
                .rechunk()
            )
        return self._frames["fundamentals_pit"]

    def prices_for_year(self, year: int) -> pl.DataFrame:
        if year not in self._years:
            if len(self._years) >= _YEARS_IN_MEMORY:
                del self._years[min(self._years)]
            path = self.root / "prices_daily" / f"year={year}" / "part-0.parquet"
            self._years[year] = pl.read_parquet(path) if path.exists() else _empty_prices(self.root)
        return self._years[year]


def _empty_prices(root: Path) -> pl.DataFrame:
    return pl.scan_parquet(root / "prices_daily" / "**" / "*.parquet").clear().collect()


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
        tables: _Tables | None = None,
        signal_universe: int = DEFAULT_UNIVERSE,
    ) -> None:
        self.root = root
        self.signal_universe = signal_universe
        self._max_asof = max_asof
        self._labels_allowed = labels_allowed
        self._tables = tables or _Tables(root)
        self._memo: dict[object, pl.DataFrame] = {}

    @classmethod
    def open(cls, root: Path | None = None) -> PitStore:
        """Open the store at ``root``, by default ``<data dir>/processed/pit``."""
        resolved = root if root is not None else get_settings().data_dir / "processed" / "pit"
        if not (resolved / "securities").is_dir():
            raise FileNotFoundError(f"no point-in-time tables at {resolved}")
        return cls(resolved)

    def for_signals(self, asof: date, universe: int = DEFAULT_UNIVERSE) -> PitStore:
        """The view handed to a signal function: no labels, and no date past ``asof``.

        Reads through a view are remembered, so signals on one date share them. ``universe``
        is how many names the view's signals cover.
        """
        return PitStore(
            self.root,
            max_asof=asof,
            labels_allowed=False,
            tables=self._tables,
            signal_universe=universe,
        )

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
        key = ("universe", asof, n)
        if key not in self._memo:
            self._memo[key] = (
                self._tables.table("universe_monthly")
                .filter(
                    pl.col("asof") == asof,
                    pl.col("available_at") <= cutoff,
                    pl.col("rank") <= n,
                )
                .drop(_HIDDEN_UNIVERSE_COLUMNS)
                .sort("rank")
                .rechunk()
            )
        return self._memo[key]

    def _price_window(self, asof: date, first: date, cutoff: datetime) -> pl.DataFrame:
        key = ("prices", asof, first)
        if key not in self._memo:
            years = range(first.year, asof.year + 1)
            self._memo[key] = (
                pl.concat([self._tables.prices_for_year(year) for year in years], how="vertical")
                .filter(
                    pl.col("date").is_between(first, asof),
                    pl.col("available_at") <= cutoff,
                )
                .sort("security_id", "date")
                .with_columns(
                    (
                        pl.col("close")
                        * pl.col("split_factor")
                        / pl.col("split_factor").last().over("security_id")
                    ).alias("adj_close")
                )
                .rechunk()
            )
        return self._memo[key]

    def prices(
        self, asof: date, lookback_days: int, ids: Sequence[str] | None = None
    ) -> pl.DataFrame:
        """Daily rows from the last ``lookback_days`` sessions through ``asof``.

        ``adj_close`` restates each close in the share basis of ``asof``. It is built only
        from splits visible at the cutoff, so a later split cannot reach it (DQ-09).
        """
        cutoff = self._gate(asof)
        first = calendar.previous_session(calendar.session_on_or_after(asof), lookback_days - 1)
        frame = self._price_window(asof, first, cutoff)
        if ids is None:
            return frame
        return frame.filter(pl.col("security_id").is_in(list(ids))).rechunk()

    def monthly(self, asof: date, months: int, ids: Sequence[str] | None = None) -> pl.DataFrame:
        """Compounded monthly returns for the last ``months`` month ends through ``asof``."""
        cutoff = self._gate(asof)
        ends = calendar.month_ends(date(asof.year - months // 12 - 1, 1, 1), asof)[-months:]
        frame = self._tables.table("prices_monthly").filter(
            pl.col("month").is_between(ends[0], asof),
            pl.col("available_at") <= cutoff,
        )
        if ids is not None:
            frame = frame.filter(pl.col("security_id").is_in(list(ids)))
        return frame.sort("security_id", "month").rechunk()

    def market(self, asof: date, lookback_days: int) -> pl.DataFrame:
        """Cap-weighted universe return for the last ``lookback_days`` sessions."""
        cutoff = self._gate(asof)
        first = calendar.previous_session(calendar.session_on_or_after(asof), lookback_days - 1)
        return (
            self._tables.table("market_daily")
            .filter(pl.col("date").is_between(first, asof), pl.col("available_at") <= cutoff)
            .sort("date")
            .rechunk()
        )

    def _known_fundamentals(self, asof: date, cutoff: datetime) -> pl.DataFrame:
        """Latest public version of every period; a view keeps only recent periods."""
        key = ("fundamentals", asof)
        if key in self._memo:
            return self._memo[key]
        rows = self._tables.fundamentals().filter(pl.col("available_at") <= cutoff)
        if self._max_asof is not None:
            rows = rows.filter(pl.col("period_end") >= date(asof.year - _VIEW_YEARS, 1, 1))
        latest = rows.unique(subset=_FUNDAMENTAL_KEY, keep="last", maintain_order=True).rechunk()
        if self._max_asof is not None:
            self._memo[key] = latest
        return latest

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
        keeps that many most recent periods per (company, item, period_type). A signal view
        sees periods from the last few years only, which is all a signal needs.
        """
        cutoff = self._gate(asof)
        known = self._known_fundamentals(asof, cutoff)
        rows = known.filter(pl.col("item").is_in(list(items)))
        if ids is not None:
            rows = rows.filter(pl.col("company_id").is_in(list(ids)))
        recent = pl.int_range(pl.len()).over("company_id", "item", "period_type")
        return rows.reverse().filter(recent < history_periods).rechunk()

    def securities(self, asof: date) -> pl.DataFrame:
        """Master rows valid at ``asof``."""
        cutoff = self._gate(asof)
        return (
            self._tables.table("securities")
            .filter(
                pl.col("valid_from") <= asof,
                pl.col("valid_to").is_null() | (pl.col("valid_to") >= asof),
                pl.col("available_at") <= cutoff,
            )
            .sort("security_id")
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

    def universe_history(self) -> pl.DataFrame:
        """Every ``universe_monthly`` row, including the ``delisted_after`` diagnostic."""
        return self._read("universe_monthly").sort("asof", "rank")

    def month_end_caps(self) -> pl.DataFrame:
        """``month``, ``security_id``, ``mcap`` and ``close`` at every month-end session."""
        prices = pl.scan_parquet(self.root / "prices_daily" / "**" / "*.parquet")
        bounds = prices.select(
            pl.col("date").min().alias("first"), pl.col("date").max().alias("last")
        ).collect()
        ends = calendar.month_ends(bounds["first"][0], bounds["last"][0])
        return (
            prices.filter(pl.col("date").is_in(ends))
            .select(pl.col("date").alias("month"), "security_id", "mcap", "close")
            .drop_nulls("mcap")
            .sort("month", "security_id")
            .collect()
        )

    def delisted_ids(self) -> pl.Series:
        """Securities that delisted at any point in the sample."""
        return self._read("delistings")["security_id"].unique().sort()
