# Data

Raw data stays under `data/`, which is git-ignored. Every download is an immutable snapshot with a manifest, and every output records the snapshot ids it came from.

## Sources

| Source | Tables | Use | Licence |
|---|---|---|---|
| Sharadar | `SEP`, `SF1`, `ACTIONS`, `TICKERS` | Prices, fundamentals, corporate actions, delisted names | Paid. Aggregate results may be published (P-10) |
| SEC EDGAR | Submissions bulk file | Filing acceptance times | Public |
| Ken French | FF5 and momentum, monthly | Validation and attribution | Public |
| Open Source Asset Pricing | `SignalDoc`, long-short returns, release 2026.10 | Replication targets | Public |
| EDGAR XBRL and Stooq | Company facts, daily prices | The free path only; watermarked, never in headline results | Public |

## Published spreads

The size table in `efp/backtest/spread_table.py` and the benchmark checks use two published sources, not a download:

- SEC staff, *A characterization of market quality for small capitalization US equities*, 2013 MIDAS data, Tables 1 and 4.
- Hagstromer, *Overestimated effective spreads: Implications for investors*, midpoint effective spread for S&P 500 stocks, November 30 to December 4, 2015. The published form is *Bias in the effective bid-ask spread*, Journal of Financial Economics, 2021.

## Pulling and building

```bash
uv run efp data pull --provider sharadar
```

```bash
uv run efp data pull-benchmarks --sources french,osap
```

```bash
uv run efp data build-pit --provider sharadar
```

`efp doctor --network` checks the key and table access first. Bulk downloads are capped at 10 MB/s and run one table at a time. EDGAR requests stay under the SEC's rate limit and send the `SEC_USER_AGENT` from `.env`.

## Traps in the vendor data

| Trap | Handling |
|---|---|
| `SEP` OHLCV is split-adjusted, `closeunadj` is raw | Stored prices are raw. Adjustment happens at read time, from data known at that date |
| `dividend` is split-adjusted | Multiplied by `closeunadj / close` |
| `sharesbas` is on the latest split basis | Market cap is `close * sharesbas * sharefactor` |
| `spinoffdividend` is per parent share | Treated as a cash distribution on the parent |
| `delisted.value` is a market cap in millions | Not a return. All delisting returns are imputed per DQ-08 |
| `SF1` dimensions | `AR*` only, so values are as reported |
| AAPL and MSFT answer without a key | `efp doctor` checks access with GME |

## What the vendor lacks

Sharadar has no gross PP&E, taxes payable, minority interest, deferred charges, deferred taxes or preferred stock. Book equity is plain `equity`, and `inv_at` uses net PP&E. HML still reaches a correlation of 0.987 with French's, so no substitute was tried. The gaps do explain two failed OSAP replications, `noa` and `accruals`.

## Published outputs

Under D-13, only aggregate tables and figures are committed: no security-level data and no return series. `results/runs/<run_id>/returns.parquet` stays local, and `metrics.json` keeps each trial's t so a fresh clone can still rebuild `multiple_testing`.
