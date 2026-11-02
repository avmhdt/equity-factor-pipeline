"""CLI skeleton: every command exists and takes the shared options."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from efp import cli
from efp.cli import app
from efp.doctor import Check

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EFP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test.person@example.com")
    monkeypatch.setenv("EFP_LOG_JSON", "0")


@pytest.mark.parametrize(
    "command",
    [
        ["doctor"],
        ["data", "pull"],
        ["data", "pull-benchmarks"],
        ["data", "build-pit"],
        ["data", "verify"],
        ["synth", "generate"],
        ["universe", "build"],
        ["signals", "compute"],
        ["evaluate"],
        ["validate"],
        ["combine"],
        ["backtest"],
        ["capacity"],
        ["attribute"],
        ["report"],
        ["rebuild"],
        ["cache"],
        ["explog"],
    ],
)
def test_every_command_accepts_config_and_log_level(command: list[str]) -> None:
    result = runner.invoke(app, [*command, "--help"], env={"COLUMNS": "200", "NO_COLOR": "1"})
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--log-level" in result.output


def test_doctor_runs_offline() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "sec_user_agent" in result.output


def test_doctor_exits_zero_when_nothing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_doctor", lambda settings, network: [Check("x", "ok", "fine")])
    assert runner.invoke(app, ["doctor"]).exit_code == 0


def test_doctor_exits_non_zero_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_pending_commands_say_when_they_arrive() -> None:
    result = runner.invoke(app, ["capacity"])
    assert result.exit_code == 2


def test_rebuild_then_cache_ls_and_prune(tmp_path: Path) -> None:
    config = tmp_path / "experiment.yaml"
    config.write_text(
        "run: {name: t, seed: 1, provider: synthetic, start: 2001-01-31, end: 2002-12-31}\n",
        encoding="utf-8",
    )
    first = runner.invoke(app, ["rebuild", "--config", str(config)])
    assert first.exit_code == 0, first.output
    assert " ran " in first.output
    second = runner.invoke(app, ["rebuild", "--config", str(config)])
    assert " cached " in second.output
    forced = runner.invoke(app, ["rebuild", "--config", str(config), "--force"])
    assert " ran " in forced.output
    listing = runner.invoke(app, ["cache", "ls"])
    assert "resolve_config" in listing.output
    pruned = runner.invoke(app, ["cache", "prune", "--keep-last", "1", "--raw", "--keep", "2"])
    assert pruned.exit_code == 0
