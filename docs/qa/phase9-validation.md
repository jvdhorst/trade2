# Phase 9 validation (data batch 2, Model C, benchmarks), as of 2026-11-10

Owner: QA / SW · Specs: [`ttm.md`](../models/ttm.md) 0.2.0, [`composite.md`](../models/composite.md) 0.1.0,
[`benchmarks.md`](../models/benchmarks.md) 0.1.1, [`backtest.md`](../models/backtest.md) 0.2.0 · Model C backtest:
[`backtest_model_c.md`](backtest_model_c.md). Screening-signal validation only; not investment advice.

## 0. Summary
| Item | Result |
|---|---|
| P9-1 data batch 2 | AAPL / JPM TTM CFO, revenue, EBIT **exact** vs 10-K / 10-Q (bar ±0.5%); CNR.TO / SU.TO all 5 flows **exact** vs Yahoo trailing |
| P9-2 / P9-3 Model C | Spec + 90 spec tests (written before the code, green); live: TSX BUY 57 / HOLD 97 / SELL 41, US BUY 80 / HOLD 321 / SELL 98 |
| P9-4 Model C backtest | **FAIL** (IC 0.0081, t 0.28; spread > 0 in 6/10 years, mean −1.89%); holdout locked |
| P9-5 benchmarks | XIC / XIU trailing distributions **exact** vs BlackRock; SPY vs SPX_TR 12m within 0.12pp; SSGA / Vanguard / ishares.com unreachable |
| Vote (Phase 10) | stays off: **0 of 3** models pass the backtest bar |

## 1. Tests and reproducibility
- `uv run pytest -q`: **1,025 passed** = 793 existing + 232 new spec tests: `test_data_batch2_spec.py` (92),
  `test_benchmarks_spec.py` (50), `test_composite_spec.py` (90). QA wrote them from the specs before (data, Model C)
  or without reading (benchmarks) the implementation. `uv run ruff check src tests`: clean.
- Two existing tests changed: `test_providers.py` used `Revenues` as its "unparsed concept" example (now parsed);
  `test_signals_report_spec.py`'s pinned column list gains the `c_*` columns (composite.md, Report).
- QA's spec ambiguities (marked `# SPEC-AMBIGUITY` in the tests): filing order of two revenue tags for one quarter
  (ttm G9), Yahoo `parse_quarterly_flows` excludes NI, `total_return` uses the last level on/before as_of, where
  `CompositeInputs` raises on fx ≤ 0, mismatch flag when a period end is None. None changed a golden value.
- **Deterministic:** the re-run backtest reproduces Phase 8's Model A and B metrics to the last digit (IC 0.0067 / −0.0539,
  spread −1.37% / −2.15%), with Model C added on the same dates.

## 2. Data batch 2 (P9-1)
### 2.1 External checks (as_of 2026-11-10, $M)
| Ticker | Item | Ours (source, quarter end) | Reference | Diff |
|---|---|---|---|---|
| AAPL | CFO TTM | 145,097 (yahoo, 2026-09-30; EDGAR gives the same, 2026-09-26) | 10-K FY2026 (2025-09-28 … 2026-09-26) 145,097 | 0.0% |
| AAPL | Revenue TTM | 478,834 | 10-K 478,834 | 0.0% |
| AAPL | EBIT TTM | 158,063 | 10-K `OperatingIncomeLoss` 158,063 | 0.0% |
| JPM | CFO TTM | −84,159 (EDGAR, 2026-09-30) | 10-K FY2025 −147,782 − 10-Q 9M2025 −267,506 + 10-Q 9M2026 −203,883 = −84,159 | 0.0% |
| JPM | Revenue TTM | 203,153 | 182,447 − 136,649 + 157,355 = 203,153 | 0.0% |
| JPM | EBIT TTM | none | JPM tags no `OperatingIncomeLoss` (banks); Yahoo none | n/a (expected) |
| CNR.TO | CFO / revenue / GP / EBIT / capex TTM | 7,007 / 18,367 / 7,702 / 6,911 / 3,168 (CAD) | Yahoo `trailing*` types (secondary source) | 0.0% each |
| SU.TO | CFO / revenue / GP / EBIT / capex TTM | 18,207 / 67,981 / 29,846 / 14,865 / 5,494 | Yahoo `trailing*` | 0.0% each |

The EDGAR references are the FY and YTD duration facts as filed (companyfacts, forms 10-K / 10-Q), i.e. the
statement figures; JPM's Q4 is derived (FY − 9M), so this also checks the Q4 rule on cash-flow YTD facts.

### 2.2 Coverage (live, 2026-11-10; metric present / names)
| Metric | TSX | US | Note |
|---|---|---|---|
| E/P, B/P | 207, 212 / 215 | 498, 503 / 503 | |
| FCF/P | 170 | 403 | Financials excluded by design; others lack a capex or CFO TTM |
| GP/A | 171 | 409 | banks, insurers excluded; some US issuers tag neither `GrossProfit` nor a cost concept |
| −accruals | 183 | 417 | |
| −leverage | 185 | **374** | ~60 US non-financials match none of D1–D5 (P9-10) |
| MOM, VOL | 215, 215 | 499, 500 | |

Flags on the live run: `FCF_PERIOD_MISMATCH` US 10, `ACCRUALS_PERIOD_MISMATCH` TSX 1 / US 5, `FLOW_CCY_MISMATCH` 0.

### 2.3 Anomalies (reported, not fixed)
- **Source choice for 52/53-week filers (P9-7).** Yahoo stamps AAPL's quarter 2026-09-30, EDGAR 2026-09-26; "latest
  `period_end` wins" (ttm.md §3) therefore picks Yahoo, whose filed date is an estimate. The values agree here; in the
  backtest Yahoo facts carry the +30-day lag, so the effect is a later, not a wrong, number. Related to P7-13.
- **Yahoo `TotalDebt` vs EDGAR D1–D5:** definitions differ (Yahoo includes leases); recorded per source, not reconciled.
- **TSX history is short:** Yahoo timeseries gives ~5 quarters, so TSX flows have no backtest depth (as in Phase 8).

### 2.4 Runtime (P8-8 fixed)
Bulk DataFrame upserts replace `executemany`: S&P 500 first load **502 s** (incl. the 2-year chart backfill; was 36 min),
TSX 215 in 200 s; a fundamentals refresh of 503 names takes 172 s.

## 3. Model C (P9-2, P9-3)
- Live counts (2026-11-10): **TSX** BUY 57, HOLD 97, SELL 41, NO_SIGNAL 0, NOT_ELIGIBLE 20; **US** BUY 80, HOLD 321,
  SELL 98, NO_SIGNAL 3 (short history / few factors), NOT_ELIGIBLE 1. Vetoes applied: `VETO_MOMENTUM` 1 TSX + 1 US,
  `VETO_DISTRESS` 0. Names scored on 3 factors: TSX 3, US 1.
- **Pooling anomaly (P9-9):** TSX holds 29% of its eligible names in BUY vs US 16% (quintiles are pooled, z-scores are
  sector-neutral but not market-neutral). Not changed; PM decision.
- External checks (composite.md): **MOM and VOL recomputed** independently in pandas from the raw Yahoo chart
  downloads for AAPL, JPM, XOM, RY.TO, CNR.TO, SU.TO: equal to 1e-9 (same session indexing). **E/P vs 1/trailingPE:
  blocked** (Yahoo quoteSummary returns 401 "Invalid Crumb" from this session, P9-8).

## 4. Backtest (P9-4; US, in-sample 2014-01 … 2023-10, survivor-biased upper bound)
| Model | Result | 12m IC (NW t) | spread years > 0 | 12m mean spread | BUY / SELL hit | BUY 12m excess vs SPX_TR |
|---|---|---|---|---|---|---|
| A residual income | FAIL | 0.0067 (0.13) | 3/10 | −1.37% | 57.1% / 50.6% | **−3.13%** (4/10 years > 0) |
| B P/B ~ ROE | FAIL | −0.0539 (−0.86) | 4/10 | −2.15% | 49.9% / 43.6% | **−1.10%** (3/10) |
| **C composite** | **FAIL** (IC) | 0.0081 (0.28) | 6/10 | −1.89% | 48.3% / 50.1% | **−1.92%** (3/10) |
| C_V value | diag. | −0.0561 (−1.32) | 3/10 | −2.66% | 47.7% / 47.1% | |
| C_Q quality | diag. | 0.0221 (0.83) | 6/10 | +2.24% | 51.9% / 50.4% | |
| C_M momentum | diag. | 0.0005 (0.02) | 5/10 | −0.32% | 47.7% / 50.1% | |
| C_R low vol | diag. | 0.0382 (0.83) | 6/10 | −1.68% | 50.2% / 51.7% | |
| ALL eligible (reference) | | | | | | −0.95% (5/10) |

- The excess vs benchmark is equal-weighted bucket minus the cap-weighted SPX_TR over the same window; the universe
  itself lags by ~1%/yr (ALL row), so A's BUY bucket sits ~2.2% below the universe, B's ~0.2%, C's ~1.0%. Phase 8's
  "+7.2% BUY excess" for Model A is the **sector-neutral** excess (vs sector median) and is not comparable.
- Model C's spread is wrecked by 2020 (−26.1% 12m: momentum/low-vol crash into the recovery) and 2016 (−7.7%).
- TSX: no Model C backtest beyond the supplementary run (Yahoo fundamentals start mid-2025; too short, never in the bar).

## 5. Proposals for the PM (nothing tuned; holdout unspent)
1. **Phase 10 stays gated:** no model passes. Options: (a) accept that the free-data US 2014–2023 sample cannot
   validate these models and keep them "unvalidated" in the report; (b) revisit after the delisted-price source (P8-4),
   since survivor bias is an upper-bound distortion in exactly the losers the SELL buckets target.
2. **Model C design questions** for AN (a priori, to be frozen before a holdout test): whether value should be
   retained given its negative IC over a decade (proposal §4 warns of multi-year value droughts, so dropping it on this
   evidence would be fitting the sample); whether pooled quintiles should be per market (P9-9).
3. **Eligibility `min_history_sessions` 273 → 274** (P9-6, composite D5).
4. Benchmark excess as a pass criterion: not recommended yet (equal- vs cap-weight gap of ~1%/yr dominates).

## 6. Benchmarks (P9-5)
| Check | Result |
|---|---|
| XIC trailing distributions (ex 2025-12-30, 2026-03-26, 06-25, 09-24) | 0.281 + 0.272 + 0.291 + 0.285 = **1.129** = BlackRock Canada history (exact per payment); yield 1.95% |
| XIU (ex 2025-11-19 … 2026-08-26) | **1.136** = BlackRock (exact); BlackRock also lists 0.00 year-end rows (2024-12-30, 2025-12-30: notional reinvested distributions), no effect |
| Older XIC row | 2024-12-30: BlackRock 0.24038, Yahoo 0.24 (Yahoo rounds); outside today's window, reported only |
| SPY / IVV / VOO issuer pages | **not reachable** from this session (ssga.com, vanguard.com, ishares.com: connection refused by the proxy); only blackrock.com (CA) is allowed. Yahoo values: SPY 7.583 (1.01%), IVV 8.397 (1.12%), VOO 7.428 (1.08%) |
| TR consistency | SPY 12m adj_close return 11.18% vs SPX_TR 11.30%: 0.12pp (tolerance 0.25pp) |
| Index level vs another public quote | not done (no second free quote host allowed) |
| SPECIAL_DIST | none flagged on 2026-11-10 |

Report header (live): SPX_TR 12m +11.3%, 5y ann +11.7%; XIC 12m +22.1%, 5y ann +13.9%, yield 1.95% (−1.70pp vs the
Canada 10y); SPY yield 1.01% (−3.85pp vs the US 10y).

## 7. Runtime
| Step | Time |
|---|---|
| `backtest build` (fresh, incl. EDGAR companyfacts v3 for 587 CIKs) | 537 s |
| `backtest run --market US` (154 dates, Models A/B/C) | 1,136 s wall on 4 workers (72.6 CPU-min; ~28 s/date, +60% vs Phase 8 for Model C's flow lookups) |
| `backtest report` | ~100 s |
