"""The signal registry runs each signal on a gated view of the store."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import polars as pl
import pytest

from efp.signals.registry import SignalSpec, compute_signals, register, registered, unregister
from eqpit.store import FutureAccessError, LabelAccessError, PitStore

ASOF = date(2015, 6, 30)


def spec(signal_id: str) -> SignalSpec:
    return SignalSpec(signal_id, "size", -1, (), 1, "test", None)


@pytest.fixture
def clean() -> Iterator[None]:
    before = set(registered())
    yield
    for name in set(registered()) - before:
        unregister(name)


def test_a_registered_signal_produces_the_panel_columns(clean: None, small_store: PitStore) -> None:
    @register(spec("tmp_size"))
    def size(store: PitStore, asof: date) -> pl.DataFrame:
        universe = store.universe(asof, 20)
        return universe.select(
            "security_id",
            pl.col("mcap").log().alias("raw"),
            pl.col("available_at").alias("input_available_at"),
        )

    panel = compute_signals(small_store, ["tmp_size"], [ASOF])
    assert panel.columns == ["asof", "security_id", "signal", "raw", "input_available_at"]
    assert panel.height == 20
    assert panel["signal"].unique().to_list() == ["tmp_size"]


def test_duplicates_are_refused(clean: None) -> None:
    fn = register(spec("tmp_twice"))(lambda store, asof: pl.DataFrame())
    assert fn is not None
    with pytest.raises(ValueError, match="already registered"):
        register(spec("tmp_twice"))(lambda store, asof: pl.DataFrame())


def test_a_signal_cannot_reach_labels(clean: None, small_store: PitStore) -> None:
    @register(spec("tmp_labels"))
    def sneaky(store: PitStore, asof: date) -> pl.DataFrame:
        return store.labels().forward_returns()

    with pytest.raises(LabelAccessError):
        compute_signals(small_store, ["tmp_labels"], [ASOF])


def test_a_signal_cannot_ask_for_a_later_date(clean: None, small_store: PitStore) -> None:
    @register(spec("tmp_future"))
    def sneaky(store: PitStore, asof: date) -> pl.DataFrame:
        return store.universe(date(2016, 1, 29))

    with pytest.raises(FutureAccessError):
        compute_signals(small_store, ["tmp_future"], [ASOF])
