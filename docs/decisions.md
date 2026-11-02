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
