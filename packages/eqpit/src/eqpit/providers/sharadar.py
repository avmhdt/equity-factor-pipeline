"""Sharadar's direct API. The key travels in the ``x-api-key`` header, never the URL.

Vendor facts that shape this module, each checked on the Full History Bundle on 2026-11-02:

- ``open``, ``high``, ``low``, ``close`` and ``volume`` are split-adjusted; ``closeunadj`` is not.
- ``dividend`` values are split-adjusted, so they are restated with ``closeunadj / close``.
- ``spinoffdividend`` is the value per parent share, without the ``spinoff`` ratio.
- ``sharesbas`` is restated to the latest split basis, so market cap is
  ``close * sharesbas * sharefactor`` and never ``closeunadj * sharesbas``.
- ``delisted.value`` is a market cap in millions, not a return, so delisting returns are absent.
- ``fundamentals.date`` is the SEC filing date. Only AR* dimensions are read (DQ-05).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import polars as pl
import structlog

from eqpit import __version__
from eqpit.benchmarks import load_risk_free
from eqpit.calendar import closes_on
from eqpit.edgar.client import EdgarClient
from eqpit.edgar.submissions import parse_filing_block
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, XbrlMap
from eqpit.ids import cik10, sharadar_id
from eqpit.pit.corpactions import UnmappedActionError
from eqpit.pit.policy import PitPolicy
from eqpit.providers.base import RawProvider
from eqpit.settings import ProviderName, get_settings
from eqpit.snapshot import SnapshotManifest, latest_snapshot, open_snapshot, read_manifest
from eqpit.throttle import DEFAULT_MAX_BYTES_PER_SECOND, ByteThrottle

log = structlog.get_logger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)

SHARADAR_BASE_URL = "https://api.sharadar.com/v1.0"
EDGAR_BULK_SUBMISSIONS = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

TABLES = {
    "SEP": "stocks",
    "SF1": "fundamentals",
    "TICKERS": "tickers",
    "ACTIONS": "actions",
}
"""Spec dataset name to API table name."""

EDGAR_DATASET = "EDGAR"
"""Acceptance times from SEC's bulk submissions file, linked per DQ-04."""

KEY_CHECK_TICKER = "GME"
"""Outside the free tier: AAPL and MSFT answer with no key at all, so they prove nothing."""

CATEGORY_SHARE_TYPES = {
    "Domestic Common Stock": "common",
    "Domestic Common Stock Primary Class": "common",
    "Domestic Common Stock Secondary Class": "common",
    "ADR Common Stock": "adr",
    "ADR Common Stock Primary Class": "adr",
    "ADR Common Stock Secondary Class": "adr",
    "ADR Preferred Stock": "other",
    "Canadian Common Stock": "other",
    "Canadian Common Stock Primary Class": "other",
    "Canadian Common Stock Secondary Class": "other",
    "Canadian Preferred Stock": "other",
    "Domestic Preferred Stock": "other",
}
"""Vendor category to share type. CRSP share codes 10 and 11 hold US-incorporated commons."""

REIT_SIC = 6798

EXCHANGES = {"NYSE": "NYSE", "NASDAQ": "NASDAQ", "NYSEMKT": "AMEX", "AMEX": "AMEX"}
"""Primary venues; every other value, such as OTC or BATS, is ``OTHER``."""

ACTION_MAP = {
    "split": "split",
    "dividend": "cash_dividend",
    "spinoffdividend": "spinoff",
    "tickerchangeto": "ticker_change",
    "delisted": "delist",
    "mergerto": "merger",
    "spacmerger": "merger",
    "acquisitionby": "merger",
}
"""Vendor action to the corporate-action vocabulary."""

IGNORED_ACTIONS = frozenset(
    {
        "adrratiosplit",
        "bankruptcyliquidation",
        "equitycancellation",
        "exchangefrom",
        "exchangeto",
        "initiated",
        "listed",
        "mergerfrom",
        "namechangefrom",
        "namechangeto",
        "regulatorydelisting",
        "relation",
        "reorganizationfrom",
        "reorganizationpostsplitcommon",
        "reorganizationto",
        "sicchangefrom",
        "sicchangeto",
        "spacunitseparation",
        "spinoff",
        "spunofffrom",
        "tickerchangefrom",
        "voluntarydelisting",
    }
)
"""Understood and handled elsewhere, or not an event for this table. A value in neither set
fails the build. ``acquisition*`` rows other than ``acquisitionby`` give consideration terms."""

DELISTING_REASONS = {
    "bankruptcyliquidation": "liquidation",
    "regulatorydelisting": "performance",
    "voluntarydelisting": "performance",
    "acquisitionby": "merger",
    "mergerto": "merger",
    "mergerfrom": "merger",
    "spacmerger": "merger",
}
"""Same-day action to normalised delisting reason. Voluntary delistings follow CRSP codes
570 to 574, which fall in the performance range; a ``delisted`` row with none is ``unknown``."""

FIELDS: XbrlMap = {
    "total_assets": ["assets"],
    "current_assets": ["assetsc"],
    "cash": ["cashneq"],
    "inventory": ["inventory"],
    "ppe_net": ["ppnenet"],
    "total_liabilities": ["liabilities"],
    "current_liabilities": ["liabilitiesc"],
    "short_term_debt": ["debtc"],
    "long_term_debt": ["debtnc"],
    "stockholders_equity": ["equity"],
    "revenue": ["revenue"],
    "cogs": ["cor"],
    "sga": ["sgna"],
    "interest_expense": ["intexp"],
    "operating_income": ["opinc"],
    "net_income": ["netinc"],
    "income_before_extra": ["netinc"],
    "depreciation": ["depamor"],
    "cfo": ["ncfo"],
    "eps_diluted": ["epsdil"],
    "shares_out": ["sharesbas"],
}
"""Canonical item to SF1 field. The vendor has no gross PP&E, deferred taxes or preferred."""

INSTANT_FIELDS = frozenset(
    {
        "assets",
        "assetsc",
        "cashneq",
        "inventory",
        "ppnenet",
        "liabilities",
        "liabilitiesc",
        "debtc",
        "debtnc",
        "equity",
        "sharesbas",
    }
)
_LINK_WINDOW_DAYS = 5
_QUARTER_DAYS = 90
_YEAR_DAYS = 364
_CHUNK_BYTES = 1 << 18
_LINK_FORMS = (
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "10-K405",
    "10-K405/A",
    "10-KSB",
    "10-KSB/A",
    "10-QSB",
    "10-QSB/A",
    "10-KT",
    "10-KT/A",
    "10-QT",
    "10-QT/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
)


class SharadarDownloadError(RuntimeError):
    """A bulk download failed. The message never holds the signed link or the key."""


def fetch_one_row(
    table: str,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Return the CSV body of a one-row query on ``table``, raising on any HTTP error."""
    with httpx.Client(
        base_url=SHARADAR_BASE_URL,
        headers={"x-api-key": api_key},
        transport=transport,
        timeout=httpx.Timeout(30.0),
    ) as client:
        response = client.get(f"/data/{table}", params={"ticker": KEY_CHECK_TICKER, "limit": 1})
        response.raise_for_status()
        return response.text


class SharadarClient:
    """Bulk access to the API.

    ``years=full`` answers with a redirect to a time-limited signed link. That link is
    followed by hand with a second client, because httpx would otherwise send ``x-api-key``
    to the storage host. It is a credential of its own, so it is never logged.

    Args:
        api_key: the subscription key.
        max_bytes_per_second: download cap.
        transport: httpx transport override, used by tests.
        throttle: byte throttle override, used by tests.
    """

    def __init__(
        self,
        api_key: str,
        max_bytes_per_second: float = DEFAULT_MAX_BYTES_PER_SECOND,
        transport: httpx.BaseTransport | None = None,
        throttle: ByteThrottle | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SHARADAR_API_KEY is not set")
        self._throttle = throttle
        self._rate = max_bytes_per_second
        self._api = httpx.Client(
            base_url=SHARADAR_BASE_URL,
            headers={"x-api-key": api_key},
            transport=transport,
            timeout=httpx.Timeout(60.0),
            follow_redirects=False,
        )
        self._storage = httpx.Client(
            transport=transport, timeout=httpx.Timeout(120.0), follow_redirects=True
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release both connection pools."""
        self._api.close()
        self._storage.close()

    def status(self, table: str) -> dict[str, Any]:
        """File name, size and modified time of a table's bulk file."""
        response = self._api.get(f"/data/{table}", params={"status": "True", "years": "full"})
        response.raise_for_status()
        loaded: dict[str, Any] = response.json()
        return loaded

    def download(self, table: str, dest: Path) -> str:
        """Stream a table's full-history zip to ``dest`` under the cap; return its sha256."""
        response = self._api.get(f"/data/{table}", params={"years": "full"})
        if not response.is_redirect:
            raise SharadarDownloadError(f"{table}: HTTP {response.status_code}, no download link")
        link = response.headers["location"]
        throttle = self._throttle or ByteThrottle(self._rate)
        digest = hashlib.sha256()
        total = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._storage.stream("GET", link) as stream:
                if stream.status_code != 200:
                    raise SharadarDownloadError(f"{table}: storage HTTP {stream.status_code}")
                with dest.open("wb") as handle:
                    for chunk in stream.iter_bytes(chunk_size=_CHUNK_BYTES):
                        handle.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        throttle.consumed(len(chunk))
        except httpx.HTTPError as error:
            raise SharadarDownloadError(f"{table}: {type(error).__name__}") from None
        log.info("sharadar_download", table=table, bytes=total)
        return digest.hexdigest()


def _zip_to_parquet(archive: Path, target: Path, dataset: str) -> int:
    """Convert a vendor zip to Parquet in one pass and return the row count.

    SF1 keeps only AR* dimensions from here on: MR* rows hold restated values (DQ-05).
    """
    workdir = target.parent / f".extract-{dataset}"
    with zipfile.ZipFile(archive) as bundle:
        member = bundle.namelist()[0]
        path = Path(bundle.extract(member, workdir))
    header = path.open(encoding="utf-8").readline().strip().split(",")
    text = {
        "ticker",
        "table",
        "action",
        "name",
        "dimension",
        "fiscalperiod",
        "contraticker",
        "contraname",
        "category",
        "exchange",
        "isdelisted",
        "secfilings",
    }
    dates = {"date", "calendardate", "reportperiod", "lastupdated"}
    numeric = (
        {name: pl.Float64 for name in header if name not in text | dates}
        if dataset in ("SEP", "SF1")
        else {}
    )
    overrides: dict[str, Any] = {**numeric, **{name: pl.Date for name in dates & set(header)}}
    if dataset == "ACTIONS":
        overrides["value"] = pl.String
    query = pl.scan_csv(
        path,
        schema_overrides=overrides,
        infer_schema_length=None if dataset in ("TICKERS", "ACTIONS") else 1000,
    )
    if dataset == "SF1":
        query = query.filter(pl.col("dimension").is_in(["ARQ", "ARY", "ART"]))
    if dataset == "ACTIONS":
        query = query.with_columns(pl.col("value").cast(pl.Float64, strict=False))
    query.sink_parquet(target, compression="zstd")
    shutil.rmtree(workdir)
    return int(pl.scan_parquet(target).select(pl.len()).collect().item())


def extract_edgar_filings(archive: Path, ciks: Iterable[int]) -> pl.DataFrame:
    """Filing rows with acceptance times for the wanted companies from SEC's bulk file."""
    frames: list[pl.DataFrame] = []
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        pages: dict[str, list[str]] = {}
        for name in names:
            if "-submissions-" in name:
                pages.setdefault(name.split("-")[0], []).append(name)
        for cik in ciks:
            stem = f"CIK{cik10(cik)}"
            if f"{stem}.json" not in names:
                continue
            main = json.loads(bundle.read(f"{stem}.json"))
            block = main.get("filings", {}).get("recent", main.get("filings", main))
            frames.append(parse_filing_block(cik, block, _LINK_FORMS))
            for page in sorted(pages.get(stem, [])):
                frames.append(parse_filing_block(cik, json.loads(bundle.read(page)), _LINK_FORMS))
    filled = [frame for frame in frames if not frame.is_empty()]
    return (
        pl.concat(filled, how="vertical")
        .unique(subset=["accession"], keep="first")
        .sort("cik", "accession")
    )


def security_windows(tickers: pl.DataFrame, actions: pl.DataFrame) -> list[dict[str, object]]:
    """SCD2 rows per security from the vendor's change events.

    ``tickers`` holds today's values. Walking each attribute's ``from`` events backwards gives
    its value on any earlier date, so a SIC or listing venue is never read from the future.
    """
    changes = actions.filter(
        pl.col("action").is_in(["tickerchangefrom", "exchangefrom", "sicchangefrom"])
    ).sort("ticker", "date")
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in changes.select(
        "ticker", "date", "action", "value", "contraticker", "contraname"
    ).iter_rows(named=True):
        by_ticker.setdefault(row["ticker"], []).append(row)
    rows: list[dict[str, object]] = []
    for row in tickers.iter_rows(named=True):
        first: date = row["firstpricedate"]
        events = [
            event
            for event in by_ticker.get(row["ticker"], [])
            if first < event["date"] <= (row["lastpricedate"] or event["date"])
        ]
        cuts = sorted({event["date"] for event in events})
        starts = [first, *cuts]
        ends: list[date | None] = [*cuts, None]
        for begin, stop in zip(starts, ends, strict=True):
            attributes = _attributes_on(row, events, begin)
            rows.append(
                {
                    "security_id": sharadar_id(row["permaticker"]),
                    "company_id": row["company_id"],
                    "cik": row["cik"],
                    "permaticker": row["permaticker"],
                    "name": row["name"],
                    "share_type": row["share_type"],
                    "valid_from": begin,
                    "valid_to": None if stop is None else date.fromordinal(stop.toordinal() - 1),
                    **attributes,
                }
            )
    return rows


def _attributes_on(
    row: dict[str, Any], events: list[dict[str, Any]], day: date
) -> dict[str, object]:
    """Attribute values in force on ``day``: the earliest later ``from`` event, else today's."""
    ticker: str = row["ticker"]
    exchange: str = row["exchange"] or "OTHER"
    sic: int | None = row["siccode"]
    seen: set[str] = set()
    for event in events:
        kind = event["action"]
        if event["date"] <= day or kind in seen:
            continue
        seen.add(kind)
        if kind == "tickerchangefrom":
            ticker = event["contraticker"] or ticker
        elif kind == "exchangefrom":
            exchange = event["contraname"] or exchange
        elif event["value"] is not None:
            sic = int(event["value"])
    return {"ticker": ticker, "exchange": EXCHANGES.get(exchange, "OTHER"), "sic": sic}


class SharadarProvider(RawProvider):
    """The Full History Bundle, read from immutable snapshots."""

    name: ProviderName = "sharadar"
    survivorship_free = True

    def __init__(
        self,
        root: Path,
        policy: PitPolicy | None = None,
        xbrl_map: XbrlMap = DEFAULT_XBRL_MAP,
    ) -> None:
        super().__init__(root, policy, FIELDS)
        self._filings_cache: pl.DataFrame | None = None
        self._tickers_cache: pl.DataFrame | None = None
        self._actions_cache: pl.DataFrame | None = None

    def pull(
        self,
        datasets: Sequence[str],
        start: date,
        end: date,
        dest: Path,
        client: SharadarClient | None = None,
        edgar: EdgarClient | None = None,
    ) -> list[SnapshotManifest]:
        """Fetch one table at a time, under the byte cap, into new snapshots."""
        wanted = list(datasets) or [*TABLES, EDGAR_DATASET]
        unknown = sorted(set(wanted) - {*TABLES, EDGAR_DATASET})
        if unknown:
            raise ValueError(f"unknown sharadar datasets: {unknown}")
        session = client or SharadarClient(get_settings().sharadar_api_key.get_secret_value())
        manifests: list[SnapshotManifest] = []
        try:
            for dataset in wanted:
                if dataset != EDGAR_DATASET:
                    manifests.append(self._pull_table(session, dataset, dest))
        finally:
            if client is None:
                session.close()
        if EDGAR_DATASET in wanted:
            manifests.append(self._pull_edgar(edgar, dest))
        return manifests

    def _pull_table(self, client: SharadarClient, dataset: str, dest: Path) -> SnapshotManifest:
        table = TABLES[dataset]
        counts: dict[str, int] = {}
        details = client.status(table)
        metadata = {"vendor_modified": str(details.get("modified", ""))}
        with open_snapshot(
            dest,
            self.name,
            dataset,
            False,
            source=f"{SHARADAR_BASE_URL}/data/{table}?years=full",
            package_version=__version__,
            row_counts=counts,
            metadata=metadata,
        ) as staging:
            archive = staging / f"{table}.csv.zip"
            client.download(table, archive)
            counts[f"{dataset}.parquet"] = _zip_to_parquet(
                archive, staging / f"{dataset}.parquet", dataset
            )
        return read_manifest(latest_snapshot(dest, dataset))

    def _pull_edgar(self, client: EdgarClient | None, dest: Path) -> SnapshotManifest:
        session = client or EdgarClient.from_settings()
        counts: dict[str, int] = {}
        ciks = self._tickers("SF1")["cik"].drop_nulls().unique().sort().to_list()
        try:
            with open_snapshot(
                dest,
                self.name,
                EDGAR_DATASET,
                False,
                source=EDGAR_BULK_SUBMISSIONS,
                package_version=__version__,
                row_counts=counts,
            ) as staging:
                archive = staging / "submissions.zip"
                session.download(EDGAR_BULK_SUBMISSIONS, archive, ByteThrottle())
                filings = extract_edgar_filings(archive, ciks)
                filings.write_parquet(staging / "filings.parquet", compression="zstd")
                counts["filings.parquet"] = filings.height
                archive.unlink()
        finally:
            if client is None:
                session.close()
        return read_manifest(latest_snapshot(dest, EDGAR_DATASET))

    def _read(self, dataset: str) -> pl.DataFrame:
        return pl.read_parquet(self.snapshot_path(dataset) / f"{dataset}.parquet")

    def _scan(self, dataset: str) -> pl.LazyFrame:
        return pl.scan_parquet(self.snapshot_path(dataset) / f"{dataset}.parquet")

    def _tickers(self, table: str) -> pl.DataFrame:
        """Master rows of one vendor table, with parsed keys. Tickers stop here."""
        if self._tickers_cache is None:
            frame = self._read("TICKERS").with_columns(
                pl.col("secfilings").str.extract(r"CIK=(\d+)", 1).cast(pl.Int64).alias("cik"),
                pl.col("firstpricedate", "lastpricedate").str.to_date("%Y-%m-%d", strict=False),
            )
            self._tickers_cache = frame.with_columns(
                pl.when(pl.col("cik").is_not_null())
                .then(pl.format("CIK:{}", pl.col("cik")))
                .otherwise(pl.format("SHR:{}", pl.col("permaticker")))
                .alias("company_id")
            )
        return self._tickers_cache.filter(pl.col("table").eq(table))

    def _actions(self) -> pl.DataFrame:
        if self._actions_cache is None:
            self._actions_cache = self._read("ACTIONS")
        return self._actions_cache

    def _master(self) -> pl.DataFrame:
        """SEP master rows typed and filtered to stocks the tables can use."""
        tickers = self._tickers("SEP")
        unknown = sorted(
            set(tickers["category"].drop_nulls().to_list()) - set(CATEGORY_SHARE_TYPES)
        )
        if unknown:
            raise ValueError(f"unmapped Sharadar categories: {unknown}")
        return tickers.with_columns(
            pl.when(pl.col("siccode").eq(REIT_SIC))
            .then(pl.lit("reit"))
            .otherwise(
                pl.col("category").replace_strict(CATEGORY_SHARE_TYPES, return_dtype=pl.String)
            )
            .alias("share_type")
        ).filter(pl.col("firstpricedate").is_not_null())

    def raw_securities(self) -> pl.DataFrame:
        rows = security_windows(self._master(), self._actions())
        return pl.DataFrame(rows, infer_schema_length=None).with_columns(
            pl.col("sic").cast(pl.Int16),
            pl.col("valid_from").cast(pl.Date),
            pl.col("valid_to").cast(pl.Date),
        )

    def _link(self) -> pl.DataFrame:
        return self._master().select(
            "ticker", "permaticker", pl.format("SHR:{}", pl.col("permaticker")).alias("security_id")
        )

    def raw_actions(self) -> pl.DataFrame:
        """Canonical actions, with cash amounts restated to the unadjusted price basis."""
        actions = self._actions()
        names = set(actions["action"].drop_nulls().to_list())
        unmapped = sorted(
            name
            for name in names - set(ACTION_MAP) - IGNORED_ACTIONS
            if not name.startswith("acquisition")
        )
        if unmapped:
            raise UnmappedActionError(f"unmapped Sharadar action types: {unmapped}")
        relevant = actions.filter(pl.col("action").is_in(list(ACTION_MAP))).unique(
            subset=["ticker", "date", "action"], keep="first", maintain_order=True
        )
        linked = relevant.join(self._link(), on="ticker", how="inner").with_columns(
            pl.col("action").replace_strict(ACTION_MAP, return_dtype=pl.String),
            pl.when(pl.col("action").eq("tickerchangeto"))
            .then(pl.col("contraticker"))
            .alias("new_ticker"),
        )
        prices = (
            self._scan("SEP")
            .select("ticker", "date", (pl.col("closeunadj") / pl.col("close")).alias("basis"))
            .filter(pl.col("basis").is_finite())
            .sort("date")
            .collect()
        )
        restated = linked.sort("date").join_asof(
            prices,
            on="date",
            by="ticker",
            strategy="forward",
            check_sortedness=False,
        )
        return restated.select(
            "security_id",
            pl.col("date").alias("ex_date"),
            "action",
            pl.when(pl.col("action").is_in(["cash_dividend", "spinoff"]))
            .then(pl.col("value") * pl.col("basis").fill_null(1.0))
            .otherwise(pl.col("value"))
            .alias("value"),
            "new_ticker",
        )

    def raw_filings(self) -> pl.DataFrame:
        """One row per SF1 filing, linked to EDGAR by CIK, filing date and period (DQ-04)."""
        if self._filings_cache is not None:
            return self._filings_cache
        master = self._tickers("SF1").select("ticker", "permaticker", "cik")
        reported = (
            self._scan("SF1")
            .filter(pl.col("dimension").is_in(["ARQ", "ARY"]))
            .select(
                "ticker",
                pl.col("date").alias("filed"),
                pl.col("reportperiod").alias("period_end"),
                "dimension",
                pl.col("fiscalperiod").str.slice(5).alias("fp"),
            )
            .unique()
            .collect()
            .join(master, on="ticker", how="inner")
            .filter(pl.col("filed") > pl.col("period_end"))
            .with_columns(
                pl.when(pl.col("dimension").eq("ARY") | pl.col("fp").eq("Q4"))
                .then(pl.lit("10-K"))
                .otherwise(pl.lit("10-Q"))
                .alias("form")
            )
            .sort("ticker", "filed", "period_end", "form")
            .unique(subset=["ticker", "filed", "period_end"], keep="first", maintain_order=True)
        )
        edgar = pl.read_parquet(self.snapshot_path(EDGAR_DATASET) / "filings.parquet").select(
            "cik",
            "filed",
            pl.col("accession").alias("edgar_accession"),
            pl.col("period_end").alias("edgar_period"),
            "acceptance_dt",
        )
        joined = reported.join(edgar, on=["cik", "filed"], how="left").with_columns(
            (pl.col("period_end") - pl.col("edgar_period")).dt.total_days().abs().alias("gap")
        )
        near = joined.filter(pl.col("gap") <= _LINK_WINDOW_DAYS).sort("gap", "edgar_accession")
        best = near.unique(
            subset=["ticker", "filed", "period_end"], keep="first", maintain_order=True
        ).select("ticker", "filed", "period_end", "edgar_accession", "acceptance_dt")
        linked = reported.join(best, on=["ticker", "filed", "period_end"], how="left")
        self._filings_cache = linked.with_columns(
            pl.coalesce(
                "edgar_accession",
                pl.format("SHR:{}:{}:{}", "permaticker", "filed", "period_end"),
            ).alias("accession")
        )
        return self._filings_cache

    def raw_facts(self) -> pl.DataFrame:
        """Long-format facts from AR* rows, with no fiscal-period dates beyond the filing's."""
        fields = sorted({field for names in FIELDS.values() for field in names})
        filings = self.raw_filings().select(
            "ticker", "filed", "period_end", "accession", "form", "cik"
        )
        master = self._tickers("SF1").select("ticker", "company_id")
        wide = (
            self._scan("SF1")
            .filter(pl.col("dimension").is_in(["ARQ", "ARY"]))
            .select(
                "ticker",
                pl.col("date").alias("filed"),
                pl.col("reportperiod").alias("period_end"),
                "dimension",
                pl.col("fiscalperiod").str.slice(5).alias("fp"),
                *fields,
            )
            .collect()
        )
        long = wide.unpivot(
            on=fields,
            index=["ticker", "filed", "period_end", "dimension", "fp"],
            variable_name="tag",
            value_name="value",
        ).drop_nulls("value")
        instant = pl.col("tag").is_in(list(INSTANT_FIELDS))
        days = (
            pl.when(pl.col("dimension").eq("ARY"))
            .then(pl.lit(_YEAR_DAYS))
            .otherwise(pl.lit(_QUARTER_DAYS))
        )
        return (
            long.filter(~(instant & pl.col("dimension").eq("ARY") & pl.col("fp").ne("FY")))
            .join(filings, on=["ticker", "filed", "period_end"], how="inner")
            .join(master, on="ticker", how="inner")
            .with_columns(
                pl.when(instant)
                .then(None)
                .otherwise(pl.col("period_end") - pl.duration(days=days))
                .cast(pl.Date)
                .alias("start"),
                pl.col("period_end").alias("end"),
                pl.when(pl.col("tag").eq("epsdil"))
                .then(pl.lit("USD/shares"))
                .when(pl.col("tag").eq("sharesbas"))
                .then(pl.lit("shares"))
                .otherwise(pl.lit("USD"))
                .alias("unit"),
            )
            .select(
                "cik",
                "company_id",
                "tag",
                "start",
                "end",
                "value",
                "unit",
                "accession",
                "form",
                "filed",
                "fp",
            )
        )

    def _shares(self) -> pl.DataFrame:
        """Shares on the latest split basis, by the instant each filing became public."""
        filings = self.filings().collect().select("accession", "available_at")
        raw = self.raw_filings().select("ticker", "filed", "period_end", "accession")
        return (
            self._scan("SF1")
            .filter(pl.col("dimension").eq("ARQ"))
            .select(
                "ticker",
                pl.col("date").alias("filed"),
                pl.col("reportperiod").alias("period_end"),
                (pl.col("sharesbas") * pl.col("sharefactor").fill_null(1.0)).alias("shares_basis"),
            )
            .drop_nulls("shares_basis")
            .collect()
            .join(raw, on=["ticker", "filed", "period_end"], how="inner")
            .join(filings, on="accession", how="inner")
            .select("ticker", "available_at", "shares_basis")
            .sort("available_at")
        )

    def raw_prices(self) -> pl.DataFrame:
        """Unadjusted bars and same-date shares.

        ``ret_vendor`` carries the vendor's total return on split and spinoff days. There
        ``closeunadj`` plus action values disagree with ``closeadj`` on some rows, and on
        every other day the unadjusted route has far less rounding noise.
        """
        link = self._link()
        rows = (
            self._scan("SEP")
            .filter(pl.col("ticker").is_in(link["ticker"].to_list()))
            .select(
                "ticker", "date", "open", "high", "low", "close", "volume", "closeadj", "closeunadj"
            )
            .filter(pl.col("close") > 0)
            .collect()
        )
        events = (
            self._actions()
            .filter(pl.col("action").is_in(["split", "spinoffdividend"]))
            .select("ticker", "date", pl.lit(True).alias("vendor_day"))
            .unique()
        )
        basis = pl.col("closeunadj") / pl.col("close")
        framed = rows.sort("ticker", "date").with_columns(
            (pl.col("closeadj") / pl.col("closeadj").shift(1).over("ticker") - 1.0).alias("vendor")
        )
        joined = framed.join(events, on=["ticker", "date"], how="left")
        joined = joined.with_columns(closes_on(joined["date"]).alias("close_at")).sort("close_at")
        with_shares = joined.join_asof(
            self._shares(),
            left_on="close_at",
            right_on="available_at",
            by="ticker",
            strategy="backward",
            check_sortedness=False,
        )
        return with_shares.join(link, on="ticker", how="inner").select(
            "security_id",
            "date",
            (pl.col("open") * basis).alias("open"),
            (pl.col("high") * basis).alias("high"),
            (pl.col("low") * basis).alias("low"),
            pl.col("closeunadj").alias("close"),
            (pl.col("volume") / basis).alias("volume"),
            (pl.col("shares_basis") / basis).alias("shares_out"),
            pl.when(pl.col("vendor_day").fill_null(False))
            .then(pl.col("vendor"))
            .alias("ret_vendor"),
        )

    def raw_delistings(self) -> pl.DataFrame:
        """``delisted`` rows with a normalised reason. Sharadar has no delisting return."""
        actions = self._actions()
        link = self._link()
        reasons = (
            actions.filter(pl.col("action").is_in(list(DELISTING_REASONS)))
            .with_columns(
                pl.col("action").replace_strict(DELISTING_REASONS, return_dtype=pl.String)
            )
            .select("ticker", "date", pl.col("action").alias("reason"))
            .sort("reason")
            .unique(subset=["ticker", "date"], keep="first", maintain_order=True)
        )
        moves = (
            actions.filter(pl.col("action").eq("exchangefrom"))
            .sort("date")
            .with_columns(
                pl.col("contraname").replace_strict(
                    EXCHANGES, default="OTHER", return_dtype=pl.String
                )
            )
            .filter(pl.col("contraname").ne("OTHER"))
            .group_by("ticker", maintain_order=True)
            .agg(pl.col("contraname").last().alias("moved_from"))
        )
        current = self._master().select(
            "ticker",
            pl.col("exchange")
            .replace_strict(EXCHANGES, default="OTHER", return_dtype=pl.String)
            .alias("listed_on"),
        )
        rows = (
            actions.filter(pl.col("action").eq("delisted"))
            .select("ticker", pl.col("date").alias("delist_date"))
            .sort("ticker", "delist_date")
            .unique(subset=["ticker"], keep="last", maintain_order=True)
            .join(link, on="ticker", how="inner")
            .join(
                reasons.rename({"date": "delist_date"}),
                on=["ticker", "delist_date"],
                how="left",
            )
            .join(moves, on="ticker", how="left")
            .join(current, on="ticker", how="left")
        )
        return rows.select(
            "security_id",
            "delist_date",
            pl.lit(None, dtype=pl.Int16).alias("delist_code"),
            pl.col("reason").fill_null("unknown"),
            pl.when(pl.col("listed_on").eq("OTHER"))
            .then(pl.coalesce("moved_from", "listed_on"))
            .otherwise(pl.col("listed_on"))
            .alias("exchange_at_delist"),
            pl.lit(None, dtype=pl.Float64).alias("dlret_raw"),
        ).sort("security_id")

    def risk_free(self) -> pl.LazyFrame | None:
        return load_risk_free(self.root.parent.parent / "external" / "french")

    def snapshot_ids(self) -> dict[str, str]:
        return {
            dataset: self.snapshot_path(dataset).name.split("=")[1]
            for dataset in (*TABLES, EDGAR_DATASET)
        }
