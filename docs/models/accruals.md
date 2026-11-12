# Model spec: Accruals / earnings quality (`accruals`, v0.1.0) — Model F

Owner: AN · Tests: QA (`tests/unit/test_accruals_spec.py`, `tests/unit/test_accruals_engine_spec.py`) · Code:
`src/valuation/models/accruals.py` · Source: proposal `docs/models/proposals/additional_models.md` §3.
**Not part of FROZEN_V2**: F is not in `revision_v2_freeze.md` and adding it must not change that freeze's hash.
**No new data**: NI to common TTM exists; CFO TTM and total assets are Phase 9 items (`ttm.md` §5). The prior-year
total assets come from an older balance-sheet filing of the same source, already stored.

## Purpose
*How much of this company's earnings is backed by operating cash?* Earnings = cash part + accrual part, and the accrual
part is less persistent. Firms whose earnings run far ahead of operating cash flow tend to see earnings fade and the
price follow; firms whose cash flow exceeds earnings tend to do the reverse. Evidence: Sloan (1996); Hribar & Collins
(2002) show the cash-flow-statement definition (`NI − CFO`) is more reliable than balance-sheet changes. The anomaly
has weakened since publication, which is why the SELL side carries the weight. Accruals are ranked within the GICS
sector because depreciation makes accruals structurally negative in asset-heavy sectors.

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote. F has no vote.

## Constants
`MODEL_VERSION = "accruals/0.1.0"`, `BUY_PCT = 0.20`, `SELL_PCT = 0.80`, `BUY_RATIO = -0.05`, `SELL_RATIO = 0.05`,
`MIN_SECTOR = 12`, `EXCLUDED_SECTORS = frozenset({"Financials"})`, `PREV_WINDOW_DAYS = (350, 380)` (engine only). All
from proposal §3 (the window as Model H). They are starting values, **never tuned on the holdout** (PM decision
2026-11-09), and F has never been backtested, so none was chosen after a result.

## Inputs (`FInputs`, frozen dataclass, one ticker at `as_of`)
| Field | Meaning | Default |
|---|---|---|
| `ticker`, `market` (`"TSX"`/`"US"`, else `ValueError`) | as Model A | |
| `sector` | GICS sector, `str \| None`; exact string match (no case folding); drives exclusion and the peer group | |
| `eligible` | eligibility E1–E5 (`bool \| None`); anything but `True` → `NOT_ELIGIBLE` | |
| `ni_ttm` | NI to common TTM, reporting ccy; None on NO_TTM or FLOW_CCY_MISMATCH | None |
| `cfo_ttm` | CFO TTM, reporting ccy; None on NO_TTM or FLOW_CCY_MISMATCH | None |
| `total_assets_t` | total assets of the balance-sheet filing `build_inputs` selected (period end `t`) | None |
| `total_assets_prev` | total assets ≈ 4 quarters earlier, same source (Data mapping) | None |
| `period_end_t` | `datetime.date \| None`: period_end of the selected balance-sheet filing | None |
| `ni_period_end`, `cfo_period_end` | `datetime.date \| None`: `TTMResult.period_end` of each flow | None |
| `flags` | passed through (`PIT_ESTIMATED`, `FLOW_CCY_MISMATCH`, `NO_TTM`, `STALE_TTM` with Model A's meaning, i.e. NI TTM, as J; engine flags `F_PRIOR_CCY_MISMATCH`) | frozenset() |

No monetary input is ever defaulted to 0. **No FX input**: every ratio is reporting ccy over reporting ccy (the flows
are forced to the balance-sheet currency by `FLOW_CCY_MISMATCH`, the prior total assets by `F_PRIOR_CCY_MISMATCH`), so
the trading currency never enters.

`FMetrics(accruals, avg_total_assets, accrual_ratio, cash_conv, period_ok)`.
`FResult`: `ticker, market, sector, signal, no_signal_reason, accruals, avg_total_assets, accrual_ratio, cash_conv,
acc_pct, n, flags`. API:
- `compute_metrics(inp: FInputs) -> FMetrics` (validates `market`);
- `map_signal(pct: float, ratio: float, ni: float) -> "BUY" | "HOLD" | "SELL"` on given floats;
- `acc_percentiles(values: Mapping[str, float]) -> dict[str, float | None]` (the percentile rule below, one group);
- `run_accruals(inputs: Sequence[FInputs], min_sector: int = MIN_SECTOR) -> list[FResult]` (one result per input,
  same order; `min_sector < 2` → `ValueError`; any market not `"TSX"`/`"US"` → `ValueError`).
Results are keyed internally by input position, not by ticker (the same ticker in two markets is two names).

## Formulas (per name, reporting ccy)
```
period_ok        = period_end_t is not None and ni_period_end == period_end_t and cfo_period_end == period_end_t
accruals         = ni_ttm − cfo_ttm                      only if ni_ttm, cfo_ttm not None and period_ok, else None
avg_total_assets = (total_assets_t + total_assets_prev) / 2      if both not None, else None
accrual_ratio    = accruals / avg_total_assets           None if accruals None, avg None, or avg ≤ 0
cash_conv        = cfo_ttm / ni_ttm                      informational; None if accruals None or ni_ttm ≤ 0
```
**Evaluation order** (so float goldens reproduce bit for bit): `avg = (ta_t + ta_prev) / 2` (sum first, then divide by
the int 2); `accruals = ni - cfo`; `ratio = accruals / avg` (one division); `cash_conv = cfo / ni`.

Metrics are computed whenever their inputs exist, also for NOT_ELIGIBLE and excluded names (as J and G). Two flows are
combined only on equal `period_end` (`ttm.md` §5.3), and both must also equal the balance-sheet `t`, so NI, CFO and
`TA_t` describe the same quarter end; otherwise accruals, ratio and cash_conv are None (not computed on a stale pair).
`avg_total_assets` is still output when the period check fails (it needs only the two balance sheets).

## Cross-section (per market and sector, not pooled)
**Deviation from the proposal, stated:** the proposal says "GICS sector (pooled)" across TSX and US. F ranks within
(market, sector) instead, consistent with Model C 0.2.0 (`composite.md` R2) and Model J: the two markets differ in
accounting regime (IFRS on most TSX filers vs US GAAP: IFRS 16 moves all lease principal out of CFO, ASC 842 keeps
operating-lease cost in CFO, so TSX accrual ratios are shifted down by construction), in reporting currency and in rf;
pooled quintiles would partly sort the market and the accounting standard, not the stock. Cost: fewer names per TSX
sector, so more `SMALL_SECTOR` there. Everything below runs separately for each group g = (market, sector); groups
never influence each other.

1. **Ranking set** `R_g` = inputs with `market == m`, `sector == s`, `eligible is True`, sector non-empty and not in
   `EXCLUDED_SECTORS`, and `accrual_ratio is not None` (which implies all four monetary inputs present, `period_ok`,
   and `avg_total_assets > 0`). **`ni_ttm ≤ 0` names are in `R_g`**: they are part of the sector distribution; the
   NI rule only blocks their BUY. `n = |R_g|`.
2. **Percentile** within `R_g`: `rank` = average rank of `accrual_ratio`, ascending (1 = most negative; ties share the
   mean of their ranks: `scipy.stats.rankdata(x, method="average")`), and
   ```
   acc_pct = float((rank − 1) / (n − 1))     n ≥ 2;   None for every name of the group when n < 2
   ```
   0 = most negative accrual ratio, 1 = most positive. Same rule as Model H's `sy_pct`.
3. `acc_pct` and `n` are output for every name in `R_g`, also when `n < min_sector` (informational); None for names
   outside every `R_g`, which never affect any other name.

*Why this percentile.* `(rank − 1)/(n − 1)` is symmetric: reversing the order maps pct p to 1 − p, so the BUY band
(≤ 0.20) and SELL band (≥ 0.80) hold the same number of names for every n (n = 12: ranks 1–3 and 10–12; n = 16: ranks
1–4 and 13–16). `rank/n` (as J) is not symmetric (1/n … 1). Average ranks make it deterministic without a ticker
tie-break, and identical ratios always get the identical percentile and therefore the identical signal (unlike J's
tie-break, which F does not need because it has one metric). Average ranks of ties are half-integers, exactly
representable, so the division is a single correctly-rounded IEEE operation: whenever `(rank − 1)/(n − 1)` is
exactly 0.2 or 0.8 in real numbers (e.g. 2/10, 8/10, 3/15, 12/15) the double equals the literal `0.2` / `0.8`
(verified), so the inclusive boundaries behave as written.

## Signal and precedence (first match wins)
| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` (reason None) | `eligible is not True` |
| 2 | `NO_SIGNAL` / `EXCLUDED_SECTOR` | sector None or `""`, or in `EXCLUDED_SECTORS` (Financials). Real Estate is **not** excluded: it is scored within its own (market, "Real Estate") group |
| 3 | `NO_SIGNAL` / `NO_DATA` | any of `ni_ttm`, `cfo_ttm`, `total_assets_t`, `total_assets_prev` is None, or not `period_ok` |
| 4 | `NO_SIGNAL` / `NON_POSITIVE_ASSETS` | `avg_total_assets ≤ 0` |
| 5 | `NO_SIGNAL` / `SMALL_SECTOR` | `n < min_sector` for the name's (market, sector) group (12 is enough, 11 is not) |
| 6 | `map_signal(acc_pct, accrual_ratio, ni_ttm)` | below |

```
map_signal(pct, ratio, ni): "SELL" if pct ≥ SELL_PCT and ratio ≥ SELL_RATIO
                            "BUY"  if pct ≤ BUY_PCT  and ratio ≤ BUY_RATIO and ni > 0
                            "HOLD" otherwise
```
The SELL and BUY conditions are mutually exclusive (pct cannot be both ≤ 0.20 and ≥ 0.80), so the order is cosmetic.
Plain float comparisons, no epsilon. A loss-maker (`ni ≤ 0`, incl. exactly 0) can be SELL, never BUY.

**Flags** (model-level):
- `F_PERIOD_MISMATCH`: `ni_ttm` and `cfo_ttm` both not None and not `period_ok` (whatever the signal; it explains a
  `NO_DATA`).
- `F_NO_PRIOR_TA`: `total_assets_t` not None and `total_assets_prev` None (whatever the signal; the usual TSX case away
  from the latest quarter).
- `F_LOSS_BUY_BLOCKED`: set only when row 6 runs, `acc_pct ≤ 0.20`, `accrual_ratio ≤ −0.05` and `ni_ttm ≤ 0` (so HOLD).
- plus the pass-through flags.

## Worked examples (golden values; relative tolerance 1e-9)
Unless stated: market `"US"`, sector `"Industrials"`, eligible True, `period_end_t = ni_period_end = cfo_period_end =
date(2026, 9, 30)`, `$M`. Full float reprs are given (computed with the evaluation order above).

**G1 proposal cases (`compute_metrics` + `map_signal`, pct given).** TA_t 4000, TA_prev 3600, NI 500.
| Case | CFO | accruals | avg_total_assets | accrual_ratio | cash_conv | pct (given) | `map_signal` |
|---|---|---|---|---|---|---|---|
| 1 | 320 | 180 | 3800.0 | `0.04736842105263158` | 0.64 | 0.90 | **HOLD** (below the +0.05 floor) |
| 2 | 280 | 220 | 3800.0 | `0.05789473684210526` | 0.56 | 0.90 | **SELL** |
| 3 | 760 | −260 | 3800.0 | `-0.06842105263157895` | 1.52 | 0.10 | **BUY** |

**G2 full cross-section X1** (`run_accruals`, default `min_sector=12`). Twelve US Industrials names, all TA_t 1100,
TA_prev 900 → avg 1000.0; ratio = (NI − CFO)/1000. n = 12, so pct = (rank − 1)/11.
| Name | NI | CFO | accruals | accrual_ratio | cash_conv | rank | acc_pct | Signal |
|---|---|---|---|---|---|---|---|---|
| A01 | 100 | 220 | −120 | −0.12 | 2.2 | 1 | 0.0 | **BUY** |
| A02 | −20 | 60 | −80 | −0.08 | None | 2 | `0.09090909090909091` | HOLD + `F_LOSS_BUY_BLOCKED` |
| A03 | 100 | 130 | −30 | −0.03 | 1.3 | 3 | `0.18181818181818182` | HOLD (ratio above −0.05 floor) |
| A04 | 100 | 120 | −20 | −0.02 | 1.2 | 4 | `0.2727272727272727` | HOLD |
| A05 | 100 | 110 | −10 | −0.01 | 1.1 | 5 | `0.36363636363636365` | HOLD |
| A06 | 100 | 100 | 0 | 0.0 | 1.0 | 6 | `0.45454545454545453` | HOLD |
| A07 | 100 | 90 | 10 | 0.01 | 0.9 | 7 | `0.5454545454545454` | HOLD |
| A08 | 100 | 80 | 20 | 0.02 | 0.8 | 8 | `0.6363636363636364` | HOLD |
| A09 | 100 | 70 | 30 | 0.03 | 0.7 | 9 | `0.7272727272727273` | HOLD |
| A10 | 100 | 60 | 40 | 0.04 | 0.6 | 10 | `0.8181818181818182` | HOLD (top quintile, ratio below +0.05 floor) |
| A11 | 100 | 40 | 60 | 0.06 | 0.4 | 11 | `0.9090909090909091` | **SELL** |
| A12 | 50 | −50 | 100 | 0.1 | −1.0 | 12 | 1.0 | **SELL** |
Every name: n = 12. All ratios print as the short decimals shown (e.g. `(100 - 220) / 1000.0 == -0.12`).

**G3 ties.**
- **T1:** X1 with A03 CFO 160 (ratio −0.06, cash_conv 1.6): A03 rank 3, pct `0.18181818181818182` → **BUY**; others as X1.
- **T2:** T1 with A04 CFO 160 as well: A03 and A04 tie at −0.06, both rank 3.5, pct `0.22727272727272727` → both
  **HOLD** (outside the band). All other names keep their X1 rank, pct and signal. Identical ratios get identical
  signals; a tie straddling the band edge moves both names out.

**G4 small sector.**
- **S1:** X1 without A12 (n = 11): all eleven `NO_SIGNAL / SMALL_SECTOR`, n = 11; acc_pct still output, = (rank − 1)/10:
  A01 0.0, A02 0.1, A03 0.2, A04 0.3, A05 0.4, A06 0.5, A07 0.6, A08 0.7, A09 0.8, A10 0.9, A11 1.0 (all exactly
  these doubles). No `F_LOSS_BUY_BLOCKED` on A02 (row 6 not reached).
- **S1b:** S1 with `min_sector=11`: A01 **BUY**, A02 HOLD + `F_LOSS_BUY_BLOCKED`, A03 HOLD (pct exactly 0.2 but ratio
  −0.03 > −0.05), A04–A09 HOLD (A09 pct exactly 0.8, ratio 0.03 < 0.05), A10 HOLD (pct 0.9, ratio 0.04), A11 **SELL**.
- **S2:** X1 with A12 eligible False: A12 `NOT_ELIGIBLE` (metrics still computed: ratio 0.1, cash_conv −1.0; acc_pct
  and n None); the other eleven → `SMALL_SECTOR`, n = 11, pcts as S1.
- `run_accruals(X1, min_sector=1)` → `ValueError`.

**G5 percentile boundaries (B, n = 16).** Sixteen US "Consumer Discretionary" names B01..B16, NI 100, TA_t 1100,
TA_prev 900, CFO = 100 − 1000·ratio with ratios (ascending) −0.20, −0.15, −0.10, −0.08, −0.06, −0.04, −0.02, 0.0,
0.01, 0.02, 0.03, 0.06, 0.07, 0.09, 0.12, 0.20 (CFO 300, 250, 200, 180, 160, 140, 120, 100, 90, 80, 70, 40, 30, 10,
−20, −100). pct = (rank − 1)/15.
| Name | ratio | acc_pct | Signal |
|---|---|---|---|
| B01–B03 | −0.2, −0.15, −0.1 | 0.0, `0.06666666666666667`, `0.13333333333333333` | **BUY** |
| B04 | −0.08 | **0.2** (3/15 == 0.2 in IEEE, verified) | **BUY** (≤ inclusive) |
| B05 | −0.06 | `0.26666666666666666` | HOLD (ratio passes, pct does not) |
| B06–B11 | −0.04 … 0.03 | `0.3333333333333333`, 0.4, `0.4666666666666667`, `0.5333333333333333`, 0.6, `0.6666666666666666` | HOLD |
| B12 | 0.06 | `0.7333333333333333` | HOLD (ratio passes, pct does not) |
| B13 | 0.07 | **0.8** (12/15 == 0.8, verified) | **SELL** (≥ inclusive) |
| B14–B16 | 0.09, 0.12, 0.2 | `0.8666666666666667`, `0.9333333333333333`, 1.0 | **SELL** |
cash_conv: B01 3.0 … B13 0.3, B15 −0.2, B16 −1.0 (CFO/100).

**`map_signal` boundaries (given floats).**
| Call | Result |
|---|---|
| `map_signal(0.2, -0.05, 1.0)` | BUY (both inclusive) |
| `map_signal(0.2, -0.0499999, 1.0)` | HOLD |
| `map_signal(0.2000001, -0.5, 1.0)` | HOLD |
| `map_signal(0.1, -0.1, 0.0)` / `(0.1, -0.1, -5.0)` | HOLD (NI must be > 0) |
| `map_signal(0.8, 0.05, 1.0)` | SELL (both inclusive) |
| `map_signal(0.8, 0.0499999, 1.0)` | HOLD |
| `map_signal(0.7999999, 0.5, 1.0)` | HOLD |
| `map_signal(0.9, 0.1, -5.0)` | SELL (a loss-maker can be SELL) |
| `map_signal(0.5, 0.0, 1.0)` | HOLD |
A data-derived ratio can sit exactly on the floor: NI 150, CFO 100, avg 1000 → `50 / 1000.0 == 0.05`; NI 100, CFO
150 → `-0.05` (both verified exact).

**G6 market separation.** X1 (US Industrials) plus C01.TO..C12.TO, market `"TSX"`, sector "Industrials", all NI 100,
CFO 300, TA 1100/900 (ratio −0.2 each): every US result identical to X1; TSX n = 12, all tied at rank 6.5, pct 0.5 →
all **HOLD** (pooled ranking would have pushed every US name up). With only C01.TO..C05.TO: TSX all `SMALL_SECTOR`
(n = 5, pct 0.5 each), US identical to X1. Adding, changing or removing TSX names never changes a US result.

**G7 sector separation.** X1 plus:
- R01..R12 = copies of A01..A12 with sector "Real Estate": each R name gets exactly its A twin's ratio, pct, n and
  signal (Real Estate is scored, in its own group); X1 unchanged.
- F01..F12 = copies with sector "Financials": all `EXCLUDED_SECTOR`, metrics computed, acc_pct and n None; X1 unchanged.
- E01..E03, sector "Energy", NI 100, CFO 300: `SMALL_SECTOR`, n = 3, pcts 0.5, 0.5, 0.5 (all tied); X1 unchanged.

**G8 precedence.** One extra name P added to X1; P = A01's inputs with one change each. P is never in the Industrials
`R_g` unless stated, so X1's results stay as G2 in every row. Metrics follow the Formulas section.
| Case | Change | Result |
|---|---|---|
| a | eligible False, sector "Financials" | `NOT_ELIGIBLE` (accruals −120, avg 1000.0, ratio −0.12, cash_conv 2.2 still computed; acc_pct, n None); eligible None likewise |
| b | sector "Financials" | `NO_SIGNAL / EXCLUDED_SECTOR`, metrics as a |
| c | sector None (or "") | `EXCLUDED_SECTOR` |
| d | total_assets_prev None, sector "Financials" | `EXCLUDED_SECTOR` (row 2 before 3); flag `F_NO_PRIOR_TA` |
| e | total_assets_prev None | `NO_DATA` + `F_NO_PRIOR_TA`; accruals −120, cash_conv 2.2, avg and ratio None |
| f | cfo_period_end date(2026, 6, 30) | `NO_DATA` + `F_PERIOD_MISMATCH`; accruals, ratio, cash_conv None; avg 1000.0 |
| g | ni_period_end and cfo_period_end both date(2026, 6, 30) | `NO_DATA` + `F_PERIOD_MISMATCH` (equal to each other, not to t) |
| h | ni_ttm None | `NO_DATA`, no `F_PERIOD_MISMATCH`; accruals, ratio, cash_conv None |
| i | total_assets_t 100, total_assets_prev −300 | `NON_POSITIVE_ASSETS`; avg −100.0, ratio None, accruals −120 |
| j | total_assets_t 0, total_assets_prev 0 | `NON_POSITIVE_ASSETS`; avg 0.0 |
| k | sector "Industrials", inputs exactly A01's (a valid 13th name) | now in `R_g`: n = 13 for every Industrials name; A01 and P tie at rank 1.5, pct `0.041666666666666664` → both BUY; the others shift to pct (rank − 1)/12: A02 `0.16666666666666666` (HOLD + `F_LOSS_BUY_BLOCKED`), A03 0.25, A04 `0.3333333333333333`, A05 `0.4166666666666667`, A06 0.5, A07 `0.5833333333333334`, A08 `0.6666666666666666`, A09 0.75, A10 `0.8333333333333334` (HOLD), A11 `0.9166666666666666` SELL, A12 1.0 SELL (this row alone changes X1, by design) |
| l | market "LSE" | `ValueError` |

## Property expectations
- Per (market, sector) independence: a name's result depends only on its own group's inputs and `min_sector`; input
  order never matters (except that results come back in input order).
- `acc_pct ∈ [0, 1]`, with 0 and 1 attained when the group has distinct extremes; reversing every ratio's sign maps
  pct p → 1 − p (ties included); for n ≥ min_sector the BUY-eligible and SELL-eligible bands hold the same number
  of names.
- Scaling every monetary input of a name (NI, CFO, both TAs) by k > 0 leaves accrual_ratio, cash_conv, ranks, pcts
  and signals unchanged.
- With the others fixed and the name staying in `R_g`, lowering its CFO (raising its accruals) never lowers its pct.
- BUY ⇒ `acc_pct ≤ 0.20`, `accrual_ratio ≤ −0.05`, `ni_ttm > 0`; SELL ⇒ `acc_pct ≥ 0.80`, `accrual_ratio ≥ 0.05`.
- `n < min_sector` → no BUY/HOLD/SELL in that group. A name outside every `R_g` never affects any other result.
- A name with `F_LOSS_BUY_BLOCKED` is HOLD; a name with `F_PERIOD_MISMATCH` is `NO_DATA` unless rows 1–2 catch it.

## Data mapping and look-ahead (engine, SW)
| Input | Source |
|---|---|
| total_assets_t, period_end_t | the balance-sheet filing `build_inputs` selected (`t_bs`, source `bs_source`; `filed_date ≤ as_of`; TSX Yahoo → `PIT_ESTIMATED`) |
| total_assets_prev | **same source as `bs_source`** (EDGAR or Yahoo, never mixed): the filing with the latest `period_end` in [`t − 380`, `t − 350`] days, `filed_date ≤ as_of`, `total_assets` present; equal period_end → later filed (as Model H's `shares_prev`). None if none (**no spot fallback**: a spot TA would change the denominator's definition for some names and not others; the name is `NO_DATA` + `F_NO_PRIOR_TA` instead). If that filing's currency ≠ the selected filing's currency → None + flag `F_PRIOR_CCY_MISMATCH` |
| ni_ttm, ni_period_end | `flow_ttm(ni_common_q)` → (`TTMResult.value`, `.period_end`); currency ≠ balance-sheet currency → value None + `FLOW_CCY_MISMATCH` (as J/G) |
| cfo_ttm, cfo_period_end | `flow_ttm(cfo_q)` likewise (EDGAR `NetCashProvidedByUsedInOperatingActivities` chain, Yahoo `quarterlyOperatingCashFlow`) |
| eligible, market, sector | as Model A (E1–E5) |
- Point in time: every row used has `filed_date ≤ as_of` (flow_ttm and the prior-filing window enforce it); TSX Yahoo
  dates are estimates (+45/+90 days, `ttm.md` §2) and carry `PIT_ESTIMATED` through to `flags`.
- The flows are not forced to the balance-sheet source (as J and G); the `period_ok` rule (both flows' period_end ==
  `t`) keeps NI, CFO and TA on the same quarter end. A balance sheet fresher than the flows (e.g. a 10-Q whose cash-flow
  YTD did not parse) is `NO_DATA` + `F_PERIOD_MISMATCH`, never scored on last quarter's flows.
- **TSX history:** Yahoo quarterly history is ~5 quarters, so `TA_{t−4q}` exists only for the latest quarter (live run
  fine; historical TSX dates mostly `NO_DATA` + `F_NO_PRIOR_TA`).
- **No annual fallback:** only discrete quarters are stored, so a TTM or prior TA from annual totals is not
  implementable in 0.1.0 (backlog, not invented here).
- Known gaps: NI is NI to common (NCI share in neither NI nor CFO consistently; small for most names); acquisitions
  (purchase accounting) distort both legs; an EDGAR Q4 CFO is FY − 9M (`ttm.md` §1); TA definitions match across
  sources closely but EDGAR and Yahoo values can differ slightly, which is why `TA_prev` is same-source.

## Report and backtest
- Report: `screen_signals` adds `f_signal, f_no_signal_reason, f_accrual_ratio, f_acc_pct, f_cash_conv, f_n` after the
  `g_*` columns and the D and E columns (order D, E, F) and before `flags`; F flags join `flags`. The disclaimer lists
  `accruals/0.1.0` and adds "Model F is new and unvalidated (not backtested)." F has no vote.
- Backtest: **deferred** (user decision): to be run locally by the user later. F is in no backtest run, in-sample or
  holdout. The proposal's hypothesis is recorded for a later pre-registration only: the sector-neutral SELL bucket
  underperforms its sector median by 2–4%/yr; the BUY bucket's edge is small or nil; IC 0.02–0.03 (weak); pass bar
  `backtest.md` §4.8 (proposal §6), with the model allowed to pass on the SELL side alone (hit rate of SELLs lagging the
  sector median > 55% in ≥ 60% of years), in which case it would enter a vote as SELL-only. A backtest must note the
  IFRS 16 (2019) / ASC 842 CFO break: within one date and one market all names share the regime, but time series
  across 2019 are not comparable.
- External checks (QA, `docs/qa/models_def_validation.md`): for 2–3 US names, NI TTM and CFO TTM vs the sum of the
  last four quarters of the 10-K/10-Q cash-flow statements (exact), TA_t and TA_{t−4q} vs the two balance sheets
  (exact), `NI − CFO` and the ratio recomputed to 1e-9; CFO TTM vs Yahoo `operatingCashflow` (±5%) as a sanity bound.

## Risks
Large non-cash impairments make accruals very negative (BUY only if NI > 0, so mostly blocked); working-capital builds
for growth look like "bad" accruals; acquisitions distort NI and CFO; the IFRS 16 CFO lift makes TSX ratios lower than
US ones (handled by per-market groups, not corrected); small TSX sectors fall to `SMALL_SECTOR`; the anomaly has
decayed since 1996. Overlap: Model C uses `−accruals/TA` as one quality metric; F isolates it with a SELL rule.
*Overfitting risk:* **low**: every constant comes from the proposal, written before any backtest; the only choices
here (per-market groups, percentile formula, prior-TA window) follow existing models (C 0.2.0, J, H) and were made
without looking at returns. F has never been run on returns.

## Changelog
- **0.1.0:** initial spec from proposal §3. Proposal goldens reproduced (ratios 0.04736842105263158 HOLD,
  0.05789473684210526 SELL, −0.06842105263157895 BUY); per-(market, sector) cross-section (deviation from "pooled",
  reasoned), `(rank − 1)/(n − 1)` average-rank percentile, `period_ok` on NI/CFO/TA quarter ends, same-source prior TA
  with no spot fallback, flags `F_PERIOD_MISMATCH`, `F_NO_PRIOR_TA`, `F_LOSS_BUY_BLOCKED`, `F_PRIOR_CCY_MISMATCH`;
  full cross-section, tie, small-sector, boundary, market/sector separation and precedence goldens added; arithmetic
  and IEEE boundaries verified with a throwaway script.
