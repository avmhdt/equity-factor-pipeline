"""Synthetic market with listings, delistings, corporate actions, filings and planted premia.

The output is raw-provider data: unadjusted prices, vendor-style facts and filings, with the
same traps as real data (after-hours acceptance, restatements, year-to-date cash flows, Q4 only
as FY minus 9M, missing delisting returns, ticker changes).

Three signals carry a planted rank IC against next-month return, set by ``target_ic``:

- ``gp_at``: trailing-four-quarter (revenue - cogs) / latest total assets, as first reported.
- ``asset_growth`` (planted negative): latest total assets / assets four quarters earlier - 1.
- ``mom_12_1``: summed log total return over months m-11 to m-1 at the end of month m, which
  skips month m (months t-12 to t-2 for holding month t).

Fundamentals drive the plant only through filings public at the month-end close, under the
default one-day lag. Each month the loadings solve ``R b = rho`` on the realised correlation
of the three scores, so overlap between signals does not inflate any of them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import NormalDist

import numpy as np
import polars as pl
from numpy.typing import NDArray

from eqpit.calendar import NEW_YORK, sessions
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP
from eqpit.ids import company_from_cik, synthetic_id

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

PLANTED_SIGNALS = ("gp_at", "asset_growth", "mom_12_1")
"""Signals with a planted IC; ``asset_growth`` is planted with a negative sign."""

_SICS = (1311, 2834, 2860, 3571, 3674, 4911, 5311, 6021, 7372, 8731, 3312, 2000, 4512, 5812)
_EXCHANGES = ("NYSE", "NASDAQ", "AMEX", "OTHER")
_EXCHANGE_P = (0.42, 0.48, 0.08, 0.02)
_SHARE_TYPES = ("common", "adr", "etf", "reit", "unit", "other")
_SHARE_TYPE_P = (0.90, 0.03, 0.02, 0.03, 0.01, 0.01)
_FYE_MONTHS = (12, 3, 6, 9)
_FYE_P = (0.7, 0.1, 0.1, 0.1)

_REASON_HAZARDS = {
    "merger": 0.025,
    "performance": 0.015,
    "liquidation": 0.002,
    "exchange": 0.003,
    "unknown": 0.002,
}
"""Annual delisting hazards by normalised reason."""

_FORMS_ANNUAL = "10-K"
_FORMS_QUARTERLY = "10-Q"
_INSTANT_ITEMS = (
    "total_assets",
    "current_assets",
    "cash",
    "inventory",
    "ppe_gross",
    "ppe_net",
    "total_liabilities",
    "current_liabilities",
    "short_term_debt",
    "long_term_debt",
    "income_taxes_payable",
    "deferred_taxes",
    "stockholders_equity",
    "preferred_stock",
    "minority_interest",
)
_INCOME_ITEMS = (
    "revenue",
    "cogs",
    "operating_income",
    "net_income",
    "income_before_extra",
    "eps_diluted",
)
_CASH_FLOW_ITEMS = ("depreciation", "cfo")
_RESTATED_ITEMS = ("revenue", "net_income", "income_before_extra")


@dataclass(frozen=True)
class SyntheticConfig:
    """Market size and truth.

    Args:
        seed: random seed; the same seed gives the same market on every platform.
        n_securities: securities alive at the start; a delisted name is replaced by a listing.
        years: calendar years simulated.
        start_year: first calendar year.
        target_ic: planted rank IC of each signal in ``PLANTED_SIGNALS``.
    """

    seed: int = 7
    n_securities: int = 500
    years: int = 20
    start_year: int = 2001
    target_ic: float = 0.05


@dataclass
class _Companies:
    """Per-company draws and quarterly fundamentals, indexed ``[company, quarter]``."""

    fye_month: IntArray
    alt_revenue_tag: BoolArray
    quarter_ends: list[date]
    values: dict[str, FloatArray]
    accept_day: IntArray
    fiscal_quarter: IntArray


@dataclass
class _Tape:
    """Raw output of the daily simulation."""

    security: list[IntArray] = field(default_factory=list)
    day: list[IntArray] = field(default_factory=list)
    open: list[FloatArray] = field(default_factory=list)
    high: list[FloatArray] = field(default_factory=list)
    low: list[FloatArray] = field(default_factory=list)
    close: list[FloatArray] = field(default_factory=list)
    volume: list[FloatArray] = field(default_factory=list)
    shares: list[FloatArray] = field(default_factory=list)
    actions: list[tuple[int, int, str, float | None, str | None]] = field(default_factory=list)
    delistings: list[tuple[int, int, str, float | None]] = field(default_factory=list)
    truth: list[pl.DataFrame] = field(default_factory=list)
    month_shares: list[FloatArray] = field(default_factory=list)


@dataclass(frozen=True)
class _MonthEvents:
    """Corporate events drawn for one month, per security slot."""

    split: FloatArray
    stock_dividend: BoolArray
    spinoff: BoolArray
    halted: BoolArray


def _normal_scores(values: FloatArray, mask: BoolArray) -> FloatArray:
    """Normal score of each finite value among ``mask``; zero elsewhere."""
    usable = mask & np.isfinite(values)
    scores = np.zeros(len(values))
    count = int(usable.sum())
    if count < 3:
        return scores
    ranks = np.empty(count, dtype=np.int64)
    ranks[np.argsort(values[usable], kind="stable")] = np.arange(count)
    normal = NormalDist()
    quantiles = np.array([normal.inv_cdf((k + 0.5) / count) for k in range(count)])
    scores[usable] = quantiles[ranks]
    return scores


def _quarter_grid(start_year: int, years: int) -> list[date]:
    ends: list[date] = []
    for year in range(start_year - 1, start_year + years):
        ends.extend([date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31)])
    return ends


def _fundamentals(
    rng: np.random.Generator, count: int, quarter_ends: list[date], days: list[date]
) -> _Companies:
    """Exogenous quarterly fundamentals and filing days for every company slot."""
    quarters = len(quarter_ends)
    level = np.exp(rng.normal(math.log(2e9), 1.3, count))
    growth_mean = rng.normal(0.02, 0.012, count)
    growth = np.zeros((count, quarters))
    growth[:, 0] = growth_mean
    for q in range(1, quarters):
        shock = rng.normal(0.0, 0.02, count)
        growth[:, q] = growth_mean + 0.8 * (growth[:, q - 1] - growth_mean) + shock
    assets = level[:, None] * np.exp(np.cumsum(growth, axis=1))
    turnover = np.exp(rng.normal(math.log(0.25), 0.4, count))[:, None]
    margin_mean = rng.uniform(0.2, 0.6, count)
    margin = np.zeros((count, quarters))
    margin[:, 0] = margin_mean
    for q in range(1, quarters):
        shock = rng.normal(0.0, 0.012, count)
        margin[:, q] = margin_mean + 0.9 * (margin[:, q - 1] - margin_mean) + shock
    margin = np.clip(margin, 0.05, 0.9)
    revenue = assets * turnover * np.exp(rng.normal(0.0, 0.03, (count, quarters)))
    cogs = revenue * (1.0 - margin)
    operating = revenue * (margin - 0.12)
    income = operating * 0.75
    extraordinary = rng.random((count, quarters)) < 0.03
    net_income = income - extraordinary * 0.002 * assets
    depreciation = assets * 0.01
    cfo = income + depreciation + assets * rng.normal(0.0, 0.004, (count, quarters))
    has_deferred = (rng.random(count) < 0.5)[:, None]
    has_preferred = (rng.random(count) < 0.1)[:, None]
    has_minority = (rng.random(count) < 0.05)[:, None]
    liabilities = assets * 0.55
    preferred = assets * 0.02 * has_preferred
    minority = assets * 0.01 * has_minority
    values: dict[str, FloatArray] = {
        "total_assets": assets,
        "current_assets": assets * 0.35,
        "cash": assets * 0.10,
        "inventory": assets * 0.10 * np.exp(rng.normal(0.0, 0.05, (count, quarters))),
        "ppe_gross": assets * 0.45,
        "ppe_net": assets * 0.30,
        "total_liabilities": liabilities,
        "current_liabilities": assets * 0.25,
        "short_term_debt": assets * 0.05,
        "long_term_debt": assets * 0.20,
        "income_taxes_payable": assets * 0.01,
        "deferred_taxes": np.where(has_deferred, assets * 0.02, np.nan),
        "stockholders_equity": assets - liabilities - minority,
        "preferred_stock": np.where(has_preferred, preferred, np.nan),
        "minority_interest": np.where(has_minority, minority, np.nan),
        "revenue": revenue,
        "cogs": cogs,
        "operating_income": operating,
        "net_income": net_income,
        "income_before_extra": income,
        "depreciation": depreciation,
        "cfo": cfo,
    }
    fye_month = rng.choice(np.array(_FYE_MONTHS), size=count, p=_FYE_P).astype(np.int64)
    quarter_months = np.array([end.month for end in quarter_ends], dtype=np.int64)
    fiscal_quarter = ((quarter_months[None, :] - fye_month[:, None]) % 12) // 3
    fiscal_quarter[fiscal_quarter == 0] = 4
    day_ordinals = np.array([item.toordinal() for item in days])
    end_index = np.searchsorted(day_ordinals, [end.toordinal() for end in quarter_ends])
    annual = fiscal_quarter == 4
    lag = np.where(
        annual, rng.integers(45, 56, (count, quarters)), rng.integers(28, 33, (count, quarters))
    )
    accept_day = end_index[None, :] + lag
    return _Companies(
        fye_month=fye_month,
        alt_revenue_tag=rng.random(count) < 0.05,
        quarter_ends=quarter_ends,
        values=values,
        accept_day=accept_day.astype(np.int64),
        fiscal_quarter=fiscal_quarter.astype(np.int64),
    )


def _visible_quarter(companies: _Companies, listed_day: IntArray, day: int) -> IntArray:
    """Latest quarter per company whose filing is public at the close of ``day``.

    The plant uses the default lag: public from the close of the session after acceptance.
    Filings from before the listing do not exist. Returns -1 where there is none.
    """
    public = (companies.accept_day + 1 <= day) & (companies.accept_day >= listed_day[:, None])
    quarters = public.shape[1]
    latest = quarters - 1 - np.argmax(public[:, ::-1], axis=1)
    return np.where(public.any(axis=1), latest, -1).astype(np.int64)


def _fundamental_scores(
    companies: _Companies, visible: IntArray, listed_day: IntArray
) -> tuple[FloatArray, FloatArray]:
    """``gp_at`` and ``asset_growth`` per company from filings public now."""
    count = len(visible)
    gp_at = np.full(count, np.nan)
    growth = np.full(count, np.nan)
    revenue = companies.values["revenue"]
    cogs = companies.values["cogs"]
    assets = companies.values["total_assets"]
    rows = np.flatnonzero(visible >= 3)
    if len(rows) == 0:
        return gp_at, growth
    latest = visible[rows]
    first_seen = companies.accept_day[rows, latest - 3] >= listed_day[rows]
    rows, latest = rows[first_seen], latest[first_seen]
    window = np.stack([latest - k for k in range(4)], axis=1)
    gross = (revenue[rows[:, None], window] - cogs[rows[:, None], window]).sum(axis=1)
    gp_at[rows] = gross / assets[rows, latest]
    older = (latest >= 4) & (
        companies.accept_day[rows, np.maximum(latest - 4, 0)] >= listed_day[rows]
    )
    growth[rows[older]] = (
        assets[rows[older], latest[older]] / assets[rows[older], latest[older] - 4] - 1
    )
    return gp_at, growth


def _simulate(
    config: SyntheticConfig,
    rng: np.random.Generator,
    days: list[date],
    companies: _Companies,
    company_of: IntArray,
    primary_of: IntArray,
    capacity: int,
    tradable: BoolArray,
) -> tuple[_Tape, IntArray, IntArray]:
    """Run the market day by day, month by month."""
    tape = _Tape()
    rho = 2.0 * math.sin(math.pi * config.target_ic / 6.0)
    months = sorted({(item.year, item.month) for item in days})
    by_month: dict[tuple[int, int], list[int]] = {key: [] for key in months}
    for index, item in enumerate(days):
        by_month[(item.year, item.month)].append(index)
    beta = rng.normal(1.0, 0.3, capacity)
    vol = np.exp(rng.normal(math.log(0.02), 0.25, capacity))
    close = np.exp(rng.normal(math.log(25.0), 0.8, capacity))
    level = companies.values["total_assets"][company_of, 0]
    shares = level * np.exp(rng.normal(-0.5, 0.6, capacity)) / close
    payer = rng.random(capacity) < 0.4
    yield_annual = rng.uniform(0.01, 0.04, capacity)
    payer_month = rng.integers(0, 3, capacity)
    listed = np.full(capacity, len(days), dtype=np.int64)
    delisted = np.full(capacity, len(days), dtype=np.int64)
    is_primary = primary_of == np.arange(capacity)
    start_slots = int(np.flatnonzero(is_primary)[config.n_securities])
    alive = np.zeros(capacity, dtype=bool)
    alive[:start_slots] = True
    listed[alive] = 0
    next_slot = start_slots
    monthly_log = np.full((capacity, len(months)), np.nan)
    company_listed = np.full(company_of.max() + 1, len(days), dtype=np.int64)
    np.minimum.at(company_listed, company_of, listed)
    previous = close.copy()
    for month_number, key in enumerate(months):
        month_days = by_month[key]
        count_days = len(month_days)
        planted = np.zeros(capacity)
        if month_number > 0:
            last_day = by_month[months[month_number - 1]][-1]
            eligible = alive & tradable & is_primary & (close >= 1.0)
            visible = _visible_quarter(companies, company_listed, last_day)
            gp_company, growth_company = _fundamental_scores(companies, visible, company_listed)
            gp_at = gp_company[company_of]
            growth = growth_company[company_of]
            momentum = np.full(capacity, np.nan)
            if month_number >= 12:
                momentum = monthly_log[:, month_number - 12 : month_number - 1].sum(axis=1)
            planted = _planted_alpha([gp_at, -growth, momentum], eligible, rho)
            tape.truth.append(
                pl.DataFrame(
                    {
                        "asof": [days[last_day]] * int(eligible.sum()),
                        "slot": np.flatnonzero(eligible),
                        "gp_at": gp_at[eligible],
                        "asset_growth": growth[eligible],
                        "mom_12_1": momentum[eligible],
                    }
                )
            )
        market = rng.normal(0.0004, 0.01, count_days)
        noise = rng.standard_t(6, (count_days, capacity)) * vol * math.sqrt(4.0 / 6.0)
        log_returns = beta[None, :] * market[:, None] + noise
        spread = log_returns[:, alive & tradable].sum(axis=0).std()
        log_returns = log_returns + spread * planted[None, :] / count_days
        second = ~is_primary
        log_returns[:, second] = log_returns[:, primary_of[second]] + rng.normal(
            0.0, 0.002, (count_days, int(second.sum()))
        )
        exits = _draw_delistings(rng, alive & is_primary, close, count_days)
        for slot, (reason, offset) in exits.items():
            for member in np.flatnonzero(primary_of == slot):
                if alive[member]:
                    dlret = _delisting_return(rng, reason)
                    day_index = month_days[offset]
                    delisted[member] = day_index
                    tape.delistings.append((int(member), day_index, reason, dlret))
                    if reason == "merger":
                        tape.actions.append((int(member), day_index, "merger", None, None))
                    tape.actions.append((int(member), day_index, "delist", None, None))
        events = _month_events(rng, alive, close)
        ex_day = rng.integers(0, count_days, capacity)
        for position, day_index in enumerate(month_days):
            live = alive & (delisted >= day_index)
            ratio = np.ones(capacity)
            cash = np.zeros(capacity)
            hits = (ex_day == position) & live
            for slot in np.flatnonzero(hits & (events.split != 1.0)).tolist():
                ratio[slot] = events.split[slot]
                tape.actions.append((int(slot), day_index, "split", float(ratio[slot]), None))
            for slot in np.flatnonzero(hits & events.stock_dividend).tolist():
                ratio[slot] *= 1.05
                tape.actions.append((int(slot), day_index, "stock_dividend", 1.05, None))
            paying = hits & payer & (month_number % 3 == payer_month)
            cash[paying] = previous[paying] * yield_annual[paying] / 4.0
            for slot in np.flatnonzero(paying).tolist():
                tape.actions.append(
                    (int(slot), day_index, "cash_dividend", float(cash[slot]), None)
                )
            for slot in np.flatnonzero(hits & events.spinoff).tolist():
                cash[slot] += previous[slot] * 0.05
                tape.actions.append(
                    (int(slot), day_index, "spinoff", float(previous[slot] * 0.05), None)
                )
            total = np.exp(log_returns[position])
            close = np.where(live, (previous * total - cash) / ratio, close)
            shares = np.where(live, shares * ratio, shares)
            gap = rng.normal(0.0, 0.3, capacity) * vol
            opened = previous / ratio * np.exp(gap)
            top = np.maximum(opened, close) * (1.0 + np.abs(rng.normal(0.0, 0.5, capacity)) * vol)
            bottom = np.minimum(opened, close) * (
                1.0 - np.minimum(np.abs(rng.normal(0.0, 0.5, capacity)) * vol, 0.5)
            )
            volume = np.floor(shares * np.exp(rng.normal(math.log(0.004), 0.5, capacity)))
            volume = np.where(events.halted & (position < 6), 0.0, volume)
            slots = np.flatnonzero(live)
            tape.security.append(slots.astype(np.int64))
            tape.day.append(np.full(len(slots), day_index, dtype=np.int64))
            tape.open.append(opened[slots])
            tape.high.append(top[slots])
            tape.low.append(bottom[slots])
            tape.close.append(close[slots])
            tape.volume.append(volume[slots])
            tape.shares.append(shares[slots])
            previous = np.where(live, close, previous)
        monthly_log[:, month_number] = np.where(alive, log_returns.sum(axis=0), np.nan)
        tape.month_shares.append(shares.copy())
        gone = alive & (delisted <= month_days[-1])
        alive = alive & ~gone
        replacements = int(gone[is_primary].sum())
        next_slot = _list_new_securities(
            replacements,
            next_slot,
            capacity,
            primary_of,
            alive,
            listed,
            company_of,
            company_listed,
            month_days[-1] + 1,
        )
        previous = np.where(alive, close, previous)
    return tape, listed, delisted


def _planted_alpha(scores: list[FloatArray], eligible: BoolArray, rho: float) -> FloatArray:
    """Standardised alpha whose correlation with each score is ``rho`` this month."""
    normal = [_normal_scores(score, eligible) for score in scores]
    active = [item for item in normal if np.count_nonzero(item) >= 30]
    combined = np.zeros(len(eligible))
    if not active:
        return combined
    matrix = np.stack(active, axis=1)[eligible]
    correlation = np.atleast_2d(np.corrcoef(matrix, rowvar=False))
    loadings = np.linalg.solve(correlation, np.full(len(active), rho))
    explained = float(loadings @ correlation @ loadings)
    for load, item in zip(loadings, active, strict=True):
        combined += float(load) * item
    return combined / math.sqrt(max(1.0 - explained, 0.5))


def _delisting_return(rng: np.random.Generator, reason: str) -> float | None:
    """Vendor delisting return, missing for a share of events as on real feeds."""
    if reason == "merger":
        return None if rng.random() < 0.1 else float(rng.normal(0.2, 0.1))
    if reason == "performance" and rng.random() < 0.5:
        return float(max(rng.normal(-0.4, 0.25), -1.0))
    return None


def _draw_delistings(
    rng: np.random.Generator, alive: BoolArray, close: FloatArray, count_days: int
) -> dict[int, tuple[str, int]]:
    """Delisting events this month: slot to (reason, day offset)."""
    exits: dict[int, tuple[str, int]] = {}
    reasons = list(_REASON_HAZARDS)
    hazards = np.array([_REASON_HAZARDS[name] / 12.0 for name in reasons])
    draws = rng.random(len(alive))
    offsets = rng.integers(0, count_days, len(alive))
    for slot in np.flatnonzero(alive):
        boosted = hazards.copy()
        if close[slot] < 3.0:
            boosted[1] *= 3.0
        edge = np.cumsum(boosted)
        index = int(np.searchsorted(edge, draws[slot]))
        if index < len(reasons):
            exits[int(slot)] = (reasons[index], int(offsets[slot]))
    return exits


def _month_events(rng: np.random.Generator, alive: BoolArray, close: FloatArray) -> _MonthEvents:
    """Corporate events drawn at the start of a month."""
    size = len(alive)
    split = np.ones(size)
    forward = alive & (close > 120.0) & (rng.random(size) < 0.05)
    split[forward] = rng.choice(np.array([2.0, 3.0, 1.5]), size=int(forward.sum()))
    reverse = alive & (close < 1.5) & (rng.random(size) < 0.03)
    split[reverse] = 0.1
    return _MonthEvents(
        split=split,
        stock_dividend=alive & (rng.random(size) < 0.0005),
        spinoff=alive & (rng.random(size) < 0.0004),
        halted=alive & (rng.random(size) < 0.0005),
    )


def _list_new_securities(
    count: int,
    next_slot: int,
    capacity: int,
    primary_of: IntArray,
    alive: BoolArray,
    listed: IntArray,
    company_of: IntArray,
    company_listed: IntArray,
    first_day: int,
) -> int:
    """Bring ``count`` new securities to market at ``first_day``."""
    for _ in range(count):
        if next_slot >= capacity:
            return next_slot
        members = np.flatnonzero(primary_of == next_slot)
        alive[members] = True
        listed[members] = first_day
        company_listed[company_of[next_slot]] = first_day
        next_slot = int(members.max()) + 1
    return next_slot


def _securities_frame(
    rng: np.random.Generator,
    days: list[date],
    company_of: IntArray,
    primary_of: IntArray,
    listed: IntArray,
    delisted: IntArray,
    exchange: NDArray[np.str_],
    share_type: NDArray[np.str_],
    sic: IntArray,
    tape: _Tape,
) -> pl.DataFrame:
    """One row per validity window; a ticker change closes one window and opens the next."""
    rows: list[dict[str, object]] = []
    last_day = len(days) - 1
    for slot in np.flatnonzero(listed < len(days)):
        company = int(company_of[slot])
        cik = 1_000_000 + company
        suffix = "" if primary_of[slot] == slot else "B"
        first, final = int(listed[slot]), int(min(delisted[slot], last_day))
        cuts: list[int] = []
        span = final - first
        if span > 300 and rng.random() < 0.2:
            cuts.append(first + int(rng.integers(100, span - 100)))
        start = first
        for version, cut in enumerate([*cuts, None]):
            stop = cut - 1 if cut is not None else None
            rows.append(
                {
                    "security_id": synthetic_id(int(slot)),
                    "company_id": company_from_cik(cik),
                    "cik": cik,
                    "ticker": f"S{slot}{suffix}" + ("X" * version),
                    "name": f"Synthetic Co {company}",
                    "exchange": str(exchange[slot]),
                    "share_type": str(share_type[slot]),
                    "sic": int(sic[slot]),
                    "valid_from": days[start],
                    "valid_to": days[stop] if stop is not None else None,
                }
            )
            if cut is not None:
                new_ticker = f"S{slot}{suffix}X"
                tape.actions.append((int(slot), cut, "ticker_change", None, new_ticker))
                start = cut
    return pl.DataFrame(rows).with_columns(
        pl.col("valid_from").cast(pl.Date),
        pl.col("valid_to").cast(pl.Date),
        pl.col("sic").cast(pl.Int16),
    )


def _acceptance(rng: np.random.Generator, day: date, next_day: date) -> tuple[datetime, date]:
    """Acceptance instant in UTC and filing date; after 17:30 New York it is the next day."""
    draw = rng.random()
    if draw < 0.55:
        minute = int(rng.integers(6 * 60, 16 * 60))
    elif draw < 0.90:
        minute = int(rng.integers(16 * 60 + 5, 17 * 60 + 30))
    else:
        minute = int(rng.integers(17 * 60 + 31, 22 * 60))
    local = datetime.combine(day, time(minute // 60, minute % 60), NEW_YORK)
    return local.astimezone(UTC), (next_day if minute > 17 * 60 + 30 else day)


def _filings_frame(
    rng: np.random.Generator,
    days: list[date],
    companies: _Companies,
    company_listed: IntArray,
    company_delisted: IntArray,
) -> pl.DataFrame:
    """Filings by listed companies, each amended at a small rate."""
    rows: list[dict[str, object]] = []
    sequence = 0
    count, quarters = companies.accept_day.shape
    for company in range(count):
        listed, ended = int(company_listed[company]), int(company_delisted[company])
        if listed >= len(days):
            continue
        cik = 1_000_000 + company
        for quarter in range(quarters):
            day = int(companies.accept_day[company, quarter])
            if day < listed or day > min(ended, len(days) - 2):
                continue
            fiscal = int(companies.fiscal_quarter[company, quarter])
            annual = fiscal == 4
            year = days[day].year
            for version in (0, 1):
                if version == 1:
                    if rng.random() >= 0.03:
                        break
                    day = day + int(rng.integers(30, 60))
                    if day > min(ended, len(days) - 2):
                        break
                sequence += 1
                accepted, filed = _acceptance(rng, days[day], days[day + 1])
                form = _FORMS_ANNUAL if annual else _FORMS_QUARTERLY
                rows.append(
                    {
                        "cik": cik,
                        "company": company,
                        "quarter": quarter,
                        "accession": f"{cik:010d}-{year % 100:02d}-{sequence % 1_000_000:06d}",
                        "form": form + ("/A" if version else ""),
                        "period_end": companies.quarter_ends[quarter],
                        "filed": filed,
                        "acceptance_dt": accepted,
                        "fiscal": fiscal,
                        "amended": version == 1,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("period_end").cast(pl.Date),
        pl.col("filed").cast(pl.Date),
        pl.col("acceptance_dt").cast(pl.Datetime("ns", "UTC")),
    )


def _fact_rows(
    filings: pl.DataFrame,
    item: str,
    tag: pl.Expr,
    values: FloatArray,
    start: FloatArray | None,
) -> pl.DataFrame:
    """One fact per filing row with a finite value."""
    finite = np.isfinite(values)
    frame = filings.with_columns(
        tag.alias("tag"),
        pl.Series("value", values),
        pl.Series("start_day", start if start is not None else np.full(len(values), np.nan)),
        pl.lit(item).alias("item_name"),
    )
    return frame.filter(pl.Series(finite)).select(
        "cik",
        "tag",
        pl.when(pl.col("start_day").is_not_nan())
        .then(pl.col("start_day").cast(pl.Int32).cast(pl.Date))
        .alias("start"),
        pl.col("period_end").alias("end"),
        "value",
        pl.when(pl.col("item_name").eq("eps_diluted"))
        .then(pl.lit("USD/shares"))
        .when(pl.col("item_name").eq("shares_out"))
        .then(pl.lit("shares"))
        .otherwise(pl.lit("USD"))
        .alias("unit"),
        "accession",
        "form",
        "filed",
        "fp",
    )


def _facts_frame(
    filings: pl.DataFrame,
    companies: _Companies,
    rng: np.random.Generator,
    shares_quarterly: FloatArray,
) -> pl.DataFrame:
    """Vendor-style facts: 3m income, year-to-date cash flow, 12m at the fiscal year end."""
    company = filings["company"].to_numpy()
    quarter = filings["quarter"].to_numpy()
    fiscal = filings["fiscal"].to_numpy()
    amended = filings["amended"].to_numpy()
    ends = companies.quarter_ends
    end_days = np.array([(item - date(1970, 1, 1)).days for item in ends], dtype=np.float64)
    quarter_start = end_days[quarter - 1] + 1.0
    year_first = quarter - (fiscal - 1)
    year_start = end_days[year_first - 1] + 1.0
    is_q4 = fiscal == 4
    framed = filings.with_columns(
        pl.when(pl.col("fiscal").eq(4))
        .then(pl.lit("FY"))
        .otherwise(pl.format("Q{}", pl.col("fiscal")))
        .alias("fp")
    )
    tags = {item: DEFAULT_XBRL_MAP[item][0] for item in DEFAULT_XBRL_MAP}
    revenue_tag = (
        pl.when(pl.Series(companies.alt_revenue_tag[company]))
        .then(pl.lit("Revenues"))
        .otherwise(pl.lit(tags["revenue"]))
    )
    factor = np.ones(len(company))
    restated_rows = int(amended.sum())
    signs = rng.choice(np.array([-1.0, 1.0]), restated_rows)
    factor[amended] = 1.0 + signs * rng.uniform(0.03, 0.08, restated_rows)
    parts: list[pl.DataFrame] = []
    for item in _INSTANT_ITEMS:
        values = np.where(amended, np.nan, companies.values[item][company, quarter])
        parts.append(_fact_rows(framed, item, pl.lit(tags[item]), values, None))
    comparative = framed.filter(pl.Series(quarter >= 4) & pl.Series(~amended))
    comparison_company = comparative["company"].to_numpy()
    comparison_quarter = comparative["quarter"].to_numpy() - 4
    parts.append(
        _fact_rows(
            comparative.with_columns(
                pl.Series("period_end", [ends[int(q)] for q in comparison_quarter], dtype=pl.Date)
            ),
            "total_assets",
            pl.lit(tags["total_assets"]),
            companies.values["total_assets"][comparison_company, comparison_quarter],
            None,
        )
    )
    shares = np.where(amended, np.nan, shares_quarterly[company, quarter])
    parts.append(_fact_rows(framed, "shares_out", pl.lit(tags["shares_out"]), shares, None))
    for item in (*_INCOME_ITEMS, *_CASH_FLOW_ITEMS):
        if item == "eps_diluted":
            quarterly = companies.values["net_income"] / np.maximum(shares_quarterly, 1.0)
        else:
            quarterly = companies.values[item]
        running = np.concatenate(
            [np.zeros((quarterly.shape[0], 1)), np.cumsum(quarterly, axis=1)], axis=1
        )
        cumulative = running[company, quarter + 1] - running[company, year_first]
        three_month = quarterly[company, quarter]
        restated = item in _RESTATED_ITEMS
        keep = ~amended | restated
        scale = factor if restated else 1.0
        tag = revenue_tag if item == "revenue" else pl.lit(tags[item])
        if item in _INCOME_ITEMS:
            values = np.where(~is_q4 & keep, three_month * scale, np.nan)
            parts.append(_fact_rows(framed, item, tag, values, quarter_start))
        selected = keep & ((fiscal > 1) | (item in _CASH_FLOW_ITEMS))
        year_to_date = np.where(selected, cumulative * scale, np.nan)
        parts.append(_fact_rows(framed, item, tag, year_to_date, year_start))
    return pl.concat(parts, how="vertical").with_columns(
        pl.format("CIK:{}", pl.col("cik")).alias("company_id")
    )


def generate_market(config: SyntheticConfig) -> dict[str, pl.DataFrame]:
    """Generate every raw dataset of the synthetic market.

    Returns frames named ``securities``, ``prices``, ``actions``, ``delistings``, ``filings``,
    ``facts``, ``risk_free`` and ``truth``.
    """
    rng = np.random.default_rng(config.seed)
    days = sessions(
        date(config.start_year, 1, 1), date(config.start_year + config.years - 1, 12, 31)
    )
    hazard = sum(_REASON_HAZARDS.values())
    spare = int(config.n_securities * hazard * config.years * 2.0) + 20
    companies_total = config.n_securities + spare
    quarter_ends = _quarter_grid(config.start_year, config.years)
    companies = _fundamentals(rng, companies_total, quarter_ends, days)
    second_class = rng.random(companies_total) < 0.04
    company_of = np.repeat(np.arange(companies_total), 1 + second_class.astype(np.int64))
    capacity = len(company_of)
    first_slot = np.concatenate([[0], np.cumsum(1 + second_class.astype(np.int64))[:-1]])
    primary_of = first_slot[company_of]
    exchange = rng.choice(np.array(_EXCHANGES), size=capacity, p=_EXCHANGE_P)
    share_type = rng.choice(np.array(_SHARE_TYPES), size=capacity, p=_SHARE_TYPE_P)
    exchange = exchange[primary_of]
    share_type = share_type[primary_of]
    sic = rng.choice(np.array(_SICS), size=capacity)[primary_of].astype(np.int64)
    tradable = np.isin(exchange, ["NYSE", "AMEX", "NASDAQ"]) & (share_type == "common")
    tape, listed, delisted = _simulate(
        config, rng, days, companies, company_of, primary_of, capacity, tradable
    )
    securities = _securities_frame(
        rng, days, company_of, primary_of, listed, delisted, exchange, share_type, sic, tape
    )
    company_listed = np.full(companies_total, len(days), dtype=np.int64)
    np.minimum.at(company_listed, company_of, listed)
    company_delisted = np.zeros(companies_total, dtype=np.int64)
    np.maximum.at(company_delisted, company_of, np.where(listed < len(days), delisted, 0))
    filings = _filings_frame(rng, days, companies, company_listed, company_delisted)
    shares_quarterly = _quarterly_shares(
        tape, days, quarter_ends, company_of, primary_of, companies_total
    )
    facts = _facts_frame(filings, companies, rng, shares_quarterly)
    day_series = pl.Series("date", days, dtype=pl.Date)
    security_ids = np.array([synthetic_id(int(slot)) for slot in range(capacity)])
    prices = (
        pl.DataFrame(
            {
                "security_id": security_ids[np.concatenate(tape.security)],
                "day": np.concatenate(tape.day),
                "open": np.concatenate(tape.open),
                "high": np.concatenate(tape.high),
                "low": np.concatenate(tape.low),
                "close": np.concatenate(tape.close),
                "volume": np.concatenate(tape.volume),
                "shares_out": np.concatenate(tape.shares),
            }
        )
        .with_columns(day_series.gather(pl.Series(np.concatenate(tape.day))).alias("date"))
        .drop("day")
    )
    actions = pl.DataFrame(
        [
            (synthetic_id(slot), days[min(day, len(days) - 1)], action, value, ticker)
            for slot, day, action, value, ticker in tape.actions
        ],
        schema={
            "security_id": pl.String,
            "ex_date": pl.Date,
            "action": pl.String,
            "value": pl.Float64,
            "new_ticker": pl.String,
        },
        orient="row",
    )
    delisting_rows = pl.DataFrame(
        [
            (synthetic_id(slot), days[day], reason, str(exchange[slot]), dlret)
            for slot, day, reason, dlret in tape.delistings
        ],
        schema={
            "security_id": pl.String,
            "delist_date": pl.Date,
            "reason": pl.String,
            "exchange_at_delist": pl.String,
            "dlret_raw": pl.Float64,
        },
        orient="row",
    ).with_columns(pl.lit(None, dtype=pl.Int16).alias("delist_code"))
    truth = (
        pl.concat(tape.truth, how="vertical")
        .with_columns(pl.format("SYN:{}", pl.col("slot")).alias("security_id"))
        .drop("slot")
    )
    month_ends_seen = sorted({(item.year, item.month): item for item in days}.values())
    risk_free = pl.DataFrame(
        {
            "month": month_ends_seen,
            "rf": 0.002 + 0.0005 * np.sin(np.arange(len(month_ends_seen)) / 12.0),
        },
        schema={"month": pl.Date, "rf": pl.Float64},
    )
    return {
        "securities": securities.sort("security_id", "valid_from"),
        "prices": prices.sort("security_id", "date"),
        "actions": actions.sort("security_id", "ex_date", "action"),
        "delistings": delisting_rows.sort("security_id"),
        "filings": filings.drop("company", "quarter", "fiscal", "amended").sort("accession"),
        "facts": facts.sort("accession", "tag", "start", "end"),
        "risk_free": risk_free,
        "truth": truth.sort("asof", "security_id"),
    }


def _quarterly_shares(
    tape: _Tape,
    days: list[date],
    quarter_ends: list[date],
    company_of: IntArray,
    primary_of: IntArray,
    companies_total: int,
) -> FloatArray:
    """Primary-class shares at each quarter end, per company."""
    months = sorted({(item.year, item.month) for item in days})
    position = {key: index for index, key in enumerate(months)}
    panel = np.stack(tape.month_shares, axis=1)
    result = np.full((companies_total, len(quarter_ends)), panel[:, 0].max())
    slots = primary_of[np.unique(company_of, return_index=True)[1]]
    for index, end in enumerate(quarter_ends):
        month = position.get((end.year, end.month))
        result[:, index] = panel[slots, month if month is not None else 0]
    return result


def snapshot_time(seed: int) -> datetime:
    """Fixed snapshot instant for a seed, so reruns are byte-identical."""
    return datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=seed)
