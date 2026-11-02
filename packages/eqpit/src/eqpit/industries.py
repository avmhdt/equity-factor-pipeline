"""SIC to Fama-French industry maps, parsed from Kenneth French's definition files.

The two ``resources/Siccodes*.txt`` files are copied from the Kenneth R. French Data Library.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Literal

import numpy as np
import polars as pl
from numpy.typing import NDArray

Scheme = Literal["ff12", "ff49"]

_HEADER = re.compile(r"^\s*(\d+)\s+\S+")
_RANGE = re.compile(r"^\s+(\d{4})-(\d{4})")
_OTHER = {"ff12": 12, "ff49": 49}
_FILES = {"ff12": "Siccodes12.txt", "ff49": "Siccodes49.txt"}


def parse_siccodes(text: str) -> list[tuple[int, int, int]]:
    """Parse a French definition file into ``(industry, sic_low, sic_high)`` rows."""
    ranges: list[tuple[int, int, int]] = []
    industry = 0
    for line in text.splitlines():
        header = _HEADER.match(line)
        span = _RANGE.match(line)
        if span is not None and line.startswith((" " * 4, "\t")):
            ranges.append((industry, int(span.group(1)), int(span.group(2))))
        elif header is not None:
            industry = int(header.group(1))
    return ranges


@lru_cache(maxsize=2)
def _table(scheme: Scheme) -> NDArray[np.int16]:
    text = resources.files("eqpit.resources").joinpath(_FILES[scheme]).read_text(encoding="utf-8")
    table = np.full(10_000, _OTHER[scheme], dtype=np.int16)
    for industry, low, high in parse_siccodes(text):
        table[low : high + 1] = industry
    return table


def ff_industry(sic: int | None, scheme: Scheme = "ff49") -> int | None:
    """Fama-French industry for one SIC code; SICs no range lists fall to Other."""
    if sic is None or not 0 <= sic <= 9999:
        return None
    return int(_table(scheme)[sic])


def ff_series(sic: pl.Series, scheme: Scheme) -> pl.Series:
    """Vectorised ``ff_industry``; null or out-of-range SICs stay null."""
    filled = sic.cast(pl.Int32).fill_null(-1).to_numpy()
    valid = (filled >= 0) & (filled <= 9999)
    codes = np.full(len(filled), -1, dtype=np.int16)
    codes[valid] = _table(scheme)[filled[valid]]
    result = pl.Series(scheme, codes, dtype=pl.Int16)
    return result.set(result == -1, None)
