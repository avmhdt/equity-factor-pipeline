"""Identifiers, industries, schemas and the XBRL map."""

from __future__ import annotations

import polars as pl
import pytest

from eqpit import ids
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, all_tags, xbrl_map_frame
from eqpit.industries import ff_industry, ff_series, parse_siccodes
from eqpit.schemas import SECURITIES, TABLES, SchemaError, conform


def test_identifiers() -> None:
    assert ids.crsp_id(14593) == "CRSP:14593"
    assert ids.sharadar_id(199059) == "SHR:199059"
    assert ids.edgar_id(320193) == "EDGAR:320193:common"
    assert ids.edgar_id(320193, "B") == "EDGAR:320193:B"
    assert ids.synthetic_id(7) == "SYN:7"
    assert ids.company_from_cik(320193) == "CIK:320193"
    assert ids.cik10(320193) == "0000320193"
    assert ids.cik10("320193") == "0000320193"


def test_accession_form() -> None:
    assert ids.is_accession("0000320193-24-000123")
    assert not ids.is_accession("320193-24-123")


@pytest.mark.parametrize(
    ("sic", "scheme", "industry"),
    [
        (3571, "ff49", 35),
        (2834, "ff49", 13),
        (6021, "ff49", 45),
        (7372, "ff49", 36),
        (3571, "ff12", 6),
        (6021, "ff12", 11),
    ],
)
def test_known_sic_codes(sic: int, scheme: str, industry: int) -> None:
    assert ff_industry(sic, scheme) == industry  # type: ignore[arg-type]


def test_unmapped_sics_fall_to_other_and_bad_ones_to_none() -> None:
    assert ff_industry(9998, "ff49") == 49
    assert ff_industry(9998, "ff12") == 12
    assert ff_industry(None) is None
    assert ff_industry(10_000) is None


def test_vectorised_lookup() -> None:
    result = ff_series(pl.Series([3571, None, 20000], dtype=pl.Int32), "ff49")
    assert result.to_list() == [35, None, None]


def test_parser_reads_ranges_under_their_industry() -> None:
    text = (
        " 1 Agric  Agriculture\n          0100-0199 Crops\n\n"
        " 2 Food   Food\n          2000-2009 x\n"
    )
    assert parse_siccodes(text) == [(1, 100, 199), (2, 2000, 2009)]


def test_conform_orders_casts_and_fills() -> None:
    frame = pl.DataFrame({"company_id": ["c"], "security_id": ["s"], "sic": [3571]})
    built = conform(frame, SECURITIES)
    assert built.columns == list(SECURITIES)
    assert built["sic"].dtype == pl.Int16
    assert built["ticker"].to_list() == [None]


def test_conform_needs_the_key() -> None:
    with pytest.raises(SchemaError, match="missing key"):
        conform(pl.DataFrame({"company_id": ["c"]}), SECURITIES)


def test_every_table_has_a_schema() -> None:
    assert set(TABLES) >= {"securities", "prices_daily", "fundamentals_pit", "forward_returns"}


def test_map_frame_has_one_row_per_tag_and_item() -> None:
    frame = xbrl_map_frame()
    assert frame.height == sum(len(tags) for tags in DEFAULT_XBRL_MAP.values())
    first = frame.filter(pl.col("item").eq("revenue"), pl.col("priority").eq(0))
    assert first["tag"].to_list() == [DEFAULT_XBRL_MAP["revenue"][0]]
    assert "Assets" in all_tags()
