"""Equal-weight composite of sign-aligned z-scores."""

from __future__ import annotations

import polars as pl


def equal_weight(wide: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    """Mean of the available scores per stock.

    Args:
        wide: ``asof``, ``security_id`` and one z column per signal.
        names: the signal columns.
    """
    return wide.select("asof", "security_id", pl.mean_horizontal(names).alias("score"))
