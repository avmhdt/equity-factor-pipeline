"""The Sharadar provider on a small hand-made vendor bundle, through a mock API."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from eqpit.calendar import session_close, sessions
from eqpit.edgar.client import EdgarClient
from eqpit.pit.build import build_pit, read_pit_manifest
from eqpit.pit.corpactions import UnmappedActionError
from eqpit.pit.policy import PitPolicy
from eqpit.pit.verify import run_checks
from eqpit.providers.sharadar import (
    EDGAR_BULK_SUBMISSIONS,
    SharadarClient,
    SharadarProvider,
    security_windows,
)
from eqpit.store import PitStore

KEY = "not-a-real-key"
AGENT = "Test Person test.person@example.com"
TICKER_HEADER = (
    "table,permaticker,ticker,name,exchange,isdelisted,category,siccode,firstpricedate,"
    "lastpricedate,secfilings\n"
)
SEC = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
DAYS = sessions(date(2023, 12, 1), date(2024, 3, 28))
SPLIT = date(2024, 1, 16)
DELIST = date(2024, 2, 14)
FILED = date(2024, 1, 10)
LATE_ACCEPTED = "2024-01-10T22:30:00.000Z"


def zipped(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, text)
    return buffer.getvalue()


def tickers_csv() -> str:
    rows = [
        f"SEP,100,AAA,Alpha Inc,NYSE,N,Domestic Common Stock Primary Class,3571,{DAYS[0]},"
        f"{DAYS[-1]},{SEC}0000000111",
        f"SF1,100,AAA,Alpha Inc,NYSE,N,Domestic Common Stock Primary Class,3571,{DAYS[0]},"
        f"{DAYS[-1]},{SEC}0000000111",
        f"SEP,200,BBBQ,Beta Corp,OTC,Y,Domestic Common Stock,2834,{DAYS[0]},{DELIST},"
        f"{SEC}0000000222",
        f"SF1,200,BBBQ,Beta Corp,OTC,Y,Domestic Common Stock,2834,{DAYS[0]},{DELIST},"
        f"{SEC}0000000222",
        f"SEP,300,CCC,Gamma REIT,NASDAQ,N,Domestic Common Stock,6798,{DAYS[0]},{DAYS[-1]},",
        f"SEP,400,DDD,Delta ADR,NYSEMKT,N,ADR Common Stock,3674,{DAYS[0]},{DAYS[-1]},",
    ]
    return TICKER_HEADER + "\n".join(rows) + "\n"


def actions_csv() -> str:
    return (
        "date,action,ticker,name,value,contraticker,contraname\n"
        f"{SPLIT},split,AAA,Alpha Inc,2.0,N/A,N/A\n"
        "2024-01-05,dividend,AAA,Alpha Inc,0.25,N/A,N/A\n"
        "2024-01-08,sicchangefrom,AAA,Alpha Inc,2834,N/A,Pharma\n"
        "2024-01-08,sicchangeto,AAA,Alpha Inc,3571,N/A,Computers\n"
        "2024-02-01,tickerchangefrom,AAA,Alpha Inc,,OLD,N/A\n"
        "2024-02-01,tickerchangeto,AAA,Alpha Inc,,AAA,N/A\n"
        "2024-02-01,exchangefrom,BBBQ,Beta Corp,,N/A,NASDAQ\n"
        "2024-02-01,exchangeto,BBBQ,Beta Corp,,N/A,OTC\n"
        f"{DELIST},delisted,BBBQ,Beta Corp,55.6,N/A,N/A\n"
        f"{DELIST},bankruptcyliquidation,BBBQ,Beta Corp,55.6,N/A,N/A\n"
        "2024-01-03,listed,AAA,Alpha Inc,,AAA,N/A\n"
        "2024-01-03,acquisitioncash,CCC,Gamma REIT,10.5,N/A,N/A\n"
    )


def stocks_csv() -> str:
    lines = ["ticker,date,open,high,low,close,volume,closeadj,closeunadj,lastupdated"]
    for index, day in enumerate(DAYS):
        adjusted = 50.0 + index
        unadjusted = adjusted * (2.0 if day < SPLIT else 1.0)
        lines.append(
            f"AAA,{day},{adjusted},{adjusted},{adjusted},{adjusted},1000,{adjusted},{unadjusted},"
            "2026-01-01"
        )
        if day <= DELIST:
            lines.append(f"BBBQ,{day},10,10,10,10,500,10,10,2026-01-01")
        lines.append(f"CCC,{day},20,20,20,20,100,20,20,2026-01-01")
        lines.append(f"DDD,{day},30,30,30,30,100,30,30,2026-01-01")
    return "\n".join(lines) + "\n"


def fundamentals_csv() -> str:
    head = (
        "ticker,dimension,calendardate,date,reportperiod,fiscalperiod,lastupdated,assets,assetsc,"
        "cashneq,inventory,ppnenet,liabilities,liabilitiesc,debtc,debtnc,equity,revenue,cor,sgna,"
        "intexp,opinc,netinc,depamor,ncfo,epsdil,sharesbas,sharefactor\n"
    )
    numbers = "100,50,10,5,20,60,30,3,20,40,80,30,10,1,20,15,2,18,1.5,1000,1"
    rows = [
        f"AAA,ARQ,2023-12-31,{FILED},2023-12-31,2023-Q4,2026-01-01,{numbers}",
        f"AAA,ARY,2023-12-31,{FILED},2023-12-31,2023-FY,2026-01-01,{numbers}",
        f"AAA,ART,2023-12-31,{FILED},2023-12-31,2023-Q4,2026-01-01,{numbers}",
        f"AAA,MRQ,2023-12-31,2020-01-01,2023-12-31,2023-Q4,2026-01-01,{numbers}",
        f"BBBQ,ARQ,2023-12-31,2024-01-12,2023-12-31,2023-Q4,2026-01-01,{numbers}",
        f"BBBQ,ARY,2023-12-31,2023-12-31,2023-12-31,2023-FY,2026-01-01,{numbers}",
    ]
    return head + "\n".join(rows) + "\n"


def submissions_zip() -> bytes:
    payload = {
        "cik": "0000000111",
        "filings": {
            "recent": {
                "accessionNumber": ["0000000111-24-000001", "0000000111-24-000002"],
                "filingDate": [str(FILED), str(FILED)],
                "reportDate": ["2023-12-30", "2021-03-31"],
                "acceptanceDateTime": [LATE_ACCEPTED, LATE_ACCEPTED],
                "form": ["10-K405", "8-K"],
            }
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("CIK0000000111.json", json.dumps(payload))
        bundle.writestr("CIK0000000999.json", json.dumps(payload))
    return buffer.getvalue()


BUNDLE = {
    "stocks": ("stocks.csv", stocks_csv),
    "fundamentals": ("fundamentals.csv", fundamentals_csv),
    "tickers": ("tickers.csv", tickers_csv),
    "actions": ("actions.csv", actions_csv),
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.sharadar.com":
        table = request.url.path.rsplit("/", 1)[1]
        if request.url.params.get("status") == "True":
            return httpx.Response(200, json={"table": table, "modified": "2026-11-01"})
        return httpx.Response(
            302, headers={"location": f"https://storage.example.com/{table}?sig=x"}
        )
    if request.url.host == "storage.example.com":
        name, build = BUNDLE[request.url.path.strip("/")]
        return httpx.Response(200, content=zipped(name, build()))
    assert str(request.url) == EDGAR_BULK_SUBMISSIONS
    return httpx.Response(200, content=submissions_zip())


@pytest.fixture(scope="module")
def provider(tmp_path_factory: pytest.TempPathFactory) -> SharadarProvider:
    root = tmp_path_factory.mktemp("sharadar") / "raw" / "sharadar"
    source = SharadarProvider(root)
    transport = httpx.MockTransport(handler)
    with (
        SharadarClient(KEY, transport=transport) as client,
        EdgarClient(AGENT, transport=transport) as edgar,
    ):
        source.pull([], DAYS[0], DAYS[-1], root, client=client, edgar=edgar)
    return source


@pytest.fixture(scope="module")
def store(provider: SharadarProvider, tmp_path_factory: pytest.TempPathFactory) -> PitStore:
    root = tmp_path_factory.mktemp("sharadar-pit") / "pit"
    build_pit(provider, root, PitPolicy(horizons_months=(1,)))
    return PitStore.open(root)


def table(store: PitStore, name: str) -> pl.DataFrame:
    return pl.read_parquet(store.root / name / "**" / "*.parquet")


def price(store: PitStore, security: str, day: date) -> dict[str, object]:
    prices = table(store, "prices_daily")
    return prices.filter(pl.col("security_id").eq(security), pl.col("date").eq(day)).row(
        0, named=True
    )


def test_snapshots_hold_only_as_reported_fundamentals(provider: SharadarProvider) -> None:
    dimensions = provider._read("SF1")["dimension"].unique().sort().to_list()
    assert dimensions == ["ARQ", "ART", "ARY"]


def test_unknown_datasets_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        SharadarProvider(tmp_path).pull(["NOPE"], DAYS[0], DAYS[-1], tmp_path)


def test_share_types_and_venues(store: PitStore) -> None:
    master = table(store, "securities")
    by_id = {row["security_id"]: row for row in master.sort("valid_from").iter_rows(named=True)}
    assert by_id["SHR:300"]["share_type"] == "reit"
    assert by_id["SHR:400"]["share_type"] == "adr"
    assert by_id["SHR:400"]["exchange"] == "AMEX"
    assert by_id["SHR:100"]["company_id"] == "CIK:111"
    assert by_id["SHR:300"]["company_id"] == "SHR:300"


def test_sic_history_comes_from_change_events(store: PitStore) -> None:
    rows = table(store, "securities").filter(pl.col("security_id").eq("SHR:100")).sort("valid_from")
    assert rows["sic"].to_list() == [2834, 3571, 3571]
    assert rows["ticker"].to_list() == ["OLD", "OLD", "AAA"]
    assert rows["valid_to"].to_list() == [date(2024, 1, 7), date(2024, 1, 31), None]


def test_a_venue_move_shows_up_on_its_date(store: PitStore) -> None:
    rows = table(store, "securities").filter(pl.col("security_id").eq("SHR:200")).sort("valid_from")
    assert rows["exchange"].to_list() == ["NASDAQ", "OTHER"]


def test_prices_use_the_unadjusted_basis_and_market_cap_stays_right(store: PitStore) -> None:
    before = price(store, "SHR:100", date(2024, 1, 12))
    after = price(store, "SHR:100", date(2024, 1, 17))
    assert before["close"] == 2.0 * (50.0 + DAYS.index(date(2024, 1, 12)))
    assert before["shares_out"] == 500.0
    assert before["volume"] == 500.0
    assert after["shares_out"] == 1000.0
    assert before["mcap"] == 1000.0 * (50.0 + DAYS.index(date(2024, 1, 12)))


def test_a_split_day_carries_the_vendor_return(store: PitStore) -> None:
    today = price(store, "SHR:100", SPLIT)
    index = DAYS.index(SPLIT)
    assert today["ret"] == pytest.approx((50.0 + index) / (49.0 + index) - 1.0)


def test_dividends_are_restated_to_the_unadjusted_basis(store: PitStore) -> None:
    row = (
        table(store, "corp_actions").filter(pl.col("action").eq("cash_dividend")).row(0, named=True)
    )
    assert row["value"] == pytest.approx(0.5)


def test_shares_are_null_until_the_first_filing_is_public(store: PitStore) -> None:
    assert price(store, "SHR:100", date(2024, 1, 9))["shares_out"] is None
    assert price(store, "SHR:100", FILED)["shares_out"] is None


def test_a_late_acceptance_gives_the_next_close_after_acceptance(store: PitStore) -> None:
    filing = table(store, "filings").filter(pl.col("accession").eq("0000000111-24-000001"))
    assert filing["pit_method"].to_list() == ["acceptance"]
    assert filing["acceptance_dt"].to_list() == [datetime(2024, 1, 10, 22, 30, tzinfo=UTC)]
    assert filing["available_at"].to_list() == [session_close(date(2024, 1, 11))]
    assert price(store, "SHR:100", date(2024, 1, 11))["shares_out"] == 500.0


def test_an_unlinked_filing_falls_back_on_its_date(store: PitStore) -> None:
    filing = table(store, "filings").filter(pl.col("cik").eq(222))
    assert filing["pit_method"].to_list() == ["filedate"]
    assert filing["accession"][0].startswith("SHR:200:")
    assert filing["available_at"].to_list() == [session_close(date(2024, 1, 16))]


def test_a_row_filed_on_its_period_end_is_dropped(store: PitStore) -> None:
    assert not table(store, "filings").filter(pl.col("filed").eq(date(2023, 12, 31))).height


def test_mr_rows_never_arrive(store: PitStore) -> None:
    fundamentals = table(store, "fundamentals_pit")
    assert (fundamentals["available_at"].dt.date() > date(2020, 1, 2)).all()


def test_flows_and_instants_take_their_period_types(store: PitStore) -> None:
    fundamentals = table(store, "fundamentals_pit").filter(pl.col("company_id").eq("CIK:111"))
    revenue = fundamentals.filter(pl.col("item").eq("revenue")).sort("period_type")
    assert dict(zip(revenue["period_type"], revenue["fiscal_period"], strict=True)) == {
        "12m": "FY",
        "3m": "Q4",
    }
    assets = fundamentals.filter(pl.col("item").eq("total_assets"))
    assert assets["period_type"].to_list() == ["instant"]
    assert assets["fiscal_period"].to_list() == ["FY"]
    assert fundamentals.filter(pl.col("item").eq("book_equity"))["value"].to_list() == [40.0]


def test_delisting_reason_and_last_primary_venue(store: PitStore) -> None:
    row = table(store, "delistings").row(0, named=True)
    assert row["reason"] == "liquidation"
    assert row["exchange_at_delist"] == "NASDAQ"
    assert row["dlret"] == -0.55
    assert row["imputation_rule"] == "shumway_nasdaq"


def test_the_universe_keeps_common_primary_venue_names(store: PitStore) -> None:
    universe = table(store, "universe_monthly").filter(pl.col("asof").eq(date(2024, 1, 31)))
    assert sorted(universe["security_id"].to_list()) == ["SHR:100", "SHR:200"]
    february = table(store, "universe_monthly").filter(pl.col("asof").eq(date(2024, 2, 29)))
    assert february["security_id"].to_list() == ["SHR:100"]


def test_the_build_is_clean_and_not_labelled_dev(store: PitStore) -> None:
    assert not [item for item in run_checks(store.root) if item.status == "fail"]
    manifest = read_pit_manifest(store.root)
    assert manifest["survivorship_limited"] is False
    assert manifest["label"] == ""
    assert set(manifest["raw_snapshots"]) == {"SEP", "SF1", "TICKERS", "ACTIONS", "EDGAR"}


def test_an_unknown_action_type_fails(provider: SharadarProvider, tmp_path: Path) -> None:
    source = SharadarProvider(provider.root)
    source._actions_cache = provider._actions().with_columns(pl.lit("brandnew").alias("action"))
    with pytest.raises(UnmappedActionError, match="brandnew"):
        source.raw_actions()


def test_an_unknown_category_fails(provider: SharadarProvider) -> None:
    source = SharadarProvider(provider.root)
    source._tickers("SEP")
    assert source._tickers_cache is not None
    source._tickers_cache = source._tickers_cache.with_columns(
        pl.lit("New Thing").alias("category")
    )
    with pytest.raises(ValueError, match="New Thing"):
        source.raw_securities()


def test_events_outside_a_listing_are_ignored() -> None:
    first = date(2020, 1, 2)
    tickers = pl.DataFrame(
        {
            "ticker": ["A"],
            "permaticker": [1],
            "company_id": ["SHR:1"],
            "cik": [None],
            "name": ["A"],
            "share_type": ["common"],
            "firstpricedate": [first],
            "lastpricedate": [first + timedelta(days=30)],
            "exchange": ["NYSE"],
            "siccode": [3571],
        },
        schema_overrides={"cik": pl.Int64},
    )
    actions = pl.DataFrame(
        {
            "ticker": ["A"],
            "date": [first],
            "action": ["sicchangefrom"],
            "value": [1000.0],
            "contraticker": ["N/A"],
            "contraname": ["N/A"],
        }
    )
    rows = security_windows(tickers, actions)
    assert [row["sic"] for row in rows] == [3571]
