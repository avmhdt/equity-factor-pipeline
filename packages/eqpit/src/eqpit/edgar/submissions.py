"""Parse EDGAR submissions JSON into filing rows (DQ-04)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from eqpit.ids import cik10
from eqpit.pit.filings import FORMS
from eqpit.schemas import INSTANT

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"

_EMPTY: dict[str, Any] = {
    "cik": pl.Int64,
    "accession": pl.String,
    "form": pl.String,
    "period_end": pl.Date,
    "filed": pl.Date,
    "acceptance_dt": INSTANT,
}


@dataclass(frozen=True)
class CompanyMeta:
    """What the submissions header says about an issuer."""

    cik: int
    name: str
    sic: int | None
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]


def submissions_url(cik: int | str) -> str:
    """URL of a company's main submissions file."""
    return f"{SUBMISSIONS_BASE}/CIK{cik10(cik)}.json"


def company_meta(payload: Mapping[str, Any]) -> CompanyMeta:
    """Read the header of a main submissions file."""
    sic = payload.get("sic") or ""
    return CompanyMeta(
        cik=int(payload["cik"]),
        name=str(payload.get("name", "")),
        sic=int(sic) if str(sic).isdigit() else None,
        tickers=tuple(payload.get("tickers") or ()),
        exchanges=tuple(item for item in (payload.get("exchanges") or ()) if item),
    )


def overflow_pages(payload: Mapping[str, Any], since: date) -> list[str]:
    """Names of older-filing pages that reach back to ``since`` or later.

    The main file holds only the recent filings; older ones sit in numbered pages, and a
    filing missing here would silently lose its acceptance time.
    """
    pages: Sequence[Mapping[str, Any]] = payload.get("filings", {}).get("files", ())
    return [
        str(page["name"]) for page in pages if date.fromisoformat(str(page["filingTo"])) >= since
    ]


def parse_filing_block(
    cik: int, block: Mapping[str, Any], forms: Sequence[str] = FORMS
) -> pl.DataFrame:
    """Rows for the wanted forms from ``filings.recent`` or an overflow page.

    ``acceptanceDateTime`` ends in ``Z`` and is true UTC. On 2009, 2012, 2015 and 2026 filings
    it matches the ET time on the EDGAR filing index once converted (see docs/pit.md).
    """
    listed = block.get("form") or []
    if not listed:
        return pl.DataFrame(schema=_EMPTY)
    frame = pl.DataFrame(
        {
            "accession": block["accessionNumber"],
            "form": listed,
            "period_end": block["reportDate"],
            "filed": block["filingDate"],
            "acceptance_dt": block["acceptanceDateTime"],
        },
        schema={name: pl.String for name in _EMPTY if name != "cik"},
    )
    return (
        frame.filter(pl.col("form").is_in(list(forms)))
        .with_columns(
            pl.lit(cik, dtype=pl.Int64).alias("cik"),
            pl.col("period_end").replace("", None).str.to_date("%Y-%m-%d", strict=False),
            pl.col("filed").str.to_date("%Y-%m-%d"),
            pl.col("acceptance_dt")
            .replace("", None)
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%.fZ", time_zone="UTC", strict=False)
            .cast(INSTANT),
        )
        .select(list(_EMPTY))
    )
