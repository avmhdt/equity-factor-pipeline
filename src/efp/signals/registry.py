"""Signal registry and the only route that runs a signal function."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import polars as pl

from eqpit.store import PitStore

Family = Literal["value", "momentum", "quality", "investment", "low_risk", "size"]
SignalFn = Callable[[PitStore, date], pl.DataFrame]
"""Takes the as-of view of the store and a rebalance date.

Returns ``security_id``, ``raw`` and ``input_available_at``, the latest ``available_at`` among
the rows that went into that value.
"""


@dataclass(frozen=True)
class SignalSpec:
    """Registry entry for one signal."""

    id: str
    family: Family
    direction: Literal[1, -1]
    items: tuple[str, ...]
    price_lookback_days: int
    reference: str
    osap_target: str | None


_REGISTRY: dict[str, tuple[SignalSpec, SignalFn]] = {}


def register(spec: SignalSpec) -> Callable[[SignalFn], SignalFn]:
    """Decorator that adds a signal function to the registry."""

    def add(fn: SignalFn) -> SignalFn:
        if spec.id in _REGISTRY:
            raise ValueError(f"signal {spec.id} is already registered")
        _REGISTRY[spec.id] = (spec, fn)
        return fn

    return add


def unregister(signal_id: str) -> None:
    """Remove a signal, for tests that register temporary probes."""
    del _REGISTRY[signal_id]


def registered() -> tuple[str, ...]:
    """Ids of all registered signals, sorted."""
    return tuple(sorted(_REGISTRY))


def compute_signals(store: PitStore, ids: Sequence[str], dates: Sequence[date]) -> pl.DataFrame:
    """Run signals over rebalance dates.

    Each call gets ``store.for_signals(asof)``: forward returns raise, and so does asking for
    any date past the one being computed.

    Returns:
        Rows of ``asof``, ``security_id``, ``signal``, ``raw`` and ``input_available_at``.
    """
    frames: list[pl.DataFrame] = []
    for signal_id in ids:
        _, fn = _REGISTRY[signal_id]
        for asof in dates:
            computed = fn(store.for_signals(asof), asof)
            frames.append(
                computed.select(
                    pl.lit(asof, dtype=pl.Date).alias("asof"),
                    "security_id",
                    pl.lit(signal_id).alias("signal"),
                    pl.col("raw").cast(pl.Float64),
                    pl.col("input_available_at").cast(pl.Datetime("ns", "UTC")),
                )
            )
    panel = pl.concat(frames, how="vertical")
    return panel.sort("signal", "asof", "security_id")
