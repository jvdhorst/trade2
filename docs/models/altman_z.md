# Model spec: Altman Z'' distress score / veto (`altman_z`, v0.1.0) — Model G

Owner: AN · Tests: QA (`tests/unit/test_altman_z_spec.py`, `tests/unit/test_altman_z_engine_spec.py`) · Code:
`src/valuation/models/altman_z.py` · Source: proposal `docs/models/proposals/additional_models.md` §4 (data gaps §13).
**Not part of FROZEN_V2**: G is not in `revision_v2_freeze.md` and adding it must not change that freeze's hash.
**New data**: `current_assets`, `current_liabilities`, `retained_earnings` (`ttm.md` §6, data batch 3); total assets,
EBIT TTM, CFO TTM and total debt are Phase 9 items (`ttm.md` §5); total equity already exists.

## Purpose
*Does the balance sheet look like a firm heading for distress?* Z'' (Altman 1995, the non-manufacturer variant of the
1968 Z-score: no sales term, book equity) scores liquidity, accumulated profitability, current profitability and
leverage. A distressed name is a SELL candidate on its own and the classic value trap for the value models; G reports
a `distress_veto` for a later combined vote. Strength says nothing about price, so G never says BUY. G is per name
(no cross-section, no universe size, no risk-free rate).

> **Screening signal only**, not investment advice. Labels exactly `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"` (G never emits `"BUY"`); `SCREEN_` is reserved for the Phase 10 vote.

## Constants
`MODEL_VERSION = "altman_z/0.1.0"`, `COEF_X1 = 6.56`, `COEF_X2 = 3.26`, `COEF_X3 = 6.72`, `COEF_X4 = 1.05`,
`DISTRESS_Z = 1.10`, `SAFE_Z = 2.60`, `OVERRIDE_CFO_DEBT = 0.25`,
`EXCLUDED_SECTORS = frozenset({"Financials", "Utilities", "Real Estate"})`. All are fixed starting values from
proposal §4 (Altman's published coefficients and zones; the override threshold from the proposal's failure-mode note),
**never tuned**, and G has never been backtested, so none was chosen after a result.

## Inputs (`GInputs`, frozen dataclass, one ticker at `as_of`)
| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`) | as Model A | |
| `sector` | GICS sector, `str \| None`; drives the exclusion | |
| `eligible` | eligibility E1–E5 (`bool \| None`); anything but `True` → `NOT_ELIGIBLE` | |
| `total_assets`, `current_assets`, `current_liabilities`, `retained_earnings` | reporting ccy, balance sheet (`ttm.md` §5.2, §6); RE negative for an accumulated deficit | None |
| `ebit_ttm` | reporting ccy; None on NO_TTM or FLOW_CCY_MISMATCH | None |
| `total_equity` | reporting ccy, as stored: **includes NCI and preferred** | None |
| `cfo_ttm`, `total_debt` | reporting ccy; feed only the override | None |
| `flags` | passed through (`PIT_ESTIMATED`, `FLOW_CCY_MISMATCH`, `EBIT_PERIOD_MISMATCH`) | frozenset() |

No monetary input is ever defaulted to 0. **No FX input**: every ratio has numerator and denominator from the same
filing (or flows in the filing's currency, enforced by `FLOW_CCY_MISMATCH`), so the trading currency never enters.

`GResult`: `ticker, market, signal, no_signal_reason, x1, x2, x3, x4, z, zone, total_liabilities, cfo_to_debt,
distress_veto, flags`. API: `compute_metrics(inp) -> GMetrics(x1, x2, x3, x4, z, total_liabilities, cfo_to_debt)`;
`zone(z) -> "distress" | "grey" | "safe"`; `map_signal(z) -> "SELL" | "HOLD"`; `evaluate(inp) -> GResult`;
`run_altman_z(inputs: Sequence[GInputs]) -> list[GResult]` (one result per input, same order). Each name is evaluated
alone, so markets (and names) are trivially independent.

## Formulas (per name, reporting ccy)
```
TL  = total_assets − total_equity                 (equity incl. NCI; computed whenever both exist)
X1  = (current_assets − current_liabilities) / total_assets
X2  = retained_earnings / total_assets
X3  = ebit_ttm / total_assets
X4  = total_equity / TL                           (None if TL ≤ 0)
Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
cfo_to_debt = cfo_ttm / total_debt                (None unless both exist and total_debt > 0)
```
**Evaluation order** (so float goldens reproduce bit for bit): `z = COEF_X1*x1 + COEF_X2*x2 + COEF_X3*x3 + COEF_X4*x4`,
summed left to right in exactly that order (Python's default for that expression); each X is a single division as
written, X1's numerator subtracted first.

Metrics are computed whenever their inputs exist, also for NOT_ELIGIBLE and excluded names (as Model J). A None input
makes every X that depends on it None, and then Z'' None; nothing is filled in. `total_assets ≤ 0` → X1–X4 and Z''
None. `TL ≤ 0` → X4 and Z'' None (X1–X3 still computed). `cfo_to_debt` is output whenever computable, for any name
(informational). Negative equity is not a gate: X4 < 0 and the formula still applies (TL = TA − equity > TA > 0).

```
zone(z):       "distress" if z < 1.10 ; "grey" if 1.10 ≤ z ≤ 2.60 ; "safe" if z > 2.60
map_signal(z): "SELL" if z < 1.10 else "HOLD"
```
Plain float comparisons, no epsilon: exactly 1.10 and exactly 2.60 are grey. `zone` is None when z is None.

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` (reason None) | `eligible is not True` |
| 2 | `NO_SIGNAL` / `EXCLUDED_SECTOR` | sector None or empty, or in `EXCLUDED_SECTORS` |
| 3 | `NO_SIGNAL` / `NO_DATA` | any of `total_assets`, `current_assets`, `current_liabilities`, `retained_earnings`, `ebit_ttm`, `total_equity` is None |
| 4 | `NO_SIGNAL` / `NON_POSITIVE_ASSETS` | `total_assets ≤ 0` |
| 5 | `NO_SIGNAL` / `NON_POSITIVE_LIABILITIES` | `TL ≤ 0` |
| 6 | `map_signal(z)`, then the override | SELL if z < 1.10, else HOLD. **No BUY, ever** |

`cfo_ttm` and `total_debt` are **not** required for a signal; they feed only the override.

**CASH_RICH_OVERRIDE** (evaluated only when row 6 is reached and `zone == "distress"`):
- `cfo_to_debt > 0.25` (strict; exactly 0.25 does not override) → signal **HOLD** instead of SELL, flag
  `CASH_RICH_OVERRIDE`. z, X1–X4 and zone are **unchanged** (zone still reads `"distress"`), so the override is visible,
  never silent.
- `cfo_to_debt` None (CFO missing, debt missing, or debt ≤ 0) → no override (conservative: SELL stands), flag
  `NO_OVERRIDE_DATA`. A debt-free distressed name therefore cannot be overridden in 0.1.0 (known limitation).
- `cfo_to_debt ≤ 0.25` → SELL stands, no override flag.

**distress_veto** (`bool | None`): True iff signal is SELL; False iff HOLD (incl. overridden names); None for
`NO_SIGNAL`/`NOT_ELIGIBLE`. It is reported for a future vote; the vote is not built and the veto blocks nothing in 0.1.0.

**Flags:** `NEG_EQUITY` (whenever `total_equity` is not None and < 0, whatever the signal), `CASH_RICH_OVERRIDE`,
`NO_OVERRIDE_DATA` (both only as above), plus the pass-through flags.

## Worked examples (golden values; relative tolerance 1e-9)
Unless stated: market `"US"`, sector `"Industrials"`, eligible True, cfo_ttm = total_debt = None, $M. Full float reprs
are given where the value is not a round decimal (computed with the evaluation order above).

**G1 grey (proposal case 1).** CA 600, CL 500, TA 2000, RE 400, EBIT 180, equity 800 → TL 1200, X1 0.05, X2 0.2,
X3 0.09, X4 0.6666666666666666, Z'' 2.2848 (float `2.2847999999999997`), zone grey → **HOLD**, veto False, flags none
(no `NO_OVERRIDE_DATA`: not distress). With cfo 50, debt 1000: cfo_to_debt 0.05 output, result unchanged.

**G2 distress (proposal case 2).** CA 450, CL 600, TA 2000, RE −100, EBIT 20, equity 300 → TL 1700, X1 −0.075,
X2 −0.05, X3 0.01, X4 0.17647058823529413 (300/1700), Z'' −0.402506 in the proposal (float
`-0.40250588235294105`), zone distress → **SELL**, veto True, cfo_to_debt None, flag `NO_OVERRIDE_DATA`.
- **G2b:** cfo 50, debt 1000 → cfo_to_debt 0.05 → **SELL**, veto True, no `NO_OVERRIDE_DATA`, no override.
- **G2c:** debt 0 (cfo 50) or cfo None (debt 1000) → cfo_to_debt None → SELL + `NO_OVERRIDE_DATA`.

**G3 override.** G2 with cfo 300, debt 1000 → cfo_to_debt 0.3 > 0.25 → **HOLD**, veto False, flag
`CASH_RICH_OVERRIDE`; z −0.40250588235294105 and zone `"distress"` unchanged. With cfo 250, debt 1000 → cfo_to_debt
0.25 (not > 0.25) → **SELL**, veto True, no override flag.

**G4 negative equity (buyback brand).** TA 1000, CA 300, CL 350, RE −400, EBIT 150, equity −200 → TL 1200,
X1 −0.05, X2 −0.4, X3 0.15, X4 −0.16666666666666666, Z'' `-0.7990000000000002`, distress → **SELL**, veto True, flags
`NEG_EQUITY`, `NO_OVERRIDE_DATA`. With cfo 400, debt 1000 → cfo_to_debt 0.4 → **HOLD**, veto False, flags
`NEG_EQUITY`, `CASH_RICH_OVERRIDE`, zone distress.

**G5 safe.** CA 900, CL 400, TA 2000, RE 800, EBIT 300, equity 1200 → TL 800, X1 0.25, X2 0.4, X3 0.15, X4 1.5,
Z'' 5.527, zone safe → **HOLD**, veto False.

**G6 precedence.** Base = G1; one change each. Metrics follow the Formulas section in every case.
| Case | Change | Result |
|---|---|---|
| a | eligible False, sector "Financials" | `NOT_ELIGIBLE`, veto None; metrics still computed (Z'' 2.2847999999999997, zone grey); eligible None likewise |
| b | sector "Financials" (or "Utilities", "Real Estate") | `NO_SIGNAL / EXCLUDED_SECTOR`, veto None, metrics computed |
| c | sector None (or "") | `EXCLUDED_SECTOR` |
| d | retained_earnings None, sector "Utilities" | `EXCLUDED_SECTOR` (row 2 before 3) |
| e | current_liabilities None | `NO_DATA`; X1 None, Z'' None, zone None; X2 0.2, X3 0.09, X4 0.6666666666666666 still output |
| f | ebit_ttm None (or total_equity None) | `NO_DATA`; X3 (resp. X4, TL) None, Z'' None |
| g | total_assets 0 | `NON_POSITIVE_ASSETS`; X1–X4, Z'' None; TL −800 |
| h | total_assets 0, equity −100 | `NON_POSITIVE_ASSETS` (row 4 before 5); TL 100; flag `NEG_EQUITY` |
| i | equity 2000 (= TA) | `NON_POSITIVE_LIABILITIES`; TL 0, X4 and Z'' None, X1–X3 as G1 |
| j | equity 2100 | `NON_POSITIVE_LIABILITIES`; TL −100 |
| k | cfo None, debt None (G1 or G5) | signal unaffected (HOLD); cfo/debt never block a signal |
`run_altman_z([G1, G2, G6e, G6a])` returns four results in that order, each identical to evaluating it alone.

**G7 boundaries** (on the functions directly). `zone(1.10)` = "grey", `map_signal(1.10)` = HOLD; `zone(1.0999999)` =
"distress", `map_signal(1.0999999)` = SELL; `zone(2.60)` = "grey"; `zone(2.6000001)` = "safe";
`map_signal(2.6000001)` = HOLD. Market `"LSE"` → `ValueError`.

## Property expectations
- G never emits BUY. SELL ⇒ zone distress and no `CASH_RICH_OVERRIDE`; HOLD with zone distress ⇔ flag
  `CASH_RICH_OVERRIDE`; `NO_OVERRIDE_DATA` ⇒ SELL.
- `distress_veto` is True ⇔ SELL, False ⇔ HOLD, None otherwise.
- Scaling every monetary input of a name by k > 0 leaves X1–X4, Z'', cfo_to_debt, zone and signal unchanged (to 1e-9).
- With the other inputs fixed (and TA > 0, TL > 0), Z'' is non-decreasing in CA, RE and EBIT and non-increasing in CL.
- A name's result depends only on its own inputs (order, other names and other markets never matter).

## Data mapping and look-ahead (engine, SW)
| Input | Source |
|---|---|
| total_assets, current_assets, current_liabilities, retained_earnings, total_equity, total_debt | the balance-sheet filing `build_inputs` selected (`filed_date ≤ as_of`, the **same filing** for all, `ttm.md` §5.2, §6; TSX → `PIT_ESTIMATED`); absent item → None, never taken from another filing or source |
| ebit_ttm, cfo_ttm | `flow_ttm(ebit_q)`, `flow_ttm(cfo_q)`: 4 discrete quarters filed ≤ as_of (`ttm.md` §5). Currency ≠ balance-sheet currency → None + `FLOW_CCY_MISMATCH` (as Model J). EBIT TTM period_end ≠ balance-sheet period_end → flag `EBIT_PERIOD_MISMATCH` (informational, not a gate) |
| eligible, market, sector | as Model A (E1–E5) |
Known gaps: unclassified balance sheets (no `AssetsCurrent`/`LiabilitiesCurrent`) give `NO_DATA`; EDGAR and Yahoo
`TotalDebt` definitions differ (`ttm.md` §5.2), which moves only the override ratio; TSX Yahoo history is ~5 quarters.

## Report and backtest
- Report: `screen_signals` adds `g_signal, g_no_signal_reason, g_z, g_zone, g_x1, g_x2, g_x3, g_x4, g_cfo_to_debt,
  g_distress_veto` after the `j_*` columns and before `flags`; G flags join `flags`. The disclaimer lists
  `altman_z/0.1.0` and adds "Model G is new and unvalidated (not backtested)." G has no vote.
- Backtest: **deferred** (user decision). G is in no backtest run, in-sample or holdout. The proposal's hypothesis is
  recorded for a later pre-registration only: the distress bucket (maybe 2–5% of eligible names) lags its sector median
  by > 3%/yr with hit rate > 55%; names it would veto from `SCREEN_BUY` have lower forward returns than un-vetoed BUYs;
  pass bar: the veto improves the combined BUY bucket's 12-month return in ≥ 60% of years (IC reported only).
- External checks (QA, `docs/qa/model_g_validation.md`): each X_i's numerator and denominator vs the 10-K/10-Q balance
  sheet (and income statement for EBIT) for 2–3 US names, exact; Z'' independently recomputed to 1e-9. Yahoo has no
  Z-score, so there is no external Z'' to compare.

## Risks
Buyback-heavy firms with negative retained earnings and equity score distressed while generating cash (mitigated only by
the override, which needs reported debt > 0); Z'' was fitted decades ago on US firms and its zones are not recalibrated;
goodwill-heavy balance sheets inflate X4 (book equity); unclassified balance sheets fall to `NO_DATA`; EBIT TTM can
include an EDGAR Q4 derived as FY − 9M (`ttm.md` §1); TSX fundamental history is short. *Overfitting risk:* **low**:
every constant is a published Altman value or from the proposal, written before any backtest, and G has never been run
on returns.

## Changelog
- **0.1.0:** initial spec from proposal §4. Proposal goldens reproduced (Z'' 2.2848 grey HOLD; −0.402506 distress
  SELL); explicit evaluation order, CASH_RICH_OVERRIDE (strict > 0.25, zone unchanged), `NO_OVERRIDE_DATA`,
  `distress_veto`, negative-equity, safe, precedence and boundary goldens added; arithmetic verified with a throwaway
  script.
