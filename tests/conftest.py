"""Shared test setup."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Logging holds a stream reference, so no test may inherit another's configuration."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()
