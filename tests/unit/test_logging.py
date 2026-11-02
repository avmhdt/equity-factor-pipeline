"""NFR-08: structured JSON log lines."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from efp.logs import configure_logging


def test_events_reach_the_json_file(tmp_path: Path) -> None:
    path = configure_logging("INFO", tmp_path)
    assert path is not None
    structlog.get_logger("test").info("stage_end", stage="pit", rows=12)
    structlog.get_logger("test").debug("hidden")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "stage_end"
    assert event["stage"] == "pit"
    assert event["rows"] == 12
    assert event["level"] == "info"
    assert "timestamp" in event


def test_console_only_mode() -> None:
    assert configure_logging("WARNING", None) is None
