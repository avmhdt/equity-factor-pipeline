"""AC-H-06: five family memos, each short, with its numbers from the JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from efp.reports.numbers import FAMILIES
from efp.reports.report import render

ROOT = Path(__file__).parents[2]
TEMPLATES = ROOT / "reports" / "templates"
MEMOS = ROOT / "reports" / "memos"
NUMBERS = ROOT / "results" / "readme_numbers.json"
MAX_WORDS = 1000
MAX_FIGURES = 2
HEADINGS = ("## Hypothesis", "## Evidence", "## Why it might stop working")


@pytest.mark.parametrize("family", FAMILIES)
def test_a_template_has_the_required_shape(family: str) -> None:
    text = (TEMPLATES / f"memo_{family}.md").read_text(encoding="utf-8")
    for heading in HEADINGS:
        assert heading in text
    assert len(re.findall(r"!\[", text)) <= MAX_FIGURES


@pytest.mark.parametrize("family", FAMILIES)
def test_a_memo_stays_under_the_word_limit(family: str) -> None:
    path = MEMOS / f"{family}.md"
    if not path.is_file():
        pytest.skip("run efp report first")
    assert len(path.read_text(encoding="utf-8").split()) <= MAX_WORDS


@pytest.mark.parametrize("family", FAMILIES)
def test_a_memo_is_rendered_from_its_template(family: str) -> None:
    path = MEMOS / f"{family}.md"
    if not path.is_file() or not NUMBERS.is_file():
        pytest.skip("run efp report first")
    numbers = json.loads(NUMBERS.read_text(encoding="utf-8"))
    template = (TEMPLATES / f"memo_{family}.md").read_text(encoding="utf-8")
    assert render(template, numbers) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("family", FAMILIES)
def test_a_memo_points_at_figures_that_exist(family: str) -> None:
    path = MEMOS / f"{family}.md"
    if not path.is_file():
        pytest.skip("run efp report first")
    for target in re.findall(r"\]\((\.\./figures/[^)]+)\)", path.read_text(encoding="utf-8")):
        assert (path.parent / target).resolve().is_file(), target
