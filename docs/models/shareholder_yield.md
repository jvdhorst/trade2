# Model spec: Shareholder yield (`shareholder_yield`, v0.1.0) — Model H

Owner: AN · Tests: QA (`tests/unit/test_shareholder_yield_spec.py`) · Code: `src/valuation/models/shareholder_yield.py` ·
Source: proposal `docs/models/proposals/additional_models.md` §5 (shortlist §12), revision round v2. Parameters frozen in
`revision_v2_freeze.md`. **No new data**: DPS_TTM, price, share counts, NI TTM and splits already exist.

## Purpose
*How much cash does the company hand back per unit of price, counting buybacks and net of issuance?* Dividends plus
net share reduction over a year. Evidence: Boudoukh, Michaely, Richardson & Roberts (2007) on payout yield; Pontiff &
Woodgate (2008) on net issuance predicting low returns. It is a different mechanism from A, B and C (capital
allocation, not book or earnings multiples), which matters after value failed in this sample: H is not tuned on it.

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote.

## Constants
`MODEL_VERSION = "shareholder_yield/0.1.0"`, `BUY_SY = 0.05`, `SELL_SY = -0.03`, `MAX_ABS_BBY = 0.15`,
`MAX_PAYOUT = 1.0`, `PREV_WINDOW_DAYS = (350, 380)`. All from the proposal; none chosen after any backtest.

## Inputs (`SYInputs`, one ticker at `as_of`)
| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`), `sector` | as Model A (sector informational; all sectors apply) | |
| `eligible` | eligibility E1–E5; anything but `True` → `NOT_ELIGIBLE` | |
| `price` | close on/before as_of, trading ccy; must be > 0 (else `ValueError`) | |
| `dps_ttm` | `ttm.dps_ttm` (split-adjusted, trading ccy) | 0.0 |
| `shares_t` | all-class shares (after `share_conversion`) of the balance-sheet filing behind `bvps` | None |
| `shares_prev` | same source, filing ≈ 1 year earlier (Data mapping) | None |
| `split_factor` | product of split ratios dated in (`period_end_prev`, `period_end_t`]; puts `shares_prev` on today's basis; must be > 0 | 1.0 |
| `ni_common_ttm`, `fx_rate` | as Model A (reporting ccy; trading-ccy units per reporting unit, > 0); NI None on CCY_MISMATCH | None, 1.0 |
| `flags` | passed through (`PIT_ESTIMATED`, `NO_TTM`, …) | frozenset() |

## Formulas
```
shares_prev_adj = shares_prev × split_factor
DY      = dps_ttm / price
BBY     = (shares_prev_adj − shares_t) / shares_prev_adj         net buyback yield (negative = net issuance)
SY      = DY + BBY                                               continuous score (backtest IC)
EPS_TTM = ni_common_ttm / shares_t × fx_rate                     (None if NI None)
payout  = dps_ttm / EPS_TTM                                      (None if EPS_TTM None or ≤ 0)
funded  = EPS_TTM is not None and EPS_TTM > 0 and payout ≤ MAX_PAYOUT
map_signal(sy, funded): "BUY" if sy ≥ 0.05 and funded; elif "SELL" if sy ≤ −0.03; else "HOLD"
sy_pct  = (rank − 1)/(n − 1) of SY among the market's eligible names with a SY (average ranks; informational only)
```
Proposal v1 `funded` (payout of earnings) is kept: the FCF version needs buyback cash, which is new data.

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` | `eligible is not True` |
| 2 | `NO_SIGNAL` / `NO_SHARES` | `shares_t` or `shares_prev` None or ≤ 0 |
| 3 | `NO_SIGNAL` / `CORPORATE_ACTION` | `abs(BBY) > 0.15` (merger, spin-off, recapitalisation, unrecorded split); 0.15 exactly passes |
| 4 | `map_signal(SY, funded)` | |

Flags: `NOT_FUNDED` (SY ≥ 0.05 but not funded, so HOLD), `SPLIT_ADJUSTED` (`split_factor ≠ 1`), `NO_TTM` (NI None;
BUY impossible, SELL possible), pass-through flags. Outputs (DY, BBY, SY, EPS, payout) are computed whenever their
inputs exist, also on NOT_ELIGIBLE / CORPORATE_ACTION.

## Worked examples (golden values; relative tolerance 1e-9)
**G1 (proposal).** price 80, dps 2.40, shares_prev 1,050e6 → shares_t 1,008e6, NI 4,200e6, fx 1 → DY **0.03**, BBY
**0.04**, SY **0.07**, EPS **4.166666666666667**, payout **0.576** → funded → **BUY**.

| Case | Inputs (fx 1, split 1 unless stated) | DY | BBY | SY | EPS / payout | Signal |
|---|---|---|---|---|---|---|
| G2 issuer | price 50, dps 0, 200e6 → 212e6, NI 300e6 | 0.0 | **−0.06** | −0.06 | 1.4150943396226414 / 0.0 | **SELL** |
| G3 corp. action | price 50, dps 0, 200e6 → 240e6 | 0.0 | **−0.2** | −0.2 | | **NO_SIGNAL / CORPORATE_ACTION** |
| G4 BUY edge | price 100, dps 2.0, 1000e6 → 970e6, NI 5000e6 | 0.02 | 0.03 | **0.05** | 5.154639175257732 / 0.38799999999999996 | **BUY** (≥) |
| G5 loss | price 100, dps 3.0, 1000e6 → 980e6, NI −100e6 | 0.03 | 0.02 | 0.05 | −0.10204081632653061 / None | HOLD + NOT_FUNDED |
| G6 payout > 1 | price 100, dps 6.0, 1000e6 → 980e6, NI 5000e6 | 0.06 | 0.02 | 0.08 | 5.1020408163265305 / **1.176** | HOLD + NOT_FUNDED |
| G7 BBY edge | price 40, dps 0, 1000e6 → 850e6, NI 1e9 | 0.0 | **0.15** | 0.15 | 1.1764705882352942 / 0.0 | **BUY** (0.15 passes) |
| G7b | same, → 849e6 | 0.0 | 0.151 | 0.151 | | NO_SIGNAL / CORPORATE_ACTION |
| G8 split | price 40, dps 0.4, shares_prev 500e6, split_factor 2.0, shares_t 980e6, NI 1e9 | 0.01 | **0.02** | 0.03 | 1.0204081632653061 / 0.392 | HOLD + SPLIT_ADJUSTED |
| G9 FX (TSX, USD reporter) | price 30.0 CAD, dps 1.12, 5.0e8 → 4.9e8, NI 1.0e9 USD, fx 1.40 | 0.037333333333333336 | 0.02 | 0.05733333333333333 | **2.857142857142857** / 0.392 | **BUY** |
| G10 SELL edge | price 50, dps 0, 1000e6 → 1030e6, NI 1e9 | 0.0 | **−0.03** | −0.03 | 0.970873786407767 / 0.0 | **SELL** (≤) |

G8 without the split factor would give BBY −0.96 → CORPORATE_ACTION: the split adjustment is required. Also:
shares_prev None → NO_SHARES; eligible None → NOT_ELIGIBLE (outputs still computed); `map_signal(0.0499, True) = HOLD`,
`(−0.0299, False) = HOLD`, `(0.06, False) = HOLD`.

## Property expectations
- Scaling price and dps by k leaves DY unchanged; scaling both share counts by k leaves BBY unchanged; changing the
  reporting currency with a matching fx leaves EPS and the signal unchanged.
- SY is monotone: raising DY or BBY never moves BUY → HOLD/SELL or HOLD → SELL (funded fixed).
- A name with NI None is never BUY.

## Data mapping and look-ahead (engine, SW)
| Input | Source |
|---|---|
| shares_t, period_end_t | the filing `build_inputs` selected for `bvps` (filed ≤ as_of) |
| shares_prev | **same source as shares_t** (EDGAR or Yahoo, never mixed): the filing with the latest `period_end` in [`period_end_t` − 380, `period_end_t` − 350] days with `filed_date` ≤ as_of and shares > 0; equal period_end → later filed. None if none |
| split_factor | `engine.split_factor(con, ticker, period_end_prev, period_end_t)` (backtest `splits` table; 1.0 in the live DB, so a live split year lands in CORPORATE_ACTION, recorded limitation) |
| dps_ttm, ni_common_ttm, fx_rate, eligible, market | as Model A |
Known gaps: multi-class EDGAR filers without a share count (META, …) → NO_SHARES in the backtest; TSX Yahoo history is
~5 quarters, so shares_prev exists only for the last ~1 quarter of TSX history.

## Backtest and report
- Report: `screen_signals` adds `h_signal, h_no_signal_reason, h_sy, h_dy, h_bby, h_payout, h_sy_pct`; flags join `flags`.
- Backtest (harness addition in `revision_v2_freeze.md` §3): model `H`, label `h_signal`, score `SY` where the label is
  BUY/HOLD/SELL. Pass bar: the unchanged `backtest.md` §4.8. Diagnostics (not a verdict): IC of `BBY` alone and of `DY`
  alone. The proposal's check: if only DY has a positive IC, H adds nothing beyond a dividend screen.

## Risks
Buybacks at high prices destroy value and the model can't tell; acquisitions paid in shares are (correctly) negative
even when good; bank buybacks are regulator-gated. *Overfitting risk:* **low**: all constants come from the proposal
(written before any backtest); H has never been run, so its in-sample result is a genuine first test. Residual risk:
the shortlist was chosen after A and B had failed, though not on H's own results.

## Changelog
- **0.1.0:** initial spec from proposal §5 (revision round v2). Proposal golden reproduced (DY 0.03, BBY 0.04, SY 0.07,
  payout 0.576 → BUY); split adjustment, same-source rule and boundary goldens added; arithmetic verified with a
  throwaway script.
