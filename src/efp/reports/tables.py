"""Aggregate tables, written as CSV and Markdown."""

from __future__ import annotations

from pathlib import Path

import polars as pl


def write_table(frame: pl.DataFrame, directory: Path, name: str) -> None:
    """Write one table to ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    frame.write_csv(directory / f"{name}.csv", line_terminator="\n", float_precision=6)
    header = "| " + " | ".join(frame.columns) + " |"
    rule = "|" + "---|" * frame.width
    rows = [
        "| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |"
        for row in frame.with_columns(pl.selectors.float().round(6)).iter_rows()
    ]
    text = "\n".join([header, rule, *rows]) + "\n"
    (directory / f"{name}.md").write_text(text, encoding="utf-8", newline="\n")
