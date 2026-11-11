# Model J validation (external checks), as of 2026-11-11

Owner: QA · Spec: [`ebit_yield_roc.md`](../models/ebit_yield_roc.md) 0.1.0 §Report and backtest, "External checks";
data rules [`ttm.md`](../models/ttm.md) §5. Screening-signal validation only; not investment advice. J has never been
backtested; nothing here is a return test.

## 0. Summary
| Check (spec) | Result |
|---|---|
| EBIT TTM vs 10-K / 10-Q operating income, exact, 2–3 US names | **PASS: exact** for AAPL, MSFT, CAT (3 of 3) |
| EV vs Yahoo `enterpriseValue` within ±10% | **blocked**: Yahoo throttled (HTTP 429) from the cloud container; not attempted further |
| EY vs 1 / Yahoo `enterpriseToEbitda` (sanity bound) | **not checkable**: needs Yahoo (same block) |
| IC / ROC from stored EDGAR balance-sheet items | AAPL, MSFT computed (§3); **CAT: `total_debt` None** in its latest 10-Q, so J is `NO_DATA` (§4) |
| Spec tests | `tests/unit/test_ebit_yield_roc_spec.py` (80) + `test_ebit_yield_roc_engine_spec.py` (11), green |

Environment: no live DB (no `data/` directory) and no live `valuation value` run. Yahoo is not contacted in any way.
EDGAR (`data.sec.gov`) is reachable; requests used the provider's default generic User-Agent.

## 1. Method (EBIT TTM)
A throwaway script (not committed) did, per name:
1. Downloaded `companyfacts/CIK##########.json` from EDGAR, passed it through `sec_edgar.trim_companyfacts` (what
   `EdgarProvider.companyfacts` caches) and parsed it with the ingest path: `parse_companyfacts` +
   `parse_quarterly_ni` + `parse_quarterly_flows`. It then loaded those facts with `ingest.pipeline.upsert_facts`
   into an in-memory DuckDB (`store.db.connect(":memory:")`).
2. **Pipeline:** `engine.flow_ttm(con, ticker, "ebit_q", 2026-11-11)`, the function the engine's J mapping calls
   (via `_flow`).
3. **Filing (independent):** used the untrimmed JSON directly, `us-gaap/OperatingIncomeLoss`, USD, forms 10-K/10-Q,
   filed ≤ as_of. TTM = latest 10-K fiscal-year value + 10-Q YTD now − the same 10-Q's prior-year YTD comparative.
   When no 10-Q comes after the 10-K, TTM = the FY value. Periods were matched by start/end dates, not by the
   companyfacts `fy` field (see §4).

## 2. EBIT TTM results (as_of 2026-11-11, USD)
| Ticker | Pipeline TTM (quarters ending) | Filing derivation | Filing TTM | Match |
|---|---|---|---|---|
| AAPL | 158,063,000,000 (2025-12-27, 2026-03-28, 2026-06-27, 2026-09-26) | FY2026 10-K, 2025-09-28..2026-09-26: 158,063M (accn 0000320193-26-000026, filed 2026-11-03) | 158,063,000,000 | **exact** |
| MSFT | 163,191,000,000 (2025-12-31 .. 2026-09-30) | FY2026 10-K, 2025-07-01..2026-06-30: 155,237M (accn 0001193125-26-323660, filed 2026-07-29) + Q1 FY27 10-Q 2026-07-01..09-30: 45,915M − 2025-07-01..09-30: 37,961M (accn 0001193125-26-436133, filed 2026-10-28) | 163,191,000,000 | **exact** |
| CAT | 14,254,000,000 (2025-12-31 .. 2026-09-30) | FY2025 10-K, 2025-01-01..12-31: 11,151M (accn 0000018230-26-000008, filed 2026-02-13) + Q3 10-Q 9M 2026: 11,594M − 9M 2025: 8,491M (accn 0000018230-26-000057, filed 2026-11-04) | 14,254,000,000 | **exact** |

Pipeline discrete quarters (`ebit_q`, $M, filed date). Each quarter had one version in the window, so there were no
restatements:
- AAPL: 50,852 (Q1 FY26, filed 2026-01-30), 35,885 (2026-05-01), 35,695 (2026-07-31), 35,631 (Q4, filed 2026-11-03).
  Q4 is FY minus Q1–Q3, derived when the 10-K appears.
- MSFT: 38,275 (2026-01-28), 38,398 (2026-04-29), 40,603 (2026-07-29; FY − 3 quarters), 45,915 (2026-10-28).
- CAT: 2,660 (Q4 2025, filed 2026-02-13; FY − 3 quarters), 3,085 (2026-05-06), 4,295 (2026-08-05), 4,214 (2026-11-04).

TTM flags: none (no `STALE_TTM`). Source `sec_edgar`, currency USD, the same as the balance sheet, so there is no
`FLOW_CCY_MISMATCH`. For each name the TTM period_end equals the selected balance-sheet period_end, so there is no
`EBIT_PERIOD_MISMATCH`.

## 3. EV / IC / EY / ROC from stored balance-sheet items
The engine selects the balance-sheet filing with `build_inputs` (latest filing with `filed_date ≤ as_of`); the script
used `bv.select_latest_filing` on the same facts. $M, reporting currency USD.

| Ticker | BS period (filed) | total_equity | total_debt (rule) | cash | pref / NCI | IC = eq + debt − cash | ROC | EV − market cap = debt + pref + NCI − cash |
|---|---|---|---|---|---|---|---|---|
| AAPL | 2026-09-26 (2026-11-03, 10-K) | 112,093 | 80,111 (D3: `LongTermDebt` 78,114 + `CommercialPaper` 1,997) | 36,305 | – / – | 155,899 | 1.0139 | 43,806 |
| MSFT | 2026-09-30 (2026-10-28, 10-Q) | 470,509 | 35,481 (D3: `LongTermDebt` 35,481; no CP / ST borrowings tagged) | 20,281 | – / – | 485,709 | 0.3360 | 15,200 |
| CAT | 2026-09-30 (2026-11-04, 10-Q) | 21,482 | **None** (no D1–D5 concept tagged) | 7,929 | – / −1 | n/a | n/a | n/a → J `NO_DATA` |

- **EV, EY: not computed.** No price source is reachable from this container: Yahoo is blocked and there is no
  stored price DB. EV = market cap (price × all-class shares, converted at fx) + the last column; EY = EBIT TTM / EV.
  As a cross-check of the stored share count: AAPL 14,532,182,000 shares (cover page), MSFT 7,426,194,552, CAT
  457,594,174.
- AAPL's ROC above 100% is expected for a buyback-heavy issuer with small invested capital. It is not a data error,
  but it shows that the financing-side IC turns small or negative after large buybacks (spec §Risks, `NEG_IC`).
- **Sanity bound (spec):** EY should be below 1 / EV-to-EBITDA, because EBIT < EBITDA. This is not checkable without
  Yahoo `enterpriseToEbitda`.

## 4. Data anomalies (not fixed in code; for SW / AN)
1. **CAT: no debt tags in its 10-Qs.** In companyfacts, CAT tags `LongTermDebtNoncurrent` and `ShortTermBorrowings`
   only in 10-Ks (filed 2024-02-16, 2025-02-14, 2026-02-13), never in a 10-Q. The Q3 2026 10-Q has no us-gaap debt
   concept at all for 2026-09-30, likely because it uses company-extension tags, which companyfacts does not carry.
   As a result, `total_debt` is None for 3 of 4 quarters, and J is `NO_DATA` for CAT except in the weeks after a
   10-K. Expect this for other filers with extension-tagged balance sheets. Possible fixes, a spec decision for AN:
   carry the last 10-K debt forward with a flag, or accept `NO_DATA`.
2. **CAT FY2025 10-K debt is probably understated under D4.** At 2025-12-31 D4 gives `LongTermDebtNoncurrent`
   30,696 + `LongTermDebtCurrent` (absent → 0) + `ShortTermBorrowings` 5,514 = 36,210. The current portion of
   long-term debt is not tagged with a D-chain concept: `LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths`
   = 7,120 is a maturity disclosure, indicative only. Defaulting a missing D4 current part to 0 can understate
   EV and IC.
3. **The companyfacts `fy`/`fp` fields describe the filing, not the period.** In MSFT's Q1 FY27 10-Q, the
   prior-year comparative (2025-07-01..09-30) carries `fy=2027`. Any derivation that keys on `fy` would pair the
   wrong periods. The pipeline uses start/end dates, which is correct; the independent check did the same.
4. **Derived Q4 (FY − Q1..Q3) only appears on the 10-K filed date** (AAPL 2026-11-03, CAT 2026-02-13). This is
   point-in-time correct, but it means that between the fiscal year end and the 10-K filing, the TTM ends one quarter
   earlier. No YTD-derivation errors were found: all three TTMs tie out exactly.
5. Minor: CAT stores NCI as −1 ($M). That is harmless for EV, and IC does not use it.

## 5. EV vs Yahoo `enterpriseValue` (±10%): how to run it locally
Status: **blocked: Yahoo throttled (HTTP 429) from the cloud container; not attempted further.**

On a machine where Yahoo responds:
1. `uv run valuation ingest --as-of <date>`, then `uv run valuation value --as-of <date>`. This writes
   `reports/<date>/screen_signals.csv` with `j_ev` (reporting currency, built from market cap / fx_rate + debt +
   pref + NCI − cash).
2. For each check name, take Yahoo `enterpriseValue` (quote summary, in trading currency). Convert `j_ev` to the
   trading currency by multiplying by the name's fx_rate. For US USD reporters the fx is 1; for TSX names use the
   run's USDCAD when the reporter files in USD.
3. Pass if |j_ev × fx / enterpriseValue − 1| ≤ 10%. Debt definitions differ (D1–D5 vs Yahoo `TotalDebt`, which
   includes leases), so expect our EV below Yahoo's for lease-heavy names. Record any miss with its debt
   components.
4. In the same pass, check `j_ey < 1 / enterpriseToEbitda` for each name.
