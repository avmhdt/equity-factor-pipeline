"""Shared test setup."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest
import structlog

from eqpit.pit.build import build_pit
from eqpit.providers.synthetic import SyntheticProvider, write_market
from eqpit.store import PitStore
from eqpit.synthetic import SyntheticConfig

SMALL_MARKET = SyntheticConfig(seed=11, n_securities=150, years=8, start_year=2010)
FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="rewrite tests/golden/synthetic.json; explain why in the commit message",
    )


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Logging holds a stream reference, so no test may inherit another's configuration."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


@pytest.fixture(scope="session")
def small_raw(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Raw snapshots of a small synthetic market."""
    root = tmp_path_factory.mktemp("market") / "raw"
    write_market(SMALL_MARKET, root)
    return root


@pytest.fixture(scope="session")
def small_provider(small_raw: Path) -> SyntheticProvider:
    """Provider over the small market."""
    return SyntheticProvider(small_raw)


@pytest.fixture(scope="session")
def small_store(
    small_provider: SyntheticProvider, tmp_path_factory: pytest.TempPathFactory
) -> PitStore:
    """Point-in-time tables of the small market."""
    root = tmp_path_factory.mktemp("pit") / "pit"
    build_pit(small_provider, root)
    return PitStore.open(root)


def read_fixture(name: str) -> pl.DataFrame:
    """Read a CSV fixture with dates parsed."""
    return pl.read_csv(FIXTURES / name, try_parse_dates=True)
