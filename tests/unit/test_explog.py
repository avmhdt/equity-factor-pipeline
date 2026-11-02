"""AC-W-13: the trial log follows contract v1.0."""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from datetime import date
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from efp.explog import COLUMNS, TrialRecord, log_failed, log_trial

MONTHS = [date(2024, month, 28) for month in range(1, 7)]
RETURNS = [0.01, -0.02, 0.03, 0.0, 0.02, -0.01]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def record(family: str = "replication") -> TrialRecord:
    series = pl.DataFrame({"date": MONTHS, "ret": RETURNS})
    return TrialRecord(family, {"factor": "umd", "net": False, "b": [1, 2]}, series, "M")


def test_a_trial_writes_one_row_and_its_series(tmp_path: Path) -> None:
    log_path = tmp_path / "results" / "trial_log.csv"
    run_id = log_trial(record(), log_path, tmp_path / "results" / "runs")
    row = rows(log_path)[0]
    assert tuple(row) == COLUMNS
    assert tuple(row)[:10] == (
        "run_id",
        "timestamp",
        "git_sha",
        "strategy_family",
        "params_json",
        "start",
        "end",
        "n_obs",
        "sharpe_annual",
        "returns_path",
    )
    assert row["run_id"] == run_id
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", run_id
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row["timestamp"])
    assert re.fullmatch(r"[0-9a-f]{40}(-dirty)?", row["git_sha"])
    assert row["params_json"] == '{"b":[1,2],"factor":"umd","net":false}'
    assert row["start"] == "2024-01-28"
    assert row["end"] == "2024-06-28"
    assert row["n_obs"] == "6"
    assert row["returns_path"] == f"runs/{run_id}/returns.parquet"
    assert row["x_periods_per_year"] == "12"
    assert row["x_returns_basis"] == "excess"


def test_sharpe_matches_the_parquet(tmp_path: Path) -> None:
    log_path = tmp_path / "trial_log.csv"
    run_id = log_trial(record(), log_path, tmp_path / "runs")
    table = pq.read_table(tmp_path / "runs" / run_id / "returns.parquet")
    assert table.num_rows == 6
    series = table.column("ret").to_pylist()
    expected = statistics.fmean(series) / statistics.stdev(series) * math.sqrt(12)
    assert float(rows(log_path)[0]["sharpe_annual"]) == pytest.approx(expected, abs=1e-9)
    metrics = json.loads((tmp_path / "runs" / run_id / "metrics.json").read_text("utf-8"))
    assert metrics["n_obs"] == 6


def test_the_log_is_append_only_lf_with_no_bom(tmp_path: Path) -> None:
    log_path = tmp_path / "trial_log.csv"
    log_trial(record(), log_path, tmp_path / "runs")
    log_trial(record(), log_path, tmp_path / "runs")
    raw = log_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert len(rows(log_path)) == 2


def test_a_gross_series_is_kept_beside_the_net_one(tmp_path: Path) -> None:
    series = pl.DataFrame({"date": MONTHS, "ret": RETURNS, "ret_gross": RETURNS})
    run_id = log_trial(
        TrialRecord("value", {"net": True}, series, "M"), tmp_path / "log.csv", tmp_path / "runs"
    )
    columns = pq.read_table(tmp_path / "runs" / run_id / "returns.parquet").column_names
    assert columns == ["date", "ret", "ret_gross"]


def test_a_flat_series_has_a_zero_sharpe(tmp_path: Path) -> None:
    series = pl.DataFrame({"date": MONTHS, "ret": [0.0] * 6})
    log_path = tmp_path / "log.csv"
    log_trial(TrialRecord("value", {}, series, "M"), log_path, tmp_path / "runs")
    assert rows(log_path)[0]["sharpe_annual"] == "0"


def test_failed_runs_count(tmp_path: Path) -> None:
    log_path = tmp_path / "log.csv"
    log_failed(log_path, "value", {"factor": "x"}, "no data")
    row = rows(log_path)[0]
    assert row["n_obs"] == "0"
    assert row["sharpe_annual"] == ""
    assert row["returns_path"] == ""
    assert row["x_note"] == "no data"


def test_outside_a_repository_the_sha_is_marked_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "log.csv"
    log_failed(log_path, "value", {}, "x")
    assert rows(log_path)[0]["git_sha"] == "0" * 40 + "-dirty"
