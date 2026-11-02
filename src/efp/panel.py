"""Build and store the signal panel: raw values, transforms, coverage and neutralization."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import structlog

import efp.signals  # noqa: F401  (registers every signal)
from efp.signals.neutralize import Mode, neutralize
from efp.signals.registry import compute_signals, registered, spec_of
from efp.signals.transforms import winsorize, zscore
from eqpit.store import PitStore

log = structlog.get_logger(__name__)

ALL_ELIGIBLE = 1_000_000
"""A universe size no market reaches, so every eligible name is kept."""


@dataclass(frozen=True)
class PanelConfig:
    """Panel rules from the experiment's ``signals`` section (DQ-11)."""

    lower: float = 0.01
    upper: float = 0.99
    min_coverage: float = 0.5
    min_names: int = 100
    neutral_mode: Mode = "sector_size_beta"
    universe: int = 3000


def universe_frame(store: PitStore, dates: Sequence[date], size: int = 3000) -> pl.DataFrame:
    """Universe rows on the rebalance dates: the largest ``size`` names."""
    history = store.labels().universe_history()
    return history.filter(pl.col("asof").is_in(list(dates)), pl.col("rank") <= size).select(
        "asof", "security_id", "company_mcap", "mcap", "rank", "exchange", "ff49"
    )


def apply_coverage(
    raw: pl.DataFrame, universe: pl.DataFrame, config: PanelConfig
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Drop signal-dates under the coverage rule; return kept rows and what was dropped."""
    sizes = universe.group_by("asof", maintain_order=True).agg(pl.len().alias("universe"))
    counts = (
        raw.drop_nulls("raw")
        .group_by("signal", "asof", maintain_order=True)
        .agg(pl.len().alias("names"))
        .join(sizes, on="asof", how="left")
    )
    enough = (pl.col("names") >= config.min_names) & (
        pl.col("names") >= config.min_coverage * pl.col("universe")
    )
    keep = counts.filter(enough).select("signal", "asof")
    dropped = counts.filter(~enough).sort("signal", "asof")
    return raw.join(keep, on=["signal", "asof"], how="inner"), dropped


def build_panel(
    store: PitStore,
    ids: Sequence[str],
    dates: Sequence[date],
    config: PanelConfig | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Compute every signal over ``dates``.

    Returns:
        The panel, one row per (asof, security, signal) with ``raw``, ``winsorized``, sign-aligned
        ``z`` and ``z_neutral``; and the signal-dates dropped for low coverage.
    """
    rules = config or PanelConfig()
    needed = sorted({*ids, "beta", "size"})
    raw = compute_signals(store, needed, dates, rules.universe)
    universe = universe_frame(store, dates, rules.universe)
    exposures = (
        raw.filter(pl.col("signal").is_in(["beta", "size"]))
        .pivot(on="signal", index=["asof", "security_id"], values="raw")
        .rename({"size": "log_size"})
    )
    kept, dropped = apply_coverage(raw.filter(pl.col("signal").is_in(list(ids))), universe, rules)
    directions = pl.DataFrame(
        {"signal": list(ids), "direction": [float(spec_of(name).direction) for name in ids]}
    )
    clipped = winsorize(
        kept.sort("signal", "asof", "security_id"),
        "raw",
        rules.lower,
        rules.upper,
        by=["signal", "asof"],
    )
    aligned = clipped.join(directions, on="signal").with_columns(
        (pl.col("winsorized") * pl.col("direction")).alias("aligned")
    )
    scored = zscore(aligned, "aligned", by=["signal", "asof"])
    framed = scored.join(
        universe.select("asof", "security_id", "ff49"), on=["asof", "security_id"], how="left"
    ).join(exposures, on=["asof", "security_id"], how="left")
    parts: list[pl.DataFrame] = []
    for (name,), part in framed.group_by(["signal"], maintain_order=True):
        parts.append(
            neutralize(
                part.sort("asof", "security_id"),
                "z",
                rules.neutral_mode,
                exempt_size=name == "size",
            )
        )
        log.info("signal_built", signal=name, rows=part.height)
    panel = pl.concat(parts, how="vertical").select(
        "asof",
        "security_id",
        "signal",
        "raw",
        "winsorized",
        "z",
        "z_neutral",
        "input_available_at",
    )
    return panel.sort("signal", "asof", "security_id"), dropped


def write_panel(panel: pl.DataFrame, root: Path) -> None:
    """Replace the stored panel with one file per signal."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for (name,), part in panel.group_by(["signal"], maintain_order=True):
        folder = root / f"signal={name}"
        folder.mkdir()
        part.write_parquet(folder / "part-0.parquet", compression="zstd", statistics=True)


def read_panel(root: Path, ids: Sequence[str] | None = None) -> pl.DataFrame:
    """Read the stored panel, optionally only some signals."""
    frame = pl.read_parquet(root / "**" / "*.parquet")
    if ids is not None:
        frame = frame.filter(pl.col("signal").is_in(list(ids)))
    return frame.select(
        "asof",
        "security_id",
        "signal",
        "raw",
        "winsorized",
        "z",
        "z_neutral",
        "input_available_at",
    ).sort("signal", "asof", "security_id")


def all_signals() -> tuple[str, ...]:
    """Every registered signal id."""
    return registered()
