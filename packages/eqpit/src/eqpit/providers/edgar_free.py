"""The free path: SEC EDGAR plus local Stooq prices. Dev only (DQ-16).

Only companies that trade today appear, so this path is survivorship-limited by design. The
Stooq file for a company is found by its current ticker, which is the one place a ticker acts
as a link; inside the PIT tables everything keys on ``security_id``.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import structlog

from eqpit import __version__
from eqpit.calendar import closes_on
from eqpit.edgar.client import EdgarClient
from eqpit.edgar.companyfacts import companyfacts_url, parse_companyfacts
from eqpit.edgar.submissions import (
    SUBMISSIONS_BASE,
    company_meta,
    overflow_pages,
    parse_filing_block,
    submissions_url,
)
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, XbrlMap, all_tags
from eqpit.ids import cik10, company_from_cik, edgar_id
from eqpit.pit.policy import PitPolicy
from eqpit.providers import stooq
from eqpit.providers.base import ProviderUnavailableError, RawProvider
from eqpit.settings import ProviderName, get_settings
from eqpit.snapshot import SnapshotManifest, latest_snapshot, open_snapshot, read_manifest

log = structlog.get_logger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
BULK_SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
BULK_COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
XBRL_ERA_START = date(2009, 1, 1)

_TICKERS_FILE = "company_tickers_exchange.json"
_EXCHANGES = {"Nasdaq": "NASDAQ", "NYSE": "NYSE", "NYSE American": "AMEX", "NYSE MKT": "AMEX"}
_DATASETS = ("submissions", "companyfacts", "prices")


class EdgarFreeProvider(RawProvider):
    """EDGAR XBRL plus free prices.

    Args:
        root: raw directory for this provider.
        policy: availability rules.
        xbrl_map: canonical item to ordered tags.
        max_companies: per-CIK download of the first N listed companies. None takes SEC's bulk
            zips, which is the preferred route for a full run.
    """

    name: ProviderName = "edgar_free"
    survivorship_free = False

    def __init__(
        self,
        root: Path,
        policy: PitPolicy | None = None,
        xbrl_map: XbrlMap = DEFAULT_XBRL_MAP,
        max_companies: int | None = None,
    ) -> None:
        super().__init__(root, policy, xbrl_map)
        self.max_companies = max_companies

    def pull(
        self,
        datasets: Sequence[str],
        start: date,
        end: date,
        dest: Path,
        client: EdgarClient | None = None,
    ) -> list[SnapshotManifest]:
        """Write new snapshots; requests go through the rate-limited client."""
        wanted = list(datasets) or list(_DATASETS)
        unknown = sorted(set(wanted) - set(_DATASETS))
        if unknown:
            raise ValueError(f"unknown edgar_free datasets: {unknown}")
        session = client or EdgarClient.from_settings()
        manifests: list[SnapshotManifest] = []
        try:
            companies = self._select_companies(session)
            if "submissions" in wanted:
                manifests.append(self._pull_submissions(session, companies, dest))
            if "companyfacts" in wanted:
                manifests.append(self._pull_companyfacts(session, companies, dest))
            if "prices" in wanted:
                manifests.append(self._pull_prices(companies, dest))
        finally:
            if client is None:
                session.close()
        return manifests

    def _select_companies(self, client: EdgarClient) -> list[dict[str, Any]]:
        payload = client.get_json(TICKERS_URL)
        fields = payload["fields"]
        rows = [dict(zip(fields, row, strict=True)) for row in payload["data"]]
        listed = [row for row in rows if row.get("exchange") in _EXCHANGES]
        seen: set[int] = set()
        chosen: list[dict[str, Any]] = []
        for row in listed:
            if row["cik"] in seen:
                continue
            seen.add(row["cik"])
            chosen.append(row)
        return chosen[: self.max_companies] if self.max_companies is not None else chosen

    def _pull_submissions(
        self, client: EdgarClient, companies: list[dict[str, Any]], dest: Path
    ) -> SnapshotManifest:
        counts: dict[str, int] = {}
        with open_snapshot(
            dest,
            self.name,
            "submissions",
            True,
            source=SUBMISSIONS_BASE,
            package_version=__version__,
            row_counts=counts,
        ) as staging:
            (staging / _TICKERS_FILE).write_text(json.dumps(companies), encoding="utf-8")
            if self.max_companies is None:
                client.download(BULK_SUBMISSIONS_URL, staging / "submissions.zip")
            else:
                for company in companies:
                    cik = int(company["cik"])
                    raw = client.get_bytes(submissions_url(cik))
                    (staging / f"CIK{cik10(cik)}.json").write_bytes(raw)
                    for page in overflow_pages(json.loads(raw), XBRL_ERA_START):
                        content = client.get_bytes(f"{SUBMISSIONS_BASE}/{page}")
                        (staging / page).write_bytes(content)
            counts["companies"] = len(companies)
        return read_manifest(latest_snapshot(dest, "submissions"))

    def _pull_companyfacts(
        self, client: EdgarClient, companies: list[dict[str, Any]], dest: Path
    ) -> SnapshotManifest:
        counts: dict[str, int] = {}
        with open_snapshot(
            dest,
            self.name,
            "companyfacts",
            True,
            source="https://data.sec.gov/api/xbrl/companyfacts",
            package_version=__version__,
            row_counts=counts,
        ) as staging:
            if self.max_companies is None:
                client.download(BULK_COMPANYFACTS_URL, staging / "companyfacts.zip")
            else:
                for company in companies:
                    cik = int(company["cik"])
                    try:
                        content = client.get_bytes(companyfacts_url(cik))
                    except Exception as error:
                        log.warning("companyfacts_missing", cik=cik, error=str(error))
                        continue
                    (staging / f"CIK{cik10(cik)}.json").write_bytes(content)
            counts["companies"] = len(companies)
        return read_manifest(latest_snapshot(dest, "companyfacts"))

    def _pull_prices(self, companies: list[dict[str, Any]], dest: Path) -> SnapshotManifest:
        stooq_dir = get_settings().stooq_dir
        if stooq_dir is None or not Path(stooq_dir).is_dir():
            raise ProviderUnavailableError("STOOQ_DIR is not set to the owner's snapshot")
        tickers = [str(company["ticker"]) for company in companies]
        prices, missing = stooq.import_tickers(Path(stooq_dir), tickers)
        check = stooq.check_split_adjustment(prices)
        if check.probes and not check.split_adjusted:
            raise ValueError("Stooq snapshot is not split-adjusted; total returns would be wrong")
        with open_snapshot(
            dest,
            self.name,
            "prices",
            True,
            source="owner Stooq bulk snapshot",
            package_version=__version__,
            row_counts={"prices.parquet": prices.height},
            metadata={
                "stooq_snapshot_date": stooq.SNAPSHOT_DATE,
                "split_adjustment_probes": str(check.probes),
                "split_adjusted": str(check.split_adjusted),
                "missing_tickers": ",".join(sorted(missing)),
            },
        ) as staging:
            prices.write_parquet(staging / "prices.parquet", compression="zstd")
        return read_manifest(latest_snapshot(dest, "prices"))

    def _companies(self) -> list[dict[str, Any]]:
        path = self.snapshot_path("submissions") / _TICKERS_FILE
        loaded: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def _members(self, dataset: str, cik: int) -> Iterator[tuple[str, bytes]]:
        """Files for a company in a snapshot, whether loose or inside a bulk zip."""
        snapshot = self.snapshot_path(dataset)
        prefix = f"CIK{cik10(cik)}"
        archive = snapshot / f"{dataset}.zip"
        if archive.exists():
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if name.startswith(prefix):
                        yield name, bundle.read(name)
            return
        for path in sorted(snapshot.glob(f"{prefix}*.json")):
            yield path.name, path.read_bytes()

    def raw_securities(self) -> pl.DataFrame:
        first_dates = dict(
            self._dated_prices().group_by("cik").agg(pl.col("date").min()).sort("cik").iter_rows()
        )
        rows: list[dict[str, object]] = []
        for company in self._companies():
            cik = int(company["cik"])
            if cik not in first_dates:
                continue
            meta = None
            for name, content in self._members("submissions", cik):
                if name == f"CIK{cik10(cik)}.json":
                    meta = company_meta(json.loads(content))
            rows.append(
                {
                    "security_id": edgar_id(cik),
                    "company_id": company_from_cik(cik),
                    "cik": cik,
                    "ticker": str(company["ticker"]),
                    "name": str(company["name"]),
                    "exchange": _EXCHANGES.get(str(company["exchange"]), "OTHER"),
                    "share_type": "common",
                    "sic": meta.sic if meta is not None else None,
                    "valid_from": first_dates[cik],
                    "valid_to": None,
                }
            )
        return pl.DataFrame(rows).with_columns(
            pl.col("sic").cast(pl.Int16),
            pl.col("valid_from").cast(pl.Date),
            pl.col("valid_to").cast(pl.Date),
        )

    def _dated_prices(self) -> pl.DataFrame:
        """Prices from the XBRL era on, the only years with fundamentals on this path."""
        link = pl.DataFrame(
            [(str(row["ticker"]), int(row["cik"])) for row in self._companies()],
            schema={"ticker": pl.String, "cik": pl.Int64},
            orient="row",
        )
        prices = pl.read_parquet(self.snapshot_path("prices") / "prices.parquet")
        return prices.filter(pl.col("date") >= XBRL_ERA_START).join(link, on="ticker", how="inner")

    def raw_prices(self) -> pl.DataFrame:
        """Stooq closes joined to the shares figure public at each day's close.

        Stooq's closes are already adjusted, so ``ret`` is close to close, and ``mcap`` here
        mixes adjusted prices with as-reported shares. That is a dev-path approximation.
        """
        shares = (
            self.fundamentals()
            .filter(pl.col("item").eq("shares_out"))
            .select("cik", pl.col("value").alias("shares_out"), "available_at")
            .sort("available_at")
            .collect()
        )
        prices = self._dated_prices()
        dated = prices.with_columns(closes_on(prices["date"]).alias("close_at")).sort("close_at")
        joined = dated.join_asof(
            shares,
            left_on="close_at",
            right_on="available_at",
            by="cik",
            strategy="backward",
            check_sortedness=False,
        )
        return joined.with_columns(
            pl.format("EDGAR:{}:common", pl.col("cik")).alias("security_id")
        ).select("security_id", "date", "open", "high", "low", "close", "volume", "shares_out")

    def raw_delistings(self) -> pl.DataFrame:
        return pl.DataFrame(
            schema={
                "security_id": pl.String,
                "delist_date": pl.Date,
                "delist_code": pl.Int16,
                "reason": pl.String,
                "exchange_at_delist": pl.String,
                "dlret_raw": pl.Float64,
            }
        )

    def raw_actions(self) -> pl.DataFrame:
        return pl.DataFrame(
            schema={
                "security_id": pl.String,
                "ex_date": pl.Date,
                "action": pl.String,
                "value": pl.Float64,
                "new_ticker": pl.String,
            }
        )

    def raw_filings(self) -> pl.DataFrame:
        frames: list[pl.DataFrame] = []
        for company in self._companies():
            cik = int(company["cik"])
            for name, content in self._members("submissions", cik):
                payload = json.loads(content)
                block = payload["filings"]["recent"] if name == f"CIK{cik10(cik)}.json" else payload
                frames.append(parse_filing_block(cik, block))
        return pl.concat(frames, how="vertical")

    def raw_facts(self) -> pl.DataFrame:
        tags = all_tags(self.xbrl_map)
        frames: list[pl.DataFrame] = []
        for company in self._companies():
            cik = int(company["cik"])
            for _, content in self._members("companyfacts", cik):
                frames.append(parse_companyfacts(json.loads(content), tags))
        return pl.concat(frames, how="vertical").with_columns(
            pl.format("CIK:{}", pl.col("cik")).alias("company_id")
        )

    def snapshot_ids(self) -> dict[str, str]:
        return {name: self.snapshot_path(name).name.split("=")[1] for name in _DATASETS}
