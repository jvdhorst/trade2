# Model spec: EBIT yield + return on capital (`ebit_yield_roc`, v0.1.0) — Model J

Owner: AN · Tests: QA (`tests/unit/test_ebit_yield_roc_spec.py`) · Code: `src/valuation/models/ebit_yield_roc.py` ·
Source: proposal `docs/models/proposals/additional_models.md` §7 (shortlist §12, order 2). **Not part of FROZEN_V2**:
J is not in `revision_v2_freeze.md` and adding it must not change that freeze's hash. **No new data**: EBIT TTM, total
debt and cash are Phase 9 items (`ttm.md` §5); market cap, equity, preferred and NCI already exist.

## Purpose
*Is this a good business (high pre-tax return on the capital it uses) at a cheap price (high pre-tax earnings yield on
enterprise value)?* The two are ranked jointly, Greenblatt-style. Enterprise value puts levered and unlevered firms on
one scale, which P/E and P/B do not, and EBIT exists in the asset-light sectors where Model A is `NO_SIGNAL`. Evidence:
Greenblatt (2005), *The Little Book That Beats the Market*; Gray & Carlisle (2012), *Quantitative Value* (the EBIT/EV
leg does most of the work). J is a relative screen: it says which names in today's universe look best on both legs,
plus an absolute yield floor against the 10-year bond.

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote.

## Constants
`MODEL_VERSION = "ebit_yield_roc/0.1.0"`, `BUY_PCT = 0.20`, `SELL_PCT = 0.80`, `BUY_SPREAD = 0.03`, `SELL_SPREAD = 0.0`,
`MIN_UNIVERSE = 12`, `EXCLUDED_SECTORS = frozenset({"Financials", "Utilities", "Real Estate"})`,
`CYCLICAL_SECTORS = frozenset({"Energy", "Materials"})`. All from proposal §7. They are starting values, **never tuned
on the holdout** (PM decision 2026-11-09), and J has never been backtested, so none was chosen after a result.

## Inputs (`JInputs`, frozen dataclass, one ticker at `as_of`)
| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`) | as Model A | |
| `sector` | GICS sector, `str \| None`; drives the exclusion and `CYCLICAL_EBIT` | |
| `eligible` | eligibility E1–E5 (`bool \| None`); anything but `True` → `NOT_ELIGIBLE` | |
| `market_cap` | trading ccy, price × all-class shares (the caps `run_signals` already computes) | None |
| `fx_rate` | trading-ccy units per reporting unit; must be > 0 (else `ValueError`) | 1.0 |
| `ebit_ttm` | reporting ccy; None on NO_TTM or FLOW_CCY_MISMATCH | None |
| `total_debt`, `cash` | reporting ccy, balance sheet (`ttm.md` §5.2); **never defaulted to 0** | None |
| `total_equity` | reporting ccy, as stored: **includes NCI and preferred** | None |
| `preferred`, `nci` | reporting ccy | 0.0, 0.0 |
| `flags` | passed through (`PIT_ESTIMATED`, `NO_TTM`, `STALE_TTM`, `FLOW_CCY_MISMATCH`, `EBIT_PERIOD_MISMATCH`). The engine passes `NO_TTM`/`STALE_TTM` with Model A's meaning (NI TTM) only; a missing EBIT TTM is visible as reason `NO_DATA`, not as a new flag | frozenset() |

`JResult`: `ticker, market, signal, no_signal_reason, ev, ey, ic, roc, rank_ey, rank_roc, combined, position, n, pct,
flags`. API: `compute_metrics(inp)` → per-name metrics (ev, ey, ic, roc); `map_signal(pct, ey, rf)`;
`run_ebit_yield_roc(inputs: Sequence[JInputs], rf: Mapping[str, float | None], min_universe: int = MIN_UNIVERSE)
-> list[JResult]` (one result per input, same order). `rf` is keyed by market, decimal (`{"US": 0.035}`).

## Formulas (per name, reporting ccy)
```
cap_rep = market_cap / fx_rate
EV      = cap_rep + total_debt + preferred + nci − cash
EY      = EBIT_TTM / EV                       (None if EV ≤ 0)
IC      = total_equity + total_debt − cash    financing side; total_equity already holds NCI and preferred
ROC     = EBIT_TTM / IC                       (None if IC ≤ 0)
```
Metrics are computed whenever their inputs exist, also for NOT_ELIGIBLE and excluded names. A None input makes every
metric that depends on it None (cash None → EV, EY, IC, ROC None); nothing is filled in. Greenblatt's ROC uses net
working capital + net fixed assets; the financing-side IC is the equivalent that can be read off any balance sheet and
checked (stated deviation, as the proposal). EV adds preferred and NCI explicitly because market_cap covers common
only; IC does not, because `total_equity` already contains them.

## Cross-section (per market, not pooled)
Everything below runs **separately for each market m**; markets never influence each other. Reason (as Model C 0.2.0,
`composite.md` R2): `rf` differs (CA10Y vs US10Y) and pooled quintiles would sort the market and currency, not the
stock. J is **not sector-neutral by design** (proposal §7): the BUY list tilts to whichever sector is cheap; the
sector exclusion is the only sector rule.

1. **Ranking set** `R_m` = inputs with `market == m`, `eligible is True`, sector non-empty and not in
   `EXCLUDED_SECTORS`, `ebit_ttm > 0`, `EV > 0`, `IC > 0`. `n = |R_m|`.
2. **Ranks** within `R_m`: `rank_ey` = 1 for the highest EY, `rank_roc` likewise; equal values share the best rank
   (`scipy.stats.rankdata(-x, method="min")`).
3. `combined = rank_ey + rank_roc`. Order `R_m` by (`combined` asc, `rank_ey` asc, `ticker` asc); the ticker is the
   stated deterministic last tie-break. `position` = 1..n in that order; `pct = position / n`.
4. Ranks, combined, position, n and pct are output for every name in `R_m`, also when `n < min_universe`
   (informational); None for names outside `R_m`, which never affect any other name.

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` (reason None) | `eligible is not True` |
| 2 | `NO_SIGNAL` / `EXCLUDED_SECTOR` | sector None or empty, or in `EXCLUDED_SECTORS` |
| 3 | `NO_SIGNAL` / `NO_DATA` | any of `market_cap`, `ebit_ttm`, `total_debt`, `cash`, `total_equity` is None |
| 4 | `NO_SIGNAL` / `NON_POSITIVE_EBIT` | `ebit_ttm ≤ 0` |
| 5 | `NO_SIGNAL` / `NON_POSITIVE_EV` | `EV ≤ 0` |
| 6 | `NO_SIGNAL` / `NEG_IC` | `IC ≤ 0` (negative-equity or cash-rich firms) |
| 7 | `NO_SIGNAL` / `SMALL_UNIVERSE` | `n < min_universe` for the name's market (12 is enough, 11 is not) |
| 8 | `NO_SIGNAL` / `NO_RF` | `rf.get(market)` missing or None |
| 9 | `map_signal(pct, ey, rf)` | below |

```
map_signal(pct, ey, rf): "BUY"  if pct ≤ BUY_PCT  and ey ≥ rf + BUY_SPREAD
                         "SELL" if pct > SELL_PCT and ey ≤ rf + SELL_SPREAD
                         "HOLD" otherwise
```
Plain float comparisons, no epsilon (in IEEE doubles `0.035 + 0.03 == 0.065` and `0.09 + 0.03 == 0.12`, verified).
Flags: `NEG_IC` (whenever IC is computed and ≤ 0, whatever the signal), `CYCLICAL_EBIT` (sector in
`CYCLICAL_SECTORS`, set for every name of that sector whatever the signal; informational: peak-cycle EBIT ranks high on both legs; the proposal's 3-year average EBIT is
deferred because TSX Yahoo history is ~5 quarters), `TOP_PCT_LOW_YIELD` (set only when `map_signal` runs, i.e. not under `SMALL_UNIVERSE`/`NO_RF`: `pct ≤ 0.20` but `ey < rf + 0.03`, so HOLD),
plus the pass-through flags.

## Worked examples (golden values; relative tolerance 1e-9)
Unless stated: market `"US"`, sector `"Industrials"`, eligible True, fx 1, preferred = nci = 0, `rf = {"US": 0.035}`.

**G1 (proposal §7, `min_universe=5`).** Inputs (cap, debt, cash, equity, EBIT) in $M:
P (2000, 700, 200, 500, 300), Q (3500, 600, 100, 0, 200), R (1200, 400, 100, 1200, 150), T (4000, 1500, 500, 1000, 400),
S (4500, 700, 200, 1500, 100).

| Name | EV | EY | IC | ROC | rank_ey | rank_roc | combined | position | pct | Signal |
|---|---|---|---|---|---|---|---|---|---|---|
| P | 2500 | 0.12 | 1000 | 0.3 | 1 | 2 | 3 | 1 | 0.2 | **BUY** (0.12 ≥ 0.065) |
| Q | 4000 | 0.05 | 500 | 0.4 | 4 | 1 | 5 | 2 | 0.4 | HOLD |
| R | 1500 | 0.1 | 1500 | 0.1 | 2 | 4 | 6 | 3 | 0.6 | HOLD |
| T | 5000 | 0.08 | 2000 | 0.2 | 3 | 3 | 6 | 4 | 0.8 | HOLD (0.8 is not > 0.8) |
| S | 5000 | 0.02 | 2000 | 0.05 | 5 | 5 | 10 | 5 | 1.0 | **SELL** (0.02 ≤ 0.035) |

R and T tie on combined 6; R's better rank_ey puts it first. Q has `total_equity` 0 yet IC = 500 > 0 (debt − cash), so
it is ranked; zero equity is not a gate.

**G2.** G1 with the default `min_universe=12`: all five `NO_SIGNAL / SMALL_UNIVERSE`, n = 5; ranks, combined, position
and pct still output exactly as G1.

**G3 rf thresholds (G1 universe, `min_universe=5`, only `rf["US"]` changed).**
| rf | P (EY 0.12, pct 0.2) | S (EY 0.02, pct 1.0) |
|---|---|---|
| 0.09 | **BUY** (0.09 + 0.03 == 0.12 exactly; ≥) | SELL |
| 0.0901 | HOLD + `TOP_PCT_LOW_YIELD` (0.1201) | SELL |
| 0.095 | HOLD + `TOP_PCT_LOW_YIELD` (0.125) | SELL |
| 0.02 | BUY | **SELL** (0.02 ≤ 0.02) |
| 0.0199 | BUY | HOLD |
Q, R, T stay HOLD in every row (T: EY 0.08 ≤ 0.09 but pct 0.8 is not > 0.8). Also `map_signal(0.2, 0.065, 0.035)` =
BUY, `map_signal(0.2000001, 0.5, 0.0)` = HOLD, `map_signal(0.8000001, 0.035, 0.035)` = SELL.

**G4 FX.** `market="TSX"`, USD reporter, market_cap 2800 (CAD), fx 1.4, other inputs as P → cap_rep
2000.0000000000002 (float repr), EV 2500.0, EY 0.12, IC 1000, ROC 0.3: the same as P. `fx_rate=0` or market `"LSE"`
→ `ValueError`.

**G5 precedence.** Base = P; one change each. Metrics follow the Formulas section in every case.
| Case | Change | Result |
|---|---|---|
| a | eligible False, sector "Financials" | `NOT_ELIGIBLE` (EV 2500, EY 0.12 still computed); eligible None likewise |
| b | sector "Financials" | `NO_SIGNAL / EXCLUDED_SECTOR` |
| c | sector None (or "") | `EXCLUDED_SECTOR` |
| d | ebit None, sector "Utilities" | `EXCLUDED_SECTOR` (row 2 before 3) |
| e | cash None | `NO_DATA`; ev, ey, ic, roc all None |
| f | ebit 0 | `NON_POSITIVE_EBIT`; EY 0.0, ROC 0.0 |
| g | ebit −10, cap 100, debt 0, cash 105, equity 10 | `NON_POSITIVE_EBIT` (row 4 before 5); EV −5, IC −95, flag `NEG_IC` |
| h | ebit 10, cap 100, debt 0, cash 150, equity 50 | `NON_POSITIVE_EV` + flag `NEG_IC`; EV −50, IC −100, ey = roc = None |
| i | ebit 100, cap 3000, debt 500, cash 100, equity −800 | `NEG_IC` + flag `NEG_IC`; EV 3400, EY 0.029411764705882353, IC −400 |
| j | sector "Energy" | ranked as P, flag `CYCLICAL_EBIT` |
Adding cases a–i to the G1 universe (`min_universe=5`) leaves n = 5 and every G1 result unchanged.

**G6 ties.** G1 plus `O` = an exact copy of P's inputs (input order P before O), `min_universe=6`. O and P share
rank_ey 1 and rank_roc 2 (min method; R gets rank_ey 3), combined 3 each; the ticker breaks the tie:
O position 1, pct 0.16666666666666666 → **BUY**; P position 2, pct 0.3333333333333333 → HOLD (not top quintile).
Then Q (5, 1, 6) pos 3 pct 0.5 HOLD; R (3, 5, 8) pos 4 pct 0.6666666666666666 HOLD; T (4, 4, 8) pos 5 pct
0.8333333333333334 HOLD (EY 0.08 > 0.035); S (6, 6, 12) pos 6 pct 1.0 SELL. Identical fundamentals can get different
signals at a quintile edge; that is the stated cost of a deterministic tie-break.

**G7 market separation.** US: U01..U12 with cap 1000, debt 0, cash 0, equity 1000, EBIT 10·i (EY = ROC = 0.01·i).
TSX: the five G1 names (tickers suffixed `.TO`), `rf = {"US": 0.035, "TSX": 0.03}`, default `min_universe=12`.
US n = 12: U12 pos 1 pct 0.08333333333333333 BUY, U11 pct 0.16666666666666666 BUY, U10–U04 HOLD, U03 (pct
0.8333333333333334, EY 0.03) SELL, U02, U01 SELL. TSX n = 5: all `SMALL_UNIVERSE`. Changing, adding or removing TSX
names (or `rf["TSX"]`) never changes any US result; dropping U01 makes every US name `SMALL_UNIVERSE` (11 < 12).

## Property expectations
- Scaling every monetary input of every name by k > 0 leaves EY, ROC, ranks, pct and signals unchanged; changing the
  reporting currency with a matching `fx_rate` leaves them unchanged.
- Raising one name's EBIT (others fixed, the name staying in `R_m`) never worsens its rank_ey, rank_roc or position.
- `pct ∈ (0, 1]`; positions in `R_m` are a permutation of 1..n.
- `n < min_universe` → no BUY/HOLD/SELL in that market. A name outside `R_m` never affects any other result.
- BUY ⇒ `ey ≥ rf + 0.03` and `pct ≤ 0.20`; SELL ⇒ `ey ≤ rf` and `pct > 0.80`.
- Per-market independence: results for market m depend only on market m's inputs and `rf[m]`.

## Data mapping and look-ahead (engine, SW)
| Input | Source |
|---|---|
| market_cap | price (close on/before as_of) × `shares_outstanding` from `build_inputs` (all classes × `share_conversion` × split factor) |
| fx_rate, total_equity, preferred (`preferred_equity`), nci (`noncontrolling_interest`), total_debt, cash | the balance-sheet filing `build_inputs` selected (`filed_date ≤ as_of`; TSX `PIT_ESTIMATED`); absent item → None (preferred/nci → 0.0, as stored) |
| ebit_ttm | `flow_ttm(ebit_q)`: 4 discrete quarters filed ≤ as_of (`ttm.md` §5; EDGAR `OperatingIncomeLoss`, Yahoo `quarterlyOperatingIncome`). EBIT currency ≠ balance-sheet currency → None + `FLOW_CCY_MISMATCH` (as Model C's `_flow`). TTM period_end ≠ balance-sheet period_end → flag `EBIT_PERIOD_MISMATCH` (informational, not a gate) |
| rf | `risk_free` per market (TSX CA10Y, US US10Y) on/before as_of; None → `NO_RF` |
| eligible, market, sector | as Model A (E1–E5) |
Known gaps: operating leases and pensions are not in `total_debt`; EDGAR (D1–D5) and Yahoo `TotalDebt` definitions
differ (`ttm.md` §5.2), so EV is not comparable across sources to the dollar; `OperatingIncomeLoss` is absent for some
filers (EBIT TTM None → `NO_DATA`); TSX Yahoo history is ~5 quarters.

## Report and backtest
- Report: `screen_signals` adds `j_signal, j_no_signal_reason, j_ey, j_roc, j_ev, j_rank_ey, j_rank_roc, j_pct` after
  the `h_*` columns and before `flags`; J flags join `flags`. The disclaimer lists `ebit_yield_roc/0.1.0` and adds
  "Model J is new and unvalidated (not backtested)." J has no vote.
- Backtest: **deferred** (user decision). J is in no backtest run, holdout or in-sample. The proposal's hypothesis is
  recorded for a later pre-registration only: top-minus-bottom quintile 12-month spread 3–6%/yr, IC 0.04–0.06, and
  the check EY alone vs the joint rank (if ROC adds no IC, keep the simpler model).
- Per-company fit ([`per_company_fit.md`](per_company_fit.md) v0.1.0, user decision 2026-11-13): J's label is now stored in the **in-sample** backtest (`bt_rows.j_signal`, labelled unvalidated) and evaluated per company (SELL_EXIT and BUY_ONLY); never on holdout dates. Label → position mapping in that spec §3.3. No parameter changes.
- External checks (QA, `docs/qa/model_j_validation.md`): EV vs Yahoo `enterpriseValue` within ±10% (debt definitions
  differ); EBIT TTM vs 10-K/10-Q operating income, exact, for 2–3 US names; EY vs 1 / Yahoo `enterpriseToEbitda` only
  as a sanity bound (EBIT < EBITDA, so EY should be below it).

## Risks
Peak-cycle EBIT in Energy and Materials ranks high on both legs at the worst time (flag only, no fix in 0.1.0);
operating leases and pensions understate EV and IC for lease-heavy retailers and airlines; negative IC after large
buybacks drops quality names to `NEG_IC`; EBIT tag variety (`OperatingIncomeLoss` vs issuer-specific lines) makes some
names `NO_DATA`; the BUY list can concentrate in one cheap sector. *Overfitting risk:* **low**: every constant comes
from the proposal, written before any backtest, and J has never been run on returns. Residual risk: J was shortlisted
after A and B had failed, though not on J's own results.

## Changelog
- **0.1.0:** initial spec from proposal §7. Proposal golden reproduced (EV 2500/4000/1500/5000/5000, EY
  0.12/0.05/0.1/0.08/0.02, ROC 0.3/0.4/0.1/0.2/0.05 → P BUY, S SELL); per-market cross-section, ticker tie-break,
  precedence, FX, tie and market-separation goldens added; arithmetic verified with a throwaway script.
