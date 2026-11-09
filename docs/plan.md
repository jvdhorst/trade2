# Equity Valuation Screener — Project Plan

## Goal
A daily-updated system that pulls financials, prices, market cap and peer data for
**TSX Composite (~220) + S&P 500**, estimates per-share value with transparent
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
| FX, rates | Bank of Canada Valet | Bank of Canada Valet |
| Sector / industry | Yahoo profile | Yahoo profile |
| Peers | Same industry within universe (fallback: sector) | same |

Known gap: TSX fundamentals from Yahoo have limited history and no filing dates.
A paid provider (FMP / EODHD) is the candidate fix; decision at Phase 5.

Allowed hosts (environment `trade2`): `query1/query2.finance.yahoo.com`,
`fc.yahoo.com`, `finance.yahoo.com`, `data.sec.gov`, `www.sec.gov`, `www.bankofcanada.ca`.

## Storage
- `data/raw/<source>/<date>/…json`: immutable raw cache (gitignored)
- `data/valuation.duckdb`: tables `securities`, `prices_daily`, `fundamentals`
  (long format, keyed by `filed_date` to prevent look-ahead), `fx_daily`,
  `valuations`, `runs`
- Monetary values always carry a currency. Conversions happen at valuation time
  using `fx_daily`.

## Phases
- **Phase 0: Foundation.** Scaffold, CI, DuckDB schema, `valuation init`.
- **Phase 1: Ingestion.** Universe, prices, fundamentals, FX, coverage report.
- **Phase 2: Book value vs price.** See `docs/models/book_value.md`.
- **Phase 3: Peer multiples.** P/E, EV/EBITDA, P/S, ROE-justified P/B.
- **Phase 4: Cash-flow models.** DDM (banks, utilities, pipelines), 2-stage DCF/FCFE.
- **Phase 5: Composite score.** Quality screens, confidence, data-provider decision.
- **Later:** baskets/indices, point-in-time backtest of the screen, HTML dashboard.
