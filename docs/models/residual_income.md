# Model spec: Residual income value (`residual_income`, v0.2.0) — Model A

Owner: AN · Tests: QA (`tests/unit/test_residual_income_spec.py`) · Code: `src/valuation/models/residual_income.py` ·
Source: proposal `docs/models/proposals/buy_hold_sell.md` §0, §2 (task P7-1; insurer CSM = P7-6). v0.2.0: revision
round v2 (§Revision v0.2.0; frozen in `revision_v2_freeze.md`)

## Purpose
This model follows on from `book_value`. It asks: *given the return this company earns on its book equity, how much is
that book worth?* A bank at 1.0× book earning ROE = r is fairly priced; one earning 2r is cheap at 1.0×. It fixes the
known flaw that a low P/B is often justified by a low ROE.

> **Screening signal only.** BUY / HOLD / SELL sorts the queue for human review. It is not investment advice, not a
> price forecast and not a trade instruction. Labels are exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`. The `SCREEN_` prefix is reserved for the Phase 10 vote (proposal §5) and is never produced here.

## Constants
`ERP = 0.05`, `HORIZON = 10`, `ROE_FLOOR = -0.10`, `ROE_CAP = 0.35`, `RETENTION_DEFAULT = 0.6`, `G_CAP = 0.04`,
`R_SHIFT = 0.01`, `DOMINANT_SHARE = 0.5` (v0.2.0), `BUY_MOS = 0.25`, `SELL_MOS = -0.20`, `PB_MAX = 8.0`, `PB_MAX_ASSET_LIGHT = 3.0`.
`BOOK_SECTORS = {Financials, Utilities, Real Estate, Energy, Materials}`; `CYCLICAL_SECTORS = {Energy, Materials}`;
`ASSET_LIGHT_SECTORS = {Information Technology, Communication Services, Health Care, Industrials, Consumer
Discretionary, Consumer Staples}` (exact GICS sector names, case-sensitive).

## Inputs (`RIInputs`, one ticker at `as_of`)
| Field | Meaning | Unit / default |
|---|---|---|
| `ticker`, `as_of` | Listed symbol, valuation date | str, date |
| `price` | Close on or before `as_of`; must be > 0 (else `ValueError`) | trading ccy |
| `bvps` | Common book per share from `book_value` (unadjusted) | trading ccy |
| `sector` | GICS sector; `None`/`""`/unknown name → `NO_SECTOR` | str \| None |
| `eligible` | Result of `eligibility`; `None` = unknown | bool \| None |
| `rf` | 10y government yield, decimal (0.035 = 3.5%) | float \| None |
| `ni_common_ttm` | TTM net income to common (`ttm.ttm`) | reporting ccy \| None |
| `common_equity_end`, `common_equity_start` | CE at q0 and ≈ 4 quarters earlier (see Data mapping) | reporting ccy \| None |
| `shares_outstanding` | Same count as `bvps` (all classes, after `share_conversion`); must be > 0 | shares |
| `fx_rate` | Trading-ccy units per 1 reporting-ccy unit; must be > 0 | 1.0 |
| `dps_ttm` | `ttm.dps_ttm` | trading ccy, 0.0 |
| `market` | `"TSX"` or `"US"`, else `ValueError` | str |
| `ttm_flags` | Flags of the TTM result, passed through | frozenset() |
| `data_flags` | Engine data flags (e.g. `ROE_PERIOD_MISMATCH`), passed through | frozenset() |
| `filed_estimated` | Balance sheet or TTM uses estimated filed dates | False |
| `roe_history` | Prior trailing-annual `ROE_raw`, most recent first, excluding the current one (entries may be None) | () |
| `csm_relevant`, `csm_after_tax` | Insurer CSM (see §CSM) | False, None (reporting ccy) |
| `ni_currency`, `reporting_currency` | Unit of NI and of CE; both set and different → `CCY_MISMATCH` | None, None |
| `ni_quarters` | v0.2.0: the 4 quarterly NI-to-common values summed into `ni_common_ttm` (after the Q4 = FY − 9M derivation), any order; None or not exactly 4 values → no one-off test | None |

## Formulas (evaluated in this order; each output is None when any input it needs is None)
```
add      = csm_after_tax if CSM applies (§CSM) else 0
ni       = None if CCY_MISMATCH else ni_common_ttm
one_off  = ni > 0 and ni_quarters has 4 values and max(ni_quarters) > DOMINANT_SHARE × ni        (v0.2.0)
ni_norm  = (ni − max(ni_quarters)) × 4/3  if one_off  else ni                                   (v0.2.0)
eps_ttm  = ni / shares_outstanding × fx_rate                              (trading ccy)
b        = retention(dps_ttm, eps_ttm)
B_0      = (CE_end + add) / shares × fx_rate   if CSM applies;  None if CSM needed but missing;  else bvps
pb       = price / B_0                          if B_0 > 0, else None
ROE_raw  = roe_raw(ni, CE_end + add, CE_start + add)   (CE_start None stays None)
ROE_norm = roe_raw(ni_norm, CE_end + add, CE_start + add)  (v0.2.0; = ROE_raw when not one_off)
roe_base = (ROE_norm + h[0] + h[1]) / 3 if sector ∈ CYCLICAL and len(h) ≥ 2 and h[0], h[1] not None  → roe_basis "3y_avg"
           else ROE_norm                                                                             → roe_basis "ttm"
           (ROE_raw None → ROE_norm, roe_base, ROE_0 and roe_basis None)
ROE_0    = clip_roe(roe_base)
r        = rf + ERP
V_RI, V_up, V_down = ri_value(B_0, ROE_0, r, b), at r + 0.01, at r − 0.01     (only if pb is not None)
MoS = V_RI/price − 1;  mos_up = V_up/price − 1;  mos_down = V_down/price − 1
justified_pb = justified_pb(ROE_0, r, b)     (info only, never drives a signal)
```
Function rules:
- `roe_raw(ni, ce_end, ce_start=None)`: ni or ce_end None → `(None, ∅)`. ce_start None → `avg = ce_end`, flags
  `{ROE_SPOT_EQUITY}`; else `avg = (ce_end + ce_start)/2`, flags ∅. `avg ≤ 0` → `(None, flags)`; else `(ni/avg, flags)`.
- `clip_roe(x) -> (min(max(x, −0.10), 0.35), clipped)`, `clipped = (result != x)`. Equality at a bound is not clipped.
- `retention(dps, eps)`: `eps ≤ 0` → 0.6; else `min(max(1 − dps/eps, 0), 1)`.
- `fade_path(roe0, r, horizon=10)`: `[r + (roe0 − r)(T − t)/T for t = 1..T]`; the last element equals `r`.
- `ri_value(b0, roe0, r, b, horizon=10)`: `B = b0; V = b0`; for `t = 1..T` with `ROE_t` from `fade_path`:
  `V += (ROE_t − r) × B / (1+r)^t`, then `B = B × (1 + ROE_t × b)`. No terminal value.
- `justified_pb(roe0, r, b)`: `g = min(b × roe0, 0.04)`; `None` if `r ≤ g`; else `(roe0 − g)/(r − g)` (may be negative).
- `map_signal(mos, mos_up, mos_down)`: `"BUY"` if `mos ≥ 0.25 and mos_up > 0`; elif `"SELL"` if `mos ≤ −0.20 and
  mos_down < 0`; else `"HOLD"`.

The r ± 1pp sensitivity re-runs `ri_value` with the shifted r everywhere (the fade goes to the shifted r and
discounting uses it); `ROE_0`, `b`, `B_0` are unchanged.

## Signal and precedence
1. `eligible is not True` (False **or None**) → `"NOT_ELIGIBLE"`, `no_signal_reason = None`. Decision: unknown
   eligibility is treated as not eligible (no data on trading → no signal).
2. Else the **first** matching reason below → `"NO_SIGNAL"` with that `no_signal_reason`.
3. Else `map_signal(mos, mos_up, mos_down)`; then (v0.2.0) a `"BUY"` with `ROE_CLIPPED` becomes `"HOLD"` with flag
   `BUY_BLOCKED_CLIP`. The result is `signal` (the **model label**, used by the backtest).
4. (v0.2.0) `screen_signal` = `"HOLD"` with flag `SELL_ZONE` when `signal == "SELL"`; otherwise `screen_signal = signal`.
   The screen and report show `screen_signal`; Model A never shows a SELL to the human (§Revision v0.2.0, R1).

| # | Reason | Condition |
|---|---|---|
| 1 | `NO_SECTOR` | sector None, `""`, or not in BOOK ∪ ASSET_LIGHT |
| 2 | `NEEDS_CSM_INPUT` | `csm_relevant` and `csm_after_tax is None` |
| 3 | `NEG_BOOK` | `B_0 ≤ 0` (or B_0 None for another reason) |
| 4 | `PB_TOO_HIGH` | `pb > 8` (8.0 exactly passes) |
| 5 | `ASSET_LIGHT` | sector ∈ ASSET_LIGHT and `pb > 3` (3.0 exactly passes) |
| 6 | `CCY_MISMATCH` | `ni_currency` and `reporting_currency` both set and different |
| 7 | `NO_TTM` | `ni_common_ttm is None` |
| 8 | `NO_EQUITY` | `common_equity_end is None` |
| 9 | `NEG_AVG_EQUITY` | average (CSM-adjusted) equity ≤ 0, i.e. `roe_raw` returned None |
| 10 | `NO_RF` | `rf is None` |

**Outputs on NOT_ELIGIBLE / NO_SIGNAL:** every numeric output is still computed whenever its inputs allow it (so the
human sees it). Only the signal is withheld. E.g. `PB_TOO_HIGH`, `ASSET_LIGHT`, `NO_SECTOR`, `NOT_ELIGIBLE` keep
`v_ri`/`mos`; `NEG_BOOK` has `pb = v_ri = mos = … = None` but keeps `roe0`, `b`, `justified_pb`.

## Flags (information only; never block a signal, except `BUY_BLOCKED_CLIP`)
| Flag | Condition |
|---|---|
| `ROE_CLIPPED` | `clip_roe` changed `roe_base` |
| `ONE_OFF_QUARTER` | v0.2.0: `one_off` True (ROE uses `ni_norm`) |
| `BUY_BLOCKED_CLIP` | v0.2.0: `map_signal` gave BUY and `ROE_CLIPPED` is set → model label HOLD (this flag **does** change the signal) |
| `SELL_ZONE` | v0.2.0: model label SELL, shown as HOLD (`screen_signal`) |
| `ROE_SPOT_EQUITY` | `common_equity_start` None, CE_end alone used |
| `CYCLICAL_ROE` | sector ∈ {Energy, Materials}, always (whatever `roe_basis`) |
| `FAIR_VALUE_BOOK` | sector Real Estate and market TSX (IFRS fair-value book) |
| `CSM_ADJUSTED` | CSM added to book and ROE equity |
| `PIT_ESTIMATED` | `filed_estimated` True |
| `NO_TTM` | `ni_common_ttm` None (added even if not in `ttm_flags`) |
| pass-through | every flag in `ttm_flags` (e.g. `STALE_TTM`) and `data_flags` (e.g. `ROE_PERIOD_MISMATCH`) |

## Insurer CSM (P7-6)
IFRS 17 life insurers (MFC.TO, SLF.TO, GWO.TO, IAG.TO) hold part of their value in the contractual service margin, so
unadjusted book makes them look expensive. Manual file `universe/insurer_csm.csv`:
`ticker, period_end, filed_date, csm_after_tax, adjusted_bvps_reported, source, note` (`csm_after_tax` = total, reporting
ccy, raw units; blank = needs human input; `adjusted_bvps_reported` = issuer's own figure, QA reference only).
- `csm_relevant = ticker appears in the file` (any row). P&C insurers (IFC, DFY, FFH) and US GAAP insurers are not listed.
- Row used: `period_end ==` the balance-sheet `period_end` used for `bvps`, and `filed_date ≤ as_of` (later rows
  don't exist); several such rows → latest `filed_date`. No such row, or blank `csm_after_tax` → `csm_after_tax = None`
  → `NEEDS_CSM_INPUT`. Never guessed or carried from another period.
- `csm_after_tax` is ignored when `csm_relevant` is False. With CSM applying and CE_end None, B_0 is None → `NEG_BOOK`
  (precedence #3 before `NO_EQUITY`).
- Applied: `B_0 = (CE_end + csm)/shares × fx`; `ROE_raw = NI / (avg_CE + csm)` (= `roe_raw(NI, CE_end + csm, CE_start +
  csm)`). The current CSM is used at both ends (simplification; recorded). Model B uses the same B_0 and ROE_raw.

## Worked examples (golden values for QA)
Assert against the values below with relative tolerance 1e-9 (absolute 1e-12 for zero). Money in raw units.

**G1 (proposal example), `ri_value(50, 0.14, 0.085, 0.6)`.** `fade_path(0.14, 0.085)` = 0.1345, 0.129, 0.1235, 0.118,
0.1125, 0.107, 0.1015, 0.096, 0.0905, 0.085.

| t | ROE_t | B_{t−1} | RI_t PV = (ROE_t − r)·B_{t−1}/1.085^t |
|---|---|---|---|
| 1 | 0.1345 | 50 | 2.281105991 |
| 2 | 0.1290 | 54.035 | 2.019613923 |
| 3 | 0.1235 | 58.217309 | 1.754783903 |
| 4 | 0.1180 | 62.53121160 | 1.488990170 |
| 5 | 0.1125 | 66.95842138 | 1.224585771 |
| 6 | 0.1070 | 71.47811482 | 0.9638675104 |
| 7 | 0.1015 | 76.06700979 | 0.7090422612 |
| 8 | 0.0960 | 80.69949069 | 0.4621953517 |
| 9 | 0.0905 | 85.34778135 | 0.2252616608 |
| 10 | 0.0850 | 89.98216588 | 0 |

`V_RI` = **61.12944654266581**; at r = 0.095: **58.845620190495296**; at r = 0.075: **63.54734542151645**.
`justified_pb(0.14, 0.085, 0.6)` = (0.14 − 0.04)/(0.085 − 0.04) = **2.2222222222** (g capped: 0.6×0.14 = 0.084 > 0.04).
- price 60 → MoS **0.0188241090444**, mos_up −0.0192396634917, mos_down 0.0591224236919 → **HOLD**.
- price 48 → MoS **0.273530136306**, mos_up **0.225950420635** > 0 → **BUY**.
- price 80 → MoS **−0.235881918217**, mos_down **−0.205658182231** < 0 → **SELL** (robust).

**G2 function edges.** `clip_roe(0.50) = (0.35, True)`; `clip_roe(−0.30) = (−0.10, True)`; `clip_roe(0.35) = (0.35,
False)`; `clip_roe(−0.10) = (−0.10, False)`. `retention(1.0, −0.5) = retention(1.0, 0.0) = 0.6`; `retention(3.0, 2.0) = 0.0`
(DPS > EPS); `retention(2.0, 2.0) = 0.0`; `retention(0.0, 2.0) = 1.0`; `retention(0.8, 2.0) = 1 − 0.8/2.0`.
`ri_value(50, 0.085, 0.085, 0.6) = 50` (ROE = r → V = B_0); `ri_value(50, 0.14, 0.085, 0.6, horizon=1) = 50`;
`ri_value(50, 0.14, 0.085, 0.0) = 59.320679683271095`; `… b = 1.0 → 62.58698765156571`.
`justified_pb(0.14, 0.03, 0.6) = None` (r < g); `justified_pb(0.14, 0.04, 0.6) = None` (r = g);
`justified_pb(0.14, 0.085, 0.0) = 0.14/0.085`. `roe_raw(10, 100, None) = (0.1, {ROE_SPOT_EQUITY})`;
`roe_raw(10, 100, −120) = (None, ∅)`; `roe_raw(None, 100, 90) = (None, ∅)`.
`map_signal`: (0.25, 0.01, 0.4) → BUY; (0.25, 0.0, 0.4) → HOLD; (0.2499, 0.2, 0.3) → HOLD; (−0.20, −0.3, −0.01) → SELL;
(−0.20, −0.3, 0.0) → HOLD; (−0.21, −0.25, 0.01) → HOLD (non-robust SELL); (0.30, −0.01, 0.6) → HOLD (non-robust BUY).

**G3 end-to-end, US bank (base case `BASE`).** ticker "BANK", market US, sector Financials, eligible True, rf 0.042,
ni_common_ttm 13.0e9, CE_end 100.0e9, CE_start 92.0e9, shares 2.0e9, fx 1.0, bvps 50.0, dps_ttm 2.6, price 75.0.
→ avg_CE 96.0e9, `roe_raw = 13/96 = 0.13541666666666666` = roe0, roe_basis "ttm", r **0.092**, eps_ttm **6.5**,
b **1 − 2.6/6.5 = 0.6**, b0 50, pb 1.5, v_ri **58.567222546400856**, v_ri_up **56.408233106641376**, v_ri_down
**60.85172204359812**, mos **−0.2191036993813219**, mos_up −0.24789022524478166, mos_down **−0.1886437060853584**,
justified_pb **1.8349358974358971** → mos ≤ −0.20 and mos_down < 0 → **SELL**, flags ∅.
- BASE with price 45 → mos **0.30149383436446353**, mos_up **0.25351629125869724** → **BUY**.
- BASE with price 52 → mos 0.12629274127693946 → **HOLD**.
- BASE with CE_start None → roe_raw **0.13**, v_ri **57.455952111741375**, mos −0.23392063851011502, mos_down
  −0.20406076860388633 → SELL, flags {ROE_SPOT_EQUITY}.
- Clip: BASE with ni 48.0e9 → roe_raw 0.5, roe0 **0.35**, eps 24.0, b = 1 − 2.6/24, v_ri **128.46557945890916**,
  mos 0.7128743927854555 → map_signal BUY → v0.2.0 **HOLD**, flags {ROE_CLIPPED, BUY_BLOCKED_CLIP}. With ni −28.8e9 → roe_raw −0.3, roe0 **−0.10**, eps −14.4,
  b **0.6** (EPS ≤ 0), v_ri **20.257657687590946**, justified_pb **−0.26315789473684215** → SELL, flags {ROE_CLIPPED}.
- Loss without clip: ni −2.0e9 → roe0 −0.020833333333333332, eps −1.0, b 0.6, v_ri **31.060323406323064** → SELL.

**G4 FX, USD reporter on TSX (Materials).** market TSX, sector Materials, rf 0.032, ni 1.0e9 (USD), CE_end 10.0e9,
CE_start 9.0e9, shares 5.0e8, fx 1.40, bvps 28.0 (= 10e9/5e8 × 1.4), dps 1.12 (CAD), price 30.0, filed_estimated True,
roe_history () → eps **1e9/5e8 × 1.4 = 2.8**, b **1 − 1.12/2.8**, roe0 **1/9.5 = 0.10526315789473684**, basis "ttm",
r 0.082, v_ri **30.56336853495171**, mos **0.0187789511650569**, mos_up −0.01932920160717344 → **HOLD**, justified_pb
1.553884711779448, flags {CYCLICAL_ROE, PIT_ESTIMATED}.

**G5 cyclical 3-year average.** G4 with roe_history (0.06, 0.02) → roe_basis **"3y_avg"**, roe0 **(1/9.5 + 0.06 +
0.02)/3 = 0.06175438596491228**, roe_raw unchanged, v_ri **25.869054656427853**, mos −0.1376981781190716 → HOLD.
roe_history (0.50, 0.45) → mean 0.35175… → roe0 **0.35**, flags ⊇ {ROE_CLIPPED, CYCLICAL_ROE} (clip after averaging),
v_ri 66.4010543305594 → map_signal BUY → v0.2.0 **HOLD** + `BUY_BLOCKED_CLIP`. roe_history (0.06,) or (0.06, None) → basis "ttm", same as G4.

**G6 insurer CSM (synthetic).** market TSX, Financials, rf 0.032, ni 6.0e9, CE_end 44.2e9, CE_start 42.0e9, shares
1.7e9, fx 1.0, bvps 26.0 (unadjusted), dps 1.70, price 45.0, filed_estimated True, csm_relevant True, csm_after_tax
19.55e9 → b0 **(44.2e9 + 19.55e9)/1.7e9 = 37.5**, pb **1.2**, roe_raw **6.0/(43.1 + 19.55) = 0.09577015163607343**
(unadjusted would be 0.1392), eps 3.5294117647058822, b **1 − 1.70/(6/1.7)**, r 0.082, v_ri **39.47586691199252**,
mos **−0.12275851306683294** → HOLD, flags {CSM_ADJUSTED, PIT_ESTIMATED}. Same with csm_after_tax None →
**NO_SIGNAL / NEEDS_CSM_INPUT**, b0 = pb = roe_raw = roe0 = v_ri = None, eps and b still computed.

**G7 reasons and boundaries (BASE with one change).**
| Change | signal / reason | notes |
|---|---|---|
| bvps −1.0 | NO_SIGNAL / NEG_BOOK | pb, v_ri, mos None; roe0 0.135416…, justified_pb 1.8349… kept |
| price 400.0 | SELL | pb **8.0** exactly passes |
| price 400.01 | NO_SIGNAL / PB_TOO_HIGH | v_ri 58.567…, mos −0.853585603993898 still reported |
| sector Health Care, price 150.0 | SELL | pb **3.0** exactly passes |
| sector Health Care, price 150.5 | NO_SIGNAL / ASSET_LIGHT | |
| sector Industrials, price 150.5 | NO_SIGNAL / ASSET_LIGHT | decision for sectors the proposal left out |
| ni None, ttm_flags {NO_TTM} | NO_SIGNAL / NO_TTM | eps, b, roe0 None; pb 1.5 |
| CE_end None | NO_SIGNAL / NO_EQUITY | |
| CE_start −120.0e9 | NO_SIGNAL / NEG_AVG_EQUITY | avg −10e9 |
| rf None | NO_SIGNAL / NO_RF | roe0, b kept; r, v_ri None |
| sector None | NO_SIGNAL / NO_SECTOR | values as BASE |
| ni_currency "CAD", reporting_currency "USD" | NO_SIGNAL / CCY_MISMATCH | eps, b, roe None |
| eligible None | NOT_ELIGIBLE | no_signal_reason None; values as BASE |
| eligible False, bvps −1.0 | NOT_ELIGIBLE | NOT_ELIGIBLE beats NEG_BOOK |
| sector Real Estate, market TSX | SELL | flags {FAIR_VALUE_BOOK} |

**G8 (v0.2.0) one-off quarter, SELL zone.** BASE (`ni_common_ttm` 13.0e9) with `ni_quarters`:

| ni_quarters (e9) | one_off | ni_norm | roe_raw | roe0 | v_ri | price → mos | signal / screen_signal | flags |
|---|---|---|---|---|---|---|---|---|
| (3.25, 3.25, 3.25, 3.25) | F | 13.0e9 | 0.13541666666666666 | same | 58.567222546400856 | 75 → −0.2191036993813219 | SELL / **HOLD** | {SELL_ZONE} |
| (1.5, 2.0, 3.0, 6.5) | F (6.5 = 0.5×13, not >) | 13.0e9 | 0.13541666666666666 | same | 58.567222546400856 | 45 → 0.30149383436446353 | BUY / BUY | ∅ |
| (1.5, 1.5, 2.0, 8.0) | **T** | **6666666666.666667** | 0.13541666666666666 | **0.06944444444444445** | **45.845100042050326** | 45 → **0.018780000934451646** | HOLD / HOLD | {ONE_OFF_QUARTER} |
| (1.5, 1.5, 2.0, 8.0) | T | as above | | | v_up 44.16687484558917, v_down 47.620985649177435 | 30 → **0.5281700014016775**, mos_up 0.472229161519639 | BUY / BUY | {ONE_OFF_QUARTER} |
| (−1, −1, −1, 16) | T | **−4.0e9** | 0.13541666666666666 | **−0.041666666666666664** | **28.034918814775413** | 75 → −0.6262010824696611 | SELL / HOLD | {ONE_OFF_QUARTER, SELL_ZONE} |
| (12, 12, 12, 12) with ni 48.0e9 | F | 48.0e9 | 0.5 | 0.35 (clip) | 128.46557945890916 | 75 → 0.7128743927854555 | **HOLD** / HOLD | {ROE_CLIPPED, BUY_BLOCKED_CLIP} |

In every row eps_ttm uses the reported NI (6.5 for ni 13.0e9), so b = 0.6; `roe_raw` (passed to Models B and C) is
unchanged by the one-off rule. `ni_quarters` None → no test (G3–G7 as printed). The one-off test applies to the
current TTM only; `roe_history` entries are not normalised (simplification).

## Property expectations
- `ri_value(k·b0, …) = k · ri_value(b0, …)`; ROE_0 = r → V = B_0; `fade_path(...)[-1] == r`.
- `ri_value` is strictly increasing in `roe0` on [−0.10, 0.35] (fixed b0 > 0, r ∈ [0.03, 0.15], b ∈ [0, 1]; checked on a grid).
- Multiplying price by k leaves v_ri unchanged and gives `1 + mos' = (1 + mos)/k`.
- Expressing NI, CE and CSM in another currency with a matching `fx_rate` leaves every per-share output and the signal unchanged.
- v0.2.0: `screen_signal` is never `"SELL"`; `signal == "SELL"` ⇔ `SELL_ZONE` in flags. Scaling all of `ni_quarters`
  and `ni_common_ttm` by k > 0 leaves `one_off` unchanged. `ni_norm ≤ ni` whenever `one_off`.
- Flags (other than `BUY_BLOCKED_CLIP`) never change the signal; removing a flag-only condition (e.g. `filed_estimated`) leaves all numbers unchanged.
- Note: with T = 10 a 1pp change in r moves V by ≈ 4%, so the robustness legs of the mapping rarely bind on real data;
  they are tested through `map_signal`.

## API contract (SW and QA)
```python
from valuation.models.residual_income import (
    RIInputs, RIResult, ERP, HORIZON, ROE_FLOOR, ROE_CAP, DOMINANT_SHARE, MODEL_VERSION,
    roe_raw, clip_roe, retention, fade_path, ri_value, justified_pb, map_signal, evaluate,
)
```
- Signatures as in §Formulas. `RIInputs`: dataclass with the Inputs-table fields and defaults.
- `evaluate(inputs) -> RIResult` (frozen dataclass): `ticker, as_of, market, sector, price, signal, no_signal_reason,
  roe_raw, roe0, roe_basis ("ttm" | "3y_avg" | None), r, b, eps_ttm, b0, pb, v_ri, mos, v_ri_up, mos_up, v_ri_down,
  mos_down, justified_pb, flags: frozenset[str]`, and (v0.2.0) `screen_signal, ni_norm, roe_norm, one_off: bool`.
  `MODEL_VERSION = "residual_income/0.2.0"`.

## Data mapping (engine, SW)
| Input | Source |
|---|---|
| bvps, shares, fx, CE_end | `book_value` inputs via `select_latest_filing` (EDGAR > Yahoo on equal period_end); `common_equity = total − NCI − pref` |
| ni_common_ttm, ttm_flags, ni_currency | `engine.ni_common_ttm` (per source; latest `period_end` wins, EDGAR on ties); q0 = its `period_end` |
| CE_end alignment | if the bvps filing's period_end ≠ q0, use CE from a filing with period_end = q0 (filed ≤ as_of, EDGAR first); none → keep the bvps filing's CE and add `ROE_PERIOD_MISMATCH` to `data_flags` |
| CE_start | CE of the filing with the latest period_end in `[q0 − 380, q0 − 350]` days among filings with `filed_date ≤ as_of` (EDGAR checked first, then Yahoo; equal period_end → later filed). None if none |
| roe_history | same rule at the quarter ends 4 and 8 positions before q0 in the TTM source's known quarters (filed ≤ as_of, chain consecutive per `ttm`): NI TTM ending there / avg CE (its CE and CE ≈ 1 year earlier). Any piece missing → that entry None |
| ni_quarters | v0.2.0: the 4 quarterly NI values of the same TTM result (same source and chain as `ni_common_ttm`); None on NO_TTM |
| dps_ttm | `engine.dps_ttm` |
| rf | `rates_daily`, latest on/before as_of: market TSX → `CA10Y`, US → `US10Y` (decimal) |
| filed_estimated | True if the balance sheet or any TTM quarter row has `filed_estimated` (TSX/Yahoo) |
| csm_relevant / csm_after_tax | `universe/insurer_csm.csv` per §CSM |
| reporting_currency | the balance-sheet filing's currency |

## External check (P7-4)
- ROE vs issuer packs: RY.TO, TD.TO, BNS.TO. `universe/issuer_sources.csv` gets an ROE line per bank: the four latest
  quarterly "Return on common equity" values as printed (annualised). Reference = their mean. Pass when
  `|roe_raw − ref| ≤ 0.015`. Banks annualise each quarter and use average monthly equity (some use adjusted
  earnings), so differences are expected; explain any miss. Yahoo `returnOnEquity` is secondary.
- MFC.TO: `b0` vs `adjusted_bvps_reported` in `insurer_csm.csv` (±3%).
- V_RI recomputed in a spreadsheet for 3 golden tickers (G1, G3, G6) to 1e-9 relative. Results: `docs/qa/`.

## Report v2: `reports/<date>/screen_signals.csv` and `.parquet` (P7-5; replaces backlog P2-9 sector gating)
One row per universe ticker (valued or not), sorted by ticker. Columns: `ticker, market, sector, eligible,
elig_fail_reason, price, bvps (= Model A b0, CSM-adjusted when flagged), pb, roe0, a_signal (v0.2.0: = `screen_signal`),
a_model_signal (v0.2.0: = `signal`), a_no_signal_reason, a_mos,
a_v_ri, a_mos_up, a_mos_down, a_justified_pb, b_signal, b_no_signal_reason, b_implied_upside, b_resid_pct, b_fitted_pb,
b_fair_value, flags` (`flags` = sorted, comma-joined union of eligibility, Model A and Model B flags). A ticker with no
usable balance sheet gets `NOT_ELIGIBLE` if not eligible, else `NO_SIGNAL` with reason `NO_DATA`, in both models
(in practice always `NOT_ELIGIBLE`: with no balance sheet there is no market cap, so eligibility rule E3 fails).
The first CSV line is the comment `# Screening signals for human review, not investment advice. Models:
residual_income/0.1.0, peer_regression/0.1.0. as_of <date>. No combined vote (Phase 10).`; the parquet stores the same
text as file metadata key `disclaimer`. No vote and no `SCREEN_` labels in Phase 7. Model B: `peer_regression.md`.

## Revision v0.2.0 (revision round v2; evidence: `docs/qa/backtest_model_a.md`, phase8-validation §5–§6)
In-sample evidence only (US 2014-01 … 2023-10); the holdout is untouched. Parameters frozen in
`revision_v2_freeze.md` before any v0.2.0 result exists.

**R1 SELL becomes information only (screen shows HOLD + `SELL_ZONE`).** Chosen over a revised fade with a terminal
value.
- *Evidence:* SELLs carry no information (12m sector-neutral excess +0.05%, hit 50.6%, 17,171 obs); SELLs above
  P/B 2.9 are wrong-way (+2.1%, hit 47.7%).
- *Why not a terminal value:* the no-TV, 10-year linear fade is a deliberately **conservative lower bound** on a
  persistent franchise (V/B ≤ 2.3 at the ROE cap, phase7 §6g). "Cheap even under conservative assumptions" (BUY) is
  meaningful; "dear under conservative assumptions" (SELL) is not, so the asymmetry follows from the model's own
  construction. A TV needs new parameters (horizon, terminal ROE premium or persistence) whose natural calibration
  target is exactly the 2014–2023 growth-over-value regime that made high-P/B SELLs win, and it would raise V for every
  name with ROE > r, changing the BUY bucket, the only part with any information (+7.2%, hit 57%).
- *Backtest:* the model label keeps SELL, so the unchanged `backtest.md` spread (BUY − SELL) measures the same SELL
  bucket as v0.1.0. R1 cannot improve the backtest; it only stops a non-informative label reaching the screen.
- *Overfitting risk:* low (no parameter; no effect on the validated metric).

**R2 one-off ROE (P7-11): dominant-quarter normalisation + no BUY on a clipped ROE.**
- *Evidence:* ROE_CLIPPED BUYs 12m excess +1.7% (median −2.8%, hit 44%, n 118) vs unclipped +7.7% (median +4.7%, hit
  58%, n 1,188); the gap opens at 6–12m, when a one-off leaves the TTM. Phase 7 (§6d, before any backtest) found 5 of 8
  live BUYs resting on one-off quarters (ECHO, FIS, FCR-UN, APA, SMCI).
- *Rule:* if one quarter delivers more than half of a positive TTM NI (twice its 25% share), ROE uses the other three
  quarters annualised (`ni_norm`). A BUY whose ROE still had to be clipped becomes HOLD: the model declared that input
  unreliable, so a value built on it should not trigger a BUY. `DOMINANT_SHARE = 0.5` is the threshold Phase 7 wrote
  down before the backtest ("flag when one quarter > 50% of TTM NI"); it was not searched.
- *Overfitting risk:* **medium.** The clipped-BUY bucket was found in this sample (n 118, about 1 a month), and the
  gate removes exactly that bucket, so in-sample BUY metrics are biased upward; only the holdout can tell.

Unchanged: ERP, HORIZON (no TV), clip bounds, BUY/SELL MoS thresholds, sector gates, CSM. Models B and C keep
`roe_raw` (not `ROE_norm`).

## Changelog
- **0.2.0:** revision round v2: R1 SELL information only (`screen_signal`, `SELL_ZONE`), R2 one-off quarter
  (`DOMINANT_SHARE`, `ni_quarters`, `ONE_OFF_QUARTER`, `BUY_BLOCKED_CLIP`); G3 clip and G5 (0.50, 0.45) BUY → HOLD; new
  G8. Golden arithmetic verified with a throwaway script.
- **0.1.0:** initial version from proposal §2. Proposal arithmetic verified (V_RI 61.13, 58.85, justified P/B 2.22,
  MoS +1.9% / +27.4% / +22.6%); no corrections. B_1 is 54.035 exactly (proposal prints 54.03). QA review
  clarifications: roe_basis None without ROE_raw; NO_DATA is unreachable in practice; CSM edge cases.
