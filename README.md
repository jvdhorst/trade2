# trade2: transparent equity valuation screener

Finds potential buy-and-hold candidates in **TSX + S&P 500** for a human to analyze further.
It does no trading. Updates run about once a day. Every number traces back to a written model spec and a source filing.

- Plan, roles and phases: [`docs/plan.md`](docs/plan.md) · Backlog: [`docs/backlog.md`](docs/backlog.md)
- Model specs: [`docs/models/`](docs/models/) · QA validation: [`docs/qa/`](docs/qa/)

## Quick start
```bash
uv sync
uv run valuation init                                 # create data/valuation.duckdb
uv run valuation ingest --universe universe/reference.csv   # prices + whatever is due (see below)
uv run valuation value                                # writes reports/<date>/screen_book_value.{csv,parquet}
uv run valuation crosscheck                           # compare against Yahoo's published book value
uv run valuation crosscheck --issuer                  # compare against issuer supplementary packs
uv run valuation universe                             # rebuild TSX Composite + S&P 500 lists
uv run pytest                                         # offline tests (network tests are deselected)
```
### Refresh policy (incremental ingest)
| Data | When it is fetched | How |
|---|---|---|
| Prices | every run | Yahoo spark batch API, 20 tickers per call; per-ticker history only for symbols the batch omits |
| TSX fundamentals | new ticker, data older than 7 days, or a new quarter expected (period end + 136 days; re-checked daily) | Yahoo fundamentals-timeseries, only the fields the model uses |
| US fundamentals | new ticker, or a new accession for the CIK in the EDGAR frames API (1 call per quarter covers every US filer) | EDGAR companyfacts, trimmed to the parsed concepts before caching |
| Profiles | new ticker or older than 30 days | Yahoo quoteSummary, with a crumb-free fallback |

```bash
uv run valuation ingest --prices-only --universe universe/tsx_composite.csv   # fastest daily mode
uv run valuation ingest --refresh-fundamentals ...                             # force a full refetch
```
Per-ticker fetches run on 4 threads (`--workers`), with a shared rate limit (Yahoo ≈4 req/s, SEC ≤10 req/s).
Fetch history is in the `fetch_log` table.

For SEC EDGAR in production, set `VALUATION_SEC_USER_AGENT="<app> <contact email>"`, as SEC's fair-access policy asks.

## Data sources
| Data | Source |
|---|---|
| Prices, profile, TSX fundamentals | Yahoo Finance (via `yfinance`, unofficial) |
| US fundamentals (point-in-time) | SEC EDGAR XBRL `companyfacts` |
| USD/CAD | Bank of Canada Valet |
| Index constituents + GICS sector | iShares XIC holdings (TSX), Wikipedia (S&P 500) |
| Issuer BVPS (QA reference) | Bank and insurer supplementary financial packs |

Raw responses are cached under `data/raw/<source>/<date>/`. Parsed data goes to DuckDB.

## Layout
```
src/valuation/
  providers/  yahoo.py, sec_edgar.py, boc.py   fetch (cached) + pure parsers
  ingest/     pipeline.py                      universe -> DB
  models/     book_value.py                    model code (spec: docs/models/book_value.md)
  engine.py                                    DB -> model inputs -> valuations table
  report/     screen.py                        CSV + Parquet
  qa/         crosscheck.py                    external cross-check
universe/     reference.csv                    14-ticker initial-testing subset (~2%)
              tsx_composite.csv, sp500.csv     full lists (iShares XIC holdings, Wikipedia)
              issuer_sources.csv               issuer BVPS disclosures for QA
```

*Not investment advice.*
