"""The ``combine`` entry point: every composite is built from the past only (DQ-13)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import polars as pl
import structlog

from efp.combine.equal import equal_weight
from efp.combine.ic_weighted import apply_weights, trailing_weights
from efp.combine.ridge import RidgeFit, fit_at, score_with
from efp.evaluation.ic import rank_ic
from efp.evaluation.run import SCORES
from efp.signals.registry import spec_of

log = structlog.get_logger(__name__)

Method = Literal["equal", "ic_weighted", "ridge"]


@dataclass(frozen=True)
class CombineConfig:
    """Choices from the experiment's ``combination`` section."""

    ic_window_months: int = 36
    alphas: tuple[float, ...] = (0.1, 1.0, 10.0, 100.0, 1000.0)
    min_train_months: int = 120
    purge_months: int = 1
    refit_months: int = 12
    score: str = "z"
    diagnostics: dict[str, object] = field(default_factory=dict, hash=False, compare=False)


def _wide(panel: pl.DataFrame, score: str) -> tuple[pl.DataFrame, list[str]]:
    names = sorted(panel["signal"].unique().to_list())
    wide = (
        panel.select("asof", "security_id", "signal", score)
        .pivot(on="signal", index=["asof", "security_id"], values=score)
        .sort("asof", "security_id")
    )
    return wide, names


def _ridge_scores(
    wide: pl.DataFrame, names: list[str], fwd: pl.DataFrame, config: CombineConfig
) -> pl.DataFrame:
    labels = fwd.filter(pl.col("h").eq(1)).select("asof", "security_id", "fwd_ret")
    training = (
        wide.join(labels, on=["asof", "security_id"], how="inner")
        .drop_nulls("fwd_ret")
        .with_columns((pl.col("fwd_ret") - pl.col("fwd_ret").mean().over("asof")).alias("target"))
    )
    months = wide["asof"].unique().sort().to_list()
    frames: list[pl.DataFrame] = []
    fit: RidgeFit | None = None
    fits: list[RidgeFit] = []
    for index, month in enumerate(months):
        due = fit is None or index % config.refit_months == 0
        if due:
            fresh = fit_at(
                training,
                names,
                months,
                index,
                config.alphas,
                config.purge_months,
                config.min_train_months,
            )
            if fresh is not None:
                fit = fresh
                fits.append(fresh)
        if fit is not None:
            frames.append(score_with(wide.filter(pl.col("asof").eq(month)), names, fit))
    config.diagnostics["fits"] = fits
    if not frames:
        return pl.DataFrame(schema={"asof": pl.Date, "security_id": pl.String, "score": pl.Float64})
    return pl.concat(frames, how="vertical")


def combine(
    signals: pl.DataFrame,
    fwd: pl.DataFrame,
    method: Method,
    cfg: CombineConfig,
) -> pl.DataFrame:
    """Composite scores ``asof``, ``security_id``, ``score`` by the chosen method.

    Equal weight uses no returns at all. The other two see a return only once its period has
    ended, at least ``purge_months`` before the date being scored.
    """
    panel = signals.drop_nulls(cfg.score)
    wide, names = _wide(panel, cfg.score)
    if method == "equal":
        result = equal_weight(wide, names)
    elif method == "ic_weighted":
        months = wide["asof"].unique().sort().to_list()
        weights = trailing_weights(
            rank_ic(panel, fwd, 1, cfg.score),
            names,
            months,
            cfg.ic_window_months,
            cfg.purge_months,
        )
        cfg.diagnostics["weights"] = weights
        result = apply_weights(panel, weights, cfg.score)
    else:
        result = _ridge_scores(wide, names, fwd, cfg)
    log.info("composite_built", method=method, rows=result.height)
    return result.drop_nulls("score").sort("asof", "security_id")


def composite_name(method: str, mode: str) -> str:
    """File and table name of one composite."""
    return f"{method}_{mode}"


def family_name(family: str, mode: str) -> str:
    """File and table name of one family's equal-weight composite."""
    return f"family_{family}_{mode}"


def build_composites(
    panel: pl.DataFrame,
    fwd: pl.DataFrame,
    methods: Sequence[Method],
    modes: Sequence[str],
    family_mode: str,
    config: CombineConfig,
) -> dict[str, pl.DataFrame]:
    """Every method in every neutralization mode, plus each family in ``family_mode``."""
    built: dict[str, pl.DataFrame] = {}
    for mode in modes:
        chosen = replace(config, score=SCORES[mode], diagnostics={})
        for method in methods:
            built[composite_name(method, mode)] = combine(panel, fwd, method, chosen)
    members: dict[str, list[str]] = {}
    for name in panel["signal"].unique().sort().to_list():
        members.setdefault(spec_of(name).family, []).append(name)
    chosen = replace(config, score=SCORES[family_mode], diagnostics={})
    for family, names in sorted(members.items()):
        subset = panel.filter(pl.col("signal").is_in(names))
        built[family_name(family, family_mode)] = combine(subset, fwd, "equal", chosen)
    return {name: scores for name, scores in built.items() if not scores.is_empty()}
