# trade2: transparent equity valuation screener

Finds potential buy-and-hold candidates in **TSX + S&P 500** for a human to analyze further.
It does no trading. Updates run about once a day. Every number traces back to a written model spec and a source filing.

- Plan, roles and phases: [`docs/plan.md`](docs/plan.md) · Backlog: [`docs/backlog.md`](docs/backlog.md)
- Model specs: [`docs/models/`](docs/models/) · QA validation: [`docs/qa/`](docs/qa/)

## Quick start
```bash
uv sync
uv run valuation init                                 # create data/valuation.duckdb
uv run valuation ingest --universe universe/reference.csv   # prices, fundamentals, FX
uv run valuation value                                # writes reports/<date>/screen_book_value.{csv,parquet}
uv run valuation crosscheck                           # compare against Yahoo's published book value
uv run pytest                                         # offline tests (network tests are deselected)
```
For SEC EDGAR in production, set `VALUATION_SEC_USER_AGENT="<app> <contact email>"`, as SEC's fair-access policy asks.

## Data sources
| Data | Source |
|---|---|
| Prices, profile, TSX fundamentals | Yahoo Finance (via `yfinance`, unofficial) |
| US fundamentals (point-in-time) | SEC EDGAR XBRL `companyfacts` |
| USD/CAD | Bank of Canada Valet |

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
```

*Not investment advice.*
