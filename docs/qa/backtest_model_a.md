# Backtest: Model A, residual income (residual_income 0.1.0) (Phase 8, P8-3), as of 2026-11-10

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
| Pass-bar leg | Bar | Model A | |
|---|---|---|---|
| Spread > 0 in ≥ 60% of years (12m) | ≥ 6/10 | **3/10** (2016, 2020, 2023) | fail |
| Mean 12m IC > 0.03 and t > 2 | 0.03 / 2 | **0.0067**, t **0.13** | fail |
| Holdout same sign | | not computed (locked) | incomplete |

Continuous output: MoS, only where Model A gives BUY/HOLD/SELL (about 180–200 names per date). Signal mix: about 11
BUY, 38 HOLD and 146 SELL per month-end. The 12m mean excess is **+7.2% for BUYs** (hit rate 57%, 1,306 BUY
observations, i.e. about 11 a month) and **+0.05% for SELLs** (SELL hit 50.6%, 17,171 observations). The BUY side carries
some information. The SELL side, which is 75% of all signals, does not: a SELL performs like its sector median. The
per-date spread (−1.4% at 12m) is negative even though the pooled BUY excess is positive, because BUYs are few
(a yearly average of 2–29 per date) and swing year by year. Model A's MoS ranking (IC ≈ 0) has no cross-sectional value.

## Result: **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0048 | -0.18 (2) | 118 | -0.37% | 118 | +50.61% | 1306 | +49.58% | 17171 | +0.61% | -0.04% |
| in-sample | 6m | -0.0055 | -0.15 (5) | 118 | -1.19% | 118 | +51.38% | 1306 | +50.02% | 17171 | +2.16% | -0.03% |
| in-sample | 12m | 0.0067 | 0.13 (11) | 118 | -1.37% | 118 | +57.12% | 1306 | +50.56% | 17171 | +7.17% | +0.05% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | -3.70% | -7.73% | -11.22% | 12 |
| 2015 | -0.21% | -2.99% | -6.74% | 12 |
| 2016 | +4.53% | +9.15% | +13.63% | 12 |
| 2017 | -1.58% | -6.87% | -15.57% | 12 |
| 2018 | -2.77% | -6.94% | -15.48% | 12 |
| 2019 | -3.21% | -7.46% | -9.16% | 12 |
| 2020 | +0.84% | +8.17% | +21.10% | 12 |
| 2021 | +0.33% | -0.12% | -0.55% | 12 |
| 2022 | -0.41% | -2.04% | -0.13% | 12 |
| 2023 | +3.08% | +6.15% | +12.75% | 10 |
| **years > 0 (12m)** | | | **3/10** | |

Turnover (monthly mean, in-sample): label +7.00%, BUY set +18.07%, SELL set +6.51%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| 2014 | 4.0 | 39.4 | 136.1 | 151.2 | 53.7 |
| 2015 | 7.8 | 42.8 | 131.2 | 156.1 | 57.5 |
| 2016 | 12.2 | 39.4 | 139.5 | 166.9 | 55.0 |
| 2017 | 2.3 | 29.6 | 159.1 | 189.2 | 46.9 |
| 2018 | 7.6 | 34.5 | 152.6 | 199.0 | 43.0 |
| 2019 | 12.1 | 43.5 | 142.7 | 207.2 | 43.2 |
| 2020 | 28.6 | 45.8 | 118.9 | 217.1 | 50.0 |
| 2021 | 7.9 | 36.1 | 143.3 | 247.4 | 33.0 |
| 2022 | 15.2 | 36.1 | 161.2 | 234.9 | 29.8 |
| 2023 | 13.2 | 33.0 | 175.7 | 233.5 | 30.1 |


## Model A in book sectors vs the plain low-P/B baseline (proposal §2 test)
The proposal's test asks whether the ROE adjustment adds anything over low P/B within book-anchored sectors.

| 12m, in-sample | Mean IC (t) | Mean spread | Years > 0 | BUY hit / excess | SELL hit / excess |
|---|---|---|---|---|---|
| A-book (Model A, book sectors only) | −0.0005 (−0.01) | −1.59% | 3/10 | 59.0% / +7.6% | 49.6% / +0.5% |
| Baseline low-P/B buckets | −0.0367 (−0.55) | −2.30% | 3/10 | 46.5% / +0.7% | 44.9% / +3.0% |

The ROE adjustment does better than plain low P/B on every measure. Its BUY bucket is clearly better (+7.6% vs +0.7%
excess), and the baseline's negative IC (cheap book underperformed in 2014–2023) is pulled to zero. **Neither is
positive**, so the improvement doesn't reach a usable signal.

## Model A restricted to book sectors: **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0074 | -0.23 (2) | 118 | -0.29% | 118 | +52.08% | 985 | +49.34% | 11971 | +1.39% | -0.00% |
| in-sample | 6m | -0.0103 | -0.23 (5) | 118 | -1.35% | 118 | +53.30% | 985 | +49.61% | 11971 | +3.34% | +0.09% |
| in-sample | 12m | -0.0005 | -0.01 (11) | 118 | -1.59% | 118 | +58.98% | 985 | +49.59% | 11971 | +7.57% | +0.51% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | -3.43% | -8.54% | -13.34% | 12 |
| 2015 | +1.43% | -1.64% | -4.62% | 12 |
| 2016 | +5.69% | +11.38% | +15.95% | 12 |
| 2017 | -6.44% | -14.78% | -22.17% | 12 |
| 2018 | -2.81% | -5.98% | -10.83% | 12 |
| 2019 | -1.65% | -5.56% | -7.08% | 12 |
| 2020 | +1.61% | +9.40% | +20.94% | 12 |
| 2021 | -0.09% | -0.31% | -1.06% | 12 |
| 2022 | +0.49% | -1.14% | -0.36% | 12 |
| 2023 | +2.83% | +4.65% | +8.36% | 10 |
| **years > 0 (12m)** | | | **3/10** | |

Turnover (monthly mean, in-sample): label +6.51%, BUY set +17.94%, SELL set +4.11%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| 2014 | 3.2 | 34.6 | 85.8 | 249.8 | 10.9 |
| 2015 | 6.7 | 36.3 | 83.5 | 257.8 | 11.2 |
| 2016 | 9.8 | 29.5 | 93.9 | 269.5 | 10.3 |
| 2017 | 1.6 | 22.8 | 115.8 | 278.9 | 8.0 |
| 2018 | 5.2 | 22.4 | 111.9 | 288.8 | 8.4 |
| 2019 | 8.2 | 30.3 | 100.8 | 301.2 | 8.2 |
| 2020 | 22.5 | 32.4 | 83.8 | 311.9 | 9.7 |
| 2021 | 7.6 | 26.8 | 105.2 | 322.8 | 5.4 |
| 2022 | 9.1 | 25.7 | 114.2 | 323.0 | 5.2 |
| 2023 | 9.9 | 23.1 | 123.1 | 323.8 | 5.6 |



## Baseline: low P/B buckets (book sectors): **FAIL** (failed: SPREAD_YEARS, IC; incomplete: HOLDOUT)

| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 3m | -0.0252 | -0.80 (2) | 118 | -0.68% | 118 | +47.81% | 3742 | +45.86% | 3742 | -0.02% | +0.62% |
| in-sample | 6m | -0.0386 | -0.85 (5) | 118 | -1.46% | 118 | +46.10% | 3742 | +45.32% | 3742 | +0.00% | +1.38% |
| in-sample | 12m | -0.0367 | -0.55 (11) | 118 | -2.30% | 118 | +46.45% | 3742 | +44.87% | 3742 | +0.74% | +2.96% |

Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, equal-weighted):

| Year | 3m | 6m | 12m | dates (12m) |
|---|---|---|---|---|
| 2014 | -2.02% | -4.64% | -10.24% | 12 |
| 2015 | -2.71% | -5.78% | -4.94% | 12 |
| 2016 | +3.16% | +5.22% | +3.93% | 12 |
| 2017 | -4.19% | -7.71% | -13.48% | 12 |
| 2018 | -3.57% | -7.64% | -17.35% | 12 |
| 2019 | -4.87% | -12.08% | -22.70% | 12 |
| 2020 | +3.87% | +14.38% | +33.61% | 12 |
| 2021 | +3.76% | +6.62% | +12.01% | 12 |
| 2022 | +0.61% | -1.93% | -3.15% | 12 |
| 2023 | -0.91% | -0.91% | -0.39% | 10 |
| **years > 0 (12m)** | | | **3/10** | |

Turnover (monthly mean, in-sample): label +6.21%, BUY set +9.39%, SELL set +7.08%.

Signal counts (mean per month-end, in-sample, by year):

| Year | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| 2014 | 28.7 | 76.8 | 28.7 | 196.5 | 53.7 |
| 2015 | 28.3 | 78.0 | 28.3 | 203.2 | 57.5 |
| 2016 | 31.2 | 80.5 | 31.2 | 215.1 | 55.0 |
| 2017 | 32.5 | 86.4 | 32.5 | 228.8 | 46.9 |
| 2018 | 31.7 | 88.6 | 31.7 | 241.8 | 43.0 |
| 2019 | 31.9 | 89.4 | 31.9 | 252.2 | 43.2 |
| 2020 | 32.0 | 88.0 | 32.0 | 258.3 | 50.0 |
| 2021 | 32.1 | 91.8 | 32.1 | 278.8 | 33.0 |
| 2022 | 33.9 | 95.1 | 33.9 | 284.5 | 29.8 |
| 2023 | 35.4 | 97.7 | 35.4 | 286.9 | 30.1 |



## Phase 7 open questions for Model A (in-sample)
#### Q1: Model A SELL bucket vs its sector median

| Horizon | Bucket | n obs | mean excess | median excess | hit vs sector | mean return | mean sector median |
|---|---|---|---|---|---|---|---|
| 3m | SELL_ALL | 17171 | -0.04% | +0.00% | +49.58% | +2.51% | +2.55% |
| 3m | PB_GT_2_9 | 4689 | +0.44% | +0.13% | +47.11% | +2.58% | +2.13% |
| 3m | PB_LE_2_9 | 12482 | -0.23% | -0.08% | +50.50% | +2.49% | +2.71% |
| 3m | HOLD_REF | 4496 | -0.01% | -0.07% | +50.44% | +3.13% | +3.14% |
| 6m | SELL_ALL | 17171 | -0.03% | -0.00% | +50.02% | +4.87% | +4.90% |
| 6m | PB_GT_2_9 | 4689 | +0.96% | +0.00% | +48.20% | +4.98% | +4.02% |
| 6m | PB_LE_2_9 | 12482 | -0.40% | -0.13% | +50.71% | +4.82% | +5.22% |
| 6m | HOLD_REF | 4496 | -0.21% | -0.22% | +51.00% | +5.59% | +5.80% |
| 12m | SELL_ALL | 17171 | +0.05% | -0.15% | +50.56% | +9.76% | +9.71% |
| 12m | PB_GT_2_9 | 4689 | +2.08% | +0.04% | +47.66% | +10.18% | +8.09% |
| 12m | PB_LE_2_9 | 12482 | -0.72% | -0.43% | +51.64% | +9.60% | +10.31% |
| 12m | HOLD_REF | 4496 | -0.44% | -1.60% | +53.71% | +11.39% | +11.83% |

#### Q2: Model A BUYs with / without ROE_CLIPPED

| Horizon | Bucket | n obs | mean excess | median excess | hit vs sector | mean return | mean sector median |
|---|---|---|---|---|---|---|---|
| 3m | CLIPPED | 118 | +1.00% | +1.73% | +51.69% | +5.56% | +4.55% |
| 3m | NOT_CLIPPED | 1188 | +0.57% | +0.10% | +50.51% | +5.39% | +4.82% |
| 6m | CLIPPED | 118 | +1.89% | +0.64% | +51.69% | +9.94% | +8.05% |
| 6m | NOT_CLIPPED | 1188 | +2.18% | +0.44% | +51.35% | +11.52% | +9.34% |
| 12m | CLIPPED | 118 | +1.73% | -2.78% | +44.07% | +18.18% | +16.45% |
| 12m | NOT_CLIPPED | 1188 | +7.70% | +4.70% | +58.42% | +29.80% | +22.10% |


**Q1, SELL skew (phase7-validation §6g).** The skew is costly only in the high-P/B part. All SELLs: 12m mean excess
+0.05%, median −0.15%, SELL hit 50.6%: no better than a coin. The forced SELLs above P/B 2.9 (4,689 observations) beat
their sector by **+2.1%** on average (SELL hit 47.7%, so 52% outperform). SELLs at P/B ≤ 2.9 lag by −0.7% (hit 51.6%).
So the SELLs the model gives "whatever the ROE" are slightly wrong-way, and the lower-P/B SELLs are close to noise.

**Q2, one-off ROE (§6d).** Yes, BUYs built on a clipped ROE underperform. ROE_CLIPPED BUYs (118 observations): 12m mean
excess +1.7%, **median −2.8%, hit 44%**. Unclipped BUYs (1,188): **+7.7%, median +4.7%, hit 58%**. At 3m the two
are similar; the gap opens at 6–12 months, when the one-off falls out of TTM.

Parameters were not changed. Proposals for the PM are in phase8-validation.md §6.
