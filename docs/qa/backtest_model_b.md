# Backtest: Model B, P/B ~ ROE peer regression (peer_regression 0.1.0, book branch) (Phase 8, P8-3), as of 2026-11-10

Owner: QA · Spec: [`docs/models/backtest.md`](../models/backtest.md) v0.1.1 · Run: `valuation backtest report --market US`
(in-sample run `b97630c9f771`, thresholds hash `203efff963f52529`, git `cc91143`+) · DB: `data/backtest.duckdb` (not in git)

> **Survivor-biased upper bound.** 158 of 258 names removed from the S&P 500 since 2013 have no Yahoo prices, so
> 8,641 of 77,862 member-dates (11.1%; 24% in 2014, falling to 1% in 2026) are missing from every metric (see
> [`phase8-validation.md`](phase8-validation.md) §3). Screening-signal validation only; not investment advice.

Universe: point-in-time S&P 500 members on each calendar month-end, 2014-01-31 to 2023-10-31 (118 in-sample dates).
The holdout (the last 36 month-ends, 2023-11-30 to 2026-10-31) stays **locked**: see phase8-validation.md §1.
Forward returns are total returns from `adj_close`, measured from the next session. "Excess" is the return minus the
median of all eligible members in the same GICS sector on that date (≥ 5 names). "Spread" is the mean BUY excess minus
the mean SELL excess on a date; yearly figures average the monthly spreads. IC is the Spearman correlation of the
continuous output with the raw forward return (≥ 20 names); t is Newey–West with lag = horizon − 1.

## Verdict: **FAIL** (in-sample legs SPREAD_YEARS and IC both fail; holdout leg not opened)
| Pass-bar leg | Bar | Model B | |
|---|---|---|---|
| Spread > 0 in ≥ 60% of years (12m) | ≥ 6/10 | **4/10** (2016, 2020, 2021, 2023) | fail |
| Mean 12m IC > 0.03 and t > 2 | 0.03 / 2 | **−0.0539**, t **−0.86** | fail |
| Holdout same sign | | not computed (locked) | incomplete |

Continuous output: −residual, only where the sector fit is valid and B gives BUY/HOLD/SELL (about 110 names per date in
Financials, Real Estate, Energy, Materials; Utilities is mostly NO_FIT). Signal mix: about 23 BUY, 65 HOLD and 23 SELL
per month-end. The sign is wrong at every horizon. 12m BUY excess is +1.0% (hit 49.9%) and SELL excess is
**+3.5%** (SELL hit 43.6%): the names that look dear for their ROE kept outperforming. The IC is negative but not
significant (t −0.86). In 2014–2019, 5 of 6 years have a negative spread; 2020–2021 are strongly positive (value
rebound).

## Result: **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0214 | -0.74 (2) | 118 | -0.51% | 118 | +47.90% | 2672 | +46.63% | 2700 | -0.13% | +0.57% |
| in-sample | 6m | -0.0400 | -0.97 (5) | 118 | -1.08% | 118 | +47.53% | 2672 | +45.07% | 2700 | +0.10% | +1.51% |
| in-sample | 12m | -0.0539 | -0.86 (11) | 118 | -2.15% | 118 | +49.93% | 2672 | +43.63% | 2700 | +1.05% | +3.49% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | -1.43% | -4.63% | -10.60% | 12 |
| 2015 | -1.36% | -4.07% | -8.04% | 12 |
| 2016 | +0.76% | +1.90% | +1.54% | 12 |
| 2017 | -4.43% | -7.81% | -16.05% | 12 |
| 2018 | -3.40% | -6.57% | -14.98% | 12 |
| 2019 | -4.10% | -12.22% | -22.61% | 12 |
| 2020 | +3.78% | +16.05% | +41.10% | 12 |
| 2021 | +3.85% | +7.86% | +11.94% | 12 |
| 2022 | +0.96% | -2.14% | -4.76% | 12 |
| 2023 | +0.44% | +1.15% | +1.64% | 10 |
| **years > 0 (12m)** | | | **4/10** | |

Turnover (monthly mean, in-sample): label +9.60%, BUY set +18.32%, SELL set +14.45%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| 2014 | 22.0 | 62.7 | 22.8 | 223.2 | 53.7 |
| 2015 | 18.5 | 52.2 | 18.5 | 248.7 | 57.5 |
| 2016 | 22.8 | 63.9 | 23.2 | 248.2 | 55.0 |
| 2017 | 22.8 | 69.5 | 25.0 | 262.9 | 46.9 |
| 2018 | 23.5 | 67.7 | 22.7 | 279.8 | 43.0 |
| 2019 | 23.2 | 67.3 | 23.4 | 291.6 | 43.2 |
| 2020 | 21.8 | 61.3 | 21.7 | 305.5 | 50.0 |
| 2021 | 25.7 | 74.8 | 25.5 | 308.8 | 33.0 |
| 2022 | 22.7 | 62.8 | 22.4 | 339.5 | 29.8 |
| 2023 | 23.7 | 67.5 | 23.8 | 340.4 | 30.1 |



## Phase 7 open questions for Model B (in-sample)
#### Q3: Model B BUYs on loss-makers (NEG_ROE_UNFITTED)

| Horizon | Bucket | n obs | mean excess | median excess | hit vs sector | mean return | mean sector median |
|---|---|---|---|---|---|---|---|
| 3m | NEG_ROE_UNFITTED | 89 | +0.60% | -0.17% | +47.19% | +4.66% | +4.05% |
| 3m | ROE_NONNEG | 2583 | -0.16% | -0.15% | +47.93% | +2.61% | +2.77% |
| 6m | NEG_ROE_UNFITTED | 89 | +2.51% | -0.05% | +48.31% | +10.26% | +7.75% |
| 6m | ROE_NONNEG | 2583 | +0.01% | -0.21% | +47.50% | +5.22% | +5.20% |
| 12m | NEG_ROE_UNFITTED | 89 | +3.62% | -1.38% | +48.31% | +15.69% | +12.07% |
| 12m | ROE_NONNEG | 2583 | +0.96% | +0.00% | +49.98% | +11.46% | +10.50% |

#### Q4: Financials, Model B as specified vs split by GICS sub-industry (diagnostic)

| Horizon | Variant | Mean IC | NW t | IC dates | mean spread | spread dates | BUY hit | SELL hit | BUY obs | SELL obs |
|---|---|---|---|---|---|---|---|---|---|---|
| 3m | B | -0.0441 | -1.18 | 113 | -1.02% | 113 | +47.55% | +44.11% | 1451 | 1451 |
| 6m | B | -0.0758 | -1.47 | 113 | -2.49% | 113 | +47.21% | +42.25% | 1451 | 1451 |
| 12m | B | -0.0777 | -1.05 | 113 | -3.69% | 113 | +49.41% | +37.77% | 1451 | 1451 |
| 3m | B_FIN_SPLIT | -0.0441 | -1.18 | 113 | -1.02% | 113 | +47.55% | +44.11% | 1451 | 1451 |
| 6m | B_FIN_SPLIT | -0.0758 | -1.47 | 113 | -2.49% | 113 | +47.21% | +42.25% | 1451 | 1451 |
| 12m | B_FIN_SPLIT | -0.0777 | -1.05 | 113 | -3.69% | 113 | +49.41% | +37.77% | 1451 | 1451 |
| 3m | B_FIN_EX_PAY_DATA | -0.0108 | -0.39 | 110 | -0.41% | 110 | +46.64% | +48.54% | 1130 | 1131 |
| 6m | B_FIN_EX_PAY_DATA | -0.0208 | -0.63 | 110 | -0.86% | 110 | +47.35% | +48.19% | 1130 | 1131 |
| 12m | B_FIN_EX_PAY_DATA | -0.0088 | -0.20 | 110 | -0.88% | 110 | +48.85% | +46.07% | 1130 | 1131 |
| 3m | B_EX_PAY_DATA_SUBSET | -0.0099 | -0.39 | 113 | -1.43% | 113 | +47.28% | +46.09% | 1413 | 460 |
| 6m | B_EX_PAY_DATA_SUBSET | -0.0198 | -0.70 | 113 | -2.81% | 113 | +46.43% | +43.04% | 1413 | 460 |
| 12m | B_EX_PAY_DATA_SUBSET | -0.0051 | -0.14 | 113 | -2.56% | 113 | +48.41% | +40.00% | 1413 | 460 |

Split groups that occurred: Financials-other


**Q3, loss-maker BUYs (§6f).** No evidence that they underperform on average, but the sample is small. NEG_ROE_UNFITTED
BUYs (89 observations, under 1 a month): 12m mean excess +3.6%, **median −1.4%**, hit 48.3%. Other BUYs (2,583):
+1.0%, median 0.0%, hit 50.0%. The positive mean comes from a few large winners; the median and hit rate are slightly
worse. Inconclusive. Capping them at HOLD would barely change Model B's results either way.

**Q4, Financials slope (§6e).** Yes, the distortion hurts Model B in Financials, and removing it brings Financials to
about zero, not to a positive signal.
- Financials as specified: 12m IC **−0.078** (t −1.05), spread **−3.7%**, SELL hit only 37.8%. The dear-for-ROE
  Financials, often payment, exchange and data names, kept outperforming.
- With the sub-industry split (spec Q4, a group needs ≥ 12 fit names) the result is **identical**. No GICS
  sub-industry ever has 12 fit names on an in-sample date. The largest, Asset Management & Custody Banks, reaches 12
  once, in the holdout. So the split rule as specified can't take effect within the S&P 500 alone.
- Extra diagnostic, `B_FIN_EX_PAY_DATA`: the pooled Financials fit without Transaction & Payment Processing and
  Financial Exchanges & Data (the P7-14 names). 12m IC −0.009 (t −0.20), spread −0.9%, SELL hit 46%. On the same
  subset, the specified fit gives IC −0.005 and spread −2.6%. So leaving those names out of the fit raises the
  Financials spread by about 1.7–2.8pp and removes most of the negative IC. What remains is noise.

Parameters were not changed. Proposals for the PM are in phase8-validation.md §6.
