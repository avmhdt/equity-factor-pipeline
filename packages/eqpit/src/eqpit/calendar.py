"""XNYS trading calendar (DQ-17). All other code stays free of pandas through this module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import lru_cache
from itertools import pairwise
from zoneinfo import ZoneInfo

import exchange_calendars
import numpy as np
import polars as pl
from numpy.typing import NDArray

from eqpit.schemas import INSTANT

NEW_YORK = ZoneInfo("America/New_York")
_EPOCH = date(1970, 1, 1)
_REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class _Sessions:
    days: NDArray[np.int64]
    closes_ns: NDArray[np.int64]


@lru_cache(maxsize=1)
def _sessions() -> _Sessions:
    calendar = exchange_calendars.get_calendar("XNYS", start="1962-01-01")
    days = calendar.sessions.to_numpy().astype("datetime64[D]").astype(np.int64)
    closes = calendar.closes.dt.tz_convert("UTC").dt.tz_localize(None)
    closes_ns = closes.to_numpy().astype("datetime64[ns]").astype(np.int64)
    return _Sessions(days, closes_ns)


def _day(value: date) -> int:
    return (value - _EPOCH).days


def _date(day: int) -> date:
    return date.fromordinal(_EPOCH.toordinal() + int(day))


def is_session(value: date) -> bool:
    """True when XNYS trades on ``value``."""
    data = _sessions().days
    index = int(np.searchsorted(data, _day(value)))
    return index < len(data) and int(data[index]) == _day(value)


def sessions(start: date, end: date) -> list[date]:
    """Sessions in the closed range ``[start, end]``."""
    data = _sessions().days
    low = int(np.searchsorted(data, _day(start), side="left"))
    high = int(np.searchsorted(data, _day(end), side="right"))
    return [_date(day) for day in data[low:high]]


def next_session(value: date, n: int = 1) -> date:
    """The ``n``-th session strictly after ``value``."""
    data = _sessions().days
    return _date(data[int(np.searchsorted(data, _day(value), side="right")) + n - 1])


def previous_session(value: date, n: int = 1) -> date:
    """The ``n``-th session strictly before ``value``."""
    data = _sessions().days
    return _date(data[int(np.searchsorted(data, _day(value), side="left")) - n])


def session_on_or_after(value: date) -> date:
    """``value`` if it is a session, else the next one."""
    data = _sessions().days
    return _date(data[int(np.searchsorted(data, _day(value), side="left"))])


def session_close(value: date) -> datetime:
    """Close of ``value`` in UTC, early closes respected.

    A date that is not a session gets 16:00 New York, so a timestamp always exists.
    """
    data = _sessions()
    index = int(np.searchsorted(data.days, _day(value)))
    if index < len(data.days) and int(data.days[index]) == _day(value):
        seconds = int(data.closes_ns[index]) // 10**9
        return datetime.fromtimestamp(seconds, tz=UTC)
    return datetime.combine(value, _REGULAR_CLOSE, NEW_YORK).astimezone(UTC)


def month_ends(start: date, end: date) -> list[date]:
    """Last session of each calendar month, for months ending in ``[start, end]``."""
    found: list[date] = []
    days = sessions(start, next_session(end, 25))
    for here, after in pairwise(days):
        if here.month != after.month and here <= end:
            found.append(here)
    return found


def closes_on(dates: pl.Series) -> pl.Series:
    """Session close, in UTC, for each date; 16:00 New York where a date is no session."""
    data = _sessions()
    days = dates.cast(pl.Date).to_physical().cast(pl.Int64)
    filled = days.fill_null(0).to_numpy()
    index = np.minimum(np.searchsorted(data.days, filled), len(data.days) - 1)
    matched = data.days[index] == filled
    regular = pl.Series("date", filled, dtype=pl.Int64).cast(pl.Date)
    fallback = (
        pl.DataFrame({"date": regular})
        .select(
            pl.col("date")
            .cast(pl.Datetime("ns"))
            .dt.offset_by("16h")
            .dt.replace_time_zone("America/New_York", ambiguous="earliest")
            .dt.convert_time_zone("UTC")
        )
        .to_series()
        .dt.timestamp("ns")
        .to_numpy()
    )
    values = np.where(matched, data.closes_ns[index], fallback)
    result = pl.Series(dates.name, values, dtype=pl.Int64).cast(INSTANT)
    return result.set(days.is_null(), None) if days.null_count() else result


def close_after(dates: pl.Series, n: int = 1) -> pl.Series:
    """Close, in UTC, of the ``n``-th session strictly after each date."""
    data = _sessions()
    days = dates.cast(pl.Date).to_physical().cast(pl.Int64)
    filled = days.fill_null(0).to_numpy()
    index = np.searchsorted(data.days, filled, side="right") + n - 1
    if len(index) and int(index.max()) >= len(data.days):
        raise ValueError("date lies past the end of the trading calendar")
    result = pl.Series(dates.name, data.closes_ns[index], dtype=pl.Int64).cast(INSTANT)
    return result.set(days.is_null(), None) if days.null_count() else result


def local_date(instants: pl.Expr) -> pl.Expr:
    """New York calendar date of a UTC instant expression."""
    return instants.dt.convert_time_zone("America/New_York").dt.date()


def month_end_of(dates: pl.Series, ends: list[date]) -> pl.Series:
    """Map each date to the first month-end session on or after it."""
    table = np.array([_day(item) for item in ends], dtype=np.int64)
    days = dates.cast(pl.Date).to_physical().cast(pl.Int64).to_numpy()
    index = np.searchsorted(table, days, side="left")
    values = np.where(index < len(table), table[np.minimum(index, len(table) - 1)], -1)
    result = pl.Series(dates.name, values, dtype=pl.Int64)
    return result.set(result == -1, None).cast(pl.Date)
