"""Static report figures: one job per chart, 150 dpi PNG."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import polars as pl
from matplotlib.axes import Axes
from matplotlib.figure import Figure

INK = "#1f2933"
MUTED = "#616e7c"
GRID = "#d9dee4"
SURFACE = "#ffffff"
SERIES = "#2c6fbb"
SECOND = "#b4531f"
DPI = 150
_DEV_ONLY = "DEV ONLY — survivorship-limited"


def _axes(width: float, height: float) -> tuple[Figure, Axes]:
    figure, axes = plt.subplots(figsize=(width, height), dpi=DPI)
    figure.patch.set_facecolor(SURFACE)
    axes.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID)
    axes.tick_params(colors=MUTED, length=0, labelsize=8)
    return figure, axes


def _finish(figure: Figure, axes: Axes, title: str, path: Path, dev_only: bool) -> None:
    axes.set_title(title, loc="left", color=INK, fontsize=10, pad=10)
    if dev_only:
        figure.text(0.5, 0.5, _DEV_ONLY, ha="center", va="center", color=MUTED, alpha=0.35)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)


def post_publication_bars(table: pl.DataFrame, path: Path, dev_only: bool = False) -> None:
    """Mean monthly return in-sample against post-publication, per signal, largest fall first.

    Args:
        table: ``signal``, ``mean_in_sample`` and ``mean_post_publication``, in decimals.
        path: output PNG.
        dev_only: watermark the figure (DQ-16).
    """
    rows = table.drop_nulls(["mean_in_sample", "mean_post_publication"]).sort(
        pl.col("mean_in_sample") - pl.col("mean_post_publication")
    )
    names = rows["signal"].to_list()
    before = [value * 100.0 for value in rows["mean_in_sample"].to_list()]
    after = [value * 100.0 for value in rows["mean_post_publication"].to_list()]
    figure, axes = _axes(7.0, 0.27 * len(names) + 1.4)
    positions = list(range(len(names)))
    axes.barh([p + 0.18 for p in positions], before, height=0.32, color=SERIES, label="In sample")
    axes.barh(
        [p - 0.18 for p in positions],
        after,
        height=0.32,
        color=SECOND,
        label="After publication",
    )
    axes.set_yticks(positions, names)
    axes.axvline(0.0, color=MUTED, linewidth=0.8)
    axes.grid(axis="x", color=GRID, linewidth=0.5)
    axes.set_axisbelow(True)
    axes.set_xlabel("Mean long-short return, % per month", color=MUTED, fontsize=8)
    axes.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)
    _finish(figure, axes, "Published anomalies, in sample and after publication", path, dev_only)
