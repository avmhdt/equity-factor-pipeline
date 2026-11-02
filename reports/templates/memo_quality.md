# Quality

## Hypothesis

Profitable, well-run firms are underpriced, because investors don't fully price the persistence of profits. Five measures test it: gross profitability, return on assets, return on equity, the Piotroski score and accruals.

## Evidence

Top {{spec.universe}}, {{spec.start}} to {{spec.end}}, one-month rank IC. The value-weighted decile spread is gross of costs. The last column is OSAP's published series, in sample and then after publication.

| Signal | Mean IC | IC t | Neutralized IC t | VW spread t | OSAP r | OSAP mean per year, before then after |
|---|---|---|---|---|---|---|
| `gp_at` | {{signal.gp_at.ic}} | {{signal.gp_at.ic_t}} | {{signal.gp_at.ic_t_neutral}} | {{signal.gp_at.vw_t}} | {{signal.gp_at.osap}} | {{signal.gp_at.before}}, {{signal.gp_at.after}} |
| `roa` | {{signal.roa.ic}} | {{signal.roa.ic_t}} | {{signal.roa.ic_t_neutral}} | {{signal.roa.vw_t}} | {{signal.roa.osap}} | {{signal.roa.before}}, {{signal.roa.after}} |
| `roe` | {{signal.roe.ic}} | {{signal.roe.ic_t}} | {{signal.roe.ic_t_neutral}} | {{signal.roe.vw_t}} | {{signal.roe.osap}} | {{signal.roe.before}}, {{signal.roe.after}} |
| `fscore` | {{signal.fscore.ic}} | {{signal.fscore.ic_t}} | {{signal.fscore.ic_t_neutral}} | {{signal.fscore.vw_t}} | {{signal.fscore.osap}} | {{signal.fscore.before}}, {{signal.fscore.after}} |
| `accruals` | {{signal.accruals.ic}} | {{signal.accruals.ic_t}} | {{signal.accruals.ic_t_neutral}} | {{signal.accruals.vw_t}} | {{signal.accruals.osap}} | {{signal.accruals.before}}, {{signal.accruals.after}} |

This is the strongest family. {{family.quality.over_hurdle}} of {{family.quality.signals}} signals clear t = {{spec.hurdle}} on IC, and they survive neutralizing: `gp_at` rises to t {{signal.gp_at.ic_t_neutral}}. Gross profitability is the one anomaly here whose published series earned clearly more after publication than before.

Accruals is the exception. Its IC is flat in this sample, and the replication fails. Sharadar has no taxes payable, so that gap is partly data. The published series has also decayed hard. `fscore` fails replication too, at r {{signal.fscore.osap}}, because OSAP's version uses different items, though its own IC is strong.

As a family, an equal-weight neutralized book has a gross Sharpe of {{family.quality.gross_sharpe}} and alpha of {{family.quality.gross_alpha}} (t {{family.quality.gross_alpha_t}}), and it loads on RMW at {{family.quality.gross_rmw}}, as it should. It replaces only {{family.quality.turnover}} of its book each month. Net of costs at {{spec.aum}} its Sharpe is still {{family.quality.net_sharpe}} with no trade cap and {{family.quality.capped_sharpe}} with one, the best of the families but below zero.

![Monthly IC](../figures/ic_quality.png)

![IC by horizon](../figures/decay_quality.png)

## Why it might stop working

- **It's a popular trade.** Quality has become a product, and flows into it raise prices today at the cost of returns tomorrow. Paying up for quality can look like skill until the multiple falls.
- **Profits mean-revert under competition.** Firms with high margins attract rivals. The signal depends on persistence that could shorten.
- **Accounting changes.** Gross profit and ROA depend on what's capitalized and what's expensed. A rule change could break comparisons over time.
- **Junk rallies.** In sharp recoveries the shorts rebound hardest, so the long-short book loses when risk appetite returns.

## Verdict

This is the family most worth keeping. The effect is strong, it survives neutralizing, and it's the cheapest of the strong families to hold. It's the first place to look for a tradable book, ideally with slower rebalancing and liquid names only.
