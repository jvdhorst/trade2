# Data spec: quarterly NI to common, TTM flows and DPS_TTM (`ttm`, v0.1.1)

Owner: SW (data spec, from proposal §0 / §8) · Tests: QA (`tests/unit/test_ttm_spec.py`) ·
Code: `src/valuation/ttm.py`, `src/valuation/providers/sec_edgar.py`, `src/valuation/providers/yahoo.py`

## Purpose
Models A, B and C need flow items (net income, later revenue, CFO) summed over the trailing twelve months (TTM),
**point in time**: on any `as_of` only data with `filed_date <= as_of` may be used. This spec fixes how discrete
quarters are built from each source, how they are stored, and how a TTM value is selected. The first flow is
`ni_common_q`: net income available to common shareholders for one fiscal quarter.

## Storage
One row per (ticker, quarter end, filed_date, item, source) in `fundamentals`, item `ni_common_q`, unit = the
currency of the value. A quarter can have several rows (restatements); each has the date it became public.

## 1. EDGAR (`sec_edgar.parse_quarterly_ni(ticker, payload) -> list[Fact]`)
Input: a companyfacts payload (`facts.us-gaap.<concept>.units.USD[]`, each fact with `start`, `end`, `val`,
`filed`, `form`, `accn`). Only facts with `form` in {10-K, 10-Q, 10-K/A, 10-Q/A, 20-F, 40-F} **and** a `start`
are used. `days = end − start` in calendar days.

Concepts:
- `A` = `NetIncomeLossAvailableToCommonStockholdersBasic`
- `N` = `NetIncomeLoss`
- `P` = first present, for the same (start, end), of `PreferredStockDividendsAndOtherAdjustments`,
  `PreferredStockDividendsIncomeStatementImpact`, `DividendsPreferredStock`; 0 if none.

**Snapshots.** Let `F` = the sorted distinct `filed` dates of the usable facts. For each `f` in `F` build the
snapshot: for every (concept, start, end) the value of the fact with the latest `filed <= f` (ties: the larger
`accn`). Facts filed after `f` don't exist in that snapshot.

**Chains.** In a snapshot, chain 1 values are `A(start, end)`; chain 2 values are `N(start, end) − P(start, end)`.
Each chain is turned into discrete quarters on its own (never mixing A and N within one derivation):
1. **Direct:** every duration with `80 <= days <= 100` is the quarter ending `end`.
2. **Difference of cumulatives:** for two durations with the same `start` s, ends e1 < e2, `e1 − s >= 80` and
   `80 <= e2 − e1 <= 100`: quarter ending e2 = `v(s, e2) − v(s, e1)`, used only when step 1 has no quarter ending
   e2. (This gives Q4 = FY − 9M YTD, Q3 = 9M − 6M, Q2 = 6M − Q1.) If several pairs qualify, use the largest e1.
3. **FY minus three quarters:** for a duration with `340 <= days <= 380` (fiscal year, start s, end e) and no
   quarter ending e yet: if quarters q1, q2, q3 (from steps 1–2) exist with `q1.start = s`, each next start 1–4 days
   after the previous end, and `80 <= e − q3.end <= 100`, then quarter ending e = `FY − (q1 + q2 + q3)`.

Per quarter end, the snapshot value is chain 1's if chain 1 has that quarter, else chain 2's. No quarter → none.

**Emission.** Process snapshots in increasing `f`. Emit `Fact(ticker, quarter_end, filed_date=f, "ni_common_q",
value, "USD", "sec_edgar", filed_estimated=False)` the first time a quarter end appears, and again whenever its
value changes (`!=`). So a derived Q4 gets the 10-K's filing date, and a restated quarter gets a second row.

## 2. Yahoo (`yahoo.parse_quarterly_ni(ticker, ts_payload) -> list[Fact]`)
From the fundamentals-timeseries types `quarterlyNetIncomeCommonStockholders` (discrete quarters, Q4 included) and
`annualNetIncomeCommonStockholders` (used only to find fiscal year ends). Unit = the point's `currencyCode`.
`estimate = quarter_end + 90 days` when the quarter end equals an annual `asOfDate` (annual filing deadline),
else `+ 45 days`. If the payload carries a top-level `"_fetched": "YYYY-MM-DD"` (stamped by the provider when the response
was downloaded; always after the quarter ends, since a response can't contain an unfinished quarter), `filed_date = min(estimate, _fetched)`: a value present in a response downloaded on day X was public
by day X. Otherwise `filed_date = estimate`. `filed_estimated = True`; source `"yahoo"`. Points without
`reportedValue.raw` are skipped. (v0.1.1; the balance sheet keeps the plain +45-day estimate for now.)

## 3. TTM selection (`ttm.ttm(quarters, as_of) -> TTMResult`)
`quarters`: objects with `period_end`, `filed_date`, `value` (e.g. `Fact`s of one ticker, one item, one source).
```
known        = quarters with filed_date <= as_of
latest[q]    = for each period_end q, the row with the latest filed_date in known
q0 > q1 > q2 > q3 = the four latest period_ends in latest
consecutive  = 80 <= (q_i − q_{i+1}).days <= 100 for i = 0, 1, 2
value        = sum of latest[q0..q3].value      if 4 quarters and consecutive, else None
```
`TTMResult(value, period_end, filed_date, quarter_ends, flags)`: when value is not None, `period_end = q0`,
`filed_date` = the latest filed_date of the four rows, `quarter_ends = (q0, q1, q2, q3)`; when value is None,
`period_end = filed_date = None` and `quarter_ends = ()`. Flags: `NO_TTM` when value is None (fewer than 4 quarters, or a gap); `STALE_TTM` when value is
not None and `(as_of − q0).days > 456`.

Source choice in the engine: compute the TTM per source and keep the one with the latest `period_end`; on a tie,
EDGAR beats Yahoo (same rule as the balance sheet).

## 4. Dividends (`yahoo.parse_chart(ticker, payload)` and `ttm.dps_ttm(dividends, as_of)`)
`parse_chart` reads a v8 chart response (`chart.result[0]`): bars from `timestamp`, `indicators.quote[0].close`,
`.volume`, `indicators.adjclose[0].adjclose` (date = UTC date of the timestamp; bars with a null close are dropped;
source `yahoo_chart`), and dividends from `events.dividends` (`{amount, date}`: ex-date = UTC date of `date`).
Returns `(bars, dividends)`, dividends as `Dividend(ticker, ex_date, amount, currency, source="yahoo_chart")` with
currency = `meta.currency`.
```
dps_ttm = sum(amount for ex_date in (as_of − 365 days, as_of])      0.0 if there are none
```
Ex-dates after `as_of` never count. Amounts are Yahoo's split-adjusted per-share values in the trading currency.

## Golden examples
**G1, Q4 from FY − 9M (AAPL FY2026, values in $M).** Facts (start, end, val, filed):
Q1 (2025-09-28, 2025-12-27, 42097, 2026-01-30); H1 YTD (2025-09-28, 2026-03-28, 71675, 2026-05-01);
Q2 (2025-12-28, 2026-03-28, 29578, 2026-05-01); 9M YTD (2025-09-28, 2026-06-27, 101464, 2026-07-31);
Q3 (2026-03-29, 2026-06-27, 29789, 2026-07-31); FY (2025-09-28, 2026-09-26, 132175, 2026-11-03), all `NetIncomeLoss`.
→ quarter 2026-09-26 = **132175 − 101464 = 30711**, filed 2026-11-03. TTM at 2026-11-09 = **132175**
(= 42097 + 29578 + 29789 + 30711). TTM at 2026-11-02 = NO_TTM if only these facts exist (Q4 not yet public).

**G2, chain 1 preferred and Q4 derivation (JPM, $M).** `A`: 9M (2025-01-01, 2025-09-30, 42991, 2025-11-04),
Q3 (2025-07-01, 2025-09-30, 14043, 2025-11-04), FY (2025-01-01, 2025-12-31, 55681, 2026-02-13),
Q1 (2026-01-01, 2026-03-31, 16148, 2026-05-01), Q2 (2026-04-01, 2026-06-30, 20752, 2026-08-06),
Q3 (2026-07-01, 2026-09-30, 14936, 2026-11-03). `N` for the same periods: 44023, 14393, 57048, 16494, 21155, 15305.
→ quarter 2025-12-31 = **55681 − 42991 = 12690** (chain 1, not 57048 − 44023). TTM at 2026-11-09 =
**12690 + 16148 + 20752 + 14936 = 64526**. TTM at 2026-11-02 (Q3 2026 not public): 14043 + 12690 + 16148 + 20752
= **63633**.

**G3, chain 2 with preferred dividends.** Only `N` and `PreferredStockDividendsAndOtherAdjustments`, quarters
(2026-01-01, 2026-03-31): N 100, P 10; (2026-04-01, 2026-06-30): N 120, P 10; (2026-07-01, 2026-09-30): N 90,
no P; FY 2025 not present → quarters 90, 110, 90.

**G4, FY minus three quarters (no YTD facts).** Direct quarters only (2025-01-01, 2025-03-31, 10),
(2025-04-01, 2025-06-30, 20), (2025-07-01, 2025-09-30, 30), all filed before 2026-02-15, plus FY (2025-01-01,
2025-12-31, 100, filed 2026-02-15) → quarter 2025-12-31 = **100 − 60 = 40**, filed 2026-02-15.

**G5, restatement.** Q1 (2026-01-01, 2026-03-31) = 50 filed 2026-05-01, and the same period = 45 in a filing of
2026-08-01 → two rows: (2026-03-31, 2026-05-01, 50) and (2026-03-31, 2026-08-01, 45). A repeat of 45 in a later
filing adds no row. `ttm` at 2026-06-01 uses 50, at 2026-09-01 uses 45.

**G6, gap → NO_TTM.** Quarters ending 2025-03-31, 2025-06-30, 2025-12-31, 2026-03-31 (2025-09-30 missing) →
value None, flags {NO_TTM}.

**G7, DPS_TTM.** Ex-dates/amounts 2025-10-27 1.54, 2026-01-26 1.64, 2026-04-23 1.64, 2026-07-27 1.76,
2026-10-27 1.76, 2026-11-20 1.80 → at 2026-11-09: **1.64 + 1.64 + 1.76 + 1.76 = 6.80**; at 2026-10-26:
1.54 + 1.64 + 1.64 + 1.76 = **6.58**.

## Changelog
- **0.1.0:** initial version (Phase 6, P6-3/P6-4).
- **0.1.1:** Yahoo filed date capped at the download date (`_fetched`). Without it, a TSX quarter reported
  within 45 days of its end was invisible on the day it was downloaded, which left 58 of 215 TSX names `NO_TTM`.
