"""Published effective spreads by market cap: the size-bucket option in D-06.

The spec names this option but gives no values. These come from two published sources, fixed
before any result that uses them was seen:

- Up to $5B: SEC staff, "A characterization of market quality for small capitalization US
  equities", 2013 MIDAS data. Table 4 mean relative effective spread by cap and price band,
  weighted across price bands by the Table 1 ticker counts. Effective spread there is twice the
  distance to the midpoint, so it is halved here.
- Above $5B: Hagstromer, "Overestimated effective spreads: Implications for investors", 1.47 bps
  on the midpoint measure for S&P 500 stocks, November 30 to December 4, 2015. That paper is on
  the half-spread convention already.

Both describe the 2010s. Applied to earlier years, the table understates spreads.
"""

from __future__ import annotations

import polars as pl

CAP_BANDS_MUSD = (100.0, 250.0, 500.0, 1000.0, 2000.0, 5000.0)
SEC_TICKERS = (
    (284, 145, 61, 30, 10, 7),
    (115, 109, 79, 51, 24, 12),
    (122, 148, 175, 143, 92, 60),
    (27, 50, 99, 166, 176, 159),
    (2, 14, 28, 64, 134, 227),
)
"""SEC Table 1: daily average tickers, price bands in rows and cap bands in columns."""
SEC_MEAN_RELATIVE_PCT = (
    (1.708, 0.477, 0.301, 0.244, 0.235, 0.221),
    (1.553, 0.521, 0.237, 0.144, 0.127, 0.121),
    (1.499, 0.625, 0.267, 0.139, 0.088, 0.074),
    (1.762, 0.800, 0.344, 0.170, 0.089, 0.056),
    (1.238, 0.910, 0.514, 0.251, 0.129, 0.072),
)
"""SEC Table 4: mean relative effective spread in percent, laid out as ``SEC_TICKERS``."""
LARGE_CAP_HALF_SPREAD_BPS = 1.47
BAND_NAMES = ("<$100M", "$100-250M", "$250-500M", "$500M-1B", "$1-2B", "$2-5B")
LARGE_BAND = ">$5B"


def band_half_spreads_bps() -> tuple[float, ...]:
    """Half-spread in bps for each SEC cap band, ticker-weighted across price bands."""
    found: list[float] = []
    for column in range(len(CAP_BANDS_MUSD)):
        tickers = [row[column] for row in SEC_TICKERS]
        spreads = [row[column] for row in SEC_MEAN_RELATIVE_PCT]
        total = sum(count * spread for count, spread in zip(tickers, spreads, strict=True))
        found.append(total / sum(tickers) * 100.0 / 2.0)
    return tuple(found)


def size_bucket_half_spread(mcap: pl.Expr) -> pl.Expr:
    """Half-spread in decimals for a market cap in USD; a missing cap gives the smallest band."""
    values = (*band_half_spreads_bps(), LARGE_CAP_HALF_SPREAD_BPS)
    expression: pl.Expr = pl.lit(values[0] / 10_000.0)
    for bound, value in zip(CAP_BANDS_MUSD, values[1:], strict=True):
        expression = (
            pl.when(mcap >= bound * 1.0e6).then(pl.lit(value / 10_000.0)).otherwise(expression)
        )
    return expression
