# Phase 8 validation (backtest harness, Models A and B), as of 2026-11-10

Owner: QA (P8-3) · Spec: [`docs/models/backtest.md`](../models/backtest.md) v0.1.1 · Per-model reports:
[`backtest_model_a.md`](backtest_model_a.md), [`backtest_model_b.md`](backtest_model_b.md) · DB: `data/backtest.duckdb`
(not in git; rebuild with `valuation backtest build`) · Full generated report: `reports/backtest/backtest_US_<run>.md`.
Screening-signal validation only; not investment advice. **All US results are a survivor-biased upper bound (§3).**

## 0. Summary
| Model | Result | 12m mean IC (NW t) | 12m spread, years > 0 | 12m mean spread | BUY hit / SELL hit (12m) | Turnover (label / BUY set) |
|---|---|---|---|---|---|---|
| **A** residual income | **FAIL** (SPREAD_YEARS, IC) | 0.0067 (0.13) | 3/10 | −1.37% | 57.1% / 50.6% | 7.0% / 18.1% |
| **B** P/B ~ ROE | **FAIL** (SPREAD_YEARS, IC) | −0.0539 (−0.86) | 4/10 | −2.15% | 49.9% / 43.6% | 9.6% / 18.3% |
| A-book (A in book sectors) | reference | −0.0005 (−0.01) | 3/10 | −1.59% | 59.0% / 49.6% | 6.5% / 17.9% |
| Baseline low P/B (book sectors) | reference | −0.0367 (−0.55) | 3/10 | −2.30% | 46.5% / 44.9% | 6.2% / 9.4% |

Bar: spread > 0 in ≥ 60% of years, mean 12m IC > 0.03 with t > 2, and the same sign in the holdout. Neither model
clears either in-sample leg, so the holdout leg can't change the verdict. Per proposal §6 and PM plan Phase 10, both
models stay **"unvalidated"** in the report, and the vote stays off. The only consistent positive is Model A's BUY
bucket: 12m excess +7.2% and hit rate 57% over 1,306 observations (about 11 a month). It is driven by BUYs whose ROE
is not clipped (§5 Q2).

## 1. Runs and the holdout lock
| Run | Command | Dates | Notes |
|---|---|---|---|
| US in-sample | `valuation backtest run` / `report` | 154 month-ends 2014-01-31 … 2026-10-31 run; metrics on the 118 in-sample dates (… 2023-10-31) | Report run `b97630c9f771` (identical to the earlier runs `cb7eb3c56ada` and `266fd65523a9`), thresholds hash `203efff963f52529…` (Phase 7 constants + liquidity.yaml, unchanged) |
| US holdout | not run | 2023-11-30 … 2026-10-31 (36) | **Kept locked on purpose.** Both models already fail two in-sample legs, so the holdout can't turn a FAIL into a PASS. It stays unspent for the first frozen-threshold re-test of a PM-revised model (§6). `bt_runs` has no `holdout` row. |
| TSX supplementary | `run --market TSX` / `report --market TSX` | 18 dates from 2025-05-31; Model A/B signals only from 2026-06-30 | "Current constituents, survivor-biased". Too short for any metric (§7). |

Signals on every date come from the unchanged live `run_signals`, using prices dated ≤ d and facts filed ≤ d. Parameters
were never changed. Signal counts per year for each model are in the per-model reports.

## 2. Tests and reproducibility
- `uv run pytest -q`: **793 passed** = 568 existing + 225 new Phase 8 spec tests. QA wrote them from the spec without
  reading `src/valuation/backtest/`. `uv run ruff check src tests`: clean.
  - `test_backtest_membership_spec.py` (42): golden M1/M2, plus a fixture shaped like the Wikipedia table.
    Spot checks of real dates: TSLA (2020-12-21), PLTR (2024-09-23), SMCI (2024-03-18) and UBER (2023-12-18) are
    members on D and not on D−1. SIVB (2023-03-15), FRC (2023-05-04), AIV and WHR are members on D−1 and not on D.
    The real-data intervals agree: TSLA from 2020-12-21; SIVB to 2023-03-14; FRC to 2023-05-03; SMCI from
    2024-03-18; WHR to 2024-03-17; PLTR from 2024-09-23; AAL to 2024-09-22.
  - `test_backtest_metrics_spec.py` (159): every golden value in spec §8 (R1 panel, N1 Newey–West, B1 baseline, P1
    pass bar), `allowed_dates` and permutation properties, the SIC table, and the thresholds hash.
  - `test_backtest_engine_spec.py` (24): split handling (shares × ratio, dividends ÷ ratio, as-traded prices), and
    the **look-ahead tests**. A fact filed after d is ignored; a price after d is ignored; membership on d is used,
    not today's; a pre-lagged estimated fact is invisible before its stored date; rates and splits after d are
    ignored.
- **Deterministic:** I recomputed 2018-09-30 from scratch and it matched the stored 437 rows exactly. Three report
  runs gave identical metrics.
- AN's spec review caught a sign error in my uncommitted dividend split adjustment (× instead of ÷) before QA's tests
  ran. QA's review led to spec 0.1.1: a removal dated exactly on the start date yields no interval, and the split
  window ends at the price date.

## 3. Survivorship gap and coverage (US)
| Year | Members (avg) | With prices | Missing prices | % missing | No usable balance sheet¹ | Not eligible² |
|---|---|---|---|---|---|---|
| 2014 | 505.2 | 381.7 | 123.5 | 24.4% | 43.3 | 53.7 |
| 2016 | 508.0 | 410.9 | 97.1 | 19.1% | 43.3 | 55.0 |
| 2018 | 506.4 | 434.7 | 71.8 | 14.2% | 37.0 | 43.0 |
| 2020 | 506.0 | 458.3 | 47.7 | 9.4% | 33.3 | 50.0 |
| 2022 | 504.7 | 474.5 | 30.2 | 6.0% | 23.8 | 29.8 |
| 2023 | 504.0 | 482.8 | 21.2 | 4.2% | 23.4 | 30.1 |
| 2026 | 503.2 | 499.2 | 4.0 | 0.8% | 23.5 | 27.1 |

¹ Priced members without a filing that has both equity and a share count filed by d, so no price/book and E3 fails.
² Includes ¹. Genuinely illiquid or small names are the difference: about 10 per date in 2014, about 4 in 2024.
Most fail E3 (US$5B cap, nominal).

- **Removed names:** 258 tickers left the index since 2013. **158 (61%) have no usable Yahoo prices**: 129 return
  404 (delisted, e.g. TWTR, XLNX, CELG, ATVI, FRC, SIVB, CTXS), 21 have no bars inside their membership (the ticker
  now belongs to another company), and 8 return no data. Over all dates, **8,641 of 77,862 member-dates (11.1%)**
  are missing. The gap is largest early (24% in 2014), and those missing names are mostly decliners and acquired
  firms. That makes every result an **upper bound**: the missing losers would most likely have sat in the BUY buckets
  of value-type models.
- 103 removed names have prices (mostly demoted to the MidCap, still listed). CIKs come from an EDGAR name match that
  must also list the ticker; 6 matches were rejected (CNX → the 2017 CONSOL spin-off, FTI, OI, …). Sectors come from
  SIC (`SECTOR_FROM_SIC`, 83 names; Corteva's SIC 0100 isn't in the spec table → NO_SECTOR).
- **Fundamentals gaps (data anomalies, not fixed):**
  - **Multi-class filers have no share count in EDGAR companyfacts.** The cover-page count is per class, and the
    non-dimensional facts omit it. META, EL, MKC, STZ, LEN, HRL, TSN, RL, NWSA, TAP, UHS, … never get a balance sheet
    in the backtest. The live run covers them through Yahoo shares, which only go back about 5 quarters.
  - **Successor CIKs (holding-company reorganisations)** have no predecessor history: XOM (new CIK 0002115436, 2026),
    BLK (2024), DIS (2019), AVGO (2018), LIN (2018; its first filings are the pre-merger shell with 25,000 shares).
  - **EDGAR cover pages reporting 0 shares** (VEEV, BE, CRWD, DDOG, HOOD early filings) crashed the live engine with a
    division by zero. A share count ≤ 0 is now treated as missing (engine guard; this also affects the live run).

## 4. Results per model (US, in-sample 2014-01 … 2023-10, 12m unless stated)
- **Model A:** IC 0.0067 (t 0.13; 3m −0.005, 6m −0.006). Spread −1.4%, positive only in 2016, 2020 and 2023 (value
  years). BUY excess +7.2% (hit 57%); SELL excess +0.05% (hit 50.6%). About 146 SELLs per month against 11 BUYs and 38
  HOLDs. Turnover is low: 7% of labels change a month, and the SELL set is very stable (6.5%).
- **Model B:** IC −0.054 (t −0.86; negative at 3, 6 and 12m). Spread −2.2%. SELL names outperformed their sector by
  3.5% (SELL hit 43.6%). The sign is wrong: "dear for its ROE" kept getting dearer in 2014–2019, then reversed in
  2020–2021.
- **A-book vs the low-P/B baseline:** the ROE adjustment beats plain P/B everywhere (IC −0.0005 vs −0.037; BUY excess
  +7.6% vs +0.7%; spread −1.6% vs −2.3%), but neither is positive. Plain low P/B lost money relative to sectors in this
  sample, so "better than low P/B" is a low bar.

## 5. Phase 7 open questions, answered
| # | Question (phase7-validation §6) | Answer from the backtest (in-sample, 12m) |
|---|---|---|
| 1 | Is Model A's heavy SELL skew (anything above P/B ≈ 2.9) costly? | **Yes, mildly, in the high-P/B part.** All SELLs perform like their sector (mean excess +0.05%, SELL hit 50.6%: no information). SELLs above P/B 2.9 (4,689 observations) **beat** their sector by +2.1% (SELL hit 47.7%). SELLs at P/B ≤ 2.9 lag by −0.7% (hit 51.6%). The forced SELL on high-P/B franchises is wrong-way. |
| 2 | Do Model A BUYs built on one-off ROE (ROE_CLIPPED) underperform? | **Yes.** Clipped BUYs: 118 observations, mean excess +1.7%, median −2.8%, hit 44%. Unclipped BUYs: 1,188 observations, +7.7%, median +4.7%, hit 58%. The gap appears at 6–12 months. |
| 3 | Do Model B's loss-maker BUYs (NEG_ROE_UNFITTED) underperform? | **Inconclusive, small n.** 89 observations: mean excess +3.6% (a few big winners) but median −1.4% and hit 48% vs 50% for the other 2,583 BUYs. Capping them at HOLD would barely change Model B's results. |
| 4 | Does the Financials-slope distortion (payments, ratings) hurt Model B? | **Yes.** Financials as specified: IC −0.078 (t −1.05), spread −3.7%, SELL hit 37.8%. The spec's sub-industry split (≥ 12 fit names per group) **never takes effect**: no S&P 500 Financials sub-industry reaches 12 on an in-sample date, so it is identical to the pooled fit. The extra diagnostic leaves Transaction & Payment Processing and Financial Exchanges & Data out of the fit: IC −0.009, spread −0.9% (same subset as specified: IC −0.005, spread −2.6%). Removing the distortion brings Financials to about zero; it doesn't make it positive. |

## 6. Proposals for the PM (no parameter was changed; nothing here was tuned on the holdout)
1. **Keep A and B "unvalidated"** (proposal §6) and the Phase 10 vote off. Don't start using their signals for
   ranking. Phase 9 (Model C) can go ahead; the harness is ready for it.
2. **Model A SELL rule:** the SELL side carries no information, and the forced SELLs above P/B ≈ 2.9 are wrong-way.
   Options for AN: report SELL as information only (HOLD in the screen); or revisit the 10-year linear fade with no
   terminal value, which is the cause per §6g. A revised rule must be fixed *before* it is tested on the holdout.
3. **One-off ROE (P7-11):** the backtest supports a dominant-quarter rule. Clipped-ROE BUYs underperform unclipped
   BUYs by about 6pp at 12m.
4. **Model B Financials (P7-14):** move payment, exchange and data names out of the book branch (they belong in the
   EV branch). Sub-industry splitting with MIN_FIT 12 can't work on S&P 500 Financials, though it might when pooled
   with TSX. Model B's sign problem isn't limited to Financials (12m IC −0.054 overall), so that change alone won't
   make B pass.
5. **Data (Phase 5 / backlog):** a delisted-price source (e.g. Stooq, a new host) to remove the survivorship bias; a
   predecessor-CIK map (XOM, BLK, DIS, AVGO, LIN); a share-count fallback for multi-class EDGAR filers (e.g.
   `WeightedAverageNumberOfSharesOutstandingBasic`, which is a spec change); inflation-indexed or relative E3 thresholds
   for history (now nominal).
6. **Holdout:** unspent. Use it once, for the first PM-approved revision with frozen thresholds.

## 7. TSX supplementary ("current constituents, survivor-biased")
TSX fundamentals come only from Yahoo, about 5–8 quarters, plus the 30-day backtest lag. Model A/B signals exist
only from 2026-06-30 (5 dates, 32–112 names with a label), and 3-month returns exist only for the first 2 of them.
Model A 3m IC 0.13 and Model B 0.10 over **2 dates**; that is meaningless. The low-P/B baseline, which needs no TTM,
has 11 dates: 3m IC 0.02, 12m n/a. **No conclusion is possible for the TSX.** Point-in-time TSX membership is out of
scope (P8 brief).

## 8. Other data anomalies (reported, not fixed)
| Finding | Effect / owner |
|---|---|
| The Wikipedia changes table **moved** on 2026-08-11 from "List of S&P 500 companies" to "Historical components of the S&P 500". | Harness reads the new article; the constituents page still supplies today's list. |
| Inconsistent change rows, skipped and logged: FB added 2013-12-23 (renamed META, so META counts as a member from 2013-01-01), KORS (now CPRI), "ALLE\|" (stray character), AGN 2015-03-23 (removed twice). | Affects 2013 only, before the first rebalance. Renames listed as add NEW / remove OLD stay two tickers (spec D5), which inflates "missing prices" slightly. |
| Yahoo `range=max` returns **monthly** bars. | The harness uses `period1/period2` with `interval=1d`. |
| Wikipedia's API returned HTTP 429 (rate limit) for about 15 minutes. | Retried with backoff; pages are cached in `data/raw/wikipedia/<day>/`. |
| Current GICS is applied to all history (the 2018 Communication Services and 2023 payments → Financials reclassifications are not replayed); SIC sectors for removed names are coarse. | Limitation (spec §6). |
| The live first load (`valuation ingest` for 503 S&P names) took 36 min; DuckDB `executemany` upserts are the bottleneck. | The backtest builder uses bulk DataFrame inserts. Backlog item for the live pipeline. |

## 9. Runtime
| Step | Time |
|---|---|
| `backtest build` (Wikipedia, 994 Yahoo chart calls at about 4 req/s, EDGAR companyfacts for 587 CIKs, rates, FX) | 7m15s first run; 65s from cache |
| `backtest run --market US` (154 dates × about 450 names) | 35.4 CPU-min (13.8 s/date); **about 9.5 min wall** on 4 worker processes; resumable per date (`data/backtest_parts/`) |
| `backtest report --market US` (4 models × 3 horizons + diagnostics) | about 15 s |
| `backtest run --market TSX` (18 dates, pooled with US for Model B) | 3m22s |
