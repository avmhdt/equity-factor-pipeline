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
LABEL_GAP = 0.055
LOG_TICKS = (1, 2, 5, 10, 20, 50, 100)
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
    _finish(
        figure,
        axes,
        "Published anomalies, in sample and after publication (OSAP series)",
        path,
        dev_only,
    )


def capacity_lines(curve: pl.DataFrame, target: float, path: Path, dev_only: bool = False) -> None:
    """Net alpha against fund size, one line per impact multiplier, with the half-alpha level.

    Args:
        curve: ``impact_multiplier``, ``aum``, ``net_alpha_annual``.
        target: half the zero-cost alpha, in decimals per year.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    figure, axes = _axes(7.0, 3.8)
    styles = {0.5: (MUTED, "--"), 1.0: (SERIES, "-"), 2.0: (SECOND, "--")}
    for (multiplier,), part in curve.sort("impact_multiplier", "aum").group_by(
        ["impact_multiplier"], maintain_order=True
    ):
        color, line = styles.get(float(multiplier), (MUTED, ":"))
        axes.plot(
            part["aum"].to_list(),
            [value * 100.0 for value in part["net_alpha_annual"].to_list()],
            color=color,
            linestyle=line,
            linewidth=2.0 if multiplier == 1.0 else 1.2,
            label=f"Impact x{multiplier:g}",
        )
    axes.axhline(target * 100.0, color=INK, linewidth=0.8)
    axes.text(
        curve["aum"].min(),  # type: ignore[arg-type]
        target * 100.0,
        " half the zero-cost alpha",
        color=INK,
        fontsize=8,
        va="bottom",
    )
    axes.set_xscale("log")
    axes.grid(axis="y", color=GRID, linewidth=0.5)
    axes.set_axisbelow(True)
    axes.set_xlabel("Fund size, USD", color=MUTED, fontsize=8)
    axes.set_ylabel("Net FF5 + UMD alpha, % per year", color=MUTED, fontsize=8)
    axes.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)
    _finish(figure, axes, "Net alpha by fund size", path, dev_only)


def _small_multiples(count: int, width: float, height: float) -> tuple[Figure, list[Axes]]:
    figure, grid = plt.subplots(1, count, figsize=(width, height), dpi=DPI, squeeze=False)
    figure.patch.set_facecolor(SURFACE)
    panels = list(grid[0])
    for axes in panels:
        axes.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            axes.spines[side].set_visible(False)
        axes.spines["bottom"].set_color(GRID)
        axes.tick_params(colors=MUTED, length=0, labelsize=7)
        axes.grid(color=GRID, linewidth=0.5)
        axes.set_axisbelow(True)
    return figure, panels


def _save(figure: Figure, title: str, path: Path, dev_only: bool = False) -> None:
    figure.suptitle(title, x=0.01, ha="left", color=INK, fontsize=10)
    if dev_only:
        figure.text(0.5, 0.5, _DEV_ONLY, ha="center", va="center", color=MUTED, alpha=0.35)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)


def french_scatter(joined: pl.DataFrame, path: Path) -> None:
    """Our monthly factor returns against French's, one panel per factor.

    Args:
        joined: ``month``, ``factor``, ``ret`` (ours) and ``theirs``, in decimals.
        path: output file.
    """
    names = sorted(joined["factor"].unique().to_list())
    figure, panels = _small_multiples(len(names), 9.0, 2.4)
    for axes, name in zip(panels, names, strict=True):
        part = joined.filter(pl.col("factor").eq(name))
        axes.scatter(
            [value * 100.0 for value in part["theirs"].to_list()],
            [value * 100.0 for value in part["ret"].to_list()],
            s=5,
            color=SERIES,
            alpha=0.5,
            linewidths=0,
        )
        correlation = part.select(pl.corr("ret", "theirs")).item()
        axes.set_title(f"{name.upper()}  r = {correlation:.3f}", color=INK, fontsize=8, loc="left")
        axes.set_xlabel("French, % per month", color=MUTED, fontsize=7)
    panels[0].set_ylabel("Ours, % per month", color=MUTED, fontsize=7)
    _save(figure, "Factors built here against Ken French's", path)


def french_cumulative(joined: pl.DataFrame, path: Path) -> None:
    """Cumulative factor returns, ours and French's, one panel per factor.

    Args:
        joined: ``month``, ``factor``, ``ret`` (ours) and ``theirs``, in decimals.
        path: output file.
    """
    names = sorted(joined["factor"].unique().to_list())
    figure, panels = _small_multiples(len(names), 9.0, 2.4)
    for axes, name in zip(panels, names, strict=True):
        part = (
            joined.filter(pl.col("factor").eq(name))
            .sort("month")
            .with_columns(
                pl.col("ret").cum_sum().mul(100.0).alias("ours_total"),
                pl.col("theirs").cum_sum().mul(100.0).alias("theirs_total"),
            )
        )
        months = part["month"].to_list()
        axes.plot(months, part["ours_total"].to_list(), color=SERIES, linewidth=1.5, label="Ours")
        axes.plot(
            months, part["theirs_total"].to_list(), color=SECOND, linewidth=1.5, label="French"
        )
        axes.set_title(name.upper(), color=INK, fontsize=8, loc="left")
        axes.tick_params(axis="x", labelrotation=45)
    panels[0].set_ylabel("Sum of monthly returns, %", color=MUTED, fontsize=7)
    panels[0].legend(frameon=False, fontsize=7, labelcolor=INK)
    _save(figure, "Cumulative factor returns", path)


def family_ic(ic: pl.DataFrame, family: str, path: Path, dev_only: bool = False) -> None:
    """A family's mean rank IC by month, with its 12-month average.

    Args:
        ic: ``signal``, ``asof``, ``ic``.
        family: family name, for the title.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    monthly = (
        ic.sort("asof", "signal")
        .group_by("asof", maintain_order=True)
        .agg(pl.col("ic").sort().mean())
        .with_columns(pl.col("ic").rolling_mean(12, min_samples=6).alias("average"))
    )
    figure, axes = _axes(7.0, 2.8)
    months = monthly["asof"].to_list()
    axes.bar(months, monthly["ic"].to_list(), width=25, color=GRID)
    axes.plot(months, monthly["average"].to_list(), color=SERIES, linewidth=2.0)
    axes.axhline(0.0, color=MUTED, linewidth=0.8)
    axes.set_ylabel("Rank IC, one month ahead", color=MUTED, fontsize=8)
    label = family.replace("_", "-").capitalize()
    _finish(figure, axes, f"{label}: monthly IC and its 12-month average", path, dev_only)


def family_decay(decay: pl.DataFrame, family: str, path: Path, dev_only: bool = False) -> None:
    """Mean IC by forecast horizon, one line per signal in a family.

    Args:
        decay: rows of ``decay`` for the family's signals.
        family: family name, for the title.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    rows = decay.filter(pl.col("curve").eq("horizon"), pl.col("neutralize").eq("none"))
    figure, axes = _axes(7.0, 2.8)
    ends: list[tuple[float, str, float]] = []
    for (name,), part in rows.sort("signal", "step").group_by(["signal"], maintain_order=True):
        steps = part["step"].to_list()
        means = part["mean_ic"].to_list()
        axes.plot(steps, means, color=SERIES, linewidth=1.2, alpha=0.75, marker="o", markersize=3)
        ends.append((means[-1], str(name), steps[-1]))
    low, high = axes.get_ylim()
    gap = (high - low) * LABEL_GAP
    previous = low - gap
    for value, name, step in sorted(ends):
        position = max(value, previous + gap)
        axes.annotate(
            name, (step, value), (step + 0.15, position), color=INK, fontsize=6, va="center"
        )
        previous = position
    axes.axhline(0.0, color=MUTED, linewidth=0.8)
    axes.grid(axis="y", color=GRID, linewidth=0.5)
    axes.set_axisbelow(True)
    axes.set_xlabel("Forecast horizon, months", color=MUTED, fontsize=8)
    axes.set_ylabel("Mean rank IC", color=MUTED, fontsize=8)
    label = family.replace("_", "-").capitalize()
    _finish(figure, axes, f"{label}: IC by horizon", path, dev_only)


def neutralization_ics(summary: pl.DataFrame, path: Path, dev_only: bool = False) -> None:
    """Mean IC before and after neutralization, per signal, top universe.

    Args:
        summary: the ``ic_summary`` table.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    top = summary.filter(pl.col("universe").eq(summary["universe"].max()))
    wide = top.pivot(on="neutralize", index="signal", values="mean_ic").sort("none")
    names = wide["signal"].to_list()
    before = [value * 100.0 for value in wide["none"].to_list()]
    after = wide["sector_size_beta"].to_list()
    figure, axes = _axes(7.0, 0.24 * len(names) + 1.4)
    positions = list(range(len(names)))
    axes.scatter(before, positions, s=26, color=SERIES, label="Before", zorder=3)
    present = [(value, index) for index, value in enumerate(after) if value is not None]
    axes.scatter(
        [value * 100.0 for value, _ in present],
        [index for _, index in present],
        s=26,
        color=SECOND,
        label="After",
        zorder=3,
    )
    axes.set_yticks(positions, names)
    axes.axvline(0.0, color=MUTED, linewidth=0.8)
    axes.grid(axis="x", color=GRID, linewidth=0.5)
    axes.set_axisbelow(True)
    axes.set_xlabel("Mean rank IC, %", color=MUTED, fontsize=8)
    axes.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)
    _finish(figure, axes, "Neutralizing to sector, size and beta", path, dev_only)


def execution_bars(timing: pl.DataFrame, path: Path, dev_only: bool = False) -> None:
    """Gross and net Sharpe by composite for each execution price.

    Args:
        timing: the ``execution_timing`` table.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    modes = ("close", "next_open", "next_close", "next_vwap_proxy")
    labels = ("Close", "Next open", "Next close", "VWAP proxy")
    shades = (GRID, "#b7c0ca", MUTED, SERIES)
    names = sorted(timing["composite"].unique().to_list())
    short = [
        name.removeprefix("family_").removesuffix("_sector_size_beta").replace("equal", "composite")
        for name in names
    ]
    figure, panels = _small_multiples(2, 9.0, 3.2)
    for axes, column, heading in zip(
        panels, ("gross_sharpe", "net_sharpe"), ("Gross Sharpe", "Net Sharpe"), strict=True
    ):
        for offset, (mode, label, shade) in enumerate(zip(modes, labels, shades, strict=True)):
            part = timing.filter(pl.col("execution").eq(mode)).sort("composite")
            axes.bar(
                [index + 0.2 * (offset - 1.5) for index in range(len(names))],
                part[column].to_list(),
                width=0.18,
                color=shade,
                label=label,
            )
        axes.set_xticks(range(len(names)), short, rotation=45, ha="right")
        axes.axhline(0.0, color=MUTED, linewidth=0.8)
        axes.set_title(heading, color=INK, fontsize=8, loc="left")
    panels[0].legend(frameon=False, fontsize=7, labelcolor=INK)
    _save(figure, "Sharpe by execution price", path, dev_only)


def gross_and_net(
    uncapped: pl.DataFrame, capped: pl.DataFrame, path: Path, dev_only: bool = False
) -> None:
    """Cumulative gross and net returns of the headline composite, one panel per pair.

    Args:
        uncapped: the monthly series without an ADV cap.
        capped: the monthly series with the cap.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    figure, axes = _axes(7.0, 3.4)
    months = uncapped["month"].to_list()
    gross = uncapped["ret_gross"].cum_sum() * 100.0
    net = uncapped["ret"].cum_sum() * 100.0
    limited = capped["ret"].cum_sum() * 100.0
    axes.plot(months, gross.to_list(), color=SERIES, linewidth=2.0, label="Gross")
    axes.plot(
        capped["month"].to_list(),
        limited.to_list(),
        color=MUTED,
        linewidth=1.5,
        label="Net, ADV cap",
    )
    axes.plot(months, net.to_list(), color=SECOND, linewidth=2.0, label="Net, no cap")
    axes.axhline(0.0, color=MUTED, linewidth=0.8)
    axes.grid(axis="y", color=GRID, linewidth=0.5)
    axes.set_axisbelow(True)
    axes.set_ylabel("Sum of monthly returns, %", color=MUTED, fontsize=8)
    axes.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=INK)
    _finish(figure, axes, "Headline composite: gross and net", path, dev_only)


def spread_model_bars(
    checks: pl.DataFrame, table: pl.DataFrame, path: Path, dev_only: bool = False
) -> None:
    """Each model's half-spreads against published ones, and the headline book's net Sharpe.

    Args:
        checks: ``spread_model_checks``.
        table: ``spread_models`` rows for the headline composite.
        path: output file.
        dev_only: watermark the figure (DQ-16).
    """
    models = ("corwin_schultz", "abdi_ranaldo", "size_bucket")
    labels = ("Corwin-Schultz", "Abdi-Ranaldo", "Size table")
    figure, panels = _small_multiples(2, 9.0, 3.4)
    left, right = panels
    bands = checks.filter(pl.col("model").eq(models[0]))["band"].to_list()
    positions = list(range(len(bands)))
    published = checks.filter(pl.col("model").eq(models[0]))["benchmark_bps"].to_list()
    left.plot(positions, published, color=INK, linewidth=2.0, marker="o", label="Published")
    for model, label, color in zip(models[:2], labels[:2], (SECOND, SERIES), strict=True):
        part = checks.filter(pl.col("model").eq(model))
        left.plot(positions, part["estimate_bps"].to_list(), color=color, marker="o", label=label)
    left.set_yscale("log")
    left.set_yticks(LOG_TICKS, [str(tick) for tick in LOG_TICKS])
    left.minorticks_off()
    left.set_xticks(positions, bands, rotation=45, ha="right")
    left.set_ylabel("Half-spread, bps (log scale)", color=MUTED, fontsize=7)
    left.set_title("Estimates against published spreads", color=INK, fontsize=8, loc="left")
    left.legend(frameon=False, fontsize=7, labelcolor=INK)
    full = table.filter(pl.col("period").eq("full"))
    recent = table.filter(pl.col("period").eq("2010_on"))
    series = (
        ("No cap, full sample", full, "net_sharpe", GRID),
        ("ADV cap, full sample", full, "capped_net_sharpe", "#b7c0ca"),
        ("No cap, 2010 on", recent, "net_sharpe", MUTED),
        ("ADV cap, 2010 on", recent, "capped_net_sharpe", SERIES),
    )
    for offset, (label, frame, column, shade) in enumerate(series):
        values = [frame.filter(pl.col("spread_model").eq(model))[column].item() for model in models]
        right.bar(
            [index + 0.2 * (offset - 1.5) for index in range(len(models))],
            values,
            width=0.18,
            color=shade,
            label=label,
        )
    right.set_xticks(range(len(models)), labels)
    right.axhline(0.0, color=MUTED, linewidth=0.8)
    low, _ = right.get_ylim()
    right.set_ylim(low * 1.3, 0.0)
    right.set_title("Headline book: net Sharpe", color=INK, fontsize=8, loc="left")
    right.legend(frameon=False, fontsize=7, labelcolor=INK, loc="lower center", ncols=2)
    _save(figure, "Spread models side by side", path, dev_only)
