"""Trial log, backtest-overfitting-lab contract v1.0 (FR-30).

Vendored from the reference writer in that repo's ``docs/trial-log.md``, with the two extra
columns section 7 of the spec names. Only the contract's owners may change the ten columns.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import statistics
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

COLUMNS = (
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
    "x_periods_per_year",
    "x_note",
    "x_returns_basis",
    "x_selected",
)
PERIODS_PER_YEAR = {"D": 252, "M": 12}


@dataclass(frozen=True)
class TrialRecord:
    """One evaluated trial.

    Args:
        strategy_family: contract family, such as ``replication``.
        params: every choice that defines the trial, JSON types only.
        returns: ``date`` and ``ret`` (decimal excess return), optionally ``ret_gross``.
        freq: D or M.
    """

    strategy_family: str
    params: dict[str, object]
    returns: pl.DataFrame
    freq: Literal["D", "M"]


def _git_sha(log_path: Path) -> str:
    """Commit hash, ``-dirty`` for tracked changes to code; results are outputs, not code."""

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout

    try:
        sha = git("rev-parse", "HEAD").strip()
        root = Path(git("rev-parse", "--show-toplevel").strip())
        log = log_path.resolve().relative_to(root.resolve()).as_posix()
        output = (root / "results").as_posix()
        status = git(
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            ".",
            f":(exclude){log}",
            f":(exclude,top){Path(output).relative_to(root).as_posix()}",
        )
    except (OSError, ValueError, subprocess.CalledProcessError):
        return "0" * 40 + "-dirty"
    return f"{sha}-dirty" if status.strip() else sha


def _append(log_path: Path, row: dict[str, str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
    with log_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, restval="", lineterminator="\n")
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _base_row(log_path: Path, family: str, params: dict[str, object]) -> dict[str, str]:
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_sha(log_path),
        "strategy_family": family,
        "params_json": json.dumps(
            params, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ),
    }


def _logged_run(log_path: Path, row: dict[str, str], n_obs: int, sharpe: str) -> str | None:
    """The run id of an identical trial already in the log."""
    if not log_path.exists():
        return None
    with log_path.open(encoding="utf-8", newline="") as handle:
        for logged in csv.DictReader(handle):
            if (
                logged["strategy_family"] == row["strategy_family"]
                and logged["params_json"] == row["params_json"]
                and logged["n_obs"] == str(n_obs)
                and logged["sharpe_annual"] == sharpe
            ):
                return logged["run_id"]
    return None


def log_trial(record: TrialRecord, log_path: Path, runs_dir: Path, once: bool = False) -> str:
    """Write the series, then append one row; return the run id.

    With ``once``, a trial already logged with the same parameters and result adds nothing:
    a rebuild repeats trials, and a repeat of the same numbers is not a new test.
    """
    row = _base_row(log_path, record.strategy_family, record.params)
    series = record.returns.sort("date")
    dates: list[date] = series["date"].to_list()
    returns: list[float] = series["ret"].to_list()
    periods = PERIODS_PER_YEAR[record.freq]
    deviation = statistics.stdev(returns)
    sharpe = statistics.fmean(returns) / deviation * math.sqrt(periods) if deviation > 0 else 0.0
    if once:
        logged = _logged_run(log_path, row, len(returns), f"{sharpe:.10g}")
        if logged is not None:
            return logged
    target = runs_dir / row["run_id"] / "returns.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    columns: dict[str, pa.Array] = {
        "date": pa.array(dates, pa.date32()),
        "ret": pa.array(returns, pa.float64()),
    }
    if "ret_gross" in series.columns:
        columns["ret_gross"] = pa.array(series["ret_gross"].to_list(), pa.float64())
    pq.write_table(pa.table(columns), target)
    row |= {
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "n_obs": str(len(returns)),
        "sharpe_annual": f"{sharpe:.10g}",
        "returns_path": f"runs/{row['run_id']}/returns.parquet",
        "x_periods_per_year": str(periods),
        "x_returns_basis": "excess",
        "x_selected": "false",
    }
    (target.parent / "metrics.json").write_text(
        json.dumps(
            {"n_obs": len(returns), "sharpe_annual": sharpe, "params": record.params},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (target.parent / "config.yaml").write_text(
        yaml.safe_dump(record.params, sort_keys=True), encoding="utf-8", newline="\n"
    )
    (target.parent / "manifest.json").write_text(
        json.dumps(
            {"git_sha": row["git_sha"], "timestamp": row["timestamp"], "run_id": row["run_id"]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _append(log_path, row)
    return row["run_id"]


def summarize_log(log_path: Path) -> pl.DataFrame:
    """Trials per family, with how many failed."""
    frame = pl.read_csv(log_path, schema_overrides={"sharpe_annual": pl.Float64})
    return (
        frame.group_by("strategy_family")
        .agg(pl.len().alias("trials"), pl.col("n_obs").eq(0).sum().alias("failed"))
        .sort("strategy_family")
    )


def export_log(log_path: Path, dest: Path) -> int:
    """Copy the log and every return file, for backtest-overfitting-lab to read."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(log_path, dest / log_path.name)
    copied = 0
    for source in sorted((log_path.parent / "runs").glob("*/returns.parquet")):
        target = dest / "runs" / source.parent.name / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def log_failed(log_path: Path, family: str, params: dict[str, object], note: str) -> str:
    """A failed or aborted run still counts in N."""
    row = _base_row(log_path, family, params) | {
        "n_obs": "0",
        "x_note": note,
        "x_returns_basis": "excess",
        "x_selected": "false",
    }
    _append(log_path, row)
    return row["run_id"]
