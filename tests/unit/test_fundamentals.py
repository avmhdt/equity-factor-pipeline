"""AC-W-06 and DQ-02 to DQ-06: fundamentals are public no earlier than their filing."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from eqpit.calendar import NEW_YORK, session_close
from eqpit.edgar.xbrl_map import xbrl_map_frame
from eqpit.pit.filings import PeriodEndAvailabilityError, build_filings, stamp_availability
from eqpit.pit.fundamentals import build_fundamentals
from eqpit.pit.policy import PitPolicy
from eqpit.schemas import INSTANT


def et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK).astimezone(UTC)


def filings(rows: list[tuple[str, str, date, date, datetime | None]]) -> pl.DataFrame:
    frame = pl.DataFrame(
        rows,
        schema={
            "accession": pl.String,
            "form": pl.String,
            "period_end": pl.Date,
            "filed": pl.Date,
            "acceptance_dt": INSTANT,
        },
        orient="row",
    )
    return build_filings(frame.with_columns(pl.lit(1, dtype=pl.Int64).alias("cik")), PitPolicy())


def facts(rows: list[tuple[str, date | None, date, float, str, str, date, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "tag": pl.String,
            "start": pl.Date,
            "end": pl.Date,
            "value": pl.Float64,
            "accession": pl.String,
            "form": pl.String,
            "filed": pl.Date,
            "fp": pl.String,
        },
        orient="row",
    ).with_columns(
        pl.lit(1, dtype=pl.Int64).alias("cik"),
        pl.lit("CIK:1").alias("company_id"),
        pl.lit("USD").alias("unit"),
    )


END = date(2023, 12, 31)
YEAR = date(2023, 1, 1)
FILED = date(2024, 2, 1)
ACCEPTED = et(2024, 2, 1, 10)
NEXT_CLOSE = session_close(date(2024, 2, 2))


def annual_filing(name: str = "a") -> tuple[str, str, date, date, datetime]:
    return (name, "10-K", END, FILED, ACCEPTED)


def test_after_hours_acceptance_misses_that_days_cutoff() -> None:
    accepted = et(2024, 2, 1, 16, 5)
    built = filings([("a", "10-K", END, FILED, accepted)])
    assert built["available_at"][0] == NEXT_CLOSE
    assert built["available_at"][0] > session_close(FILED)
    assert built["pit_method"][0] == "acceptance"


def test_morning_acceptance_still_waits_for_the_next_close() -> None:
    assert filings([annual_filing()])["available_at"][0] == NEXT_CLOSE


def test_holiday_skips_to_the_next_session() -> None:
    accepted = et(2024, 7, 3, 12)
    built = filings([("a", "10-Q", date(2024, 6, 30), date(2024, 7, 3), accepted)])
    assert built["available_at"][0] == session_close(date(2024, 7, 5))


def test_early_close_is_respected() -> None:
    accepted = et(2023, 11, 22, 12)
    built = filings([("a", "10-Q", date(2023, 9, 30), date(2023, 11, 22), accepted)])
    assert built["available_at"][0] == datetime(2023, 11, 24, 18, 0, tzinfo=UTC)


def test_zero_extra_lag_is_the_acceptance_instant() -> None:
    frame = pl.DataFrame(
        {"form": ["10-K"], "period_end": [END], "acceptance_dt": [ACCEPTED]},
        schema_overrides={"acceptance_dt": INSTANT},
    )
    built = stamp_availability(frame, PitPolicy(extra_lag_trading_days=0))
    assert built["available_at"][0] == ACCEPTED


def test_fallback_order() -> None:
    frame = pl.DataFrame(
        {
            "form": ["10-K", "10-K", "10-K", "10-Q"],
            "period_end": [END, END, END, date(2023, 9, 30)],
            "acceptance_dt": [ACCEPTED, None, None, None],
            "filed": [FILED, FILED, None, None],
            "rdq": [None, None, FILED, None],
        },
        schema_overrides={"acceptance_dt": INSTANT, "filed": pl.Date, "rdq": pl.Date},
    )
    built = stamp_availability(frame.head(3), PitPolicy())
    assert built["pit_method"].to_list() == ["acceptance", "filedate", "rdq"]
    assert built["available_at"].to_list() == [NEXT_CLOSE] * 3


@pytest.mark.parametrize(
    ("form", "period_end", "expected"),
    [
        ("10-K", date(2003, 12, 31), date(2004, 6, 30)),
        ("10-Q", date(2003, 9, 30), date(2004, 1, 30)),
    ],
)
def test_conservative_lag_when_no_date_exists(form: str, period_end: date, expected: date) -> None:
    frame = pl.DataFrame({"form": [form], "period_end": [period_end]})
    built = stamp_availability(frame, PitPolicy())
    assert built["pit_method"][0] == "conservative_lag"
    assert built["available_at"][0] == session_close(expected)


def test_availability_on_the_period_end_fails_the_build() -> None:
    with pytest.raises(PeriodEndAvailabilityError):
        filings([("a", "10-K", date(2024, 2, 5), FILED, ACCEPTED)])


def test_other_forms_are_left_out() -> None:
    built = filings([annual_filing("a"), ("b", "8-K", END, FILED, ACCEPTED)])
    assert built["accession"].to_list() == ["a"]


def fundamentals(fact_rows: list, filing_rows: list | None = None) -> pl.DataFrame:
    table = filings(filing_rows if filing_rows is not None else [annual_filing()])
    return build_fundamentals(facts(fact_rows), table, PitPolicy())


def test_a_restatement_is_a_new_row_at_its_own_time() -> None:
    late = et(2024, 3, 11, 9)
    built = fundamentals(
        [
            ("Revenues", YEAR, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Revenues", YEAR, END, 90.0, "b", "10-K/A", date(2024, 3, 11), "FY"),
        ],
        [annual_filing("a"), ("b", "10-K/A", END, date(2024, 3, 11), late)],
    ).sort("available_at")
    assert built["value"].to_list() == [100.0, 90.0]
    assert built["is_restatement"].to_list() == [False, True]
    assert built["available_at"].to_list() == [NEXT_CLOSE, session_close(date(2024, 3, 12))]


def test_a_repeated_comparative_adds_no_row() -> None:
    later = et(2025, 2, 3, 9)
    built = fundamentals(
        [
            ("Assets", None, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Assets", None, END, 100.0, "b", "10-K", date(2025, 2, 3), "FY"),
            ("Assets", None, date(2024, 12, 31), 120.0, "b", "10-K", date(2025, 2, 3), "FY"),
        ],
        [annual_filing("a"), ("b", "10-K", date(2024, 12, 31), date(2025, 2, 3), later)],
    )
    period = built.filter(pl.col("period_end").eq(END))
    assert period.height == 1
    assert period["accession"][0] == "a"
    assert period["available_at"][0] == NEXT_CLOSE


def test_q4_is_full_year_minus_nine_months() -> None:
    nine = date(2023, 9, 30)
    built = fundamentals(
        [
            ("Revenues", YEAR, nine, 70.0, "q", "10-Q", date(2023, 11, 1), "Q3"),
            ("Revenues", YEAR, END, 100.0, "a", "10-K", FILED, "FY"),
        ],
        [("q", "10-Q", nine, date(2023, 11, 1), et(2023, 11, 1, 9)), annual_filing()],
    )
    quarter = built.filter(pl.col("period_type").eq("3m"))
    assert quarter["value"].to_list() == [30.0]
    assert quarter["fiscal_period"].to_list() == ["Q4"]
    assert quarter["available_at"].to_list() == [NEXT_CLOSE]
    assert quarter["source_tag"][0].startswith("derived:12m-9m")


def test_a_reported_quarter_is_not_overwritten_by_a_derived_one() -> None:
    nine = date(2023, 9, 30)
    built = fundamentals(
        [
            ("Revenues", YEAR, nine, 70.0, "q", "10-Q", date(2023, 11, 1), "Q3"),
            ("Revenues", YEAR, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Revenues", date(2023, 10, 1), END, 31.0, "a", "10-K", FILED, "Q4"),
        ],
        [("q", "10-Q", nine, date(2023, 11, 1), et(2023, 11, 1, 9)), annual_filing()],
    )
    assert built.filter(pl.col("period_type").eq("3m"))["value"].to_list() == [31.0]


def test_year_to_date_cash_flow_steps_down_to_quarters() -> None:
    six, three = date(2023, 6, 30), date(2023, 3, 31)
    built = (
        fundamentals(
            [
                (
                    "NetCashProvidedByUsedInOperatingActivities",
                    YEAR,
                    three,
                    10.0,
                    "q1",
                    "10-Q",
                    date(2023, 5, 1),
                    "Q1",
                ),
                (
                    "NetCashProvidedByUsedInOperatingActivities",
                    YEAR,
                    six,
                    25.0,
                    "q2",
                    "10-Q",
                    date(2023, 8, 1),
                    "Q2",
                ),
            ],
            [
                ("q1", "10-Q", three, date(2023, 5, 1), et(2023, 5, 1, 9)),
                ("q2", "10-Q", six, date(2023, 8, 1), et(2023, 8, 1, 9)),
            ],
        )
        .filter(pl.col("period_type").eq("3m"))
        .sort("period_end")
    )
    assert built["value"].to_list() == [10.0, 15.0]
    assert built["fiscal_period"].to_list() == ["Q1", "Q2"]


def test_a_restated_nine_months_re_derives_the_quarter_at_its_own_time() -> None:
    nine = date(2023, 9, 30)
    built = fundamentals(
        [
            ("Revenues", YEAR, nine, 70.0, "q", "10-Q", date(2023, 11, 1), "Q3"),
            ("Revenues", YEAR, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Revenues", YEAR, nine, 72.0, "r", "10-Q/A", date(2024, 3, 1), "Q3"),
        ],
        [
            ("q", "10-Q", nine, date(2023, 11, 1), et(2023, 11, 1, 9)),
            annual_filing(),
            ("r", "10-Q/A", nine, date(2024, 3, 1), et(2024, 3, 1, 9)),
        ],
    )
    quarter = built.filter(pl.col("period_type").eq("3m")).sort("available_at")
    assert quarter["value"].to_list() == [30.0, 28.0]
    assert quarter["is_restatement"].to_list() == [False, True]


def test_the_first_tag_in_the_map_wins() -> None:
    built = fundamentals(
        [
            ("Revenues", YEAR, END, 90.0, "a", "10-K", FILED, "FY"),
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                YEAR,
                END,
                100.0,
                "a",
                "10-K",
                FILED,
                "FY",
            ),
        ]
    )
    assert built["value"].to_list() == [100.0]


def test_one_tag_can_feed_two_items() -> None:
    built = fundamentals([("NetIncomeLoss", YEAR, END, 5.0, "a", "10-K", FILED, "FY")])
    assert sorted(built["item"].to_list()) == ["income_before_extra", "net_income"]
    assert "NetIncomeLoss" in xbrl_map_frame()["tag"].to_list()


def test_book_equity_adds_deferred_taxes_and_removes_preferred() -> None:
    built = fundamentals(
        [
            ("StockholdersEquity", None, END, 100.0, "a", "10-K", FILED, "FY"),
            ("DeferredIncomeTaxLiabilitiesNet", None, END, 7.0, "a", "10-K", FILED, "FY"),
            ("PreferredStockValue", None, END, 2.0, "a", "10-K", FILED, "FY"),
        ]
    ).filter(pl.col("item").eq("book_equity"))
    assert built["value"].to_list() == [105.0]


def test_comparatives_are_labelled_from_their_own_filing() -> None:
    three = date(2023, 3, 31)
    built = fundamentals(
        [
            ("Assets", None, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Assets", None, three, 90.0, "a", "10-K", FILED, "FY"),
            ("Revenues", date(2023, 10, 1), END, 9.0, "a", "10-K", FILED, "FY"),
        ]
    ).sort("period_end", "item")
    assert built["fiscal_period"].to_list() == ["Q1", "Q4", "FY"]


def test_odd_durations_are_dropped() -> None:
    built = fundamentals([("Revenues", date(2023, 5, 1), END, 9.0, "a", "10-K", FILED, "FY")])
    assert built.is_empty()


def test_facts_dated_after_their_filing_are_dropped() -> None:
    built = fundamentals(
        [
            ("Assets", None, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Assets", None, date(2030, 1, 1), 1.0, "a", "10-K", FILED, "FY"),
        ]
    )
    assert built["period_end"].to_list() == [END]


def test_facts_without_a_known_filing_fall_back_on_their_filing_date() -> None:
    built = fundamentals(
        [("Assets", None, END, 100.0, "missing", "10-K", FILED, "FY")], [annual_filing()]
    )
    assert built["pit_method"].to_list() == ["filedate"]
    assert built["available_at"].to_list() == [NEXT_CLOSE]


def test_never_before_acceptance_or_the_period_end() -> None:
    built = fundamentals(
        [
            ("Assets", None, END, 100.0, "a", "10-K", FILED, "FY"),
            ("Revenues", YEAR, END, 100.0, "a", "10-K", FILED, "FY"),
        ]
    )
    assert (built["available_at"] >= ACCEPTED).all()
    assert (built["available_at"].dt.date() > built["period_end"]).all()
