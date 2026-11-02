"""FR-11, DQ-01, DQ-12: PitStore serves only what was public at the cutoff."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from eqpit import calendar
from eqpit.store import FutureAccessError, LabelAccessError, PitStore


@pytest.fixture
def asof() -> date:
    return date(2015, 6, 30)


def test_cutoff_is_the_close_in_utc(small_store: PitStore) -> None:
    assert small_store.cutoff(date(2015, 6, 30)) == datetime(2015, 6, 30, 20, 0, tzinfo=UTC)
    assert small_store.cutoff(date(2015, 1, 30)) == datetime(2015, 1, 30, 21, 0, tzinfo=UTC)
    assert small_store.cutoff(date(2015, 11, 27)) == datetime(2015, 11, 27, 18, 0, tzinfo=UTC)


def test_month_ends_are_last_sessions(small_store: PitStore) -> None:
    ends = small_store.month_ends(date(2015, 1, 1), date(2015, 6, 30))
    assert ends[0] == date(2015, 1, 30)
    assert ends[-1] == date(2015, 6, 30)
    assert len(ends) == 6


def test_every_table_row_seen_is_public(small_store: PitStore, asof: date) -> None:
    cutoff = small_store.cutoff(asof)
    prices = small_store.prices(asof, 252)
    fundamentals = small_store.fundamentals(asof, ["total_assets", "revenue"], history_periods=8)
    universe = small_store.universe(asof)
    securities = small_store.securities(asof)
    for frame in (prices, fundamentals, universe, securities):
        assert not frame.is_empty()
        assert frame["available_at"].max() <= cutoff


def test_a_fundamental_appears_only_after_the_lag(small_store: PitStore) -> None:
    table = pl.read_parquet(small_store.root / "fundamentals_pit" / "**" / "*.parquet")
    row = table.filter(pl.col("item").eq("total_assets")).row(0, named=True)
    public = row["available_at"].date()
    before = calendar.previous_session(public)
    seen = small_store.fundamentals(public, ["total_assets"], [row["company_id"]], 80)
    hidden = small_store.fundamentals(before, ["total_assets"], [row["company_id"]], 80)
    assert row["period_end"] in seen["period_end"].to_list()
    assert row["period_end"] not in hidden["period_end"].to_list()


def test_restated_values_replace_earlier_ones_only_from_their_own_time(
    small_store: PitStore,
) -> None:
    table = pl.read_parquet(small_store.root / "fundamentals_pit" / "**" / "*.parquet")
    restated = table.filter(pl.col("is_restatement"), pl.col("item").eq("revenue")).row(
        0, named=True
    )
    key = (
        pl.col("company_id").eq(restated["company_id"]),
        pl.col("item").eq("revenue"),
        pl.col("period_end").eq(restated["period_end"]),
        pl.col("period_type").eq(restated["period_type"]),
    )
    first = table.filter(*key, ~pl.col("is_restatement")).row(0, named=True)
    public = restated["available_at"].date()
    day_before = calendar.previous_session(public)

    def value_at(when: date) -> float:
        seen = small_store.fundamentals(
            when, ["revenue"], [restated["company_id"]], history_periods=200
        ).filter(*key)
        return float(seen["value"][0])

    assert value_at(day_before) == first["value"]
    assert value_at(public) == restated["value"]


def test_history_periods_keeps_the_latest(small_store: PitStore, asof: date) -> None:
    frame = small_store.fundamentals(asof, ["total_assets"], history_periods=2)
    counts = frame.group_by("company_id").len()
    assert counts["len"].max() == 2


def test_universe_hides_the_delisting_diagnostic(small_store: PitStore, asof: date) -> None:
    universe = small_store.universe(asof, 50)
    assert "delisted_after" not in universe.columns
    assert universe.height == 50
    assert universe["rank"].to_list() == list(range(1, 51))


def test_prices_window_counts_sessions(small_store: PitStore, asof: date) -> None:
    prices = small_store.prices(asof, 21)
    assert prices["date"].n_unique() == 21
    assert prices["date"].max() == asof


def test_adj_close_uses_the_as_of_share_basis(small_store: PitStore) -> None:
    actions = pl.read_parquet(small_store.root / "corp_actions" / "**" / "*.parquet")
    split = actions.filter(pl.col("action").eq("split"), pl.col("value") > 1).row(0, named=True)
    security = split["security_id"]
    after = calendar.next_session(split["ex_date"], 5)
    frame = small_store.prices(after, 15, [security]).sort("date")
    before_split = frame.filter(pl.col("date") < split["ex_date"]).row(-1, named=True)
    assert before_split["adj_close"] == pytest.approx(before_split["close"] / split["value"])
    assert frame["adj_close"][-1] == frame["close"][-1]


def test_a_split_after_asof_does_not_change_adj_close(small_store: PitStore) -> None:
    actions = pl.read_parquet(small_store.root / "corp_actions" / "**" / "*.parquet")
    split = actions.filter(pl.col("action").eq("split"), pl.col("value") > 1).row(0, named=True)
    before = calendar.previous_session(split["ex_date"], 2)
    frame = small_store.prices(before, 10, [split["security_id"]])
    assert frame["adj_close"].to_list() == frame["close"].to_list()


def test_securities_show_the_window_valid_at_asof(small_store: PitStore) -> None:
    master = pl.read_parquet(small_store.root / "securities" / "**" / "*.parquet")
    changed = master.filter(pl.col("valid_to").is_not_null()).row(0, named=True)
    seen = small_store.securities(changed["valid_to"]).filter(
        pl.col("security_id").eq(changed["security_id"])
    )
    assert seen["ticker"].to_list() == [changed["ticker"]]


def test_signal_view_refuses_labels(small_store: PitStore, asof: date) -> None:
    view = small_store.for_signals(asof)
    with pytest.raises(LabelAccessError):
        view.labels()


def test_signal_view_refuses_later_dates(small_store: PitStore, asof: date) -> None:
    view = small_store.for_signals(asof)
    later = date(2015, 7, 31)
    with pytest.raises(FutureAccessError):
        view.prices(later, 5)
    with pytest.raises(FutureAccessError):
        view.universe(later)
    with pytest.raises(FutureAccessError):
        view.fundamentals(later, ["revenue"])
    with pytest.raises(FutureAccessError):
        view.securities(later)
    assert not view.prices(asof, 5).is_empty()


def test_labels_are_separate(small_store: PitStore) -> None:
    labels = small_store.labels()
    forward = labels.forward_returns((1, 3))
    assert sorted(forward["h"].unique().to_list()) == [1, 3]
    monthly = labels.monthly_returns(date(2015, 1, 1), date(2015, 12, 31))
    assert monthly["month"].n_unique() == 12
    plain = labels.monthly_returns(date(2015, 1, 1), date(2015, 12, 31), include_delisting=False)
    assert plain.height <= monthly.height


def test_open_uses_the_data_dir(monkeypatch: pytest.MonkeyPatch, small_store: PitStore) -> None:
    root = small_store.root.parent
    target = root / "processed"
    target.mkdir(exist_ok=True)
    link = target / "pit"
    if not link.exists():
        link.mkdir()
        (link / "securities").mkdir()
    monkeypatch.setenv("EFP_DATA_DIR", str(root))
    assert PitStore.open().root == link


def test_open_refuses_a_missing_store(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PitStore.open(tmp_path)
