# Revision round v2: frozen parameters and holdout protocol (v1.0, frozen 2026-11-10)

Owner: AN (PM approves) · Applies to: `residual_income` 0.2.0 (A), `peer_regression` 0.2.0 (B, diagnostic),
`composite` 0.2.0 (C), `shareholder_yield` 0.1.0 (H), `eligibility` 0.1.1 · Bar: `backtest.md` v0.2.0 §4.8,
**unchanged**. Screening signals only; not investment advice.

Every revision was written from the US in-sample evidence (2014-01 … 2023-10) and economic reasoning only. **No
holdout-period result has been computed or looked at**: `bt_runs` has no `holdout` row (phase8 §1, phase9 §4).

## 1. Revisions and their overfitting risk
| Model | Change | Justification (in-sample evidence / economics) | Overfitting risk |
|---|---|---|---|
| A | SELL → information only (`screen_signal` HOLD + `SELL_ZONE`); model label unchanged | SELL hit 50.6%; P/B > 2.9 SELLs +2.1% wrong-way; a no-TV fade is a lower bound, so only BUY is meaningful | low (backtest label unchanged) |
| A | One-off quarter: > 50% of positive TTM NI → ROE on the other 3 quarters annualised; no BUY on a clipped ROE | clipped BUYs +1.7% (hit 44%) vs +7.7% (hit 58%); Phase 7 found one-offs before the backtest | **medium** (gate removes a bucket found in-sample, n 118) |
| B | Payments / exchanges & data → EV branch | Financials IC −0.078 → −0.009 without them; book is not their operating asset | low–medium |
| B | Diagnostic only, never in the gate | IC −0.054 at all horizons; low-P/B baseline also negative; sign flip would be fitting | none |
| C | B/P only in BOOK_SECTORS; value kept, equal weights | V IC −0.056; book omits intangibles outside book sectors; same sector set as A/B since Phase 7 | **medium** (direction agrees with the sample) |
| C | Cross-section per market | TSX 29% vs US 16% BUY (P9-9) | low (no US in-sample effect) |
| C / elig. | E6 273 → 274 sessions | MOM needs 274 bars (P9-6) | none |
| H | New model from the proposal, constants as proposed | payout/issuance literature; never backtested | low |

Because A and C were revised using the in-sample period, their **in-sample legs are no longer independent evidence**
(biased upward). The holdout leg is the only clean test. H's in-sample legs are a genuine first test.

## 2. Frozen parameter set
Canonical JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"))`, ASCII, sets as sorted lists, no trailing
newline). It holds every constant of the four specs' Constants sections, the structural switches, the full
`config/liquidity.yaml` after the E6 change, and the unchanged pass-bar constants:
```json
{"backtest_bar":{"HOLDOUT_N":36,"PASS_HORIZON":12,"PASS_IC":0.03,"PASS_T":2.0,"PASS_YEAR_SHARE":0.6},"composite":{"ANNUALIZE":252,"BOOK_SECTORS":["Energy","Financials","Materials","Real Estate","Utilities"],"BP_SECTORS":"BOOK_SECTORS","BUY_PCT":0.8,"CROSS_SECTION":"per_market","FACTORS":{"momentum":["mom"],"quality":["roe","gpa","neg_accruals","neg_leverage"],"risk":["neg_vol"],"value":["ep","bp","fcfp"]},"FINANCIALS":"Financials","MARKETS":["TSX","US"],"MIN_FACTORS":3,"MIN_SECTOR_NAMES":5,"MODEL_VERSION":"composite/0.2.0","MOM_LOOKBACK":273,"MOM_SKIP":21,"MOM_VETO_PCT":0.1,"SELL_PCT":0.2,"STD_EPS":1e-12,"VOL_WINDOW":252,"WEIGHTS":"equal","WINSOR":[0.05,0.95]},"liquidity":{"tsx":{"max_stale_sessions":5,"max_zero_volume_days":3,"mdtv_window":63,"min_history_sessions":274,"min_market_cap":1000000000,"min_mdtv":5000000,"min_price":2},"us":{"max_stale_sessions":5,"max_zero_volume_days":3,"mdtv_window":63,"min_history_sessions":274,"min_market_cap":5000000000,"min_mdtv":25000000,"min_price":5}},"peer_regression":{"BOOK_SECTORS":["Energy","Financials","Materials","Real Estate","Utilities"],"BUY_PCT":0.2,"BUY_UPSIDE":0.15,"EV_SUB_INDUSTRIES":["Financial Exchanges & Data","Transaction & Payment Processing Services"],"MIN_FIT":12,"MIN_R2":0.2,"MODEL_VERSION":"peer_regression/0.2.0","SELL_PCT":0.8,"SELL_UPSIDE":-0.15,"STATUS":"diagnostic","WINSOR":[5,95]},"residual_income":{"ASSET_LIGHT_SECTORS":["Communication Services","Consumer Discretionary","Consumer Staples","Health Care","Industrials","Information Technology"],"BOOK_SECTORS":["Energy","Financials","Materials","Real Estate","Utilities"],"BUY_BLOCKED_IF_ROE_CLIPPED":true,"BUY_MOS":0.25,"CYCLICAL_SECTORS":["Energy","Materials"],"DOMINANT_SHARE":0.5,"ERP":0.05,"G_CAP":0.04,"HORIZON":10,"MODEL_VERSION":"residual_income/0.2.0","PB_MAX":8.0,"PB_MAX_ASSET_LIGHT":3.0,"RETENTION_DEFAULT":0.6,"ROE_CAP":0.35,"ROE_FLOOR":-0.1,"R_SHIFT":0.01,"SELL_MOS":-0.2,"SELL_SHOWN_AS":"HOLD"},"shareholder_yield":{"BUY_SY":0.05,"MAX_ABS_BBY":0.15,"MAX_PAYOUT":1.0,"MODEL_VERSION":"shareholder_yield/0.1.0","PREV_WINDOW_DAYS":[350,380],"SELL_SY":-0.03}}
```
**SHA-256 = `6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e`**

The implementation exposes this object (e.g. `valuation.models.FROZEN_V2`) built from the modules' own constants; a
QA test asserts its canonical SHA-256 equals the value above. Any difference blocks the holdout run. Spec text
(formulas, precedence, goldens) is frozen at the commit that merges this document; a later change to any of the five
specs is a new version that this protocol does not cover.

## 3. Harness additions (no change to the bar)
- Model `H`: `bt_rows` gains `h_signal, h_score (SY), h_dy, h_bby`; report model `H` (label `h_signal`, score SY
  where the label ∈ {BUY, HOLD, SELL}); diagnostics `H_BBY`, `H_DY` (IC only).
- Model A's backtest label is the **model** label `signal` (with SELL), not `screen_signal`.
- Model B rows are labelled "diagnostic"; its pass line is printed but never a verdict.
- The thresholds hash (`backtest.md` §2) gains `obj["shareholder_yield"]`; `bt_runs` also records the §2 hash.
- **Holdout guard:** `report --holdout` refuses if `bt_runs` already has a `kind='holdout'` row whose
  `model_version` contains any of the four frozen versions, or if the §2 hash doesn't match.

## 4. Holdout protocol
1. **Preconditions** (in order): specs merged; QA spec tests written before the code and green (incl. the §2 hash
   test); SW implementation merged; in-sample US backtest of the revised models run and reported
   (`docs/qa/backtest_v2_insample.md`) **without any change afterwards**, whatever it shows; PM sign-off.
2. **One run.** A single `valuation backtest run --market US` + `report --holdout` over all 154 dates covers A, B, C
   and H together (once per model). Holdout dates: the 36 month-ends 2023-11-30 … 2026-10-31 (`HOLDOUT_N`, today
   fixed at 2026-11-10 via `--today`, so the split matches Phase 8–9).
3. **No changes afterwards.** No parameter, spec or code change to any of the four models may be evaluated on these
   dates again. A bug found later that changes any label or score **voids** that model's holdout result (it stays
   unvalidated); a report-only bug may be fixed and the report regenerated from the stored rows.
4. **Verdict** per model = `pass_bar` (§4.8) unchanged: in-sample SPREAD_YEARS and IC legs plus the HOLDOUT sign leg.
   Reported next to it, information only: holdout mean IC (NW t), holdout mean spread, BUY/SELL hit rates, BUY excess
   vs SPX_TR. Results go to `docs/qa/holdout_v2.md` with the run id, git sha and both hashes.
5. **On fail:** the model stays **"unvalidated"** in the report; its signals keep the unvalidated disclaimer and are
   not used for ranking. The holdout is spent for every model in this run: no further revision may be validated on
   2023-11 … 2026-10. A later revision can only be tested on new out-of-sample month-ends (from 2026-11-30, ≥ 36 of
   them) or on data that removes the survivorship bias (P8-4) **plus** a fresh period.
6. **Phase 10 gate:** the vote is built only if **≥ 2 of {A 0.2.0, C 0.2.0, H 0.1.0} PASS**. Model B is diagnostic
   and never counts. With fewer than 2 passes Phase 10 stays blocked and the screen shows each model separately.
7. **Amendment 2026-11-10: pre-registered H confirmation test.** Added by the PM with user approval, **before any
   holdout-period result was computed or seen** (the holdout command had not been run; `docs/qa/holdout_v2.md` did
   not exist).
   - **Why:** the §4.4 bar's IC leg is evaluated in-sample, and A 0.2.0, C 0.2.0 and H 0.1.0 all already fail it
     there (`docs/qa/r2-insample.md`). The unchanged bar therefore cannot produce a PASS from this holdout run.
     Model H was the only model with positive in-sample BUY excess vs SPX_TR, so it gets a confirmation test on
     the holdout alone.
   - **Test (H 0.1.0 only, frozen parameters unchanged, same single run as §4.2):** H is **holdout-confirmed** if,
     over the holdout month-ends with a full 12-month forward return, **all three** hold:
     1. holdout mean 12m rank IC of `h_score` > 0 (the "Mean IC" holdout row, 12m);
     2. holdout mean 12m sector-neutral BUY − SELL spread > 0 (the "Mean spread" holdout row, 12m);
     3. holdout mean 12m BUY excess vs SPX_TR > 0 (the holdout benchmark-excess section, BUY bucket, 12m).

     No t-stat threshold; these are sign tests on data no one has seen. All three figures are already in the
     holdout report; QA reads them and records the result in `docs/qa/holdout_v2.md`.
   - **What it changes:** only H's status label. "Holdout-confirmed" is reported next to H's unchanged §4.4
     verdict, which stays as computed. A and C are judged by the unchanged bar only. B stays diagnostic.
   - **What it does not change:** the frozen parameters and their hash, the one-run rule (§4.2–4.3), the on-fail
     rule (§4.5), and the Phase 10 gate (§4.6). A holdout-confirmed H is one model, so Phase 10 stays blocked.
   - **Not run yet:** the user approved this test but asked that the holdout run wait for a separate go-ahead.

## 5. What this protocol does not do
It does not lower or reinterpret the bar, or pick the best of several variants: each model has exactly one frozen
variant. The only added criterion is the H confirmation test in §4.7, registered before the holdout was seen. The survivor-bias caveat applies to the holdout too (about 4% of member-dates missing in 2023–2026,
phase8 §3).
