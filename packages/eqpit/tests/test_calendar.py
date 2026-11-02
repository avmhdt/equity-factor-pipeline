"""DQ-17: sessions, month ends and closes."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from eqpit import calendar


def test_sessions_skip_weekends_and_holidays() -> None:
    days = calendar.sessions(date(2024, 7, 3), date(2024, 7, 8))
    assert days == [date(2024, 7, 3), date(2024, 7, 5), date(2024, 7, 8)]


def test_is_session() -> None:
    assert calendar.is_session(date(2024, 7, 3))
    assert not calendar.is_session(date(2024, 7, 4))
    assert not calendar.is_session(date(2024, 7, 6))


def test_next_and_previous_session() -> None:
    assert calendar.next_session(date(2024, 7, 3)) == date(2024, 7, 5)
    assert calendar.next_session(date(2024, 7, 4)) == date(2024, 7, 5)
    assert calendar.next_session(date(2024, 7, 3), 2) == date(2024, 7, 8)
    assert calendar.previous_session(date(2024, 7, 5)) == date(2024, 7, 3)
    assert calendar.previous_session(date(2024, 7, 8), 2) == date(2024, 7, 3)
    assert calendar.session_on_or_after(date(2024, 7, 4)) == date(2024, 7, 5)
    assert calendar.session_on_or_after(date(2024, 7, 5)) == date(2024, 7, 5)


def test_month_ends_are_last_sessions() -> None:
    assert calendar.month_ends(date(2024, 1, 1), date(2024, 4, 30)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 28),
        date(2024, 4, 30),
    ]


def test_month_ends_stop_at_end_and_drop_partial_months() -> None:
    assert calendar.month_ends(date(2024, 1, 1), date(2024, 3, 15)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
    ]


@pytest.mark.parametrize(
    ("day", "hour"),
    [
        (date(2024, 1, 31), 21),
        (date(2024, 7, 31), 20),
        (date(2024, 7, 3), 17),
        (date(2023, 11, 24), 18),
    ],
)
def test_closes_follow_daylight_saving_and_early_closes(day: date, hour: int) -> None:
    assert calendar.session_close(day) == datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_a_non_session_gets_four_pm_new_york() -> None:
    assert calendar.session_close(date(2024, 7, 4)) == datetime(2024, 7, 4, 20, tzinfo=UTC)
    assert calendar.session_close(date(2024, 1, 6)) == datetime(2024, 1, 6, 21, tzinfo=UTC)


def test_vectorised_closes_match_the_scalar_ones() -> None:
    dates = pl.Series("d", [date(2024, 1, 31), date(2024, 7, 3), date(2024, 7, 4), None])
    result = calendar.closes_on(dates).to_list()
    assert result[:3] == [calendar.session_close(day) for day in dates.to_list()[:3]]
    assert result[3] is None


def test_close_after_counts_sessions_strictly_after() -> None:
    dates = pl.Series("d", [date(2024, 7, 3), date(2024, 7, 4), None])
    result = calendar.close_after(dates, 1).to_list()
    assert result[0] == calendar.session_close(date(2024, 7, 5))
    assert result[1] == calendar.session_close(date(2024, 7, 5))
    assert result[2] is None
    two = calendar.close_after(dates, 2).to_list()
    assert two[0] == calendar.session_close(date(2024, 7, 8))


def test_close_after_refuses_dates_past_the_calendar() -> None:
    with pytest.raises(ValueError, match="past the end"):
        calendar.close_after(pl.Series("d", [date(2090, 1, 1)]), 1)


def test_local_date_uses_new_york() -> None:
    frame = pl.DataFrame(
        {"at": [datetime(2024, 2, 2, 2, 0, tzinfo=UTC)]},
        schema={"at": pl.Datetime("ns", "UTC")},
    )
    assert frame.select(calendar.local_date(pl.col("at"))).item() == date(2024, 2, 1)


def test_month_end_of_maps_forward_and_leaves_later_dates_null() -> None:
    ends = [date(2024, 1, 31), date(2024, 2, 29)]
    dates = pl.Series(
        "d", [date(2024, 1, 2), date(2024, 1, 31), date(2024, 2, 1), date(2024, 3, 1)]
    )
    assert calendar.month_end_of(dates, ends).to_list() == [
        date(2024, 1, 31),
        date(2024, 1, 31),
        date(2024, 2, 29),
        None,
    ]
