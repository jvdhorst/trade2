# Phase 7 validation (Model A residual income, Model B peer P/B), as of 2026-11-09

Owner: QA (P7-4) · Universe: TSX Composite (215) + S&P 500 (503) · DB: `data/valuation.duckdb` (not in git, opened
read-only) · Signals run `155b7bfcf148` (re-run after the CE-alignment fix, §6b) · Report: `reports/2026-11-09/screen_signals.csv/.parquet` (718 rows, sorted,
disclaimer line and parquet `disclaimer` key present; signals match the DB row for row, numbers to CSV rounding).
Screening signals for human review only, not investment advice.

## 1. Tests
`uv run pytest -q`: **568 passed** (incl. 2 SW regression tests for the §6b fix, `test_signals_engine.py`). Phase 7 files: `test_residual_income_spec.py` 196, `test_peer_regression_spec.py`
102, `test_signals_report_spec.py` 30, `test_issuer_check.py` 15 (incl. the ROE parser). `uv run ruff check src tests`:
clean.

## 2. Model B regression reproduced independently (spec §External check)
Script: `tests/qa_scripts/reproduce_phase7_regression.py <db> [sectors]` (standalone, does not import `valuation`).
It rebuilds F and S from the payload (eligible True, not NEEDS_CSM_INPUT, b0 > 0, roe_raw not None, F: roe_raw ≥ 0),
pb = price/b0, `numpy.percentile` 5/95, winsorizes, brute-force Theil–Sen (pandas cross join, equal-x pairs dropped,
`numpy.median`), R², every residual and resid_pct, and compares with `scipy.stats.theilslopes(..., method="joint")`.

| Sector | Pairs | n_fit / n_scored | slope | intercept | R² | lo / hi | max \|Δ\| params | max \|Δ\| residual, resid_pct | scipy Δ |
|---|---|---|---|---|---|---|---|---|---|
| Financials | 3,635 | 86 / 93 | 6.41674 | −0.19002 | 0.5199 | 0.0731 / 0.4255 | 0 | 0, 0 (93 names) | 0 |
| Energy | 1,029 | 46 / 52 | 2.23752 | 0.49788 | 0.2815 | 0.0519 / 0.4769 | 0 | 0, 0 (53 names) | 0 |
| Materials, Real Estate | 2,004 / 559 | 64/75, 34/37 | as §5 | | | | 0 | 0, 0 | 0 |
| Utilities (LOW_R2) | 778 | 40 / 43 | 4.76195 | 0.26486 | 0.0147 | | 0 | no stored residuals, as spec | 0 |

**Pass** (tolerance 1e-6; all differences are exactly 0). Stored residuals exist exactly where expected: ineligible
names with data (IPCO in Energy, 8 in Materials, 4 in Real Estate) have a residual but no resid_pct, as specified.

## 3. V_RI recomputed independently (own loop, not the package; tolerance rel 1e-9)
| Case | Inputs (b0, ROE_0, r, b, price) | V_RI | MoS / up / down | max rel diff |
|---|---|---|---|---|
| G1 (prices 60/48/80) | 50, 0.14, 0.085, 0.6 | 61.12944654 (r±1pp 58.8456 / 63.5473) | HOLD / BUY / SELL values | 2.3e-12¹ |
| G3 BASE | 50, 13/96, 0.092, 0.6, 75 | 58.56722255 | −0.2191 / −0.2479 / −0.1886 | 0 |
| G6 CSM | 37.5, 0.0957702, 0.082, 1−1.7/(6/1.7), 45 | 39.47586691 | −0.1228 | 0 |
| RY.TO | 96.697, 0.17204, 0.0865, 0.57565, 288.32 | 130.8748 | −0.5461 / −0.5630 / −0.5282 | 0 |
| JPM | 132.236, 0.18671, 0.0986, 0.74689, 342.08 | 182.6687 | −0.4660 / −0.4864 / −0.4444 | 0 |
| MFC.TO | 41.681, 0.08568, 0.0865, 0.46230, 62.49 | 41.5540 | −0.3350 / −0.3590 / −0.3097 | 0 |

¹ against the spec's 12-digit printed MoS values. ROE_0 = NI/avg CE and b = 1 − DPS/EPS were also recomputed from the
payload's raw NI, CE, CSM and DPS (exact). **Pass.**

## 4. Issuer checks (`valuation crosscheck --issuer --as-of 2026-11-09`, re-run by QA: same results)
| Ticker | BVPS ours / issuer | Δ (±3%) | ROE_0 ours vs mean of 4 reported quarterly ROEs | Δ (±1.5pp) |
|---|---|---|---|---|
| RY.TO | 96.70 / 96.73 | −0.03% | 17.20% vs 17.375% (17.9, 17.2, 17.6, 16.8) | −0.17pp |
| TD.TO | 69.66 / 69.69 | −0.04% | 13.64% vs 13.70% (15.8, 14.7, 13.6, 10.7) | −0.06pp |
| BNS.TO | 64.99 / 64.99 | 0.00% | 12.31% vs 12.325% (14.1, 13.1, 11.1, 11.0) | −0.02pp |
| MFC.TO | 28.04 / 27.48 | +2.04% | — | |

**MFC CSM-adjusted b0:** 41.681 vs issuer `adjusted_bvps_reported` 41.12 → **+1.36%, pass (±3%)**. Both add the same
CSM per share (22,668M / 1,661.9M = 13.64); the 0.56 gap is our unadjusted BVPS 28.04 vs issuer 27.48, i.e. the
C$937M participating policyholders' equity Yahoo includes in common equity (937/1,661.9 = 0.564; see
`book_value-validation.md` §4). **All issuer checks pass.** Secondary Yahoo `returnOnEquity`: **not available** —
`reference_metrics` holds only priceToBook, sharesOutstanding, regularMarketPrice, bookValue, marketCap (not ingested).

## 5. Signals
r = rf + 5%: TSX 8.65% (CA10Y 3.65%), US 9.86% (US10Y 4.86%).

| Market | Model | BUY | HOLD | SELL | NO_SIGNAL | NOT_ELIGIBLE |
|---|---|---|---|---|---|---|
| TSX | A | 4 | 23 | 108 | 60 | 20 |
| US | A | 4 | 29 | 201 | 268 | 1 |
| TSX | B | 26 | 62 | 22 | 85 | 20 |
| US | B | 27 | 89 | 31 | 355 | 1 |

NO_SIGNAL reasons TSX / US — **A:** ASSET_LIGHT 23/118, PB_TOO_HIGH 18/111, NEG_BOOK 1/37, NO_TTM 10/2, NO_SECTOR 5/0,
NEEDS_CSM_INPUT 3/0 (SLF, GWO, IAG). **B:** EV_BRANCH_PENDING 62/320, NO_FIT 12/31 (Utilities), NO_ROE 8/0,
NEEDS_CSM_INPUT 3/0, NEG_BOOK 0/4.

| Sector fit | Status | n_fit | n_scored | R² | slope | intercept | lo | hi |
|---|---|---|---|---|---|---|---|---|
| Financials | OK | 86 | 93 | 0.520 | 6.417 | −0.190 | 0.073 | 0.426 |
| Real Estate | OK | 34 | 37 | 0.508 | 5.340 | 0.318 | 0.030 | 0.470 |
| Energy | OK | 46 | 52 | 0.281 | 2.238 | 0.498 | 0.052 | 0.477 |
| Materials | OK | 64 | 75 | 0.237 | 2.783 | 0.382 | 0.031 | 0.412 |
| Utilities | LOW_R2 | 40 | 43 | 0.015 | 4.762 | 0.265 | 0.035 | 0.374 |

A×B (rows A, cols B: BUY/HOLD/SELL/NO_SIGNAL/NOT_ELIG): BUY 5/0/0/3/0 · HOLD 30/7/0/15/0 · SELL 17/140/37/115/0 ·
NO_SIGNAL 1/4/16/307/0 · NOT_ELIGIBLE 0/0/0/0/21. Agreement on BUY: 5; on SELL: 37; no A BUY is a B SELL.

**All 8 Model A BUYs** (by MoS):
| Ticker | Sector | P/B | ROE_0 (raw) | MoS | MoS r+1pp | Flags | Model B |
|---|---|---|---|---|---|---|---|
| SMCI | IT | 1.17 | 29.2% | +80.4% | +72.8% | — | EV_BRANCH_PENDING |
| FIS | Financials | 0.90 | 23.0% | +78.0% | +71.2% | — | BUY +303% |
| FCR-UN.TO | Real Estate | 1.03 | 22.3% | +62.6% | +56.1% | FAIR_VALUE_BOOK, PERIOD_MISMATCH, SPOT_EQUITY | BUY +342% |
| ONEX.TO | Financials | 0.62 | 5.3% | +41.0% | +35.4% | ROE_WINSORIZED | BUY +114% |
| LNR.TO | Cons. Disc. | 0.78 | 10.6% | +38.6% | +33.1% | PERIOD_MISMATCH | EV_BRANCH_PENDING |
| APA | Energy | 1.84 | 35% (30.8%, 3y avg) | +32.8% | +27.4% | CYCLICAL, CLIPPED | BUY +78% |
| BTO.TO | Materials | 1.90 | 32.8% (3y) | +29.9% | +24.4% | CYCLICAL, PERIOD_MISMATCH | BUY +92% |
| ECHO | Comm. Svcs | 2.08 | 35% (93.7%) | +26.1% | +20.7% | CLIPPED | EV_BRANCH_PENDING |

(PIT_ESTIMATED omitted.) 5 of 8 rest on a one-off or doubtful ROE (§6d): ONEX (P/B 0.62 at ROE 5%: holding company) and
LNR are the only ones without an obvious earnings or data question.

**Top 10 Model B BUYs** (by upside; resid_pct, Model A): ALL +466% (0.00, HOLD) · FCR-UN.TO +342% (0.00, BUY) · FIS
+303% (0.01, BUY) · ARE +199%\* (0.03, SELL) · CAR-UN.TO +184%\* (0.06, HOLD) · HR-UN.TO +169%\* (0.08, SELL) · MOS
+149%\* (0.00, HOLD) · BIR.TO +142% (0.00, HOLD) · REI-UN.TO +131% (0.11, HOLD) · CPT +131% (0.14, SELL).
\* NEG_ROE_UNFITTED. Model B BUYs by sector: Financials 19, Materials 15, Energy 11, Real Estate 8.

## 6. Data anomalies (reported, not fixed)
| # | Finding (verified numbers) | Owner / next step |
|---|---|---|
| a | BCE, CCA, QBR-B, RCI-B, T (.TO) carry sector **"Communication"** (iShares label), not GICS "Communication Services" → A: NO_SECTOR; B: EV_BRANCH_PENDING (not NO_SECTOR, since the label is non-empty). | SW: map the label in universe build. |
| b | **ROE_PERIOD_MISMATCH 124 TSX / 78 US (48 US after the fix below).** TSX: all 124 have balance sheet 2026-06-30 (filed est. 08-14) and TTM to 2026-09-30 (Yahoo NI filed date capped at download date, ttm v0.1.1, while the Q3 balance sheet's estimated filed date 11-14 > as_of; P6-8). US splits into: **22 false positives** — same quarter, Yahoo labels it 09-30 while EDGAR uses the 52/53-week date (AAPL, AMD, INTC, JNJ, PFE, QCOM, … 09-25..09-27); Yahoo NI then wins on the later date (harmless: CE is the same quarter); **30 engine stricter than spec** — EDGAR has total equity at q0 filed ≤ as_of, but `_filings` drops filings without `shares_outstanding` (multi-class cover pages: META, V, MA, BRK-B, ECHO, CMCSA, …), so CE_end falls back to Yahoo Q2; spec §Data mapping asks only for CE at q0; **26 genuine** (Yahoo NI newer than any balance sheet, 10-Q not out). | SW: (1) **fixed in this PR**: CE lookups accept equity-only filings (US mismatches 78 → 48; regression tests `tests/unit/test_signals_engine.py`); (2) open: treat period ends ≤ 7 days apart as one quarter (22, needs a spec change); TSX: P6-8. |
| c | **ROE_SPOT_EQUITY 49 TSX** (+3 US): no balance sheet 350–380 days before q0 in Yahoo's ~5-quarter history → CE_end alone. | Known data limit; P6 backlog (deeper history). |
| d | **One-off ROE.** ECHO: NI TTM 9.89B (Q2 2026 +8.46B one-off after −12.8B in Q3 2025) on avg CE 10.6B → ROE_raw 93.7%, clipped 35%, still BUY. FCR-UN.TO: NI TTM 1.08B incl. Q4 2025 0.85B (IFRS fair-value gains) → 22.3% for a REIT, spot equity. FIS: Q1 2026 NI 2.37B (vs ~0.3B/q) → 23%. CPT: Q3 2026 NI 0.90B (vs ~0.1B/q). APA: 3y history (27.2%, 61.6%) → mean 39.9% → 35%; without the 61.6% year ≈ 29%. **SMCI:** CE 6.52B (2025-09-30) → 16.07B (2026-09-30), EDGAR and Yahoo agree on total equity to the dollar (Q2 jump 7.58B → 14.48B, shares 601M → 657M, NI TTM 3.29B); but **Yahoo tags preferred equity 4.23B at 2026-06-30** (0 before), EDGAR none. If real, common equity is overstated by ~4.2B (BVPS 24.4 → ~18). Unresolved. | AN/PM: one-off gains need a rule (e.g. flag when one quarter > 50% of TTM NI). SW: check SMCI 10-Q for preferred/mezzanine equity. |
| e | **ALL Model B +466%** (+489% before the §6b fix). ROE_raw 41.4% (NI TTM 11.5B / avg CE 27.9B) sits just below hi 42.6%, so not winsorized; fitted ln P/B = −0.190 + 6.417 × 0.414 = 2.46 → fitted P/B 11.8 vs actual 2.08. The Financials high-ROE tail is MA (251%), MCO (80%), AMP (72%), V (65%), AON (43%): payment networks, ratings, brokers/asset managers with P/B 7–100, which steepen the slope. A P&C insurer is extrapolated on that line. | AN/PM: sub-industry peers or a lower hi cap within Financials. |
| f | **7 Model B BUYs are loss-makers** scored at the sector 5th-percentile ROE (NEG_ROE_UNFITTED): ARE, CAR-UN.TO, HR-UN.TO (Real Estate, roe_w 3.0%), MOS, WFG.TO (Materials, 3.1%), VET.TO (Energy, 5.2%), LSPD.TO (Financials, 7.8%); upside +39% to +199%. The known limitation in the spec. | PM decision (e.g. cap at HOLD). |
| g | **Model A SELL share 309/718** (84% of the 369 names with a signal; TSX 80%, US 86%); SELL P/B median 2.40 (p10 1.45, p75 2.95). With T = 10, linear fade and no terminal value, V_RI/B_0 = 1.00 at ROE = r, 1.20 at r + 5pp, 2.2–2.3 at the 35% cap, so SELL triggers above P/B ≈ 1.25 at ROE = r, ≈ 1.5 at r + 5pp, and **any name above P/B ≈ 2.9 is SELL whatever its ROE** (86 of the SELLs). RY, TD, BNS, JPM are all SELL (MoS −44% to −55%). A property of the approved parameters, not tuned. | PM / Phase 8 backtest. |
| h | **Utilities LOW_R2** (R² 0.015, n_fit 40) → all 43 scored Utilities NO_FIT; no Model B signal for utilities. | As designed; PM aware. |
| i | **NO_TTM 12 names** (reason): AAUC, AIF, CHP-UN, CJT, CTC-A, ENB, HWX, PXT, SCR, X (.TO), FDX, SKYD. 6 more carry the flag behind an earlier reason (CRT-UN, TSU, VYLR not eligible; FDXF NEG_BOOK; FERG, JWEL ASSET_LIGHT). | P6 backlog (Yahoo short history / semi-annual reporters). |

**Robustness legs:** 0 names with MoS ≥ 0.25 but mos_up ≤ 0 and 0 with MoS ≤ −0.20 but mos_down ≥ 0, so r ± 1pp
changed no signal in this run (as the spec's property note expects with T = 10).

## 7. Limitations
- **No backtest yet (Phase 8):** BUY/HOLD/SELL thresholds and both models are unvalidated against returns; the counts
  above say nothing about predictive value.
- Survivor bias: n/a until the Phase 8 point-in-time universe; today's index members only.
- TSX point in time is estimated (Yahoo filed dates = period end + 45 days, NI capped at download date): 215/215 TSX
  and 121 US names are PIT_ESTIMATED.
- CSM is manual: only MFC filled; SLF, GWO, IAG are NEEDS_CSM_INPUT. The current CSM is used at both ROE ends.
- Model B covers book sectors only (EV/Sales branch Phase 10); 382 names EV_BRANCH_PENDING.
