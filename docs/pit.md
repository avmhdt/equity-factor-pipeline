# How we know a value was public

Every row in the point-in-time tables carries `available_at`, the UTC instant it became public. A feature for rebalance date T may read a row only if `available_at <= cutoff(T)`, where `cutoff(T)` is the close on T: 16:00 New York, or the early close. `PitStore` is the only read path, and it applies that filter.

## Fundamentals

| Step | Rule | Where |
|---|---|---|
| Availability | The close of the next trading day after EDGAR acceptance (D-11). A filing accepted at 16:05 ET misses that day's cutoff | `eqpit/pit/filings.py` |
| No acceptance time | Filing date + 1 trading day, then report date + 1 trading day, then 6 months (annual) or 4 months (quarterly) after the period end. Each row records its `pit_method` | `eqpit/pit/filings.py` |
| Fiscal period end | Never an availability time. A row available by its period end fails the build | `assert_after_period_end` |
| Restatements | A new row at the restated filing's own time. The original row stays | `eqpit/pit/fundamentals.py` |
| Q4 and cash flow | 3-month values derived from year-to-date ones, public once both inputs are | `_derive_quarters` |

`results/tables/pit_method_coverage.csv` reports the share of rows per method.

### EDGAR acceptance times are UTC

`acceptanceDateTime` in EDGAR's submissions JSON ends in `Z`, and it means it. On 2026-11-01 we compared Apple filings from 2009, 2012, 2015 and 2026 with the EDGAR filing index:

| Filing date | JSON | Index (ET) |
|---|---|---|
| 2026-07-31 | `2026-07-31T10:01:02.000Z` | 2026-07-31 06:01:02 |
| 2015-07-22 | `2015-07-22T21:01:17.000Z` | 2015-07-22 17:01:17 |
| 2012-04-25 | `2012-04-25T21:01:16.000Z` | 2012-04-25 17:01:16 |
| 2009-01-23 | `2009-01-23T02:45:14.000Z` | 2009-01-22 21:45:14 |

The last row shows the 17:30 rule too: accepted at 21:45 ET on the 22nd, it carries a filing date of the 23rd.

## Prices and corporate actions

| Table | `available_at` |
|---|---|
| `prices_daily` | The close of that date |
| `corp_actions` | The close of the ex-date |
| `delistings` | The close of the delisting date |
| `securities` | 00:00 New York on `valid_from` |
| `universe_monthly` | The close of the as-of date |

`split_factor` only looks back: it is the cumulative ratio up to and including each date. A factor "to latest" would make old rows depend on later splits. `PitStore.prices` returns `adj_close` in the share basis of the as-of date.

## Labels

Forward returns stay out of the signal path. `compute_signals` hands each signal `store.for_signals(asof)`, which raises on `labels()` and on any later date.

## The leakage test

`tests/leakage/` is the `leakage` job, a required check on `main`.

| Test | What it proves |
|---|---|
| `test_no_feature_after_cutoff` | Every signal row's `input_available_at` is at or before its cutoff |
| `test_perturbation_invariance` | Corrupting every raw row not yet public at T, then rebuilding all tables, leaves signals at dates up to T bit-identical |
| `test_canary_leaky_signal_is_caught` | A signal that reads next month's returns is caught by the static check, and by perturbation when it lies about its timestamp |
| `test_signals_import_only_pitstore` | `efp.signals` has no route around the gate |
| `test_fundamentals_never_before_acceptance` | No fundamental is available before acceptance or by its period end |

## The free path

`edgar_free` covers only companies that trade today, so everything built from it carries `survivorship_limited: true` and the label `DEV ONLY — survivorship-limited`. Its numbers are never headline results.
