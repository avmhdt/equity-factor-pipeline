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

## Known limits on the free path

- Companies that trade today only, so it cannot measure survivorship bias. It builds no `delistings` or `corp_actions` rows.
- Security master rows carry today's ticker and SIC code.
