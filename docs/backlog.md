# Backlog (PM)

Status: `todo` · `doing` · `done` · `blocked`

## Phase 0: Foundation
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| P0-1 | Repo scaffold: `pyproject.toml`, ruff, pytest, `.gitignore` | SW | `uv run pytest` and `uv run ruff check` pass | done |
| P0-2 | GitHub Actions CI (lint + offline tests) | SW | Workflow runs on push/PR | done |
| P0-3 | DuckDB schema + `valuation init` | SW | Creates all tables; re-running is a no-op | done |
| P0-4 | Environment allow-list for data hosts | PM | Yahoo, EDGAR, BoC return 200 from the session | done |

## Phase 1: Ingestion
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| P1-1 | Reference universe CSV (14 tickers, the ~2% initial-testing subset) | AN/SW | Loaded into `securities` | done |
| P1-2 | Full universe CSVs: TSX Composite + S&P 500 | SW | ≥ 700 rows, exchange + currency set | done (215 + 503, `valuation universe`) |
| P1-3 | Yahoo provider: price, shares, profile, balance sheet | SW | Raw JSON cached; parsed rows in DB | done |
| P1-4 | EDGAR provider: companyfacts → `fundamentals` with `filed_date` | SW | US reference tickers have equity + shares | done |
| P1-5 | BoC provider: USD/CAD daily | SW | `fx_daily` populated | done |
| P1-6 | Parser tests on recorded fixtures | QA | Offline, deterministic | done |
| P1-7 | Coverage / data-quality report | QA/SW | Missing fields, stale prices, unit mismatches listed | done (`coverage_book_value.csv`) |
| P1-8 | Full TSX Composite ingest run (215) | SW | ≥95% valued | done (100%) |
| P1-9 | S&P 500 ingest run (503) | SW | ≥95% valued; run on request | done (P6-1: 503/503) |
| P1-11 | Faster ingestion: batch prices, timeseries fundamentals, EDGAR frames, refresh policy, threads | SW/QA | Facts identical to the old route; daily run ≤2 min | done |
| P1-10 | QA triage of Yahoo outliers (BIP/BEP units, POW, CLS, AAUC, MDA, GFL, DML, BAM) | QA | Each explained or fixed | todo |

## Phase 2: Book value vs price
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| P2-1 | Model spec with worked examples | AN | `docs/models/book_value.md` | done |
| P2-2 | Golden + property + look-ahead tests from spec | QA | `tests/unit/test_book_value_spec.py` | done |
| P2-3 | Implement `valuation.models.book_value` | SW | QA tests green | done |
| P2-4 | `valuation value` + `valuation report` → CSV + Parquet | SW | `reports/<date>/screen.{csv,parquet}` | done |
| P2-5 | External cross-check vs Yahoo `bookValue` / `priceToBook` | QA | Results and explained misses in `docs/qa/book_value-validation.md` | done (11/14 pass; TD, BNS, MFC open → P2-6) |
| P2-6 | Issuer check: RY, TD, BNS, MFC against supplementary packs | QA | BVPS within ±3% | done (automated, `crosscheck --issuer`) |
| P2-7 | Spec v0.2.0: GICS sector for peers | AN/QA/SW | Tests green | done |
| P2-9 | Rank only in P/B-meaningful sectors; others unranked | AN | Spec v0.3.0 + tests | superseded by P7-5 (Model A sector applicability, Model B book sectors) |
| P2-8 | Non-common equity input (insurers' participating policyholders' equity) | AN | Spec + source identified | todo (MFC CSM-adjusted book is +1.4% vs issuer because of it) |

## Phases 6–10: BUY / HOLD / SELL models
Plan: [`docs/pm_plan_buy_hold_sell.md`](pm_plan_buy_hold_sell.md) · Proposal: [`docs/models/proposals/buy_hold_sell.md`](models/proposals/buy_hold_sell.md)

| ID | Task | Owner | Status |
|---|---|---|---|
| P6-0 | AN proposal on main | PM | done (this PR) |
| P6-1 | S&P 500 into the main DB | SW | done (503/503 valued; TSX 215/215) |
| P6-2 | 13-month price/volume history + daily volume | SW | done (spark has no volume → chart API; 2y backfill; daily run 251 s for 718) |
| P6-3 | Quarterly NI to common + TTM engine | SW/QA | done (AAPL/JPM TTM exact vs 10-K/10-Q; spec `docs/models/ttm.md`) |
| P6-4 | Dividends per share | SW | done (DPS_TTM; vs Yahoo checked, issuer ±2% check open → P6-7) |
| P6-5 | Risk-free rates (BoC 10y, FRED DGS10) | SW | done (`rates_daily`, 2013→) |
| P6-6 | Eligibility spec + filter | AN/QA/SW | done (TSX 9.3% removed, S&P 0.2%) |
| P6-7 | DPS_TTM vs issuer-declared dividends for the reference set (±2%) | QA | todo |
| P6-8 | Yahoo balance-sheet filed date: apply the download-date cap used for NI (ttm.md v0.1.1) | SW | todo |
| P6-9 | Report files per universe (a second `value` run on the same day overwrites the first) | SW | todo |
| P7-1 | Specs `residual_income.md` (A) and `peer_regression.md` (B book branch) | AN | done (proposal goldens reproduced: V_RI 61.13 / 58.85, fitted P/B 1.935, FV 77.39, +38.2%) |
| P7-2 | QA tests from spec, before code (incl. Theil–Sen vs scipy, look-ahead, CSM, report) | QA | done (335 new tests) |
| P7-3 | `models/residual_income.py`, `models/peer_regression.py`, signal mapping | SW | done (tests green) |
| P7-4 | External checks: issuer ROE ±1.5pp, regression reproduced, `docs/qa/phase7-validation.md` | QA | done (RY/TD/BNS ROE within 0.2pp) |
| P7-5 | Report v2 `screen_signals.{csv,parquet}` with disclaimer, `signals` table | SW | done |
| P7-6 | Insurer CSM via manual `universe/insurer_csm.csv` | QA/human | MFC done; **SLF, GWO, IAG need human input** (NO_SIGNAL / NEEDS_CSM_INPUT) |
| P7-7 | TSX sector label "Communication" (iShares) ≠ GICS "Communication Services": 5 names get NO_SECTOR | SW | todo |
| P7-8 | PM decision: Model B BUY on loss-makers scored at the 5th-pct ROE (NEG_ROE_UNFITTED, 7 BUYs) | PM/AN | backtest: inconclusive (89 obs, median −1.4%, hit 48%) |
| P7-9 | PM/backtest: Model A gives SELL on 309/718 (no terminal value, T = 10); review only after Phase 8, no tuning | PM | backtest done: SELLs ≈ sector (hit 50.6%), P/B > 2.9 SELLs beat sector +2.1% → P8-7 |
| P7-10 | Fill SLF/GWO/IAG CSM after tax each quarter (issuer SIPs) | human | todo |
| P7-11 | One-off gains drive 5 of 8 Model A BUYs (ECHO, FIS, FCR-UN, APA, SMCI): rule for a dominant quarter (AN) | AN/PM | backtest supports it: clipped BUYs hit 44% vs 58% → P8-7 |
| P7-12 | SMCI: Yahoo tags US$4.23B preferred at 2026-06-30, EDGAR none; read the 10-Q | QA | todo |
| P7-13 | 52/53-week filers: Yahoo vs EDGAR quarter ends 1–5 days apart give 22 false ROE_PERIOD_MISMATCH (spec change) | AN/SW | todo |
| P7-14 | Model B Financials slope steepened by payments/ratings/brokers (MA, MCO, V, AMP, AON): ALL +466%; sub-industry peers? | AN/PM | backtest: hurts (Fin IC −0.078); sub-industry split never reaches 12 names; excluding payments/exchanges → IC ≈ 0 → P8-7 |
| P8-0 | Spec `docs/models/backtest.md` (calendar, holdout lock, metrics, pass bar, goldens) | AN | done (v0.1.1 after QA review) |
| P8-1 | PIT S&P 500 membership from Wikipedia (changes table moved to "Historical components of the S&P 500") | SW/QA | done (792 intervals, 503–508 members/date; real add/remove dates tested) |
| P8-2 | Backtest DB + engine: as-traded prices 2012→, splits, EDGAR history, live run_signals per month-end, resumable; look-ahead tests | SW/QA | done (225 spec tests; 154 dates in ~9.5 min wall) |
| P8-3 | Run + report: `docs/qa/backtest_model_a.md`, `backtest_model_b.md`, `phase8-validation.md` | QA | done: **A FAIL, B FAIL** (in-sample; holdout kept locked) |
| P8-4 | Delisted-price source (e.g. Stooq, new host) to remove survivorship bias: 158/258 removed names lack prices (11% of member-dates) | PM/SW | todo |
| P8-5 | Predecessor-CIK map for reorganised filers (XOM, BLK, DIS, AVGO, LIN) | SW | todo |
| P8-6 | Share count for multi-class EDGAR filers (META, EL, MKC, STZ, LEN, HRL, TSN, …): spec a fallback (e.g. weighted-average basic shares) | AN/SW | todo |
| P8-7 | PM decision on the backtest proposals (phase8-validation §6): Model A SELL rule, one-off ROE rule (P7-11), Model B Financials payments/exchanges (P7-14) | PM/AN | todo |
| P8-8 | Live first load: bulk upserts instead of `executemany` (36 min for 503 names) | SW | todo |
| P8-9 | Historical E3/E2 thresholds are nominal (US$5B cap fails ~10 names/date in 2014); consider indexed/relative thresholds for history | AN/PM | todo |
| P9 | Data batch 2 + Model C composite | AN/QA/SW | todo |
| P10 | Model B EV/Sales branch + vote | AN/QA/SW | todo |

## Decisions log
- 2026-11-09: initial testing uses a ~2% subset of the universe (14 tickers, `universe/reference.csv`).
  The peer fair value is therefore mostly FEW_PEERS until P1-2.
- 2026-11-09: data provider choice deferred; free sources behind a parse/fetch split.

- 2026-11-09: issuer filings are the primary reference for financials; Yahoo `bookValue` is secondary
  (wrong for TD, BNS, MFC).

- 2026-11-09: expanded to the full TSX Composite (user request). S&P 500 not yet ingested.

- 2026-11-09: BUY/HOLD/SELL plan approved with PM defaults: FRED host allowed, AN's parameters
  as the starting config (no tuning on the holdout), a survivor-biased US backtest accepted as a stated
  limitation, and one session plus one PR per phase.

- 2026-11-09: Phase 6. History backfill uses `range=2y`, not 13 months (13mo returns ~272 sessions, E6 needs 273).
  Eligibility removal rates came in at TSX 9.3% / S&P 0.2%, below the expected 15–25% / <10%. Reported to PM,
  thresholds not tuned. FRED only answers curl-style User-Agents (documented in `providers/rates.py`).

- 2026-11-09: Phase 7. Model A + Model B book branch, report v2 (no vote). Parameters as approved, not tuned.
  ROE_PERIOD_MISMATCH on 124 TSX / 78 US names traces back to P6-8 (TSX balance-sheet filed date) and to Yahoo NI
  being newer than EDGAR's balance sheet for US names; flagged, not changed.
- 2026-11-09: AN's 10 additional models proposal (`docs/models/proposals/additional_models.md`) accepted
  as a proposal. Only the new document is taken; the planning-doc edits on `claude/an-additional-models`
  are dropped. Its shortlist (H shareholder yield, J EBIT yield + ROC, G Altman Z'' veto) is for after Phase 10.
- 2026-11-09: insurers without a sourced CSM (SLF, GWO, IAG) stay `NO_SIGNAL` / `NEEDS_CSM_INPUT` (user
  decision). No manual inputs will be added for now.

- 2026-11-10: Phase 8. Backtest harness built (own DB `data/backtest.duckdb`). US in-sample 2014-01…2023-10:
  Model A FAIL (12m IC 0.007, t 0.13, spread > 0 in 3/10 years), Model B FAIL (IC −0.054, t −0.86, 4/10). Both stay
  "unvalidated"; vote stays off. Holdout (last 36 month-ends) kept locked for the first frozen-threshold re-test of a
  revised model. Results are a survivor-biased upper bound (158/258 removed names lack prices). No parameter changed.

## Next up (PM selection)
1. Phase 6 (data batch 1 + eligibility filter). P2-9 sector gating is superseded by P7-5.
2. Phase 3 spec (AN): peer multiples, including an insurer-appropriate measure (adjusted BV with CSM).
3. P2-8 non-common equity input.
