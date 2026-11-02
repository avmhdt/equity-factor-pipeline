"""Typed environment settings, read from the process environment and ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["wrds", "sharadar", "edgar_free", "synthetic"]


class Settings(BaseSettings):
    """Every variable in ``.env.example``. Secrets are never logged or repr'd in clear."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    sec_user_agent: str = ""
    efp_provider: ProviderName = "sharadar"
    wrds_username: str = ""
    sharadar_api_key: SecretStr = SecretStr("")
    tiingo_api_key: SecretStr = SecretStr("")
    stooq_dir: Path | None = None
    data_root: str = ""
    efp_data_dir: str = ""
    sec_max_rps: float = Field(default=8.0, gt=0)
    efp_log_level: Literal["DEBUG", "INFO", "WARNING"] = "INFO"
    efp_log_json: bool = True
    efp_workers: int = Field(default=0, ge=0)

    @field_validator("stooq_dir", mode="before")
    @classmethod
    def _empty_path_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def data_dir(self) -> Path:
        """Root for raw/, interim/, processed/, external/ and logs/.

        ``EFP_DATA_DIR`` wins, then ``DATA_ROOT`` (``~`` expanded), then ``./data``.
        """
        if self.efp_data_dir:
            return Path(self.efp_data_dir).expanduser()
        if self.data_root:
            return Path(self.data_root).expanduser()
        return Path("data")


def get_settings() -> Settings:
    """Load settings fresh, so tests and long runs see the current environment."""
    return Settings()
