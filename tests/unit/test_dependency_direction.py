"""NFR-12: eqpit stands alone."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "packages" / "eqpit" / "src" / "eqpit"
FORBIDDEN = {"efp", "statsmodels", "sklearn", "linearmodels", "scipy", "yaml"}
ALLOWED_DEPENDENCIES = {
    "polars",
    "pyarrow",
    "httpx",
    "pydantic",
    "pydantic-settings",
    "exchange-calendars",
    "structlog",
    "numpy",
}


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_eqpit_imports_nothing_forbidden() -> None:
    for path in PACKAGE.rglob("*.py"):
        assert not imported_roots(path) & FORBIDDEN, path


def test_eqpit_never_imports_pandas_directly() -> None:
    for path in PACKAGE.rglob("*.py"):
        assert "pandas" not in imported_roots(path), path


def test_declared_dependencies_stay_minimal() -> None:
    config = tomllib.loads((ROOT / "packages" / "eqpit" / "pyproject.toml").read_text("utf-8"))
    names = {item.split(">")[0].split("[")[0] for item in config["project"]["dependencies"]}
    assert names <= ALLOWED_DEPENDENCIES


def test_both_packages_declare_the_mit_licence() -> None:
    for path in (ROOT / "pyproject.toml", ROOT / "packages" / "eqpit" / "pyproject.toml"):
        assert tomllib.loads(path.read_text("utf-8"))["project"]["license"] == "MIT"
