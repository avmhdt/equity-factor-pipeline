"""AC-H-11: every number in the README comes from readme_numbers.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from efp.reports.report import render

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
NUMBERS = ROOT / "results" / "readme_numbers.json"
SECTIONS = (
    "Hypothesis",
    "Data",
    "Method",
    "Results",
    "What failed",
    "Limitations",
    "How to reproduce",
)
_IGNORED = (
    r"```.*?```",  # commands
    r"`[^`]*`",  # code
    r"\]\([^)]*\)",  # link targets
    r"\b(?:FR|AC-W|AC-H|NFR|DQ|D|P)-\d+\b",  # requirement ids
    r"\(?\b(?:19|20)\d{2}\)",  # citations
    r"\bFF-?\d+\b",  # factor and industry names
    r"\bO\+H\+L\+C\b",
    r"\b(?:Almgren|Zarinelli|Tóth) (?:\d{4}|et al)\b|\(Tóth 2011\)",  # study names
    r"\b2% daily volatility\b|\bAt 1% of daily volume\b|\bAt 10%|2007 to 2009",  # stated cases
    r"\$2-5B|\$500M-1B|\$100-250M|(?:[Uu]nder|up to) \$\d+[MB]",  # published band names
)
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Numeric tokens in a document, without code, links and identifiers."""
    for pattern in _IGNORED:
        text = re.sub(pattern, " ", text, flags=re.DOTALL)
    return {token.rstrip(",") for token in _NUMBER.findall(text)}


def known() -> set[str]:
    values = json.loads(NUMBERS.read_text(encoding="utf-8")).values()
    return {token.rstrip(",") for value in values for token in _NUMBER.findall(str(value))}


def test_every_number_in_the_readme_is_named() -> None:
    if not NUMBERS.is_file():
        pytest.skip("run efp report first")
    assert numbers_in(README.read_text(encoding="utf-8")) - known() - {"1"} == set()


def test_the_readme_has_every_section() -> None:
    text = README.read_text(encoding="utf-8")
    for section in SECTIONS:
        assert f"## {section}" in text


def test_the_readme_is_rendered_from_the_template() -> None:
    if not NUMBERS.is_file():
        pytest.skip("run efp report first")
    numbers = json.loads(NUMBERS.read_text(encoding="utf-8"))
    template = (ROOT / "reports" / "templates" / "README.md").read_text(encoding="utf-8")
    assert render(template, numbers) == README.read_text(encoding="utf-8")


def test_an_unknown_number_is_an_error() -> None:
    with pytest.raises(KeyError, match="no number named missing"):
        render("{{ missing }}", {})


def test_filling_a_placeholder() -> None:
    assert render("t {{headline.gross_alpha_t}}", {"headline.gross_alpha_t": "7.7"}) == "t 7.7"


def test_the_scanner_finds_numbers_and_skips_ids() -> None:
    text = "Sharpe 1.07 at $100M on 3,000 names (FR-22, FF-49), see `x 9.9` and (2016)."
    assert numbers_in(text) == {"1.07", "100", "3,000"}
