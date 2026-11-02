"""Published market impact laws, beside the coefficient of 1.0 that D-06 set for the headline.

Added after the net failure, at the owner's request, and the second such round after spreads.
Each value below was read from the paper's own text before any result used it, and none was
fitted to this project's data. All are average execution cost per dollar traded, against the
price at arrival, where ``x`` is the trade as a fraction of daily volume.

- ``square_root``: ``k * vol * sqrt(x)``, the D-06 form with vol the daily return deviation.
- ``square_root`` with ``PEAK_TO_AVERAGE``: Toth et al., "Anomalous price impact and the critical
  nature of liquidity in financial markets", Physical Review X, 2011, give
  ``Y * vol * sqrt(Q / V)`` with Y "of order unity" for the price change between the first and
  last trade. That is a peak. Filling evenly along that path pays the mean of ``sqrt(s)`` on
  ``[0, 1]``, which is 2/3 of it.
- ``almgren_2005``: Almgren, Thum, Hauptmann and Li, "Direct estimation of equity market impact",
  2005. Realized cost is ``I / 2 + vol * eta * (x / T) ^ (3 / 5)`` with
  ``I = gamma * vol * x * (shares / V) ^ (1 / 4)``, gamma 0.314 and eta 0.142. ``T`` is one day,
  and ``shares / V`` is market cap over ADV.
- ``zarinelli_2015``: Zarinelli, Treccani, Farmer and Lillo, "Beyond the square root", Market
  Microstructure and Liquidity, 2015, Table 4. Peak impact is ``Y * x ^ delta`` in units of
  ``(high - low) / open``, so ``range`` here is that measure and not the return deviation.
  Dividing by ``1 + delta`` gives the mean along the path. The paper gives no thresholds for its
  size classes, so the D-06 tiers at ranks 500 and 1,000 stand in.
"""

from __future__ import annotations

import math
from typing import Literal

ImpactForm = Literal["square_root", "almgren_2005", "zarinelli_2015"]
PEAK_TO_AVERAGE = 2.0 / 3.0
ALMGREN_GAMMA = 0.314
ALMGREN_ETA = 0.142
ALMGREN_EXPONENT = 0.6
ALMGREN_LIQUIDITY_EXPONENT = 0.25
ZARINELLI_FITS: tuple[tuple[int | None, float, float], ...] = (
    (500, 0.19, 0.51),
    (1000, 0.15, 0.46),
    (None, 0.12, 0.42),
)
"""Largest rank, Y and delta, for Zarinelli's large, medium and small classes."""


def almgren_cost(x: float, vol: float, cap_over_adv: float) -> float:
    """Average cost per dollar under Almgren et al., traded over one day."""
    permanent = ALMGREN_GAMMA * vol * x * cap_over_adv**ALMGREN_LIQUIDITY_EXPONENT
    return float(permanent / 2.0 + vol * ALMGREN_ETA * x**ALMGREN_EXPONENT)


def zarinelli_cost(x: float, daily_range: float, rank: int | None) -> float:
    """Average cost per dollar under Zarinelli et al., from peak impact by size class."""
    for limit, scale, delta in ZARINELLI_FITS:
        if limit is None or (rank is not None and rank <= limit):
            return float(daily_range * scale * x**delta / (1.0 + delta))
    raise ValueError("the last fit has no rank limit")


def square_root_cost(x: float, vol: float, coefficient: float = 1.0) -> float:
    """Cost per dollar under the D-06 form."""
    return coefficient * vol * math.sqrt(x)
