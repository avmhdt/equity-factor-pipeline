"""Parse EDGAR companyfacts JSON into long-format facts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

import polars as pl

from eqpit.ids import cik10

COMPANYFACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"

_TAXONOMIES = ("us-gaap", "dei")
_UNITS = ("USD", "shares", "USD/shares")
_SCHEMA = {
    "cik": pl.Int64,
    "tag": pl.String,
    "start": pl.String,
    "end": pl.String,
    "value": pl.Float64,
    "unit": pl.String,
    "accession": pl.String,
    "form": pl.String,
    "filed": pl.String,
    "fp": pl.String,
}


def companyfacts_url(cik: int | str) -> str:
    """URL of a company's facts file."""
    return f"{COMPANYFACTS_BASE}/CIK{cik10(cik)}.json"


def parse_companyfacts(payload: Mapping[str, Any], tags: Collection[str]) -> pl.DataFrame:
    """One row per reported fact for the wanted tags.

    Each fact keeps the accession it came from, so a later comparative stays a separate row
    from the original report. Facts in other units are skipped.
    """
    cik = int(payload["cik"])
    rows: list[tuple[object, ...]] = []
    facts = payload.get("facts", {})
    for taxonomy in _TAXONOMIES:
        concepts = facts.get(taxonomy, {})
        for tag in tags:
            concept = concepts.get(tag)
            if concept is None:
                continue
            for unit in _UNITS:
                for fact in concept.get("units", {}).get(unit, ()):
                    rows.append(
                        (
                            cik,
                            tag,
                            fact.get("start"),
                            fact["end"],
                            float(fact["val"]),
                            unit,
                            fact["accn"],
                            fact.get("form"),
                            fact.get("filed"),
                            fact.get("fp"),
                        )
                    )
    frame = pl.DataFrame(rows, schema=_SCHEMA, orient="row")
    return frame.with_columns(
        pl.col(name).str.to_date("%Y-%m-%d") for name in ("start", "end", "filed")
    )
