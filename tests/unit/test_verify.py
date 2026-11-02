"""``efp data verify``: each hard check fails on the fault it guards against."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from efp.cli import app
from eqpit.pit.verify import DqResult, item_coverage, pit_method_coverage, run_checks, write_reports
from eqpit.store import PitStore

runner = CliRunner()


@pytest.fixture
def copy(small_store: PitStore, tmp_path: Path) -> Path:
    target = tmp_path / "pit"
    shutil.copytree(small_store.root, target)
    return target


def rewrite(root: Path, table: str, change: pl.Expr) -> None:
    path = root / table / "part-0.parquet"
    frame = pl.read_parquet(path).with_columns(change)
    frame.write_parquet(path)


def failed(results: list[DqResult]) -> list[str]:
    return [item.check for item in results if item.status == "fail"]


def test_clean_tables_pass(small_store: PitStore) -> None:
    assert failed(run_checks(small_store.root)) == []


def test_null_available_at_fails(copy: Path) -> None:
    rewrite(copy, "filings", pl.lit(None, dtype=pl.Datetime("ns", "UTC")).alias("available_at"))
    assert "DQ-01" in failed(run_checks(copy))


def test_a_missing_column_fails(copy: Path) -> None:
    path = copy / "delistings" / "part-0.parquet"
    pl.read_parquet(path).drop("available_at").write_parquet(path)
    assert "DQ-01" in failed(run_checks(copy))


def test_availability_before_acceptance_fails(copy: Path) -> None:
    rewrite(
        copy,
        "fundamentals_pit",
        pl.col("available_at").dt.offset_by("-400d").alias("available_at"),
    )
    assert {"DQ-02", "DQ-03"} <= set(failed(run_checks(copy)))


def test_a_wrongly_marked_first_report_fails(copy: Path) -> None:
    rewrite(copy, "fundamentals_pit", pl.lit(True).alias("is_restatement"))
    assert "DQ-05" in failed(run_checks(copy))


def test_a_doubled_company_fails(copy: Path) -> None:
    rewrite(copy, "universe_monthly", pl.lit("CIK:same").alias("company_id"))
    assert "DQ-07" in failed(run_checks(copy))


def test_a_broken_rank_fails(copy: Path) -> None:
    rewrite(copy, "universe_monthly", (pl.col("rank") * 2).alias("rank"))
    assert "DQ-07" in failed(run_checks(copy))


def test_a_delisting_without_a_return_fails(copy: Path) -> None:
    rewrite(copy, "delistings", pl.lit(None, dtype=pl.Float64).alias("dlret"))
    assert "DQ-08" in failed(run_checks(copy))


def test_an_unknown_action_fails(copy: Path) -> None:
    rewrite(copy, "corp_actions", pl.lit("weird").alias("action"))
    assert "DQ-09" in failed(run_checks(copy))


def test_a_wrong_market_cap_fails(copy: Path) -> None:
    path = copy / "prices_daily" / "year=2012" / "part-0.parquet"
    frame = pl.read_parquet(path).with_columns((pl.col("mcap") * 2.0).alias("mcap"))
    frame.write_parquet(path)
    assert "DQ-09" in failed(run_checks(copy))


def test_an_unflagged_extreme_return_fails(copy: Path) -> None:
    path = copy / "prices_daily" / "year=2012" / "part-0.parquet"
    frame = pl.read_parquet(path).with_columns(
        pl.lit(3.0).alias("ret"), pl.lit(0, dtype=pl.UInt8).alias("price_flag")
    )
    frame.write_parquet(path)
    assert "DQ-10" in failed(run_checks(copy))


def test_a_date_off_the_calendar_fails(copy: Path) -> None:
    path = copy / "prices_daily" / "year=2012" / "part-0.parquet"
    frame = pl.read_parquet(path)
    frame.head(1).with_columns(pl.lit(date(2012, 7, 4)).alias("date")).vstack(frame).write_parquet(
        path
    )
    assert "DQ-17" in failed(run_checks(copy))


def test_a_mislabelled_free_path_build_fails(copy: Path) -> None:
    path = copy / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["provider"] = "edgar_free"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert "DQ-16" in failed(run_checks(copy))


def test_reports_are_written(small_store: PitStore, tmp_path: Path) -> None:
    write_reports(small_store.root, tmp_path)
    report = pl.read_csv(tmp_path / "dq_report.csv")
    assert set(report.columns) == {"check", "status", "hard", "detail"}
    coverage = pit_method_coverage(small_store.root)
    assert coverage["share"].sum() == pytest.approx(1.0)
    items = item_coverage(small_store.root)
    assert "total_assets" in items["item"].to_list()


def test_cli_exits_one_on_a_hard_failure(
    copy: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    shutil.copytree(copy, data / "processed" / "pit")
    rewrite(data / "processed" / "pit", "corp_actions", pl.lit("weird").alias("action"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(data))
    monkeypatch.setenv("EFP_LOG_JSON", "0")
    result = runner.invoke(app, ["data", "verify"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
