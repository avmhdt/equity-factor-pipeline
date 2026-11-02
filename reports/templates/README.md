# Cross-sectional equity factor research pipeline

> Point-in-time data, a library of factors, and the evaluation stack a systematic equity desk uses every day.

**Bottom line:** a composite of {{spec.signals}} published signals earns a real gross return, with FF5 + UMD alpha of {{headline.gross_alpha}} a year (t {{headline.gross_alpha_t}}). As a monthly equal-weight decile book it doesn't survive its costs: at {{spec.aum}} the net Sharpe is {{headline.net_sharpe}} with no trade cap and {{headline.capped_sharpe}} with one, on the Corwin-Schultz spreads D-06 set for the headline. Those spreads turned out to be far too high, so after that failure the book was rerun on other spread models. Better spreads don't rescue it: on published spreads the net Sharpe is {{spread.table.headline.net_sharpe}} and {{spread.table.headline.capped_sharpe}}, because market impact is the larger cost. The best case found, the top {{spec.variant}} on published spreads, is about zero.

This README is rendered by `efp report`. Every number comes from [results/readme_numbers.json](results/readme_numbers.json), and a test fails the build if one doesn't. Family memos are in [reports/memos](reports/memos).

## Hypothesis

Published equity anomalies should still add up to something tradable once you measure them without look-ahead, include dead companies and charge realistic costs. The project tests that in three steps: rebuild the data so nothing leaks, confirm the signals match published work, then ask what survives trading.

## Data

- **Prices and fundamentals:** Sharadar (`SEP`, `SF1`, `ACTIONS`, `TICKERS`), {{spec.start}} to {{spec.end}}, including delisted names.
- **Filing times:** SEC EDGAR acceptance timestamps. {{pit.acceptance}} of fundamentals rows link to an acceptance time; the rest use the filing date plus one session, which is never earlier.
- **Point in time:** a fundamental is visible from the close of the session after acceptance. Signal code reads only through a gate that refuses later rows and all labels.
- **Delistings:** missing delisting returns are imputed at -{{spec.delist_nyse}} on NYSE and AMEX and -{{spec.delist_nasdaq}} on NASDAQ for performance failures, following Shumway.
- **Benchmarks:** Ken French factors and Open Source Asset Pricing (OSAP) series.

Raw data stays in a git-ignored folder. Only aggregate tables and figures are published, and return series aren't. See [docs/data.md](docs/data.md) and [docs/pit.md](docs/pit.md).

## Method

- **Universe:** top {{spec.universe}} US common stocks by market cap, rebuilt monthly, priced above {{spec.min_price}}.
- **Signals:** {{spec.signals}} across value, momentum, quality, investment, low risk and size, winsorized and z-scored each month. Each runs with and without neutralization to FF-49 sector, size and beta.
- **Evaluation:** rank IC, decile spreads, Fama-MacBeth, turnover and decay, with Newey-West errors.
- **Combination:** equal weight; IC-weighted on a trailing {{spec.ic_window}} months; ridge on an expanding window of at least {{spec.min_train}} months. All walk-forward, with a {{spec.purge}}-month purge.
- **Headline book:** equal weight of neutralized scores, chosen before the first backtest ran. Dollar neutral, gross leverage {{spec.leverage}}, rebalanced monthly into equal-weight deciles.
- **Costs:** Corwin-Schultz half-spread with a {{spec.floor_bps}} bp floor, square-root impact with coefficient {{spec.impact}} on {{spec.adv_days}}-day ADV, and borrow of {{spec.borrow_top}} for the top {{spec.tier_top}}, {{spec.borrow_mid}} to rank {{spec.tier_mid}} and {{spec.borrow_rest}} beyond. These are modelling assumptions, not measurements. Two other spread models were run later; see [below](#other-spread-models-run-after-the-failure).

Details are in [docs/methods.md](docs/methods.md) and [docs/signals.md](docs/signals.md).

## Results

### The data is right

Our factors against Ken French's:

| Factor | Correlation |
|---|---|
| UMD | {{french.umd}} |
| SMB | {{french.smb}} |
| HML | {{french.hml}} |
| CMA | {{french.cma}} |
| RMW | {{french.rmw}} |

Dropping delisted names would have added {{survivorship.bias}} a year to the equal-weight universe, raising it from {{survivorship.mean_full}} to {{survivorship.mean_survivors}} and its Sharpe from {{survivorship.sharpe_full}} to {{survivorship.sharpe_survivors}}.

![French replication](reports/figures/french_scatter.png)

### Published anomalies replicate, and they decay

{{osap.passed}} of {{osap.total}} signals correlate at {{spec.min_correlation}} or more with OSAP's published series with the same sign, over {{osap.shortest}} to {{osap.longest}} months. The bar was {{spec.min_replicated}}.

On OSAP's series, pooled returns fall {{postpub.post_sample}} out of sample (t {{postpub.post_sample_t}}) and {{postpub.after}} after publication (t {{postpub.after_t}}). McLean and Pontiff (2016) report {{spec.paper_post_sample}} and {{spec.paper_post_publication}} across {{spec.paper_signals}} anomalies, so the direction agrees and the post-publication decline here is smaller. Our own Sharadar series can't test this: the data starts in {{spec.start}}, so only {{postpub.ours_signals}} signals have enough in-sample months.

![Post-publication decay](reports/figures/post_publication.png)

### Significance

All {{trials.total}} logged trials are judged against t > {{spec.hurdle}} (Harvey, Liu and Zhu, 2016) and BHY-adjusted p-values. {{testing.positive.over_3}} trials clear t = {{spec.hurdle}} with a positive sign, and {{testing.positive.bhy}} survive BHY at 5%. Many of those are the same composites gross. The hurdle is two-sided, so the net rows that also clear it do so because they lose money significantly.

Neutralizing to sector, size and beta usually shrinks an IC without killing it:

![Neutralization](reports/figures/neutralization_ic.png)

### The composite

Headline: equal weight, neutralized, top {{spec.universe}}, next-day VWAP proxy, {{spec.aum}}.

| | Gross | Net, no cap | Net, {{spec.adv_cap}} ADV cap |
|---|---|---|---|
| Mean per year | {{headline.gross_mean}} | {{headline.net_mean}} | {{headline.capped_mean}} |
| Sharpe | {{headline.gross_sharpe}} | {{headline.net_sharpe}} | {{headline.capped_sharpe}} |
| FF5 + UMD alpha | {{headline.gross_alpha}} (t {{headline.gross_alpha_t}}) | {{headline.net_alpha}} (t {{headline.net_alpha_t}}) | {{headline.net_capped_alpha}} (t {{headline.net_capped_alpha_t}}) |

Gross loadings are RMW {{headline.gross_rmw}}, HML {{headline.gross_hml}}, UMD {{headline.gross_umd}} and market {{headline.gross_mkt_rf}}, with R-squared of {{headline.gross_r2}}. The six factors explain over half the variance, and a large alpha remains.

Costs are {{headline.total_cost}} a year: {{headline.impact}} impact, {{headline.spread}} spread and {{headline.borrow}} borrow, on {{headline.turnover}} of the book replaced each month.

![Gross and net](reports/figures/composite_gross_net.png)

Other methods, gross Sharpe then net with no cap: IC-weighted {{method.ic_weighted.gross_sharpe}}, {{method.ic_weighted.net_sharpe}}; ridge {{method.ridge.gross_sharpe}}, {{method.ridge.net_sharpe}}. Fitting weights didn't help after costs.

Execution price barely matters next to costs. The composite's gross Sharpe is {{timing.close.gross}} at the close and {{timing.next_vwap_proxy.gross}} at the next-day VWAP proxy.

![Execution timing](reports/figures/execution_timing.png)

### Costs and capacity

D-06's sensitivity scales every cost together:

| Costs | Net Sharpe, no cap | Net Sharpe, ADV cap |
|---|---|---|
| x{{spec.low_multiplier}} | {{headline.cost_0.5.uncapped_sharpe}} | {{headline.cost_0.5.capped_sharpe}} |
| x1 | {{headline.cost_1.uncapped_sharpe}} | {{headline.cost_1.capped_sharpe}} |
| x{{spec.high_multiplier}} | {{headline.cost_2.uncapped_sharpe}} | {{headline.cost_2.capped_sharpe}} |

Capacity is the fund size where net alpha is {{spec.half}} of zero-cost alpha, here {{headline.half_alpha}}. **Under Corwin-Schultz, no fund size from {{spec.grid_low}} to {{spec.grid_high}} reaches it:** at {{spec.grid_low}} net alpha is already {{headline.alpha_at_$10M}}, because spread and borrow don't shrink with size. Past about {{spec.aum}} net alpha rises again only because the ADV cap freezes the book; that isn't capacity.

The top {{spec.variant}}, added afterwards as a labelled sensitivity, gives alpha of {{top1000.gross_alpha}} (t {{top1000.gross_alpha_t}}), net Sharpe {{top1000.net_sharpe}}, and the same capacity outcome.

![Capacity](reports/figures/capacity_curve.png)

### Other spread models, run after the failure

These runs came after the Corwin-Schultz result failed, at the owner's request, so treat them as a follow-up and not as a test. Corwin-Schultz stays the headline, and every run is in the trial log. The reading rule was set before they ran: a model's net result counts only as far as its estimates match published spreads for the same stocks.

**Abdi-Ranaldo** uses daily close, high and low prices. **The size table** isn't an estimator: it's published effective spreads by market cap, from an SEC staff study of {{spec.table_year}} up to $5B and Hagströmer's S&P 500 sample of {{spec.large_cap_year}} above that. The spec names the option but gives no values, so these were sourced before any result used them.

Mean half-spread in bps against those published figures:

| Stocks | Published | Corwin-Schultz | Abdi-Ranaldo |
|---|---|---|---|
| Top {{spec.tier_top}}, late {{spec.large_cap_year}} | {{spec.large_cap_bps}} | {{check.cs.top.bps}} | {{check.ar.top.bps}} |
| $2-5B, {{spec.table_year}} | {{check.published.large}} | {{check.cs.large.bps}} | {{check.ar.large.bps}} |
| $500M-1B, {{spec.table_year}} | {{check.published.middle}} | {{check.cs.middle.bps}} | {{check.ar.middle.bps}} |
| $100-250M, {{spec.table_year}} | {{check.published.small}} | {{check.cs.small.bps}} | {{check.ar.small.bps}} |
| Under $100M, {{spec.table_year}} | {{check.published.micro}} | {{check.cs.micro.bps}} | {{check.ar.micro.bps}} |

Both daily-range estimators read far too high outside the smallest stocks: {{check.cs.top.ratio}}x and {{check.ar.top.ratio}}x on large caps. Abdi-Ranaldo is no improvement here, so under the rule neither net result says much about true costs. The size table matches because it is those figures, so that isn't independent evidence. Its figures describe the 2010s and it applies them to every year, which understates earlier spreads, so results are also cut from {{spec.recent_start}}.

Net Sharpe at {{spec.aum}}, next-day VWAP proxy, with capacity at impact x1:

| Book and spreads | No cap | ADV cap | ADV cap, {{spec.recent_start}} on | Capacity |
|---|---|---|---|---|
| Top {{spec.universe}}, Corwin-Schultz | {{spread.cs.headline.net_sharpe}} | {{spread.cs.headline.capped_sharpe}} | {{spread.cs.headline.recent.capped_sharpe}} | {{spread.cs.headline.capacity_1}} |
| Top {{spec.universe}}, Abdi-Ranaldo | {{spread.ar.headline.net_sharpe}} | {{spread.ar.headline.capped_sharpe}} | {{spread.ar.headline.recent.capped_sharpe}} | {{spread.ar.headline.capacity_1}} |
| Top {{spec.universe}}, size table | {{spread.table.headline.net_sharpe}} | {{spread.table.headline.capped_sharpe}} | {{spread.table.headline.recent.capped_sharpe}} | {{spread.table.headline.capacity_1}} |
| Top {{spec.variant}}, Corwin-Schultz | {{spread.cs.top1000.net_sharpe}} | {{spread.cs.top1000.capped_sharpe}} | {{spread.cs.top1000.recent.capped_sharpe}} | {{spread.cs.top1000.capacity_1}} |
| Top {{spec.variant}}, Abdi-Ranaldo | {{spread.ar.top1000.net_sharpe}} | {{spread.ar.top1000.capped_sharpe}} | {{spread.ar.top1000.recent.capped_sharpe}} | {{spread.ar.top1000.capacity_1}} |
| Top {{spec.variant}}, size table | {{spread.table.top1000.net_sharpe}} | {{spread.table.top1000.capped_sharpe}} | {{spread.table.top1000.recent.capped_sharpe}} | {{spread.table.top1000.capacity_1}} |

![Spread models](reports/figures/spread_models.png)

What this supports:

- **Corwin-Schultz overstated spreads, and that mattered.** Published spreads cut the headline book's spread cost from {{spread.cs.headline.spread}} to {{spread.table.headline.spread}} a year.
- **It isn't why the book fails.** Impact is {{spread.table.headline.impact}} a year either way. With the published table the top {{spec.universe}} still nets {{spread.table.headline.net_mean}} uncapped and {{spread.table.headline.capped_mean}} capped.
- **The best case is about zero, not positive.** The top {{spec.variant}} on the size table nets {{spread.table.top1000.net_mean}} uncapped and {{spread.table.top1000.capped_mean}} capped, with capped alpha of {{spread.table.top1000.capped_alpha}} (t {{spread.table.top1000.capped_alpha_t}}). It's negative from {{spec.recent_start}} on, at {{spread.table.top1000.recent.capped_mean}}, and it's the best of {{spec.models}} models across {{spec.books}} books, picked after the fact.
- **Capacity exists only in that cell, and it's small:** {{spread.table.top1000.capacity_1}} at impact x1, {{spread.table.top1000.capacity_0.5}} at x{{spec.low_multiplier}} and {{spread.table.top1000.capacity_2}} at x{{spec.high_multiplier}}.
- **Impact is now the assumption to question.** It's the largest cost in the headline book under every model, and unlike spreads it hasn't been checked against published data. These runs don't test it.

### How many tries

The headline rests on {{trials.total}} logged trials: {{trials.replication}} replication, {{trials.composite}} composite, {{trials.value}} value, {{trials.momentum}} momentum, {{trials.quality}} quality, {{trials.investment}} investment, {{trials.low_risk}} low risk and {{trials.size}} size. {{trials.dirty}} were run from a dirty tree and are kept. {{trials.late_spread}} of the composite rows are the later spread-model runs, marked in `x_note`. `efp explog export` hands the log to the sibling backtest-overfitting-lab repo.

## What failed

- **The headline hypothesis.** The composite doesn't pay for itself as specified, at any size tested.
- **{{osap.failed}} replications** fall short: `sue` r {{signal.sue.osap}}, `noa` r {{signal.noa.osap}}, `accruals` r {{signal.accruals.osap}}, `fscore` r {{signal.fscore.osap}}. Each is listed with its likely cause in the table. Tuning stopped there.
- **Capacity has almost no dollar answer.** The book fails the test at the bottom of the grid in all but one cell, and that cell is the size table on the top {{spec.variant}}, at {{spread.table.top1000.capacity_1}}.
- **Our own decay test** is too short to say anything.
- **Abdi-Ranaldo** didn't improve on Corwin-Schultz against published spreads for these stocks.
- **Neutralized `ind_mom`** was noise for two milestones before a table moved and gave it away. It's one number per industry, so sector dummies span it. It's now left out, and the old trial rows stay in the log.

## Limitations

- **The spread estimates are poor.** Corwin-Schultz reads {{check.cs.top.bps}} bps on large caps against a published {{spec.large_cap_bps}}, and Abdi-Ranaldo is no closer. The headline keeps Corwin-Schultz because D-06 set it, so its net figures overstate spread cost.
- **The size table is a snapshot.** It holds {{spec.table_year}} and {{spec.large_cap_year}} figures in fixed dollar bands, which understates spreads in earlier years and ignores price.
- **Impact is unverified.** It's the largest cost in the headline book, and none of this tests the coefficient of {{spec.impact}}. A better execution model could change the net result, and that hasn't been measured.
- **The book is deliberately naive.** Monthly rebalancing to equal weight has no turnover control. A desk would trade less.
- **Uncapped and capped are both flawed.** Uncapped positions reach {{spec.position_adv}}x ADV. Capped books miss trades and drift, with net exposure up to {{spec.capped_exposure}} of NAV.
- **Restatements and items.** Sharadar's `AR*` dimensions are as-reported, but it lacks gross PP&E, taxes payable, deferred taxes and preferred stock. Book equity is plain `equity`.
- **VWAP proxy.** There is no intraday data, so it's (O+H+L+C)/4 of the next session.
- **Borrow is assumed,** not measured, and the flat top rate is likely too low for hard-to-borrow shorts.
- **Free path.** The EDGAR and Stooq route is survivorship-limited and watermarked; no headline result uses it.

## How to reproduce

Anyone, no data needed:

```bash
uv run efp synth generate --out data/raw/synthetic --seed 7
```

```bash
uv run efp rebuild --config configs/synthetic.yaml
```

With licensed snapshots in `data/raw/`:

```bash
uv run efp rebuild --config configs/headline.yaml
```

`efp rebuild` runs every stage through a content-addressed cache and rewrites every table, figure, memo and this README.

## Questions this project must answer

**How do you know a fundamental value was public on that date?** Each value carries the SEC's acceptance timestamp, and it's visible only from the close of the next session. A gate enforces that for every read, and a CI test corrupts all later data and requires signal values to stay bit-identical.

**Why does your value factor differ from Fama-French's?** Construction. Correlation with HML is {{gap.hml_ff_convention.correlation}} under Fama-French's rules, {{gap.rebalance_monthly.correlation}} once re-formed monthly, {{gap.decile_spread.correlation}} on deciles and {{gap.neutralized.correlation}} neutralized. See `results/tables/value_gap_decomposition.csv`.

**What happens to Sharpe at next-day VWAP instead of the close?** Very little gross: {{timing.close.gross}} to {{timing.next_vwap_proxy.gross}}. Net of costs it goes from {{timing.close.net}} to {{timing.next_vwap_proxy.net}}.

**Would a better spread model have saved it?** No. It shrinks the loss, but the headline book stays negative on published spreads, at {{spread.table.headline.net_sharpe}} uncapped and {{spread.table.headline.capped_sharpe}} capped. Impact is the larger problem.

**What do you do with an anomaly that fails?** Report it with a reason. Tuning a definition to raise a correlation is logged with its effect in [docs/decisions.md](docs/decisions.md).

## Requirements

- [x] Point-in-time universe: top {{spec.variant}}–{{spec.universe}} US stocks by market cap, rebuilt monthly, including companies that later delisted. (FR-09)
- [x] Corporate actions handled: splits, dividends, ticker changes, and delisting returns (with an adjustment when they are missing). (FR-04, FR-05, FR-06, AC-W-05)
- [x] Fundamentals lagged to the SEC filing acceptance date, never to the fiscal period end. (FR-08, AC-W-06)
- [x] A library of {{spec.min_replicated}}+ signals across value, momentum, quality, investment and low-risk, each winsorized and cross-sectionally z-scored. (FR-14, FR-15, AC-W-07)
- [x] Neutralization to sector, size and beta, with a switch to show results before and after. (FR-16, AC-W-08)
- [x] Evaluation: rank IC and IC information ratio, quantile spread returns, Fama–MacBeth regressions with Newey–West errors, turnover and decay curves. (FR-17 to FR-20, AC-W-09)
- [x] A combination layer (equal weight, IC weighted, ridge) feeding a cost-aware long–short backtest. (FR-22, FR-23, AC-W-10, AC-W-11)
- [x] One command rebuilds every result from raw files; intermediate data is cached and versioned. (FR-29, AC-W-12)

## Success criteria

### Works (correct and reproducible)

- [x] Your momentum and size long–short returns correlate above {{spec.french_size_momentum}} with Ken French’s series; value above {{spec.french_value}} (definitions differ). (FR-21, AC-W-01)
- [x] An automated leakage test fails the build if any feature uses data timestamped after its rebalance date. (FR-12, AC-W-02)
- [x] Delisted stocks are in the universe, and a survivors-only comparison shows the size of the bias you avoided. (FR-10, AC-W-03)
- [x] The full pipeline reproduces from raw data with one command on a clean machine. (FR-29, AC-W-04)

### Impresses hirers

- [x] Replicate {{spec.min_replicated}}+ published anomalies and show post-publication decay, in line with McLean and Pontiff (2016). (FR-26, FR-27, AC-H-01, AC-H-02)
- [x] Judge significance against the t > {{spec.hurdle}} multiple-testing hurdle of Harvey, Liu and Zhu (2016), not t > {{spec.weak_hurdle}}. (FR-28, AC-H-03)
- [x] Attribute the combined signal to FF5 plus momentum and report the residual alpha honestly. (FR-25, AC-H-04)
- [x] Net-of-cost results with a capacity estimate: the AUM at which costs erase half the alpha. (FR-23, FR-24, AC-H-05, AC-H-07)
- [x] A two-page memo per signal family: hypothesis, evidence, and why it might stop working. (FR-31, AC-H-06)
