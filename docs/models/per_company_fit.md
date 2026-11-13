# Spec: Per-company model fit (`fit`, v0.1.0)

Owner: AN · Tests: QA (`tests/unit/test_fit_spec.py`, `test_fit_guard_spec.py`, `test_fit_screen_spec.py`, plus a
whole-market reproduction check) · Code: `src/valuation/backtest/fit.py` (new), small additions to `runner.py`,
`engine.py`, `report.py`, `cli_cmd.py`, `valuation/cli.py`, `valuation/report/screen.py` · Builds on `backtest.md`
v0.2.0 (harness, `forward_returns`, rebalance calendar) · Source: user decision 2026-11-13.

> **Screening signals only**, not investment advice. A per-company result says whether a model's past labels would
> have helped someone who already held that stock, over two fixed in-sample periods. It is not a forecast.
> **CONFIRMED means "worked in both windows", not "will work".** Every result is a **survivor-biased upper bound**
> (`backtest.md` §5). All models stay **unvalidated** under the `backtest.md` bar. Nothing here changes the freeze.

## Purpose
Phases 8–R2 tested each model across the whole S&P 500 (pooled BUY − SELL spreads, rank ICs). Every model failed
(`docs/qa/phase8-validation.md`, `docs/qa/r2-insample.md`). The user now takes a different view: assume each model
is sound, and find the companies where it works under backtesting. For each (company, model) pair, the per-company
mode compares a strategy driven by the model's monthly label with simply holding that stock. A pair is **CONFIRMED**
only if it passes a multiple-testing-corrected test in an early window and then keeps a minimum effect in a later
window. The holdout stays locked.

The harness keeps **two modes**:
1. **Whole-market** (`valuation backtest report`): `backtest.md`, unchanged. Its results must reproduce exactly.
2. **Per-company** (`valuation backtest fit`): this spec.

Both read the same stored per-date labels (`bt_rows`) and forward returns (`bt_returns`), written once by
`valuation backtest run`.

Out of scope:
- per-company tuning of any model parameter (no parameter is changed, chosen or fitted per company);
- the TSX market (history too short);
- any holdout-period date or return.

## 1. Constants (`fit.py`)
| Name | Value | Meaning |
|---|---|---|
| `FIT_MODEL_VERSION` | `"fit/0.1.0"` | this spec |
| `FIT_MARKET` | `"US"` | the only market fitted |
| `FIT_HORIZON` | `1` | months; 1-month forward returns from `forward_returns` |
| `FIT_SEL_START`, `FIT_SEL_END` | `date(2014, 1, 31)`, `date(2019, 12, 31)` | selection window (§4.3) |
| `FIT_CONF_START`, `FIT_CONF_END` | `date(2020, 1, 31)`, `date(2023, 10, 31)` | confirmation window (§4.3) |
| `FIT_LOCK_END` | `date(2023, 10, 31)` | last in-sample day; no signal date and no exit session after it (§7) |
| `FIT_COST_BP` | `10` | one-way cost per switch, basis points; `FIT_COST = FIT_COST_BP / 10_000 = 0.001` |
| `FIT_CASH_RATE` | `0.0` | monthly return of cash (§4.4) |
| `FIT_INITIAL_POSITION` | `1` | assumed position before a window's first month and after a gap (§4.5) |
| `FIT_Q` | `0.10` | Benjamini–Hochberg FDR level, selection (§6.2) |
| `FIT_MIN_EFFECT` | `0.01` | minimum annualised excess in confirmation (1 pp a year, inclusive) |
| `FIT_SEL_MIN_MONTHS`, `FIT_SEL_MIN_ACTIVE`, `FIT_SEL_MIN_SWITCHES` | `36`, `6`, `2` | selection activity minimums (§6.1) |
| `FIT_CONF_MIN_MONTHS`, `FIT_CONF_MIN_ACTIVE`, `FIT_CONF_MIN_SWITCHES` | `24`, `3`, `1` | confirmation activity minimums |
| `FIT_PRIMARY_VARIANT` | `"SELL_EXIT"` | the variant whose status drives the screen |
| `FIT_MODELS` | `("A", "B", "C", "H", "J", "G", "D", "E", "F")` | models fitted, in screen column order |
| `FIT_CAN_EMIT` | §3.2 | variants tested per model |

`FIT_PARAMS` is the dict below, built from these constants (dates as ISO strings, tuples as lists, `FIT_CAN_EMIT`
values as sorted lists). Its canonical JSON (`valuation.models.frozen.canonical_json`: `sort_keys=True`,
`separators=(",", ":")`, ASCII) hashes with `params_sha256` to the **fit params hash**:
```
{"FIT_CAN_EMIT":{"A":["BUY_ONLY","SELL_EXIT"],"B":["BUY_ONLY","SELL_EXIT"],"C":["BUY_ONLY","SELL_EXIT"],"D":["BUY_ONLY","SELL_EXIT"],"E":["BUY_ONLY","SELL_EXIT"],"F":["BUY_ONLY","SELL_EXIT"],"G":["SELL_EXIT"],"H":["BUY_ONLY","SELL_EXIT"],"J":["BUY_ONLY","SELL_EXIT"]},"FIT_CASH_RATE":0.0,"FIT_CONF_END":"2023-10-31","FIT_CONF_MIN_ACTIVE":3,"FIT_CONF_MIN_MONTHS":24,"FIT_CONF_MIN_SWITCHES":1,"FIT_CONF_START":"2020-01-31","FIT_COST_BP":10,"FIT_HORIZON":1,"FIT_INITIAL_POSITION":1,"FIT_LOCK_END":"2023-10-31","FIT_MARKET":"US","FIT_MIN_EFFECT":0.01,"FIT_MODELS":["A","B","C","H","J","G","D","E","F"],"FIT_MODEL_VERSION":"fit/0.1.0","FIT_PRIMARY_VARIANT":"SELL_EXIT","FIT_Q":0.1,"FIT_SEL_END":"2019-12-31","FIT_SEL_MIN_ACTIVE":6,"FIT_SEL_MIN_MONTHS":36,"FIT_SEL_MIN_SWITCHES":2,"FIT_SEL_START":"2014-01-31"}
```
fit params hash = `4d0a9e579c7d41a703ba4f2b0fdfd08f4883cb8315cb9b82e4c172313cd4b926`. QA pins it.
*Clarification (PM, 2026-11-13):* `fit_params_hash` in §11 is a function `fit_params_hash() -> str` returning this
hex string. Any change to
a constant is a new `FIT_MODEL_VERSION`.

**Fixed before any result.** Every threshold above was set in this spec before any per-company number was computed.
None may be tuned after seeing results. Tuning would void the luck guard.

## 2. Two-mode architecture
```
valuation backtest run     → bt_rows (labels A,B,C,H + new J,G,D,E,F), bt_returns (h = 1,3,6,12), bt_label_versions
         ├─ valuation backtest report → whole-market (backtest.md; reads h ∈ {3,6,12} only; unchanged)
         └─ valuation backtest fit    → company_model_fit, fit_runs, reports/backtest/company_model_fit.csv (+ .md)
valuation value            → screen_signals.csv gains <m>_fit, <m>_highlight, highlight, highlight_models (§9)
```

### 2.1 Changes to `backtest run` (labels and returns, computed once; resumable)
1. **New label columns.** `bt_rows` gains `j_signal VARCHAR, j_pct DOUBLE, g_signal VARCHAR, g_z DOUBLE,
   d_signal VARCHAR, d_mos DOUBLE, e_signal VARCHAR, e_f_score DOUBLE, f_signal VARCHAR, f_acc_pct DOUBLE`. They are
   copied in `engine.run_one` from the live `run_signals` report row (`j_signal`, `j_pct`, `g_signal`, `g_z`,
   `d_signal`, `d_mos`, `e_signal`, `e_f_score`, `f_signal`, `f_acc_pct`). `run_signals` already computes J, G, D, E
   and F on every backtest date and today discards them, so A/B/C/H labels and scores do not change. Older tables get
   these columns through the same `ALTER TABLE … ADD COLUMN` step used for the C/H columns. All five models are
   labelled **"unvalidated (not frozen, not in FROZEN_V2)"** wherever they appear.
2. **Part stamp.** `engine.MODEL_VERSION` becomes `"backtest/0.3.0"`. The part stamp (`run_backtest`'s `stamp`) gains
   `"label_versions"`, the dict model → that module's `MODEL_VERSION` for the nine `FIT_MODELS`. A part without it,
   or with different values, is re-run. (The old stamp did not cover J–F versions; see §15.) `run_model_version()`
   still contains the four `FROZEN_VERSIONS`, so the holdout guard (`holdout.py` check d) is unaffected.
3. **`bt_label_versions(universe VARCHAR, model VARCHAR, model_version VARCHAR, PRIMARY KEY (universe, model))`.**
   `run_backtest` rewrites it for the universe after loading the parts (nine rows, from the common stamp).
4. **1-month returns.** `runner.compute_returns` computes `forward_returns(prices, dates, horizons=RETURN_HORIZONS)`
   with `RETURN_HORIZONS = (FIT_HORIZON, *metrics.HORIZONS) = (1, 3, 6, 12)` into the same `bt_returns` table
   (`horizon = 1` rows). `metrics.HORIZONS` stays `(3, 6, 12)`.

### 2.2 Whole-market report: unchanged by construction
- `report.load_returns` must read `WHERE horizon IN (3, 6, 12)` (`metrics.HORIZONS`). Without this filter,
  `sector_excess` (which takes horizons from `fwd["horizon"].unique()`) would add h = 1 rows to `bt_metrics` and to
  the yearly tables. That would break reproduction (§15).
- The default `report` adds no model. Optional flag `report --extra-models` adds diagnostic rows `J, G, D, E, F`
  ("unvalidated, diagnostic, never a verdict"):
  - label = the `*_signal` column;
  - score where the label ∈ {BUY, HOLD, SELL}: J `−j_pct`, G `g_z`, D `d_mos`, E `e_f_score`, F `−f_acc_pct`.

  `--extra-models` with `--holdout` is **refused** (exit 2): the unfrozen models are never computed on holdout dates.
  *Clarification (PM):* the `--extra-models` rows go to `bt_metrics` and `bt_signals` like every other report model;
  `report --holdout` stays refused by the existing CLI as before.
- **QA reproduction check:**
  - Starting from a DB that holds the pre-change stored rows plus the new columns and h = 1 rows, the default `report`
    gives `bt_metrics`, `bt_diagnostics` and `bt_signals` row-identical (except `run_id`) to the report without the
    h = 1 rows and new columns.
  - On real data, the A, B, C, H in-sample numbers in `docs/qa/r2-insample.md` §1 reproduce to the printed precision.

## 3. Labels, positions and testable variants
### 3.1 Label source per model (`bt_rows`, `universe = 'US'`)
| Model | Spec / version | Label column | Emits |
|---|---|---|---|
| A | `residual_income` 0.2.0 | `a_signal`: in `bt_rows` this is the **model** label incl. SELL (freeze §3; `run_one` stores `a_model_signal`) | BUY, HOLD, SELL |
| B (diagnostic) | `peer_regression` 0.2.0 | `b_signal` | BUY, HOLD, SELL |
| C | `composite` 0.2.0 | `c_signal` | BUY, HOLD, SELL |
| H | `shareholder_yield` 0.1.0 | `h_signal` | BUY, HOLD, SELL |
| J | `ebit_yield_roc` 0.1.0 | `j_signal` (new) | BUY, HOLD, SELL |
| G | `altman_z` 0.1.0 | `g_signal` (new) | **HOLD, SELL only (never BUY)** |
| D | `dividend_sustainability` 0.1.0 | `d_signal` (new) | BUY, HOLD, SELL |
| E | `piotroski` 0.1.0 | `e_signal` (new) | BUY, HOLD, SELL |
| F | `accruals` 0.1.0 | `f_signal` (new) | BUY, HOLD, SELL |

All models can also emit `NO_SIGNAL` and `NOT_ELIGIBLE`. A NULL label (column missing on an old row) is treated as
missing.

### 3.2 Testable variants (`FIT_CAN_EMIT`, from the specs, never from data)
| Model | SELL_EXIT (primary) | BUY_ONLY |
|---|---|---|
| A, B, C, H, J, D, E, F | tested | tested |
| G | tested | **NOT_TESTED** (`NO_LABEL_TYPE`: G cannot emit BUY) |

No model lacks SELL, so SELL_EXIT is tested for all nine. If a model never says SELL for a given company, the pair
is INSUFFICIENT by the activity rule (§6.1), not NOT_TESTED.

### 3.3 Label → position (`position(label, variant) -> int`; 1 = hold the stock, 0 = cash)
| Label on signal date d | SELL_EXIT | BUY_ONLY |
|---|---|---|
| BUY | 1 | 1 |
| HOLD | 1 | 0 |
| SELL | **0** | 0 |
| NO_SIGNAL | 1 | 0 |
| NOT_ELIGIBLE | 1 | 0 |
| NULL / any other value | 1 | 0 |

**Decision (NO_SIGNAL, NOT_ELIGIBLE, missing): the variant's default position, never a carried-over position.**
- Each variant acts only on its trigger label: SELL for SELL_EXIT, BUY for BUY_ONLY.
- A month without that label means "no instruction", so the position falls back to the default: invested for
  SELL_EXIT (the overlay on a holder), cash for BUY_ONLY.
- Carrying the previous position forward would stretch an old SELL or BUY over months where the model did not
  restate it. The model would get credit (or blame) for months it had no opinion on.
- The rule uses only the label dated d, so it cannot look ahead.

## 4. Monthly returns, positions and costs
### 4.1 Observation
One observation per (ticker, model, signal date d) is assembled from:
- `bt_rows` (universe US): the ticker is a point-in-time member with a security row and a price bar on d (the
  harness universe, `backtest.md` §3);
- `bt_returns`, horizon 1: `t0` = first session after d (≤ `ENTRY_MAX_DAYS` = 5 days); `t1` = first session after the
  last calendar day of the next month; `ret = adj(t1)/adj(t0) − 1` (`backtest.md` §4.1).

Rows with `ret` NaN are dropped (flags `NO_ENTRY`, `INCOMPLETE`, `NO_EXIT`). `EARLY_EXIT` rows (delisting or end of
data inside the month; `t1` = last session) are **kept**: the return was realised by `t1`.

Because `t1(d)` is the first session after the next month-end, it equals `t0` of the next signal date whenever that
date has an entry. Consecutive 1-month returns therefore chain exactly:
`Π(1 + ret)` over consecutive months = `adj(t1_last) / adj(t0_first)`. Entry is on the next trading day after the
signal date; nothing dated after d is used for the label.

### 4.2 Strategy return per month (one pair, one window, months in date order)
```
pos_t    = position(label_t, variant)                                  (§3.3)
prev_t   = pos_{t−1}  if the previous observation in this window is dated add_months(d_t, −1)
           (the immediately preceding calendar month-end), else FIT_INITIAL_POSITION (= 1)
switch_t = 1 if pos_t ≠ prev_t else 0
r_hold_t = ret_t
r_strat_t = pos_t · ret_t + (1 − pos_t) · FIT_CASH_RATE − FIT_COST · switch_t
e_t      = r_strat_t − r_hold_t
```
With `FIT_CASH_RATE = 0`:
- invested month: `e_t = −FIT_COST · switch_t`, so 0 or −0.001;
- cash month: `e_t = −ret_t − FIT_COST · switch_t`.

**Sign convention:** `e_t > 0` means the strategy beat holding the stock. For SELL_EXIT, a positive mean excess means
the model's SELLs avoided months when the stock fell. The series is **sparse**: most SELL_EXIT months are exactly 0.

### 4.3 Windows (clean: every return realised inside its window)
`window_of(d, t1, sel=(FIT_SEL_START, FIT_SEL_END), conf=(FIT_CONF_START, FIT_CONF_END))`:
- `"SEL"` if `FIT_SEL_START ≤ d ≤ FIT_SEL_END` and `t1 ≤ FIT_SEL_END`;
- `"CONF"` if `FIT_CONF_START ≤ d ≤ FIT_CONF_END` and `t1 ≤ FIT_CONF_END`;
- else `None` (not used).

**Decision:** each window uses only returns whose exit session is on or before the window end. No selection return
realises in 2020, and no confirmation return realises after 2023-10-31. Since `t1` always falls in month d + 2, the
**last usable signal dates** are:
- selection: **2019-10-31** (exit 2019-12-02). The window holds 70 month-ends, 2014-01-31 … 2019-10-31.
- confirmation: **2023-08-31** (exit 2023-10-02). The window holds 44 month-ends, 2020-01-31 … 2023-08-31.

Signal dates 2019-11-30 and 2019-12-31 (exits 2020-01-02 and 2020-02-03) and 2023-09-30 and 2023-10-31 (exits
2023-11-01 and 2023-12-01) are unused embargo months. The filter is applied to `t1`, not derived from the calendar, so
an `EARLY_EXIT` row counts if its `t1` is inside the window. The windows are fixed constants and do not depend on
`--today` (§7).

### 4.4 Cash return: 0
**Decision:** `FIT_CASH_RATE = 0`.
- **No short rate in the data.** The backtest DB has no T-bill or other short-rate series. `rates_daily` holds only
  10-year yields: `US10Y` from FRED DGS10 and `CA10Y` from BoC, loaded by `runner._rates_fx`. `benchmarks_daily` holds
  equity indices and ETFs only.
- **The 10-year yield is the wrong instrument.** A 10y yield / 12 is a duration-risk return, not cash. It would
  overstate cash in 2014–2021, when 10y was about 2% and bills were about 0–1%, and understate it in 2023.
- **Zero is simple, reproducible and conservative.** It biases SELL_EXIT *against* passing, because time out of the
  stock earns nothing. The bias is small: 3-month bills averaged roughly 1% a year over 2014–2023, so a pair that is
  out 20% of months forgoes about 0.2 pp a year, well below `FIT_MIN_EFFECT`.

A short-rate series would be a later version (backlog).

### 4.5 Transaction costs and the initial state
- **Rate and scope.** `FIT_COST = 10 bp` one-way, charged on every switch (in→out or out→in). It is subtracted from
  that month's strategy return.
- **The holder is the reference.** The hold leg pays no costs: the comparison is an overlay on someone who already
  holds the stock.
- **Initial state.** Before a window's first observation, and after any gap (the previous calendar month-end is not an
  observation of this pair in this window, e.g. a non-member month or a NaN return), the previous position is
  `FIT_INITIAL_POSITION = 1` (invested). So:
  - a SELL_EXIT window that starts in SELL pays one switch;
  - a BUY_ONLY window that starts in cash pays one switch.
- **No forced exit cost** at a window end.
- `n_switches` counts every charged switch, including the initial one.

## 5. Pair metrics (one pair, one variant, one window)
`pair_metrics(obs, variant, start, end, cost=FIT_COST, lock=FIT_LOCK_END) -> PairMetrics`:
1. `assert_no_holdout(obs, lock)` on the **whole** input (§7).
2. Keep rows with `start ≤ date ≤ end`, `t1 ≤ end` and `ret` not NaN.
3. Sort by date and apply §4.2.

Outputs:
- `n_months` = T, the number of kept months.
- `n_active` = the number of months with `pos = 0` (SELL_EXIT: the months out) or `pos = 1` (BUY_ONLY: the months in).
- `n_switches` = Σ switch_t.
- `ann_excess = 12 · mean(e)`: arithmetic, the same quantity the t-test examines; NaN if T = 0.
- `t_stat = mean(e) / (sd(e) / √T)`, with `sd` the sample standard deviation (ddof = 1). NaN if T < 2 or sd = 0.
- `p_value`: **one-sided** (H1: mean e > 0), `scipy.stats.t.sf(t_stat, df = T − 1)`. NaN when t is NaN.

This is identical to `scipy.stats.ttest_1samp(e, 0, alternative="greater")`. Scipy is a project dependency
(`pyproject.toml`). A plain Student t is used, not Newey–West: the monthly returns do not overlap (h = 1).
Clustering of SELL months is a caveat (§14).

`PairMetrics(n_months: int, n_active: int, n_switches: int, ann_excess: float, t_stat: float, p_value: float)`
is a frozen dataclass. For a NOT_TESTED variant, every metric column is NULL.

## 6. Luck guard: select early, confirm later
### 6.1 Activity minimums
`activity_reason(m: PairMetrics, min_months, min_active, min_switches) -> str | None` checks, in this order:
1. `n_months < min_months` → `"FEW_MONTHS"`;
2. `n_active < min_active` → `"FEW_ACTIVE"`;
3. `n_switches < min_switches` → `"FEW_SWITCHES"`;
4. otherwise `None`.

The thresholds are:
- selection `(36, 6, 2)`: about half of the 70 usable months, six trigger months, at least one out-and-back;
- confirmation `(24, 3, 1)`: about half of the 44 months.

In selection, a defined p-value is also required: p NaN after the activity checks → `"ZERO_VAR"`.

### 6.2 Selection test and the BH family
**Family:** per fit run and per variant, every pair `(ticker, model)` with:
- the variant in `FIT_CAN_EMIT[model]`;
- selection `activity_reason(...) is None`;
- selection `p_value` not NaN.

It spans **all nine models and all tickers**: one global family for SELL_EXIT (primary) and a separate one for
BUY_ONLY. INSUFFICIENT and NOT_TESTED pairs are excluded. m = the family size.

`bh_qvalues(p: Sequence[float]) -> numpy.ndarray`. With NaN entries excluded, m = number of non-NaN, `p_(1) ≤ … ≤
p_(m)` (stable sort):
```
q_(i) = min(1, min_{j ≥ i} (m · p_(j) / j))
```
q is NaN where p is NaN; an empty input returns an empty array. A pair **passes selection** iff `q ≤ FIT_Q` (0.10,
inclusive). Since q ≥ p and the test is one-sided, passing implies `sel_ann_excess > 0` (QA property).

### 6.3 Confirmation
For a pair that passed selection:
1. `activity_reason(conf, 24, 3, 1)` not None → INSUFFICIENT `CONF_<reason>`.
2. Otherwise `conf_ann_excess ≥ FIT_MIN_EFFECT` (0.01, inclusive) → **CONFIRMED**.
3. Otherwise → NOT_CONFIRMED `CONF_BELOW_EFFECT`.

"Same sign" is implied: selection passing means a positive effect, and 0.01 > 0. Confirmation p-values are reported
but not used.

### 6.4 Status rules (`classify`, evaluated top to bottom; first match wins)
| # | Condition | status | reason |
|---|---|---|---|
| 1 | variant ∉ `FIT_CAN_EMIT[model]` | NOT_TESTED | `NO_LABEL_TYPE` |
| 2 | selection activity fails (incl. ZERO_VAR) | INSUFFICIENT | `SEL_FEW_MONTHS` / `SEL_FEW_ACTIVE` / `SEL_FEW_SWITCHES` / `SEL_ZERO_VAR` |
| 3 | `q > FIT_Q` | NOT_CONFIRMED | `SEL_NOT_SIGNIFICANT` |
| 4 | confirmation activity fails | INSUFFICIENT | `CONF_FEW_MONTHS` / `CONF_FEW_ACTIVE` / `CONF_FEW_SWITCHES` |
| 5 | `conf_ann_excess ≥ FIT_MIN_EFFECT` | CONFIRMED | `OK` |
| 6 | otherwise | NOT_CONFIRMED | `CONF_BELOW_EFFECT` |

`classify(variant, model, sel: PairMetrics, conf: PairMetrics, q: float, th: FitThresholds) -> tuple[str, str]`.
`FitThresholds` is a frozen dataclass with fields `sel_min_months, sel_min_active, sel_min_switches,
conf_min_months, conf_min_active, conf_min_switches, q, min_effect, can_emit`, defaulting to the §1 constants.

**Status used by the screen = the SELL_EXIT status.** BUY_ONLY statuses are computed, stored and reported for review,
but never drive `<m>_fit`. A ticker with no row → NOT_TESTED.

### 6.5 Rolling windows
**Deferred** (backlog). If added later they are diagnostic only and never change a status.

## 7. Holdout lock (hard guard)
The holdout (36 month-ends 2023-11-30 … 2026-10-31) is reserved for H's pre-registered one-shot test
(`revision_v2_freeze.md` §4.7, §4a; `holdout_h_preregistration.md`).

**Rule.** No per-company metric may use:
- a signal date after `FIT_LOCK_END = 2023-10-31`, or
- a return whose exit session `t1` is after `FIT_LOCK_END`.

`FIT_LOCK_END` is a constant: unlike `holdout_dates`, it does not move with `--today`.

**`assert_no_holdout(df, lock=FIT_LOCK_END, date_col="date", exit_col="t1", ret_col="ret") -> None`** raises
`HoldoutLeak(ValueError)` if any row has:
- `date > lock`; or
- `t1` not null and `t1 > lock`; or
- `ret` not NaN and `t1` null (an unverifiable exit).

An empty frame passes. The error message names the first offending row (sorted by date, ticker).

**Where it is enforced:**
1. **Module import.** `fit.py` asserts
   `FIT_SEL_START < FIT_SEL_END < FIT_CONF_START ≤ FIT_CONF_END ≤ FIT_LOCK_END == date(2023, 10, 31)`.
2. **Loader.** `load_fit_frame(con)` reads only:
   ```sql
   SELECT r.date, r.ticker, <label columns>, x.ret, x.t1, x.flag
   FROM bt_rows r JOIN bt_returns x ON x.date = r.date AND x.ticker = r.ticker AND x.horizon = 1
   WHERE r.universe = 'US' AND r.date <= FIT_LOCK_END AND x.t1 <= FIT_LOCK_END AND x.ret IS NOT NULL
   ```
   It then calls `assert_no_holdout` on the result. So even if a later holdout run adds holdout rows to `bt_rows`,
   they are never loaded.
3. **Every metric.** `pair_metrics` calls `assert_no_holdout` on its whole input, inside the window filter's caller,
   so an unfiltered frame raises.
4. **CLI.** `backtest fit` refuses (exit 2, message `REFUSED: ...`) in these cases:
   - `--holdout` is given (fit never runs on the holdout);
   - `--market TSX`;
   - `in_sample_dates(rebalance_dates(BT_START, today))[-1] < FIT_LOCK_END` (a `--today` before 2026-11-01 would make
     2023-10-31 a holdout date).

   `fit` never writes `bt_runs` and never touches `docs/qa/holdout_v2.md`.

**QA must test:**
- `assert_no_holdout` raises on date 2023-11-30;
- it raises on (2023-09-30, t1 2023-11-01);
- it raises on (2023-08-31, ret 0.01, t1 NULL);
- it passes on t1 = 2023-10-31 exactly;
- it passes on (2023-10-31, ret NaN, t1 NULL);
- `pair_metrics` raises when its input contains a holdout row, even if the window would drop it;
- **leak test:** on a fixture DB, adding `bt_rows`/`bt_returns` rows dated 2023-11-30 … 2026-10-31, and changing
  every price after 2023-10-31, leaves `company_model_fit` identical (except `run_id`);
- the CLI refusals above;
- `holdout.check_holdout_guard` still passes after a fit run (no `bt_runs` holdout row was written).

## 8. Outputs
### 8.1 DuckDB `company_model_fit` (backtest DB; appended per run)
One row per (run_id, ticker, model, variant). Columns, in this order:

| Column | Type | Content |
|---|---|---|
| `run_id` | VARCHAR | fit run id (`uuid4().hex[:12]`) |
| `fit_version` | VARCHAR | `FIT_MODEL_VERSION` |
| `fit_params_hash` | VARCHAR | §1 hash |
| `market` | VARCHAR | `"US"` |
| `ticker` | VARCHAR | normalised ticker (`backtest.md` §1) |
| `model` | VARCHAR | one of `FIT_MODELS` |
| `model_version` | VARCHAR | the model module's `MODEL_VERSION` (equals `bt_label_versions`, §10) |
| `variant` | VARCHAR | `SELL_EXIT` or `BUY_ONLY` |
| `is_primary` | BOOLEAN | `variant == FIT_PRIMARY_VARIANT` |
| `sel_n_months`, `sel_n_active`, `sel_n_switches` | INTEGER | §5, selection |
| `sel_ann_excess`, `sel_t`, `sel_p` | DOUBLE | §5, selection |
| `sel_q` | DOUBLE | BH q within the variant's family; NULL if not in the family |
| `conf_n_months`, `conf_n_active`, `conf_n_switches` | INTEGER | §5, confirmation |
| `conf_ann_excess`, `conf_t`, `conf_p` | DOUBLE | §5, confirmation (computed for every tested pair) |
| `status` | VARCHAR | CONFIRMED / NOT_CONFIRMED / INSUFFICIENT / NOT_TESTED |
| `reason` | VARCHAR | §6.4 code |

Primary key `(run_id, ticker, model, variant)`. Each fitted ticker gets 18 rows (9 models × 2 variants). A ticker is
fitted if it has at least one observation in either window. Selection and confirmation metrics are computed for every
tested pair whatever its status (informational). They are NULL only for NOT_TESTED.

### 8.2 DuckDB `fit_runs`
Columns: `run_id, started_at, market, today, git_sha, fit_version, fit_params_hash, bt_thresholds_hash
(engine.thresholds_hash()), frozen_params_hash (params_sha256(FROZEN_V2)), label_versions (JSON text), n_tickers,
n_family_sell_exit, n_family_buy_only, n_confirmed_sell_exit, n_confirmed_buy_only`.

### 8.3 Files (`--out`, default `reports/backtest/`)
- **`company_model_fit.csv`**: the rows of **this run only**, columns as §8.1, sorted by `(ticker, model, variant)`
  lexicographically (`BUY_ONLY` before `SELL_EXIT`). Written with `float_format="%.10g"`, NULL as an empty field, and
  a first comment line `# <disclaimer>` (§14 summary). It is overwritten by each fit, so it always holds the latest
  run. A copy is kept as `company_model_fit_<run_id>.csv`.
- **`company_model_fit_<run_id>.md`**: summary. It contains:
  - the disclaimer and survivor label;
  - the run id and both hashes;
  - window bounds and usable dates;
  - family sizes;
  - a status count per model × variant;
  - the list of CONFIRMED SELL_EXIT pairs with sel/conf metrics;
  - per model, the share of eligible member-dates with a label in {BUY, HOLD, SELL} in each window (label coverage;
    below 50% is marked `LOW_LABEL_COVERAGE`, information only, §15);
  - a line stating that with BH at q = 0.10 about 10% of selection passes may be false discoveries.

## 9. Screen integration (`screen_signals.csv` / `.parquet`)
`valuation value` gains `--fit PATH` (default `reports/backtest/company_model_fit.csv`). After `run_signals`,
`report.screen.attach_fit(df, fit, live_versions) -> (df, header_lines)` appends these columns **after `flags`**
(existing columns and order unchanged):

`a_fit, b_fit, c_fit, h_fit, j_fit, g_fit, d_fit, e_fit, f_fit, a_highlight, b_highlight, c_highlight, h_highlight,
j_highlight, g_highlight, d_highlight, e_highlight, f_highlight, highlight, highlight_models`

### 9.1 `<m>_fit`
`<m>_fit` = the `status` of the row (ticker, model M, variant SELL_EXIT) in the fit CSV. It is **`NOT_TESTED`** when:
- the CSV is missing or unreadable;
- the ticker has no such row;
- the screen row's market is not US;
- the row's `model_version` differs from the live module's `MODEL_VERSION` (a stale fit; one header line per affected
  model, `# fit: model G fitted on altman_z/0.0.9, live altman_z/0.1.0 -> NOT_TESTED`).

Values: CONFIRMED, NOT_CONFIRMED, INSUFFICIENT, NOT_TESTED.

### 9.2 Highlight columns
- **Current label per model.** A uses `a_model_signal` (the model label with SELL that was fitted; the screen's
  `a_signal` shows SELL as HOLD). Every other model uses `<m>_signal`.
- **`<m>_highlight` (bool)** = `<m>_fit == "CONFIRMED"` and the current label ∈ {BUY, HOLD, SELL}. Under SELL_EXIT
  every one of these labels is an instruction for this company: SELL = step out, BUY/HOLD = stay in. NO_SIGNAL and
  NOT_ELIGIBLE are no instruction.
- **`highlight` (bool)** = any `<m>_highlight` for M ∈ {A, C, H, J, G, D, E, F}. B is diagnostic, so `b_highlight` is
  shown but never sets the row flag.
- **`highlight_models` (str)**: the models counted in `highlight`, in `FIT_MODELS` order, comma-joined; empty if none.

Meaning for the reader: **a model's signal is shown as relevant only where that model is CONFIRMED for the company**.
All other labels remain visible but carry no per-company support.

**Header lines** added to the CSV comment block and the Parquet metadata key `fit` (*clarification (PM):*
`attach_fit` returns them with the leading `# `, i.e. in CSV comment form):
- `# fit: company_model_fit run <run_id> (fit/0.1.0, params <first 16 hex>); CONFIRMED = worked in both in-sample windows (2014-2019, 2020-2023), not a forecast; survivor-biased`;
- or, when there is no CSV, `# fit: no company_model_fit results; all <m>_fit = NOT_TESTED`.

## 10. CLI and backward compatibility
`valuation backtest {build, run, report, benchmarks, fit}`: `fit` is added to the existing `action` choices. All
existing flags keep their meaning and defaults.

| Command | Behaviour |
|---|---|
| `backtest build` / `benchmarks` | unchanged |
| `backtest run [--market] [--today] [--workers] [--holdout --freeze …]` | as today, plus §2.1 (new columns, h = 1 returns, stamp, `bt_label_versions`). Resumable: cached parts with a matching stamp are reused. The first run after this change re-runs every date once (stamp change) |
| `backtest report [...]` | unchanged output (§2.2); new opt-in flag `--extra-models` (refused with `--holdout`) |
| `backtest fit [--market US] [--bt-db] [--today] [--out]` | new: per-company evaluation from stored rows. Never computes labels. Refuses `--holdout`, `--market TSX`, and the `--today` condition in §7 |

**Fit preconditions** (refuse with exit 2 and the hint "run `valuation backtest run --market US` first"):
- `bt_label_versions` exists for US and equals the live `MODEL_VERSION` of all nine models;
- `bt_returns` has `horizon = 1` rows;
- `bt_rows` (US) holds every usable window date (the 70 + 44 of §4.3).

`valuation value --fit PATH` is new (§9). Without fit results the screen still runs; all `<m>_fit` = NOT_TESTED.

## 11. Module layout and API (`src/valuation/backtest/fit.py`, pure functions unless stated)
```python
from valuation.backtest.fit import (
    FIT_MODEL_VERSION, FIT_PARAMS, fit_params_hash, FitThresholds, PairMetrics, HoldoutLeak,
    LABEL_COLUMNS,            # {"A": "a_signal", "B": "b_signal", ..., "F": "f_signal"}
    position,                 # (label: str | None, variant: str) -> int
    window_of,                # (d: date, t1: date, sel=(FIT_SEL_START, FIT_SEL_END), conf=(FIT_CONF_START, FIT_CONF_END)) -> str | None
    assert_no_holdout,        # (df, lock=FIT_LOCK_END, date_col="date", exit_col="t1", ret_col="ret") -> None
    strategy_returns,         # (obs, variant, cost=FIT_COST, cash=FIT_CASH_RATE) -> DataFrame
    pair_metrics,             # (obs, variant, start, end, cost=FIT_COST, lock=FIT_LOCK_END) -> PairMetrics
    activity_reason,          # (m, min_months, min_active, min_switches) -> str | None
    bh_qvalues,               # (p: Sequence[float]) -> numpy.ndarray
    classify,                 # (variant, model, sel, conf, q, th=FitThresholds()) -> tuple[str, str]
    fit_table,                # (frame, versions, sel=..., conf=..., th=FitThresholds(), cost=FIT_COST, lock=FIT_LOCK_END) -> DataFrame
    load_fit_frame,           # (con) -> DataFrame     [DB]
    run_fit,                  # (con, today, out_dir) -> dict   [DB + files]
)
from valuation.report.screen import attach_fit   # (df, fit: DataFrame | None, live_versions: dict) -> (DataFrame, list[str])
```
- `strategy_returns` input: one pair's rows for one window (`date, label, ret`, any order). Output sorted by date with
  columns `date, label, ret, pos, switch, cost, r_strat, r_hold, excess`. The gap rule and the initial position
  follow §4.2/§4.5. *Clarification (PM):* `cost` = `FIT_COST · switch_t`, the cost charged in that month.
- `fit_table` input `frame`: wide `ticker, date, label_A … label_F` (or long `ticker, model, date, label`), `ret, t1`.
  Output: §8.1 columns minus `run_id, fit_version, fit_params_hash, market`. Deterministic: same input → same output.
  Row permutation does not matter.
  *Clarification (PM):* the wide label columns are named `label_<M>` (the pure-function interface); `run_fit` maps the
  stored loader columns (`LABEL_COLUMNS`: `a_signal` … `f_signal`) to `label_<M>`. `fit_table` emits rows for every
  model in `FIT_MODELS` that has a label column (wide) or rows (long) in the frame; a model absent from the frame gets
  no rows. The "18 rows per ticker" of §8.1 applies to the full nine-model frame that `run_fit` passes.
- Frames carry `datetime.date` dates.

## 12. Golden examples (QA; tolerance 1e-9 absolute unless stated exact)
Computed with numpy 2 / scipy 1.18.1. `scipy.stats.ttest_1samp(e, 0, alternative="greater")` agrees.

### G1 fixture (pure functions, parameterised windows)
- **Windows:** sel = (2015-01-31, 2015-10-31), conf = (2015-11-30, 2016-04-30), lock = 2016-04-30.
- **Thresholds:** `FitThresholds(sel_min_months=6, sel_min_active=2, sel_min_switches=2, conf_min_months=3,
  conf_min_active=1, conf_min_switches=1, q=0.10, min_effect=0.01, can_emit={"E": {"SELL_EXIT", "BUY_ONLY"},
  "G": {"SELL_EXIT"}})`.
- **Cost:** 0.001; cash 0.
- **Signal dates** d1 … d14 = month-ends 2015-01-31 … 2016-02-29. Weekday sessions, so `t1` = first weekday after
  the next month-end: 2015-03-02, 04-01, 05-01, 06-01, 07-01, 08-03, 09-01, 10-01, 11-02, 12-01, 2016-01-01, 02-01,
  03-01, 04-01.
- **Window membership:**
  - SEL keeps d1–d8 (d8 t1 2015-10-01 ≤ 10-31);
  - d9 and d10 are unused (t1 2015-11-02 and 2015-12-01 > 2015-10-31, and the dates are before conf start);
  - CONF keeps d11–d14 (d14 t1 2016-04-01 ≤ 04-30).

Returns (`ret`):
| | d1 | d2 | d3 | d4 | d5 | d6 | d7 | d8 | d9 | d10 | d11 | d12 | d13 | d14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAA | .02 | −.06 | .03 | −.08 | −.05 | .04 | .01 | −.07 | .02 | −.01 | −.05 | .03 | −.04 | .02 |
| BBB | .01 | −.07 | .02 | −.09 | .03 | .02 | −.06 | .01 | .01 | .00 | .04 | .02 | .05 | .01 |
| CCC | .03 | .02 | −.01 | **NaN** (NO_ENTRY) | .01 | −.02 | .02 | .01 | .00 | .01 | .02 | −.03 | .01 | .02 |
| DDD | .01 | .02 | .03 | −.02 | .01 | .02 | .01 | .02 | .01 | .01 | .01 | .02 | −.01 | .00 |
| EEE | .01 | −.05 | .02 | −.06 | −.04 | .03 | .02 | −.05 | .01 | .02 | −.03 | .02 | .01 | .03 |

Labels (NS = NO_SIGNAL, NE = NOT_ELIGIBLE, ∅ = NULL):
| pair | d1 | d2 | d3 | d4 | d5 | d6 | d7 | d8 | d9 | d10 | d11 | d12 | d13 | d14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAA·E | HOLD | SELL | BUY | SELL | SELL | NS | HOLD | SELL | SELL | HOLD | SELL | HOLD | SELL | NE |
| BBB·E | BUY | SELL | HOLD | SELL | HOLD | BUY | SELL | HOLD | HOLD | HOLD | SELL | HOLD | SELL | HOLD |
| CCC·E | HOLD | SELL | SELL | HOLD | SELL | BUY | HOLD | SELL | HOLD | HOLD | HOLD | SELL | HOLD | HOLD |
| DDD·E | HOLD | HOLD | HOLD | SELL | HOLD | ∅ | BUY | HOLD | HOLD | HOLD | HOLD | SELL | HOLD | HOLD |
| EEE·E | HOLD | SELL | HOLD | SELL | SELL | HOLD | HOLD | SELL | SELL | SELL | HOLD | HOLD | HOLD | BUY |
| AAA·G | HOLD | SELL | SELL | HOLD | HOLD | HOLD | SELL | HOLD | HOLD | HOLD | SELL | HOLD | HOLD | HOLD |

**G1a `strategy_returns`, AAA·E SELL_EXIT, selection (d1–d8):**
| d | label | ret | pos | switch | r_strat | e |
|---|---|---|---|---|---|---|
| 2015-01-31 | HOLD | .02 | 1 | 0 | .02 | 0 |
| 2015-02-28 | SELL | −.06 | 0 | 1 | −.001 | .059 |
| 2015-03-31 | BUY | .03 | 1 | 1 | .029 | −.001 |
| 2015-04-30 | SELL | −.08 | 0 | 1 | −.001 | .079 |
| 2015-05-31 | SELL | −.05 | 0 | 0 | 0 | .05 |
| 2015-06-30 | NO_SIGNAL | .04 | 1 (default, not carried) | 1 | .039 | −.001 |
| 2015-07-31 | HOLD | .01 | 1 | 0 | .01 | 0 |
| 2015-08-31 | SELL | −.07 | 0 | 1 | −.001 | .069 |

n_months 8, n_active 4, n_switches 5, ann_excess **0.3825** (= 12 · 0.255/8), t **2.534568010738418**,
p **0.019485995463755224** (df 7).

**G1b gap rule, CCC·E SELL_EXIT selection.** d4 has ret NaN, so the series is d1, d2, d3, d5, d6, d7, d8 (T = 7).
- pos = 1, 0, 0, 0, 1, 1, 0.
- At d5 the previous observation (d3) is not the previous month-end (d4), so prev = 1. d5 SELL therefore pays a
  switch: e = −0.01 − 0.001 = −0.011. A carried-over position (d3 = 0) would give −0.01 with no switch, which is
  wrong under this spec.
- e = 0, −.021, .01, −.011, −.001, 0, −.011.
- n_active 4, n_switches 4, ann_excess **−0.05828571428571429**, t **−1.2653489768601378**,
  p **0.8736682012212719**.

**G1c BUY_ONLY, BBB·E selection.**
- pos = 1, 0, 0, 0, 0, 1, 0, 0 (HOLD → cash).
- e = 0, .069, −.02, .09, −.03, −.001, .059, −.01 (d2: −(−.07) − .001; d3: −.02 with no switch, since d2 was already
  cash).
- n_active 2, n_switches 3, ann_excess **0.2355**, t **1.2128551108555325**, p **0.1322612485566378**.

**G1d all pairs** (sel / conf: n_months, n_active, n_switches, ann_excess, t, p):
| pair·variant | selection | conf | in family | q | status | reason |
|---|---|---|---|---|---|---|
| AAA·E SELL_EXIT | 8, 4, 5, 0.3825, 2.534568010738418, 0.019485995463755224 | 4, 2, 4, 0.258, 1.6350085663531526, 0.10028073422494856 | yes | **0.04871498865938806** | **CONFIRMED** | OK |
| BBB·E SELL_EXIT | 8, 3, 6, 0.321, 1.9590086492171759, 0.0454762232812197 | 4, 2, 4, −0.282, −1.7871023864790274, 0.9140561144549066 | yes | **0.0757937054686995** | **NOT_CONFIRMED** | CONF_BELOW_EFFECT |
| CCC·E SELL_EXIT | 7, 4, 4, −0.05828571428571429, −1.2653489768601378, 0.8736682012212719 | 4, 1, 2, 0.084, 0.9540527858909348, 0.2052204690499026 | yes | **0.8736682012212719** | **NOT_CONFIRMED** | SEL_NOT_SIGNIFICANT |
| DDD·E SELL_EXIT | 8, **1**, 2, 0.027, 0.9390442180993814, 0.18948566197027977 | 4, 1, 2, −0.066, −1.0634101379502299, 0.8171977706580784 | no | NULL | **INSUFFICIENT** | SEL_FEW_ACTIVE |
| EEE·E SELL_EXIT | 8, 4, 5, 0.2925, 2.546247426916576, 0.019156456560723584 | 4, **0**, 0, 0.0, NaN, NaN | yes | **0.04871498865938806** | **INSUFFICIENT** | CONF_FEW_ACTIVE |
| AAA·G SELL_EXIT | 8, 3, 4, 0.024, 0.2238067144620166, 0.4146496683124279 | 4, 1, 2, 0.144, 0.9727953421229352, 0.20120247341636044 | yes | **0.5183120853905349** | NOT_CONFIRMED | SEL_NOT_SIGNIFICANT |
| AAA·G BUY_ONLY | NULL | NULL | no | NULL | **NOT_TESTED** | NO_LABEL_TYPE |
| BBB·E BUY_ONLY | 8, 2, 3, 0.2355, 1.2128551108555325, 0.1322612485566378 | 4, 0, 1, −0.363, −3.279867562423663, 0.9767847609795925 | yes (m = 1) | 0.1322612485566378 | NOT_CONFIRMED | SEL_NOT_SIGNIFICANT |
| AAA·E BUY_ONLY | 8, 1, 3, 0.2805, 1.4276710835930253, 0.09822001251629679 | 4, 0, 1, 0.117, 0.48148148148148157, 0.33155645150122326 | no | NULL | INSUFFICIENT | SEL_FEW_ACTIVE |
| CCC·E BUY_ONLY | 7, 1, 4, −0.144, −2.327950780989512, 0.9705972904469337 | 4, 0, 1, −0.063, −0.4371522885184593, 0.654221833935601 | no | NULL | INSUFFICIENT | SEL_FEW_ACTIVE |
| DDD·E BUY_ONLY | 8, 1, 3, −0.1395, −2.1212415119074643, 0.9642049332697483 | 4, 0, 1, −0.063, −0.8047217485013146, 0.760076937296911 | no | NULL | INSUFFICIENT | SEL_FEW_ACTIVE |
| EEE·E BUY_ONLY | 8, 0, 1, 0.1785, 1.0994374318721514, 0.1539726199500789 | 4, 1, 2, −0.006, −0.04729837698404029, 0.5173760044626572 | no | NULL | INSUFFICIENT | SEL_FEW_ACTIVE |

Notes:
- DDD d6 has a NULL label, so pos is 1 in SELL_EXIT.
- AAA·E conf: d11 starts the window in SELL (prev = 1, so a switch): e = .049, −.001, .039, −.001. d14 NOT_ELIGIBLE →
  invested (a switch back). ann = 12 · 0.086 / 4 = 0.258 ≥ 0.01.
- EEE conf has no SELL, so e ≡ 0, ann 0.0, sd 0 → t, p NaN. Confirmation does not need p.

**G1e BH (SELL_EXIT family, m = 5).**
- Sorted p: EEE 0.019156456560723584, AAA·E 0.019485995463755224, BBB 0.0454762232812197, AAA·G 0.4146496683124279,
  CCC 0.8736682012212719.
- m·p/rank: 0.09578228280361792, 0.04871498865938806, 0.07579370546869950, 0.5183120853905349, 0.8736682012212719.
- Running minimum from the end gives q (AAA·E and EEE share 0.04871498865938806).

**G2 `bh_qvalues` alone.**
- [0.01, 0.04, 0.03, NaN, 0.5] → [0.04, 0.0533333333333333, 0.0533333333333333, NaN, 0.5];
- [0.02, 0.02, 0.2] → [0.03, 0.03, 0.2];
- [] → [];
- [0.3] → [0.3].

**G3 `position`.** SELL_EXIT: BUY 1, HOLD 1, SELL 0, NO_SIGNAL 1, NOT_ELIGIBLE 1, None 1. BUY_ONLY: BUY 1, all
others 0.

**G4 real window boundaries** (constants of §1; rebalance calendar with today 2026-11-10 → 154 dates, in-sample 118
… 2023-10-31; any weekday-session price series):
- Selection keeps 70 dates, 2014-01-31 … 2019-10-31.
  - First observation: t0 2014-02-03, t1 2014-03-03.
  - Last observation: 2019-10-31, t0 2019-11-01, t1 2019-12-02.
- Confirmation keeps 44 dates, 2020-01-31 … 2023-08-31.
  - First observation: t0 2020-02-03, t1 2020-03-02.
  - Last observation: 2023-08-31, t1 2023-10-02.
- Excluded:
  - 2019-11-30 (t1 2020-01-01 with weekday sessions; 2020-01-02 on the NYSE calendar);
  - 2019-12-31 (t1 2020-02-03);
  - 2023-09-30 (t1 2023-11-01 > lock);
  - 2023-10-31 (t1 2023-12-01).
- `window_of(2019-10-31, 2019-12-02)` = `"SEL"`; `window_of(2019-11-30, 2020-01-02)` = None;
  `window_of(2023-08-31, 2023-10-02)` = `"CONF"`; `window_of(2023-09-30, 2023-11-01)` = None.
- An `EARLY_EXIT` row dated 2019-11-30 with t1 2019-12-13 → `"SEL"` (t1 ≤ 2019-12-31).

**G5 `assert_no_holdout`** (lock 2023-10-31; rows date / ret / t1):
- (2023-08-31, .01, 2023-10-02) passes;
- (2023-09-15, .01, 2023-10-31) passes (boundary);
- (2023-10-31, NaN, NULL) passes;
- (2023-09-30, .01, 2023-11-01) raises;
- (2023-11-30, NaN, NULL) raises;
- (2023-08-31, .01, NULL) raises;
- empty passes.

**G6 screen merge** (live versions current).

Fit CSV, SELL_EXIT rows:
- XOM: A CONFIRMED (`residual_income/0.2.0`), C NOT_CONFIRMED, G CONFIRMED with `model_version` `altman_z/0.0.9`;
- MSFT: B CONFIRMED, E CONFIRMED.

Screen rows:
- XOM US: a_model_signal SELL (a_signal HOLD), c_signal HOLD, g_signal HOLD.
- MSFT US: b_signal BUY, e_signal NO_SIGNAL.
- RY TSX.

Expected:
| | fit columns | highlight columns | header |
|---|---|---|---|
| XOM | a_fit CONFIRMED, c_fit NOT_CONFIRMED, g_fit **NOT_TESTED** (version mismatch), others NOT_TESTED | a_highlight True, others False; highlight True, highlight_models "A" | one mismatch header line for G |
| MSFT | b_fit CONFIRMED, e_fit CONFIRMED | b_highlight True, e_highlight False (NO_SIGNAL); **highlight False** (B never sets it), highlight_models "" | |
| RY | every `<m>_fit` NOT_TESTED | all highlights False | |

With `fit=None`: every `<m>_fit` NOT_TESTED, all highlights False, and the header line "no company_model_fit results".

**G7 chaining.** Any positive adj_close series on every weekday from 2015-02-02 to 2015-05-29, with the h = 1 rows
of d = 2015-01-31, 02-28, 03-31 (t0(2015-01-31) = 2015-02-02):
- t1(2015-01-31) = t0(2015-02-28) = 2015-03-02;
- t1(2015-02-28) = t0(2015-03-31) = 2015-04-01;
- Π(1 + ret) = adj(2015-05-01)/adj(2015-02-02).

## 13. Property expectations (QA)
- **No look-ahead:**
  - changing any price dated after `FIT_LOCK_END`, or any `bt_rows`/`bt_returns` row with date > `FIT_LOCK_END`,
    changes no `company_model_fit` value;
  - changing a label dated in window W changes only that pair's metrics for W, and the family q-values of the
    variant.
- **Determinism:** two fits on the same DB give identical rows except `run_id`.
- **Consistency:**
  - `status == CONFIRMED` ⇒ `sel_q ≤ 0.10`, `sel_ann_excess > 0`, `conf_ann_excess ≥ 0.01`, and both windows meet
    their activity minimums;
  - `sel_q` is not NULL ⇔ the pair is in the family;
  - NOT_TESTED ⇔ (model G and BUY_ONLY).
- **Hold identity:** a pair whose labels in a window are never SELL has SELL_EXIT e ≡ 0, ann 0, n_active 0 →
  INSUFFICIENT. A BUY_ONLY pair labelled BUY every month likewise has e ≡ 0, n_switches 0.
- **Cost monotonicity:** raising the cost never raises `ann_excess` (QA uses the pure function with `cost` as a
  parameter; the constant stays 10 bp).
- **Reproduction:** whole-market `report` outputs are unchanged (§2.2).

## 14. Per-company caveats (printed in the summary; the CSV disclaimer is a one-line summary)
- **Survivorship.** 157 of 253 removed S&P 500 members lack Yahoo prices (13.8% of member-dates;
  `docs/qa/r2-insample.md` §5). Those names, often the failures where a SELL would have helped, cannot be fitted.
  Fitted companies are survivors-with-prices, so every result is an upper bound for the universe as it really was.
- **Low power per company.** A company gives at most 70 + 44 monthly observations, and few of them are label
  changes. A SELL_EXIT series is mostly zeros. The t-test treats months as independent, but SELL streaks cluster
  (often in drawdowns), so the p-values are optimistic. The activity minimums limit, but do not remove, this.
- **Absolute, not relative.** SELL_EXIT vs hold rewards a SELL before *any* fall of the stock, including market-wide
  falls (2018 Q4, 2020 Q1, 2022). A model can look "fit" for a high-beta name through market timing it did not
  intend. A market-relative variant is backlog.
- **Regime dependence.** The two windows are 2014–2019 (mostly a bull market) and 2020–2023 (COVID crash, rate
  shock). Surviving both is evidence of persistence across two regimes, not across all.
- **CONFIRMED = "worked in both windows", not "will work".** The holdout is reserved for H's pre-registered test and
  cannot confirm per-company results. A forward check needs new month-ends from 2026-11-30.
- **Multiple testing remains.** BH controls the expected share of false discoveries among selection passes at 10%, but
  only within this family. Several thousand pairs mean a few hundred passes could include tens of false ones.
  Confirmation reduces this but does not eliminate it. Choosing among companies after the fact (e.g. "the 10 best")
  is not supported by this test.
- **Unvalidated models.** J, G, D, E, F were never backtested before; A, C, H failed the whole-market bar; B is
  diagnostic. A per-company fit does not change any model's validation status or the Phase 10 gate.
- **Screening, not advice.** A highlight marks where a model's labels were historically useful for that stock. It is
  not a recommendation to trade.

## 15. Data anomalies and code observations (reported, not fixed here)
1. **No short-rate series:** `rates_daily` has only US10Y and CA10Y, so cash is 0 (§4.4).
2. **`bt_returns` has no h = 1 rows,** and `sector_excess` takes horizons from the data, so adding h = 1 without the
   §2.2 filter would silently change whole-market outputs.
3. **J–F labels are computed on every backtest date but discarded** (`engine.run_one` keeps only A/B/C/H). The part
   stamp (`run_model_version()`) covers only the four frozen versions, so a J–F change would not invalidate cached
   parts. §2.1 fixes both for this mode.
4. **Inputs for J–F may be missing from the backtest DB.**
   - EDGAR companyfacts are cached trimmed to `TRIM_CONCEPTS` (`sec_edgar.trim_companyfacts`).
   - A backtest DB built, or a cache filled, before data batches 2–3 (EBIT, CFO, revenue, GP, current assets/
     liabilities, retained earnings) lacks those items. J–F would then be mostly `NO_SIGNAL`, and the pairs
     INSUFFICIENT.
   - QA must report label coverage per model (§8.3) before reading any J–F result. A rebuild (`backtest build`,
     network) may be needed; it is not run here.
5. **The holdout split moves with `--today`** (`holdout_dates` = last 36 month-ends). The fit therefore uses the fixed
   `FIT_LOCK_END` and refuses a `--today` that would put 2023-10-31 in the holdout.
6. **In-sample 12m whole-market returns already realise in 2023-11 … 2024-10** (the existing `backtest.md` definition,
   r2-insample header). The per-company mode is stricter: no exit after 2023-10-31.
7. **A's label naming:** in `bt_rows`, `a_signal` holds the model label (`a_model_signal`); in `screen_signals`,
   `a_signal` is the screen label (SELL shown as HOLD). §3.1 and §9 name the right column for each.
8. **J–F specs** state "Backtest: deferred … in no backtest run". This spec adds them to **in-sample label generation
   and per-company fit only**. Their whole-market hypotheses remain un-pre-registered, and `--extra-models` metrics
   are in-sample diagnostics.
9. `data/` is not in git and was not present in this checkout, so DB contents (e.g. `rates_daily` date coverage)
   were checked from code only.

## 16. Decisions (summary)
- **D1 primary metric (fixed now):**
  - variant SELL_EXIT;
  - `ann_excess = 12 · mean(e_t)`, `e_t = r_strat_t − r_hold_t`;
  - one-sided Student t on monthly `e_t`, df = T − 1;
  - selection BH q ≤ 0.10 over one global family per variant;
  - confirmation `ann_excess ≥ 0.01`.
- **D2 cash 0; costs 10 bp per switch; initial and post-gap position invested.**
- **D3 NO_SIGNAL / NOT_ELIGIBLE / missing → the variant's default position** (SELL_EXIT in, BUY_ONLY out), never
  carried.
- **D4 clean windows:** exit ≤ window end; usable 70 + 44 dates; 4 embargo month-ends.
- **D5 INSUFFICIENT** covers both selection inactivity (outside the family) and confirmation inactivity (in the family,
  cannot be confirmed). NOT_TESTED only for a variant the model cannot emit, or a missing or stale fit in the screen.
- **D6 the screen uses SELL_EXIT status**; `highlight` = a confirmed model with a current BUY/HOLD/SELL, B excluded
  from the row flag; a stale `model_version` → NOT_TESTED.
- **D7 fit never touches `bt_runs` or holdout dates.** J–F are never computed on holdout dates (including
  `--extra-models`).

## Changelog
- **0.1.0** (2026-11-13): initial version. Golden values verified with a throwaway numpy/scipy script.
  Clarifications (PM resolutions of QA SPEC-QUESTIONs, no number or threshold changed, no version bump):
  `fit_params_hash()` is a function; `strategy_returns.cost` = `FIT_COST · switch`; G1a parenthetical typo 0.254 → 0.255;
  `fit_table` rows only for models present in the frame; wide label columns `label_<M>`; `attach_fit` header lines carry
  `# `; `--extra-models` rows go to `bt_metrics`/`bt_signals`.
