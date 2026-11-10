# R2 in-sample re-run: A 0.2.0, C 0.2.0, H 0.1.0 (+ B 0.2.0 diagnostic)

> **IN-SAMPLE ONLY: NOT THE VERDICT.** US, 118 month-ends 2014-01-31 … 2023-10-31. A and C were revised using this
> same period, so their numbers here are **biased upward** and are not independent evidence (freeze §1). H's legs are a
> genuine first test. Nothing in this report may feed back into the parameters: the set stays frozen at SHA-256
> `6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e` (`tests/unit/test_frozen_v2_spec.py`).
> Screening-signal backtest for model validation only; not investment advice. Survivor-biased upper bound.

Run: `valuation backtest run --market US --today 2026-11-10` then `report` (bt run `abcc86d1c00b`, report
`reports/backtest/backtest_US_abcc86d1c00b.md`, not committed; `data/` is not in git). Code at branch
`claude/r2-implementation`.

**Holdout untouched.** Without `--holdout`, the US `run` and `report` now only use the in-sample dates: `bt_rows` and
`bt_returns` both hold exactly 118 dates (2014-01-31 … 2023-10-31). No holdout-date signals, returns or metrics were
computed. As in Phases 8–9, the 12m forward returns of the last in-sample dates use prices up to 2024-10, which is
inside the holdout calendar window; that is the existing in-sample definition (`backtest.md`), not a holdout metric.

## 1. Pass bar (`backtest.md` §4.8, unchanged; 12m)
| Model | Verdict leg status | 12m mean IC | NW t | mean spread | spread years > 0 | BUY hit | BUY obs | SELL hit | SELL obs |
|---|---|---|---|---|---|---|---|---|---|
| **A 0.2.0** (model label incl. SELL) | FAIL (SPREAD_YEARS, IC); HOLDOUT not computed | 0.0051 | 0.10 | +0.47% | 3/10 | 58.9% | 1,042 | 50.7% | 17,699 |
| **C 0.2.0** | FAIL (IC); HOLDOUT not computed | 0.0129 | 0.45 | −1.46% | 7/10 | 48.5% | 9,239 | 50.5% | 9,318 |
| **H 0.1.0** | FAIL (IC); HOLDOUT not computed | 0.0298 | 0.80 | +2.36% | 8/10 | 49.6% | 9,614 | 54.9% | 1,941 |
| B 0.2.0 (diagnostic, never a verdict) | FAIL (SPREAD_YEARS, IC) | −0.0165 | −0.36 | −0.14% | 4/10 | 49.7% | 2,351 | 48.4% | 2,380 |

The IC leg needs mean IC > 0.03 **and** NW t > 2.0; no model meets it in-sample. Because the IC leg is evaluated on the
in-sample period, A, C and H already fail the unchanged bar here: the one-shot holdout run can confirm the HOLDOUT
leg's sign but cannot turn an in-sample IC/SPREAD_YEARS failure into a PASS. **This is information for the PM before
signing off the holdout run (freeze §4.1), not a reason to change anything.**

Model A BUY bucket: 12m sector-neutral excess +9.07% (median +5.36%, hit 58.9%, n 1,042), vs v0.1.0's +7.2% (hit 57%,
n 1,306). Q2 confirms the gate: 0 ROE_CLIPPED BUYs (91 name-dates carry `BUY_BLOCKED_CLIP`). The SELL bucket (model
label) is unchanged in kind (+0.02%, hit 50.7%); the screen never shows it (17,699 rows with `SELL_ZONE`).

## 2. Model C factor ICs (diagnostics, 12m)
| Factor | mean IC | NW t | mean spread | years > 0 | v0.1.0 IC (phase 9) |
|---|---|---|---|---|---|
| V value (B/P only in book sectors) | −0.0345 | −0.91 | −1.65% | 4/10 | −0.056 |
| Q quality | 0.0221 | 0.84 | +2.25% | 6/10 | +0.022 |
| M momentum 12-1 | 0.0005 | 0.02 | −0.32% | 5/10 | ≈ 0 |
| R low volatility | 0.0382 | 0.83 | −1.68% | 6/10 | +0.038 |

V is less negative than in v0.1.0, as the revision expected (and as is to be expected in-sample); it is still negative.
Q, M, R are unchanged by construction on US dates (per-market statistics have no effect without TSX inputs).

Model H parts (IC only): **BBY 0.0436 (t 1.58)**, DY −0.0250 (t −0.50). H's information comes from net buybacks,
not dividends, so the proposal's "only DY positive" check is not triggered.

## 3. Excess return vs SPX_TR (equal-weighted bucket minus benchmark, same window)
| Model | BUY 3m | 6m | 12m | 12m years > 0 | SELL 12m | ALL 12m |
|---|---|---|---|---|---|---|
| A | −0.49% | −0.98% | −0.79% | 4/10 | −3.11% | −0.95% |
| C | −0.32% | −0.77% | −2.04% | 3/10 | −0.36% | −0.95% |
| H | +0.15% | +0.22% | **+0.65%** | 5/10 | −2.94% | −0.95% |
| B (diag.) | −0.18% | −0.44% | −1.26% | 3/10 | −1.27% | −0.95% |

Per-year 12m BUY excess, H: 2014 +1.8%, 2015 −0.7%, 2016 +6.0%, 2017 +0.5%, 2018 −3.6%, 2019 −15.6%, 2020 +20.1%,
2021 +7.6%, 2022 −4.9%, 2023 −5.9%. ALL (every eligible name, equal-weighted) trails SPX_TR by 0.95% a year: the
benchmark is cap-weighted and mega-cap growth led this decade.

## 4. Signal counts (mean names per month-end, 2014-01 … 2023-10)
| Model | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|
| A (model label) | 8.8 | 35.9 | 150.0 | 199.7 | 44.5 |
| A (screen: SELL → HOLD) | 8.8 | 185.9 | 0 | 199.7 | 44.5 |
| B (diag.) | 19.9 | 57.0 | 20.2 | 297.3 | 44.5 |
| C | 78.3 | 235.7 | 79.0 | 1.4 | 44.5 |
| H | 81.5 | 268.1 | 16.4 | 28.4 | 44.5 |

## 5. Data observations (reported, not fixed)
1. **ONE_OFF_QUARTER is common:** 16.4% of eligible name-dates have one quarter > 50% of a positive TTM NI. That is a
   wide net (any year with one weak or negative quarter qualifies). It only changes ROE, and A's BUYs stay few (8.8 a
   month); the frequency is recorded for the PM, not acted on.
2. **H BUYs are many** (81.5 a month, about 20% of eligible names): with dividends around 2% and large-cap buybacks of
   3%+ in 2014–2019, SY ≥ 5% is common among S&P 500 names. BUY hit is 49.6% at 12m; the spread comes from SELL
   (net issuers, 16.4 a month, hit 54.9%).
3. **Model B Financials:** 1,597 Financials name-dates (payments / exchanges & data) now go to `EV_BRANCH_PENDING`, as
   specified.
4. **Survivorship:** the same as phase 8 (157 of 253 removed members lack Yahoo prices; 13.8% of member-dates
   missing a price; 1,125 member-dates missing fundamentals).
5. **H `shares_t` basis (spec note, not a blocker):** EPS_TTM uses the as-filed `shares_t` (spec formula) while
   `dps_ttm` is on the as_of share basis; a split between the balance-sheet period end and as_of misstates `payout` by
   the split ratio for that month. It is rare and only affects the `funded` gate. Recorded for AN; no parameter is
   involved and nothing was changed.
