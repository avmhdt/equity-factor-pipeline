"""Replication against Open Source Asset Pricing's published portfolios (FR-26)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import polars as pl

from efp.evaluation.french import Panels
from efp.signals.registry import registered, spec_of

DEFAULT_QUANTILE = 0.2
THRESHOLD = 0.70
MINIMUM_REPLICATED = 20
MIN_NAMES = 4
_PRICE = re.compile(r"abs\(prc\)>(\d+)")


@dataclass(frozen=True)
class Convention:
    """How OSAP builds one published long-short portfolio."""

    target: str
    sign: int
    weighting: str
    quantile: float
    nyse_breakpoints: bool
    period: int
    start_month: int
    min_price: float
    exchanges: tuple[str, ...]


def read_convention(row: dict[str, object]) -> Convention:
    """Read one SignalDoc row. A blank quantile means quintiles, OSAP's default."""
    text = str(row.get("Filter") or "")
    price = _PRICE.search(text)
    exchanges: tuple[str, ...] = ("NYSE", "AMEX", "NASDAQ")
    if "exchcd==1" in text:
        exchanges = ("NYSE",)
    elif "exchcd%in%c(1,2)" in text and "c(1,2,3)" not in text:
        exchanges = ("NYSE", "AMEX")
    quantile = row.get("LS Quantile")
    period = row.get("Portfolio Period")
    start = row.get("Start Month")
    return Convention(
        target=str(row["Acronym"]),
        sign=int(float(str(row["Sign"]))),
        weighting=str(row.get("Stock Weight") or "EW"),
        quantile=float(str(quantile)) if quantile is not None else DEFAULT_QUANTILE,
        nyse_breakpoints=row.get("Quantile Filter") == "NYSE",
        period=int(float(str(period))) if period is not None else 1,
        start_month=int(float(str(start))) if start is not None else 6,
        min_price=float(price.group(1)) if price else 0.0,
        exchanges=exchanges,
    )


def formation_months(months: list[date], convention: Convention) -> list[date]:
    """Month ends on which the portfolio re-forms."""
    if convention.period <= 1:
        return months
    return [
        month for month in months if (month.month - convention.start_month) % convention.period == 0
    ]


def leg_assignments(
    scores: pl.DataFrame, panels: Panels, convention: Convention, months: list[date]
) -> pl.DataFrame:
    """``hold``, ``previous``, ``leg``, ``security_id`` for one signal's portfolios.

    Args:
        scores: ``asof``, ``security_id`` and sign-aligned ``z``.
        panels: monthly inputs.
        convention: the published portfolio's rules.
        months: every month end, sorted.
    """
    position = {month: index for index, month in enumerate(months)}
    filtered = (
        scores.join(
            panels.universe.select("asof", "security_id", "exchange"),
            on=["asof", "security_id"],
            how="inner",
        )
        .join(
            panels.caps.select(pl.col("month").alias("asof"), "security_id", "close"),
            on=["asof", "security_id"],
            how="inner",
        )
        .filter(
            pl.col("close") > convention.min_price,
            pl.col("exchange").is_in(convention.exchanges),
        )
        .drop_nulls("z")
    )
    frames: list[pl.DataFrame] = []
    for formed in formation_months(sorted(filtered["asof"].unique().to_list()), convention):
        at = filtered.filter(pl.col("asof").eq(formed))
        base = at.filter(pl.col("exchange").eq("NYSE")) if convention.nyse_breakpoints else at
        if base.height < MIN_NAMES:
            continue
        low = base["z"].quantile(convention.quantile, interpolation="linear")
        high = base["z"].quantile(1.0 - convention.quantile, interpolation="linear")
        legs = at.filter((pl.col("z") >= high) | (pl.col("z") <= low)).with_columns(
            pl.when(pl.col("z") >= high)
            .then(pl.lit("long"))
            .otherwise(pl.lit("short"))
            .alias("leg")
        )
        index = position[formed]
        for offset in range(convention.period):
            if index + offset + 1 >= len(months):
                break
            frames.append(
                legs.select(
                    "security_id",
                    "leg",
                    pl.lit(months[index + offset + 1], dtype=pl.Date).alias("hold"),
                    pl.lit(months[index + offset], dtype=pl.Date).alias("previous"),
                )
            )
    if not frames:
        return pl.DataFrame(
            schema={
                "security_id": pl.String,
                "leg": pl.String,
                "hold": pl.Date,
                "previous": pl.Date,
            }
        )
    return pl.concat(frames, how="vertical")


def long_short(assignments: pl.DataFrame, panels: Panels, weighting: str) -> pl.DataFrame:
    """Monthly long minus short as ``month``, ``ret``."""
    weights = panels.caps.select(
        pl.col("month").alias("previous"), "security_id", pl.col("mcap").alias("cap")
    )
    held = (
        assignments.join(weights, on=["previous", "security_id"], how="left")
        .join(panels.returns.rename({"month": "hold"}), on=["hold", "security_id"], how="inner")
        .with_columns(
            pl.when(pl.lit(weighting).eq("VW"))
            .then(pl.col("cap").fill_null(0.0))
            .otherwise(pl.lit(1.0))
            .alias("weight")
        )
        .filter(pl.col("weight") > 0)
        .sort("hold", "leg", "security_id")
    )
    legs = held.group_by("hold", "leg", maintain_order=True).agg(
        ((pl.col("ret") * pl.col("weight")).sum() / pl.col("weight").sum()).alias("ret")
    )
    wide = legs.pivot(on="leg", index="hold", values="ret").drop_nulls().sort("hold")
    return wide.select(
        pl.col("hold").alias("month"), (pl.col("long") - pl.col("short")).alias("ret")
    )


def replicate_signal(
    name: str,
    scores: pl.DataFrame,
    panels: Panels,
    doc: pl.DataFrame,
    published: pl.DataFrame,
    months: list[date],
) -> tuple[dict[str, object], pl.DataFrame]:
    """One row of the table, and our series for later analysis."""
    spec = spec_of(name)
    target = spec.osap_target
    assert target is not None
    rows = doc.filter(pl.col("Acronym").eq(target)).to_dicts()
    if not rows:
        return _unmatched(name, target, "target not in SignalDoc"), scores.clear()
    convention = read_convention(rows[0])
    ours = long_short(
        leg_assignments(scores, panels, convention, months), panels, convention.weighting
    )
    aligned = spec.direction * convention.sign
    theirs = published.filter(pl.col("target").eq(target)).select(
        "month", (pl.col("ret") * aligned).alias("theirs")
    )
    joined = ours.join(theirs, on="month", how="inner").sort("month")
    if joined.height < 3:
        return _unmatched(name, target, "no overlap"), ours
    correlation = joined.select(pl.corr("ret", "theirs")).item()
    mean_ours = float(joined["ret"].to_numpy().mean())
    mean_theirs = float(joined["theirs"].to_numpy().mean())
    same_sign = mean_ours * mean_theirs > 0
    passed = correlation >= THRESHOLD and same_sign
    return (
        {
            "signal": name,
            "target": target,
            "weighting": convention.weighting,
            "quantile": convention.quantile,
            "period": convention.period,
            "opposite_sign": aligned < 0,
            "months": joined.height,
            "correlation": correlation,
            "mean_ours": mean_ours,
            "mean_osap": mean_theirs,
            "same_sign": same_sign,
            "passed": passed,
            "reason": "" if passed else _reason(correlation, same_sign),
        },
        ours,
    )


def _reason(correlation: float, same_sign: bool) -> str:
    if correlation < THRESHOLD and not same_sign:
        return "correlation under 0.70 and mean of the opposite sign"
    return "correlation under 0.70" if correlation < THRESHOLD else "mean of the opposite sign"


def _unmatched(name: str, target: str, reason: str) -> dict[str, object]:
    return {
        "signal": name,
        "target": target,
        "weighting": None,
        "quantile": None,
        "period": None,
        "opposite_sign": None,
        "months": 0,
        "correlation": None,
        "mean_ours": None,
        "mean_osap": None,
        "same_sign": None,
        "passed": False,
        "reason": reason,
    }


def replicate_all(
    panel: pl.DataFrame, panels: Panels, doc: pl.DataFrame, published: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Every signal with a target; returns the table and ``signal``, ``month``, ``ret``."""
    months = panels.caps["month"].unique().sort().to_list()
    rows: list[dict[str, object]] = []
    series: list[pl.DataFrame] = []
    for name in registered():
        if spec_of(name).osap_target is None:
            continue
        scores = panel.filter(pl.col("signal").eq(name)).select("asof", "security_id", "z")
        if scores.is_empty():
            continue
        row, ours = replicate_signal(name, scores, panels, doc, published, months)
        rows.append(row)
        if "month" in ours.columns:
            series.append(ours.with_columns(pl.lit(name).alias("signal")))
    table = pl.DataFrame(rows, infer_schema_length=None).sort("signal")
    return table, pl.concat(series, how="vertical").select("signal", "month", "ret")
