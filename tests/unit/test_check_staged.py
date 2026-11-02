"""The pre-commit data guard (NFR-06)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def guard() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_staged.py"
    spec = importlib.util.spec_from_file_location("check_staged", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rejects_data_paths(capsys: pytest.CaptureFixture[str]) -> None:
    assert guard().main(["data/raw/x.csv", "README.md"]) == 1
    assert "data/raw/x.csv" in capsys.readouterr().err


def test_rejects_windows_separators() -> None:
    assert guard().problems(["data\\raw\\x.csv"])


def test_large_csv_only_allowed_in_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir()
    for name in ("big.csv", "results/big.csv"):
        (tmp_path / name).write_bytes(b"x" * 500_001)
    (tmp_path / "small.csv").write_bytes(b"x")
    module = guard()
    assert module.problems(["big.csv"])
    assert module.problems(["results/big.csv", "small.csv", "packages/data.py"]) == []
    assert module.main(["small.csv"]) == 0
