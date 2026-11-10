# Model spec: Value–Quality–Momentum–Risk composite (`composite`, v0.2.0) — Model C

Owner: AN · Tests: QA (`tests/unit/test_composite_spec.py`) · Code: `src/valuation/models/composite.py` ·
Source: proposal `docs/models/proposals/buy_hold_sell.md` §0, §1 (E6), §4, §6, §8 (task P9-2). Data: `ttm.md` v0.2.0
(Phase 9 data batch 2: `cfo_q`, `capex_q`, `gross_profit_q`, balance-sheet `total_assets`, `total_debt`).
v0.2.0: revision round v2 (§Revision v0.2.0; frozen in `revision_v2_freeze.md`): B/P only in book sectors,
cross-section per market, E6 = 274 sessions.

## Purpose
*Where does this stock rank, inside its own GICS sector, on four separately checkable traits?* Cheap (Value),
profitable and conservatively financed (Quality), not in a persistent downtrend (Momentum, the value-trap guard) and
not extremely volatile (Risk). Model C gives no fair value, only a rank. It covers **every** sector, including the
asset-light ones where Models A and B are weak. All parameters below come from proposal §4; equal weights are not
fitted, and nothing here was chosen after looking at backtest results.

> **Screening signal only.** BUY / HOLD / SELL sorts the queue for human review. It is not investment advice, not a
> price forecast and not a trade instruction. Labels are exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote. Same disclaimer and report as Model A
> (`residual_income.md` §Report v2).

## Constants
`MODEL_VERSION = "composite/0.2.0"`, `WINSOR = (0.05, 0.95)`, `MIN_SECTOR_NAMES = 5`, `MIN_FACTORS = 3`,
`BUY_PCT = 0.80`, `SELL_PCT = 0.20`, `MOM_VETO_PCT = 0.10`, `MOM_SKIP = 21`, `MOM_LOOKBACK = 273`, `VOL_WINDOW = 252`,
`ANNUALIZE = 252`, `STD_EPS = 1e-12`, `FINANCIALS = "Financials"`, (v0.2.0) `BOOK_SECTORS = frozenset({"Financials",
"Utilities", "Real Estate", "Energy", "Materials"})` (the same set as Models A and B), `MARKETS = ("TSX", "US")`.
```
FACTORS = {"value":    ("ep", "bp", "fcfp"),
           "quality":  ("roe", "gpa", "neg_accruals", "neg_leverage"),
           "momentum": ("mom",),
           "risk":     ("neg_vol",)}
METRICS = the 9 metric names in the order above;  NON_FIN_METRICS = ("fcfp", "gpa", "neg_accruals", "neg_leverage")
```
New parameters beyond the proposal: only `MIN_SECTOR_NAMES` (see Decisions D9). `STD_EPS` is a floating-point
zero, as in Model B.

## Inputs (`CompositeInputs`, one per ticker, all at the same `as_of`)
| Field | Meaning | Unit / default |
|---|---|---|
| `ticker`, `sector` | Listed symbol; GICS sector, exact name (`None`/`""` → `NO_SECTOR`) | str, str \| None |
| `market` | v0.2.0: `"TSX"` or `"US"`, else `ValueError` | `"US"` |
| `eligible` | Result of `eligibility` (E1–E5); anything but `True` → `NOT_ELIGIBLE` | bool \| None |
| `history_ok` | `eligibility.model_c_history_ok` (E6); anything but `True` → `NO_SIGNAL / SHORT_HISTORY` | bool \| None |
| `price` | Close on/before as_of (same close as Model A) | trading ccy \| None |
| `market_cap` | `price × shares_outstanding` (all classes, the engine's `caps`) | trading ccy \| None |
| `fx_rate` | Trading-ccy units per 1 reporting-ccy unit (as Model A); must be > 0 else `ValueError` | 1.0 |
| `bvps` | **Model A's `b0`** (CSM-adjusted where Model A adjusts; None when A has none) | trading ccy \| None |
| `ni_ttm`, `ni_period_end` | Model A's NI to common TTM (None on `CCY_MISMATCH`) and its TTM `period_end` | reporting ccy, date \| None |
| `roe` | **Model A's `roe_raw`** (TTM basis, unclipped, no 3-year average, CSM-adjusted equity) | decimal \| None |
| `cfo_ttm`, `cfo_period_end` | TTM operating cash flow (`ttm.ttm` on `cfo_q`) and its `period_end` | reporting ccy, date \| None |
| `capex_ttm`, `capex_period_end` | TTM capex (`capex_q`, stored as a **positive** outflow) and its `period_end` | reporting ccy, date \| None |
| `gross_profit_ttm` | TTM gross profit (`gross_profit_q`) | reporting ccy \| None |
| `total_assets`, `total_debt` | From the balance-sheet filing `build_inputs` selected (the one behind `bvps`) | reporting ccy \| None |
| `mom`, `vol` | `momentum(adj_closes)`, `volatility(adj_closes)` (below) | decimal \| None |
| `flags` | Passed through (e.g. `NO_TTM`, `STALE_TTM`, `PIT_ESTIMATED`) | frozenset() |

All monetary inputs except `price`, `market_cap` and `bvps` are in the reporting currency. Missing data is `None` and
is **never** replaced by 0.

## Price metrics (`momentum`, `volatility`)
Input `adj_closes`: the ticker's own `adj_close` values in date order, from the bars with `date ≤ as_of` and
`adj_close` not null (exactly the bars `eligibility` counts as `history_sessions`), so `len(adj_closes) ==
history_sessions`. The last element is the last bar on/before as_of (`t`). Any value ≤ 0 → `ValueError`.
Indices are Python indices into that list (sessions of the ticker, not calendar days):
```
momentum(c)   = c[-1 - 21] / c[-1 - 273] − 1           = adj_close(t−21) / adj_close(t−273) − 1
                needs len(c) ≥ 274, else None
volatility(c) = numpy.std(numpy.diff(numpy.log(c[-253:])), ddof=1) × sqrt(252)
                (the last 253 bars → the last 252 daily log returns; sample stdev = STDEV.S)
                needs len(c) ≥ 253, else None
```
`neg_vol = −vol`. Note D5: E6 passes with 273 bars but momentum needs 274 (t and 273 sessions before it).

## Raw metrics (`raw_metrics(inp) -> tuple[dict[str, float | None], frozenset[str]]`)
Every metric is None when any input it needs is None or fails its guard. `cap_rep = market_cap / fx_rate`
(market cap in reporting currency), only when `market_cap > 0`.
```
ep           = ni_ttm / cap_rep                          (= EPS_TTM / price)
bp           = bvps / price   (price > 0)  — v0.2.0: only if sector ∈ BOOK_SECTORS, else None (no flag)
roe          = roe                                       (passed through unchanged)
mom          = mom ;   neg_vol = −vol
-- non-financials only (sector != "Financials"; for Financials these four are None, not 0):
fcfp         = (cfo_ttm − capex_ttm) / cap_rep           only if cfo_period_end == capex_period_end, else None + FCF_PERIOD_MISMATCH
gpa          = gross_profit_ttm / total_assets           (total_assets > 0)
neg_accruals = −(ni_ttm − cfo_ttm) / total_assets        (total_assets > 0) only if ni_period_end == cfo_period_end, else None + ACCRUALS_PERIOD_MISMATCH
neg_leverage = −total_debt / total_assets                (total_assets > 0)
```
Mismatch flags are set only when both flows exist and their period ends differ. Negative E/P, B/P and FCF/P are kept
(they rank low); they are not set to missing (D4). `raw_metrics` is computed for every input, eligible or not.

## Cross-section (`run_composite`)
**Statistics universe** `U` = inputs with `eligible is True` and `history_ok is True` and a non-empty `sector`.
**v0.2.0: steps 1–5 run separately per market** on `U_m` = names in U with `market == m` (winsor bounds, sector z with
groups (market, sector), `pct`, `mom_pct`, `factor_pct`); markets never influence each other. Below, "U" means `U_m`. Names outside U get raw metrics only: no winsorized value, z, factor, S or percentile, and they
never influence any name in U.

1. **Winsorize (pooled, per metric).** For metric m: `V_m` = raw values of names in U with m not None.
   `lo_m, hi_m = winsor_bounds(V_m)` = `numpy.percentile(V_m, [5, 95])` (default `linear`). `w_i,m =
   min(max(x_i,m, lo_m), hi_m)` for names in U. `V_m` empty → no bounds, no w.
2. **Sector z-score.** For sector s and metric m: `G` = names in U with sector s and `w_m` not None, `n = |G|`,
   `mean = numpy.mean(w)`, `std = numpy.std(w, ddof=0)` (population). If `n ≥ min_sector_names` and `std > 1e-12`:
   `z_i,m = (w_i,m − mean)/std` for i in G; otherwise no z for that (sector, metric).
3. **Factor score** = arithmetic mean of the name's available z of that factor's metrics (≥ 1), else None.
   `value`, `quality`, `momentum`, `risk` (V, Q, M, R). M = z_mom, R = z_neg_vol.
4. **Composite** `S` = arithmetic mean of the available factor scores, if at least `MIN_FACTORS = 3` are available,
   else None. Equal weights over the available factors: with 3 factors S = (sum of the 3)/3, i.e. weight 1/3 each.
   With all 4 this is exactly the proposal's `0.25·V + 0.25·Q + 0.25·M + 0.25·R`.
5. **Percentile** `pct_rank(values)` for a mapping ticker → value with n entries: `rank` = ascending rank with
   average ranks for ties (1 = lowest; = `scipy.stats.rankdata(method="average")`), `pct = (rank − 1)/(n − 1)`;
   n < 2 → every pct None. Higher value → higher pct.
   - `pct` (signal): over `P` = names in U with S not None, values S.
   - `mom_pct` (veto): over names in U with `mom` not None, values the **raw** `mom` (winsorizing would create ties).
   - `factor_pct[k]` (diagnostic): over names in U with factor k not None, values the factor score.
6. **Mapping** `map_signal(pct)`: `"BUY"` if `pct ≥ 0.80`; elif `"SELL"` if `pct ≤ 0.20`; else `"HOLD"`.
7. **Vetoes** (only on a BUY; the name becomes `"HOLD"`, both flags set when both apply):
   - `VETO_DISTRESS`: `bvps is not None and bvps ≤ 0 and ni_ttm is not None and ni_ttm < 0`.
   - `VETO_MOMENTUM`: `mom_pct is not None and mom_pct ≤ 0.10`.
   A HOLD or SELL that meets a veto condition gets no flag (`mom_pct` is reported, so the human sees it).

## Signal and precedence
For each name the first matching row wins:

| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` | `eligible is not True` (no_signal_reason None) |
| 2 | `NO_SIGNAL` / `SHORT_HISTORY` | `history_ok is not True` (E6 failed) |
| 3 | `NO_SIGNAL` / `NO_SECTOR` | sector None or `""` |
| 4 | `NO_SIGNAL` / `FEW_FACTORS` | fewer than 3 of the 4 factor scores (covers NO_TTM names without B/P etc.) |
| 5 | `NO_SIGNAL` / `FEW_NAMES` | `|P_m| < 2` in the name's market (pct undefined; not reachable in a real run) |
| 6 | `map_signal(pct)`, then vetoes | |

## Flags (model flags, added to the passed-through `flags`)
| Flag | Condition |
|---|---|
| `VETO_DISTRESS`, `VETO_MOMENTUM` | Step 7, only when a BUY was turned into HOLD |
| `FCF_PERIOD_MISMATCH` | CFO and capex TTM both present, different `period_end` → `fcfp` None |
| `ACCRUALS_PERIOD_MISMATCH` | NI and CFO TTM both present, different `period_end` → `neg_accruals` None |

No-signal reasons: `SHORT_HISTORY`, `NO_SECTOR`, `FEW_FACTORS`, `FEW_NAMES`. A name with `NO_TTM` loses only the flow
metrics (ep, fcfp, roe, gpa, neg_accruals); it still gets B/P, leverage, M and R and can get a signal with 3 factors.

## Factor diagnostics (backtest only, not signals)
`factor_labels[k] = map_signal(factor_pct[k])` (None when factor_pct is None), with `k` in value/quality/momentum/risk.
They use the same quintile rule as S, inside U restricted to names with that factor, independent of S and with no
vetoes. They exist so the harness can report each factor's own spread and IC (proposal §4 QA): a dead factor is
visible rather than hidden in the composite.

## Worked examples (golden values; absolute tolerance 1e-6 on printed values)
**G1 (proposal X/Y/Z, z-scores given).** S = mean of V, Q, M, R (4 factors):

| Ticker | V | Q | M | R | S | rank / n | pct | mom_pct | Signal |
|---|---|---|---|---|---|---|---|---|---|
| X | +1.2 | +0.8 | +0.4 | +0.2 | (1.2+0.8+0.4+0.2)/4 = **+0.65** | 47 / 51 | 46/50 = **0.92** | — | **BUY** |
| Y | +1.6 | −0.6 | −1.9 | −0.4 | −1.3/4 = **−0.325** | 16 / 51 | 15/50 = **0.30** | 4/50 = 0.08 | **HOLD**, no flag |
| Z | −1.0 | −0.5 | −0.8 | −1.1 | −3.4/4 = **−0.85** | 4 / 51 | 3/50 = **0.06** | — | **SELL** |

The proposal's "Pct in universe" is reproduced by a 51-name universe with the ranks shown. The proposal rounds Y's S
to −0.33; the exact value is −0.325. Y's MOM is in the bottom 10% (`mom_pct` 0.08 ≤ 0.10), but Y is HOLD, so the veto
changes nothing and no flag is set. **G1b:** the same Y with pct 0.84 (rank 43 of 51) → BUY → vetoed → `HOLD` +
`VETO_MOMENTUM`. Boundaries: `map_signal(0.80) = "BUY"`, `(0.7999) = "HOLD"`, `(0.20) = "SELL"`, `(0.2001) = "HOLD"`;
`mom_pct = 5/50 = 0.10` vetoes, `6/50 = 0.12` does not.

**G1c small functions.** `winsor_bounds([1, 2, 3, 4, 100]) = (1.2, 80.8)`; `winsorize([1, 2, 3, 4, 100], 1.2, 80.8)
= [1.2, 2, 3, 4, 80.8]`. `pct_rank({"a": 1, "b": 2, "c": 2, "d": 3}) = {a: 0, b: 0.5, c: 0.5, d: 1}`;
`pct_rank({"a": 5})` → `{a: None}`. `sector_zscores([1, 2, 3, 4, 5])` → mean 3, std √2, z
`[−1.414214, −0.707107, 0, 0.707107, 1.414214]`; `sector_zscores([1, 2, 3, 4])` → z None (n < 5);
`sector_zscores([2, 2, 2, 2, 2])` → z None (std 0).

**G2 full run from raw inputs (`min_sector_names=3` passed explicitly** so that the tiny universe still produces
z-scores while one sector-metric (Industrials gpa, n = 2) shows the minimum rule). All period ends 2026-09-30 except
I4's capex (2026-06-30). I5 and F5 carry `flags={"NO_TTM"}` and no flow inputs. Every other omitted field is None;
`history_ok` True for all. Input order as listed. Sector Ind = `"Industrials"`, Fin = `"Financials"`.

| Ticker | Sector | elig | price | market_cap | fx | bvps | ni_ttm | roe | cfo_ttm | capex_ttm | gross_profit_ttm | total_assets | total_debt | mom | vol |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| I1 | Ind | T | 50 | 10000 | 1 | 20 | 800 | 0.18 | 1100 | 300 | 3000 | 8000 | 1600 | 0.25 | 0.19 |
| I2 | Ind | T | 40 | 8000 | 1 | −2 | −50 | — | 1400 | 200 | 4000 | 6000 | 5000 | 0.6 | 0.15 |
| I3 | Ind | T | 30 | 6000 | 1 | 25 | 700 | 0.17 | 1300 | 200 | — | 7000 | 700 | −0.35 | 0.16 |
| I4 | Ind | T | 60 | 12000 | 1 | 15 | 500 | 0.11 | 700 | 400 (pe 06-30) | — | 10000 | 4000 | 0.05 | 0.3 |
| I5 | Ind | T | 25 | 5000 | 1 | 12 | — | — | — | — | — | — | — | 0.1 | 0.28 |
| I6 | Ind | T | 54 | 13500 | 1.35 | 27 | 300 | 0.08 | 450 | 350 | — | 9000 | 3600 | −0.05 | 0.35 |
| I7 | Ind | T | 80 | 16000 | 1 | 10 | 200 | 0.06 | 150 | 250 | — | 12000 | 7000 | −0.15 | 0.45 |
| X1 | Ind | **F** | 5 | 500 | 1 | 30 | −400 | −0.5 | −100 | 50 | — | 2000 | 1800 | −0.8 | 1.2 |
| F1 | Fin | T | 100 | 50000 | 1 | 70 | 4500 | 0.1 | 9000 | 500 | — | 900000 | 100000 | 0.06 | 0.21 |
| F2 | Fin | T | 60 | 20000 | 1 | 50 | 1500 | 0.09 | — | — | — | — | — | 0.08 | 0.22 |
| F3 | Fin | T | 45 | 9000 | 1 | 60 | 600 | 0.05 | — | — | — | — | — | −0.1 | 0.3 |
| F4 | Fin | T | 120 | 30000 | 1 | 40 | 3600 | 0.16 | — | — | — | — | — | −0.25 | 0.25 |
| F5 | Fin | T | 20 | 4000 | 1 | — | — | — | — | — | — | — | — | 0.02 | 0.26 |

*Raw metrics → winsorized value* (`a → b` = clipped to b; no arrow = unchanged). X1 is outside U: raw only. F1's
cfo/capex/total_assets/total_debt are ignored (Financials). I6: cap_rep = 13500/1.35 = 10000.

| Ticker | ep | bp | fcfp | roe | gpa | neg_accruals | neg_leverage | mom | neg_vol |
|---|---|---|---|---|---|---|---|---|---|
| I1 | 0.080000 | 0.400000 | 0.080000 | 0.180000 → 0.176000 | 0.375000 → 0.389583 | 0.037500 | −0.200000 | 0.250000 | −0.190000 |
| I2 | −0.006250 → 0.002188 | −0.050000 → 0.037500 | 0.150000 | — | 0.666667 → 0.652083 | 0.241667 → 0.202679 | −0.833333 → −0.770833 | 0.600000 → 0.407500 | −0.150000 → −0.155500 |
| I3 | 0.116667 | 0.833333 | 0.183333 → 0.176667 | 0.170000 | — | 0.085714 | −0.100000 → −0.125000 | −0.350000 → −0.295000 | −0.160000 |
| I4 | 0.041667 | 0.250000 | — | 0.110000 | — | 0.020000 | −0.400000 | 0.050000 | −0.300000 |
| I5 | — | 0.480000 | — | — | — | — | — | 0.100000 | −0.280000 |
| I6 | 0.030000 | 0.500000 | 0.010000 | 0.080000 | — | 0.016667 | −0.400000 | −0.050000 | −0.350000 |
| I7 | 0.012500 | 0.125000 | −0.006250 → −0.003000 | 0.060000 | — | −0.004167 → 0.001042 | −0.583333 | −0.150000 | −0.450000 → −0.395000 |
| X1 | −0.800000 | 6.000000 | −0.300000 | −0.500000 | — | 0.150000 | −0.900000 | −0.800000 | −1.200000 |
| F1 | 0.090000 | 0.700000 | — | 0.100000 | — | — | — | 0.060000 | −0.210000 |
| F2 | 0.075000 | 0.833333 | — | 0.090000 | — | — | — | 0.080000 | −0.220000 |
| F3 | 0.066667 | 1.333333 → 1.083333 | — | 0.050000 → 0.054000 | — | — | — | −0.100000 | −0.300000 |
| F4 | 0.120000 → 0.118500 | 0.333333 | — | 0.160000 | — | — | — | −0.250000 | −0.250000 |
| F5 | — | — | — | — | — | — | — | 0.020000 | −0.260000 |

*Winsor bounds* (pooled over U; numpy repr in brackets where it differs from the printed value):
ep [0.002188 (0.002187500000000002), 0.118500]; bp [0.037500, 1.083333]; fcfp [−0.003000, 0.176667];
roe [0.054000, 0.176000]; gpa [0.389583, 0.652083]; neg_accruals [0.001042, 0.202679];
neg_leverage [−0.770833, −0.125000]; mom [−0.295000, 0.407500 (0.40749999999999975)]; neg_vol [−0.395000, −0.155500].

*Sector stats* (n, mean, std ddof=0): Financials ep (4, 0.087542, 0.019733), bp (4, 0.737500, 0.270897),
roe (4, 0.101000, 0.038118), mom (5, −0.038000, 0.123028), neg_vol (5, −0.248000, 0.031875); Industrials ep
(6, 0.047170, 0.039703), bp (7, 0.375119, 0.247668), fcfp (5, 0.082733, 0.072106), roe (5, 0.119200, 0.046761),
**gpa (2, 0.520833, 0.131250) → n < 3, no z**, neg_accruals (6, 0.060600, 0.068892), neg_leverage (6, −0.413194,
0.218216), mom (7, 0.044643, 0.220107), neg_vol (7, −0.261500, 0.087936).

*Sector z-scores* (X1 has none):

| Ticker | z_ep | z_bp | z_fcfp | z_roe | z_gpa | z_neg_accruals | z_neg_leverage | z_mom | z_neg_vol |
|---|---|---|---|---|---|---|---|---|---|
| I1 | 0.826879 | 0.100461 | −0.037907 | 1.214696 | — | −0.335312 | 0.976989 | 0.932987 | 0.813089 |
| I2 | −1.132969 | −1.363193 | 0.932891 | — | — | 2.062346 | −1.638923 | 1.648547 | 1.205419 |
| I3 | 1.750396 | 1.850116 | 1.302719 | 1.086383 | — | 0.364545 | 1.320685 | −1.543079 | 1.154245 |
| I4 | −0.138615 | −0.505189 | — | −0.196747 | — | −0.589334 | 0.060465 | 0.024339 | −0.437817 |
| I5 | — | 0.423474 | — | — | — | — | — | 0.251501 | −0.210380 |
| I6 | −0.432461 | 0.504228 | −1.008706 | −0.838311 | — | −0.637719 | 0.060465 | −0.429985 | −1.006411 |
| I7 | −0.873230 | −1.009897 | −1.188997 | −1.266021 | — | −0.864525 | −0.779682 | −0.884309 | −1.518145 |
| F1 | 0.124582 | −0.138429 | — | −0.026234 | — | — | — | 0.796564 | 1.192166 |
| F2 | −0.635577 | 0.353762 | — | −0.288576 | — | — | — | 0.959128 | 0.878438 |
| F3 | −1.057888 | 1.276621 | — | −1.233006 | — | — | — | −0.503948 | −1.631385 |
| F4 | 1.568883 | −1.491955 | — | 1.547816 | — | — | — | −1.723179 | −0.062746 |
| F5 | — | — | — | — | — | — | — | 0.471436 | −0.376473 |

*Factors, composite and signal.* P = I1–I7, F1–F4 (n = 11, pct = (rank − 1)/10). The `mom_pct` set is U with mom
(12 names, divisor 11). Factor labels B/H/S = BUY/HOLD/SELL diagnostics in V/Q/M/R order.

| Ticker | V | Q | M | R | n | S | pct | mom_pct | signal / reason | flags (model) | V/Q/M/R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I1 | 0.296478 | 0.618791 | 0.932987 | 0.813089 | 4 | 0.665336 | 1.000000 | 0.909091 | **BUY** | | B/H/B/H |
| I2 | −0.521090 | 0.211711 | 1.648547 | 1.205419 | 4 | 0.636147 | 0.900000 | 1.000000 | **HOLD** (BUY vetoed) | VETO_DISTRESS | S/H/B/B |
| I3 | 1.634410 | 0.923871 | −1.543079 | 1.154245 | 4 | 0.542362 | 0.800000 | 0.000000 | **HOLD** (BUY vetoed) | VETO_MOMENTUM | B/B/S/B |
| I4 | −0.321902 | −0.241872 | 0.024339 | −0.437817 | 4 | −0.244313 | 0.300000 | 0.545455 | HOLD | FCF_PERIOD_MISMATCH | S/H/H/H |
| I5 | 0.423474 | — | 0.251501 | −0.210380 | **3** | 0.154865 | 0.500000 | 0.818182 | HOLD | (NO_TTM passed) | B/—/H/H |
| I6 | −0.312313 | −0.471855 | −0.429985 | −1.006411 | 4 | −0.555141 | 0.200000 | 0.363636 | **SELL** | | H/H/H/S |
| I7 | −1.024041 | −0.970076 | −0.884309 | −1.518145 | 4 | −1.099143 | 0.000000 | 0.181818 | **SELL** | | S/S/S/S |
| X1 | — | — | — | — | 0 | — | — | — | NOT_ELIGIBLE | | —/—/—/— |
| F1 | −0.006924 | −0.026234 | 0.796564 | 1.192166 | 4 | 0.488893 | 0.700000 | 0.636364 | HOLD | | H/H/H/B |
| F2 | −0.140907 | −0.288576 | 0.959128 | 0.878438 | 4 | 0.352021 | 0.600000 | 0.727273 | HOLD | | H/H/B/H |
| F3 | 0.109367 | −1.233006 | −0.503948 | −1.631385 | 4 | −0.814743 | 0.100000 | 0.272727 | **SELL** | | H/S/H/S |
| F4 | 0.038464 | 1.547816 | −1.723179 | −0.062746 | 4 | −0.049911 | 0.400000 | 0.090909 | HOLD (veto condition, no flag) | | H/B/S/H |
| F5 | — | — | 0.471436 | −0.376473 | 2 | — | — | 0.454545 | NO_SIGNAL / FEW_FACTORS | (NO_TTM passed) | —/—/H/H |

Checks: I1 Q = (1.214696 − 0.335312 + 0.976989)/3 (gpa has no z); I5 S = (0.423474 + 0.251501 − 0.210380)/3;
I3 pct = 8/10 = 0.80 → BUY boundary, then vetoed (mom_pct 0); I6 pct = 2/10 = 0.20 → SELL boundary; I2's B/P is
negative and ROE is None (negative average equity), and distress needs bvps ≤ 0 **and** NI < 0. F4 has mom_pct
0.090909 ≤ 0.10 but is HOLD → no flag. `factor_pct`: value I1 0.8, I2 0.1, I3 1.0, I4 0.2, I5 0.9, I6 0.3, I7 0.0,
F1 0.5, F2 0.4, F3 0.7, F4 0.6 (n 11); quality n 10 (I5 missing): I1 0.777778, I2 0.666667, I3 0.888889, I4 0.444444,
I6 0.222222, I7 0.111111, F1 0.555556, F2 0.333333, F3 0.0, F4 1.0; momentum and risk n 12 (F5 included): momentum
I1 0.818182, I2 1.0, I3 0.090909, I4 0.454545, I5 0.545455, I6 0.363636, I7 0.181818, F1 0.727273, F2 0.909091,
F3 0.272727, F4 0.0, F5 0.636364; risk I1 0.636364, I2 1.0, I3 0.818182, I4 0.272727, I5 0.454545, I6 0.181818,
I7 0.090909, F1 0.909091, F2 0.727273, F3 0.0, F4 0.545455, F5 0.363636.

**G2b default minimum.** G2 with the default `min_sector_names = 5`: Financials ep, bp, roe (n = 4) get no z, so
F1–F4 have only M and R → `NO_SIGNAL / FEW_FACTORS` (n_factors 2). Industrials z are unchanged (all n ≥ 5 except gpa).
P = I1–I7 (divisor 6): I1 BUY (pct 1.0), I2 HOLD + VETO_DISTRESS (0.833333), I3 **HOLD, no flag** (0.666667, not a
BUY, so no veto), I4 HOLD (0.333333), I5 HOLD (0.5), I6 SELL (0.166667), I7 SELL (0.0); S values as in G2.

**G2c ineligible names are excluded.** G2 with X1 `eligible=True`: the bp bounds become (0.04625, 3.433333) instead of
(0.0375, 1.083333), so X1's exclusion in G2 is visible. G2 with X1 removed: every result identical to G2. G2 with I1's
`history_ok=False`: I1 → `NO_SIGNAL / SHORT_HISTORY` and it leaves U (all statistics recomputed without it).

**G2-v2 (v0.2.0): G2 under v0.2.0** (all names `market="US"`, `min_sector_names=3`). Only B/P changes: Industrials
have bp None (`raw_metrics` bp column "—" for I1–I7, X1); Financials keep it, with bounds now from F1–F4 only:
bp **[0.3883333333333333, 1.258333333333333]**, Financials bp (4, 0.795, 0.31246999855986) → z_bp F1 −0.304029, F2
0.122678, F3 1.482809, F4 −1.301458. Every other bound, sector stat and z is as in G2. **I5 loses its only value metric
→ 2 factors → `NO_SIGNAL / FEW_FACTORS`.** P = 10 names (divisor 9):

| Ticker | V | S | pct | signal | value factor_pct |
|---|---|---|---|---|---|
| I1 | 0.394486 | 0.689838 | 0.888889 | **BUY** | 0.888889 |
| I2 | −0.100039 | 0.741410 | 1.000000 | **HOLD** + VETO_DISTRESS | 0.444444 |
| I3 | 1.526557 | 0.515399 | 0.777778 | HOLD (not a BUY → no veto flag) | 1.0 |
| I4 | −0.138615 | −0.198491 | 0.333333 | HOLD + FCF_PERIOD_MISMATCH | 0.333333 |
| I5 | — | — | — | NO_SIGNAL / FEW_FACTORS | — |
| I6 | −0.720583 | −0.657209 | 0.222222 | HOLD | 0.111111 |
| I7 | −1.031113 | −1.100911 | 0.000000 | **SELL** | 0.0 |
| F1 | −0.089724 | 0.468193 | 0.666667 | HOLD | 0.555556 |
| F2 | −0.256449 | 0.323135 | 0.555556 | HOLD | 0.222222 |
| F3 | 0.212461 | −0.788970 | 0.111111 | **SELL** | 0.777778 |
| F4 | 0.133713 | −0.026099 | 0.444444 | HOLD (mom_pct ≤ 0.10, no flag) | 0.666667 |

Q, M, R per name as in G2 (I1 V = (z_ep + z_fcfp)/2 = (0.826879 − 0.037907)/2). The v0.1.0 G2 tables above stay as
the reference for the unchanged steps (raw metrics other than bp, z of other metrics).

**G4 (v0.2.0) two markets.** `min_sector_names=3`; inputs (fx 1, no flow/balance-sheet extras, so fcfp, gpa,
neg_accruals, neg_leverage are None; history_ok and eligible True):

| Ticker | market | sector | price | market_cap | bvps | ni_ttm | roe | mom | vol |
|---|---|---|---|---|---|---|---|---|---|
| U1 | US | Industrials | 50 | 10000 | 20 | 800 | 0.18 | 0.25 | 0.20 |
| U2 | US | Industrials | 40 | 8000 | 10 | 300 | 0.10 | 0.05 | 0.25 |
| U3 | US | Industrials | 30 | 6000 | 25 | 600 | 0.15 | −0.10 | 0.30 |
| U4 | US | Industrials | 60 | 12000 | 12 | 400 | 0.12 | 0.40 | 0.22 |
| F1 | US | Financials | 100 | 50000 | 70 | 4500 | 0.10 | 0.06 | 0.21 |
| F2 | US | Financials | 60 | 20000 | 50 | 1500 | 0.09 | 0.08 | 0.22 |
| F3 | US | Financials | 45 | 9000 | 60 | 600 | 0.05 | −0.10 | 0.30 |
| T1 | TSX | Industrials | 20 | 2000 | 15 | 250 | 0.14 | 0.30 | 0.24 |
| T2 | TSX | Industrials | 35 | 3500 | 10 | 140 | 0.08 | −0.20 | 0.35 |
| T3 | TSX | Industrials | 15 | 1500 | 12 | 210 | 0.16 | 0.10 | 0.28 |

Bounds US: ep [0.034583333333333334, 0.097], bp [0.7133333333333333, 1.2833333333333332] (F1–F3 only), roe [0.062,
0.171], mom [−0.1, 0.355], neg_vol [−0.3, −0.203]; TSX: ep [0.0485, 0.1385], roe [0.086, 0.158], mom [−0.17, 0.28],
neg_vol [−0.343, −0.244] (no bp: no TSX book-sector name).

| Ticker | V | Q | M | R | S | pct (own market) | mom_pct | signal |
|---|---|---|---|---|---|---|---|---|
| U1 | 0.658373 | 1.311816 | 0.631810 | 1.092763 | 0.923691 | 1.0 (/6) | 0.833333 | **BUY** |
| U2 | −0.919866 | −1.293469 | −0.504028 | −0.183258 | −0.725155 | 0.166667 | 0.333333 | **SELL** |
| U3 | 1.289669 | 0.541239 | −1.355906 | −1.540728 | −0.266432 | 0.333333 | 0.083333 | HOLD |
| U4 | −1.028176 | −0.559586 | 1.228125 | 0.631223 | 0.067896 | 0.5 | 1.0 | HOLD |
| F1 | 0.193046 | 0.994832 | 0.579324 | 0.827606 | 0.648702 | 0.833333 | 0.5 | **BUY** |
| F2 | −0.339250 | 0.373062 | 0.827606 | 0.579324 | 0.360185 | 0.666667 | 0.666667 | HOLD |
| F3 | 0.146205 | −1.367894 | −1.406930 | −1.406930 | −1.008887 | 0.0 | 0.083333 | **SELL** |
| T1 | 0.529908 | 0.392232 | 1.135550 | 1.099853 | 0.789386 | 1.0 (/2) | 1.0 | **BUY** |
| T2 | −1.400471 | −1.372813 | −1.297771 | −1.319824 | −1.347720 | 0.0 | 0.0 | **SELL** |
| T3 | 0.870563 | 0.980581 | 0.162221 | 0.219971 | 0.558334 | 0.5 | 0.5 | HOLD |

F1 V = (z_ep 1.323501 + z_bp −0.937410)/2. Changing any TSX input changes no US result and vice versa.

**G3 momentum and volatility (session indexing).**
- `c = [1.0, 2.0, …, 300.0]` (c_k = k + 1, 300 bars): `momentum = c[-22]/c[-274] − 1 = 279/27 − 1 = 9.333333333333334`
  (exact). Off by one in either index gives 280/28 − 1 = 9.0 or 279/28 − 1 = 8.964286, so this catches both mistakes.
- Geometric `c_k = 100 × 1.001^k`, k = 0..273 (274 bars): `momentum = 1.001^252 − 1 = 0.286434044376152` (±1e-12);
  `volatility` ≈ 0 (|vol| < 1e-12, all log returns equal). The first 273 bars only → `momentum` **None**,
  `volatility` ≈ 0 (253 ≤ 273).
- Alternating `c_k = 100 × exp(0.01 × (k mod 2))`, 274 bars: the last 252 log returns are ±0.01 alternately, mean 0 →
  `volatility = 0.01 × sqrt(252/251) × sqrt(252) = 0.01 × 252/sqrt(251) = 0.159060989580646` (±1e-12);
  `momentum = c[252]/c[0] − 1 = 0.0`. Setting `c[-254] = 1000` (outside the window) changes neither value; setting
  `c[-253] = 1000` (first bar of the window) changes `volatility` to 2.308090. The last 253 bars → 0.159060989580646;
  the last 252 bars → `volatility` **None**.
- `momentum([100, 0, 100, …])` or any value ≤ 0 → `ValueError`.

## Property expectations (QA)
- **v0.2.0 market separation:** adding, removing or changing any name of one market changes no result, bound or stat
  of the other market. `bp` is None for every name outside BOOK_SECTORS.
- **Permutation:** permuting the inputs permutes the results identically; stats unchanged.
- **Outsiders don't count:** adding a `NOT_ELIGIBLE`, `SHORT_HISTORY` or `NO_SECTOR` name (any values) changes no other
  name's result and no bound or sector stat.
- **Scale invariance:** multiplying every monetary input of one name (price, market_cap, bvps, ni, cfo, capex, gross
  profit, total assets, total debt) by k > 0 changes no result (all metrics are ratios); so does changing its
  reporting currency with `fx_rate` adjusted to match. Multiplying all `adj_closes` by k leaves `momentum` and
  `volatility` unchanged (1e-12).
- **Sector neutrality:** for each (sector, metric) with a z, the z values have mean 0 and population std 1 (1e-12).
- **Monotonicity:** raising one name's S (e.g. its R) never lowers its pct; a BUY never becomes SELL by a veto.
- `S` equals the mean of the non-None factor scores, and `n_factors` equals their count, for every name with S.
- Results are in input order, one per input; duplicate tickers → `ValueError`.

**Look-ahead** is the engine's job (and the backtest's): `CompositeInputs` are built only from fundamentals with
(effective) `filed_date ≤ as_of` and prices `≤ as_of`; the model functions have no dates except the period-end
equality checks.

## API contract (SW and QA)
```python
from valuation.models.composite import (
    CompositeInputs, CompositeResult, CompositeStats, SectorStat,
    MODEL_VERSION, BOOK_SECTORS, MARKETS, WINSOR, MIN_SECTOR_NAMES, MIN_FACTORS, BUY_PCT, SELL_PCT, MOM_VETO_PCT,
    MOM_SKIP, MOM_LOOKBACK, VOL_WINDOW, ANNUALIZE, STD_EPS, FINANCIALS, FACTORS, METRICS,
    momentum, volatility, raw_metrics, winsor_bounds, winsorize, sector_zscores, pct_rank,
    map_signal, run_composite,
)
```
- `momentum(adj_closes: Sequence[float]) -> float | None`; `volatility(adj_closes: Sequence[float]) -> float | None`.
- `CompositeInputs(ticker, sector, eligible, history_ok, market="US", price=None, market_cap=None, fx_rate=1.0, bvps=None,
  ni_ttm=None, ni_period_end=None, roe=None, cfo_ttm=None, cfo_period_end=None, capex_ttm=None,
  capex_period_end=None, gross_profit_ttm=None, total_assets=None, total_debt=None, mom=None, vol=None,
  flags=frozenset())`: frozen dataclass; period ends are `datetime.date | None`.
- `raw_metrics(inp) -> tuple[dict[str, float | None], frozenset[str]]` (all 9 METRICS keys; mismatch flags).
- `winsor_bounds(values) -> tuple[float, float]` (empty → `ValueError`); `winsorize(values, lo, hi) -> list[float]`.
- `sector_zscores(values, min_names=MIN_SECTOR_NAMES) -> tuple[float, float, list[float] | None]` (mean, std ddof=0,
  z or None when `n < min_names` or `std ≤ STD_EPS`); empty → `ValueError`.
- `pct_rank(values: Mapping[str, float]) -> dict[str, float | None]`; `map_signal(pct: float) -> str`.
- `run_composite(inputs: Sequence[CompositeInputs], min_sector_names: int = MIN_SECTOR_NAMES)
  -> tuple[list[CompositeResult], CompositeStats]`.
- `CompositeResult`: `ticker, sector, signal, no_signal_reason, score (= S), pct, n_factors, value, quality, momentum,
  risk, mom_pct, metrics (raw, 9 keys), winsorized (dict, only metrics with a value), z (dict, only metrics with a z),
  factor_pct (dict 4 keys, value or None), factor_labels (dict 4 keys, label or None), flags (frozenset)`.
  Names outside U: `n_factors = 0`, factors/score/pct/mom_pct None, `winsorized = z = {}`, factor entries None.
- `CompositeStats` (v0.2.0 keyed by market): `bounds: dict[tuple[str, str], tuple[float, float]]` keyed (market,
  metric), `sectors: dict[tuple[str, str, str], SectorStat]` keyed (market, sector, metric) with `SectorStat(n, mean,
  std, used: bool)` for every key with n ≥ 1 in `U_m`.

## Data mapping (engine, SW; `run_signals` gains Model C)
After Models A and B, the engine builds one `CompositeInputs` per universe ticker at `as_of`:

| Field | Source |
|---|---|
| sector, eligible, market | as for Model A (`securities` GICS sector; `eligibility.eligible`; market) |
| history_ok | `eligibility[t].model_c_history_ok` (None if no eligibility row → NOT_ELIGIBLE anyway) |
| price, market_cap, fx_rate | `build_inputs`: `inp.price`, `inp.price × inp.shares_outstanding`, `inp.fx_rate` |
| bvps, roe, ni_ttm | Model A's `b0`, `roe_raw`, and the NI it used (`ni_common_ttm`, None on `CCY_MISMATCH`); `ni_period_end` = NI TTM `period_end` |
| cfo, capex, gross profit | `ttm.ttm` per source on `cfo_q`, `capex_q` (positive outflow), `gross_profit_q`; latest `period_end` wins, EDGAR on ties (ttm §3). `NO_TTM` → None. A unit ≠ the balance sheet's reporting currency → None (flag `FLOW_CCY_MISMATCH`) |
| total_assets, total_debt | the same filing `build_inputs` selected for book value (latest public balance sheet, filed ≤ as_of); not re-aligned to the TTM quarter. Missing → None |
| mom, vol | `prices_daily` bars of the ticker with `date ≤ as_of` and `adj_close` not null, date order → `momentum`, `volatility` |
| flags | NI TTM flags (`NO_TTM`, `STALE_TTM`) and `PIT_ESTIMATED` as for Model A |

A ticker without a `build_inputs` row still gets an input (price, cap and fundamentals None; mom and vol from prices),
so it lands in `FEW_FACTORS` if eligible. `revenue_q`, `ebit_q` and `cash` (also in data batch 2) are not used by
Model C (Model B EV branch, Phase 10). All names of one as_of run together; v0.2.0 statistics per market.

**Report** (`screen_signals.csv`): adds `c_signal, c_no_signal_reason, c_score, c_pct, c_n_factors, c_value,
c_quality, c_momentum, c_risk`; model flags join `flags`. Payload (`signals` JSON) adds `c_metrics`, `c_winsorized`,
`c_z`, `c_mom`, `c_vol`, `c_mom_pct`, `c_factor_pct`, `c_factor_labels`. `runs.config_json` adds `MODEL_VERSION` and
`CompositeStats` (bounds and sector stats). Counts per market and label as for A and B.

**Backtest** (`backtest.md` §3, §7; harness change is a separate task): model `C` with `label = c_signal` and
`score = S` where `c_signal ∈ {BUY, HOLD, SELL}`, else NaN. Diagnostic models `C_V`, `C_Q`, `C_M`, `C_R`: `label =
factor_labels[k]`, `score` = the factor score where that label is not None, else NaN. The §4 metrics apply unchanged;
the pass bar (proposal §6) applies to `C` only; the factor rows show each factor's own spread and IC. MOM needs 274
bars before the first backtest date.

## External checks (P9 QA)
- MOM recomputed from Yahoo's adjusted-close download for 5 tickers (≥ 2 TSX): exact to 1e-9 with the same session
  indexing (count sessions in the download, not calendar days).
- VOL vs a spreadsheet `STDEV.S(LN(P_t/P_{t−1}))` over the last 252 returns × `SQRT(252)`, 1e-9.
- E/P vs `1 / Yahoo trailingPE` within ±10% relative (differences: share count, NI to common vs total, TTM timing).
- Golden z-score file: 20 real names of one run with raw metrics, bounds, sector stats and z, recomputed independently
  in pandas to 1e-9. Results: `docs/qa/`.

## Decisions (where the proposal is silent; fixed a priori, before any Model C backtest)
- **D1 ROE** = Model A's `roe_raw`, unclipped: the proposal's NI_TTM/avg_CE; winsorizing already limits outliers, and
  Models A, B, C then see the same ROE. Negative-equity names get None (no ROE), not a huge number.
- **D2 Book** = Model A's `b0` (CSM-adjusted for CSM insurers), as in Model B, so all three models see the same book.
- **D3 NI** = NI to common, also in accruals: it is the only NI item stored; the NCI share is small for most names.
- **D4 Negative ratios are kept** (E/P, B/P, FCF/P < 0 rank low after winsorizing): dropping them would hide the
  weakest names from Value.
- **D5 Momentum indices exactly as written** (t−21, t−273 → 274 bars) while E6 needs 273: neither fixed parameter
  is changed; a name with exactly 273 bars gets M None (it can still score with 3 factors). Open point for the PM:
  set eligibility `min_history_sessions` to 274 (a config value, not a Model C change).
- **D6 Universe U** = eligible ∧ E6 ∧ sector: statistics use exactly the names Model C can score; NOT_ELIGIBLE and
  short-history names never move the bounds, z, quintiles or veto percentile.
- **D7 Winsorizing is pooled** over U (proposal: "of the eligible universe"); z is per sector. Percentile method =
  numpy default `linear`, the same as Model B.
- **D8 z uses population std (ddof = 0):** it standardizes the whole sector cross-section rather than estimating a
  population; sector z then have std exactly 1.
- **D9 `MIN_SECTOR_NAMES = 5`** per (sector, metric): with fewer names a z-score is mostly noise; 5 is small enough
  that almost every pooled sector qualifies (the only new parameter).
- **D10 Percentile** = `(rank − 1)/(n − 1)`, average ranks, as the backtest's P/B baseline; boundaries inclusive
  (≥ 0.80, ≤ 0.20, ≤ 0.10). The veto uses the raw MOM over all of U (not only names with S), so the "bottom 10%" is
  the universe's, as in the proposal.
- **D11 Veto flags only when applied** (BUY → HOLD); a missing input never triggers a veto.
- **D12 Flow pairs need equal period ends** (FCF, accruals) and otherwise drop the metric with a flag; balance-sheet
  denominators come from the book-value filing, not re-aligned, to avoid a second filing-selection rule.

## Revision v0.2.0 (revision round v2; evidence: `docs/qa/backtest_model_c.md`, phase9-validation §3–§5)
In-sample evidence only; holdout untouched; parameters frozen in `revision_v2_freeze.md`.

**R1 Value: kept at equal weight, but B/P only where book measures invested capital (BOOK_SECTORS).** Options
weighed: keep as is, re-weight, drop, or a quality-adjusted value.
- *Evidence:* V's 12m IC −0.056 (t −1.32, not significant), 3/10 years; Q +0.022, M ≈ 0, R +0.038 (none significant).
- *Not dropped, not re-weighted:* multi-year value droughts are documented (proposal §4), so a decade of negative
  value in US large caps is weak evidence against the long-run premium; re-weighting toward Q and R would fit weights to
  four insignificant in-sample ICs. Equal weights stay.
- *Not a quality-adjusted residual value:* that is Model B's construction (P/B regressed on ROE), which had the same
  negative sign (−0.054); Q is already a separate factor in C.
- *Structural fix:* in asset-light sectors book equity omits most invested capital (expensed R&D, brand, software;
  buybacks shrink or turn book negative), so B/P there ranks accounting conventions, not cheapness. Models A and B
  already treat book as meaningful only in BOOK_SECTORS (A's asset-light P/B gate, B's book branch); C now does the
  same. Outside book sectors V = mean of E/P and FCF/P; Financials keep E/P and B/P. No new parameter.
- *Overfitting risk:* **medium.** The direction agrees with the in-sample (value, partly B/P, lost), and Lev &
  Srivastava (2019)-type arguments became popular after value's drawdown. Mitigants: the B/P-only IC by sector was
  never measured here, and the rule reuses a sector set fixed in Phase 7, before any backtest.

**R2 Cross-section per market (P9-9; the brief calls it P9-8).** Pooled quintiles with sector-only neutrality give the
TSX 29% BUY vs US 16% (2026-11-10): sector means mix two markets with different valuation levels, so the BUY share
follows the market, not the stock. Per-market statistics make the quintiles 20/20 within each market by construction.
*Cost:* fewer names per (market, sector) on the TSX, so more `FEW_FACTORS` there (MIN_SECTOR_NAMES unchanged at 5).
*Backtest effect:* none on US in-sample dates (no TSX inputs there). *Overfitting risk:* low (fixes a live anomaly; not
measured on returns).

**R3 Momentum history (P9-6, D5).** `config/liquidity.yaml` `min_history_sessions` 273 → **274** in both markets
(eligibility.md v0.1.1), so every E6-passing name has MOM (t−273). The momentum formula (proposal's 12-1) is unchanged.
*Overfitting risk:* none (a definitional off-by-one).

Unchanged: factor definitions other than bp, equal weights, quintile bands, vetoes, MIN_FACTORS, MIN_SECTOR_NAMES,
winsor levels.

## Changelog
- **0.2.0:** revision round v2: bp only in BOOK_SECTORS, per-market cross-section (`market` input, stats keyed by
  market), E6 274 (eligibility); new goldens G2-v2 and G4 (throwaway numpy/scipy script; it reproduces the v0.1.0 G2
  exactly when bp is allowed in all sectors).
- **0.1.0:** initial version from proposal §4 (task P9-2). Proposal X/Y/Z arithmetic verified: S(Y) = −0.325
  (the proposal prints −0.33, rounded). Golden numbers computed with a numpy reference script (6 decimals printed).
