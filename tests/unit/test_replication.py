"""OSAP replication, post-publication decay, multiple testing and the value gap."""

from __future__ import annotations

import csv
import io
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import polars as pl
import pytest
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests
from typer.testing import CliRunner

from efp.cli import app
from efp.evaluation.french import Panels, load_panels
from efp.evaluation.multiple_testing import (
    bhy_adjust,
    hurdle_table,
    trial_statistics,
    two_sided_p,
)
from efp.evaluation.osap import (
    INVERTED_TARGETS,
    Convention,
    formation_months,
    leg_assignments,
    long_short,
    read_convention,
    replicate_all,
    replicate_signal,
)
from efp.evaluation.postpub import per_signal, pooled_regression, summary_rows, tag_periods
from efp.evaluation.value_gap import decompose
from efp.explog import TrialRecord, log_failed, log_trial
from efp.panel import PanelConfig, build_panel
from efp.reports.figures import post_publication_bars
from eqpit.benchmarks import load_osap_returns, load_signal_doc, pull_osap
from eqpit.store import PitStore

JANUARY = date(2020, 1, 31)
FEBRUARY = date(2020, 2, 28)
MARCH = date(2020, 3, 31)
DOC_COLUMNS = (
    "Acronym",
    "Sign",
    "Stock Weight",
    "LS Quantile",
    "Quantile Filter",
    "Portfolio Period",
    "Start Month",
    "Filter",
    "SampleStartYear",
    "SampleEndYear",
    "Year",
)


def signal_doc() -> pl.DataFrame:
    rows = [
        ("Size", -1.0, "EW", 0.5, None, 12.0, 6.0, None, 1926, 1975, 1981),
        ("Mom6m", 1.0, "EW", 0.1, None, 3.0, 6.0, None, 1964, 2014, 2016),
        ("GP", 1.0, "VW", None, "NYSE", 12.0, 6.0, "abs(prc)>5", 1963, 2015, 2017),
        ("Beta", 1.0, "EW", None, None, 1.0, 6.0, "exchcd==1", 1929, 1968, 1973),
    ]
    return pl.DataFrame(rows, schema=list(DOC_COLUMNS), orient="row", infer_schema_length=None)


def test_conventions_read_each_field() -> None:
    rows = {row["Acronym"]: row for row in signal_doc().to_dicts()}
    size = read_convention(rows["Size"])
    assert (size.sign, size.quantile, size.period) == (-1, 0.5, 12)
    gross = read_convention(rows["GP"])
    assert gross.weighting == "VW"
    assert gross.nyse_breakpoints
    assert gross.min_price == 5.0
    assert gross.quantile == 0.2
    assert read_convention(rows["Beta"]).exchanges == ("NYSE",)
    pair = read_convention({**rows["GP"], "Filter": "exchcd%in%c(1,2)"})
    assert pair.exchanges == ("NYSE", "AMEX")


def convention(period: int = 1, weighting: str = "EW", min_price: float = 0.0) -> Convention:
    return Convention("X", 1, weighting, 0.25, False, period, 6, min_price, ("NYSE", "NASDAQ"))


def test_formation_months_follow_the_period() -> None:
    months = [date(2020, month, 28) for month in range(1, 13)]
    assert formation_months(months, convention(1)) == months
    assert formation_months(months, convention(12)) == [date(2020, 6, 28)]
    assert formation_months(months, convention(6)) == [date(2020, 6, 28), date(2020, 12, 28)]


def small_panels() -> Panels:
    names = ["A", "B", "C", "D"]
    universe = pl.DataFrame(
        {
            "asof": [JANUARY] * 4,
            "security_id": names,
            "exchange": ["NYSE"] * 4,
            "company_id": names,
            "company_mcap": [1.0, 2.0, 3.0, 4.0],
            "ff49": [45, 1, 1, 1],
        }
    )
    caps = pl.DataFrame(
        {
            "month": [JANUARY] * 4 + [FEBRUARY] * 4,
            "security_id": names * 2,
            "mcap": [100.0, 100.0, 300.0, 100.0] * 2,
            "close": [10.0, 10.0, 3.0, 10.0] * 2,
        }
    )
    returns = pl.DataFrame(
        {
            "month": [FEBRUARY] * 4 + [MARCH] * 4,
            "security_id": names * 2,
            "ret": [0.01, 0.02, 0.03, 0.05] * 2,
        }
    )
    return Panels(universe, caps, returns)


def scores() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "asof": [JANUARY] * 4,
            "security_id": ["A", "B", "C", "D"],
            "raw": [-2.0, -1.0, 1.0, 2.0],
            "z": [-2.0, -1.0, 1.0, 2.0],
        }
    )


def test_long_minus_short_with_equal_weights() -> None:
    months = [JANUARY, FEBRUARY, MARCH]
    legs = leg_assignments(scores(), small_panels(), convention(), months)
    assert set(legs["leg"].to_list()) == {"long", "short"}
    series = long_short(legs, small_panels(), "EW")
    assert series["month"].to_list() == [FEBRUARY]
    assert series["ret"][0] == pytest.approx(0.05 - 0.01)


def test_a_long_holding_period_keeps_the_legs() -> None:
    months = [JANUARY, FEBRUARY, MARCH]
    rules = convention(period=2)
    moved = scores().with_columns(pl.lit(date(2020, 6, 30)).alias("asof"))
    panels = small_panels()
    shifted = Panels(
        panels.universe.with_columns(pl.lit(date(2020, 6, 30)).alias("asof")),
        panels.caps,
        panels.returns,
    )
    assert leg_assignments(moved, shifted, rules, months).is_empty()
    legs = leg_assignments(
        scores(), panels, Convention("X", 1, "EW", 0.25, False, 1, 6, 0.0, ("NYSE",)), months
    )
    assert legs["hold"].unique().to_list() == [FEBRUARY]


def test_the_price_filter_removes_cheap_names() -> None:
    months = [JANUARY, FEBRUARY, MARCH]
    legs = leg_assignments(scores(), small_panels(), convention(min_price=5.0), months)
    assert "C" not in legs["security_id"].to_list()


def test_ep_keeps_positive_values_only() -> None:
    rules = replace(convention(), target="EP")
    legs = leg_assignments(scores(), small_panels(), rules, [JANUARY, FEBRUARY, MARCH])
    assert set(legs["security_id"].to_list()) <= {"C", "D"}


def test_gp_leaves_out_financials() -> None:
    rules = replace(convention(), target="GP")
    legs = leg_assignments(scores(), small_panels(), rules, [JANUARY, FEBRUARY, MARCH])
    assert "A" not in legs["security_id"].to_list()


def test_the_inverted_target_is_not_flipped(
    small_store: PitStore, replication_panel: pl.DataFrame
) -> None:
    panels = load_panels(small_store)
    doc = pl.DataFrame(
        [("EntMult", -1.0, "EW", 0.2, None, 12.0, 6.0, None, 1963, 2009, 2011)],
        schema=list(DOC_COLUMNS),
        orient="row",
    )
    months = panels.caps["month"].unique().sort().to_list()
    scored = replication_panel.filter(pl.col("signal").eq("mom_6_1")).select(
        "asof", "security_id", "raw", "z"
    )
    published = pl.DataFrame(
        {"month": months, "target": ["EntMult"] * len(months), "ret": [0.01] * len(months)},
        schema={"month": pl.Date, "target": pl.String, "ret": pl.Float64},
    )
    assert {"EntMult"} == INVERTED_TARGETS
    row, _ = replicate_signal("ebitda_ev", scored, panels, doc, published, months)
    assert row["opposite_sign"] is False
    assert row["mean_osap"] == pytest.approx(0.01)


def test_value_weights_use_the_prior_month_cap() -> None:
    assignments = pl.DataFrame(
        {
            "security_id": ["A", "C", "D"],
            "leg": ["short", "long", "long"],
            "hold": [FEBRUARY] * 3,
            "previous": [JANUARY] * 3,
        }
    )
    series = long_short(assignments, small_panels(), "VW")
    expected = (0.03 * 300.0 + 0.05 * 100.0) / 400.0 - 0.01
    assert series["ret"][0] == pytest.approx(expected)


def published_frame(panel_series: pl.DataFrame, target: str, flip: float = 1.0) -> pl.DataFrame:
    return panel_series.select(
        "month", pl.lit(target).alias("target"), (pl.col("ret") * flip).alias("ret")
    )


@pytest.fixture(scope="module")
def replication_panel(small_store: PitStore) -> pl.DataFrame:
    dates = small_store.month_ends(date(2012, 1, 1), date(2017, 12, 29))
    panel, _ = build_panel(
        small_store, ["size", "mom_6_1", "gp_at", "beta"], dates, PanelConfig(min_names=30)
    )
    return panel


def test_a_series_compared_with_itself_replicates(
    small_store: PitStore, replication_panel: pl.DataFrame
) -> None:
    panels = load_panels(small_store)
    doc = signal_doc()
    empty = pl.DataFrame(schema={"month": pl.Date, "target": pl.String, "ret": pl.Float64})
    first, series = replicate_all(replication_panel, panels, doc, empty)
    assert first["months"].sum() == 0
    assert "no overlap" in first["reason"].to_list()
    assert set(series["signal"].to_list()) == {"size", "mom_6_1", "gp_at", "beta"}
    target = {"size": "Size", "mom_6_1": "Mom6m", "gp_at": "GP", "beta": "Beta"}
    published = pl.concat(
        published_frame(
            series.filter(pl.col("signal").eq(name)),
            acronym,
            flip=-1.0 if name == "beta" else 1.0,
        )
        for name, acronym in target.items()
    )
    table, _ = replicate_all(replication_panel, panels, doc, published)
    rows = {row["signal"]: row for row in table.iter_rows(named=True)}
    assert rows["size"]["correlation"] == pytest.approx(1.0)
    assert rows["size"]["passed"] is True
    assert rows["beta"]["opposite_sign"] is True
    assert rows["beta"]["correlation"] == pytest.approx(1.0)
    assert rows["size"]["opposite_sign"] is False


def test_a_target_missing_from_the_doc_is_listed(
    small_store: PitStore, replication_panel: pl.DataFrame
) -> None:
    table, _ = replicate_all(
        replication_panel,
        load_panels(small_store),
        signal_doc().filter(pl.col("Acronym").ne("GP")),
        pl.DataFrame(schema={"month": pl.Date, "target": pl.String, "ret": pl.Float64}),
    )
    row = table.filter(pl.col("signal").eq("gp_at")).row(0, named=True)
    assert row["reason"] == "target not in SignalDoc"
    assert row["passed"] is False


def decay_series(signal: str, target: str, base: float) -> pl.DataFrame:
    months = [date(year, 1 + month, 28) for year in range(2000, 2012) for month in range(12)]
    returns = [
        base if month.year <= 2003 else base * 0.7 if month.year <= 2006 else base * 0.4
        for month in months
    ]
    return pl.DataFrame(
        {
            "signal": [signal] * len(months),
            "target": [target] * len(months),
            "month": months,
            "ret": returns,
        }
    )


def decay_doc() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Acronym": ["X", "Y"],
            "SampleStartYear": ["2000", "2000"],
            "SampleEndYear": ["2003", "2003"],
            "Year": ["2006", "2006"],
        }
    )


def test_periods_come_from_the_doc_years() -> None:
    tagged = tag_periods(decay_series("x", "X", 0.01), decay_doc())
    counts = dict(tagged.group_by("period").len().iter_rows())
    assert counts == {"in_sample": 48, "post_sample": 36, "post_publication": 60}


def test_pooled_regression_recovers_the_decline() -> None:
    both = pl.concat([decay_series("x", "X", 0.01), decay_series("y", "Y", 0.02)])
    result = pooled_regression(tag_periods(both, decay_doc()))
    assert result["signals"] == 2.0
    assert result["post_sample"] == pytest.approx(-0.3)
    assert result["post_publication"] == pytest.approx(-0.6)


def test_clustered_errors_match_statsmodels_by_month() -> None:
    rng = np.random.default_rng(5)
    frames = []
    for signal, target in (("x", "X"), ("y", "Y")):
        frame = decay_series(signal, target, 0.01)
        frames.append(
            frame.with_columns(
                pl.Series("ret", frame["ret"].to_numpy() + rng.normal(0, 0.01, frame.height))
            )
        )
    tagged = tag_periods(pl.concat(frames), decay_doc())
    result = pooled_regression(tagged)
    scale = (
        tagged.filter(pl.col("period").eq("in_sample"))
        .group_by("signal")
        .agg(pl.col("ret").mean().alias("scale"))
    )
    scaled = tagged.join(scale, on="signal").with_columns(
        (pl.col("ret") / pl.col("scale")).alias("y")
    )
    months = scaled["month"].to_physical().to_numpy()
    design = np.column_stack(
        [
            (scaled["signal"] == "x").to_numpy().astype(float),
            (scaled["signal"] == "y").to_numpy().astype(float),
            (scaled["period"] == "post_sample").to_numpy().astype(float),
            (scaled["period"] == "post_publication").to_numpy().astype(float),
        ]
    )
    fitted = sm.OLS(scaled["y"].to_numpy(), design).fit(
        cov_type="cluster", cov_kwds={"groups": months}
    )
    assert result["post_publication"] == pytest.approx(float(fitted.params[3]))
    assert result["post_publication_t"] == pytest.approx(float(fitted.tvalues[3]))


def test_a_negative_or_short_in_sample_mean_is_left_out() -> None:
    frame = decay_series("x", "X", -0.01)
    assert pooled_regression(tag_periods(frame, decay_doc()))["signals"] == 0.0


def test_per_signal_decline_and_the_paper_row() -> None:
    table = per_signal(tag_periods(decay_series("x", "X", 0.01), decay_doc()))
    assert table["decline"][0] == pytest.approx(-0.6)
    summary = summary_rows({"ours": {"signals": 1.0, "months": 10.0}})
    assert summary["sample"].to_list() == ["McLean and Pontiff (2016)", "ours"]


def test_a_signal_with_no_in_sample_months_still_has_a_row() -> None:
    frame = decay_series("x", "X", 0.01).filter(pl.col("month").dt.year() > 2003)
    table = per_signal(tag_periods(frame, decay_doc()))
    assert table["mean_in_sample"].null_count() == 1
    assert table["decline"].null_count() == 1


def test_bhy_equals_statsmodels() -> None:
    p_values = np.random.default_rng(2).uniform(0.0, 0.2, 40)
    expected = multipletests(p_values, method="fdr_by")[1]
    assert bhy_adjust(p_values) == pytest.approx(expected)
    assert len(bhy_adjust(np.array([]))) == 0


def test_two_sided_p() -> None:
    assert two_sided_p(1.959964) == pytest.approx(0.05, abs=1e-5)
    assert two_sided_p(float("nan")) == 1.0


def test_trial_statistics_keep_failures_in_the_count(tmp_path: Path) -> None:
    log_path = tmp_path / "trial_log.csv"
    months = [date(2010 + month // 12, 1 + month % 12, 28) for month in range(60)]
    rng = np.random.default_rng(3)
    good = pl.DataFrame({"date": months, "ret": 0.02 + rng.normal(0, 0.01, 60)})
    noise = pl.DataFrame({"date": months, "ret": rng.normal(0, 0.01, 60)})
    for series in (good, noise):
        log_trial(TrialRecord("value", {"a": 1}, series, "M"), log_path, tmp_path / "runs")
    log_failed(log_path, "value", {"a": 2}, "no data")
    table = hurdle_table(trial_statistics(log_path))
    assert table.height == 3
    assert table["t_over_3"].to_list() == [True, False, False]
    assert table["bhy_5pct"][0] is True
    assert table["p"][2] == 1.0


def test_the_figure_is_written(tmp_path: Path) -> None:
    table = pl.DataFrame(
        {
            "signal": ["a", "b"],
            "mean_in_sample": [0.01, 0.02],
            "mean_post_publication": [0.002, None],
        }
    )
    post_publication_bars(table, tmp_path / "bars.png")
    post_publication_bars(table, tmp_path / "dev.png", dev_only=True)
    assert (tmp_path / "bars.png").stat().st_size > 1000


def test_value_gap_steps_run_on_the_synthetic_market(
    small_store: PitStore, replication_panel: pl.DataFrame
) -> None:
    months = small_store.month_ends(date(2011, 1, 1), date(2017, 12, 29))
    french = pl.DataFrame(
        {"month": months, "hml": [0.01 * ((index % 5) - 2) for index in range(len(months))]},
        schema={"month": pl.Date, "hml": pl.Float64},
    )
    dates = months[12:]
    panel, _ = build_panel(small_store, ["bm", "beta", "size"], dates, PanelConfig(min_names=30))
    table = decompose(
        small_store,
        load_panels(small_store),
        panel.filter(pl.col("signal").eq("bm")),
        french,
        small_store.labels().forward_returns((1,)),
    )
    assert table["step"].to_list() == [
        "hml_ff_convention",
        "universe_top_3000",
        "breakpoints_all_names",
        "rebalance_monthly",
        "pit_latest_quarter",
        "decile_spread",
        "neutralized",
    ]
    assert table["correlation_with_previous"].null_count() == 1
    assert (table["months"] > 10).all()


def osap_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith(".parquet"):
        wide = pl.DataFrame(
            {"date": [date(2020, 1, 31), date(2020, 2, 29)], "Size": [1.5, None], "GP": [0.5, 2.0]}
        )
        buffer = io.BytesIO()
        wide.write_parquet(buffer)
        return httpx.Response(200, content=buffer.getvalue())
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(DOC_COLUMNS)
    writer.writerow(["Size", "-1", "EW", "0.5", "", "12", "6", "", "1926", "1975", "1981"])
    return httpx.Response(200, text=text.getvalue())


def test_osap_pull_and_load(tmp_path: Path) -> None:
    root = tmp_path / "osap"
    manifest = pull_osap(root, transport=httpx.MockTransport(osap_handler))
    assert manifest.provider == "osap"
    assert set(manifest.files) == {"PredictorLSretWide.parquet", "SignalDoc.csv"}
    returns = load_osap_returns(root)
    assert returns.filter(pl.col("target").eq("GP"))["ret"].to_list() == pytest.approx(
        [0.005, 0.02]
    )
    assert returns.filter(pl.col("target").eq("Size")).height == 1
    assert returns["month"].max() == date(2020, 2, 28)
    assert load_signal_doc(root)["Acronym"].to_list() == ["Size"]


def test_cli_targets_that_need_no_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, small_store: PitStore
) -> None:
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    shutil.copytree(small_store.root, data / "processed" / "pit")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(data))
    monkeypatch.setenv("EFP_LOG_JSON", "0")
    runner = CliRunner()
    assert runner.invoke(app, ["validate", "jkp"]).exit_code == 2
    assert runner.invoke(app, ["data", "pull-benchmarks", "--sources", "nope"]).exit_code == 2
    results = tmp_path / "results"
    months = [date(2010 + month // 12, 1 + month % 12, 28) for month in range(60)]
    series = pl.DataFrame({"date": months, "ret": [0.01 * (month % 3 - 1) for month in range(60)]})
    log_trial(
        TrialRecord("value", {"a": 1}, series, "M"), results / "trial_log.csv", results / "runs"
    )
    tested = runner.invoke(app, ["validate", "multiple-testing"])
    assert tested.exit_code == 0, tested.output
    assert "1 trials" in tested.output
    assert (data / "processed" / "tables" / "synthetic" / "multiple_testing.csv").is_file()
