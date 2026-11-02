"""``efp evaluate``: every evaluation table, in both neutralization modes, with trials logged."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import polars as pl
import structlog

from efp.evaluation.decay import decay_curve, lagged_ic
from efp.evaluation.fama_macbeth import FMResult, fama_macbeth
from efp.evaluation.ic import ic_summary, newey_west_t, rank_ic
from efp.evaluation.quantiles import quantile_spreads
from efp.evaluation.turnover import decile_weights, rank_autocorrelation, turnover
from efp.explog import TrialRecord
from efp.signals.registry import spec_of
from eqpit.store import PitStore

log = structlog.get_logger(__name__)

SCORES = {"none": "z", "sector_size_beta": "z_neutral"}
DECAY_HORIZONS = (1, 2, 3, 6, 9, 12)
DECAY_LAGS = (0, 1, 2, 3, 6, 9, 12)
Logger = Callable[[TrialRecord], str]


@dataclass(frozen=True)
class EvaluationConfig:
    """Choices from the experiment's ``evaluation`` section."""

    quantiles: int = 10
    weightings: tuple[str, ...] = ("ew", "vw")
    breakpoints: str = "nyse"
    universes: tuple[int, ...] = (3000, 1000)
    neutralize: tuple[str, ...] = ("none", "sector_size_beta")


def _with_universe(panel: pl.DataFrame, store: PitStore) -> pl.DataFrame:
    history = store.labels().universe_history()
    return panel.join(
        history.select("asof", "security_id", "rank", "exchange", "mcap"),
        on=["asof", "security_id"],
        how="inner",
    )


def _ic_rows(framed: pl.DataFrame, fwd: pl.DataFrame, config: EvaluationConfig) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for size in config.universes:
        subset = framed.filter(pl.col("rank") <= size)
        for mode in config.neutralize:
            ic = rank_ic(subset, fwd, 1, SCORES[mode])
            for (name,), part in ic.group_by(["signal"], maintain_order=True):
                summary = ic_summary(part)
                rows.append(
                    {
                        "signal": name,
                        "family": spec_of(str(name)).family,
                        "universe": size,
                        "neutralize": mode,
                        "mean_ic": summary.mean_ic,
                        "std_ic": summary.std_ic,
                        "icir": summary.icir,
                        "icir_annual": summary.icir_annual,
                        "t_nw": summary.t_nw,
                        "hit_rate": summary.hit_rate,
                        "months": summary.months,
                    }
                )
    return pl.DataFrame(rows).sort("signal", "universe", "neutralize")


def _spread_rows(
    framed: pl.DataFrame,
    fwd: pl.DataFrame,
    config: EvaluationConfig,
    provider: str,
    logger: Logger | None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for size in config.universes:
        subset = framed.filter(pl.col("rank") <= size)
        for mode in config.neutralize:
            for weighting in config.weightings:
                table = quantile_spreads(
                    subset,
                    fwd,
                    config.quantiles,
                    weighting,  # type: ignore[arg-type]
                    config.breakpoints,  # type: ignore[arg-type]
                    SCORES[mode],
                )
                spreads = table.filter(pl.col("quantile").eq(0)).sort("signal", "asof")
                for (name,), part in spreads.group_by(["signal"], maintain_order=True):
                    series = part["ret"].to_numpy()
                    rows.append(
                        {
                            "signal": name,
                            "universe": size,
                            "neutralize": mode,
                            "weighting": weighting,
                            "mean_monthly": float(series.mean()),
                            "t_nw": newey_west_t(series),
                            "months": len(series),
                        }
                    )
                    if logger is not None:
                        logger(_trial(str(name), part, size, mode, weighting, config, provider))
    return pl.DataFrame(rows).sort("signal", "universe", "neutralize", "weighting")


def _trial(
    name: str,
    spread: pl.DataFrame,
    size: int,
    mode: str,
    weighting: str,
    config: EvaluationConfig,
    provider: str,
) -> TrialRecord:
    return TrialRecord(
        spec_of(name).family,
        {
            "signal": name,
            "neutralization": mode,
            "construction": f"q{config.quantiles}_top_minus_bottom",
            "weighting": weighting,
            "breakpoints": config.breakpoints,
            "execution": "close",
            "costs": "none",
            "universe": size,
            "provider": provider,
            "freq": "M",
            "net": False,
        },
        spread.select(pl.col("asof").alias("date"), "ret"),
        "M",
    )


def _fama_macbeth_rows(
    framed: pl.DataFrame, fwd: pl.DataFrame, config: EvaluationConfig
) -> pl.DataFrame:
    labels = fwd.filter(pl.col("h").eq(1)).select("asof", "security_id", "fwd_ret")
    rows: list[dict[str, object]] = []
    for mode in config.neutralize:
        score = SCORES[mode]
        wide = (
            framed.select("asof", "security_id", "signal", score)
            .pivot(on="signal", index=["asof", "security_id"], values=score)
            .join(labels, on=["asof", "security_id"], how="inner")
            .sort("asof", "security_id")
        )
        names = [
            name
            for name in wide.columns
            if name not in ("asof", "security_id", "fwd_ret")
            and wide[name].null_count() < wide.height
        ]
        families: dict[str, list[str]] = {}
        for name in names:
            families.setdefault(spec_of(name).family, []).append(name)
        for name in names:
            result = fama_macbeth(wide, "fwd_ret", [name])
            rows.append(_fm_row("univariate", name, name, mode, result))
        for family, members in families.items():
            if len(members) < 2:
                continue
            result = fama_macbeth(wide, "fwd_ret", members)
            for member in members:
                rows.append(_fm_row(f"multivariate_{family}", member, member, mode, result))
    return pl.DataFrame(rows).sort("signal", "model", "neutralize")


def _fm_row(model: str, signal: str, term: str, mode: str, result: FMResult) -> dict[str, object]:
    index = result.names.index(term)
    return {
        "signal": signal,
        "model": model,
        "neutralize": mode,
        "coefficient": result.coefficients[index],
        "t_nw": result.t_stats[index],
        "months": result.periods,
        "lags": result.lags,
    }


def _turnover_rows(
    framed: pl.DataFrame, fwd: pl.DataFrame, config: EvaluationConfig
) -> pl.DataFrame:
    labels = fwd.filter(pl.col("h").eq(1)).select("asof", "security_id", "fwd_ret")
    frames: list[pl.DataFrame] = []
    for mode in config.neutralize:
        score = SCORES[mode]
        correlation = (
            rank_autocorrelation(framed, score)
            .group_by("signal", maintain_order=True)
            .agg(pl.col("autocorrelation").mean())
        )
        moved = (
            turnover(decile_weights(framed, score, config.quantiles), labels)
            .group_by("signal", maintain_order=True)
            .agg(pl.col("turnover").mean().alias("monthly_turnover"))
        )
        frames.append(
            correlation.join(moved, on="signal", how="left").with_columns(
                pl.lit(mode).alias("neutralize")
            )
        )
    return pl.concat(frames, how="vertical").sort("signal", "neutralize")


def _decay_rows(framed: pl.DataFrame, fwd: pl.DataFrame, config: EvaluationConfig) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    for mode in config.neutralize:
        score = SCORES[mode]
        horizons = decay_curve(framed, fwd, DECAY_HORIZONS, score).select(
            "signal",
            pl.lit("horizon").alias("curve"),
            pl.col("horizon").alias("step"),
            "mean_ic",
            "months",
        )
        lags = lagged_ic(framed, fwd, DECAY_LAGS, score).select(
            "signal", pl.lit("lag").alias("curve"), pl.col("lag").alias("step"), "mean_ic", "months"
        )
        frames.append(
            pl.concat([horizons, lags], how="vertical").with_columns(
                pl.lit(mode).alias("neutralize")
            )
        )
    return pl.concat(frames, how="vertical").sort("signal", "neutralize", "curve", "step")


def evaluate_panel(
    panel: pl.DataFrame,
    store: PitStore,
    config: EvaluationConfig,
    provider: str,
    logger: Logger | None = None,
) -> dict[str, pl.DataFrame]:
    """Every evaluation table for a panel.

    Returns tables named ``ic_summary``, ``quantile_spreads``, ``fama_macbeth``, ``turnover`` and
    ``decay``. Each long-short series goes to ``logger`` as one trial.
    """
    fwd = store.labels().forward_returns(DECAY_HORIZONS)
    framed = _with_universe(panel, store)
    log.info("evaluation_start", rows=framed.height)
    tables = {
        "ic_summary": _ic_rows(framed, fwd, config),
        "quantile_spreads": _spread_rows(framed, fwd, config, provider, logger),
    }
    log.info("evaluation_spreads_done")
    top = framed.filter(pl.col("rank") <= max(config.universes))
    tables["turnover"] = _turnover_rows(top, fwd, config)
    tables["decay"] = _decay_rows(top, fwd, config)
    log.info("evaluation_decay_done")
    tables["fama_macbeth"] = _fama_macbeth_rows(top, fwd, config)
    return tables


def merge_rows(existing: pl.DataFrame, fresh: pl.DataFrame, signals: Sequence[str]) -> pl.DataFrame:
    """Replace the given signals' rows in a table, keeping every other signal's."""
    kept = existing.filter(~pl.col("signal").is_in(list(signals)))
    return pl.concat([kept, fresh], how="diagonal_relaxed").sort("signal")


def signals_argument(text: str | None) -> Sequence[str] | None:
    """Split a ``--signals`` value."""
    return [item.strip() for item in text.split(",")] if text else None
