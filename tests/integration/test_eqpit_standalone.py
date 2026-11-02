"""AC-H-09: the reuse example runs with the research stack blocked."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "run_reuse_example.py"
FORBIDDEN = {"efp", "statsmodels", "sklearn", "linearmodels"}


def test_the_reuse_example_runs() -> None:
    plain = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env={**plain, "PYTHONIOENCODING": "ascii"},
    )
    assert done.returncode == 0, done.stderr
    assert "refuses forward returns" in done.stdout


def test_the_blocker_blocks() -> None:
    code = (
        "import runpy, sys\n"
        f"module = runpy.run_path({str(SCRIPT)!r}, run_name='loaded')\n"
        "sys.meta_path.insert(0, module['_Blocker']())\n"
        "import statsmodels.api\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode != 0
    assert "must not import" in done.stderr


@pytest.mark.parametrize(
    "path", sorted((ROOT / "packages" / "eqpit" / "src").rglob("*.py")), ids=lambda path: path.name
)
def test_eqpit_imports_nothing_forbidden(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        assert not {name.split(".")[0] for name in names} & FORBIDDEN, path
