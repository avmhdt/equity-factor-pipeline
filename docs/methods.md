# Methods

Results are in the [README](../README.md) and choices are in [decisions.md](decisions.md). This page says how each number is built.

## Universe

The largest 3,000 US common stocks by market cap at each month end, priced above $1, on NYSE, AMEX or NASDAQ. Names that later delist stay in. The top 1,000 is a variant.

## Signals

Each signal reads through `PitStore.for_signals(asof)`, which refuses labels and any date past `asof`. Values are winsorized at the 1st and 99th percentiles, signed so that higher is better, and z-scored by date. A signal-date needs 100 names and half the universe, or it's dropped.

Neutralized scores are residuals from a cross-sectional regression on FF-49 dummies, log market cap and beta, re-scaled to unit variance. `ind_mom` is one value per industry, so sector dummies span it and it has no neutralized form.

See [signals.md](signals.md) for definitions.

## Evaluation

- **Rank IC:** Spearman correlation of score and next-month return, by date. The t is Newey-West with `floor(4 (T / 100) ^ (2 / 9))` lags.
- **Spreads:** top decile minus bottom, equal and value weighted, on NYSE breakpoints.
- **Fama-MacBeth:** through `linearmodels`, each signal alone and each family together.
- **Turnover and decay:** rank autocorrelation, decile turnover, and IC by horizon and lag.

## Replication

French factors use NYSE 2x3 sorts formed in June, except UMD, which is monthly.

OSAP portfolios follow each row of `SignalDoc`: side, weighting, quantile, breakpoints, holding period, start month, price floor and exchanges. They use every eligible name, with no neutralization. Five accounting signals use OSAP's annual timing: the newest fiscal year at least 6 months old and under 18.

Post-publication decay follows McLean and Pontiff (2016): returns scaled by each anomaly's in-sample mean, regressed on post-sample and post-publication dummies with signal effects and month-clustered errors.

## Multiple testing

Every logged trial gets a Newey-West t, flags for t > 2 and t > 3.0, and a Benjamini-Hochberg-Yekutieli adjusted p across the whole log. Flags are two-sided. A rebuild adds no row for a trial the log already holds with the same parameters and result.

## Combination

All walk-forward. A return period must end by T to inform a score at T.

- **Equal:** mean of available scores.
- **IC-weighted:** trailing 36-month mean IC, purged one month, negatives floored at zero, equal if none is positive.
- **Ridge:** `RidgeCV` on an expanding window of at least 120 months, refit every 12, purged one month, with `TimeSeriesSplit` inner folds. The target is the return demeaned by date.

## Backtest

Each month, top and bottom deciles equal-weighted, dollar neutral, gross 2.0. Positions drift during the month and return to target at the next rebalance. A name with no return earns zero, which matches `forward_returns` after a delisting. Series are excess returns on a self-financing book, with no interest on short proceeds.

Execution modes are the close, and the next session's open, close and (O+H+L+C)/4. The last is a proxy, since there's no intraday data. Holding returns run from one execution to the next, so all modes share the same delisting treatment.

## Costs

D-06 fixes these as modelling assumptions.

| Cost | Model |
|---|---|
| Half-spread | Corwin-Schultz over 20 days, negative estimates at zero, floor 2 bps |
| Impact | `k * daily vol * sqrt(trade / ADV)` with `k = 1.0`, 20-day windows |
| Borrow | 0.25% a year for the top 500, 1.0% to rank 1,000, 5.0% beyond |

Costs apply to the trade in every name, including one leaving the book. A name with no inputs pays the spread floor and the top borrow rate.

Every composite runs uncapped, as FR-23 specifies, and capped at 10% of ADV, as FR-24 specifies for capacity. A capped book misses trades and stops being dollar neutral.

## Capacity

The headline book runs at 25 fund sizes from $10M to $10B with the 10% cap, at impact x0.5, x1 and x2. Capacity is the size where net FF5 + UMD alpha is half the zero-cost alpha, interpolated on log size. `capacity.csv` reports `estimated`, `above grid max`, `below grid min` or `no gross alpha`.

## Attribution

Monthly returns regressed on `mkt_rf`, `smb`, `hml`, `rmw`, `cma` and `umd` from French's files, with Newey-West errors, gross, net and net with the cap.

## Rebuild

`efp rebuild` runs each stage through a cache keyed on stage code, config, input keys and raw snapshot ids. Raw signals run together, because a month loop shares reads. Panels run per signal and evaluation per family, so a change to one signal's parameter re-runs that panel, its family and what follows, and nothing else. `pipeline_timings` is the one table that differs between runs.
