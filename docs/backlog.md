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

## Decisions log
- 2026-11-09: initial testing uses a ~2% subset of the universe (14 tickers, `universe/reference.csv`).
  The peer fair value is therefore mostly FEW_PEERS until P1-2.
- 2026-11-09: data provider choice deferred; free sources behind a parse/fetch split.

- 2026-11-09: issuer filings are the primary reference for financials; Yahoo `bookValue` is secondary
  (wrong for TD, BNS, MFC).

- 2026-11-09: expanded to the full TSX Composite (user request). S&P 500 not yet ingested.

## Next up (PM selection)
1. P2-9 sector gating (removes the asset-light artifacts at the top of the screen), then P1-10 triage.
2. Phase 3 spec (AN): peer multiples, including an insurer-appropriate measure (adjusted BV with CSM).
3. P2-8 non-common equity input.
