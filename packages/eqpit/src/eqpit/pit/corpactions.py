"""Corporate actions: vendor values mapped to one vocabulary, or the build fails."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from eqpit.calendar import closes_on
from eqpit.schemas import CORP_ACTIONS, conform

CANONICAL_ACTIONS = frozenset(
    {
        "split",
        "cash_dividend",
        "stock_dividend",
        "ticker_change",
        "spinoff",
        "merger",
        "delist",
    }
)


class UnmappedActionError(ValueError):
    """A vendor action type has no canonical mapping (DQ report must show zero of these)."""


def build_corp_actions(raw: pl.DataFrame, mapping: Mapping[str, str] | None = None) -> pl.DataFrame:
    """Map vendor action values and stamp ``available_at`` at the close of ``ex_date``.

    Args:
        raw: rows with ``security_id``, ``ex_date``, ``action``, ``value``, ``new_ticker``.
        mapping: vendor value to canonical value; None means already canonical.
    """
    table = dict(mapping) if mapping is not None else {name: name for name in CANONICAL_ACTIONS}
    unmapped = sorted(set(raw["action"].drop_nulls().to_list()) - set(table))
    if unmapped:
        raise UnmappedActionError(f"unmapped corporate action types: {unmapped}")
    invalid = sorted(set(table.values()) - CANONICAL_ACTIONS)
    if invalid:
        raise UnmappedActionError(f"mapping targets outside the vocabulary: {invalid}")
    keys = list(table)
    normalised = raw.with_columns(
        pl.col("action").replace_strict(keys, [table[key] for key in keys], return_dtype=pl.String),
        closes_on(raw["ex_date"]).alias("available_at"),
    )
    return conform(normalised, CORP_ACTIONS).sort("security_id", "ex_date", "action")
