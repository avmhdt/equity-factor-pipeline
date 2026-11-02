"""Size signal."""

from __future__ import annotations

from datetime import date

import polars as pl

from efp.signals.common import members, to_signal
from efp.signals.registry import SignalSpec, register
from eqpit.store import PitStore


@register(SignalSpec("size", "size", -1, (), 1, "Banz 1981", "Size"))
def size(store: PitStore, asof: date) -> pl.DataFrame:
    """Log market equity at the as-of date."""
    return to_signal(members(store, asof), pl.col("me").log(), ("me_at",))
