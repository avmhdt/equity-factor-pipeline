"""Run the example in docs/reuse.md with the research stack blocked (AC-H-09)."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

BLOCKED = ("efp", "statsmodels", "sklearn", "linearmodels")
DOC = Path(__file__).parents[1] / "docs" / "reuse.md"


class _Blocker(importlib.abc.MetaPathFinder):
    """Fail any import of a package eqpit must not depend on."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(f"eqpit must not import {fullname}")
        return None


def example(text: str) -> str:
    """The first Python block of a Markdown document."""
    found = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if found is None:
        raise ValueError("no python block")
    return found.group(1)


def main() -> None:
    """Run the example."""
    sys.meta_path.insert(0, _Blocker())
    exec(compile(example(DOC.read_text(encoding="utf-8")), str(DOC), "exec"), {"__name__": "doc"})


if __name__ == "__main__":
    main()
