"""Rules that shape the point-in-time tables, set from experiment configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PitPolicy:
    """Point-in-time and universe rules.

    Args:
        extra_lag_trading_days: D-11. 1 means the close of the next trading day after
            acceptance; 0 means the acceptance instant itself.
        annual_lag_months: DQ-03 fallback lag for annual filings without any filing date.
        quarterly_lag_months: DQ-03 fallback lag for quarterly filings.
        imputation_nyse_amex: DQ-08 return for performance delistings on NYSE or AMEX.
        imputation_nasdaq: DQ-08 return for performance delistings elsewhere.
        imputation_other: DQ-08 return for every other missing delisting return.
        zero_imputation: sensitivity switch; every missing delisting return becomes zero.
        min_price_usd: DQ-07 price floor.
        exchanges: DQ-07 eligible exchanges.
        share_types: DQ-07 eligible share types.
        horizons_months: forward-return horizons written to ``forward_returns``.
    """

    extra_lag_trading_days: int = 1
    annual_lag_months: int = 6
    quarterly_lag_months: int = 4
    imputation_nyse_amex: float = -0.30
    imputation_nasdaq: float = -0.55
    imputation_other: float = 0.0
    zero_imputation: bool = False
    min_price_usd: float = 1.0
    exchanges: tuple[str, ...] = ("NYSE", "AMEX", "NASDAQ")
    share_types: tuple[str, ...] = ("common",)
    horizons_months: tuple[int, ...] = tuple(range(1, 13))
