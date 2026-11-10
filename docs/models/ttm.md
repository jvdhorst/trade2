# Data spec: quarterly flows (NI, CFO, capex, revenue, gross profit, EBIT), balance-sheet batch 2, TTM and DPS_TTM (`ttm`, v0.2.0)

Owner: SW (data spec, from proposal §0 / §8) · Tests: QA (`tests/unit/test_ttm_spec.py`, `tests/unit/test_data_batch2_spec.py`) ·
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

## 5. Data batch 2 (v0.2.0, Phase 9 / P9-1): more quarterly flows and balance-sheet items
Model C (`composite.md`) and Model B's EV branch need CFO, capex, revenue, gross profit and EBIT as TTM flows, and
total assets, total debt and cash from the balance sheet. Everything in §1–§3 applies unchanged; this section only
adds items and the tag chains that fill them.

### 5.1 Flow items (one discrete fiscal quarter each, unit = currency)
| Item | EDGAR chains, in priority order (each chain derived on its own, §1 steps 1–3) | Yahoo quarterly type |
|---|---|---|
| `ni_common_q` | unchanged (§1) | `quarterlyNetIncomeCommonStockholders` |
| `cfo_q` | 1 `NetCashProvidedByUsedInOperatingActivities` · 2 `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | `quarterlyOperatingCashFlow` |
| `capex_q` | 1 `PaymentsToAcquirePropertyPlantAndEquipment` · 2 `PaymentsToAcquireProductiveAssets` | `quarterlyCapitalExpenditure` (sign flipped, below) |
| `revenue_q` | 1 `Revenues` · 2 `RevenueFromContractWithCustomerExcludingAssessedTax` · 3 `RevenueFromContractWithCustomerIncludingAssessedTax` · 4 `SalesRevenueNet` · 5 `RevenuesNetOfInterestExpense` | `quarterlyTotalRevenue` |
| `gross_profit_q` | 1 `GrossProfit` · 2 derived `REV − COST` (below) | `quarterlyGrossProfit` |
| `ebit_q` | 1 `OperatingIncomeLoss` | `quarterlyOperatingIncome` |

- **Chain = one concept** (or one derived pair). Inside a snapshot (§1) every chain is turned into discrete quarters
  separately; per quarter end the value comes from the **first chain in priority order that has that quarter**.
  Chains are never mixed inside one derivation (a Q4 is FY − 9M of the same chain). Emission (first appearance, then
  every change of value) and filed dates are exactly as in §1, per item. This generalises the NI rule (chain 1 `A`,
  chain 2 `N − P`), which is unchanged.
- **Cash-flow items are year-to-date in 10-Qs.** EDGAR's Q2/Q3 10-Q carry 6M/9M cumulatives only; §1 step 2
  (difference of cumulatives) turns them into quarters (Q2 = 6M − Q1, Q3 = 9M − 6M, Q4 = FY − 9M). No new rule.
- **`gross_profit_q` chain 2** (`REV − COST`): for a (start, end) where both exist in the snapshot,
  `REV` = the value of the first revenue concept of the `revenue_q` chain list present for that (start, end), and
  `COST` = the first present of `CostOfRevenue`, `CostOfGoodsAndServicesSold`, `CostOfGoodsSold`. Missing either →
  no chain-2 duration (unlike NI's preferred dividends, there is no 0 default).
- **Capex sign.** `capex_q` is stored as a **positive outflow**. EDGAR `PaymentsTo…` facts are positive outflows
  and are stored as reported. Yahoo reports capital expenditure as a negative cash flow, so the stored value is
  `−reportedValue` (a reported −30 becomes 30). A Yahoo capex that is reported positive therefore becomes negative;
  it is stored as is (an anomaly to report, not fixed).
- **Yahoo flows** use the parse of §2 unchanged (discrete quarters including Q4; `filed_date` = quarter end + 90 days
  for a fiscal year end found in `annualNetIncomeCommonStockholders`, else + 45 days, capped at `_fetched`;
  `filed_estimated = True`; unit = `currencyCode`; points without `reportedValue.raw` skipped).
- Not a revenue for banks and insurers in any comparable sense: `revenue_q` is stored where tagged, but models must
  not rank Financials on it (Model B's EV branch excludes them; Model C does not use revenue).

API: `sec_edgar.parse_quarterly_flows(ticker, payload) -> list[Fact]` returns the facts of every flow item above
except `ni_common_q` (`parse_quarterly_ni` stays); `yahoo.parse_quarterly_flows(ticker, ts_payload) -> list[Fact]`
likewise for the Yahoo types. Item names are constants in `providers/base.py`: `CFO_Q, CAPEX_Q, REVENUE_Q,
GROSS_PROFIT_Q, EBIT_Q` (`FLOW_ITEMS` = all six incl. `NI_COMMON_Q`).

### 5.2 Balance-sheet items (instant, same filing as book value)
Stored like the existing balance-sheet items: EDGAR one set per accession at that filing's primary balance-sheet date
(`parse_companyfacts`), Yahoo per quarter end with the +45-day estimate (`parse_balance_sheet`). A missing item is
**absent**, never 0.
| Item | EDGAR (first rule that applies, values at the filing's balance-sheet date) | Yahoo quarterly type |
|---|---|---|
| `total_assets` | `Assets` | `quarterlyTotalAssets` |
| `cash` | 1 `CashAndCashEquivalentsAtCarryingValue` · 2 `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` · 3 `Cash` | `quarterlyCashAndCashEquivalents` |
| `total_debt` | rules D1–D5 below | `quarterlyTotalDebt` |

**Total debt (EDGAR).** `ST` = first present of `ShortTermBorrowings`, `CommercialPaper`, else 0.
| Rule | Applies when present | total_debt |
|---|---|---|
| D1 | `DebtLongtermAndShorttermCombinedAmount` | that value |
| D2 | `LongTermDebtNoncurrent` and `DebtCurrent` | `LongTermDebtNoncurrent + DebtCurrent` (DebtCurrent already holds current LTD + short-term borrowings) |
| D3 | `LongTermDebt` (total incl. current maturities) | `LongTermDebt + ST` |
| D4 | `LongTermDebtNoncurrent` | `LongTermDebtNoncurrent + (LongTermDebtCurrent or 0) + ST` |
| D5 | `LongTermDebtAndCapitalLeaseObligations` | `that + (LongTermDebtAndCapitalLeaseObligationsCurrent or 0) + ST` |
| — | none of the above | absent |

Definitions differ by source and are not reconciled: EDGAR `total_debt` excludes operating-lease liabilities and
(except D5) finance leases; Yahoo `TotalDebt` usually includes capital/finance leases and, for some issuers,
operating leases. The source is recorded with the fact; a model must take numerator and denominator from the
same filing. Item names: `TOTAL_ASSETS, TOTAL_DEBT, CASH` (added to `ITEMS`, so `build_inputs`' filing carries them).

### 5.3 TTM selection for every flow item
`ttm.ttm` (§3) is used unchanged for each flow item; the engine's `flow_ttm(con, ticker, item, as_of)` generalises
`ni_common_ttm`: compute the TTM per source, keep the one with the latest `period_end`, EDGAR on a tie; returns
`(TTMResult, source, currency)`. Two flow TTMs are combined (e.g. `CFO − capex`) by a model only when both have the
same `period_end`; that rule belongs to the model spec.

### Golden examples, data batch 2 (QA fixtures; values in $M; all facts `form` 10-Q/10-K, unit USD)
**G8, CFO from year-to-date facts only** (`NetCashProvidedByUsedInOperatingActivities`, (start, end, val, filed)):
9M 2025 (2025-01-01, 2025-09-30, 280, 2025-11-01); FY 2025 (2025-01-01, 2025-12-31, 400, 2026-02-15);
Q1 (2026-01-01, 2026-03-31, 100, 2026-05-01); 6M (2026-01-01, 2026-06-30, 250, 2026-08-01);
9M (2026-01-01, 2026-09-30, 330, 2026-11-01). → `cfo_q` rows (period_end, filed, value): (2025-12-31, 2026-02-15,
**120**), (2026-03-31, 2026-05-01, 100), (2026-06-30, 2026-08-01, **150**), (2026-09-30, 2026-11-01, **80**). The 9M 2025
fact alone yields no quarter. TTM at 2026-11-09 = **450**; at 2026-10-01 = NO_TTM (three quarters known).

**G9, revenue chain fallback and priority.** `Revenues` direct quarters (2025-07-01, 2025-09-30, 500) and
(2025-10-01, 2025-12-31, 510); `RevenueFromContractWithCustomerExcludingAssessedTax` direct quarters
(2025-07-01, 2025-09-30, 480), (2026-01-01, 2026-03-31, 520), (2026-04-01, 2026-06-30, 530); all filed by
2026-08-01. → `revenue_q`: 2025-09-30 **500** (chain 1 wins over 480), 2025-12-31 510, 2026-03-31 **520** (chain 2
fills), 2026-06-30 530. TTM at 2026-08-15 = **2060**.

**G10, gross profit from revenue − cost.** No `GrossProfit` for (2026-04-01, 2026-06-30): `Revenues` 1000,
`CostOfRevenue` 600 → **400**. For (2026-07-01, 2026-09-30): `GrossProfit` 450, `Revenues` 1100, `CostOfRevenue` 700
→ **450** (chain 1). For (2026-01-01, 2026-03-31): `Revenues` 900 and no cost concept and no `GrossProfit` → no
`gross_profit_q` row. With `CostOfGoodsAndServicesSold` 520 instead of `CostOfRevenue` for (2026-04-01, 2026-06-30)
and `Revenues` 1000 → **480**.

**G11, capex sign.** EDGAR `PaymentsToAcquirePropertyPlantAndEquipment` (2026-07-01, 2026-09-30, 30) → `capex_q`
**30**. Yahoo `quarterlyCapitalExpenditure` reportedValue −30 at asOfDate 2026-09-30 → **30**; reportedValue +5 → −5.
Only `PaymentsToAcquireProductiveAssets` (2026-07-01, 2026-09-30, 12) → 12 (chain 2).

**G12, total debt rules** (instant values at the filing's balance-sheet date):
| Facts present | total_debt |
|---|---|
| `DebtLongtermAndShorttermCombinedAmount` 900, `LongTermDebt` 800 | **900** (D1) |
| `LongTermDebtNoncurrent` 700, `DebtCurrent` 150, `LongTermDebt` 800 | **850** (D2 before D3) |
| `LongTermDebt` 800, `ShortTermBorrowings` 50, `CommercialPaper` 30 | **850** (D3, ST = STB) |
| `LongTermDebt` 800, `CommercialPaper` 30 | **830** (D3) |
| `LongTermDebt` 800 | **800** (D3, ST = 0) |
| `LongTermDebtNoncurrent` 700, `LongTermDebtCurrent` 100, `CommercialPaper` 50 | **850** (D4) |
| `LongTermDebtNoncurrent` 700 | **700** (D4) |
| `LongTermDebtAndCapitalLeaseObligations` 600, `LongTermDebtAndCapitalLeaseObligationsCurrent` 40 | **640** (D5) |
| `ShortTermBorrowings` 50 only | absent (no long-term rule applies) |

**G13, cash and assets.** `CashAndCashEquivalentsAtCarryingValue` 70 and
`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` 75 → `cash` **70**; only the latter → **75**; only
`Cash` 60 → **60**. `Assets` 5000 → `total_assets` 5000; no `Assets` → absent.

**G14, Yahoo flows and balance sheet.** Timeseries payload with `_fetched` "2026-11-05", `annualNetIncomeCommonStockholders`
at asOfDate 2025-12-31, and quarterly points (asOfDate, reportedValue, currencyCode CAD):
`quarterlyOperatingCashFlow` 2025-12-31 → 210, 2026-09-30 → 190; `quarterlyTotalRevenue` 2026-09-30 → 1200;
`quarterlyTotalAssets` 2026-09-30 → 9000; `quarterlyTotalDebt` 2026-09-30 → 2500;
`quarterlyCashAndCashEquivalents` 2026-09-30 → 300. → `cfo_q` (2025-12-31, filed **2026-03-31** (+90, fiscal year
end), 210, CAD, estimated) and (2026-09-30, filed **2026-11-05** (+45 = 2026-11-14 capped at `_fetched`), 190);
`revenue_q` (2026-09-30, 2026-11-05, 1200); balance sheet facts at period_end 2026-09-30, filed **2026-11-14**
(+45, the balance sheet is not capped, §2): `total_assets` 9000, `total_debt` 2500, `cash` 300.

## Golden examples (§1–§4)
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
- **0.2.0:** data batch 2 (Phase 9, P9-1): flow items `cfo_q`, `capex_q` (positive outflow), `revenue_q`,
  `gross_profit_q`, `ebit_q` with EDGAR tag chains and Yahoo types; balance-sheet items `total_assets`, `total_debt`
  (rules D1–D5), `cash`; generic `flow_ttm`. Goldens G8–G14.
- **0.1.0:** initial version (Phase 6, P6-3/P6-4).
- **0.1.1:** Yahoo filed date capped at the download date (`_fetched`). Without it, a TSX quarter reported
  within 45 days of its end was invisible on the day it was downloaded, which left 58 of 215 TSX names `NO_TTM`.
