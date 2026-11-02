"""FR-21 and FR-10: factor construction, the French parser and survivorship tables."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from efp.cli import app
from efp.evaluation.french import (
    Panels,
    bucket_returns,
    buckets,
    compare,
    factor_returns,
    load_panels,
    replicate,
)
from efp.evaluation.survivorship import (
    bias_table,
    delisted_names_by_year,
    equal_weight_universe,
    series_by_mode,
)
from eqpit.benchmarks import (
    align_to_sessions,
    load_french,
    load_risk_free,
    parse_french_monthly,
    pull_french,
)
from eqpit.store import PitStore

FF5 = """This file was created by CMPT_ME_BEME_OP_INV_RETS using the 202609 CRSP database.
The 1-month TBill rate data are provided by Ibbotson and Associates Inc.

,Mkt-RF,SMB,HML,RMW,CMA,RF
196307,  -0.39,  -0.47,  -0.83,   0.66,  -1.15,   0.27
196308,   5.07,  -0.79,   1.67,   0.40,  -0.40,   0.25
202401,   0.70,  -5.00,  -2.38,   1.50,  -1.00,   0.47

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RMW,CMA,RF
1964,  12.00,   1.00,   2.00,   3.00,   4.00,   3.00
"""
MOM = """This file was created by CMPT_ME_PRIOR_RETS using the 202609 CRSP database.

,Mom
192701,  -2.18
196307, -99.99
196308,   1.00
202401,   5.00

Annual Factors: January-December
,Mom
1928,  10.00
"""
JANUARY = date(2024, 1, 31)
FEBRUARY = date(2024, 2, 29)


def test_parser_reads_only_the_monthly_block_as_decimals() -> None:
    frame = parse_french_monthly(FF5)
    assert frame.height == 3
    assert frame.columns == ["mkt_rf", "smb", "hml", "rmw", "cma", "rf", "calendar_month"]
    assert frame["smb"].to_list() == pytest.approx([-0.0047, -0.0079, -0.05])
    assert frame["calendar_month"].to_list()[0] == date(1963, 7, 31)


def test_missing_marker_becomes_null() -> None:
    frame = parse_french_monthly(MOM)
    assert frame["mom"].to_list()[1] is None


def test_months_align_to_the_last_session() -> None:
    frame = pl.DataFrame({"calendar_month": [date(2024, 3, 31)], "x": [1.0]})
    assert align_to_sessions(frame)["month"].to_list() == [date(2024, 3, 28)]


def french_handler(request: httpx.Request) -> httpx.Response:
    text = FF5 if "5_Factors" in request.url.path else MOM
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("file.csv", text)
    return httpx.Response(200, content=buffer.getvalue())


def test_pull_then_load(tmp_path: Path) -> None:
    root = tmp_path / "french"
    manifest = pull_french(root, transport=httpx.MockTransport(french_handler))
    assert manifest.provider == "french"
    assert manifest.survivorship_limited is False
    frame = load_french(root)
    assert "umd" in frame.columns
    assert frame.filter(pl.col("month").eq(JANUARY))["umd"].to_list() == [0.05]
    risk_free = load_risk_free(root)
    assert risk_free is not None
    assert risk_free.collect()["rf"].to_list()[-1] == pytest.approx(0.0047)


def test_no_snapshot_means_no_risk_free(tmp_path: Path) -> None:
    assert load_risk_free(tmp_path) is None


def formation() -> pl.DataFrame:
    rows = []
    for index in range(20):
        rows.append(
            {
                "security_id": f"S{index}",
                "size": float(index + 1),
                "value": float(index),
                "exchange": "NYSE" if index % 2 == 0 else "NASDAQ",
            }
        )
    return pl.DataFrame(rows)


def test_breakpoints_come_from_nyse_names_only() -> None:
    frame = formation().with_columns(
        pl.when(pl.col("exchange").eq("NASDAQ")).then(1e9).otherwise(pl.col("size")).alias("size")
    )
    assigned = buckets(frame, "value")
    nasdaq = assigned.filter(pl.col("exchange").eq("NASDAQ"))
    assert nasdaq["bucket"].str.starts_with("B").all()
    assert assigned.filter(pl.col("exchange").eq("NYSE"))["bucket"].str.starts_with("S").sum() == 5


def test_bucket_edges() -> None:
    assigned = buckets(formation(), "value").sort("value")
    assert assigned["bucket"][0] == "SL"
    assert assigned["bucket"][-1] == "BH"


def test_too_few_nyse_names_gives_no_sort() -> None:
    assert buckets(formation().head(4), "value").is_empty()


def test_missing_characteristics_drop_out() -> None:
    frame = formation().with_columns(
        pl.when(pl.col("value").eq(3.0)).then(None).otherwise(pl.col("value")).alias("value")
    )
    assert "S3" not in buckets(frame, "value")["security_id"].to_list()


def test_returns_are_weighted_by_the_prior_month_cap() -> None:
    panels = Panels(
        pl.DataFrame(),
        pl.DataFrame(
            {
                "month": [JANUARY, JANUARY, FEBRUARY, FEBRUARY],
                "security_id": ["A", "B", "A", "B"],
                "mcap": [100.0, 300.0, 999.0, 1.0],
            }
        ),
        pl.DataFrame(
            {"month": [FEBRUARY, FEBRUARY], "security_id": ["A", "B"], "ret": [0.04, 0.0]}
        ),
    )
    assignments = pl.DataFrame(
        {
            "hold": [FEBRUARY, FEBRUARY],
            "previous": [JANUARY, JANUARY],
            "factor": ["bm", "bm"],
            "bucket": ["SL", "SL"],
            "security_id": ["A", "B"],
        }
    )
    result = bucket_returns(assignments, panels)
    assert result["ret"].to_list() == pytest.approx([0.01])


def test_spreads_follow_the_two_by_three_definitions() -> None:
    portfolios = pl.DataFrame(
        {
            "month": [FEBRUARY] * 6,
            "factor": ["bm"] * 6,
            "bucket": ["SL", "SM", "SH", "BL", "BM", "BH"],
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05, 0.08],
        }
    )
    factors = factor_returns(portfolios)
    by_name = dict(zip(factors["factor"], factors["ret"], strict=True))
    assert by_name["hml"] == pytest.approx((0.03 + 0.08) / 2 - (0.01 + 0.04) / 2)
    assert by_name["smb"] == pytest.approx(0.02 - 0.17 / 3)


def test_replication_runs_on_the_synthetic_market(small_store: PitStore) -> None:
    factors = replicate(small_store)
    assert set(factors["factor"].unique().to_list()) == {
        "smb",
        "hml",
        "rmw",
        "cma",
        "umd",
        "low_risk",
    }
    assert factors["month"].n_unique() > 60
    assert factors["ret"].is_finite().all()


def test_compare_reports_each_factor_against_its_threshold() -> None:
    months = [date(2024, month, 28) for month in range(1, 7)]
    values = [0.01, -0.02, 0.03, 0.0, 0.02, -0.01]
    ours = pl.concat(
        pl.DataFrame({"month": months, "factor": [name] * 6, "ret": values})
        for name in ("smb", "hml", "umd", "rmw", "cma")
    )
    french = pl.DataFrame(
        {
            "month": months,
            "smb": values,
            "hml": [-value for value in values],
            "umd": values,
            "rmw": values,
            "cma": values,
        }
    )
    table = compare(ours, french)
    rows = {row["factor"]: row for row in table.iter_rows(named=True)}
    assert rows["smb"]["passed"] is True
    assert rows["hml"]["passed"] is False
    assert rows["rmw"]["passed"] is None
    assert rows["smb"]["months"] == 6


def test_compare_needs_an_overlap() -> None:
    ours = pl.DataFrame(
        {"month": [JANUARY], "factor": ["smb"], "ret": [0.01]},
    )
    french = pl.DataFrame(
        {
            "month": [FEBRUARY],
            "smb": [0.0],
            "hml": [0.0],
            "umd": [0.0],
            "rmw": [0.0],
            "cma": [0.0],
        }
    )
    assert compare(ours, french)["correlation"].null_count() == 5


def test_survivors_only_removes_every_delisted_name(small_store: PitStore) -> None:
    full = load_panels(small_store)
    survivors = load_panels(small_store, survivors_only=True)
    delisted = small_store.labels().delisted_ids().to_list()
    assert survivors.universe.filter(pl.col("security_id").is_in(delisted)).is_empty()
    assert full.universe.height > survivors.universe.height


def test_the_bias_table_compares_both_modes(small_store: PitStore) -> None:
    series = series_by_mode(small_store)
    assert set(series["mode"].unique().to_list()) == {"full", "survivors_only"}
    table = bias_table(series)
    assert "ew_universe" in table["series"].to_list()
    row = table.filter(pl.col("series").eq("ew_universe")).row(0, named=True)
    assert row["bias_mean"] > 0
    assert row["bias_mean"] == pytest.approx(
        row["mean_annual_survivors_only"] - row["mean_annual_full"]
    )


def test_later_delisted_names_are_counted_per_year(small_store: PitStore) -> None:
    table = delisted_names_by_year(small_store)
    assert (table["later_delisted_months"] > 0).any()
    assert table["universe_months"].sum() > table["later_delisted_months"].sum()


def test_equal_weight_universe_is_one_series_per_month(small_store: PitStore) -> None:
    series = equal_weight_universe(load_panels(small_store))
    assert series["month"].n_unique() == series.height


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, small_store: PitStore) -> Path:
    import shutil

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    shutil.copytree(small_store.root, data / "processed" / "pit")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(data))
    monkeypatch.setenv("EFP_LOG_JSON", "0")
    pull_french(data / "external" / "french", transport=httpx.MockTransport(french_handler))
    return tmp_path


def test_cli_writes_tables_outside_results_for_synthetic(workspace: Path) -> None:
    runner = CliRunner()
    french = runner.invoke(app, ["validate", "french"])
    assert french.exception is None or isinstance(french.exception, SystemExit), french.output
    survivors = runner.invoke(app, ["validate", "survivorship"])
    assert survivors.exit_code == 0, survivors.output
    tables = workspace / "data" / "processed" / "tables" / "synthetic"
    assert (tables / "french_replication.csv").is_file()
    assert (tables / "french_replication.md").is_file()
    assert (tables / "survivorship_bias.csv").is_file()
    assert not (workspace / "results").exists()
