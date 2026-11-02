"""Validated experiment configuration (YAML)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

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
