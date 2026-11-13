# Model spec: Piotroski F-score (`piotroski`, v0.1.0) — Model E

Owner: AN · Tests: QA (`tests/unit/test_piotroski_spec.py`, `tests/unit/test_piotroski_engine_spec.py`) · Code:
`src/valuation/models/piotroski.py` · Source: proposal `docs/models/proposals/additional_models.md` §2 (data gaps §13).
**Not part of FROZEN_V2**: E is not in `revision_v2_freeze.md` and adding it must not change that freeze's hash.
**No new data**: NI, CFO, gross profit and revenue quarters (`ttm.md` §1, §5.1), total assets and total debt (§5.2),
current assets and current liabilities (§6, data batch 3) and share counts already exist. What E adds is the
*prior-period* lookups (balance sheets one and two years back, the prior TTM), all from stored data.

## Purpose
*Is the firm's accounting position getting stronger or weaker?* Nine binary signals on profitability,
leverage/liquidity and operating efficiency, summed to F (0–9). It is a financial-strength screen that separates
improving from deteriorating firms, and it needs no price. Evidence: Piotroski (2000), *Value Investing: The Use of
Historical Financial Statement Information to Separate Winners from Losers*; the effect is strongest among cheap (high
book-to-market) stocks and weaker in large caps. E is per name (no cross-section, no universe size, no risk-free rate).

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote. E has no vote.

## Constants
`MODEL_VERSION = "piotroski/0.1.0"`, `N_SIGNALS = 9`, `MIN_SCORED = 8`, `BUY_F_9 = 8` (BUY bar when 9 signals are
scored), `BUY_F_8 = 7` (BUY bar when 8 are scored), `SELL_F = 2`, `SHARE_TOL_NUM = 201`, `SHARE_TOL_DEN = 200`
(the proposal's 1.005 = 201/200, 0.5% tolerance for option dilution), `PRIOR_WINDOW_DAYS = (350, 380)`,
`PRIOR2_WINDOW_DAYS = (715, 745)`, `EXCLUDED_SECTORS = frozenset({"Financials"})`. All are starting values from
proposal §2 (the windows follow Model H's one-year rule), **never tuned on the holdout** (PM decision 2026-11-09), and
E has never been backtested, so none was chosen after a result.

## Inputs (`EInputs`, frozen dataclass, one ticker at `as_of`)
Subscripts: `t` = the balance-sheet date (see Data mapping), `4q` = one year earlier, `8q` = two years earlier; "TTM" =
the four quarters ending at `t`, "TTM prior" = the four quarters ending one year earlier.

| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`) | as Model A | |
| `sector` | GICS sector, `str \| None`; drives the exclusion | |
| `eligible` | eligibility E1–E5 (`bool \| None`); anything but `True` → `NOT_ELIGIBLE` | |
| `ni_ttm`, `ni_ttm_prior` | NI to common, TTM and TTM prior, reporting ccy | None |
| `cfo_ttm` | operating cash flow TTM | None |
| `gp_ttm`, `gp_ttm_prior` | gross profit TTM and TTM prior | None |
| `rev_ttm`, `rev_ttm_prior` | revenue TTM and TTM prior | None |
| `ta_t`, `ta_4q`, `ta_8q` | total assets at t, t−4q, t−8q | None |
| `debt_t`, `debt_4q` | total debt at t, t−4q (0 is a value, not missing) | None |
| `ca_t`, `cl_t`, `ca_4q`, `cl_4q` | current assets / current liabilities at t and t−4q | None |
| `shares_t`, `shares_4q` | all-class share counts (after `share_conversion`) of the t and t−4q filings, each on its own filing's basis | None |
| `split_factor` | product of split ratios dated in (`period_end_4q`, `t`]; puts `shares_4q` on t's basis; must be > 0 (else `ValueError`) | 1.0 |
| `flags` | passed through (`PIT_ESTIMATED`, `E_PERIOD_MISMATCH`, `FLOW_CCY_MISMATCH`, `PRIOR_CCY_MISMATCH`) | frozenset() |

No monetary input is ever defaulted to 0. **No FX input**: every ratio's numerator and denominator are in the reporting
currency (flows in another currency are None + `FLOW_CCY_MISMATCH`, prior filings in another currency give None +
`PRIOR_CCY_MISMATCH`, Data mapping), so the trading currency never enters.

`EResult`: `ticker, market, signal, no_signal_reason, f_score, n_scored, signals, roa, roa_prior, lev_t, lev_prior,
cr_t, cr_prior, shares_prev_adj, gm_t, gm_prior, ato_t, ato_prior, flags`, where `signals: tuple[int | None, ...]` has
length 9 (signal 1 first), each element `1`, `0` or `None` (unscored). API:
`compute_metrics(inp) -> EMetrics(signals, f_score, n_scored, roa, roa_prior, lev_t, lev_prior, cr_t, cr_prior,
shares_prev_adj, gm_t, gm_prior, ato_t, ato_prior, prior_ta_fallback)`; `map_signal(f_score, n_scored) -> "BUY" |
"HOLD" | "SELL"` (`ValueError` unless `n_scored` is 8 or 9); `evaluate(inp) -> EResult`;
`run_piotroski(inputs: Sequence[EInputs]) -> list[EResult]` (one result per input, same order). Each name is evaluated
alone, so markets (and names) are trivially independent.

## Formulas (per name, reporting ccy)
`q(a, b)` = `a / b` if both are not None and `b > 0`, else None (a single float division; a denominator ≤ 0 is a failed
guard, not a value).
```
ta_8q_eff       = ta_8q if ta_8q is not None else ta_4q        PRIOR_TA_FALLBACK substitution (missing only;
                                                                a present ta_8q ≤ 0 is NOT replaced)
roa             = q(ni_ttm, ta_4q)
roa_prior       = q(ni_ttm_prior, ta_8q_eff)
lev_t           = q(debt_t, ta_t)            lev_prior = q(debt_4q, ta_4q)
cr_t            = q(ca_t, cl_t)              cr_prior  = q(ca_4q, cl_4q)
gm_t            = q(gp_ttm, rev_ttm)         gm_prior  = q(gp_ttm_prior, rev_ttm_prior)
ato_t           = q(rev_ttm, ta_4q)          ato_prior = q(rev_ttm_prior, ta_8q_eff)
shares_prev_adj = shares_4q * split_factor   (None if shares_4q is None or ≤ 0)
```

| # | Group | Scored (not None) when | Signal = 1 when (else 0) |
|---|---|---|---|
| 1 | profitability | `roa` not None (needs `ni_ttm`, `ta_4q > 0`) | `roa > 0` |
| 2 | | `cfo_ttm` not None | `cfo_ttm > 0` |
| 3 | | `roa` and `roa_prior` not None (needs `ni_ttm_prior`, `ta_8q_eff > 0`) | `roa > roa_prior` |
| 4 | | `cfo_ttm` and `ni_ttm` not None | `cfo_ttm > ni_ttm` (negative accruals) |
| 5 | leverage/liquidity | `lev_t` and `lev_prior` not None (needs `ta_t > 0`, `ta_4q > 0`) | `lev_t < lev_prior` |
| 6 | | `cr_t` and `cr_prior` not None (needs `cl_t > 0`, `cl_4q > 0`) | `cr_t > cr_prior` |
| 7 | | `shares_t` not None and `> 0`, `shares_prev_adj` not None | `shares_t * 200 <= shares_prev_adj * 201` (no net issuance beyond 0.5%) |
| 8 | efficiency | `gm_t` and `gm_prior` not None (needs `rev_ttm > 0`, `rev_ttm_prior > 0`) | `gm_t > gm_prior` |
| 9 | | `ato_t` and `ato_prior` not None (needs `ta_4q > 0`, `ta_8q_eff > 0`) | `ato_t > ato_prior` |

```
n_scored          = number of signals that are not None                 (0..9)
f_score           = sum of the scored signals; None when n_scored == 0  (0..n_scored)
prior_ta_fallback = ta_8q is None and (signal 3 or signal 9 is scored)  → flag PRIOR_TA_FALLBACK
map_signal(f, n): n == 9: "BUY" if f ≥ 8 ; n == 8: "BUY" if f ≥ 7 ;
                  otherwise "SELL" if f ≤ 2 ; else "HOLD"                (ValueError if n not in {8, 9})
```
**Evaluation order** (so goldens reproduce bit for bit): every ratio is one division `a / b` as written; comparisons are
plain float comparisons, no epsilon. Strict `>` / `<` everywhere except signal 7, so **equal ratios score 0** (a firm
whose leverage stays exactly flat, including debt-free in both years, scores 0 on signal 5). Signal 7 is evaluated in
the cross-multiplied form `shares_t * SHARE_TOL_DEN <= shares_prev_adj * SHARE_TOL_NUM`, i.e.
`shares_t * 200 <= shares_prev_adj * 201`, with `shares_prev_adj = shares_4q * split_factor` computed first. Reason
(verified): the double nearest 1.005 is slightly below 1.005, so the naive `shares_t <= shares_4q * 1.005` gives
`100.5e6 <= 100e6 * 1.005` = `100500000.0 <= 100499999.99999999` = **False**; the cross-multiplied form gives
`20100000000.0 <= 20100000000.0` = True. For whole share counts below about 4.4e13 both products are exact integers,
so the test is an exact rational comparison. No `shares_limit` is output (its float would read 100499999.99999999 and
mislead).

**Metrics are computed whenever their inputs exist**, also for NOT_ELIGIBLE and excluded names (as Models J and G):
signals, n_scored, f_score and every ratio are always output. A None input makes the ratios and signals that depend on
it None; nothing is filled in, and a missing input is never scored as 0. A ratio is output even when the matching
signal is unscored (e.g. `roa_prior` with `roa` None).

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` (reason None) | `eligible is not True` |
| 2 | `NO_SIGNAL` / `EXCLUDED_SECTOR` | sector None or empty, or in `EXCLUDED_SECTORS` (Financials: no gross margin, current ratio or meaningful CFO) |
| 3 | `NO_SIGNAL` / `F_INCOMPLETE` | `n_scored < 8` (f_score and signals still output) |
| 4 | `map_signal(f_score, n_scored)` | BUY / HOLD / SELL |

`F_INCOMPLETE` is the reason only (not also a flag). Real Estate and Utilities are **scored**: a filer without gross
profit has signal 8 unscored (n_scored ≤ 8), one with an unclassified balance sheet has signal 6 unscored; either alone
still allows a signal at n_scored = 8.

**Flags (model):** `PRIOR_TA_FALLBACK` (as defined above; set whatever the signal, also on NOT_ELIGIBLE / excluded).
**Reserved, never set in 0.1.0:** `ANNUAL_PRIOR` (prior TTM from fiscal-year totals; not implementable, see Data
mapping). Pass-through flags as listed under Inputs.

## Worked examples (golden values; relative tolerance 1e-9)
Unless stated: market `"US"`, sector `"Industrials"`, eligible True, split_factor 1.0, flags empty, $M. Full float reprs
are given (computed with the evaluation order above; plain decimals are exact reprs). Signal strings use `1`/`0`/`-`
(unscored) in signal order 1..9.

**E1 (proposal golden).** ni_ttm 120, ni_ttm_prior 100, cfo_ttm 150, ta_t 1000, ta_4q 950, ta_8q 900, debt_t 300,
debt_4q 310, ca_t 400, cl_t 200, ca_4q 380, cl_4q 200, shares_t 100e6, shares_4q 101e6, gp_ttm 420, rev_ttm 1200,
gp_ttm_prior 400, rev_ttm_prior 1150.

| # | Intermediate (float repr) | Signal |
|---|---|---|
| 1 | roa = 120/950 = `0.12631578947368421` > 0 | 1 |
| 2 | cfo 150 > 0 | 1 |
| 3 | roa_prior = 100/900 = `0.1111111111111111` < roa | 1 |
| 4 | 150 > 120 | 1 |
| 5 | lev_t = 300/1000 = `0.3` < lev_prior = 310/950 = `0.3263157894736842` | 1 |
| 6 | cr_t = `2.0` > cr_prior = `1.9` | 1 |
| 7 | shares_prev_adj `101000000.0`; `100e6*200 = 2.0e10` ≤ `101e6*201 = 2.0301e10` | 1 |
| 8 | gm_t = `0.35` > gm_prior = 400/1150 = `0.34782608695652173` | 1 |
| 9 | ato_t = 1200/950 = `1.263157894736842` vs ato_prior = 1150/900 = `1.2777777777777777` | **0** |

→ signals `(1,1,1,1,1,1,1,1,0)` (`111111110`), **f_score 8, n_scored 9 → BUY**, no model flags.

**E2 (proposal fallback variant).** E1 with ta_8q None → ta_8q_eff = 950: roa_prior = 100/950 = `0.10526315789473684`
(signal 3 still 1), ato_prior = 1150/950 = `1.2105263157894737` < `1.263157894736842` → signal 9 = **1**; other
intermediates as E1. → `111111111`, **f_score 9, n_scored 9 → BUY**, flag `PRIOR_TA_FALLBACK`. The fallback changes
the score, so the flag must be visible.
- **E2b:** E1 with ta_8q None and ni_ttm_prior None → roa_prior None, signal 3 unscored; signal 9 scored via the
  fallback (ato_prior `1.2105263157894737`) → `11-111111`, f 8, n 8 → **BUY**, flag `PRIOR_TA_FALLBACK`.

**E3 n_scored = 8 (Real Estate without gross profit).** E1 with sector `"Real Estate"`, gp_ttm None, gp_ttm_prior None
→ gm_t = gm_prior = None, signal 8 unscored → `1111111-0`, **f 7, n 8 → BUY** (≥ 7 at n = 8). The same inputs with
sector `"Industrials"` give the same result (no sector rule beyond the exclusion).

**E4 n_scored = 8, F = 6.** E3 with ca_t 380 → cr_t `1.9` == cr_prior `1.9` → signal 6 = **0** (strict `>`, tie) →
`1111101-0`, **f 6, n 8 → HOLD**.

**E5 n_scored = 9, F = 7.** E1 with ca_t 380 → `111110110`, **f 7, n 9 → HOLD** (BUY needs 8 at n = 9).

**E6 SELL.** ni_ttm −50, ni_ttm_prior −20, cfo_ttm −30, ta_t 1000, ta_4q 950, ta_8q 900, debt_t 400, debt_4q 310, ca_t
300, cl_t 250, ca_4q 380, cl_4q 200, shares_t 110e6, shares_4q 100e6, gp_ttm 300, rev_ttm 1200, gp_ttm_prior 400,
rev_ttm_prior 1150 → roa `-0.05263157894736842`, roa_prior `-0.022222222222222223`, lev_t `0.4`, lev_prior
`0.3263157894736842`, cr_t `1.2`, cr_prior `1.9`, shares_prev_adj `100000000.0`, gm_t `0.25`, gm_prior
`0.34782608695652173`, ato_t `1.263157894736842`, ato_prior `1.2777777777777777` → `000100000` (only signal 4: −30 >
−50), **f 1, n 9 → SELL**.
- **E6b (SELL edge):** E6 with cfo_ttm 10 → `010100000`, **f 2 → SELL** (≤ 2).
- **E6c:** E6b with rev_ttm 1300 → gm_t `0.23076923076923078` (still 0), ato_t `1.368421052631579` > ato_prior →
  `010100001`, **f 3 → HOLD**.
- **E6d (SELL at n = 8):** E6b with gp_ttm None → gm_t None, signal 8 unscored → `0101000-0`, **f 2, n 8 → SELL**.

**E7 n_scored = 7.** E1 with gp_ttm None and cl_t None → cr_t None, gm_t None (gm_prior `0.34782608695652173` and
cr_prior `1.9` still output) → `11111-1-0`, **f_score 6, n_scored 7 → NO_SIGNAL / F_INCOMPLETE**.

**E8 typical TSX name (Yahoo, ~5 quarters).** E1 with market `"TSX"`, ni_ttm_prior None, gp_ttm_prior None,
rev_ttm_prior None, ta_8q None, flags {`PIT_ESTIMATED`} → roa_prior, gm_prior, ato_prior None; signals 3, 8, 9 unscored
→ `11-1111--`, **f 6, n 6 → NO_SIGNAL / F_INCOMPLETE**; flags {`PIT_ESTIMATED`} only: **no** `PRIOR_TA_FALLBACK`,
because neither signal 3 nor 9 is scored, so the substitution changed nothing.

**E9 signal 7 boundary (IEEE verified).** On E1 (signals 1–6, 8, 9 as E1):
| Case | shares_t, shares_4q, split_factor | Test | Signal 7 | Result |
|---|---|---|---|---|
| a | 100.5e6, 100e6, 1.0 (exactly +0.5%) | `20100000000.0 <= 20100000000.0` | **1** | `111111110`, f 8 → BUY |
| b | 100500001.0, 100e6, 1.0 | `20100000200.0 <= 20100000000.0` | 0 | `111111010`, f 7 → HOLD |
| c | 100.5e6, 50e6, 2.0 (2:1 split) | shares_prev_adj `100000000.0` → as a | **1** | BUY |
| d | 101505000.0, 101e6, 1.0 | `20301000000.0 <= 20301000000.0` | **1** | BUY |
| e | 100e6, 0.0 (or None), 1.0 | shares_prev_adj None | unscored | `111111-10`, f 7, n 8 → BUY |
| f | None, 101e6, 1.0 | shares_t None | unscored | as e |
In case a the naive `100.5e6 <= 100e6 * 1.005` would be False (`100499999.99999999`); implementations must use the
cross-multiplied form. split_factor 0 or −1 → `ValueError`.

**E10 ties (equal ratios → 0, strict).** One change each on E1:
| Case | Change | Equal values | Signals | Result |
|---|---|---|---|---|
| a | debt_4q 285 | lev_t `0.3` == lev_prior `0.3` (285/950) | `111101110` | f 7, n 9 → HOLD |
| b | debt_t 0, debt_4q 0 (debt-free both years) | `0.0` == `0.0` | `111101110` | HOLD |
| c | debt_t 0 only | `0.0` < `0.3263157894736842` | `111111110` | BUY |
| d | gp_ttm_prior 402.5 | gm `0.35` == gm_prior `0.35` | `111111100` | f 7 → HOLD |
| e | ni_ttm_prior 120, ta_8q 950 | roa == roa_prior `0.12631578947368421`; ato_prior 1150/950 `1.2105263157894737` | `110111111` | f 8 → BUY |
| f | rev_ttm_prior 1200, ta_8q 950 | ato == ato_prior `1.263157894736842`; roa_prior `0.10526315789473684`, gm_prior `0.3333333333333333` | `111111110` | f 8 → BUY |
| g | cfo_ttm 120 | cfo == ni | `111011110` | f 7 → HOLD |
| h | cfo_ttm 0 | cfo 0 not > 0, and 0 not > 120 | `101011110` | f 6 → HOLD |
| i | ni_ttm 0 | roa `0.0` not > 0 (signal 3: 0.0 < 0.111…) | `010111110` | f 6 → HOLD |

**E11 guards (denominator ≤ 0 → unscored, never 0).** One change each on E1:
| Case | Change | Signals | f / n | Result |
|---|---|---|---|---|
| a | ta_4q 0 | roa, lev_prior, ato_t None (roa_prior `0.1111111111111111`, ato_prior `1.2777777777777777` output) → `-1-1-111-` | 5 / 5 | F_INCOMPLETE |
| b | ta_4q 0, ta_8q None | as a; roa_prior, ato_prior None; **no** `PRIOR_TA_FALLBACK` (3 and 9 unscored) | 5 / 5 | F_INCOMPLETE |
| c | ta_8q 0 (or −5) | **no fallback** (present ta_8q ≤ 0 is not replaced): roa_prior, ato_prior None → `11-11111-`, no flag | 7 / 7 | F_INCOMPLETE |
| d | ta_t 0 | lev_t None → `1111-1110` | 7 / 8 | BUY |
| e | cl_t 0 (or cl_4q −1) | cr_t (resp. cr_prior) None → `11111-110` | 7 / 8 | BUY |
| f | rev_ttm 0 | gm_t None; ato_t `0.0` scored → `1111111-0` | 7 / 8 | BUY |
| g | rev_ttm_prior 0 | gm_prior None; ato_prior `0.0` → signal 9 = 1 → `1111111-1` | 8 / 8 | BUY |
| h | ni_ttm None | signals 1, 3, 4 unscored → `-1--11110` | 5 / 6 | F_INCOMPLETE |
| i | every monetary and share input None | `---------`, f_score **None**, n 0; every ratio None | — / 0 | F_INCOMPLETE |

**E12 precedence.** Base = E1; one change each. Metrics follow the Formulas section in every case.
| Case | Change | Result |
|---|---|---|
| a | eligible False, sector "Financials" | `NOT_ELIGIBLE`, reason None; f 8, n 9, signals `111111110` still output; eligible None likewise |
| b | eligible False, gp_ttm None, cl_t None (n 7) | `NOT_ELIGIBLE` (row 1 before 3); f 6, n 7 |
| c | sector "Financials" | `NO_SIGNAL / EXCLUDED_SECTOR`; f 8, n 9 output |
| d | sector None (or "") | `EXCLUDED_SECTOR` |
| e | sector "Financials", every input as E11i | `EXCLUDED_SECTOR` (row 2 before 3) |
| f | sector "Financials", ta_8q None | `EXCLUDED_SECTOR`, f 9, flag `PRIOR_TA_FALLBACK` still set |
| g | gp_ttm None, cl_t None | `F_INCOMPLETE` (E7) |
| h | sector "Real Estate" / "Utilities" | scored as E1 → BUY |
| i | market "LSE" | `ValueError` |
`run_piotroski([E1, E7, E12a, E6])` returns four results in that order (BUY, NO_SIGNAL, NOT_ELIGIBLE, SELL), each
identical to evaluating it alone.

**E13 map_signal directly.** `(8, 9)` BUY; `(7, 9)` HOLD; `(9, 9)` BUY; `(7, 8)` BUY; `(6, 8)` HOLD; `(8, 8)` BUY;
`(2, 9)` SELL; `(3, 9)` HOLD; `(2, 8)` SELL; `(3, 8)` HOLD; `(0, 9)` SELL; `(5, 7)` and `(9, 10)` → `ValueError`.

## Property expectations
- Every element of `signals` is 1, 0 or None; `n_scored` = count of non-None; `f_score` = their sum (None iff
  n_scored = 0); `0 ≤ f_score ≤ n_scored ≤ 9`.
- BUY ⇒ n_scored ≥ 8 and f_score ≥ 7, and f_score ≥ 8 when n_scored = 9; SELL ⇒ n_scored ≥ 8 and f_score ≤ 2.
  n_scored < 8 ⇒ never BUY/HOLD/SELL.
- Setting any input from a value to None never increases n_scored and never turns an unscored signal into a scored one
  (except `ta_8q`: None triggers the fallback, which is exactly the flagged case). A missing input never yields 0.
- `PRIOR_TA_FALLBACK` ⇒ `ta_8q is None` and signal 3 or 9 scored.
- Scaling every monetary input by 2^k leaves every ratio and signal bit-identical; scaling by any k > 0 leaves signals
  unchanged except possibly at an exact tie; scaling both share counts by 2^k leaves signal 7 unchanged.
- With the other inputs fixed, raising `ni_ttm` never turns signals 1 or 3 from 1 to 0 (signal 4 may go 1 → 0, by
  design); raising `shares_t` never turns signal 7 from 0 to 1.
- A name's result depends only on its own inputs (order, other names and other markets never matter).

## Data mapping and look-ahead (engine, SW)
Everything is filed ≤ as_of. `t` = `period_end` of the balance-sheet filing `build_inputs` selected; `bs_source` its
source; `bs_ccy` its reporting currency. Prior filings come from the **same source** (`_filings(con, ticker,
bs_source, need_shares=False)`, filed ≤ as_of), so debt and asset definitions do not change between periods (EDGAR
D1–D5 vs Yahoo `TotalDebt`, `ttm.md` §5.2); there is no fallback to the other source.

| Input | Source |
|---|---|
| ta_t, debt_t, ca_t, cl_t | the selected filing (same filing for all, `ttm.md` §5.2, §6); absent item → None |
| shares_t | the selected filing's share count × `share_conversion` (filing basis, as Model H's `shares_t`; **not** the price-date `shares_outstanding`) |
| filing t−4q | same source: the filing with the latest `period_end` in [t − 380, t − 350] days, filed ≤ as_of; equal period_end → later filed (`filing_in_window(…, PRIOR_WINDOW_DAYS)`). None if none |
| ta_4q, debt_4q, ca_4q, cl_4q, shares_4q | that one filing (shares × `share_conversion`); an item it lacks is None, never taken from another filing |
| split_factor | `engine.split_factor(con, ticker, period_end_4q, t)`; 1.0 if there is no t−4q filing |
| filing t−8q, ta_8q | same source, latest `period_end` in [t − 745, t − 715] days (window measured from t, not from t−4q), filed ≤ as_of; equal period_end → later filed; its `total_assets` only |
| ni_ttm, cfo_ttm, gp_ttm, rev_ttm | `flow_ttm(con, ticker, item, as_of)` for `ni_common_q`, `cfo_q`, `gross_profit_q`, `revenue_q` (`ttm.md` §3, §5.3). Used only if the TTM's `period_end == t`; otherwise that item is None and flag `E_PERIOD_MISMATCH` is set (a TTM that is None altogether gives None, no flag). TTM currency ≠ `bs_ccy` → None + `FLOW_CCY_MISMATCH` |
| ni_ttm_prior, gp_ttm_prior, rev_ttm_prior | computed only when the item's current TTM passed the checks above. From the **same source and item as that current TTM**: `q_prev` = the latest quarter `period_end` of those rows (filed ≤ as_of) in [t − 380, t − 350]; prior = `ttm.ttm(rows with period_end ≤ q_prev, as_of)`; used only if its value is not None and its `period_end` (= q_prev) is in that window, else None. Currency of the `q_prev` row ≠ `bs_ccy` → None + `FLOW_CCY_MISMATCH`. Restatements filed ≤ as_of are used (latest filed per quarter, `ttm.md` §3) |
| prior filing currency | a t−4q or t−8q filing whose `_currency` ≠ `bs_ccy` → its monetary items None (shares kept), flag `PRIOR_CCY_MISMATCH` |
| eligible, market, sector | as Model A (E1–E5); `PIT_ESTIMATED` as Model A (Yahoo filing, estimated filed dates) |

The current flows need not come from `bs_source` (flow_ttm picks per item; requiring `period_end == t` keeps them
aligned with the balance sheet). No `cfo_ttm_prior` is needed (no signal compares CFO over time).

**TSX limitation (known, 0.1.0).** Yahoo's free quarterly series holds ~5 quarters: the current TTM and the t−4q
balance sheet exist (the latter only for the latest quarter), but the prior TTM needs quarters t−4q … t−7q (8 in all)
and t−8q needs a balance sheet two years back, so `ni_ttm_prior`, `gp_ttm_prior`, `rev_ttm_prior` and `ta_8q` are
usually None. Signals 3, 8, 9 are then unscored, n_scored ≤ 6, and the typical TSX name is **NO_SIGNAL /
F_INCOMPLETE** (golden E8). The proposal's `ANNUAL_PRIOR` route (prior TTM from fiscal-year totals) is **not
implemented** in 0.1.0 because no annual flow totals are stored (only discrete quarters); the flag name is reserved
and is never set. Backlog (SW): store annual flow totals (Yahoo `annual*` types; EDGAR FY durations) and specify
`ANNUAL_PRIOR` in a later version, or wait for the provider decision (proposal §13, "TSX fundamental history").

Other known gaps: `GrossProfit` tag variety (derived `REV − COST` helps, `ttm.md` §5.1); unclassified balance sheets have
no CA/CL (signal 6 unscored); multi-class EDGAR filers without a cover-page share count have signal 7 unscored; an
acquisition between t−4q and t makes every Δ signal compare unlike asset bases (no detection in 0.1.0).

## Report and backtest
- Report: `screen_signals` adds `e_signal, e_no_signal_reason, e_f_score, e_n_scored, e_signals` after the `g_*` and
  `d_*` columns and before the `f_*` columns and `flags` (order D, E, F). `e_signals` is a 9-character string of `1`,
  `0`, `-` (unscored) in signal order 1..9 (e.g. `111111110`); `e_f_score` is empty when None. E flags
  (`PRIOR_TA_FALLBACK`, `E_PERIOD_MISMATCH`, `PRIOR_CCY_MISMATCH`, `FLOW_CCY_MISMATCH`) join `flags`. The disclaimer
  lists `piotroski/0.1.0` and adds "Model E is new and unvalidated (not backtested)." E has no vote.
- Backtest: **deferred** (user decision): the D/E/F backtests are to be run locally by the user later. E is in no
  backtest run, in-sample or holdout. The proposal's hypothesis is recorded for a later pre-registration only: in
  this large-cap, liquid universe the original 23%/yr high-minus-low spread will not appear; expect 2–5%/yr for F ≥ 8
  minus F ≤ 2 with a small positive IC (0.02–0.04); specific check: the spread is larger within the cheapest half of
  each sector (by B/P) than in the dearest half, as in the paper.
- Per-company fit ([`per_company_fit.md`](per_company_fit.md) v0.1.0, user decision 2026-11-13): E's label is now stored in the **in-sample** backtest (`bt_rows.e_signal`, labelled unvalidated) and evaluated per company (SELL_EXIT and BUY_ONLY); never on holdout dates. Label → position mapping in that spec §3.3. No parameter changes.
- External checks (QA, `docs/qa/models_def_validation.md`): for 2–3 US names, each input (NI, CFO, GP, revenue TTM and
  prior TTM; TA at t, t−4q, t−8q; debt, CA, CL, shares at t and t−4q) vs the 10-K/10-Q, exact; ROA, gross margin and
  current ratio vs the issuer's ratios (±1% relative); the nine signals recomputed independently. Secondary: Yahoo
  `returnOnAssets`, `grossMargins`, `currentRatio` (±10%; different definitions). There is no free published F-score
  with a point-in-time basis to compare to.

## Risks
Binary signals lose magnitude (a 0.01pp margin gain scores like a 5pp one); acquisitions break every Δ signal; signal 7
penalises stock-funded M&A; a debt-free firm can never score signal 5 (strict `<`, stated); designed for value stocks,
so a high F among expensive growth names says little about price; TSX names are mostly F_INCOMPLETE until annual
totals or a provider exist; EDGAR Q4 values are derived as FY − 9M (`ttm.md` §1). *Overfitting risk:* **low**: every
constant is Piotroski's or from the proposal, written before any backtest, and E has never been run on returns.
Residual risk: E was shortlisted after A and B had failed, though not on E's own results.

## Changelog
- **0.1.0:** initial spec from proposal §2. Proposal goldens reproduced (F 8 BUY with signal 9 = 0; TA_{t−8q} fallback
  F 9 BUY with `PRIOR_TA_FALLBACK`, ROA_prior 0.10526315789473684, turnover prior 1.2105263157894737). Added: unscored
  (None) signals on missing inputs or denominators ≤ 0, `F_INCOMPLETE` below 8 scored, fallback only for a missing (not
  non-positive) TA_{t−8q} and flagged only when it entered a scored signal, signal 7 in exact cross-multiplied form
  (201/200), same-source prior filings and prior TTMs, `E_PERIOD_MISMATCH`, `PRIOR_CCY_MISMATCH`, `ANNUAL_PRIOR`
  reserved (not implemented; TSX limitation); n_scored 8/9 thresholds, SELL, tie, guard, precedence and boundary
  goldens; arithmetic verified with a throwaway script.
