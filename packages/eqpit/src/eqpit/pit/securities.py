"""Security master: permanent id, slowly changing attributes (FR-04)."""

from __future__ import annotations

import polars as pl

from eqpit.industries import ff_series
from eqpit.schemas import SECURITIES, conform


def build_securities(raw: pl.DataFrame) -> pl.DataFrame:
    """Add industry codes and ``available_at`` (00:00 New York on ``valid_from``).

    Args:
        raw: one row per validity window, with ``security_id``, ``company_id``, ``ticker``,
            ``name``, ``exchange``, ``share_type``, ``sic``, ``valid_from`` and ``valid_to``.
    """
    enriched = raw.with_columns(
        ff_series(raw["sic"], "ff49").alias("ff49"),
        ff_series(raw["sic"], "ff12").alias("ff12"),
        pl.col("valid_from")
        .cast(pl.Datetime("ns"))
        .dt.replace_time_zone("America/New_York", ambiguous="earliest")
        .dt.convert_time_zone("UTC")
        .alias("available_at"),
    )
    return conform(enriched, SECURITIES).sort("security_id", "valid_from")
