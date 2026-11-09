# Equity Valuation Screener — Project Plan

## Goal
A daily-updated system that pulls financials, prices, market cap and peer data for
**TSX Composite (215) + S&P 500 (503)**, estimates per-share value with transparent
models, and writes a **ranked CSV/Parquet report** of buy-and-hold candidates for
human review. No trading. Single companies first; baskets/indices later.

Stack: Python 3.12+, DuckDB + Parquet, pytest, ruff, uv.

## Team & operating model
Each role runs as a subagent, or as a separate session titled `<role> - <request>`
(e.g. `AN - book value model spec`).

| Role | Owns | Hand-off artifact |
|---|---|---|
| **PM** | Backlog, phase gates, task selection, acceptance criteria | `docs/backlog.md` |
| **AN** | Model math, assumptions, worked examples | `docs/models/<model>.md` |
| **SW** | Ingestion, storage, model code, report | `src/valuation/` |
| **QA** | Tests proving code == AN spec; data quality; external cross-checks | `tests/`, `docs/qa/` |

Per-model workflow: **AN spec → QA writes tests from the spec (golden examples) →
SW implements until green → QA cross-checks against an external source → PM signs off.**
QA writes tests from the spec, not from the code.

## Outcomes (v1 definition of done)
1. Daily job ingests prices + latest fundamentals for the universe, idempotently,
   with raw responses cached.
2. Every valuation is reproducible: stored with inputs, source, as-of date, model version.
3. Report: one row per ticker: price, market cap, currency, fair value(s), upside %,
   peer-relative percentile, data-quality flags. Ranked.
4. Every model has a written spec, a test suite, and ≥1 external cross-check with a
   documented tolerance.
5. Non-goals (v1): trading, intraday data, price forecasting, portfolio construction.

## Data sources (free first, behind a `Provider` interface)

| Need | US (S&P 500) | Canada (TSX) |
|---|---|---|
| Prices, shares, market cap | Yahoo chart/quote (yfinance) | Yahoo (`.TO`) |
| Fundamentals | SEC EDGAR XBRL `companyfacts` (official, point-in-time) | Yahoo fundamentals (annual/quarterly) |
| FX, rates | Bank of Canada Valet (USD/CAD); FRED `DGS10` (US 10y) | Bank of Canada Valet (USD/CAD, 10y) |
| Sector / industry | Yahoo profile | Yahoo profile |
| Peers | Same industry within universe (fallback: sector) | same |

Known gap: TSX fundamentals from Yahoo have limited history and no filing dates.
A paid provider (FMP / EODHD) is the candidate fix; decision at Phase 5.

Allowed hosts (environment `trade2`): `query1/query2.finance.yahoo.com`,
`fc.yahoo.com`, `finance.yahoo.com`, `data.sec.gov`, `www.sec.gov`, `www.bankofcanada.ca`,
`fred.stlouisfed.org` (approved 2026-11-09 for the US 10-year yield).

## Storage
- `data/raw/<source>/<date>/…json`: immutable raw cache (gitignored)
- `data/valuation.duckdb`: tables `securities`, `prices_daily` (close, adj_close, volume), `fundamentals`
  (long format, keyed by `filed_date` to prevent look-ahead, incl. quarterly `ni_common_q`), `dividends`,
  `fx_daily`, `rates_daily`, `reference_metrics`, `fetch_log`, `valuations`, `runs`
- Monetary values always carry a currency. Conversions happen at valuation time
  using `fx_daily`.

## Phases
- **Phase 0: Foundation.** Scaffold, CI, DuckDB schema, `valuation init`.
- **Phase 1: Ingestion.** Universe, prices, fundamentals, FX, coverage report.
- **Phase 2: Book value vs price.** See `docs/models/book_value.md`.
- **Phases 3–5 (original plan): peer multiples, cash-flow models, composite.** Superseded by Phases 6–10 below,
  which deliver the same ground as BUY/HOLD/SELL models (B = peer multiples, A and the proposed D/I/L = cash-flow
  and intrinsic models, C = composite). Only the Phase 5 **data-provider decision** (TSX history, delisted prices)
  remains an open item under that name.
- **Phases 6–10: BUY / HOLD / SELL.** Data batch 1 + eligibility (6, done) → Model A + B's P/B branch (7) →
  backtest harness (8) → data batch 2 + Model C (9) → B's EV/Sales branch + vote (10). Plan:
  `docs/pm_plan_buy_hold_sell.md`; proposals: `docs/models/proposals/`.
- **Later:** additional models (`docs/models/proposals/additional_models.md`), baskets/indices, HTML dashboard.
