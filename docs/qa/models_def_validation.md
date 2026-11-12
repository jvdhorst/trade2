# Models D, E, F validation (external checks), as of 2026-11-12

Owner: QA · Specs: [`dividend_sustainability.md`](../models/dividend_sustainability.md) (D),
[`piotroski.md`](../models/piotroski.md) (E), [`accruals.md`](../models/accruals.md) (F), all 0.1.0, §Report and
backtest, "External checks"; data rules [`ttm.md`](../models/ttm.md) §1, §5 and §6. This is a screening-signal
validation only, not investment advice. D, E and F have never been backtested, and nothing here is a return test. No
backtest, holdout or frozen model was run or touched.

## 0. Summary
| Check (spec) | Result |
|---|---|
| **E**: each input (NI, CFO, GP, revenue TTM and prior TTM; TA at t, t−4q, t−8q; debt, CA, CL, shares at t and t−4q) vs the 10-K / 10-Q, exact | **PASS: exact** for AAPL, MSFT, CAT and KO (4 of 4 names, 72 of 72 inputs; CAT debt is None on both sides) |
| **E**: nine signals recomputed independently by hand formula | **PASS**: signals, F, n_scored and signal identical for all 4 names; all 10 ratios bit-identical |
| **E**: ROA, gross margin and current ratio vs the issuer's own ratios (±1%) | **n/a**: none of the four issuers tags these ratios in XBRL. Their numerators and denominators match the face statements exactly, so the ratios agree by construction |
| **E** secondary: Yahoo `returnOnAssets`, `grossMargins`, `currentRatio` (±10%) | **blocked**: Yahoo `quoteSummary` returned HTTP 401 "Invalid Crumb"; not retried |
| **F**: NI TTM, CFO TTM (sum of four quarters), TA_t, TA_{t−4q} vs the filings, exact | **PASS: exact** for AAPL, MSFT, CAT and KO (16 of 16) |
| **F**: `NI − CFO` and the accrual ratio recomputed, to 1e-9 | **PASS**: \|Δratio\| = 0 for all 4 names (bit-identical) |
| **F**: `acc_pct` | **n/a**: it needs a (market, sector) universe of ≥ 12 names (`MIN_SECTOR`); only the ratio is reported |
| **F**: CFO TTM vs Yahoo `operatingCashflow` (±5%) | **blocked**: Yahoo `quoteSummary` HTTP 401 (as above) |
| **D**: NI TTM and shares vs the 10-K / 10-Q, exact | **PASS** for KO, JNJ, MSFT (exact). **PEP: NI TTM fails**: the pipeline has no NI TTM at all (`NO_TTM`; §4.1). Shares exact for all 4 |
| **D**: DPS_TTM vs EDGAR declared DPS (`CommonStockDividendsPerShareDeclared`, ±2%) | **PASS** for MSFT (−1.89%) and PEP (+0.02%). KO and JNJ no longer tag the declared concept; vs `CommonStockDividendsPerShareCashPaid` instead: KO 0.00%, JNJ 0.00% (§4.3) |
| **D**: `dy` vs Yahoo `trailingAnnualDividendYield` (±5%), `payout` vs Yahoo `payoutRatio` (±10%) | **blocked**: Yahoo `quoteSummary` HTTP 401; not retried |
| **D** metrics recomputed by hand (eps, payout, g_sust, dy, k, spread) | **PASS**: bit-identical for KO, JNJ, MSFT (PEP has no metrics: NO_DATA) |
| Full engine `run_signals` on the same in-memory DB | **consistent**: the `d_*`, `e_*`, `f_*` columns equal the hand mapping for every name (§3.4) |
| Live `valuation value` run (report columns `d_*`, `e_*`, `f_*`) | **blocked**: no live DB in the container (no `data/` directory); see §5 |
| Spec tests | `test_piotroski_spec.py` (114), `test_piotroski_engine_spec.py` (17), `test_accruals_spec.py` (81), `test_accruals_engine_spec.py` (21), `test_dividend_sustainability_spec.py` (101), `test_dividend_sustainability_engine_spec.py` (15): 349 passed |

| Ticker | Models | BS date (form, filed) | Inputs match? | E signals / F / n / signal | F accrual ratio (pct) | D payout / spread / signal |
|---|---|---|---|---|---|---|
| AAPL | E, F | 2026-09-26 (10-K, 2026-11-03) | yes, all exact | `111111111` / 9 / 9 / **BUY** | −0.033837 (n/a) | (not a D name; see §4.4) |
| MSFT | D, E, F | 2026-09-30 (10-Q, 2026-10-28) | yes, all exact | `111110100` / 6 / 9 / HOLD | −0.075221 (n/a) | 0.1895 / −0.0314 / SELL (`G_CAPPED`) |
| CAT | E, F | 2026-09-30 (10-Q, 2026-11-04) | yes, all exact (debt None both sides) | `1111-1101` / 7 / 8 / **BUY** | −0.023464 (n/a) | – |
| KO | D, E, F | 2026-10-02 (10-Q, 2026-10-28) | yes, all exact | `111111111` / 9 / 9 / **BUY** | −0.014935 (n/a) | 0.6175 / −0.0160 / HOLD (`G_CAPPED`) |
| PEP | D | 2026-09-05 (10-Q, 2026-10-08) | shares exact; **NI TTM None in the pipeline** (true value 10,896 $M) | (`----111--`, F_INCOMPLETE; not an E name) | (NO_DATA) | – / – / **NO_SIGNAL / NO_DATA** |
| JNJ | D | 2026-09-27 (10-Q, 2026-10-21) | yes, all exact | – | – | 0.7897 / −0.0371 / SELL |

Environment: no live DB and no live `valuation value` run. EDGAR (`data.sec.gov`) is reachable. Every request used the
provider's default generic User-Agent (`trade2-valuation-screener research`); no personal information was sent.
**Yahoo:** the brief expected Yahoo to be throttled; it was not for the chart API. One small probe
(`query1.finance.yahoo.com/v8/finance/chart/KO?range=1y&interval=3mo&events=div`) returned HTTP 200. Then the
pipeline's own chart request (`HISTORY_URL`, `range=2y`, daily, `events=div|split`) was made once for each of KO, PEP,
JNJ, MSFT, AAPL and CAT (all HTTP 200), plus one `quoteSummary` request for KO (`v10`, module `summaryDetail`), which
returned **HTTP 401 "Invalid Crumb"**. That one was not retried. The fundamentals-timeseries endpoint was not used.
FRED `DGS10` (one request, HTTP 200) supplied rf. So D's DPS comes from its real source (the Yahoo chart), and EDGAR
is the cross-check, as the spec intends.

## 1. Method
A throwaway script (not committed) did the following, with as_of 2026-11-12:
1. **Download:** `companyfacts/CIK##########.json` from EDGAR for AAPL, MSFT, CAT, KO, PEP and JNJ. Each file was
   saved untrimmed in its own new, empty directory under the session scratchpad, as were the Yahoo chart JSONs and the
   FRED CSV. All Python that read them ran with `python -I`.
2. **Pipeline:** each JSON went through `sec_edgar.trim_companyfacts`, then `parse_companyfacts` +
   `parse_quarterly_ni` + `parse_quarterly_flows`. The facts were loaded with `ingest.pipeline.upsert_facts` into
   `store.db.connect(":memory:")`. Also loaded: `securities` rows (`upsert_security`: USD, the GICS sector from
   `universe/sp500.csv`, 1 share class, conversion 1); the Yahoo chart bars and dividends (`yahoo.parse_chart` →
   `upsert_prices` / `upsert_dividends`); and US10Y (`rates.parse_fred_csv` → `rates_daily`). Then:
   - balance sheet `t`: the real `engine.build_inputs(con, as_of, tickers)`;
   - **E and F**: the real `engine._ef_inputs(con, inp, meta, "US", True, frozenset(), as_of)`. Inside, it uses
     `filing_in_window` (t−4q window 350–380 days, t−8q 715–745), `flow_ttm` (with the `period_end == t` rule) and
     `flow_ttm_at` (prior TTM). Then `piotroski.evaluate` and `accruals.compute_metrics`;
   - **D**: mapped exactly as `run_signals` does: `engine.ni_common_ttm`, `engine.dps_ttm`, `engine.ex_date_count`,
     the first-bar coverage guard, `dps_ttm_prev = dps_ttm(as_of − 365) / split_factor(…)`, rf from
     `engine.risk_free`, and `roe_raw` as in Model A (`ri.roe_raw(NI TTM, CE_end, CE_start)` with `_filing_at` /
     `_filing_year_before`). Then `dividend_sustainability.evaluate`;
   - finally, `engine.run_signals(con, as_of, tickers)` on the same DB, as a cross-check of the hand mapping.
3. **Filing (independent):** worked from the *untrimmed* JSON, without any repo code. Forms 10-K / 10-Q (and /A) filed
   ≤ as_of. For each accession, its BS date is its latest equity date
   (`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`, else `StockholdersEquity`).
   - **BS filings:** `t` is the latest BS date (ties go to the latest filed). t−4q / t−8q is the accession with the
     latest BS date 350–380 / 715–745 days before `t`. `Assets`, `AssetsCurrent` and `LiabilitiesCurrent` were read
     at that accession and date. Debt uses rules D1–D5 (`ttm.md` §5.2), re-implemented from the spec text. Shares
     are the dei cover-page `EntityCommonStockSharesOutstanding` of that accession, else `CommonStockSharesOutstanding`.
   - **Flows:** each TTM is **the sum of four discrete quarters** taken from the raw duration facts (latest filed
     ≤ as_of per period). A quarter is the direct 3-month value, else YTD(end) − YTD(previous quarter end) on the
     same start (Q4 = FY − 9M). Periods were matched on start/end dates, never on `fy`/`fp`.
   - **Concepts:** NI = `NetIncomeLossAvailableToCommonStockholdersBasic`, else `NetIncomeLoss` − preferred
     dividends. CFO = `NetCashProvidedByUsedInOperatingActivities`. Revenue = the first concept of the revenue chain
     with all four quarters. GP = `GrossProfit`, else revenue − cost (`CostOfRevenue` / `CostOfGoodsAndServicesSold` /
     `CostOfGoodsSold`).
   - **Prior TTMs** end at the revenue series' quarter end inside [t − 380, t − 350].
   - **Declared DPS:** `CommonStockDividendsPerShareDeclared` (unit `USD/shares`), the last four discrete quarters
     ending ≤ as_of.
4. **Compare:** each input with `==` (exact float equality). Then recomputed by hand from the *filing* values:
   - **E:** the nine signals, with `q(a, b)` = a/b for b > 0, TA_{t−8q} fallback, strict comparisons and signal 7 as
     `shares_t·200 ≤ shares_4q·201`;
   - **F:** `NI − CFO`, `(TA_t + TA_prev)/2` and the ratio;
   - **D:** `eps = NI/shares·1.0`, `payout`, `g_sust = clip(roe·(1 − payout), 0, 0.06)`, `dy`,
     `k = dy·(1 + g) + g` and `spread = k − (rf + 0.05)`, with literal constants.

## 2. Model E inputs: pipeline vs filing (USD $M; every row matched exactly)
Selected filings (pipeline = independent for every name; t−4q / t−8q by `filing_in_window`):
| Ticker | t (accession) | t−4q (form, filed) | t−8q (form, filed) | Prior TTM end (q_prev) |
|---|---|---|---|---|
| AAPL | 2026-09-26 (0000320193-26-000026) | 2025-09-27 (10-K, 2025-10-31) | 2024-09-28 (10-K, 2024-11-01) | 2025-09-27 |
| MSFT | 2026-09-30 (0001193125-26-436133) | 2025-09-30 (10-Q, 2025-10-29) | 2024-09-30 (10-Q, 2024-10-30) | 2025-09-30 |
| CAT | 2026-09-30 (0000018230-26-000057) | 2025-09-30 (10-Q, 2025-11-03) | 2024-09-30 (10-Q, 2024-11-06) | 2025-09-30 |
| KO | 2026-10-02 (0001628280-26-067844) | 2025-09-26 (10-Q, 2025-10-23) | 2024-09-27 (10-Q, 2024-10-24) | 2025-09-26 |

Flows (TTM ending t, and prior TTM; concept as resolved by both paths):
| Ticker | NI TTM | NI prior | CFO TTM | GP TTM | GP prior | Rev TTM | Rev prior |
|---|---|---|---|---|---|---|---|
| AAPL | 132,175 | 112,010 | 145,097 | 233,602 | 195,201 | 478,834 | 416,161 |
| MSFT | 142,631 | 104,912 | 195,878 | 234,394 | 202,037 | 346,445 | 293,812 |
| CAT | 11,831 | 9,273 | 14,184 | 27,047 (derived) | 22,905 (derived) | 77,773 | 64,671 |
| KO | 14,629 | 13,031 | 16,227 | 31,816 | 29,376 | 51,054 | 47,663 |

The quarters behind the current TTMs (independent derivation; the pipeline's stored quarters are the same values):
- AAPL NI: 42,097 + 29,578 + 29,789 + 30,711. The last is FY 363d − 9M 272d from the FY2026 10-K.
- MSFT NI: 38,458 + 31,778 + 35,766 (FY − 9M) + 36,629.
- CAT NI: 2,402 (FY − 9M) + 2,549 + 3,593 + 3,287.
- KO NI: 2,271 (FY − 9M) + 3,924 + 4,425 + 4,009.

Concepts used:
- NI: `NetIncomeLoss` for AAPL, MSFT and KO, which have no NI-available tag and no preferred dividends;
  `NetIncomeLossAvailableToCommonStockholdersBasic` for CAT.
- Revenue: `RevenueFromContractWithCustomerExcludingAssessedTax` for AAPL and MSFT; `Revenues` for CAT and KO.
- GP: CAT tags no `GrossProfit`, so it is `Revenues − CostOfRevenue` (§4.5).

Balance sheets:
| Ticker | TA t / t−4q / t−8q | Debt t / t−4q (rule) | CA t / t−4q | CL t / t−4q | Shares t / t−4q (dei cover) |
|---|---|---|---|---|---|
| AAPL | 404,548 / 359,241 / 364,980 | 80,111 / 98,657 (D3) | 173,857 / 147,957 | 169,915 / 165,631 | 14,532,182,000 / 14,776,353,000 |
| MSFT | 779,403 / 636,351 / 523,013 | 35,481 / 43,208 (D3) | 186,137 / 189,066 | 151,968 / 134,996 | 7,426,194,552 / 7,432,377,655 |
| CAT | 106,843 / 93,722 / 86,273 | **None / None** (no D1–D5 concept) | 57,834 / 49,818 | 40,666 / 35,991 | 457,594,174 / 467,979,596 |
| KO | 107,950 / 106,045 / 106,266 | 41,546 / 47,089 (D5) | 33,326 / 27,247 | 24,209 / 22,499 | 4,301,902,335 / 4,301,608,845 |

split_factor 1.0 for all names (no splits in the windows; the `splits` table was empty, as in the live DB). E flags:
none. Every current TTM ended at `t`, so there is no `E_PERIOD_MISMATCH`, no `PRIOR_TA_FALLBACK` (TA_{t−8q} is present
for every name) and no currency flag.

## 3. Results: module vs hand recomputation
### 3.1 Model E
| Ticker | roa / roa_prior | lev_t / lev_prior | cr_t / cr_prior | gm_t / gm_prior | ato_t / ato_prior | Signals (module = hand) | F / n | Signal |
|---|---|---|---|---|---|---|---|---|
| AAPL | 0.367928 / 0.306894 | 0.198026 / 0.274626 | 1.023200 / 0.893293 | 0.487856 / 0.469052 | 1.332905 / 1.140230 | `111111111` | 9 / 9 | **BUY** |
| MSFT | 0.224139 / 0.200592 | 0.045523 / 0.067900 | 1.224843 / 1.400530 | 0.676569 / 0.687640 | 0.544424 / 0.561768 | `111110100` | 6 / 9 | HOLD |
| CAT | 0.126235 / 0.107484 | None / None | 1.422171 / 1.384179 | 0.347769 / 0.354177 | 0.829827 / 0.749609 | `1111-1101` | 7 / 8 | **BUY** (≥ 7 at n = 8) |
| KO | 0.137951 / 0.122626 | 0.384863 / 0.444047 | 1.376595 / 1.211032 | 0.623183 / 0.616327 | 0.481437 / 0.448525 | `111111111` | 9 / 9 | **BUY** |

All ten ratios matched bit for bit (e.g. AAPL roa `0.3679284936852976`, KO ato_prior `0.44852539852822165`). Signal 7
(no net issuance beyond 0.5%) is 1 for every name: all four have lower or flat share counts (KO +0.007%). CAT's
signal 5 is unscored because it has no debt tag (§4.2). That leaves n_scored = 8, and F = 7 reaches the n = 8 BUY bar.
CAT would be HOLD if a debt value existed and its leverage had not fallen.

### 3.2 Model F
| Ticker | NI TTM | CFO TTM | NI − CFO (module = hand) | TA_t | TA_{t−4q} (period end) | Avg TA | Accrual ratio (module = hand, full repr) | \|Δ\| | cash_conv | acc_pct |
|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | 132,175 | 145,097 | −12,922 | 404,548 | 359,241 (2025-09-27) | 381,894.5 | `-0.033836570047486934` | 0 | 1.0978 | n/a |
| MSFT | 142,631 | 195,878 | −53,247 | 779,403 | 636,351 (2025-09-30) | 707,877 | `-0.0752206951207625` | 0 | 1.3733 | n/a |
| CAT | 11,831 | 14,184 | −2,353 | 106,843 | 93,722 (2025-09-30) | 100,282.5 | `-0.023463715005110564` | 0 | 1.1989 | n/a |
| KO | 14,629 | 16,227 | −1,598 | 107,950 | 106,045 (2025-09-26) | 106,997.5 | `-0.014934928386177247` | 0 | 1.1092 | n/a |

`period_ok` is True for all four names (NI and CFO TTMs end at `t`), and there are no F flags. F's TA_{t−4q} is the same
filing as E's t−4q for every name. The percentile and the signal need ≥ 12 eligible names per (market, sector), so
`acc_pct` is **n/a** here. With 2 IT names, the cross-check run gives `SMALL_SECTOR`, as specified. For information
only: MSFT's ratio (−0.075) is below `BUY_RATIO` (−0.05), and the others lie inside ±0.05.

### 3.3 Model D
rf = US10Y 0.0488 (FRED, 2026-11-09, the latest on/before as_of), so r = 0.0988. Price = Yahoo close of 2026-11-11.
The first price bar is 2024-11-12 for every name, so both DPS windows are covered (as_of − 723 = 2024-11-19 ≥ first
bar), and `dps_ttm_prev` is computed.
| Ticker | NI TTM (pipe = filing) | Shares (pipe = cover) | DPS_TTM Yahoo (ex-dates in window) | EDGAR DPS, last 4 quarters | Δ | dps_prev / g_hist | roe_raw | eps | payout | dy | k | spread | Signal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KO | 14,629 | 4,301,902,335 | 2.10 (0.51, 0.53, 0.53, 0.53; n_ex 4) | **CashPaid** 0.51 + 0.53 + 0.53 + 0.53 = 2.10 | 0.00% | 2.015 / 0.0422 | 0.4264 | 3.4006 | 0.6175 | 0.02152 | 0.08281 | −0.01599 | HOLD (`G_CAPPED`) |
| PEP | **None** (true: 10,896) | 1,362,741,092 | 5.806 (1.423, 1.423, 1.48, 1.48; n_ex 4) | Declared 1.4225 + 1.4225 + 1.48 + 1.48 = 5.805 | +0.02% | 5.556 / 0.0450 | None | None | None | 0.04034 | None | None | **NO_SIGNAL / NO_DATA** |
| JNJ | 16,095 | 2,407,310,266 | 5.28 (1.30, 1.30, 1.34, 1.34; n_ex 4) | **CashPaid** 1.30 + 1.30 + 1.34 + 1.34 = 5.28 | 0.00% | 5.08 / 0.0394 | 0.2002 | 6.6859 | 0.7897 | 0.01883 | 0.06172 | −0.03708 | SELL (spread ≤ −0.02) |
| MSFT | 142,631 | 7,426,194,552 | 3.64 (0.91 × 4; n_ex 4) | Declared 0.91 + 0.91 + 0.91 + 0.98 = 3.71 | −1.89% | 3.32 / 0.0964 | 0.3422 | 19.2065 | 0.1895 | 0.00698 | 0.06740 | −0.03140 | SELL (spread ≤ −0.02; `G_CAPPED`) |

eps, payout, g_sust, dy, k and spread recomputed by hand are bit-identical to the module for KO, JNJ and MSFT. With
EDGAR's DPS in place of Yahoo's, payout would be 0.6175 (KO, unchanged), 0.7897 (JNJ, unchanged) and 0.1932 (MSFT).
No signal changes. MSFT's −1.89% is timing: the 0.98 declared in the September 2026 quarter has its ex-date after
as_of (Yahoo shows none yet), and the 365-day ex-date window holds four payments of 0.91. Both pass ±2%. That is
expected but tight for any issuer that raises its dividend in the last declared quarter.

### 3.4 Cross-check: full `run_signals` on the same DB
`engine.run_signals(con, 2026-11-12, {AAPL, MSFT, CAT, KO, PEP, JNJ})` ran without error. All six names are eligible.
Its `e_signals` / `e_f_score` / `e_n_scored` / `e_signal`, `f_accrual_ratio` / `f_cash_conv` and `d_payout` /
`d_g_sust` / `d_dy` / `d_k` / `d_spread` / `d_signal` equal §3.1–3.3 for every name. Extra rows from that run, not part
of the spec checks:
- **JNJ** E: `110110100`, F 5, n 9, HOLD.
- **PEP** E: `----111--`, n 3, `F_INCOMPLETE`. PEP F: NO_DATA (§4.1).
- **AAPL** D: SELL with flag `DIV_CUT` (§4.4).

## 4. Data anomalies (not fixed in code; for SW / AN)
1. **PEP has no flow TTM at all (`NO_TTM`): its 16-week fiscal Q4 is never stored.** PepsiCo reports 12/12/12/16-week
   quarters. The Q4 that ended 2025-12-27 runs 2025-09-07..2025-12-27, which is **112 days** (FY 363d − 36-week YTD
   251d).
   - **Cause:** `_quarters_from_durations` accepts quarters of 80–100 days only, and so does its FY − 9M step
     (`is_q`). `ttm.QUARTER_DAYS` = (80, 100) also rejects the 112-day gap between quarter ends.
   - **Effect:** the store holds Q1–Q3 of every year and no Q4 (e.g. `ni_common_q` 2025-03-22, 2025-06-14,
     2025-09-06, 2026-03-21, …), so NI, CFO, revenue and GP TTMs are None for every as_of.
   - **Consequences:** PEP is D `NO_DATA`, E `F_INCOMPLETE` (n 3, only balance-sheet signals) and F `NO_DATA`. The
     same applies to Models A, H, J and G (`NO_TTM` in the run's flags).
   - **True values** (independent, with a 120-day tolerance): NI TTM 10,896 $M (2,540 + 2,327 + 2,981 + 3,048),
     CFO TTM 14,569, revenue TTM 98,241.
   - This is a systematic gap for 52/53-week filers with a 16-week quarter, not a PEP data error. Allowing ≤ ~120 days
     for a Q4 derived as FY − 9M is an SW/AN decision (`ttm.md` §1).
2. **CAT: `total_debt` None in both 10-Qs** (t and t−4q). There is no D1–D5 concept outside 10-Ks, as already found in
   Model G §4.2 and Model J §4.1. E signal 5 is unscored and CAT reaches BUY at n = 8. Outside 10-K season, such
   filers always run on 8 signals.
3. **KO and JNJ no longer tag `CommonStockDividendsPerShareDeclared`.** KO's declared-DPS facts stop in 2018 (latest
   quarter 2018-09-28), and JNJ has one such fact in total. Both tag `CommonStockDividendsPerShareCashPaid` instead,
   which was used for the ±2% check (a stated substitution). If the spec's concept is used literally, the KO
   comparison would silently use 2017–2018 quarters (sum 1.54, a false +36% gap). A future automated check should
   require the quarters to end within the last year. KO also has 4 old facts of the declared concept under unit `pure`
   instead of `USD/shares`. PEP's Q4 declared DPS derived as FY − 9M is `1.4224999999999994` (float), and Yahoo
   rounds the same payment to 1.423.
4. **AAPL: false `DIV_CUT` from the 365-day window edge** (seen in the cross-check; AAPL is not a D name here).
   AAPL's 2025-11-10 ex-date is 367 days before as_of, and its November 2026 ex-date was not yet in the Yahoo chart on
   2026-11-11. The window (2025-11-12, 2026-11-12] therefore holds only 3 payments (0.26 + 0.27 + 0.27 = 0.80, n_ex 3)
   against 4 a year earlier (1.03). The result is g_hist −0.223 and `DIV_CUT`, although the dividend was raised
   (0.26 → 0.27). The signal is SELL anyway (spread −0.036), but the flag is misleading. This is the mirror image of
   the "5 payments in 365 days" risk in the spec (G-E1). It recurs for any quarterly payer whose ex-date drifts later
   by a few days. Whether to annualise by payment count or use a 372-day window is an AN decision.
5. **CAT gross profit is derived** (`Revenues − CostOfRevenue`; CAT tags no `GrossProfit`). CAT's `Revenues` includes
   Financial Products revenues, which have no matching cost of goods sold, so the derived margin (0.348) is not the
   margin of the machinery business. It is consistent over time, so signal 8 (Δ margin) is still meaningful. This is
   the `GrossProfit` tag gap noted in `piotroski.md`.
6. **JNJ Q3 2026 NI is 210 $M** as tagged, against 5.1–5.5 bn in the other three quarters (presumably a one-off charge;
   not investigated). The value is stored and used as reported. It lowers NI TTM and lifts D's payout to 0.79, the
   one-off-NI risk in D's §Risks. The run also flags JNJ `EBIT_PERIOD_MISMATCH` (Models J/G). E and F are not
   affected (all JNJ flows end at `t`).
7. **KO debt uses rule D5** (`LongTermDebtAndCapitalLeaseObligations` + current + short-term borrowings), so it
   includes finance leases. The rule is the same at t and t−4q, so signal 5 compares like with like.
8. **No restatement effects**: for every name, the latest-filed value per period equals the one the pipeline used.
   Several comparatives were re-filed unchanged (e.g. KO's and JNJ's 2025 quarters in the 2026 10-Qs). No
   `E_PERIOD_MISMATCH`, `F_PERIOD_MISMATCH`, `PRIOR_TA_FALLBACK` or currency flag occurred for the E/F names.
9. As in Models G and J, the companyfacts `fy`/`fp` fields describe the filing, not the period. Both paths key on
   start/end dates.

## 5. Blocked checks and how to run them locally
**Yahoo `quoteSummary` cross-checks** (D: `trailingAnnualDividendYield` ±5%, `payoutRatio` ±10%; E: `returnOnAssets`,
`grossMargins`, `currentRatio` ±10%; F: `operatingCashflow` ±5%). Status: **blocked**: HTTP 401 "Invalid Crumb" from
the cloud container (one attempt, not retried). On a machine where Yahoo serves a crumb (e.g. `yfinance`
`Ticker(t).info`), compare those fields to §3.1–3.3 for the same names. Expect `payoutRatio` to use Yahoo's own EPS
(diluted), and `trailingAnnualDividendYield` to lag by one payment.

**Live DB run.** Status: **not run** (no `data/` directory in the container). Locally:
`uv run valuation ingest --as-of 2026-11-12`, then `uv run valuation value --as-of 2026-11-12`. In
`reports/2026-11-12/screen_signals.csv`, the rows AAPL, MSFT, CAT, KO, PEP and JNJ should show the `d_*`, `e_*` and
`f_*` values in §3, given the same EDGAR facts and Yahoo dividends and no restatement filed after this check. `f_acc_pct`
and `f_signal` will then be populated from the full US sector groups. Spec tests:
`uv run pytest -q tests/unit/test_piotroski_spec.py tests/unit/test_piotroski_engine_spec.py
tests/unit/test_accruals_spec.py tests/unit/test_accruals_engine_spec.py tests/unit/test_dividend_sustainability_spec.py
tests/unit/test_dividend_sustainability_engine_spec.py`.
