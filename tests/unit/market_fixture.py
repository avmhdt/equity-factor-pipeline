"""The ten-security fixture market, with a split, a dividend, a ticker change, two delistings."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from eqpit.calendar import month_ends
from eqpit.pit.corpactions import build_corp_actions
from eqpit.pit.delisting import build_delistings
from eqpit.pit.policy import PitPolicy
from eqpit.pit.prices import build_prices
from eqpit.pit.securities import build_securities

FIXTURES = Path(__file__).parents[1] / "fixtures"


def read(name: str) -> pl.DataFrame:
    """Read a CSV fixture with dates parsed."""
    return pl.read_csv(FIXTURES / name, try_parse_dates=True)


def actions() -> pl.DataFrame:
    """Canonical actions, with the empty ``value`` cells typed."""
    raw = read("actions_10sec.csv").with_columns(pl.col("value").cast(pl.Float64))
    return build_corp_actions(raw)


def prices() -> pl.DataFrame:
    """The ``prices_daily`` table."""
    return build_prices(read("prices_10sec.csv"), actions())


def delistings(policy: PitPolicy | None = None) -> pl.DataFrame:
    """The ``delistings`` table."""
    raw = read("delistings_10sec.csv").with_columns(
        pl.col("delist_code").cast(pl.Int16), pl.col("dlret_raw").cast(pl.Float64)
    )
    return build_delistings(raw, policy or PitPolicy())


def securities() -> pl.DataFrame:
    """The ``securities`` table."""
    raw = read("securities_10sec.csv").with_columns(
        pl.col("valid_to").cast(pl.Date), pl.col("sic").cast(pl.Int16)
    )
    return build_securities(raw)


def ends() -> list:
    """Month ends covered by the fixture."""
    frame = read("prices_10sec.csv")
    return month_ends(frame["date"].min(), frame["date"].max())
