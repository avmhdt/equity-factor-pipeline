"""``efp report``: figures, numbers and documents from the tables (FR-31)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from efp.reports import figures
from efp.reports.numbers import FAMILIES, build_numbers, write_numbers

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.$]+)\s*\}\}")
MOMENTUM_FACTORS = ("smb", "hml", "umd", "rmw", "cma")


@dataclass(frozen=True)
class ReportPaths:
    """Where a report reads and writes.

    Args:
        tables: CSV tables.
        inputs: series kept for figures, outside git.
        figures: PNG output.
        numbers: ``readme_numbers.json``.
        templates: document templates, or None to render no documents.
        memos: memo output.
        readme: README output.
    """

    tables: Path
    inputs: Path
    figures: Path
    numbers: Path
    templates: Path | None = None
    memos: Path | None = None
    readme: Path | None = None


def render(template: str, numbers: dict[str, str]) -> str:
    """Fill ``{{ name }}`` placeholders; an unknown name is an error, never a blank."""

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in numbers:
            raise KeyError(f"no number named {name}")
        return numbers[name]

    return _PLACEHOLDER.sub(fill, template)


def _read(paths: ReportPaths, name: str) -> pl.DataFrame | None:
    path = paths.tables / f"{name}.csv"
    return pl.read_csv(path) if path.is_file() else None


def _family_figures(paths: ReportPaths, dev_only: bool) -> None:
    decay = _read(paths, "decay")
    summary = _read(paths, "ic_summary")
    for family in FAMILIES:
        source = paths.inputs / f"ic_{family}.parquet"
        if source.is_file():
            figures.family_ic(
                pl.read_parquet(source), family, paths.figures / f"ic_{family}.png", dev_only
            )
        if decay is not None and summary is not None:
            members = summary.filter(pl.col("family").eq(family))["signal"].unique().to_list()
            figures.family_decay(
                decay.filter(pl.col("signal").is_in(members)),
                family,
                paths.figures / f"decay_{family}.png",
                dev_only,
            )


def _book_figures(paths: ReportPaths, headline: str, execution: str, dev_only: bool) -> None:
    timing = _read(paths, "execution_timing")
    if timing is not None:
        figures.execution_bars(timing, paths.figures / "execution_timing.png", dev_only)
    uncapped = paths.inputs / f"{headline}__{execution}.parquet"
    capped = paths.inputs / f"{headline}__{execution}__capped.parquet"
    if uncapped.is_file() and capped.is_file():
        figures.gross_and_net(
            pl.read_parquet(uncapped),
            pl.read_parquet(capped),
            paths.figures / "composite_gross_net.png",
            dev_only,
        )


def _french_figures(paths: ReportPaths, french: pl.DataFrame | None) -> None:
    ours = paths.inputs / "french_ours.parquet"
    if french is None or not ours.is_file():
        return
    series = pl.read_parquet(ours).filter(pl.col("factor").is_in(MOMENTUM_FACTORS))
    published = french.unpivot(index="month", on=list(MOMENTUM_FACTORS), variable_name="factor")
    joined = series.join(
        published.rename({"value": "theirs"}), on=["month", "factor"], how="inner"
    ).drop_nulls()
    figures.french_scatter(joined, paths.figures / "french_scatter.png")
    figures.french_cumulative(joined, paths.figures / "french_cumulative.png")


def write_report(
    paths: ReportPaths,
    log_path: Path,
    headline: str,
    execution: str,
    french: pl.DataFrame | None = None,
    dev_only: bool = False,
) -> dict[str, str]:
    """Render everything that exists; a run without a table skips what needs it."""
    summary = _read(paths, "ic_summary")
    if summary is not None:
        figures.neutralization_ics(summary, paths.figures / "neutralization_ic.png", dev_only)
    _family_figures(paths, dev_only)
    _book_figures(paths, headline, execution, dev_only)
    _french_figures(paths, french)
    numbers = build_numbers(paths.tables, log_path, headline, execution)
    write_numbers(numbers, paths.numbers)
    if paths.templates is not None and paths.memos is not None:
        paths.memos.mkdir(parents=True, exist_ok=True)
        for family in FAMILIES:
            source = paths.templates / f"memo_{family}.md"
            text = render(source.read_text(encoding="utf-8"), numbers)
            (paths.memos / f"{family}.md").write_text(text, encoding="utf-8", newline="\n")
    if paths.templates is not None and paths.readme is not None:
        text = render((paths.templates / "README.md").read_text(encoding="utf-8"), numbers)
        paths.readme.write_text(text, encoding="utf-8", newline="\n")
    return numbers
