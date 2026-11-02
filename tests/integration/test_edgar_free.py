"""FR-01, FR-07, DQ-16: the free path, end to end through a mock SEC."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
import yaml

from efp.config import load_xbrl_map
from eqpit.edgar.client import EdgarClient
from eqpit.edgar.companyfacts import companyfacts_url, parse_companyfacts
from eqpit.edgar.submissions import (
    company_meta,
    overflow_pages,
    parse_filing_block,
    submissions_url,
)
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, all_tags
from eqpit.pit.build import DEV_ONLY_LABEL, build_pit, read_pit_manifest
from eqpit.pit.verify import run_checks
from eqpit.providers import stooq
from eqpit.providers.base import ProviderUnavailableError
from eqpit.providers.edgar_free import (
    BULK_COMPANYFACTS_URL,
    BULK_SUBMISSIONS_URL,
    TICKERS_URL,
    EdgarFreeProvider,
)
from eqpit.snapshot import read_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures"
AGENT = "Test Person test.person@example.com"
ROOT = Path(__file__).parents[2]
STOOQ_HEADER = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def tickers_payload() -> dict:
    return {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [
            [111, "After Hours Inc.", "AHI", "Nasdaq"],
            [111, "After Hours Inc.", "AHIW", "Nasdaq"],
            [222, "Year To Date Corp.", "YTD", "NYSE"],
            [333, "Over The Counter", "OTCX", "OTC"],
        ],
    }


def sec_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    companies = load("companyfacts_3co.json")
    if url == TICKERS_URL:
        return httpx.Response(200, json=tickers_payload())
    if url == submissions_url(111):
        return httpx.Response(200, json=load("submissions_after_hours.json"))
    if url == submissions_url(222):
        payload = load("submissions_after_hours.json")
        payload["cik"] = "0000000222"
        payload["filings"]["files"] = []
        return httpx.Response(200, json=payload)
    if url.endswith("CIK0000000111-submissions-001.json"):
        return httpx.Response(200, json=load("submissions_overflow.json"))
    if url == companyfacts_url(111):
        return httpx.Response(200, json=companies[0])
    if url == companyfacts_url(222):
        return httpx.Response(404)
    raise AssertionError(f"unexpected request {url}")


def stooq_tree(root: Path) -> Path:
    folder = root / "d_us_txt" / "data" / "daily" / "us" / "nasdaq stocks" / "1"
    folder.mkdir(parents=True)
    (root / "d_us_txt" / "data" / "daily" / "us" / "nyse etfs").mkdir(parents=True)
    rows = [
        (date(2008, 12, 31), 1.0),
        (date(2024, 1, 31), 10.0),
        (date(2024, 2, 1), 10.0),
        (date(2024, 2, 2), 11.0),
        (date(2024, 2, 5), 11.0),
        (date(2024, 2, 29), 12.0),
        (date(2024, 3, 28), 13.0),
        (date(2024, 5, 31), 14.0),
    ]
    for ticker in ("ahi", "ytd"):
        body = "".join(
            f"{ticker.upper()}.US,D,{day:%Y%m%d},000000,{close},{close},{close},{close},1000,0\n"
            for day, close in rows
        )
        (folder / f"{ticker}.us.txt").write_text(STOOQ_HEADER + body, encoding="utf-8")
    (folder.parents[1] / "nyse etfs" / "spy.us.txt").write_text(STOOQ_HEADER, encoding="utf-8")
    return root


@pytest.fixture
def pulled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> EdgarFreeProvider:
    monkeypatch.setenv("STOOQ_DIR", str(stooq_tree(tmp_path / "stooq")))
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "raw"
    provider = EdgarFreeProvider(root, max_companies=2)
    client = EdgarClient(AGENT, transport=httpx.MockTransport(sec_handler))
    with client:
        provider.pull([], date(2009, 1, 1), date(2024, 12, 31), root, client=client)
    return provider


def test_filing_rows_keep_after_hours_acceptance_in_utc() -> None:
    payload = load("submissions_after_hours.json")
    frame = parse_filing_block(111, payload["filings"]["recent"]).sort("accession")
    assert frame["form"].to_list() == ["10-K", "10-K/A", "10-Q"]
    late = frame.row(1, named=True)
    assert late["acceptance_dt"] == datetime(2024, 2, 1, 22, 45, 10, tzinfo=UTC)
    assert late["filed"] == date(2024, 2, 2)


def test_an_empty_block_gives_an_empty_frame() -> None:
    assert parse_filing_block(111, {"form": []}).is_empty()


def test_header_fields() -> None:
    meta = company_meta(load("submissions_after_hours.json"))
    assert (meta.cik, meta.sic, meta.tickers, meta.exchanges) == (111, 3571, ("AHI",), ("Nasdaq",))
    assert company_meta({"cik": "5", "sic": ""}).sic is None


def test_overflow_pages_stop_at_the_xbrl_era() -> None:
    pages = overflow_pages(load("submissions_after_hours.json"), date(2009, 1, 1))
    assert pages == ["CIK0000000111-submissions-001.json"]


def test_facts_keep_each_report_and_skip_unknown_tags_and_units() -> None:
    companies = load("companyfacts_3co.json")
    frame = parse_companyfacts(companies[0], all_tags())
    assert frame.filter(pl.col("tag").eq("Revenues")).height == 3
    assert frame.filter(pl.col("tag").eq("UnknownConcept")).is_empty()
    assert frame.filter(pl.col("unit").eq("shares")).height == 1
    assert parse_companyfacts(companies[2], all_tags()).is_empty()


def test_the_yaml_map_matches_the_default() -> None:
    loaded = load_xbrl_map(ROOT / "configs" / "xbrl_map.yaml")
    assert loaded == {key: list(value) for key, value in DEFAULT_XBRL_MAP.items()}
    assert yaml.safe_load((ROOT / "configs" / "xbrl_map.yaml").read_text(encoding="utf-8"))


def test_pull_writes_labelled_snapshots(pulled: EdgarFreeProvider) -> None:
    ids = pulled.snapshot_ids()
    assert set(ids) == {"submissions", "companyfacts", "prices"}
    for dataset, snapshot in ids.items():
        manifest = read_manifest(pulled.root / dataset / f"snapshot={snapshot}")
        assert manifest.survivorship_limited is True
        assert manifest.provider == "edgar_free"
    prices = read_manifest(pulled.root / "prices" / f"snapshot={ids['prices']}")
    assert prices.metadata["stooq_snapshot_date"] == "2026-10-30"


def test_otc_names_and_extra_tickers_are_left_out(pulled: EdgarFreeProvider) -> None:
    assert [row["ticker"] for row in pulled._companies()] == ["AHI", "YTD"]


def test_build_labels_the_tables_dev_only(pulled: EdgarFreeProvider, tmp_path: Path) -> None:
    build_pit(pulled, tmp_path / "pit")
    manifest = read_pit_manifest(tmp_path / "pit")
    assert manifest["survivorship_limited"] is True
    assert manifest["label"] == DEV_ONLY_LABEL
    assert manifest["company_count"] == 2
    assert not [item for item in run_checks(tmp_path / "pit") if item.status == "fail"]


def test_prices_start_in_the_xbrl_era(pulled: EdgarFreeProvider) -> None:
    assert pulled.raw_prices()["date"].min() == date(2024, 1, 31)


def test_shares_reach_prices_only_after_their_filing_is_public(pulled: EdgarFreeProvider) -> None:
    prices = pulled.raw_prices().filter(pl.col("security_id").eq("EDGAR:111:common")).sort("date")
    by_day = dict(zip(prices["date"].to_list(), prices["shares_out"].to_list(), strict=True))
    assert by_day[date(2024, 2, 1)] is None
    assert by_day[date(2024, 2, 2)] == 5e8


def test_fundamentals_use_the_overflow_filings_and_amendments(pulled: EdgarFreeProvider) -> None:
    filings = pulled.filings().collect()
    assert "0000000111-14-000001" in filings["accession"].to_list()
    revenue = (
        pulled.fundamentals()
        .collect()
        .filter(
            pl.col("cik").eq(111), pl.col("item").eq("revenue"), pl.col("period_type").eq("12m")
        )
        .sort("available_at")
    )
    assert revenue["value"].to_list() == [400.0, 420.0]
    assert revenue["is_restatement"].to_list() == [False, True]


def test_unknown_datasets_are_refused(tmp_path: Path) -> None:
    provider = EdgarFreeProvider(tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        provider.pull(["nope"], date(2009, 1, 1), date(2024, 1, 1), tmp_path)


def test_prices_need_the_stooq_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STOOQ_DIR", raising=False)
    provider = EdgarFreeProvider(tmp_path, max_companies=1)
    client = EdgarClient(AGENT, transport=httpx.MockTransport(sec_handler))
    with client, pytest.raises(ProviderUnavailableError, match="STOOQ_DIR"):
        provider.pull(["prices"], date(2009, 1, 1), date(2024, 1, 1), tmp_path, client=client)


def test_bulk_route_when_no_limit_is_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STOOQ_DIR", str(stooq_tree(tmp_path / "stooq")))
    monkeypatch.chdir(tmp_path)
    companies = load("companyfacts_3co.json")

    def archive(members: dict[str, object]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name, payload in members.items():
                bundle.writestr(name, json.dumps(payload))
        return buffer.getvalue()

    bulk = {
        BULK_SUBMISSIONS_URL: archive(
            {
                "CIK0000000111.json": load("submissions_after_hours.json"),
                "CIK0000000111-submissions-001.json": load("submissions_overflow.json"),
            }
        ),
        BULK_COMPANYFACTS_URL: archive({"CIK0000000111.json": companies[0]}),
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == TICKERS_URL:
            return httpx.Response(
                200,
                json={
                    "fields": ["cik", "name", "ticker", "exchange"],
                    "data": [[111, "After Hours Inc.", "AHI", "Nasdaq"]],
                },
            )
        return httpx.Response(200, content=bulk[url])

    provider = EdgarFreeProvider(tmp_path / "raw")
    client = EdgarClient(AGENT, transport=httpx.MockTransport(handler))
    with client:
        provider.pull([], date(2009, 1, 1), date(2024, 1, 1), tmp_path / "raw", client=client)
    assert set(bulk) <= set(requested)
    assert len(requested) == 3
    assert "0000000111-14-000001" in provider.filings().collect()["accession"].to_list()
    assert provider.fundamentals().collect().height > 0


def test_stooq_index_skips_etfs(tmp_path: Path) -> None:
    index = stooq.build_index(stooq_tree(tmp_path))
    assert set(index) == {"AHI", "YTD"}


def test_stooq_missing_and_empty_files(tmp_path: Path) -> None:
    root = stooq_tree(tmp_path)
    empty = next(root.rglob("ytd.us.txt"))
    empty.write_text("", encoding="utf-8")
    prices, missing = stooq.import_tickers(root, ["AHI", "YTD", "NOPE"])
    assert missing == ["YTD", "NOPE"]
    assert prices["ticker"].unique().to_list() == ["AHI"]
    none, gone = stooq.import_tickers(root, ["NOPE"])
    assert none.is_empty()
    assert gone == ["NOPE"]


def test_split_check_passes_adjusted_and_fails_unadjusted_files() -> None:
    def closes(before: float, after: float) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "ticker": ["AAPL", "AAPL"],
                "date": [date(2020, 8, 28), date(2020, 8, 31)],
                "close": [before, after],
            }
        )

    assert stooq.check_split_adjustment(closes(124.8, 129.0)).split_adjusted
    bad = stooq.check_split_adjustment(closes(499.0, 129.0))
    assert bad.probes == 1
    assert not bad.split_adjusted
    assert stooq.check_split_adjustment(closes(1.0, 1.0).head(1)).probes == 0
