"""Post-publication decay in the style of McLean and Pontiff (2016) (FR-27).

Returns are scaled by each anomaly's in-sample mean, so a coefficient reads as a share of the
in-sample return. McLean and Pontiff report declines of 26% out of sample and 58% after
publication.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import polars as pl
import statsmodels.api as sm

PAPER_POST_SAMPLE = -0.26
PAPER_POST_PUBLICATION = -0.58
MIN_IN_SAMPLE_MONTHS = 36


def tag_periods(series: pl.DataFrame, doc: pl.DataFrame) -> pl.DataFrame:
    """Add ``period``: in-sample, post-sample or post-publication, from SignalDoc years.

    Args:
        series: ``signal``, ``target``, ``month``, ``ret``.
        doc: SignalDoc with ``Acronym``, ``SampleStartYear``, ``SampleEndYear`` and ``Year``.
    """
    dates = doc.select(
        pl.col("Acronym").alias("target"),
        pl.col("SampleStartYear").cast(pl.Int32, strict=False).alias("sample_start"),
        pl.col("SampleEndYear").cast(pl.Int32, strict=False).alias("sample_end"),
        pl.col("Year").cast(pl.Int32, strict=False).alias("published"),
    )
    year = pl.col("month").dt.year()
    return (
        series.join(dates, on="target", how="inner")
        .filter(year >= pl.col("sample_start"))
        .with_columns(
            pl.when(year <= pl.col("sample_end"))
            .then(pl.lit("in_sample"))
            .when(year <= pl.col("published"))
            .then(pl.lit("post_sample"))
            .otherwise(pl.lit("post_publication"))
            .alias("period")
        )
    )


def per_signal(tagged: pl.DataFrame) -> pl.DataFrame:
    """Mean return per period per signal, with decline from the in-sample mean."""
    means = (
        tagged.sort("signal", "month")
        .group_by("signal", "period", maintain_order=True)
        .agg(pl.col("ret").mean().alias("mean"), pl.len().alias("months"))
    )
    wide = means.pivot(on="period", index="signal", values=["mean", "months"])  # noqa: PD010
    for name in ("in_sample", "post_sample", "post_publication"):
        for prefix in ("mean", "months"):
            column = f"{prefix}_{name}"
            if column not in wide.columns:
                wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
    return wide.with_columns(
        (pl.col("mean_post_publication") / pl.col("mean_in_sample") - 1.0).alias("decline")
    ).sort("signal")


def pooled_regression(tagged: pl.DataFrame) -> dict[str, float]:
    """Regress scaled returns on period dummies with signal effects, clustering on month.

    Only signals with enough in-sample months and a positive in-sample mean enter: a share of
    a mean near zero or below it has no meaning.
    """
    means = (
        tagged.filter(pl.col("period").eq("in_sample"))
        .group_by("signal")
        .agg(pl.col("ret").mean().alias("scale"), pl.len().alias("count"))
        .filter(pl.col("count") >= MIN_IN_SAMPLE_MONTHS, pl.col("scale") > 0)
    )
    usable = tagged.join(means, on="signal", how="inner").with_columns(
        (pl.col("ret") / pl.col("scale")).alias("scaled")
    )
    signals = usable["signal"].n_unique()
    if signals == 0:
        return {
            "signals": 0.0,
            "months": 0.0,
            "post_sample": float("nan"),
            "post_sample_t": float("nan"),
            "post_publication": float("nan"),
            "post_publication_t": float("nan"),
        }
    frame = usable.select("signal", "month", "period", "scaled").to_pandas()
    design = pd.get_dummies(frame["signal"], dtype=float)
    design["post_sample"] = (frame["period"] == "post_sample").astype(float)
    design["post_publication"] = (frame["period"] == "post_publication").astype(float)
    groups = pd.factorize(frame["month"])[0]
    fitted = sm.OLS(frame["scaled"].to_numpy(), design).fit(
        cov_type="cluster", cov_kwds={"groups": np.asarray(groups)}
    )
    return {
        "signals": float(signals),
        "months": float(frame["month"].nunique()),
        "post_sample": float(fitted.params["post_sample"]),
        "post_sample_t": float(fitted.tvalues["post_sample"]),
        "post_publication": float(fitted.params["post_publication"]),
        "post_publication_t": float(fitted.tvalues["post_publication"]),
    }


def summary_rows(results: dict[str, dict[str, float]]) -> pl.DataFrame:
    """Pooled results beside the paper's reported declines."""
    rows: list[dict[str, object]] = [
        {
            "sample": "McLean and Pontiff (2016)",
            "signals": 97.0,
            "months": None,
            "post_sample": PAPER_POST_SAMPLE,
            "post_sample_t": None,
            "post_publication": PAPER_POST_PUBLICATION,
            "post_publication_t": None,
        }
    ]
    rows.extend({"sample": name, **result} for name, result in results.items())
    return pl.DataFrame(rows, infer_schema_length=None)
