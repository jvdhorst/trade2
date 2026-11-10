# Model spec: Backtest harness (`backtest`, v0.1.0) — Phase 8

Owner: AN · Tests: QA (`tests/unit/test_backtest_membership_spec.py`, `test_backtest_metrics_spec.py`,
`test_backtest_engine_spec.py`) · Code: `src/valuation/backtest/` (`membership.py`, `metrics.py`, `engine.py`) ·
Source: proposal `docs/models/proposals/buy_hold_sell.md` §0, §2 (QA), §6; PM plan Phase 8 (task P8-0, for P8-1..P8-3)

## Purpose
The harness checks whether Model A (`residual_income.md`) and Model B (`peer_regression.md`) sort stocks usefully,
**point in time**: on each month-end since 2014 it rebuilds the S&P 500 membership, runs the live signal code on only the
data that was public then, and measures the sector-neutral forward returns of the BUY and SELL buckets. It also answers
the Phase 7 open questions (`docs/qa/phase7-validation.md` §6d–g) as diagnostics. It **never changes a model
parameter**: thresholds are the Phase 7 approved constants, frozen.

> **Screening signals only**, not investment advice. A pass means the signal sorted past returns better than chance
> in this sample; it is not a forecast. Every US result is labelled **"survivor-biased upper bound"** while any member
> on any date lacks prices (§Survivorship), and every TSX result **"current constituents, survivor-biased"**.

## Constants
`MEMBERSHIP_START = date(2013, 1, 1)`, `REBALANCE_START = date(2014, 1, 1)`, `HORIZONS = (3, 6, 12)` (months),
`ENTRY_MAX_DAYS = 5`, `MIN_SECTOR = 5`, `MIN_IC_NAMES = 20`, `HOLDOUT_N = 36`, `PASS_YEAR_SHARE = 0.60`,
`PASS_IC = 0.03`, `PASS_T = 2.0`, `PASS_HORIZON = 12`, `PIT_EXTRA_LAG_DAYS = 30`, `PRICE_LOOKBACK_DAYS = 10`,
`BASELINE_BUY_PCT = 0.20`, `BASELINE_SELL_PCT = 0.80`, `BASELINE_MIN = 5`, `Q1_PB_SPLIT = 2.9`, `Q4_MIN_SUBIND = 12`.
`BOOK_SECTORS` = Model A's set (Financials, Utilities, Real Estate, Energy, Materials). `MODEL_VERSION = "backtest/0.1.0"`.
Live model constants (`residual_income`, `peer_regression`, `eligibility`/`liquidity.yaml`) are imported, never copied.

## 1. Point-in-time S&P 500 membership (`membership.py`, P8-1)
**Ticker normalisation** `norm(s)`: remove every `[...]` footnote (regex `\[[^\]]*\]`), strip whitespace, upper-case,
`.` → `-`. Empty result, NaN or `"—"` → `None`. `norm("brk.b[5] ") = "BRK-B"`.

`parse_current(html) -> list[str]`: the first table (id `constituents`), column `Symbol`, normalised, page order.
`parse_changes(html) -> list[Change]`: the table with id `changes` ("Selected changes to the list of S&P 500
components"; two header rows: Date | Added Ticker, Security | Removed Ticker, Security | Reason), read by position
(columns 0–5) with rowspan/colspan cells repeated as `pandas.read_html` does. `Change(date, added_ticker, added_name,
removed_ticker, removed_name)` (frozen dataclass; `Reason` is not kept). Date format `"%B %d, %Y"` ("July 9, 2024");
a row whose date does not parse is skipped (logged). Tickers normalised; names stripped of footnotes, `None` if empty.
Page order is kept.

`build_intervals(current, changes, start=MEMBERSHIP_START) -> list[Interval]`, `Interval(ticker, start, end)`
(`end` inclusive, `None` = still a member). A change dated D is effective at the **open of D**. Algorithm (backward walk):
1. `open = {t: None for t in current}` (ticker → its interval's end, still unknown start).
2. Group changes by date; walk dates from newest to oldest; stop at dates `< start` (ignored; a change dated exactly
   `start` is applied). For date D with added set A and removed set R (all rows of that date):
   - `x ∈ A \ R`: if x is open → emit `Interval(x, D, open.pop(x))`; else skip (inconsistent row, logged).
   - `y ∈ R \ A`: if y is not open → `open[y] = D − 1 day`; else skip (logged).
   - `z ∈ A ∩ R` (same-day add and remove of the same ticker): if z is open → nothing (no gap); else `open[z] = D − 1 day`.
3. After the walk: every still-open ticker gets `Interval(t, start, end)`.
4. Merge intervals of one ticker that touch (`next.start == prev.end + 1 day`); sort by `(ticker, start)`.

`members_on(intervals, d) -> set[str]` = `{i.ticker : i.start <= d and (i.end is None or d <= i.end)}`.
Storage: DuckDB `index_membership(index_name, ticker, name, start_date, end_date, source, fetched_on)`, one row per
interval, `index_name = 'SP500'`, `source = 'wikipedia:List_of_S&P_500_companies'`, `end_date` NULL when open, `name`
from the latest change row or current table naming the ticker. The page is fetched (cached to `data/raw`) by the
existing Wikipedia path.

## 2. Rebalance calendar and holdout lock (`engine.py`)
`rebalance_dates(start=REBALANCE_START, today) -> list[date]`: every **calendar** month-end m (last calendar day of the
month) with `m >= start` and `m < today` (strictly before), ascending. `holdout_dates(dates, n=HOLDOUT_N)` = the last
`min(n, len(dates))` dates; in-sample = the others. With today 2026-11-10: 154 dates 2014-01-31 … 2026-10-31; in-sample
118 (… 2023-10-31), holdout 36 (2023-11-30 … 2026-10-31).

Every metric function takes `allowed_dates` (`None` = all dates in its input). The runner computes **in-sample only**
by default. Holdout metrics are produced only with the CLI flag `--holdout`. Every run writes a row to `bt_runs` (§7)
with `kind = 'in_sample'` or `'holdout'` and `thresholds_hash = sha256(json.dumps(obj, sort_keys=True,
separators=(",", ":")).encode()).hexdigest()`, `obj = {"residual_income": {...}, "peer_regression": {...},
"liquidity": <liquidity.yaml dict>}` where the two model dicts hold every constant listed in those specs' Constants
sections (name → value; sets as sorted lists). The hash makes it visible if thresholds changed between runs.
Returns realised after 2023-10-31 do enter in-sample 12-month returns (the lock is on signal dates).

## 3. Point-in-time signals per date d
- **Universe** = `members_on(intervals, d)` ∩ tickers with data (a `securities` row and ≥ 1 price bar). E1 of
  eligibility = member on d. Eligibility → Model A → Model B run **exactly as the live `run_signals`** (same code path,
  same thresholds), with prices dated ≤ d, fundamentals with effective filed_date ≤ d, CSM rows with filed_date ≤ d,
  and `rates_daily` latest on/before d. All names of one date are fitted together (Model B).
- **Estimated filing dates:** a fact with `filed_estimated = true` (Yahoo / TSX) is usable only when
  `filed_date + PIT_EXTRA_LAG_DAYS ≤ d`. EDGAR facts use their `filed` date as is.
- **Splits** (table `splits(ticker, date, ratio, source)`, ratio = new shares per old share, 4-for-1 = 4.0, effective
  at the open of `date`). Yahoo split-adjusts `close`, `volume` and dividend amounts to the download date, so the
  backtest DB (`data/backtest.duckdb`) stores them **as traded**: at ingest, with `F(t)` = product of ratios of splits
  dated after t, `close = close_yahoo × F(t)`, `volume = volume_yahoo / F(t)`, `amount = amount_yahoo × F(ex_date)`;
  `adj_close` is kept as delivered. On date d: `shares_d = shares_filing × Π ratio (split date in (filed_date, d])`;
  a dividend on the d basis = `amount / Π ratio (split date in (ex_date, d])` (**divided**: more shares, less per share).
  Example: split 4.0 on 2020-08-31; filing filed 2020-07-30 with 1.0e9 shares; Yahoo close 125 on 2020-08-28 (as traded
  500); Yahoo dividend 0.205 ex 2020-08-07 (as traded 0.82). d = 2020-08-30 → shares 1.0e9, price 500, dividend 0.82,
  market cap 5.0e11; d = 2020-08-31 → shares 4.0e9; d = 2020-09-30 → shares 4.0e9, dividend 0.82/4 = 0.205.
- **Total returns** use Yahoo `adj_close` (split- and dividend-adjusted, basis-free for ratios), local currency.
- **Sector:** current members (in `universe/sp500.csv`): its GICS sector and sub-industry, applied to all history.
  Other names: GICS sector from the SEC SIC code (EDGAR submissions API) via the table in §6, flag `SECTOR_FROM_SIC`,
  no sub-industry. Neither → sector `None` → Model A `NO_SECTOR`, excluded from medians.
- **Continuous outputs** (`score`, higher = more attractive): Model A `mos`, only where A's label ∈ {BUY, HOLD, SELL};
  Model B `−residual`, only where a residual is stored **and** B's label ∈ {BUY, HOLD, SELL}; baseline `−ln(P/B)`.
  Elsewhere `score = NaN`.

## 4. Metrics (`metrics.py`, pure functions, pandas in/out)
Inputs use long frames. `prices`: `ticker, date, adj_close` (rows with `adj_close` null or ≤ 0 are not sessions).
`panel`: one row per (date, ticker) in the universe: `date, ticker, sector, eligible (bool), label, score`.

**4.1 `forward_returns(prices, dates, horizons=HORIZONS)`** → `date, ticker, horizon, t0, t1, ret, flag`, one row per
(date, ticker in prices, horizon). `last_panel` = max date in all of `prices`. For ticker sessions s:
```
t0      = first session with date > d;  none, or t0 − d > ENTRY_MAX_DAYS days   → ret NaN, flag NO_ENTRY
target  = last calendar day of the month h months after d's month  (2014-01-31, h=3 → 2014-04-30)
target >= last_panel                                                  → ret NaN, flag INCOMPLETE
t1      = first session with date > target                            → ret = adj(t1)/adj(t0) − 1, flag None
no such session and last session > t0   → t1 = last session, ret as above, flag EARLY_EXIT
no such session and last session == t0  → ret NaN, flag NO_EXIT
```
Checks run in this order (NO_ENTRY, INCOMPLETE, then t1).

**4.2 `sector_excess(fwd, panel, min_sector=MIN_SECTOR)`** → panel left-joined with fwd per horizon, plus
`n_sector, sector_median, excess`. Per (date, horizon, sector): `V` = names with `eligible` True, sector not None and
`ret` not NaN; `n_sector = |V|`; `sector_median = numpy.median(ret over V)` if `n_sector ≥ min_sector` else NaN;
`excess = ret − sector_median` for names in V, else NaN. Medians use **all** eligible members, not only signalled ones,
and are the same for every model.

**4.3 `spread_by_date(obs, allowed_dates=None)`** → `date, horizon, n_buy, n_sell, buy_excess, sell_excess, spread`
(one row per allowed date × horizon): BUY/SELL = names with that label and non-NaN excess; means equal-weighted;
`spread = buy_excess − sell_excess` only if `n_buy ≥ 1` and `n_sell ≥ 1`, else NaN.
**`yearly_spread(by_date)`** → `year, horizon, n_dates, spread`: mean of non-NaN spreads of dates in calendar year Y;
years with no defined spread are omitted.

**4.4 `hit_rates(obs, allowed_dates=None)`** → `horizon, n_buy, buy_hit_rate, n_sell, sell_hit_rate`, pooled over all
(date, ticker): `buy_hit_rate` = share of BUYs with excess > 0; `sell_hit_rate` = share of SELLs with excess < 0
(excess exactly 0 is a miss for both); NaN if n = 0.

**4.5 `rank_ic(obs, allowed_dates=None, min_names=MIN_IC_NAMES)`** → `date, horizon, n, ic`: over names with
`eligible` True, score and **raw** `ret` both non-NaN; `ic` = Pearson correlation of
`scipy.stats.rankdata(score, method="average")` and `rankdata(ret, …)` (= `scipy.stats.spearmanr`); NaN if
`n < min_names` or either rank vector is constant. Mean IC = arithmetic mean of non-NaN ic.

**4.6 `newey_west_t(x, lag)`**: x = the IC series in date order with NaN dates **dropped** (not zero-filled); T = len,
`m = mean`, `γ_l = (1/T) Σ_{t=l+1..T} (x_t − m)(x_{t−l} − m)`, `var = γ_0 + 2 Σ_{l=1..L} (1 − l/(L+1)) γ_l`,
`t = m / sqrt(var/T)`. `L = h − 1` (h = 3 → 2, 6 → 5, 12 → 11). `T ≤ L + 1` or `var ≤ 0` → NaN.

**4.7 `turnover(labels, allowed_dates=None)`** (`date, ticker, label`) → per consecutive pair of the sorted distinct
allowed dates (d_{k−1}, d_k), row at d_k: `label_turnover` = share of tickers with a label in {BUY, HOLD, SELL} on both
dates whose label differs (NaN if none); `buy_turnover = |BUY_k \ BUY_{k−1}| / |BUY_k|` (NaN if BUY_k empty);
`sell_turnover` likewise. Reported: the means of the non-NaN values.

**4.8 `pass_bar(yearly_spreads_12, ic_mean, ic_t, holdout_ic_mean=None, holdout_spread=None)`** → `PassResult(result,
failed_legs, incomplete_legs)`. All in-sample, h = 12. `yearly_spreads_12` = sequence of yearly spreads (NaN allowed).
| Leg | Passes when | Incomplete when |
|---|---|---|
| `SPREAD_YEARS` | share of non-NaN years with spread > 0 is ≥ 0.60 (0 is not > 0) | no non-NaN year |
| `IC` | `ic_mean > 0.03` and `ic_t > 2` (NaN t fails) | `ic_mean` NaN |
| `HOLDOUT` | `sign(holdout_ic_mean) == sign(ic_mean)` and `sign(holdout_spread) == sign(mean of non-NaN yearly spreads)` (`numpy.sign`, so a 0 matches only 0) | either holdout value None/NaN, or `ic_mean` NaN, or no non-NaN yearly spread |

`result` = `FAIL` if any computable leg fails; else `INCOMPLETE` if any leg is incomplete; else `PASS`. Legs are listed
in table order. `holdout_spread` = mean of non-NaN 12-month `spread_d` over holdout dates; `holdout_ic_mean` = mean
12-month IC over holdout dates. Without `--holdout` the HOLDOUT leg is always incomplete, so the best in-sample result is
`INCOMPLETE`. The bar applies to Models A and B only (US).

**4.9 `low_pb_baseline(panel_pb, min_sector=BASELINE_MIN)`** (`date, ticker, sector, eligible, price, bvps`) →
`date, ticker, sector, pb, pct, label, score`. `bvps` = unadjusted `book_value` bvps (common equity / shares, no CSM).
Per date and BOOK sector: G = names with eligible True, bvps > 0, price > 0; `pb = price/bvps`; if `|G| ≥ min_sector`:
`rank` = ascending pb, average ranks for ties (1 = cheapest), `pct = (rank − 1)/(|G| − 1)`, label `BUY` if
`pct ≤ 0.20`, `SELL` if `pct ≥ 0.80`, else `HOLD`; `score = −ln(pb)`. Otherwise label `NOT_ELIGIBLE` (eligible not
True) or `NO_SIGNAL` (other sector, bvps ≤ 0/None, price ≤ 0, or |G| < min_sector), pct and score NaN. It is a
reference: it is compared with **A-book** (Model A labels and scores restricted to BOOK sectors), proposal §2's test of
whether the ROE adjustment adds anything. Neither enters the pass bar.

## 5. Survivorship and coverage
Per (ticker, interval): **valid** if the ticker has ≥ 1 price bar with date in `[start, end or today]`; no bar inside
it (incl. a first bar only after `end`, i.e. the symbol now belongs to another company) → invalid. A member-date
`(d, t)` is `missing_price` if its interval is invalid or t has no bar with `d − PRICE_LOOKBACK_DAYS < date ≤ d`.
Such names cannot be eligible (no bars) and enter no metric. Reported per run (`bt_coverage`): per date
`n_members, n_with_price, missing_price`; totals: count and share of member-dates missing prices, and the number of
distinct tickers with an invalid interval that has an `end` ("removed names lacking prices"). Any `missing_price > 0`
→ every result carries the label **"survivor-biased upper bound"**. Names without fundamentals (e.g. no CIK) are
eligible-checked as live (no balance sheet → `NOT_ELIGIBLE`) and counted as `missing_fundamentals`.

## 6. SIC → GICS sector table (removed names; first matching row wins; SIC as integer)
| SIC | Sector | SIC | Sector |
|---|---|---|---|
| 1200–1399, 2900–2999 | Energy | 1000–1199, 1400–1499 | Materials |
| 2830–2836, 3841–3851, 5120–5129, 8000–8099 | Health Care | 2840–2844, 2000–2199, 5400–5499, 5140–5149, 5912 | Consumer Staples |
| 2700–2799, 4800–4899, 7810–7849 | Communication Services | 4900–4949 | Utilities |
| 6798, 6500–6599 | Real Estate | 6000–6799 | Financials |
| 3570–3579, 3600–3629, 3640–3699, 3800–3840, 3852–3899, 7370–7379 | Information Technology | 3711–3716, 3751, 3630–3639 | Consumer Discretionary |
| 2400–2499, 2600–2699, 2800–2899, 3000–3099, 3200–3399 | Materials | 2200–2399, 2500–2599, 3100–3199, 3900–3999, 5200–5999, 7000–7099, 7900–7999 | Consumer Discretionary |
| 1500–1799, 3400–3569, 3580–3599, 3700–3799, 4000–4799, 4950–4999, 5000–5199, 7380–7389, 8700–8799, 7000–8999 | Industrials | anything else, missing | `None` (NO_SECTOR) |

Order of evaluation: the rows top to bottom, left cell before right cell. Checks: 1311 → Energy; 2834 → Health Care;
2911 → Energy; 3674 → IT; 3714 → Consumer Discretionary; 4911 → Utilities; 6022 → Financials; 6798 → Real Estate;
5411 → Consumer Staples; 5331 → Consumer Discretionary; 7372 → IT; 4512 → Industrials; 9995 → None.
Known limitations: current GICS is applied to all history (2018 Communication Services and 2023 payments → Financials
reclassifications are not replayed); SIC is coarse.

## 7. Runner, outputs and diagnostics
CLI: `valuation backtest build|run|report [--market US|TSX] [--holdout] [--today YYYY-MM-DD]` (default US, in-sample). Per date it runs
§3, then §4 per model (`A`, `B`, `A_BOOK`, `BASELINE_PB`) and horizon. Re-running on the same DB gives identical tables
(sorted outputs, no randomness). Tables:
| Table | Columns |
|---|---|
| `index_membership` | §1 |
| `splits` | `ticker, date, ratio, source` |
| `bt_runs` | `run_id, started_at, kind, market, thresholds_hash, git_sha, today, n_dates, model_version` |
| `bt_signals` | `run_id, date, ticker, model, label, score, sector, eligible, pb, flags` |
| `bt_returns` | `run_id, date, ticker, horizon, t0, t1, ret, flag` |
| `bt_metrics` | `run_id, model, scope ('in_sample'/'holdout'), horizon, metric, key, value` (metric ∈ yearly_spread [key = year], mean_spread, mean_ic, nw_t, n_ic_dates, buy_hit_rate, sell_hit_rate, n_buy, n_sell, label_turnover, buy_turnover, sell_turnover, pass_result [key = legs]) |
| `bt_diagnostics` | `run_id, question, model, bucket, horizon, n, mean_excess, hit_rate, mean_ic, n_ic_dates, mean_spread, n_spread_dates` |
| `bt_coverage` | `run_id, date, n_members, n_with_price, missing_price, missing_fundamentals` |

**Diagnostics** (in-sample, per horizon; `mean_excess` = pooled mean of excess over observations; `hit_rate` as §4.4
for the bucket's side; parameters never change):
- **Q1** (§6g, SELL bias): Model A SELLs, buckets `PB_GT_2_9` (A's `pb > 2.9`) and `PB_LE_2_9`.
- **Q2** (§6d, one-off ROE): Model A BUYs with flag `ROE_CLIPPED` vs without (`CLIPPED` / `NOT_CLIPPED`).
- **Q3** (§6f): Model B BUYs with flag `NEG_ROE_UNFITTED` vs without.
- **Q4** (§6e, Financials peers): variant `B_FIN_SPLIT`: per date, Financials names are grouped by GICS sub-industry
  where that sub-industry has ≥ 12 names in Model B's fit set F; all other Financials names (incl. SIC-mapped, without
  sub-industry) pool as `Financials-other`; each group is fitted and mapped with the unchanged `peer_regression`
  functions (a group with n_fit < 12 gets `FEW_NAMES` → NO_SIGNAL). Compared with Model B on the Financials subset:
  mean IC (−residual vs ret, min_names 20), n_ic_dates, mean spread (excess vs the ordinary GICS Financials median),
  n_spread_dates, hit rates. Buckets `B` and `B_FIN_SPLIT`.

QA writes `docs/qa/backtest_<model>.md` per model (P8-3) from `bt_metrics`, `bt_diagnostics` and `bt_coverage`.
**TSX (supplementary):** membership = today's TSX universe file on every date, only Yahoo fundamentals (short history,
+30-day lag), label "current constituents, survivor-biased"; same metrics, never in the pass bar.

## 8. Golden examples (QA; tolerance 1e-9 absolute unless an exact value is given)
**M1 membership.** `current = ["AAA", "BBB", "CCC", "RE"]`, start 2013-01-01, changes (date, added, removed):
2021-07-01 (RE, DDD); 2019-06-03 (BBB, BBB); 2016-05-02 (DDD, RE); 2012-12-03 (CCC, ZZZ). Expected intervals:
`AAA [2013-01-01, None]`, `BBB [2013-01-01, None]` (same-day add+remove: one interval, no gap), `CCC [2013-01-01,
None]` (the 2012 change is before start: ignored, ZZZ absent), `DDD [2016-05-02, 2021-06-30]`, `RE [2013-01-01,
2016-05-01]`, `RE [2021-07-01, None]` (re-added). `members_on`: 2016-05-01 → {AAA, BBB, CCC, RE}; 2016-05-02 →
{AAA, BBB, CCC, DDD}; 2019-06-03 → {AAA, BBB, CCC, DDD}; 2021-07-01 → {AAA, BBB, CCC, RE}.

**M2 parsing.** Constituents rows `MMM`, `BRK.B` → `parse_current = ["MMM", "BRK-B"]`. Changes table rows (date cell
rowspan 2 over the first two): `July 9, 2024 | ABC[3] | Abc Corp | xyz.a | Xyz Inc`, `| NEWT | New T | | |`,
`June 3, 2019 | | | OLD | Old Co` → `[Change(2024-07-09, "ABC", "Abc Corp", "XYZ-A", "Xyz Inc"), Change(2024-07-09,
"NEWT", "New T", None, None), Change(2019-06-03, None, None, "OLD", "Old Co")]`.

**C1 calendar.** `rebalance_dates(date(2014,1,1), date(2014,4,30)) = [2014-01-31, 2014-02-28, 2014-03-31]`;
with today 2014-05-01 also 2014-04-30. `holdout_dates` of 10 dates with n = 36 → all 10.

**R1 metrics panel** (h = 3, `min_sector = 3`, `min_names = 3`). Sessions (adj_close) on 2023-12-01, 2024-01-02,
2024-02-01, 2024-03-01, 2024-04-01, 2024-05-01 (`last_panel` = 2024-05-01):
F1 100, 102, 104, 110, 108, 115 · F2 50, 49, 51, 48, 50, 52 · F3 20, 21, 22, 23, 22, 24 · F4 80, 82, 78, 76, 79, 81 ·
F5 30, 31, 29, 27 (no bars after 2024-03-01) · E1 40, 42, 41, 45, 43, 46 · E2 60, 58, 61, 63, 62, 66 ·
E3 25, 24, 26, 27, 25, 28 · E5 70, 71, 69, 72, 74, 73 · E4 only 2024-01-09 10, 02-01 11, 03-01 12, 04-01 11.5, 05-01 12.5.
F* sector Financials, E* Energy. Dates d1 2023-11-30 (target 2024-02-29), d2 2023-12-31 (2024-03-31), d3 2024-01-31
(2024-04-30), d4 2024-02-29 (2024-05-31 ≥ last_panel → all INCOMPLETE). Labels / scores (NE = NOT_ELIGIBLE, NS = NO_SIGNAL;
eligible = label ≠ NE):
| | F1 | F2 | F3 | F4 | F5 | E1 | E2 | E3 | E4 | E5 |
|---|---|---|---|---|---|---|---|---|---|---|
| d1 | BUY .40 | SELL −.30 | HOLD .05 | HOLD .10 | SELL −.25 | BUY .30 | SELL −.22 | NE .50 | HOLD 0 | NE NaN |
| d2 | BUY .35 | HOLD 0 | BUY .30 | SELL −.28 | HOLD 0 | BUY .27 | HOLD .10 | SELL −.21 | NS NaN | HOLD .05 |
| d3 | HOLD .15 | SELL −.24 | BUY .33 | SELL −.30 | HOLD .02 | BUY .26 | HOLD −.05 | HOLD .12 | BUY .29 | SELL −.35 |
| d4 | BUY .3 | SELL −.3 | BUY .3 | SELL −.3 | HOLD 0 | BUY .3 | HOLD 0 | HOLD 0 | BUY .3 | SELL −.3 |

Forward returns (ret / excess; median per sector-date):
| | d1 ret | d1 excess | d2 ret | d2 excess | d3 ret | d3 excess |
|---|---|---|---|---|---|---|
| F1 | 0.1 | 0.14 | 0.058823529412 | 0.038415366146 | 0.105769230769 | 0.067307692308 |
| F2 | −0.04 | 0 | 0.020408163265 | 0 | 0.019607843137 | −0.018853695324 |
| F3 | 0.15 | 0.19 | 0.047619047619 | 0.027210884354 | 0.090909090909 | 0.052447552448 |
| F4 | −0.05 | −0.01 | −0.036585365854 | −0.056993529119 | 0.038461538462 | 0 |
| F5 | −0.1 | −0.06 | −0.129032258065 EARLY_EXIT | −0.149440421330 | −0.068965517241 EARLY_EXIT | −0.107427055703 |
| E1 | 0.125 | NaN | 0.023809523810 | −0.018150570087 | 0.121951219512 | 0.039984006397 |
| E2 | 0.05 | NaN | 0.068965517241 | 0.027005423345 | 0.081967213115 | 0 |
| E3 | 0.08 | NaN (NE) | 0.041666666667 | −0.000293427230 | 0.076923076923 | −0.005044136192 |
| E4 | NaN NO_ENTRY (40 d) | NaN | NaN NO_ENTRY (9 d) | NaN | 0.136363636364 | 0.054396423249 |
| E5 | 0.028571428571 | NaN (NE) | 0.042253521127 | 0.000293427230 | 0.057971014493 | −0.023996198622 |

Medians: d1 Financials −0.04 (n 5), Energy n 2 < 3 → NaN; d2 Financials 0.020408163265 (= 1/49), Energy
**0.041960093897** (n 4, mean of the middle two); d3 Financials 0.038461538462 (= 1/26), Energy 0.081967213115 (= 5/61).
F5: d2 = 27/31 − 1, t1 = its last session 2024-03-01; d3 = 27/29 − 1; d4 INCOMPLETE (not EARLY_EXIT).
`spread_by_date`: d1 n_buy 1, n_sell 2, buy 0.14, sell −0.03, **spread 0.17** (E1, E2 have no excess);
d2 n_buy 3, n_sell 2, buy 0.015825226804, sell −0.028643478175, **spread 0.044468704979**; d3 3 / 3, buy
0.048942660698, sell −0.014283297982, **spread 0.063225958680**; d4 0 / 0, spread NaN.
`yearly_spread`: 2023 n_dates 2, **0.107234352489**; 2024 n_dates 1, **0.063225958680** (d4 not counted).
`hit_rates`: n_buy 7, buy_hit_rate **6/7 = 0.857142857143** (E1 d2 misses); n_sell 7, sell_hit_rate **5/7 =
0.714285714286** (F2 d1 and F4 d3 have excess exactly 0: misses).
`rank_ic`: d1 n 7 (E3 ineligible, E4 no ret), ic **0.5** exactly; d2 n 9 (F2/F5 score tie, average ranks), ic
**0.6861984751796929**; d3 n 10, ic **11/15 = 0.733333333333**; d4 n 0 → NaN. Mean IC **0.6398439361710088**. With
`min_names = 8`: d1 NaN, mean **0.7097659042565131**.
`turnover` on d1..d3 labels: d2 label 5/7 = 0.714285714286 (both-labelled: F1–F5, E1, E2; changed F2, F3, F4, F5, E2),
buy 1/3 (F3), sell 2/2 = 1.0; d3 label 4/9 = 0.444444444444 (changed F1, F2, E3, E5), buy 1/3 (E4), sell 2/3 (F2, E5).
Means: label **73/126 = 0.579365079365**, buy **1/3**, sell **5/6 = 0.833333333333**.

**N1 Newey–West.** x = [0.05, 0.02, −0.01, 0.04, 0.06, 0.03, −0.02, 0.05], L = 2: m 0.0275, γ0 0.00074375,
γ1 −0.00013203125, γ2 −0.0004515625, var = 1/3750, **t = 0.0275·√30000 = 4.763139720814412**. L = 0:
**2.852096383629523** (= plain t with population SD). L = 6: 7.545937746270388; L = 7 (T = L + 1) → NaN.
x = [0.02] × 5, L = 2 → var 0 → NaN. NaN entries are dropped first: [0.05, NaN, 0.02, …] equals the series without it.

**B1 baseline** (one date, Financials, all eligible unless stated; price / bvps): B1 10/10, B2 20/20, B3 32/20, B4 24/16,
B5 50/20, B6 12/10, B7 20/−5, B8 5/10 ineligible. G = B1–B6 (n 6): pb 1.0, 1.0, 1.6, 1.5, 2.5, 1.2; ranks 1.5, 1.5,
5, 4, 6, 3; pct **0.1, 0.1, 0.8, 0.6, 1.0, 0.4** → BUY, BUY, **SELL** (0.8 inclusive), HOLD, SELL, HOLD; score −ln(pb):
B3 −0.47000362924573563, B4 −0.4054651081081644, B5 −0.9162907318741551, B6 −0.1823215567939546, B1/B2 0.
B7 NO_SIGNAL, B8 NOT_ELIGIBLE. An Energy group of 4 valid names → all NO_SIGNAL (4 < 5).

**P1 pass bar.** Base: yearly [0.02, −0.01, 0.03, 0.0, 0.01, NaN], ic_mean 0.045, ic_t 2.4, holdout_ic 0.01,
holdout_spread 0.005 → share 3/5 = 0.6, mean yearly 0.01 → **PASS**, `failed_legs ()`.
ic_t 2.0 → FAIL (IC). ic_mean 0.03 → FAIL (IC). holdout None → **INCOMPLETE**, incomplete (HOLDOUT).
holdout_ic 0.0 → FAIL (HOLDOUT). holdout_spread −0.001 → FAIL (HOLDOUT). yearly [−0.01, 0.02], holdout None →
**FAIL** (SPREAD_YEARS), incomplete (HOLDOUT). yearly all NaN, rest base → INCOMPLETE, incomplete (SPREAD_YEARS, HOLDOUT).

## Property expectations (look-ahead and determinism)
- Changing any price dated > d, any fact with effective filed_date > d, or any rate dated > d leaves every `bt_signals`
  row of date d unchanged (forward returns may change). Adding a split after d changes no ratio, label or score on d.
- A Yahoo fact with estimated filed_date f is invisible on f + 29 days and visible on f + 30.
- Permuting input rows of any metric function changes no output; restricting `allowed_dates` to S equals running on
  the input filtered to S (spreads, hit rates, IC); turnover pairs are consecutive **within** S.
- Re-running the backtest on the same DB and `--today` gives identical tables except `run_id`/`started_at`.

## API contract (SW and QA)
```python
from valuation.backtest.membership import Change, Interval, norm, parse_changes, parse_current, build_intervals, members_on
from valuation.backtest.metrics import (forward_returns, sector_excess, spread_by_date, yearly_spread, hit_rates,
    rank_ic, newey_west_t, turnover, pass_bar, PassResult, low_pb_baseline)
from valuation.backtest.engine import (rebalance_dates, holdout_dates, sic_to_sector, thresholds_hash,
    run_backtest, signals_on)
from valuation.backtest.data import parse_splits, as_traded
from valuation.engine import split_factor, dps_ttm, build_inputs   # live engine, split-aware
```
- `signals_on(con, d, intervals) -> pd.DataFrame`: §3 for one date on a DuckDB connection with the live schema (plus
  `splits`): universe = `members_on(intervals, d)` ∩ tickers with a `securities` row and ≥ 1 `prices_daily` bar dated
  ≤ d; runs the live `run_signals(con, d, tickers=universe, csm_path=None)` and returns its report frame (columns as
  the live `SIGNAL_COLUMNS`: `ticker, market, sector, eligible, …, a_signal, a_mos, …, b_signal, …`), one row per
  universe ticker, sorted by ticker. It does not apply the +30-day lag itself: the lag is applied once when the
  backtest DB is built (`filed_date` of estimated facts is stored + 30 days), so a test DB passes it in pre-lagged.
- `split_factor(con, ticker, after, through)` = Π ratio of `splits` rows with `after < date ≤ through` (1.0 if none).
  `build_inputs` multiplies the filing's shares by `split_factor(ticker, filed_date, price_date)`; `dps_ttm` divides
  each dividend by `split_factor(ticker, ex_date, as_of)` before summing. With an empty `splits` table both are
  unchanged from Phase 7.
- `parse_splits(chart_payload) -> list[(date, ratio)]` (sorted; `ratio = numerator/denominator`, date = UTC date of
  the event timestamp); `as_traded(bars, divs, splits)` applies §3's ingest rule to `PriceBar` / `Dividend` lists
  (`valuation.providers.base`), `adj_close` unchanged.
Signatures as in §1, §2, §4; `sic_to_sector(sic: int | None) -> str | None`; `PassResult(result: str, failed_legs:
tuple[str, ...], incomplete_legs: tuple[str, ...])`. Frames carry exactly the columns named; dates are `datetime.date`.

## Decisions (beyond the SW brief)
- **D1 price basis.** The brief says prices are stored as traded; Yahoo delivers `close`, `volume` and dividends
  split-adjusted to the download date (`providers/base.py`: "split-adjusted"), so the backtest DB undoes this at ingest
  (§3) and the brief's share rule then holds. The brief's dividend rule ("the same way") is fixed as a **division** by
  the ratios in (ex_date, d]. QA check: NVDA (10:1 on 2024-06-10) as-traded close on 2024-06-07 ≈ 1209, Yahoo ≈ 120.9.
- **D2 incomplete horizon** is `target ≥ last_panel` (not only `>`): with the target on the last date no session can
  follow it for anyone, so EARLY_EXIT would be wrong. Added flags `NO_ENTRY`, `INCOMPLETE`, `NO_EXIT` for NaN reasons.
- **D3 pass bar** takes summary numbers; a failed computable leg gives FAIL even if another leg is incomplete; an
  in-sample leg without data is INCOMPLETE (like the holdout leg).
- **D4 per-date `missing_price`** also covers a valid ticker with no bar in the 10 days up to d (history starting late).
- **D5 membership:** inconsistent change rows are skipped and logged, never guessed; touching intervals are merged.
  A rename listed as add NEW / remove OLD stays two tickers; OLD's dates count as `missing_price` (Yahoo keeps the
  history under NEW only). This inflates the survivorship count; an alias table is a possible later fix.
- **D6** all runs are recorded in `bt_runs` (kind `in_sample` or `holdout`), not only holdout runs; `spread_by_date` is
  an extra public helper. TSX results never enter the pass bar.

## Changelog
- **0.1.0:** initial version (P8-0). Golden values computed with numpy/pandas/scipy (`rankdata`, `spearmanr` agree).
