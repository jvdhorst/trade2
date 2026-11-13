# Model spec: Dividend sustainability and implied return (`dividend_sustainability`, v0.1.0) — Model D

Owner: AN · Tests: QA (`tests/unit/test_dividend_sustainability_spec.py`,
`tests/unit/test_dividend_sustainability_engine_spec.py`) · Code: `src/valuation/models/dividend_sustainability.py` ·
Source: proposal `docs/models/proposals/additional_models.md` §1 (data gaps §13). **Not part of FROZEN_V2**: D is not in
`revision_v2_freeze.md` and adding it must not change that freeze's hash. **No new data**: DPS (`dividends`), NI TTM,
shares, fx, price, rf and Model A's `roe_raw` already exist.

## Purpose
*Do earnings cover the dividend, and does the dividend yield plus a sustainable growth rate beat the cost of equity?*
For a dividend payer the dividend is the most checkable cash number there is. D turns it into an implied return
(Gordon: `P = D1 / (k − g)` solved for `k`) and compares it with `r = rf + 5%`. A dividend larger than earnings is a
SELL on its own. Evidence: Gordon (1962); payout and coverage screens are commonly cited; Arnott & Asness (2003) find
that high payout ratios preceded *higher*, not lower, earnings growth. D is per name (no cross-section, no universe
size); each name is evaluated alone.

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote. D has no vote.

## Constants
`MODEL_VERSION = "dividend_sustainability/0.1.0"`, `ERP = 0.05`, `G_FLOOR = 0.0`, `G_CAP = 0.06`,
`DIV_CUT_G = -0.02`, `BUY_SPREAD = 0.02`, `SELL_SPREAD = -0.02`, `MAX_PAYOUT_BUY = 0.75`, `MAX_PAYOUT = 1.0`,
`MIN_EX_DATES = 2`, `EXCLUDED_SECTORS = frozenset({"Real Estate"})`. All are starting values from proposal §1
(the ±2pp spread, the 0.75 and 1.0 payout bounds, the 6% growth cap, the −2% cut threshold, 2 ex-dates, the 5% ERP),
**never tuned on the holdout** (PM decision 2026-11-09), and D has never been backtested, so none was chosen after a
result. Engine-only constants (not model parameters): `PREV_DAYS = 365`, `COVERAGE_GRACE_DAYS = 7` (Data mapping).

## Inputs (`DInputs`, frozen dataclass, one ticker at `as_of`)
| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`) | as Model A | |
| `sector` | GICS sector, `str \| None`; drives the exclusion (exact, case-sensitive `"Real Estate"`) | |
| `eligible` | eligibility E1–E5 (`bool \| None`); anything but `True` → `NOT_ELIGIBLE` | |
| `price` | close on/before as_of, trading ccy; must be a number > 0 (None or ≤ 0 → `ValueError`) | |
| `dps_ttm` | sum of dividends with ex_date in (as_of − 365, as_of], trading ccy, on the as_of share basis; None = dividend history does not cover the window | None |
| `dps_ttm_prev` | the same sum for (as_of − 730, as_of − 365], **on the as_of share basis**; None = not covered | None |
| `n_ex_dates` | number of distinct ex-dates in (as_of − 365, as_of]; `int ≥ 0` (negative → `ValueError`) | 0 |
| `ni_common_ttm` | reporting ccy; None on NO_TTM or CCY_MISMATCH (as Model A / H) | None |
| `shares_outstanding` | all classes × conversion × split factor to the price date (the `build_inputs` count) | None |
| `fx_rate` | trading-ccy units per reporting unit; must be > 0 (else `ValueError`) | 1.0 |
| `roe_raw` | Model A's `roe_raw` (unclipped; None when avg equity ≤ 0 or NI None) | None |
| `rf` | 10-year yield for the market, decimal (0.035 = 3.5%); None → `NO_RF` | None |
| `flags` | passed through (`PIT_ESTIMATED`, `NO_TTM`, `STALE_TTM`, `ROE_SPOT_EQUITY`, `CSM_ADJUSTED`) | frozenset() |

No monetary input is ever defaulted to 0 by the model. `dps_ttm = 0.0` is a real observation (no ex-date in a
covered window), distinct from None (window not covered).

`DResult`: `ticker, market, signal, no_signal_reason, eps, payout, roe0, g_raw, g_sust, g_hist, dy, k, r, spread,
v_ddm, mos, flags`. API: `compute_metrics(inp) -> DMetrics(eps, payout, roe0, g_raw, g_sust, g_hist, dy, k, r, spread,
v_ddm, mos, div_cut)`; `map_signal(payout, spread, div_cut) -> "BUY" | "HOLD" | "SELL"` (both floats not None);
`evaluate(inp) -> DResult`; `run_dividend_sustainability(inputs: Sequence[DInputs]) -> list[DResult]` (one result per
input, same order, each identical to evaluating it alone). `compute_metrics` and `evaluate` raise the `ValueError`s
above.

## Formulas (per name)
**Currency.** EPS is put in the trading currency (`NI / shares × fx`, as Model H), so `payout` and `DY` compare
trading-ccy DPS with trading-ccy EPS and price. `roe_raw` is a ratio within one filing currency and needs no fx.

**Evaluation order** (each line exactly as written, left to right, so float goldens reproduce bit for bit; an output is
None when any input it needs is None):
```
eps     = ni_common_ttm / shares_outstanding * fx_rate          None if ni None, shares None or shares ≤ 0
payout  = dps_ttm / eps                                          None if dps None, eps None or eps ≤ 0
roe0    = roe_raw                                                unclipped pass-through
g_raw   = roe0 * (1.0 - payout)
g_sust  = min(max(g_raw, G_FLOOR), G_CAP)                        flag G_CAPPED if g_raw > 0.06, G_FLOORED if g_raw < 0.0
g_hist  = dps_ttm / dps_ttm_prev - 1.0                           None if either is None or dps_ttm_prev ≤ 0
div_cut = g_hist is not None and g_hist < DIV_CUT_G              flag DIV_CUT when True
dy      = dps_ttm / price
k       = dy * (1.0 + g_sust) + g_sust                           implied return
r       = rf + ERP
spread  = k - r                                                  decimal (0.02 = 2pp)
v_ddm   = dps_ttm * (1.0 + g_sust) / (r - g_sust)                None if r - g_sust ≤ 0; informational only
mos     = v_ddm / price - 1.0                                    informational only
```
Clipping: equality at a bound is not clipped (`g_raw == 0.06` → no `G_CAPPED`; `g_raw == 0.0` → no `G_FLOORED`).
With `eps > 0` and `roe_raw > 0` (NI > 0 and avg equity > 0 imply it), `g_raw < 0` happens only when `payout > 1`, so
`G_FLOORED` names are always SELL by the payout rule.

**`dps_ttm_prev ≤ 0` (decision beyond the proposal).** `g_hist` is None, `div_cut` False, and flag `NEW_PAYER` is set
when `dps_ttm > 0` (the name paid nothing a year ago). `NEW_PAYER` does not block BUY: the 2-ex-date rule and the
payout cap are the guards. `dps_ttm_prev` None → `g_hist` None, `div_cut` False, flag `NO_DIV_HISTORY` (also never
blocks BUY; recorded limitation: a cut cannot be seen without history).

**V_DDM and MoS are informational only** and never read by `map_signal` or the precedence table: with `r − g` near
3%, a 1pp change in `g` moves `V_DDM` by ~30% but `k` by only ~1pp (proposal). `v_ddm` is None when `r ≤ g_sust`
(possible only when `rf < 0.01`).

Metrics are computed whenever their inputs exist, also for `NOT_ELIGIBLE`, excluded and other `NO_SIGNAL` names (as J
and G). Nothing is filled in.

```
map_signal(payout, spread, div_cut):
    "SELL" if payout > MAX_PAYOUT or spread <= SELL_SPREAD
    "BUY"  if spread >= BUY_SPREAD and payout <= MAX_PAYOUT_BUY and not div_cut
    "HOLD" otherwise
```
SELL is tested first (a name with payout > 1 is SELL whatever its spread). Plain float comparisons in decimals, no
epsilon: `spread` is compared with the floats `0.02` and `-0.02`, never with a value in pp. Note
`0.035 + 0.05 == 0.085` exactly in IEEE (verified), so `r` in the goldens is the literal 0.085. A spread computed
from inputs that "should" land on 0.02 usually does not (G13); boundary goldens are therefore on `map_signal`
directly with given floats (G12).

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` (reason None) | `eligible is not True` |
| 2 | `NO_SIGNAL` / `EXCLUDED_SECTOR` | sector None or empty, or in `EXCLUDED_SECTORS` (Real Estate: payout against EPS is meaningless under depreciation; Model L2 later) |
| 3 | `NO_SIGNAL` / `NO_DATA` | `dps_ttm` is None |
| 4 | `NO_SIGNAL` / `NO_DIVIDEND` | `dps_ttm ≤ 0` |
| 5 | `NO_SIGNAL` / `IRREGULAR_DIV` | `n_ex_dates < 2` (a special dividend only) |
| 6 | `NO_SIGNAL` / `NO_DATA` | `ni_common_ttm` None, or `shares_outstanding` None or ≤ 0 |
| 7 | `NO_SIGNAL` / `NEG_EARNINGS` | `eps ≤ 0` (payout undefined) |
| 8 | `NO_SIGNAL` / `NO_ROE` | `roe_raw` is None |
| 9 | `NO_SIGNAL` / `NO_RF` | `rf` is None |
| 10 | `map_signal(payout, spread, div_cut)` | above |

`dps_ttm_prev` is **not** required for a signal. **Financials are included** (banks and insurers have meaningful
payout and ROE; for insurers `roe_raw` is Model A's CSM-adjusted value). Bank payout is regulator-capped, so
`payout ≤ 0.75` is almost always true for them (Risks).

**Flags** (set whenever the condition holds, whatever the signal, unless stated):
`G_CAPPED`, `G_FLOORED` (g_raw computed and outside [0, 0.06]); `DIV_CUT` (`div_cut` True); `NEW_PAYER`,
`NO_DIV_HISTORY` (as above); `NEG_EARNINGS` (`eps` not None and ≤ 0); `IRREGULAR_DIV` (`dps_ttm > 0` and
`n_ex_dates < 2`); `BUY_BLOCKED_PAYOUT` and `BUY_BLOCKED_DIV_CUT` (only when row 10 is reached and the signal is HOLD
with `spread ≥ 0.02`: the first when `payout > 0.75`, the second when `div_cut`; both may be set); plus the
pass-through flags.

## Worked examples (golden values; relative tolerance 1e-9)
Unless stated: market `"US"`, sector `"Consumer Staples"`, eligible True, fx 1.0, shares_outstanding 100e6,
n_ex_dates 4, rf 0.035 (so r = 0.085). Full float reprs from a throwaway script using exactly the evaluation order
above. Money inputs: price, dps_ttm, dps_ttm_prev in trading ccy per share; NI in reporting ccy.

**G1 (proposal golden).** price 45.0, dps_ttm 2.0, dps_ttm_prev 1.9, NI 400e6, roe_raw 0.16 (= 400 / 2,500) →

| Step | Value (float repr) |
|---|---|
| eps | 4.0 |
| payout | 0.5 |
| roe0 | 0.16 |
| g_raw | 0.08 → g_sust **0.06** (`G_CAPPED`) |
| g_hist | 0.05263157894736836 (no cut) |
| dy | 0.044444444444444446 |
| k | 0.10711111111111112 |
| r | 0.085 |
| spread | **0.02211111111111111** (+2.2111pp) |
| v_ddm, mos | 84.79999999999998 (proposal 84.80); 0.8844444444444441 (informational) |
| **Signal** | **BUY** (spread ≥ 0.02, payout 0.5 ≤ 0.75, no cut); flags {`G_CAPPED`} |

| Case | Change from G1 | Key outputs (float repr) | Signal, flags |
|---|---|---|---|
| G2 | price 50.0 | dy 0.04, k 0.10239999999999999, spread 0.017399999999999985 (+1.74pp), v_ddm 84.79999999999998, mos 0.6959999999999997 | **HOLD**; `G_CAPPED` |
| G3 | NI 180e6, roe_raw 0.072 (EPS 1.80) | eps 1.8, payout 1.1111111111111112, g_raw −0.008000000000000004 → g_sust 0.0, k 0.044444444444444446, spread −0.04055555555555556, v_ddm 23.52941176470588, mos −0.47712418300653603 | **SELL**; `G_FLOORED` |
| G3b | G3 with price 15.0 | payout 1.1111111111111112, g_sust 0.0, dy 0.13333333333333333, k 0.13333333333333333, spread **0.048333333333333325** (≥ 0.02), v_ddm 23.52941176470588, mos 0.5686274509803921 | **SELL** whatever the spread (payout > 1 first); `G_FLOORED`; no `BUY_BLOCKED_*` (not HOLD) |
| G4 DIV_CUT | dps_ttm_prev 2.1 | g_hist −0.04761904761904767 < −0.02, spread 0.02211111111111111 (as G1) | **HOLD**; `G_CAPPED`, `DIV_CUT`, `BUY_BLOCKED_DIV_CUT` |
| G5 spread SELL, uncapped | price 100.0, NI 250e6, roe_raw 0.1 | eps 2.5, payout 0.8, g_raw = g_sust 0.019999999999999997, dy 0.02, k 0.0404, spread **−0.04460000000000001**, v_ddm 31.384615384615383, mos −0.6861538461538461 | **SELL** (spread ≤ −0.02); no flags |
| G6 uncapped BUY | price 25.0, dps 1.5, prev 1.4, NI 250e6, roe_raw 0.12 | eps 2.5, payout 0.6, g_raw = g_sust 0.048, g_hist 0.0714285714285714, dy 0.06, k 0.11088, spread 0.02588, v_ddm 42.486486486486484, mos 0.6994594594594594 | **BUY**; no flags |
| G7 payout block | price 25.0, NI 250e6, roe_raw 0.2 | payout 0.8, g_sust 0.039999999999999994, dy 0.08, k 0.1232, spread 0.0382, v_ddm 46.222222222222214, mos 0.8488888888888886 | **HOLD**; `BUY_BLOCKED_PAYOUT` |
| G8 payout = 1 | dps 4.0, prev 3.8 | payout 1.0 (not > 1), g_raw 0.0 → g_sust 0.0 (no `G_FLOORED`), dy 0.08888888888888889, k 0.08888888888888889, spread 0.003888888888888886, v_ddm 47.05882352941176, mos 0.04575163398692794 | **HOLD**; no flags |
| G9 g on the cap | dps 1.5, prev 1.4, NI 300e6, roe_raw 0.12 | eps 3.0, payout 0.5, g_raw 0.06 (== cap, no `G_CAPPED`), dy 0.03333333333333333, k 0.09533333333333333, spread 0.01033333333333332, v_ddm 63.59999999999998, mos 0.4133333333333329 | **HOLD**; no flags |
| G10 FX | market `"TSX"`, USD reporter: price 63.0 CAD, dps 2.8, prev 2.66, NI 400e6 USD, fx 1.4, roe_raw 0.16, rf 0.035 | eps 5.6, payout 0.5, g_sust 0.06, g_hist 0.05263157894736836, dy 0.04444444444444444, k 0.1071111111111111, spread 0.022111111111111095, v_ddm 118.71999999999996, mos 0.8844444444444437 | **BUY**; `G_CAPPED` (same as G1) |
| G11 V_DDM undefined | rf 0.005 | r 0.055, g_sust 0.06 > r → v_ddm None, mos None; k 0.10711111111111112, spread 0.052111111111111115 | **BUY**; `G_CAPPED` (V_DDM never gates) |
| G14 NEW_PAYER | dps_ttm_prev 0.0 | g_hist None, other metrics as G1 | **BUY**; `G_CAPPED`, `NEW_PAYER` |
| G15 no history | dps_ttm_prev None | g_hist None, other metrics as G1 | **BUY**; `G_CAPPED`, `NO_DIV_HISTORY` |

**G12 boundaries on `map_signal` directly** (given floats): `(0.75, 0.02, False)` = BUY;
`(0.7500001, 0.03, False)` = HOLD; `(0.5, 0.0199999, False)` = HOLD; `(0.5, 0.02, True)` = HOLD;
`(1.0, 0.05, False)` = HOLD (1.0 is not > 1.0, but > 0.75); `(1.0000001, 0.05, False)` = SELL;
`(0.5, -0.02, False)` = SELL; `(1.0, -0.0199999, False)` = HOLD; `(1.5, 0.05, True)` = SELL.

**G13 IEEE note (fragile constructed spread).** G1 with price 40.0, rf 0.043: dy 0.05, k 0.113, r 0.093 (exact
`0.043 + 0.05`), spread **0.020000000000000004** (not 0.02) → BUY. A test may use this case only with this exact repr;
it does not test the `≥` boundary (G12 does).

**G16 DIV_CUT boundary (IEEE).** G1 with price 20.0, dps_ttm_prev 1.0:
| dps_ttm | g_hist | payout, dy, k, spread | Signal, flags |
|---|---|---|---|
| 0.98 | **−0.020000000000000018** (< −0.02: a nominal −2% *is* a cut in IEEE) | 0.245, 0.049, 0.11194000000000001, 0.026940000000000006 | **HOLD**; `G_CAPPED`, `DIV_CUT`, `BUY_BLOCKED_DIV_CUT` |
| 0.99 | −0.010000000000000009 | 0.2475, 0.0495, 0.11247, 0.027469999999999994 | **BUY**; `G_CAPPED` |
| 0.97 | −0.030000000000000027 | 0.2425, 0.0485, 0.11141000000000001, 0.026410000000000003 | **HOLD**; `G_CAPPED`, `DIV_CUT`, `BUY_BLOCKED_DIV_CUT` |

**G17 precedence.** Base = G1; one change each. Metrics follow the Formulas section in every case.
| Case | Change | Result |
|---|---|---|
| a | eligible False, sector "Real Estate" | `NOT_ELIGIBLE` (reason None); metrics as G1 (spread 0.02211111111111111); eligible None likewise |
| b | sector "Real Estate" | `NO_SIGNAL / EXCLUDED_SECTOR`; metrics as G1 |
| c | sector None (or "") | `EXCLUDED_SECTOR` |
| d | dps_ttm None, sector "Real Estate" | `EXCLUDED_SECTOR` (row 2 before 3) |
| e | dps_ttm None | `NO_DATA`; payout, g_raw, g_sust, g_hist, dy, k, spread, v_ddm, mos None; eps 4.0, r 0.085; flag `NO_DIV_HISTORY` not set (prev given) |
| f | dps_ttm 0.0, n_ex_dates 0 | `NO_DIVIDEND`; payout 0.0, g_raw 0.16 → g_sust 0.06, g_hist −1.0, dy 0.0, k 0.06, spread −0.02500000000000001, v_ddm 0.0, mos −1.0; flags `G_CAPPED`, `DIV_CUT` (dividend eliminated) |
| g | dps_ttm 0.0, n_ex_dates 0, NI None | `NO_DIVIDEND` (row 4 before 6) |
| h | n_ex_dates 1 | `IRREGULAR_DIV`; flags `IRREGULAR_DIV`, `G_CAPPED`; metrics as G1 |
| i | n_ex_dates 1, NI −50e6, roe_raw −0.02 | `IRREGULAR_DIV` (row 5 before 7); flags `IRREGULAR_DIV`, `NEG_EARNINGS` |
| j | NI None (roe_raw None) | `NO_DATA`; eps, payout, g_*, k, spread, v_ddm, mos None; dy 0.044444444444444446, g_hist 0.05263157894736836, r 0.085 |
| k | shares_outstanding None (or 0) | `NO_DATA`; eps None |
| l | NI −50e6, roe_raw −0.02 | `NEG_EARNINGS` + flag; eps −0.5, payout None, g_sust, k, spread None; dy 0.044444444444444446 |
| m | NI 0.0, roe_raw 0.0 | `NEG_EARNINGS` + flag; eps 0.0 |
| n | roe_raw None | `NO_ROE`; payout 0.5, g_raw, g_sust, k, spread, v_ddm, mos None |
| o | roe_raw None, rf None | `NO_ROE` (row 8 before 9) |
| p | rf None | `NO_RF`; g_sust 0.06, k 0.10711111111111112, r, spread, v_ddm, mos None; flag `G_CAPPED` |
| q | sector "Financials" | **BUY** as G1 (Financials included) |
| r | market "LSE"; price 0 or None; fx_rate 0; n_ex_dates −1 | `ValueError` |
`run_dividend_sustainability([G1, G3, G17e, G17a])` returns four results in that order, each identical to `evaluate`.

## Property expectations
- BUY ⇒ `spread ≥ 0.02`, `payout ≤ 0.75`, no `DIV_CUT`. SELL ⇒ `payout > 1` or `spread ≤ −0.02`. `G_FLOORED` ⇒ SELL
  (when row 10 is reached).
- `0 ≤ g_sust ≤ 0.06` whenever computed; `k ≥ dy` whenever computed.
- Scaling price, dps_ttm, dps_ttm_prev **and** NI by c > 0 (roe_raw, shares, rf fixed) leaves payout, g_raw, g_sust,
  g_hist, dy, k, spread, mos and the signal unchanged (eps and v_ddm scale by c); changing the reporting currency with
  a matching `fx_rate` (NI × fx fixed, since eps = NI / shares × fx) leaves every output unchanged.
- Lowering price (all else fixed) never moves BUY → HOLD/SELL or HOLD → SELL (payout and div_cut do not depend on price;
  k rises). Raising rf never moves SELL → HOLD/BUY or HOLD → BUY.
- Replacing `v_ddm`/`mos` by anything never changes a signal (they are not read).
- A name's result depends only on its own inputs. An ex-date after as_of never changes any result (engine).

## Data mapping and look-ahead (engine, SW)
| Input | Source |
|---|---|
| price, shares_outstanding, fx_rate | `build_inputs` (price close on/before as_of; shares all classes × `share_conversion` × split factor to the price date; balance-sheet filing `filed_date ≤ as_of`; TSX → `PIT_ESTIMATED`) |
| ni_common_ttm | as Model H: `flow_ttm(ni_common_q)` value, None when the NI currency ≠ reporting currency (Model A's CCY_MISMATCH); NO_TTM → None |
| roe_raw | Model A's result `roe_raw` for the same ticker and as_of (raw TTM NI, not one-off-normalised; CSM-adjusted equity for insurers; spot CE_end with `ROE_SPOT_EQUITY` when CE_start is missing) |
| dps_ttm | `engine.dps_ttm(con, t, as_of)` (ex_date in (as_of − 365, as_of], each amount restated by splits in (ex_date, as_of]) |
| dps_ttm_prev | `engine.dps_ttm(con, t, as_of − 365 days) / engine.split_factor(con, t, as_of − 365 days, as_of)`. The division is required: `dps_ttm` at `as_of − 365` is on that date's share basis, and a split in between would otherwise read as a cut (G-E3). |
| n_ex_dates | `SELECT count(DISTINCT ex_date) FROM dividends WHERE ticker = ? AND ex_date > as_of − 365 AND ex_date <= as_of` |
| coverage guard | `first_bar` = earliest `prices_daily.date` for the ticker (dividends come from the same chart download). `first_bar > as_of − 358 days` (365 − `COVERAGE_GRACE_DAYS`) → `dps_ttm` None (and `n_ex_dates` 0). `first_bar > as_of − 723 days` (730 − 7) → `dps_ttm_prev` None. The 7-day grace absorbs a history that starts on the first trading day after the download's range start (weekends, holidays); the live 2-year backfill (`HISTORY_BACKFILL_RANGE = "2y"`) therefore normally covers the prior window. Without the guard an uncovered window reads as 0 and would fake `NO_DIVIDEND`, `NEW_PAYER` or an understated DPS. |
| rf | `risk_free` per market (TSX CA10Y, US US10Y) on/before as_of, decimal; None → `NO_RF` |
| eligible, market, sector | as Model A (E1–E5) |
| flags | `a.flags ∩ {PIT_ESTIMATED, NO_TTM, STALE_TTM, ROE_SPOT_EQUITY, CSM_ADJUSTED}` |
SW records `d_dps_ttm`, `d_dps_ttm_prev`, `d_n_ex_dates`, `d_eps`, `d_first_bar` in the per-ticker `details` audit
record (as H's `h_shares_t`), not as report columns.

**Look-ahead.** Every input is known on as_of: dividends with `ex_date ≤ as_of` (an ex-date after as_of is never in
`dps_ttm` or `n_ex_dates`, even if declared earlier); filings with `filed_date ≤ as_of`; price and rf on/before as_of.
Amounts are Yahoo's per-share values in the trading currency; Yahoo restates them for splits up to the download date,
which the backtest undoes (`backtest/data.py`) and the engine re-applies through as_of only.

**Engine goldens** (QA builds the DuckDB fixtures; ticker US, first price bar 2024-11-01, splits empty unless stated).
- **G-E1.** Dividends (ex_date, amount): 2025-01-27 1.54, 2025-04-24 1.54, 2025-07-28 1.54, 2025-10-27 1.54,
  2026-01-26 1.64, 2026-04-23 1.64, 2026-07-27 1.76, 2026-10-27 1.76, 2026-11-20 1.80. At as_of **2026-11-09**:
  dps_ttm **6.8** (1.64 + 1.64 + 1.76 + 1.76; the 2026-11-20 ex-date is after as_of and excluded), n_ex_dates 4,
  dps_ttm_prev **6.16** (window (2024-11-09, 2025-11-09]), g_hist 0.10389610389610393. At as_of **2026-11-20** (the
  ex-date on as_of counts): dps_ttm **8.6**, n_ex_dates **5** (a drifting quarterly calendar can put 5 payments in
  365 days, Risks), dps_ttm_prev 6.16, g_hist 0.39610389610389607.
- **G-E2 coverage.** G-E1 with first price bar 2024-12-02, as_of 2026-11-09: as_of − 723 = 2024-11-16 < 2024-12-02 →
  dps_ttm_prev None, flag `NO_DIV_HISTORY`; as_of − 358 = 2025-11-16 ≥ first bar → dps_ttm 6.8 as before.
- **G-E3 split.** As-traded dividends: 2024-12-02, 2025-03-03, 2025-06-02, 2025-09-02, 2025-12-01, 2026-03-02 at 1.00;
  2026-06-01, 2026-09-01 at 0.50; split ratio 2.0 dated 2026-05-01. At as_of 2026-11-09: dps_ttm 2.0 (0.5 + 0.5 +
  0.5 + 0.5 after restating the two pre-split amounts), n_ex_dates 4, `dps_ttm(2025-11-09)` 4.0, split_factor 2.0 →
  dps_ttm_prev 2.0, g_hist 0.0, no `DIV_CUT`. Without the division g_hist would be −0.5 (a false cut).

## Report and backtest
- Report: `screen_signals` adds `d_signal, d_no_signal_reason, d_payout, d_g_sust, d_g_hist, d_dy, d_k, d_spread,
  d_v_ddm, d_mos` after the `g_*` columns and before `flags` (D first, then E, then F); D flags join `flags`. `r` is not
  a column (it is `rf + 0.05`, and `k − spread`); `eps` and `roe0` are in `DResult` and the details record. The
  disclaimer lists `dividend_sustainability/0.1.0` and adds "Model D is new and unvalidated (not backtested)." D has
  no vote.
- Backtest: **deferred** (user decision): the D/E/F backtests are to be run locally by the user later. D is in no
  backtest run, in-sample or holdout. The proposal's hypothesis is recorded for a later pre-registration only: among
  payers, a positive BUY − SELL 12-month spread of 2–4%/yr, with the SELL side (payout > 1) doing most of the work; IC
  vs 12-month return 0.02–0.04; pass bar proposal §6 (spread > 0 in ≥ 60% of years, IC > 0.03, t > 2); specific check:
  the `payout > 1` SELL bucket should show a higher subsequent dividend-cut rate than the rest, else the coverage rule
  is not informative.
- Per-company fit ([`per_company_fit.md`](per_company_fit.md) v0.1.0, user decision 2026-11-13): D's label is now stored in the **in-sample** backtest (`bt_rows.d_signal`, labelled unvalidated) and evaluated per company (SELL_EXIT and BUY_ONLY); never on holdout dates. Label → position mapping in that spec §3.3. No parameter changes.
- External checks (QA, `docs/qa/models_def_validation.md`): for 2–3 US names, NI TTM and shares vs the 10-K/10-Q and
  DPS_TTM vs declared dividends per share (`CommonStockDividendsPerShareDeclared`, ±2%); `dy` vs Yahoo
  `trailingAnnualDividendYield` (±5%; Yahoo lags one payment); `payout` vs Yahoo `payoutRatio` (±10%).

## Risks
`g_sust` comes from TTM ROE, so it inherits Model A's one-off-NI problem (no normalisation in D 0.1.0). Yield traps: a
high `dy` from a falling price; `DIV_CUT` only sees cuts that already happened, and only with covered history. A
365-day window can hold 3 or 5 payments when ex-dates drift (G-E1), moving dps_ttm by ±25% for quarterly payers;
semi-annual payers with a drifting date can drop to 1 ex-date → `IRREGULAR_DIV`. USD-declared dividends on `.TO`
listings arrive in CAD from the chart (consistent with the price). Banks' payout is regulated, so the payout cap rarely
binds for them and their BUY/SELL rests on the spread alone; insurer ROE depends on the CSM add-back. `g_hist` over
one year only (3-year history needs the §13 dividend backfill, backlog). *Overfitting risk:* **low**: every constant
comes from the proposal, written before any backtest, and D has never been run on returns. Residual risk: D was
shortlisted after A and B had failed, though not on D's own results.

## Changelog
- **0.1.0:** initial spec from proposal §1. Proposal golden reproduced (EPS 4.0, payout 0.5, g_sust 0.06 `G_CAPPED`,
  g_hist +0.052632, DY 0.044444, k 0.107111, spread +2.2111pp → BUY; V_DDM 84.80, MoS +0.884444 informational; price
  50 → HOLD at +1.74pp; EPS 1.80 → payout 1.1111 → SELL). Added: trading-ccy EPS, `G_FLOORED`, `NEW_PAYER` and
  `NO_DIV_HISTORY` rules, `BUY_BLOCKED_*` flags, the precedence table, split-consistent `dps_ttm_prev`, the dividend
  coverage guard, IEEE boundary notes, and precedence, FX and engine goldens; arithmetic verified with a throwaway
  script.
