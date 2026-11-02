"""Write every point-in-time table for a provider (``efp data build-pit``)."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import structlog

from eqpit import calendar
from eqpit.pit.labels import build_forward_returns
from eqpit.pit.policy import PitPolicy
from eqpit.pit.universe import build_universe
from eqpit.providers.base import DataProvider

log = structlog.get_logger(__name__)

MANIFEST_NAME = "manifest.json"
DEV_ONLY_LABEL = "DEV ONLY — survivorship-limited"


def _write(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd", statistics=True)


def _write_table(frame: pl.DataFrame, out: Path, table: str) -> None:
    _write(frame, out / table / "part-0.parquet")


def _write_prices(prices: pl.DataFrame, out: Path) -> None:
    years = prices.with_columns(pl.col("date").dt.year().alias("_year"))
    for (year,), part in years.group_by(["_year"], maintain_order=False):
        _write(part.drop("_year"), out / "prices_daily" / f"year={year}" / "part-0.parquet")


def build_pit(provider: DataProvider, out: Path, policy: PitPolicy | None = None) -> None:
    """Build securities, prices, delistings, actions, filings, fundamentals and universe.

    Tables replace whatever is in ``out``. Every table is sorted, so the same raw snapshot
    gives the same content. ``manifest.json`` records where the tables came from.
    """
    rules = policy or getattr(provider, "policy", None) or PitPolicy()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    securities = provider.securities().collect()
    log.info("pit_table", table="securities", rows=securities.height)
    _write_table(securities, out, "securities")
    actions = provider.corp_actions().collect()
    _write_table(actions, out, "corp_actions")
    log.info("pit_table", table="corp_actions", rows=actions.height)
    prices = provider.prices_daily().collect()
    _write_prices(prices, out)
    log.info("pit_table", table="prices_daily", rows=prices.height)
    delistings = provider.delistings().collect()
    _write_table(delistings, out, "delistings")
    log.info("pit_table", table="delistings", rows=delistings.height)
    filings = provider.filings().collect()
    _write_table(filings, out, "filings")
    log.info("pit_table", table="filings", rows=filings.height)
    fundamentals = provider.fundamentals().collect()
    _write_table(fundamentals, out, "fundamentals_pit")
    log.info("pit_table", table="fundamentals_pit", rows=fundamentals.height)
    first, last = prices["date"].min(), prices["date"].max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    ends = calendar.month_ends(first, last)
    universe = build_universe(prices, securities, delistings, ends, rules)
    _write_table(universe, out, "universe_monthly")
    log.info("pit_table", table="universe_monthly", rows=universe.height)
    risk_free = provider.risk_free()
    forward = build_forward_returns(
        prices,
        delistings,
        ends,
        rules.horizons_months,
        risk_free.collect() if risk_free is not None else None,
    )
    _write_table(forward, out, "forward_returns")
    log.info("pit_table", table="forward_returns", rows=forward.height)
    limited = not provider.survivorship_free
    manifest: dict[str, Any] = {
        "provider": provider.name,
        "survivorship_limited": limited,
        "label": DEV_ONLY_LABEL if limited else "",
        "raw_snapshots": provider.snapshot_ids(),
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "policy": asdict(rules),
        "row_counts": {
            "securities": securities.height,
            "corp_actions": actions.height,
            "prices_daily": prices.height,
            "delistings": delistings.height,
            "filings": filings.height,
            "fundamentals_pit": fundamentals.height,
            "universe_monthly": universe.height,
            "forward_returns": forward.height,
        },
        "company_count": securities["company_id"].n_unique(),
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def rebuild_universe(root: Path, policy: PitPolicy) -> int:
    """Rewrite ``universe_monthly`` from stored tables; returns the row count."""
    prices = pl.read_parquet(root / "prices_daily" / "**" / "*.parquet")
    securities = pl.read_parquet(root / "securities" / "**" / "*.parquet")
    delistings = pl.read_parquet(root / "delistings" / "**" / "*.parquet")
    first, last = prices["date"].min(), prices["date"].max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    ends = calendar.month_ends(first, last)
    universe = build_universe(prices, securities, delistings, ends, policy)
    _write_table(universe, root, "universe_monthly")
    return universe.height


def read_pit_manifest(root: Path) -> dict[str, Any]:
    """Read the manifest of a point-in-time build."""
    loaded: dict[str, Any] = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    return loaded
