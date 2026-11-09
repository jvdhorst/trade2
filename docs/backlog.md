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
| P1-9 | S&P 500 ingest run (503) | SW | ≥95% valued; run on request | todo |
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
| P2-9 | Rank only in P/B-meaningful sectors; others unranked | AN | Spec v0.3.0 + tests | todo |
| P2-8 | Non-common equity input (insurers' participating policyholders' equity) | AN | Spec + source identified | todo |

## Phases 6–10: BUY / HOLD / SELL models
Plan: [`docs/pm_plan_buy_hold_sell.md`](pm_plan_buy_hold_sell.md) · Proposal: [`docs/models/proposals/buy_hold_sell.md`](models/proposals/buy_hold_sell.md)

| ID | Task | Owner | Status |
|---|---|---|---|
| P6-0 | AN proposal on main | PM | done (this PR) |
| P6-1 | S&P 500 into the main DB | SW | todo |
| P6-2 | 13-month price/volume history + daily volume | SW | todo |
| P6-3 | Quarterly NI to common + TTM engine | SW/QA | todo |
| P6-4 | Dividends per share | SW | todo |
| P6-5 | Risk-free rates (BoC 10y, FRED DGS10) | SW | todo (host allowed) |
| P6-6 | Eligibility spec + filter | AN/QA/SW | todo |
| P7-1..6 | Model A (residual income) + Model B P/B~ROE branch, report v2, insurer CSM | AN/QA/SW | todo |
| P8-1..3 | Backtest harness (PIT S&P membership, engine, A/B results) | SW/QA | todo |
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

## Next up (PM selection)
1. Phase 6 (data batch 1 + eligibility filter). P2-9 sector gating is superseded by P7-5.
2. Phase 3 spec (AN): peer multiples, including an insurer-appropriate measure (adjusted BV with CSM).
3. P2-8 non-common equity input.
