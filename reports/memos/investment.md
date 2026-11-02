# Investment

## Hypothesis

Firms that grow their assets or issue shares earn less afterwards. Either their cost of capital is low, so more projects look worthwhile, or managers issue stock and build empires when shares are dear. Four measures test it: asset growth, investment in plant and inventory, net operating assets, and net share issuance.

## Evidence

Top 3,000, 1998 to 2025, one-month rank IC. The value-weighted decile spread is gross of costs. The last column is OSAP's published series, in sample and then after publication.

| Signal | Mean IC | IC t | Neutralized IC t | VW spread t | OSAP r | OSAP mean per year, before then after |
|---|---|---|---|---|---|---|
| `asset_growth` | 0.006 | 1.1 | -1.6 | 0.8 | 0.84 | 18.1%, 5.8% |
| `inv_at` | 0.010 | 2.1 | 0.7 | 0.8 | 0.80 | 9.6%, 2.3% |
| `noa` | 0.005 | 1.0 | -1.2 | 0.5 | 0.46 | 13.1%, 0.4% |
| `nsi` | 0.034 | 5.2 | 3.1 | 2.2 | 0.73 | 8.2%, 11.3% |

Only 1 of 4 signals clears t = 3.0 on IC: net share issuance. Asset growth, investment and net operating assets are weak on their own, and the first and third turn negative after neutralizing. Their published series fell hard after publication.

Replication needed care here. Annual Compustat items reach OSAP's portfolios with a 6-month lag, so our latest-quarter signals don't match them. Using OSAP's timing lifted `asset_growth` to r 0.84 and `inv_at` to r 0.80. `noa` stays at r 0.46, since Sharadar has no minority interest or deferred charges.

Yet the family book has the highest gross Sharpe of any family, 1.15, with alpha of 9.3% (t 6.0). Combining the four at low turnover does better than most of them alone suggest. It loads on CMA at 0.32, and the factors explain little of it: R-squared is 0.10. Most of the return isn't the Fama-French investment factor.

Net of costs at $100M the Sharpe is -1.64 with no trade cap and -0.76 with one.

Rerun after the net failure on published spreads instead of Corwin-Schultz, the Sharpe is -1.17 with no cap and -0.25 with one. That's a follow-up, not a test, and the result is still below zero.

![Monthly IC](../figures/ic_investment.png)

![IC by horizon](../figures/decay_investment.png)

## Why it might stop working

- **Published decay.** Three of these four anomalies have lost most of their published return. Net share issuance is the exception so far, and exceptions have a habit of closing.
- **Buybacks changed the signal.** Issuance now mixes equity pay, buybacks and acquisitions. If the mix keeps shifting, the same number means something different.
- **Interest rates.** Low rates make growth cheap for every firm. A long period of different rates would test whether the effect is about mispricing or about the cost of capital.
- **It may be a combination artefact.** The strong family result rests on combining signals, not on any single one, so it's the result most exposed to selection across these four choices.

## Verdict

Share issuance is the real signal here. The family book is attractive gross, but the strength of the combination deserves more suspicion than a single clean effect would.
