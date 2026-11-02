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

Two other spread models were run after the Corwin-Schultz result failed, at the owner's request. Headline figures don't use them.

- **Abdi-Ranaldo:** the two-day corrected estimator over 20 days. Each two-day squared spread `4 (c_t - m_t) (c_t - m_t+1)`, with `c` the log close and `m` the log mid-range, goes to zero if negative, then the roots are averaged and halved.
- **Size table:** published effective spreads by market cap. Up to $5B it uses SEC staff's 2013 mean relative effective spreads by cap band, weighted across price bands by their ticker counts and halved: 81.5, 29.1, 15.0, 8.7, 5.3 and 3.5 bps. Above $5B it uses Hagstromer's 1.47 bps for S&P 500 stocks in late 2015. The 2 bp floor applies to all three models.

`spread_models.csv` has each book under each model, with and without the cap, for the full sample and from 2010. `spread_model_capacity.csv` has capacity at each impact multiplier. `spread_model_checks.csv` compares each model with the published figures for the same stocks.

Published impact laws were run as a second round after the net failure, at the owner's request. Headline figures don't use them. Each gives average cost per dollar for a trade that is a share `x` of daily volume:

- **Y = 1, averaged:** `(2/3) * vol * sqrt(x)`. Toth et al. give `Y vol sqrt(Q/V)` with Y of order unity for the price change across an order, and filling evenly along that path pays 2/3 of it.
- **Almgren et al. 2005:** `I/2 + vol * 0.142 * x^(3/5)`, with `I = 0.314 * vol * x * (market cap / ADV)^(1/4)`, over one day.
- **Zarinelli et al. 2015:** `range * Y * x^delta / (1 + delta)`, with `range` the 20-day mean of `(high - low) / open`. Y and delta are 0.19 and 0.51 for ranks to 500, 0.15 and 0.46 to rank 1,000, and 0.12 and 0.42 beyond. The paper gives no thresholds for its size classes, so the D-06 ranks stand in.

`impact_models.csv` has each law with Corwin-Schultz and the size table, capped and uncapped, for the full sample and from 2010. `impact_model_capacity.csv` has capacity, `impact_model_checks.csv` has costs at set trade sizes, and `impact_trade_profile.csv` shows where the book's trades sit against the sources' range.

Costs apply to the trade in every name, including one leaving the book. A name with no inputs pays the spread floor and the top borrow rate.

Every composite runs uncapped, as FR-23 specifies, and capped at 10% of ADV, as FR-24 specifies for capacity. A capped book misses trades and stops being dollar neutral.

## Capacity

The headline book runs at 25 fund sizes from $10M to $10B with the 10% cap, at impact x0.5, x1 and x2. Capacity is the size where net FF5 + UMD alpha is half the zero-cost alpha, interpolated on log size. `capacity.csv` reports `estimated`, `above grid max`, `below grid min` or `no gross alpha`.

## Attribution

Monthly returns regressed on `mkt_rf`, `smb`, `hml`, `rmw`, `cma` and `umd` from French's files, with Newey-West errors, gross, net and net with the cap.

## Rebuild

`efp rebuild` runs each stage through a cache keyed on stage code, config, input keys and raw snapshot ids. Raw signals run together, because a month loop shares reads. Panels run per signal and evaluation per family, so a change to one signal's parameter re-runs that panel, its family and what follows, and nothing else. `pipeline_timings` is the one table that differs between runs.
