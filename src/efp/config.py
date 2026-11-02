"""Validated experiment configuration (YAML)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from efp.backtest.capacity import CapacityConfig
from efp.backtest.costs import CostConfig
from efp.backtest.run import BacktestConfig
from efp.combine.walkforward import CombineConfig, Method, composite_name, variant_name
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


def panel_config(experiment: Experiment | None, signal: str | None = None) -> PanelConfig:
    """Panel rules; ``signals.overrides.<id>`` replaces ``winsorize`` for one signal."""
    if experiment is None:
        return PanelConfig()
    section = experiment.signals
    override = section.get("overrides", {}).get(signal, {})
    bounds = override.get("winsorize", section.get("winsorize", {}))
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


def combine_config(experiment: Experiment) -> CombineConfig:
    """Composite choices."""
    section = experiment.combination
    ridge = section.get("ridge", {})
    defaults = CombineConfig()
    return CombineConfig(
        ic_window_months=int(section.get("ic_window_months", defaults.ic_window_months)),
        alphas=tuple(float(value) for value in ridge.get("alphas", defaults.alphas)),
        min_train_months=int(ridge.get("min_train_months", defaults.min_train_months)),
        purge_months=int(ridge.get("purge_months", defaults.purge_months)),
    )


def combine_methods(experiment: Experiment) -> tuple[Method, ...]:
    """Composite methods to build."""
    return tuple(experiment.combination.get("methods", ("equal", "ic_weighted", "ridge")))


def headline_method(experiment: Experiment) -> Method:
    """The method behind the headline composite."""
    return experiment.combination.get("headline_method", "equal")  # type: ignore[no-any-return]


def headline_composite(experiment: Experiment) -> tuple[str, str]:
    """The composite the headline numbers quote: its name and neutralization mode."""
    mode = str(experiment.combination.get("headline_neutralize", "sector_size_beta"))
    return composite_name(headline_method(experiment), mode), mode


def capacity_composites(experiment: Experiment) -> list[str]:
    """The headline composite, then each universe variant as a sensitivity."""
    headline, _ = headline_composite(experiment)
    return [headline, *[variant_name(headline, size) for size in experiment.universe.variants]]


def backtest_config(experiment: Experiment) -> BacktestConfig:
    """Backtest choices: D-05 and D-06 with the spec's defaults."""
    section = experiment.backtest
    costs = section.get("costs", {})
    impact = costs.get("impact", {})
    tiers = costs.get("borrow", {}).get("tiers_pct_annual")
    capacity = section.get("capacity", {})
    defaults = BacktestConfig()
    cost_defaults = defaults.costs
    grid = defaults.capacity
    return BacktestConfig(
        construction=section.get("construction", defaults.construction),
        gross=float(section.get("gross_leverage", defaults.gross)),
        quantiles=int(experiment.evaluation.get("quantiles", defaults.quantiles)),
        executions=tuple(section.get("execution", defaults.executions)),
        headline_execution=section.get("headline_execution", defaults.headline_execution),
        aum=float(section.get("headline_aum_usd", defaults.aum)),
        costs=CostConfig(
            floor_bps=float(costs.get("half_spread", {}).get("floor_bps", cost_defaults.floor_bps)),
            impact_coefficient=float(impact.get("coefficient", cost_defaults.impact_coefficient)),
            adv_days=int(impact.get("adv_window_days", cost_defaults.adv_days)),
            vol_days=int(impact.get("vol_window_days", cost_defaults.vol_days)),
            borrow_tiers=(
                tuple((int(tier["max_rank"]), float(tier["rate"])) for tier in tiers[:-1])
                if tiers
                else cost_defaults.borrow_tiers
            ),
            borrow_rest=float(tiers[-1]["rate"]) if tiers else cost_defaults.borrow_rest,
        ),
        impact_multipliers=tuple(
            float(value)
            for value in costs.get("sensitivity_multipliers", defaults.impact_multipliers)
        ),
        capacity=CapacityConfig(
            aum_min=float(capacity.get("aum_min_usd", grid.aum_min)),
            aum_max=float(capacity.get("aum_max_usd", grid.aum_max)),
            points=int(capacity.get("points", grid.points)),
            max_participation=float(capacity.get("max_adv_participation", grid.max_participation)),
        ),
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
