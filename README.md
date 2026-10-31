# Cross-sectional equity factor research pipeline

> Point-in-time data, a library of factors, and the evaluation stack a systematic equity desk uses every day.

**Status:** planning

| | |
|---|---|
| Track | Quant Researcher |
| Estimated effort | 6–8 weeks |
| Stack | Python (Polars or pandas), SQL, statsmodels |
| Data cost | Free → Academic |
| Strongest for | Systematic equity, multi-strat pods |

## Requirements

- [ ] Point-in-time universe: top 1,000–3,000 US stocks by market cap, rebuilt monthly, including companies that later delisted.
- [ ] Corporate actions handled: splits, dividends, ticker changes, and delisting returns (with an adjustment when they are missing).
- [ ] Fundamentals lagged to the SEC filing acceptance date, never to the fiscal period end.
- [ ] A library of 20+ signals across value, momentum, quality, investment and low-risk, each winsorized and cross-sectionally z-scored.
- [ ] Neutralization to sector, size and beta, with a switch to show results before and after.
- [ ] Evaluation: rank IC and IC information ratio, quantile spread returns, Fama–MacBeth regressions with Newey–West errors, turnover and decay curves.
- [ ] A combination layer (equal weight, IC weighted, ridge) feeding a cost-aware long–short backtest.
- [ ] One command rebuilds every result from raw files; intermediate data is cached and versioned.

## Data

Raw and licensed data live in `data/`, which is git-ignored. Only download scripts and small synthetic fixtures are committed.

| Dataset | Cost | What it gives you |
|---|---|---|
| [CRSP and Compustat (via WRDS)](https://wrds-www.wharton.upenn.edu) | Academic | The standard: delisted names and point-in-time fundamentals. Free through most universities. |
| [SEC EDGAR XBRL APIs](https://www.sec.gov/edgar/sec-api-documentation) | Free | Company facts with filing dates. You build point-in-time tables yourself. |
| [Sharadar (Nasdaq Data Link)](https://data.nasdaq.com) | Paid | Survivorship-free prices and fundamentals. The most affordable serious option outside academia. |
| [Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) | Free | Benchmark factor returns to validate your replications against. |
| [Open Source Asset Pricing](https://www.openassetpricing.com) | Free | Hundreds of published signals with code. Ready-made replication targets. |
| [JKP Global Factor Data](https://jkpfactors.com) | Free | Global factor returns and characteristics for tests outside the US. |
| [yfinance (avoid for final results)](https://github.com/ranaroussi/yfinance) | Free | No delisted tickers, so results are survivorship-biased. Fine for prototyping only. |

## Success criteria

### Works (correct and reproducible)

- [ ] Your momentum and size long–short returns correlate above 0.9 with Ken French’s series; value above 0.8 (definitions differ).
- [ ] An automated leakage test fails the build if any feature uses data timestamped after its rebalance date.
- [ ] Delisted stocks are in the universe, and a survivors-only comparison shows the size of the bias you avoided.
- [ ] The full pipeline reproduces from raw data with one command on a clean machine.

### Impresses hirers

- [ ] Replicate 20+ published anomalies and show post-publication decay, in line with McLean and Pontiff (2016).
- [ ] Judge significance against the t > 3.0 multiple-testing hurdle of Harvey, Liu and Zhu (2016), not t > 2.
- [ ] Attribute the combined signal to FF5 plus momentum and report the residual alpha honestly.
- [ ] Net-of-cost results with a capacity estimate: the AUM at which costs erase half the alpha.
- [ ] A two-page memo per signal family: hypothesis, evidence, and why it might stop working.

## Questions this project must answer

- How do you know a fundamental value was public on that date?
- Why does your value factor differ from Fama–French’s?
- What happens to your Sharpe if you trade at next-day VWAP instead of the close?
