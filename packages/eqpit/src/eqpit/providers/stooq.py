"""The owner's local Stooq snapshot, read once into Parquet. Dev only (P-12)."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

SNAPSHOT_DATE = "2026-10-30"
_FILE_SUFFIX = ".us.txt"
_COLUMNS = {
    "ticker": pl.String,
    "per": pl.String,
    "date": pl.String,
    "time": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "openint": pl.Float64,
}
SPLIT_PROBES = (("AAPL", date(2020, 8, 28), date(2020, 8, 31), 4.0),)
"""Known splits: (ticker, last pre-split day, ex-date, ratio)."""


@dataclass(frozen=True)
class AdjustmentCheck:
    """Result of probing the snapshot for split adjustment."""

    probes: int
    split_adjusted: bool


def build_index(root: Path) -> dict[str, Path]:
    """Walk the tree once and map ticker to file; stock folders only, never ETFs."""
    index: dict[str, Path] = {}
    for directory, _, names in os.walk(root):
        if " stocks" not in directory.replace("\\", "/"):
            continue
        for name in names:
            if name.endswith(_FILE_SUFFIX):
                index[name.removesuffix(_FILE_SUFFIX).upper()] = Path(directory) / name
    return index


def read_prices(path: Path, ticker: str) -> pl.DataFrame:
    """Read one Stooq file; the ``ticker`` given is stored, not the file's own suffix form."""
    if path.stat().st_size == 0:
        return pl.DataFrame(schema={"ticker": pl.String, "date": pl.Date})
    frame = pl.read_csv(
        path,
        has_header=True,
        new_columns=list(_COLUMNS),
        schema_overrides=_COLUMNS,
    )
    return frame.select(
        pl.lit(ticker).alias("ticker"),
        pl.col("date").str.to_date("%Y%m%d"),
        "open",
        "high",
        "low",
        "close",
        "volume",
    )


def import_tickers(root: Path, tickers: Iterable[str]) -> tuple[pl.DataFrame, list[str]]:
    """Read the wanted tickers; returns prices and the tickers Stooq does not have."""
    index = build_index(root)
    frames: list[pl.DataFrame] = []
    missing: list[str] = []
    for ticker in tickers:
        path = index.get(ticker.upper())
        if path is None:
            missing.append(ticker)
            continue
        frame = read_prices(path, ticker)
        if frame.is_empty():
            missing.append(ticker)
        else:
            frames.append(frame)
    prices = pl.concat(frames, how="vertical") if frames else pl.DataFrame()
    return prices.sort("ticker", "date") if frames else prices, missing


def check_split_adjustment(prices: pl.DataFrame) -> AdjustmentCheck:
    """Verify, rather than assume, that the snapshot is split-adjusted.

    An unadjusted file would show the split's full price drop across the ex-date.
    """
    probes = 0
    adjusted = True
    for ticker, before, after, ratio in SPLIT_PROBES:
        rows = prices.filter(pl.col("ticker").eq(ticker), pl.col("date").is_in([before, after]))
        if rows.height != 2:
            continue
        probes += 1
        closes = rows.sort("date")["close"].to_list()
        adjusted = adjusted and closes[0] / closes[1] < ratio * 0.5
    return AdjustmentCheck(probes, adjusted)
