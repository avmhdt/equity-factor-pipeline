| signal | target | weighting | quantile | period | opposite_sign | months | correlation | mean_ours | mean_osap | same_sign | passed | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| accruals | Accruals | EW | 0.1 | 12 | False | 330 | 0.392 | 0.000144 | 0.000888 | True | False | correlation under 0.70; likely cause: Sharadar has no taxes payable, part of OSAP's change in current liabilities |
| asset_growth | AssetGrowth | EW | 0.1 | 12 | False | 330 | 0.843816 | 0.007843 | 0.009241 | True | True |  |
| beta | Beta | EW | 0.2 | 1 | True | 329 | 0.857229 | -0.000466 | -0.005472 | True | True |  |
| bm | BMdec | EW | 0.2 | 12 | False | 330 | 0.906674 | 0.004204 | 0.00426 | True | True |  |
| cfp | CF | EW | 0.1 | 12 | False | 318 | 0.737371 | 0.006437 | 0.003311 | True | True |  |
| ebitda_ev | EntMult | EW | 0.1 | 12 | False | 318 | 0.858625 | 0.003552 | 0.006146 | True | True |  |
| ep | EP | EW | 0.2 | 1 | False | 324 | 0.79037 | 0.00405 | 0.002828 | True | True |  |
| fscore | PS | VW | 0.1 | 1 | False | 321 | 0.224922 | 0.005579 | 0.005349 | True | False | correlation under 0.70; likely cause: OSAP's PS uses other items for some tests; ours scores a missing comparison as 0 |
| gp_at | GP | VW | 0.2 | 12 | False | 318 | 0.718218 | 0.006872 | 0.005608 | True | True |  |
| high_52w | High52 | EW | 0.3 | 6 | False | 324 | 0.975668 | 0.005138 | 0.001789 | True | True |  |
| ind_mom | IndMom | EW | 0.3 | 6 | False | 330 | 0.86828 | 0.005568 | 0.005278 | True | True |  |
| inv_at | InvestPPEInv | EW | 0.3 | 1 | False | 330 | 0.804457 | 0.002964 | 0.003199 | True | True |  |
| ivol | IdioVol3F | VW | 0.2 | 1 | False | 334 | 0.949492 | 0.006236 | 0.003206 | True | True |  |
| ltr_36_13 | LRreversal | EW | 0.2 | 1 | False | 301 | 0.933156 | 0.001777 | 0.00109 | True | True |  |
| max_ret | MaxRet | VW | 0.1 | 1 | False | 334 | 0.948317 | 0.005706 | 0.001612 | True | True |  |
| mom_12_1 | Mom12m | EW | 0.1 | 3 | False | 324 | 0.921025 | 0.011198 | 0.007977 | True | True |  |
| mom_6_1 | Mom6m | EW | 0.1 | 3 | False | 330 | 0.930484 | 0.013638 | 0.0107 | True | True |  |
| noa | NOA | EW | 0.1 | 1 | False | 330 | 0.462941 | 0.001789 | 0.006176 | True | False | correlation under 0.70; likely cause: Sharadar has no minority interest or deferred charges, both in OSAP's formula |
| nsi | ShareIss1Y | EW | 0.2 | 12 | False | 318 | 0.733994 | 0.006716 | 0.009178 | True | True |  |
| roa | roaq | EW | 0.1 | 1 | False | 332 | 0.960336 | 0.014152 | 0.010467 | True | True |  |
| roe | RoE | EW | 0.2 | 12 | False | 330 | 0.857219 | 0.004955 | 0.003199 | True | True |  |
| size | Size | EW | 0.5 | 12 | False | 330 | 0.872715 | 0.000764 | 0.000305 | True | True |  |
| sp | SP | EW | 0.2 | 12 | False | 318 | 0.765376 | 0.011741 | 0.008175 | True | True |  |
| str_1m | STreversal | EW | 0.1 | 1 | False | 334 | 0.942137 | 0.005161 | 0.010164 | True | True |  |
| sue | EarningsSurprise | EW | 0.1 | 1 | False | 304 | 0.689107 | 0.005516 | 0.001866 | True | False | correlation under 0.70; likely cause: OSAP takes a two-year drift out of the EPS change; ours does not |
| vol_12m | RealizedVol | VW | 0.2 | 1 | False | 329 | 0.919185 | 0.00419 | 0.00039 | True | True |  |
