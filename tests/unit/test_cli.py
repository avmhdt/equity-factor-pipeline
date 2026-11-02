"""CLI skeleton: every command exists and takes the shared options."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
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


def commands(group: typer.core.TyperGroup, prefix: str = "") -> Iterator[tuple[str, Any]]:
    for name, command in group.commands.items():
        if isinstance(command, typer.core.TyperGroup):
            yield from commands(command, f"{prefix}{name} ")
        else:
            yield f"{prefix}{name}", command


def test_every_command_in_the_spec_exists_and_takes_the_shared_options() -> None:
    root = typer.main.get_command(app)
    assert isinstance(root, typer.core.TyperGroup)
    found = dict(commands(root))
    assert set(found) == {
        "doctor",
        "data pull",
        "data pull-benchmarks",
        "data build-pit",
        "data verify",
        "synth generate",
        "universe build",
        "signals compute",
        "evaluate",
        "validate",
        "combine",
        "backtest",
        "capacity",
        "attribute",
        "report",
        "rebuild",
        "cache",
        "explog",
    }
    for name, command in found.items():
        options = {option for param in command.params for option in param.opts}
        assert {"--config", "--log-level"} <= options, name


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
        "run: {name: t, seed: 1, provider: synthetic, start: 2001-01-31, end: 2002-12-31}\n"
        "synthetic: {n_securities: 40, years: 2}\n",
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
    assert "synthetic_raw" in listing.output
    pruned = runner.invoke(app, ["cache", "prune", "--keep-last", "1", "--raw", "--keep", "2"])
    assert pruned.exit_code == 0
