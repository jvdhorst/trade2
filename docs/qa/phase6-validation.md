# Phase 6 validation (data batch 1 + eligibility), as of 2026-11-09

Owner: QA/SW · Universe: TSX Composite (215) + S&P 500 (503) · DB: `data/valuation.duckdb` (not in git)

## Coverage (P6-1)
| Universe | Valued | Peer fair value | NO_TTM (NI to common) | TTM source |
|---|---|---|---|---|
| TSX Composite | 215/215 (100%) | 204 | 14 | Yahoo 201 |
| S&P 500 | 503/503 (100%) | 466 | 5 | EDGAR 416, Yahoo 82 |

Yahoo wins over EDGAR for 82 US names because the quarter is already on Yahoo (earnings release) while the 10-Q
isn't on EDGAR yet; on equal quarter ends EDGAR wins. The remaining TSX `NO_TTM` names have a quarter missing from
Yahoo's free series (it returns only ~5 quarters) or report semi-annually.

## TTM NI to common vs filings (P6-3, ±0.5%)
| Ticker | Ours (TTM to) | Reference | Diff |
|---|---|---|---|
| AAPL | 132,175M (2026-09-26) | 10-K FY2026 `NetIncomeLoss` 132,175M | 0.00% |
| JPM | 64,526M (2026-09-30) | 10-K FY2025 − 9M (55,681 − 42,991) + 10-Q Q1–Q3 2026 (16,148 + 20,752 + 14,936) | 0.00% |
| JPM | | Yahoo `trailingNetIncomeCommonStockholders` 64,527M (independent source) | −0.002% |
| RY.TO | 22,187M (2026-07-31) | Yahoo `trailingNetIncomeCommonStockholders` 22,187M | 0.00% |

Golden tests: `tests/unit/test_ttm_spec.py` (G1–G7, incl. Q4 = FY − 9M and FY − 3Q) and
`tests/unit/test_phase6_fixtures.py` (trimmed real AAPL/JPM companyfacts).

## DPS_TTM (P6-4), reference set vs Yahoo `trailingAnnualDividendRate` (secondary)
| Ticker | Ours | Yahoo | Diff | Explanation |
|---|---|---|---|---|
| RY.TO | 6.800 | 6.580 | +3.3% | Yahoo lags one payment; ours = 1.64 + 1.64 + 1.76 + 1.76 (ex-dates Jan–Oct 2026) |
| TD.TO | 4.400 | 4.330 | +1.6% | same (dividend raised) |
| BNS.TO | 4.480 | 4.440 | +0.9% | same |
| MFC.TO, CNQ.TO, JPM, AAPL | = | = | 0.0% | |
| ENB.TO | 3.853 | 3.860 | −0.2% | |
| ABX.TO, NTR.TO, BN.TO | 1.305 / 3.072 / 0.374 | 0.65 / 2.19 / 0.26 | +40–100% | USD-declared dividends: the `.TO` chart gives them converted to CAD (trading currency, consistent with the price); Yahoo's rate is in USD. ABX also paid a special 0.575 in Feb 2026 |
| HD | 9.290 | 4.630 | +100.6% | Ours = 2.30 + 3 × 2.33 (quarterly). Yahoo's value is stale |
| SHOP.TO, BRK-B | 0 | 0 | | no dividend |

**Open (P6-7):** the plan's acceptance is issuer-declared dividends ±2%. That needs issuer sources (as in
`issuer_sources.csv`); not done in this phase.

## Eligibility removal (P6-6), thresholds `config/liquidity.yaml` (unchanged, not tuned)
| Market | Not eligible | E1 | E2 (MDTV) | E3 (cap) | E4 (price) | E5 (active) | E6 short history (Model C only) |
|---|---|---|---|---|---|---|---|
| TSX | 20/215 (**9.3%**) | 0 | 19 | 1 (AP-UN) | 0 | 0 | 0 |
| US | 1/503 (**0.2%**) | 0 | 1 (VYLR, < 63 sessions listed) | 0 | 0 | 0 | 4 (FDXF, HONA, Q, VYLR) |

Both are **below** the expected ranges (<10% S&P, 15–25% TSX), not above, so no `REVIEW THRESHOLDS` notice. Flagged
for PM: the TSX bar (C$5M MDTV) removes fewer names than the proposal expected. The thresholds are not tuned here.
TSX names failing E2 sit at C$1.9–5.0M MDTV (e.g. IAU, BBUC, RCH, CURA, BHC). Median MDTV: TSX C$23M, US US$346M.

## Timings (P6-2, acceptance: daily run ≤ 5 min for 718 tickers)
| Run | Time |
|---|---|
| Daily ingest, 718 tickers, all caches cold (spark + 718 five-day charts + frames + rates) | **251 s** |
| First load S&P 500 (profiles, EDGAR, old code) | 15 min |
| History backfill (2y charts) + fundamentals refresh: TSX / S&P 500 | 551 s / 1757 s |
| `valuation value` per universe | < 1 min |
