"""Settings loading and data-root precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from eqpit.settings import Settings, get_settings


def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("DATA_ROOT", "EFP_DATA_DIR", "SHARADAR_API_KEY", "STOOQ_DIR", "SEC_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean(monkeypatch, tmp_path)
    settings = get_settings()
    assert settings.data_dir == Path("data")
    assert settings.sec_max_rps == 8.0
    assert settings.efp_provider == "sharadar"


def test_efp_data_dir_wins_over_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_ROOT", "/one")
    monkeypatch.setenv("EFP_DATA_DIR", "/two")
    assert Settings().data_dir == Path("/two")


def test_data_root_expands_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_ROOT", "~/data/equity-factor-pipeline")
    assert "~" not in str(Settings().data_dir)


def test_reads_env_file_and_hides_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("SHARADAR_API_KEY=abc123\nSTOOQ_DIR=\n", encoding="utf-8")
    settings = Settings()
    assert settings.sharadar_api_key.get_secret_value() == "abc123"
    assert "abc123" not in repr(settings)
    assert settings.stooq_dir is None
