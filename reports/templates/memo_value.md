# Value

## Hypothesis

Cheap stocks earn more than expensive ones, either as pay for distress risk or because investors extrapolate bad news too far. Five measures test it: book-to-market, earnings yield, sales yield, cash-flow yield and EBITDA over enterprise value.

## Evidence

Top {{spec.universe}}, {{spec.start}} to {{spec.end}}, one-month rank IC. The value-weighted decile spread is gross of costs. The last column is OSAP's published series, in sample and then after publication.

| Signal | Mean IC | IC t | Neutralized IC t | VW spread t | OSAP r | OSAP mean per year, before then after |
|---|---|---|---|---|---|---|
| `bm` | {{signal.bm.ic}} | {{signal.bm.ic_t}} | {{signal.bm.ic_t_neutral}} | {{signal.bm.vw_t}} | {{signal.bm.osap}} | {{signal.bm.before}}, {{signal.bm.after}} |
| `ep` | {{signal.ep.ic}} | {{signal.ep.ic_t}} | {{signal.ep.ic_t_neutral}} | {{signal.ep.vw_t}} | {{signal.ep.osap}} | {{signal.ep.before}}, {{signal.ep.after}} |
| `sp` | {{signal.sp.ic}} | {{signal.sp.ic_t}} | {{signal.sp.ic_t_neutral}} | {{signal.sp.vw_t}} | {{signal.sp.osap}} | {{signal.sp.before}}, {{signal.sp.after}} |
| `cfp` | {{signal.cfp.ic}} | {{signal.cfp.ic_t}} | {{signal.cfp.ic_t_neutral}} | {{signal.cfp.vw_t}} | {{signal.cfp.osap}} | {{signal.cfp.before}}, {{signal.cfp.after}} |
| `ebitda_ev` | {{signal.ebitda_ev.ic}} | {{signal.ebitda_ev.ic_t}} | {{signal.ebitda_ev.ic_t_neutral}} | {{signal.ebitda_ev.vw_t}} | {{signal.ebitda_ev.osap}} | {{signal.ebitda_ev.before}}, {{signal.ebitda_ev.after}} |

{{family.value.over_hurdle}} of {{family.value.signals}} signals clear t = {{spec.hurdle}} on IC, and none does on the value-weighted spread. The IC gives every stock equal say; the spread gives the largest ones most of it. So the effect lives mostly outside the largest names, and classic book-to-market is the weakest of the five.

Measures built on earnings and cash flow beat book value here, which fits the idea that book equity has lost touch with what firms own.

As a family, an equal-weight neutralized book has a gross Sharpe of {{family.value.gross_sharpe}} and FF5 + UMD alpha of {{family.value.gross_alpha}} (t {{family.value.gross_alpha_t}}). Net of costs at {{spec.aum}} the Sharpe is {{family.value.net_sharpe}} with no trade cap and {{family.value.capped_sharpe}} with one.

Our value factor is not HML. Its correlation with HML falls from {{gap.hml_ff_convention.correlation}} under Fama-French's rules to {{gap.decile_spread.correlation}} on our decile construction and {{gap.neutralized.correlation}} once neutralized. Neutralization is the largest single step.

Rerun after the net failure on published spreads instead of Corwin-Schultz, the Sharpe is {{spread.table.family.value.net_sharpe}} with no cap and {{spread.table.family.value.capped_sharpe}} with one. That's a follow-up, not a test, and the result is still below zero.

A second round after the failure added published impact laws to published spreads. With the trade cap the Sharpe is {{impact.almgren.table.family.value.capped_sharpe}} under Almgren and {{impact.zarinelli.table.family.value.capped_sharpe}} under Zarinelli, with alpha of {{impact.almgren.table.family.value.capped_alpha}} (t {{impact.almgren.table.family.value.capped_alpha_t}}) and {{impact.zarinelli.table.family.value.capped_alpha}} (t {{impact.zarinelli.table.family.value.capped_alpha_t}}). Neither clears t = {{spec.hurdle}}, so no net edge is established, and a second look at the same data is weak evidence either way.

![Monthly IC](../figures/ic_value.png)

![IC by horizon](../figures/decay_value.png)

## Why it might stop working

- **It already weakened.** All of these signals are long published. Most of OSAP's series earn less after publication than before, and the pooled decline across anomalies is {{postpub.after}}.
- **Book value may keep ageing.** Intangible investment is expensed, so book-to-market misprices firms that spend on research and brands. Nothing here adjusts for that.
- **Long droughts.** Value can trail for a decade. A fund can't always wait, and redemptions at the bottom crystallize the loss.
- **Crowding.** Slow signals are cheap to trade, so they are the first place capital goes.

## Verdict

The cross-sectional signal is real but concentrated away from the largest names, and it doesn't pay for its costs as a stand-alone book under these assumptions.
