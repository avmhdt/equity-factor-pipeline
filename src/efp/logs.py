"""Structured logging: console lines plus JSON lines under ``data/logs/`` (NFR-08)."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import structlog


class _JsonLineTee:
    """Processor that appends each event to a JSON-lines file before console rendering."""

    def __init__(self, handle: TextIO) -> None:
        self._handle = handle

    def __call__(
        self, logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        line = json.dumps(dict(event_dict), sort_keys=True, default=str)
        self._handle.write(line + "\n")
        self._handle.flush()
        return event_dict


def configure_logging(level: str = "INFO", json_dir: Path | None = None) -> Path | None:
    """Configure structlog; returns the JSON log path when file logging is on."""
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    log_path: Path | None = None
    if json_dir is not None:
        json_dir.mkdir(parents=True, exist_ok=True)
        log_path = json_dir / f"efp-{datetime.now(UTC):%Y%m%d}.jsonl"
        processors.append(_JsonLineTee(log_path.open("a", encoding="utf-8")))
    processors.append(structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    return log_path
