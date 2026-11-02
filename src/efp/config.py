"""Validated experiment configuration (YAML)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from efp.evaluation.run import EvaluationConfig
from efp.panel import PanelConfig
from eqpit.pit.policy import PitPolicy
from eqpit.settings import ProviderName


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunConfig(_Section):
    name: str
    seed: int
    provider: ProviderName
    start: date
    end: date


class DataConfig(_Section):
    root: str = "./data"
    snapshot: str = "latest"


class LagMonths(_Section):
    annual: int = 6
    quarterly: int = 4


class DelistingImputation(_Section):
    performance_nyse_amex: float = -0.30
    performance_nasdaq: float = -0.55
    other: float = 0.0


class PitConfig(_Section):
    cutoff_time_et: str = "16:00"
    extra_lag_trading_days: int = Field(default=1, ge=0)
    fallback_lag_months: LagMonths = LagMonths()
    delisting_imputation: DelistingImputation = DelistingImputation()


class UniverseConfig(_Section):
    size: int = 3000
    variants: list[int] = [1000]
    min_price_usd: float = 1.0
    exchanges: list[str] = ["NYSE", "AMEX", "NASDAQ"]
    share_types: list[str] = ["common"]
    survivors_only: bool = False


class Experiment(_Section):
    """One experiment. Sections later milestones consume stay free-form until then."""

    run: RunConfig
    data: DataConfig = DataConfig()
    pit: PitConfig = PitConfig()
    universe: UniverseConfig = UniverseConfig()
    signals: dict[str, Any] = {}
    evaluation: dict[str, Any] = {}
    replication: dict[str, Any] = {}
    combination: dict[str, Any] = {}
    backtest: dict[str, Any] = {}
    attribution: dict[str, Any] = {}
    experiment_log: dict[str, Any] = {}
    report: dict[str, Any] = {}
    synthetic: dict[str, Any] = {}
    edgar_free: dict[str, Any] = {}


def pit_policy(experiment: Experiment) -> PitPolicy:
    """The point-in-time rules an experiment sets."""
    pit = experiment.pit
    universe = experiment.universe
    return PitPolicy(
        extra_lag_trading_days=pit.extra_lag_trading_days,
        annual_lag_months=pit.fallback_lag_months.annual,
        quarterly_lag_months=pit.fallback_lag_months.quarterly,
        imputation_nyse_amex=pit.delisting_imputation.performance_nyse_amex,
        imputation_nasdaq=pit.delisting_imputation.performance_nasdaq,
        imputation_other=pit.delisting_imputation.other,
        min_price_usd=universe.min_price_usd,
        exchanges=tuple(universe.exchanges),
        share_types=tuple(universe.share_types),
    )


def panel_config(experiment: Experiment | None) -> PanelConfig:
    """Panel rules; defaults when no experiment is given."""
    if experiment is None:
        return PanelConfig()
    section = experiment.signals
    bounds = section.get("winsorize", {})
    return PanelConfig(
        lower=float(bounds.get("lower", 0.01)),
        upper=float(bounds.get("upper", 0.99)),
        min_coverage=float(section.get("min_coverage", 0.5)),
        min_names=int(section.get("min_names", 100)),
    )


def evaluation_config(experiment: Experiment | None) -> EvaluationConfig:
    """Evaluation choices; defaults when no experiment is given."""
    if experiment is None:
        return EvaluationConfig()
    section = experiment.evaluation
    return EvaluationConfig(
        quantiles=int(section.get("quantiles", 10)),
        weightings=tuple(section.get("weighting", ("ew", "vw"))),
        breakpoints=str(section.get("breakpoints", "nyse")),
        universes=(experiment.universe.size, *experiment.universe.variants),
        neutralize=tuple(experiment.signals.get("neutralize", ("none", "sector_size_beta"))),
    )


def trial_log_path(experiment: Experiment | None, provider: str, data_dir: Path) -> Path:
    """Licensed runs log to the committed file; synthetic runs stay under the data root."""
    if provider in ("sharadar", "wrds"):
        return Path("results") / "trial_log.csv"
    name = experiment.run.name if experiment is not None else provider
    return data_dir / "processed" / "trials" / name / "trial_log.csv"


def load_xbrl_map(path: Path) -> dict[str, list[str]]:
    """Load a canonical item to ordered tags map."""
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, list[str]] = yaml.safe_load(handle)
    return loaded


def load_config(path: Path) -> Experiment:
    """Load and validate an experiment YAML file."""
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return Experiment.model_validate(raw)


def canonical_json(value: object) -> str:
    """Stable JSON used for hashing configuration."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def config_hash(section: object) -> str:
    """Hex sha256 of a configuration section."""
    if isinstance(section, BaseModel):
        section = section.model_dump(mode="json")
    return hashlib.sha256(canonical_json(section).encode()).hexdigest()
