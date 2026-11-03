# Decisions

Owner decisions copied from `DECISIONS.md` on 2026-11-01, followed by choices made while building. `DECISIONS.md` overrides the spec, and the spec overrides `README.md`. API keys never go in this file.

## Owner decisions

### Plan and merging

- **Scope:** project 1 of 15, for researcher and developer job applications, built a few at a time with no fixed date.
- **Merging:** agents push feature branches and open PRs. PRs merge automatically once CI is green. Branch protection and auto-merge are turned on once CI exists (M1).

### Identity and access

- **GitHub:** `gh` active account is `avmhdt`, with admin on the repo. Actions is enabled.
- **SEC:** `SEC_USER_AGENT` is set in `.env` to the owner's handle and contact email.
- **WRDS:** no access. CRSP, Compustat, OptionMetrics and Capital IQ paths stay off, so history starts in 1998 and the pre-1998 tests drop out.

### Data

- **D-01 Sharadar:** Full History Bundle bought directly from Sharadar, not through Nasdaq Data Link.
  - Base URL `https://api.sharadar.com/v1.0`, key in `SHARADAR_API_KEY`, sent in the `x-api-key` header so it stays out of URLs and logs.
  - Tables `stocks` (SEP), `fundamentals` (SF1), `tickers`, `actions`. Bulk zips with `years=full`.
  - `actions` has no delisting-return field, so the adjustment for missing delisting returns stays (DQ-08).
- **Licence (P-10):** Personal Use License. Sharadar replied in writing on 2026-10-31 that public code, aggregate results, job applications, aggregate cross-checks and hosted coding tools are allowed. Publishing stays aggregate-only, with no security-level data (D-13).
- **Stooq (P-12):** the snapshot at `E:\trading-strategy-brainstorm\data\stooq` is dev only and labelled so.

### Money

The Sharadar subscription is already held. Nothing else is approved for this project (USD 0).

### Machine

- **D-12 Development OS:** native Windows with `uv` and Python 3.12. CI covers ubuntu-latest and windows-latest. WSL2 Ubuntu is available but not used here.
- **Storage:** stay on `D:`. Free space is checked at each milestone. Project data never goes on `C:` or into git.

### Accepted

Spec defaults stand for D-03, D-05, D-06 except borrow, D-08, D-10, D-13 and D-15.

### Changed from the spec default

| Decision | Was | Now |
|---|---|---|
| D-06 borrow cost | Flat 25 bps/yr | 0.25%/yr top 500, 1.0%/yr ranks 501–1,000, 5.0%/yr otherwise, shared with `stat-arb-pairs` |
| D-06 spread model | Corwin-Schultz alone | Abdi-Ranaldo and the size-bucket table reported beside it, disclosed as run after the net failure. Corwin-Schultz stays the headline |
| D-06 market impact | Coefficient 1.0 alone | Checked against published evidence, disclosed as the second round after the net failure. Coefficient 1.0 stays in the table and the headline |

## Reference hardware (NFR targets)

Recorded 2026-11-01. Intel Core i9-14900K (24 cores, 32 threads), 128 GB RAM (137,142,276,096 bytes reported), Windows 11 Pro. `D:` is an internal 2 TB SATA HDD with 965 GB free at M1. Live recorders run on this PC, so downloads and heavy runs are kept modest.

## Choices made while building

| Milestone | Topic | Choice | Why |
|---|---|---|---|
| M1 | EDGAR rate limit | The token bucket holds one token | A larger bucket lets a burst plus the refill exceed `SEC_MAX_RPS` inside a one-second window, which NFR-10 forbids |
| M1 | Sharadar key check | `efp doctor --network` queries ticker `GME`, not `AAPL` | On 2026-11-02 the API answered AAPL and MSFT with no key or a false key, and refused other tickers with HTTP 403 `Exceeds free tier`. A one-row AAPL query proves nothing about the key |
| M1 | Lockfile in CI | `uv sync --locked` rather than `--frozen` | It also fails when `uv.lock` is stale |
| M1 | `eqpit` extras | `eqpit[wrds]` and `eqpit[free]` declared, unused until a provider needs them | Keeps the lock stable, as section 6 lists them |
| M2 | D-11 extra lag | 1 trading day for headline; 0 is the sensitivity | Spec default |
| M2 | `acceptanceDateTime` | Parsed as true UTC | Checked against the EDGAR filing index on 2026-11-01 for Apple filings from 2009, 2012, 2015 and 2026: the `Z` value converts to the ET time the index shows. A 2009 filing accepted at 21:45 ET also carries the next day's filing date, as the 17:30 rule predicts |
| M2 | `split_factor` | Cumulative split ratio up to and including each date, not "to latest" | "To latest" would make every old row depend on later splits, the hidden leak R-05 warns about. `PitStore.prices` returns `adj_close`, rebased to the as-of date, which gives the same price levels |
| M2 | Stale flag | Set from the fifth zero-volume day on, not back-filled over the run | Marking day one of a run needs days after it |
| M2 | `forward_returns.fwd_ret` | Cumulative from the as-of close to the month end `h` months ahead | The spec wording could also mean the single month; DQ-13's purge of `h` months fits a cumulative label |
| M2 | Delisted names in `forward_returns` | Proceeds sit flat at zero after delisting | Dropping them would bring back the survivorship bias at longer horizons |
| M2 | Delisting on a month-end session | The delisting return falls in the next month | That session's close is still a valid mark, and the name is in that month's universe |
| M2 | `universe_monthly` columns | Added `company_id` and `available_at` to the spec's list | DQ-01 says every row of every PIT table carries `available_at`; signals need `company_id` to reach fundamentals |
| M2 | `PitStore.fundamentals(ids=...)` | `ids` are `company_id` values | Fundamentals belong to issuers |
| M2 | `PitStore.for_signals(asof)` | `compute_signals` hands signals a view that raises on `labels()` and on any later date | Makes "never give signal functions access to LabelStore" a runtime rule, not a convention |
| M2 | `DataProvider` protocol | Added `risk_free()`, `snapshot_ids()` and `pin()` | Forward excess returns and manifests need them |
| M2 | `book_equity` | Derived per filing as equity + deferred taxes - preferred | The spec lists it as a canonical item; XBRL has no such tag |
| M2 | Fiscal period labels | Read from the fact's own filing, stepping back from its `fp` for comparatives | A company-wide fiscal calendar would let later filings change earlier labels |
| M2 | Bit reproducibility | Float aggregations use `group_by(..., maintain_order=True)` and `PitStore` returns one chunk | Measured on 2026-11-01 with Polars 2.1.0: without `maintain_order`, a `group_by` float sum on the same frame differs in its last bits from run to run, which breaks NFR-03 and the bit-identical leakage test. The leakage test now rejects a bare `group_by` in `efp.signals` |
| M2 | Stage cache | A stage's code hash also covers the packages it names | A stage function alone would not notice a change inside `eqpit` |
| M2 | Where free and synthetic tables go | `data/processed/tables/<run>/`, never `results/tables/` | Only licensed runs write the committed directory (DQ-16) |
| M2 | `eqpit` dependencies | `numpy` declared, beyond NFR-12's list | The synthetic generator needs it, and it already comes with `exchange-calendars` |
| M2 | `configs/xbrl_map.yaml` | `eqpit.edgar.xbrl_map.DEFAULT_XBRL_MAP` holds the same map, and a test keeps them equal | `eqpit` has no YAML parser under NFR-12 |
| M2 | SIC to FF industries | French's `Siccodes12.txt` and `Siccodes49.txt` ship inside `eqpit` | CI has no network. Attribution is in `eqpit/industries.py` |
| M2 | Synthetic signal definitions | `gp_at` = TTM (revenue - cogs) / latest assets; `asset_growth` = latest assets / assets four quarters back - 1; `mom_12_1` = log return over months m-11 to m-1 at the end of month m | M4's signals must match these to recover the planted IC. Spec tables use `t` both for the holding month (UMD) and the formation month (`str_1m`); `mom_12_1` follows French's convention |
| M2 | Planted IC | Each month's loadings solve `R b = rho` on the realised score correlation | Persistent fundamentals feed into past returns, so `mom_12_1` overlaps the others; fixed loadings would inflate all three |
| M2 | `edgar_free` download | 150 companies per CIK (701 requests, 3.5 minutes, 680 MB) instead of the bulk zips | Live recorders share this PC, so downloads stay modest. Bulk stays the route when `max_companies` is unset, tested with a mock |
| M2 | `edgar_free` prices | From 2009 on; the snapshot keeps everything | Fundamentals start there. Stooq has five rows on non-sessions before that (1978-03-24, 1985-09-27), which DQ-17 caught |
| M2 | `edgar_free` market cap | Stooq adjusted close times as-reported shares | Stooq does not publish unadjusted prices or splits, so old market caps run low after later splits. Dev path only; it is one more reason these tables stay labelled |
| M2 | Stooq adjustment | Checked at import against Apple's 2020 split, which fails the pull if the file is unadjusted | P-12 says to verify, not assume |
| M3 | D-04 sample period | `stocks` starts 1997-12-31 and ends 2026-10-30; `fundamentals` holds filings from 1993, thin before 1998. The universe is usable from early 1998, once first filings make market caps visible. French replication overlap: SMB, HML, RMW and CMA 1998-07 to 2026-09, UMD 1999-01 to 2026-09 | Recorded from the bundle pulled on 2026-11-02 |
| M3 | Bulk downloads | 10 MB/s cap (`eqpit/throttle.py`), one table at a time. Measured 9.93 MB/s | Owner's rule: live recorders share the connection |
| M3 | Signed download links | `years=full` redirects to a time-limited link, followed by hand with a second client that has no key header, and never logged | httpx would otherwise send `x-api-key` to the storage host |
| M3 | `dividend` values | Restated with `closeunadj / close` before use | They are split-adjusted: Apple's May 2020 dividend reads 0.205, while 0.82 was paid against a 303 unadjusted close. Used directly, total returns on any stock that later split would be too low |
| M3 | `spinoffdividend` | Taken as the value per parent share, not multiplied by `spinoff` | Alone it matches `closeadj` (median gap 0.0014%); times the ratio it misses by 8% |
| M3 | `sharesbas` | Treated as restated to the latest split basis: market cap is `close * sharesbas * sharefactor` | Apple shows 17.1B shares in July 2020, when 4.3B were outstanding. `closeunadj * sharesbas` would be 4x too high on that date |
| M3 | `ret` | `(closeunadj * ratio + cash) / previous - 1`, with the vendor's `closeadj` return on split and spinoff days | Over 37M rows the mean gap to `closeadj` is 0.0025%, against 0.08% from split-adjusted closes, which carry rounding noise. About 500 rows disagree by over 5%, mostly spinoff days and days with a split and a dividend together, where the vendor's own return is the better one. On those days `closeadj` is the source |
| M3 | Shares before the first filing | Null, so no market cap and no universe entry | Using a later filing would leak. An IPO joins at the close after its first filing |
| M3 | Secondary share classes | No market cap | SF1 covers the primary class only, and `sharesbas` with `sharefactor` gives the company total, so the sum over classes in DQ-07 comes from the primary row |
| M3 | SCD2 history | SIC, venue and ticker windows come from `sicchangefrom`, `exchangefrom` and `tickerchangefrom` | Closes R-05's current-SIC risk. Today's value applies only after the last change. `share_type` uses today's SIC to find REITs |
| M3 | Company key | `CIK:<cik>` from `secfilings`, else `SHR:<permaticker>` | 105 master rows across `SEP` and `SF1` have no CIK |
| M3 | Share types | Domestic Common Stock classes map to `common`; SIC 6798 to `reit`; ADR commons to `adr`; Canadian and preferred to `other` | CRSP share codes 10 and 11 mean US-incorporated commons |
| M3 | Action types | 73 vendor values, each mapped or listed as ignored. `acquisition*` rows beyond `acquisitionby` give consideration terms. A new value fails the build | Spec section 7b |
| M3 | Delisting reasons | Bankruptcy is `liquidation`; regulatory and voluntary are `performance`, as CRSP codes 57x are; acquisitions and mergers are `merger`; no reason given is `unknown` | Result: 7,509 merger, 3,331 liquidation, 1,433 performance, 2,433 unknown |
| M3 | Delisting returns | All imputed per DQ-08: `delisted.value` is a market cap in millions, not a return | Lehman's 55.6 confirms it. `exchange_at_delist` is the last primary venue, since a name that moved to OTC first still imputes by its listing venue |
| M3 | Link to EDGAR | Same CIK and filing date, with period ends within 5 days (DQ-04). 87.6% of fundamentals rows link | The rest use filing date + 1 trading day, which is no earlier than the acceptance rule |
| M3 | SEC bulk file | The 1.57 GB `submissions.zip` is reduced to 719,548 filing rows; the zip is not kept | Only the extract is used. Re-pulling costs one request |
| M3 | Sharadar book equity | `equity` alone | The vendor has no deferred taxes or preferred stock. HML still reaches 0.987, so no substitute was tried |
| M3 | SF1 rows filed on or before the period end | Dropped from `filings` | 27 ARQ rows. DQ-03 forbids them |
| M3 | Extreme prices | Flagged, not removed (DQ-10): 7,604 rows, including bad ticks where a return reaches 999x | The spec forbids silent deletion. M4 measured the effect on labels and found it negligible |
| M3 | Low-risk family in the survivorship table | A 2x3 sort on trailing 36-month return volatility | AC-W-03 needs every family, and the signal library arrives in M4. The other families use the FF-convention factors. Rerun with library signals at M4 |
| M3 | Trial log timing | Wired in at M3 rather than M4 | The first replication is already a trial. 20 rows so far: 6 factors, plus 14 for both survivorship modes |
| M3 | Trial `git_sha` | The first 20 rows point at `6e55571`, a commit on the M3 branch | Squash merges drop it from `main`, but it stays reachable through `refs/pull/3/head` |
| M3 | Run directories | M3's 20 rows have `metrics.json` only. From M4 each run also has `config.yaml` and `manifest.json` | Rows are never rewritten |
| M4 | Signal months | Month 0 ends at the rebalance date. `mom_12_1` is months 11 to 1, `mom_6_1` 5 to 1, `ltr_36_13` 35 to 12, `ind_mom` 5 to 0, `str_1m` month 0 | Spec uses `t` for the holding month in some rows and the latest month in others. These match UMD's convention, and `str_1m` as the month just ended |
| M4 | `fwd_ret` and rebalance dates | `prices_monthly` and `market_daily` added as gated PIT tables | Signals need monthly returns and a market return without rescanning daily prices. Neither carries anything later than its own close |
| M4 | Market return | Cap-weighted over the prior month end's top 3,000, by prior-day market cap | FR-16's "own cap-weighted universe return" |
| M4 | Read speed | `PitStore` shares eager tables with its signal views, and a view remembers reads for one date. Views see fundamentals from the last 6 years | 26 signals over 345 months took 20 minutes at 8 threads. Every read still filters on `available_at` |
| M4 | Staleness | A fundamental older than 550 days at the rebalance date is treated as missing | A company that stopped filing should not carry an old value forever |
| M4 | TTM | Four quarters spanning 255 to 295 days, all public at the cutoff | DQ-06 |
| M4 | Winsorize bounds | Sample values (nearest rank, outward), not interpolated | Hypothesis found interpolation is not idempotent: `[0,0,0,0,1]` shrinks again on a second pass. The spec requires idempotence |
| M4 | Fama-MacBeth variance | `linearmodels` is the source. The manual cross-check uses T-1 to match it | Without that they differ by exactly sqrt(T/(T-1)) |
| M4 | Discrete scores | Spreads run from the lowest to the highest filled quantile | `fscore` takes 10 values, so deciles can be empty. Dates were being skipped, which removed the signal from the table |
| M4 | Extreme prices in labels | No special handling | Measured in the top 3,000: 6 of 1.03M stock-months exceed +500%, the largest is 16x in January 2021, and clipping at +500% moves the equal-weight mean by 0.02% a year. The bad ticks sit below the $1 floor |
| M4 | `inv_at` | Net PP&E stands in for gross on Sharadar | The vendor has no gross PP&E |
| M4 | `accruals` | Taxes payable counted as unchanged on Sharadar | The vendor has no such item |
| M4 | `fscore` | Uses the last two fiscal years; a missing comparison scores 0 | Piotroski's definition is annual |
| M4 | `sue` | Needs 6 of the last 8 year-over-year EPS changes | EPS is as reported, so a split inside the window adds noise |
| M4 | Trials | One row per long-short spread: 26 signals, 2 neutralization modes, 2 universes, 2 weightings. Each is logged in its family | Every spread appears in a table a human reads. 212 rows here, 232 in total with M3's 20. `fscore`'s 4 neutralized spreads appear twice: they ran first, then again in the rerun that fixed its raw spreads |
| M4 | `efp evaluate --signals` | Replaces those signals' rows in the tables and leaves the rest | Re-evaluating one signal should not re-log the others |
| M5 | Each OSAP portfolio | Built by that row's SignalDoc rules: side, weighting, quantile, NYSE breakpoints, holding period, start month, price floor, exchanges. A blank quantile means quintiles | FR-26 asks for OSAP's convention, and these are per signal |
| M5 | OSAP universe | Every eligible name, from `efp signals compute --all-eligible`, and no neutralization | OSAP's portfolios are not limited to the top 3,000 and are not neutralized. Effect on correlation: `size` .514 to .873, `ltr_36_13` .743 to .933, `str_1m` .835 to .942, `mom_12_1` .793 to .921, `sp` .923 to .765, `nsi` .865 to .734 |
| M5 | OSAP screens | `EP` and `EntMult` keep positive values, `GP` leaves out financials | OSAP's signal definitions. Effect: `ep` -.042 to .790, `ebitda_ev` .859 with the next row, `gp_at` .424 to .718 |
| M5 | `EntMult` direction | Published series compared unflipped | `EntMult` is EV over EBITDA and `ebitda_ev` is its inverse, so the sign rule would flip a series that already points the same way. Effect: -.595 to .859 |
| M5 | Annual timing for five accounting signals | `bm`, `asset_growth`, `noa`, `inv_at` and `accruals` use the newest fiscal year at least 6 months old and under 18, at each OSAP formation date | OSAP's lag on annual Compustat. Replication portfolios only: the signal library still uses the latest quarter. Effect: `bm` .624 to .907, `asset_growth` .459 to .844, `inv_at` .639 to .804, `noa` .246 to .463, `accruals` .268 to .392. Taken from OSAP's documented convention, not searched for, and no other definition was changed after seeing these |
| M5 | June-only scores | Rejected after one run | `noa` and `inv_at` are monthly in OSAP, so scores that exist only in June held for 28 months in total. `inv_at` printed .922 on those 28 months, which was not a result. A data test now requires 240 months behind a pass |
| M5 | AC-H-01 | 22 of 26 pass over 301 to 334 months. `accruals` .392, `fscore` .225, `noa` .463 and `sue` .689 fail with same-sign means | Causes are listed in the table. `noa` needs minority interest and deferred charges, `accruals` needs taxes payable; Sharadar has none of them. Stopped here rather than tune further |
| M5 | Post-publication sample | Reported on both Sharadar and OSAP's published series. The per-signal table and figure use OSAP's | Sharadar starts in 1998, so only 9 signals have 36 in-sample months with a positive mean, and its pooled result (+22% after publication, t 0.1) says nothing either way. OSAP's series over 26 signals: -30% post-sample (t -1.0), -39% post-publication (t -2.1), against the paper's -26% and -58% |
| M5 | Value gap | The largest single steps are monthly re-forming (correlation with HML .954 to .824), the decile construction (.807 to .655) and neutralization (.655 to .294) | Mean return goes negative at the decile step: the research value factor is not HML, and the headline should not imply it is |
| M5 | Multiple testing | Over all logged trials, repeats included: 298 trials, 78 over t=2, 23 over t=3, 8 under 5% BHY | Repeats raise the count, which only makes the BHY test stricter. Composites join at M6 |
| M5 | Dirty flag | Tracked changes under `results/` no longer mark a run dirty | A run's own table changes would otherwise dirty the next run. `signals compute --all-eligible` also stopped overwriting the headline drops table, which is what dirtied the second OSAP run |
| M5 | OSAP rows in the trial log | 26 at top 3,000, 26 all-eligible and dirty, then 5, 5 and 4 reruns. The five rows at `a15fb2a` hold the June-only series, so `noa` and `inv_at` warn on short history | Rows are never rewritten |
| M5 | Grouped float sums | Values are sorted before a grouped `sum`, in signals and in the PIT build, and the static leakage check rejects a bare `sum` in signal code | CI's bit-identical perturbation test failed on one ubuntu run: 80 values on one date moved by 6e-16. It reproduced on Linux in 2 of 25 runs. Row order inside groups never changed, yet a plain grouped `sum` or `mean` differed about once in 1,000 evaluations, even on one thread, and `sort().sum()` never did in 8,000. Polars appears to sample each frame to choose a group-by path, adding the same numbers in a different grouping on the partitioned one. This was float noise, not a leak, and the tolerance stays at zero |
| M6 | Headline composite | Equal weight of neutralized scores on the top 3,000, chosen before the first backtest ran | No fitted parameters and the full sample. Ridge starts 120 months later. All six method-and-mode pairs are in `backtest_summary` |
| M6 | Headline fund size | $100M | D-05 and D-06 fix the price and costs but no size. It is a point on the FR-24 grid |
| M6 | A name leaving the book | Pays the spread and impact to sell | The first draft looked up costs only for that month's targets, so exits were free. Fixed before any result, with a test |
| M6 | ADV cap | `backtest_summary` carries both the uncapped book and one capped at 10% of ADV | FR-23 sets no cap and FR-24 sets 10%. Uncapped, the 99th-percentile position is 3.7x ADV at $100M, which overstates impact. Capped, trades go unfilled and the book stops being dollar neutral: net exposure reaches 0.38 of NAV. Neither figure is the clean one, so both are reported |
| M6 | Cost sensitivity | `cost_sensitivity` scales spread, impact and borrow together, x0.5 and x2, as D-06 asks. `capacity` scales impact alone, as AC-H-05 asks | |
| M6 | Corwin-Schultz | Kept, per D-06 | It reads 16 to 25 bps half-spread on the top 500 names, where real spreads are nearer 1 bp. It is a known weakness of the estimator and much of the net result rests on it. D-06's alternatives are Abdi-Ranaldo and a size-bucket table; neither was run |
| M6 | Capacity | `below grid min`, on the top 3,000 and top 1,000 at every impact multiplier | FR-24 names only "> grid max". Spread and borrow alone take about 15% a year at $10M, more than half the 14.3% zero-cost alpha, so no fund size meets the test. Past about $100M the cap freezes the book, so net alpha rises again; that is the cap, not capacity |
| M6 | Top 1,000 | Reported as a labelled sensitivity, added after seeing the top 3,000 result | D-03 keeps the top 3,000 as the headline, and `universe.variants` already lists it. Its trials are logged |
| M6 | Neutralizing `ind_mom` | Left out of neutralized tables and composites | An M4 defect. It is one value per industry, so FF-49 dummies span it and the residuals were rounding error re-standardised to unit variance. M4's neutralized `ind_mom` rows, including t -2.3 at the top 1,000, were noise. Dates where residuals fall under 1e-8 of the input's deviation now give nulls, so AC-W-08's "both modes" holds for 25 of 26 signals. Taking the noise out of the composite moved gross alpha from 13.0% to 14.3%. The old rows stay in the trial log |
| M6 | `efp rebuild` shape | Raw signals run together; panels run per signal; evaluation runs per family | A month loop shares reads across signals, so splitting raw signals would cost hours. Fama-MacBeth by family needs its members together. A panel parameter (`signals.overrides.<id>.winsorize`) re-runs that panel, its family, and everything after |
| M6 | Rebuild time | 72 minutes from raw at 8 threads, under NFR-01's 2 hours | OSAP's annual timing is the slowest stage at 24 minutes. `pipeline_timings` is the one table that differs between runs |
| M6 | Trials in a rebuild | A rebuild adds nothing for a trial the log holds with the same parameters and result. Direct commands still append | A repeat of the same numbers is not a new test, and otherwise rebuilding would change `multiple_testing`. Direct commands follow FR-30 as written |
| M6 | Rows in the trial log | 485 rows. M6 added 187: 70 from a first rebuild at `a9d0cb8`, whose composites held the `ind_mom` noise, then 117 from the second at `9c351f9` | Rows are never rewritten |
| M6 | Reading `multiple_testing` | `t_over_3` is two-sided. Most composite rows clear it because their net returns are significantly negative | The README claims significance only for positive t |
| M6 | Returns not in the backtest | No interest on short proceeds or cash | The series are excess returns on a self-financing book |
| M6 | Low-risk survivorship row | Stays a 2x3 sort on trailing volatility | The M3 note planned a rerun with library signals. It would need the signals built survivors-only, and the sort already answers AC-W-03 |
| M7 | AC-W-04 | Passed in a clean directory on 2026-11-02: a fresh clone of `5664102`, with only `data/raw` and `data/external` copied in, and no `.env`, cache or return files | 48 of 48 stages ran in 74.0 minutes at 8 threads. `git status` afterwards showed only `pipeline_timings` changed: every table, figure, memo, the README and `readme_numbers.json` came out byte-identical, and the trial log gained no rows. It ran on the same machine, so it doesn't test another OS |
| M7 | Documents | The README and memos render from `reports/templates`, with every number a named placeholder. An unknown name is an error | FR-31 and AC-H-11. Tests check that each document equals its rendered template and that every number in the README is in `readme_numbers.json` |
| M7 | Fixed numbers | Thresholds and settings the documents state are a named list in `efp/reports/numbers.py`, with a few diagnostics from M6 | They're inputs or one-off measurements, so no table holds them. The diagnostics are the Corwin-Schultz range, the 3.7x ADV position and the 0.38 exposure |
| M7 | Trial t | Each trial's t is in its `metrics.json`, backfilled for the 485 existing runs. `trial_log.csv` is untouched | Return series stay out of git under D-13, so a clone would otherwise score every trial p = 1 and `multiple_testing` wouldn't reproduce |
| M7 | Figure inputs | Series figures need stay in `data/processed/report`, outside git | D-13 allows figures, not series |
| M7 | `efp report` | Runs at the end of every rebuild and alone. A run skips what its tables don't support, and only licensed runs write the README and memos | Synthetic runs must not overwrite documents |
| M7 | Memo wording | Claims were checked against the tables before commit. Five overreached and were corrected | The numbers are generated, but the sentences around them aren't, so a wrong claim can survive a right number. Re-read the memos if a rebuild moves a table |
| M7 | NFR-07 review | A test checks that no table has a security column, and that no return series or `data/` path is tracked | The spec asks for a checklist. A test stays true |
| M7 | AC-H-09 | `scripts/run_reuse_example.py` runs the `docs/reuse.md` example with `efp`, `statsmodels`, `scikit-learn` and `linearmodels` blocked. A CI job installs `eqpit` alone on Ubuntu and Windows and runs it | |
| M7 | D-08 and D-10 | Defaults stand: git install only, no JKP comparison | FR-33 and FR-34 are Could. D-10 says to revisit only after M6's exit criteria pass; the owner hasn't changed it |
| M7 | Coverage | 94% for `efp`, 99% for `eqpit`, against 85% | |
| M7 | AC-W-12 and NFR-01 | A no-op headline rebuild took 8 seconds with 48 of 48 stages cached, 0.2% of the 74-minute run and well under NFR-01's 2 minutes. It left a clean tree and added no trials | The cache test runs a build of about 20 seconds, where start-up and report time dwarf 5%, so it allows a fixed 10 seconds. It failed once on CI at 2.1 seconds against a 2.04-second limit. The ratio is measured here instead |
| D-06 rerun | Why it ran | Owner's decision on 2026-11-02, after the Corwin-Schultz net failure. Run beside Corwin-Schultz, which stays the headline. Thresholds didn't change and nothing was tuned | The reading rule was committed before any result: a model's net result counts only as far as its estimates match published spreads for the same stocks |
| D-06 rerun | Size-bucket values | The spec names the option but gives none. Up to $5B: SEC staff 2013, Table 4 mean relative effective spread weighted by Table 1 ticker counts, halved. Above $5B: Hagstromer's 1.47 bps, S&P 500, late 2015 | Sources were read and the table fixed before it produced any result. Mean over median, because a cost is an expectation. The 1.47 is on the half-spread convention: that paper scales quoted spreads by twice the midpoint "for comparability" |
| D-06 rerun | Abdi-Ranaldo form | The two-day corrected version from the paper: negative two-day squares go to zero, then roots are averaged. 20 days, as for Corwin-Schultz | Eq. 11 of the paper. It reports this and the monthly form as the two better treatments of negatives |
| D-06 rerun | The check | Corwin-Schultz reads 18.5x and Abdi-Ranaldo 21x published spreads on late-2015 large caps, 7.9x and 8.6x at $2-5B, and both read low under $100M | Abdi-Ranaldo isn't an improvement here. The size table's match is by construction, so it isn't independent evidence |
| D-06 rerun | Result | Published spreads cut the headline's spread cost from 11.1% to 2.5% a year, and it still loses money: Sharpe -1.18 uncapped, -0.19 capped. The top 1,000 on the size table is about zero: -0.05 and 0.07 | Impact is 32% a year on the headline under every model. The top 1,000 cell is the best of 3 models across 8 books, chosen after the fact, and it's negative from 2010. It is the one cell with a capacity estimate, about $20M |
| D-06 rerun | 2010 cut | Results are also reported from 2010 | Both size-table sources describe the 2010s, so the full sample understates early spreads |
| D-06 rerun | Trials | 32 rows, marked in `x_note`. Corwin-Schultz rows weren't repeated | Every run counts toward N, including ones made because a result disappointed |
| D-06 rerun | Next assumption | Impact wasn't tested | It's the largest cost in the headline book under every model. D-06 keeps the coefficient, and no published check was made. This rerun doesn't support a claim about what a better execution model would earn |
| D-06 rerun | AC-W-04 again | Passed on a fresh clone of `af7d7a8`: 49 of 49 stages in 81.3 minutes, only `pipeline_timings` changed, no new trial rows | The `spread_models` stage adds about 7 minutes. Same machine as before |
| Impact round | Why it ran | Owner's request on 2026-11-02, after the net failure. It's the second such round, after spreads. Coefficient 1.0 stays in the table and the headline doesn't change. Thresholds don't change | Set out here before any result, as with the spread round |
| Impact round | Rule 1: basis | Every law is compared as average execution cost per dollar traded, against the arrival price. A published value goes through a stated conversion to that basis, or it isn't used | Studies report different things. Peak impact isn't cost. Tóth and Zarinelli measure the peak |
| Impact round | Rule 2: sources | Values come from the paper's own text, complete enough to compute a cost. Every qualifying law is reported, whichever way it moves the result. Nothing is fitted to this project's data | Tóth et al. 2011 (Y of order unity, converted at 2/3). Almgren et al. 2005 (gamma 0.314, eta 0.142, exponent 3/5). Zarinelli et al. 2015, Table 4 (Y 0.19, 0.15, 0.12 and delta 0.51, 0.46, 0.42). Frazzini et al. is check-only: its table has no intercept and its measure already includes spread |
| Impact round | Rule 3: units | Zarinelli's vol is `(high - low) / open`, about 1.6x a return deviation, so that measure is computed here rather than applying its Y to the wrong vol. Its size classes have no published thresholds, so the D-06 ranks of 500 and 1,000 stand in | Applying Y to the return deviation would flatter the result |
| Impact round | Rule 4: range | The sources describe single orders, and none reaches a trade above a day's volume. Only the 10% ADV capped book is inside their range. Uncapped figures are shown, but marked as outside it | Almgren says its model works for orders of a few percent of ADV in large caps, so a book with its median name near rank 2,000 is already outside its sample |
| Impact round | Rule 5: pairing | Each law runs with Corwin-Schultz and with the size table | Corwin-Schultz failed its check. Running only with it would test impact against a known overstatement |
| Impact round | Rule 6: what would count | A net result counts as positive only if the capped book has FF5 + UMD alpha above t = 3.0, and is positive from 2010, under both fitted laws (Almgren and Zarinelli) with the size table. Anything short of that is reported as not established. Even a pass is a hypothesis to test on new data, because it comes from a second round after a failure | Set high on purpose: this is the spec's own hurdle, and more looks at the same data can only weaken what a result means |
| Impact round | Rule outcome | Not met, for the top 3,000 and the top 1,000. Capped alpha on the size table is 4.4% (t 2.4) and 5.2% (t 2.9) for the top 3,000, and 6.0% (t 2.9) and 6.2% (t 3.0) for the top 1,000. All four are positive from 2010 | Almgren misses t = 3.0 in both books, so the rule fails on it. The README's wording comes from `impact.rule.*`, computed from the table |
| Impact round | What it found | Coefficient 1.0 is 2.7x to 6.5x published estimates at 1% of daily volume. On Corwin-Schultz the capped book stays negative under every law. On the size table it's positive under both fitted laws | Costs at D-06 values were too high to judge the book. A net edge is not established, and neither is its absence |
| Impact round | Almgren uncapped | Not meaningful: its linear term gives -22,472% a year on trades of many days' volume | Rule 4 set uncapped figures aside before this was seen |
| Impact round | Trials | 96 rows, marked in `x_note`. Coefficient 1.0 rows weren't repeated | 3 laws, 2 spread models, 8 books, capped and uncapped |
| Impact round | Stop here | No third round on this data | Each look weakens what a result can mean. New data or measured execution costs would settle it |
| Impact round | AC-W-04 again | Passed on a fresh clone of `dd1daa8` on 2026-11-03, run after the reboot: 50 of 50 stages, only `pipeline_timings` changed, and the trial log gained no rows | It took 209.3 minutes at 8 threads with the recorders on the same HDD, against 81.3 on a quieter disk. NFR-01's 2 hours holds on a quiet disk, not under that load. It ran on the same machine, so it doesn't test another OS |

## Known limits on the free path

- Companies that trade today only, so it cannot measure survivorship bias. It builds no `delistings` or `corp_actions` rows.
- Security master rows carry today's ticker and SIC code.
