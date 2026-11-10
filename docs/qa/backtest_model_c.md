# Backtest: Model C, value–quality–momentum–risk composite (composite 0.1.0) (Phase 9, P9-4), as of 2026-11-10

Owner: QA (P9-4) · Spec: [`docs/models/composite.md`](../models/composite.md) 0.1.0, harness [`backtest.md`](../models/backtest.md)
0.2.0 · Run `3b8b3acaa8ab` (in-sample, US), thresholds hash `62e8c911af5b6f7f…` (Phase 8 constants + liquidity.yaml +
composite constants; no A/B constant changed, A and B reproduce Phase 8 to the last digit) · DB `data/backtest.duckdb`.
Screening-signal validation only; not investment advice. **Survivor-biased upper bound:** 11.1% of member-dates lack
prices (158 of 258 removed names), as in Phase 8.

## Verdict: **FAIL** (in-sample IC leg fails; spread-years leg passes; holdout leg not opened)
| Leg | Bar | Model C | Result |
|---|---|---|---|
| SPREAD_YEARS | 12m spread > 0 in ≥ 60% of years | 6/10 (60%) | pass, exactly on the bar |
| IC | mean 12m IC > 0.03 and NW t > 2 | 0.0081, t 0.28 (lag 11, 118 dates) | **fail** |
| HOLDOUT | same sign out of sample | not computed | locked |

The mean 12m spread is **−1.89%** although 6 of 10 years are positive: 2020 (−26.1%) and 2016 (−7.7%) dominate the
mean. Hit rates are coin flips: BUY 48.3% (9,251 obs), SELL 50.1% (9,318 obs). Model C therefore stays
**"unvalidated"**, like A and B. The holdout (last 36 month-ends) stays unspent; the vote (Phase 10) stays off: 0 of 3
models pass.

## Each factor's own spread and IC (diagnostic: quintiles of the factor score in the eligible universe, no vetoes)
| Factor | 12m mean IC | NW t | 12m mean spread | years > 0 | BUY hit / SELL hit | Turnover (BUY set) | Reading |
|---|---|---|---|---|---|---|---|
| **C (composite)** | 0.0081 | 0.28 | −1.89% | 6/10 | 48.3% / 50.1% | 19.7% | dead |
| V value (E/P, B/P, FCF/P) | **−0.0561** | −1.32 | −2.66% | 3/10 | 47.7% / 47.1% | 11.6% | **negative** (value lagged 2014–2023) |
| Q quality (ROE, GP/A, −accruals, −leverage) | 0.0221 | 0.83 | **+2.24%** | 6/10 | 51.9% / 50.4% | 8.1% | weak positive, not significant |
| M momentum 12-1 | 0.0005 | 0.02 | −0.32% | 5/10 | 47.7% / 50.1% | 23.0% | dead |
| R low volatility | **0.0382** | 0.83 | −1.68% | 6/10 | 50.2% / 51.7% | 9.2% | IC above 0.03 but t < 1; spread negative |

No single factor clears the bar either. The equal weights combine a negative value factor with weak quality and
risk factors, which nets out to about zero. That is a finding, not a reason to reweight: weights are fixed by the
proposal and are not tuned on these results (PM proposals in phase9-validation.md §5).

## Excess return vs benchmark (SPX_TR, in-sample, 12m; reported alongside the bar, not a criterion)
| Bucket | mean 12m excess | years > 0 |
|---|---|---|
| C BUY | −1.92% | 3/10 |
| C SELL | +0.12% | 2/10 |
| ALL eligible (equal-weighted) | −0.95% | 5/10 |

Equal-weighted buckets start about 1%/yr behind the cap-weighted index (the ALL row), so the BUY − ALL gap
(≈ −1.0%) is the model's contribution.

## Generated report sections (verbatim from `reports/backtest/backtest_US_3b8b3acaa8ab.md`)
### Model C (value-quality-momentum-risk composite): **FAIL** (failed: IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | 0.0001 | 0.01 (2) | 118 | -0.23% | 118 | +48.74% | 9251 | +49.31% | 9318 | -0.04% | +0.24% |
| in-sample | 6m | 0.0081 | 0.28 (5) | 118 | -0.70% | 118 | +48.88% | 9251 | +49.73% | 9318 | +0.09% | +0.85% |
| in-sample | 12m | 0.0081 | 0.28 (11) | 118 | -1.89% | 118 | +48.32% | 9251 | +50.13% | 9318 | +0.45% | +2.35% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | +2.08% | +1.91% | +3.32% | 12 |
| 2015 | +3.22% | +5.12% | +1.94% | 12 |
| 2016 | -4.17% | -6.42% | -7.71% | 12 |
| 2017 | +1.32% | +1.16% | -0.34% | 12 |
| 2018 | +0.17% | +0.82% | +3.26% | 12 |
| 2019 | +1.37% | +4.38% | +2.86% | 12 |
| 2020 | -7.46% | -17.85% | -26.06% | 12 |
| 2021 | +1.16% | +4.88% | +4.55% | 12 |
| 2022 | +0.16% | -2.25% | -3.12% | 12 |
| 2023 | -0.14% | +1.62% | +3.31% | 10 |
| **years > 0 (12m)** | | | **6/10** | |

Turnover (monthly mean, in-sample): label +13.86%, BUY set +19.69%, SELL set +16.37%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| 2014 | 65.8 | 198.1 | 66.3 | 0.5 | 53.7 |
| 2015 | 67.3 | 202.2 | 67.9 | 0.5 | 57.5 |
| 2016 | 71.5 | 212.7 | 71.6 | 2.3 | 55.0 |
| 2017 | 76.0 | 227.2 | 76.2 | 0.8 | 46.9 |
| 2018 | 78.9 | 235.2 | 79.1 | 0.5 | 43.0 |
| 2019 | 81.1 | 242.2 | 81.2 | 1.0 | 43.2 |
| 2020 | 80.8 | 245.0 | 82.0 | 2.5 | 50.0 |
| 2021 | 85.3 | 260.5 | 86.8 | 2.1 | 33.0 |
| 2022 | 89.0 | 266.8 | 89.5 | 2.1 | 29.8 |
| 2023 | 90.2 | 272.4 | 91.0 | 1.8 | 30.1 |

### Model C factor V (value), diagnostic: **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0267 | -1.22 (2) | 118 | -0.72% | 118 | +48.25% | 9318 | +47.71% | 9318 | -0.14% | +0.56% |
| in-sample | 6m | -0.0392 | -1.31 (5) | 118 | -1.48% | 118 | +48.12% | 9318 | +47.71% | 9318 | -0.20% | +1.23% |
| in-sample | 12m | -0.0561 | -1.32 (11) | 118 | -2.66% | 118 | +47.66% | 9318 | +47.06% | 9318 | +0.37% | +2.92% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | +0.13% | -1.16% | -5.57% | 12 |
| 2015 | -2.01% | -4.98% | -2.97% | 12 |
| 2016 | +1.95% | +4.36% | +5.72% | 12 |
| 2017 | -1.50% | -3.09% | -8.28% | 12 |
| 2018 | -3.18% | -7.10% | -14.44% | 12 |
| 2019 | -3.94% | -10.12% | -24.36% | 12 |
| 2020 | +0.24% | +6.21% | +19.72% | 12 |
| 2021 | +1.94% | +4.35% | +9.37% | 12 |
| 2022 | +0.70% | -2.04% | -4.43% | 12 |
| 2023 | -1.68% | -1.21% | -1.07% | 10 |
| **years > 0 (12m)** | | | **3/10** | |

Turnover (monthly mean, in-sample): label +7.83%, BUY set +11.61%, SELL set +9.40%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL |
|---|---|---|---|---|
| 2014 | 66.3 | 197.5 | 66.3 | 54.2 |
| 2015 | 67.9 | 201.6 | 67.9 | 58.0 |
| 2016 | 71.6 | 212.6 | 71.6 | 57.3 |
| 2017 | 76.2 | 226.9 | 76.2 | 47.8 |
| 2018 | 79.1 | 235.0 | 79.1 | 43.5 |
| 2019 | 81.2 | 242.2 | 81.2 | 44.2 |
| 2020 | 82.0 | 243.8 | 82.0 | 52.5 |
| 2021 | 86.8 | 259.0 | 86.8 | 35.1 |
| 2022 | 89.5 | 266.3 | 89.5 | 31.9 |
| 2023 | 91.0 | 271.6 | 91.0 | 31.9 |

### Model C factor Q (quality), diagnostic: **FAIL** (failed: IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | 0.0117 | 0.81 (2) | 118 | +0.53% | 118 | +50.89% | 9282 | +50.27% | 9282 | +0.38% | -0.13% |
| in-sample | 6m | 0.0174 | 0.83 (5) | 118 | +1.14% | 118 | +51.23% | 9282 | +49.91% | 9282 | +1.07% | -0.06% |
| in-sample | 12m | 0.0221 | 0.83 (11) | 118 | +2.24% | 118 | +51.86% | 9282 | +50.37% | 9282 | +2.56% | +0.34% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | +1.67% | +3.44% | +5.70% | 12 |
| 2015 | +1.04% | +1.32% | -0.97% | 12 |
| 2016 | -2.36% | -4.61% | -6.42% | 12 |
| 2017 | +1.82% | +4.44% | +10.03% | 12 |
| 2018 | +1.62% | +1.84% | +2.18% | 12 |
| 2019 | +0.77% | +4.59% | +12.45% | 12 |
| 2020 | -0.52% | -2.67% | -1.06% | 12 |
| 2021 | +1.28% | +3.00% | +2.49% | 12 |
| 2022 | -0.34% | +0.36% | +1.37% | 12 |
| 2023 | +0.28% | -0.55% | -4.44% | 10 |
| **years > 0 (12m)** | | | **6/10** | |

Turnover (monthly mean, in-sample): label +6.71%, BUY set +8.09%, SELL set +10.05%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL |
|---|---|---|---|---|
| 2014 | 65.8 | 196.0 | 65.8 | 56.7 |
| 2015 | 67.8 | 201.4 | 67.8 | 58.5 |
| 2016 | 71.6 | 211.5 | 71.6 | 58.4 |
| 2017 | 75.7 | 225.1 | 75.7 | 50.8 |
| 2018 | 78.4 | 233.1 | 78.4 | 46.8 |
| 2019 | 81.0 | 240.1 | 81.0 | 46.6 |
| 2020 | 81.7 | 243.2 | 81.7 | 53.8 |
| 2021 | 86.7 | 258.1 | 86.7 | 36.3 |
| 2022 | 89.1 | 265.7 | 89.1 | 33.4 |
| 2023 | 91.0 | 270.5 | 91.0 | 33.0 |

### Model C factor M (momentum 12-1), diagnostic: **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0060 | -0.28 (2) | 118 | -0.25% | 118 | +48.05% | 9319 | +48.92% | 9315 | -0.08% | +0.20% |
| in-sample | 6m | 0.0035 | 0.12 (5) | 118 | -0.22% | 118 | +48.23% | 9319 | +49.48% | 9315 | +0.24% | +0.49% |
| in-sample | 12m | 0.0005 | 0.02 (11) | 118 | -0.32% | 118 | +47.69% | 9319 | +50.12% | 9315 | +1.14% | +1.44% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | +0.41% | +0.29% | +3.59% | 12 |
| 2015 | +2.97% | +4.57% | -0.13% | 12 |
| 2016 | -4.11% | -6.25% | -4.34% | 12 |
| 2017 | +1.95% | +3.11% | +2.57% | 12 |
| 2018 | -0.69% | -0.77% | +0.07% | 12 |
| 2019 | +1.95% | +6.83% | +13.26% | 12 |
| 2020 | -3.91% | -11.80% | -19.67% | 12 |
| 2021 | -0.90% | +0.73% | -4.62% | 12 |
| 2022 | -0.83% | -2.31% | -2.12% | 12 |
| 2023 | +0.88% | +4.07% | +9.89% | 10 |
| **years > 0 (12m)** | | | **5/10** | |

Turnover (monthly mean, in-sample): label +18.00%, BUY set +23.02%, SELL set +23.53%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL |
|---|---|---|---|---|
| 2014 | 66.3 | 197.4 | 66.3 | 54.2 |
| 2015 | 67.9 | 201.9 | 67.6 | 58.0 |
| 2016 | 71.6 | 212.6 | 71.6 | 57.3 |
| 2017 | 76.2 | 226.9 | 76.2 | 47.8 |
| 2018 | 79.1 | 235.0 | 79.1 | 43.5 |
| 2019 | 81.2 | 242.1 | 81.2 | 44.2 |
| 2020 | 82.0 | 243.8 | 82.0 | 52.5 |
| 2021 | 87.0 | 258.8 | 86.8 | 35.1 |
| 2022 | 89.5 | 266.3 | 89.5 | 31.9 |
| 2023 | 90.9 | 271.7 | 91.0 | 31.9 |

### Model C factor R (low volatility), diagnostic: **FAIL** (failed: IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | 0.0126 | 0.49 (2) | 118 | -0.44% | 118 | +49.10% | 9325 | +49.36% | 9311 | -0.07% | +0.43% |
| in-sample | 6m | 0.0219 | 0.61 (5) | 118 | -0.96% | 118 | +49.21% | 9325 | +50.31% | 9311 | -0.01% | +1.05% |
| in-sample | 12m | 0.0382 | 0.83 (11) | 118 | -1.68% | 118 | +50.17% | 9325 | +51.66% | 9311 | +0.51% | +2.23% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | +0.93% | +0.93% | +3.53% | 12 |
| 2015 | +4.07% | +7.85% | +6.92% | 12 |
| 2016 | -3.82% | -6.76% | -10.74% | 12 |
| 2017 | -0.84% | -3.49% | -5.06% | 12 |
| 2018 | +2.31% | +6.64% | +16.25% | 12 |
| 2019 | +2.65% | +5.05% | +1.34% | 12 |
| 2020 | -10.03% | -22.39% | -37.58% | 12 |
| 2021 | +1.23% | +4.84% | +9.17% | 12 |
| 2022 | -0.19% | -1.55% | -1.08% | 12 |
| 2023 | -0.77% | -0.70% | +0.84% | 10 |
| **years > 0 (12m)** | | | **6/10** | |

Turnover (monthly mean, in-sample): label +6.48%, BUY set +9.19%, SELL set +8.46%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL |
|---|---|---|---|---|
| 2014 | 66.4 | 197.4 | 66.3 | 54.2 |
| 2015 | 67.9 | 201.6 | 67.9 | 58.0 |
| 2016 | 71.8 | 212.4 | 71.6 | 57.3 |
| 2017 | 76.2 | 226.9 | 76.2 | 47.8 |
| 2018 | 79.1 | 235.0 | 79.1 | 43.5 |
| 2019 | 81.2 | 242.2 | 81.2 | 44.2 |
| 2020 | 82.0 | 244.1 | 81.8 | 52.5 |
| 2021 | 87.2 | 259.0 | 86.5 | 35.1 |
| 2022 | 89.5 | 266.3 | 89.5 | 31.9 |
| 2023 | 91.0 | 271.6 | 91.0 | 31.9 |

