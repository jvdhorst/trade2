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
uv run valuation value                                # screen_book_value + screen_signals (Models A/B) in reports/<date>/
uv run valuation crosscheck                           # compare against Yahoo's published book value
uv run valuation crosscheck --issuer                  # issuer supplementary packs: BVPS (±3%) and ROE (±1.5pp)
uv run valuation universe                             # rebuild TSX Composite + S&P 500 lists
uv run pytest                                         # offline tests (network tests are deselected)
```
### Refresh policy (incremental ingest)
| Data | When it is fetched | How |
|---|---|---|
| Prices | every run | Yahoo spark batch API, 20 tickers per call; per-ticker history only for symbols the batch omits |
| Volume, adj. close, dividends | every run, one chart call per ticker (shared ≈4 req/s limit) | Yahoo chart API: a 2-year backfill once, then 5 days a day; full refetch when a new dividend/split appears (adj_close is rebased) or after a gap > 7 days |
| Risk-free 10y | every run | Bank of Canada Valet `BD.CDN.10YR.DQ.YLD`, FRED `DGS10` → `rates_daily` (back to 2013 on first load) |
| TSX fundamentals | new ticker, data older than 7 days, or a new quarter expected (period end + 136 days; re-checked daily) | Yahoo fundamentals-timeseries, only the fields the model uses |
| US fundamentals | new ticker, or a new accession for the CIK in the EDGAR frames API (1 call per quarter covers every US filer) | EDGAR companyfacts, trimmed to the parsed concepts before caching (balance sheet + quarterly NI to common) |
| Profiles | new ticker or older than 30 days | Yahoo quoteSummary, with a crumb-free fallback |

```bash
uv run valuation ingest --prices-only --universe universe/tsx_composite.csv   # fastest daily mode
uv run valuation ingest --refresh-fundamentals ...                             # force a full refetch
uv run valuation ingest --refresh-history ...      # refetch 2 years of daily bars for every ticker
uv run valuation ingest --no-history ...           # skip the per-ticker chart call (no volume update)
```
`valuation value` also applies the **eligibility filter** ("well-traded", spec
[`docs/models/eligibility.md`](docs/models/eligibility.md), thresholds in [`config/liquidity.yaml`](config/liquidity.yaml),
`--liquidity` to override). The screen and coverage reports get `eligible`, `elig_fail_reason` and `mdtv`, plus
TTM NI to common (`ni_common_ttm`, spec [`docs/models/ttm.md`](docs/models/ttm.md)), `dps_ttm` and the local 10-year
yield `rf`. The run prints the removal rate per market and rule; the thresholds are recorded in `runs.config_json`.
### Screening signals (Phase 7): `reports/<date>/screen_signals.{csv,parquet}`
*Screening signals for human review, not investment advice.* The file's first line (Parquet: metadata
`disclaimer`) says so. One row per ticker with two independent model views and no combined vote (that comes in Phase 10):

| Model | Spec | Signal rule |
|---|---|---|
| A, residual income | [`residual_income.md`](docs/models/residual_income.md) | ROE_0 = TTM NI to common / avg common equity, clipped to [−10%, 35%], fading linearly to `r = 10y yield + 5%` over 10 years. BUY if MoS ≥ +25% and still > 0 at r+1pp; SELL if MoS ≤ −20% and still < 0 at r−1pp |
| B, P/B ~ ROE (book branch) | [`peer_regression.md`](docs/models/peer_regression.md) | Theil–Sen `ln(P/B) ~ ROE` per GICS sector (Financials, Utilities, Real Estate, Energy, Materials), TSX + US pooled, n ≥ 12 and R² ≥ 0.20. BUY if residual percentile ≤ 20% and upside ≥ +15%; SELL if ≥ 80% and ≤ −15% |

Each model column is `BUY`, `HOLD`, `SELL`, `NO_SIGNAL` (with a reason code) or `NOT_ELIGIBLE` (fails the
eligibility filter). IFRS 17 life insurers need the after-tax CSM, a manual input in
[`universe/insurer_csm.csv`](universe/insurer_csm.csv) (`--csm`). A listed insurer with a blank value gets `NO_SIGNAL`
/ `NEEDS_CSM_INPUT` until a human fills it in. Full per-ticker inputs are in the DuckDB table `signals` (`payload_json`).

### Backtest (Phase 8): `valuation backtest build|run|report`
Spec [`docs/models/backtest.md`](docs/models/backtest.md). It has its own DuckDB, `data/backtest.duckdb`, so the live DB is never touched.
```bash
uv run valuation backtest build                  # PIT S&P 500 membership (Wikipedia), daily prices 2012→ (as traded),
                                                 # splits, dividends, EDGAR history, 10y yields, USD/CAD
uv run valuation backtest run --workers 4        # month-ends 2014→: eligibility → Model A → Model B per date (resumable)
uv run valuation backtest report                 # in-sample metrics + diagnostics → reports/backtest/*.md
uv run valuation backtest report --holdout       # also the locked last 36 month-ends (frozen thresholds; logged in bt_runs)
uv run valuation backtest run --market TSX       # supplementary: current TSX constituents (survivor-biased, short history)
uv run valuation backtest report --market TSX
```
How it works:
- Each month-end runs the live `run_signals` unchanged, using only prices dated ≤ d and facts filed ≤ d. Estimated Yahoo filing dates get an extra 30 days.
- Forward 3/6/12-month total returns come from `adj_close`, measured from the next session.
- The metrics are: the sector-neutral BUY − SELL spread per year, hit rates against the sector median, the Spearman rank IC with a Newey–West t, turnover, and the pass bar. A low-P/B baseline is reported alongside.
- Removed index members that Yahoo no longer serves have no prices. US results are therefore labelled a **survivor-biased upper bound**, and the size of the gap is reported.

Results: [`docs/qa/phase8-validation.md`](docs/qa/phase8-validation.md).

Per-ticker fetches run on 4 threads (`--workers`), with a shared rate limit (Yahoo ≈4 req/s, SEC ≤10 req/s).
Fetch history is in the `fetch_log` table.

For SEC EDGAR in production, set `VALUATION_SEC_USER_AGENT="<app> <contact email>"`, as SEC's fair-access policy asks.

## Data sources
| Data | Source |
|---|---|
| Prices, profile, TSX fundamentals | Yahoo Finance (via `yfinance`, unofficial) |
| US fundamentals (point-in-time) | SEC EDGAR XBRL `companyfacts` |
| USD/CAD, Canada 10y yield | Bank of Canada Valet |
| US 10y yield | FRED `DGS10` (`fred.stlouisfed.org`) |
| Daily volume, adjusted close, dividends | Yahoo chart API |
| Index constituents + GICS sector | iShares XIC holdings (TSX), Wikipedia (S&P 500) |
| Point-in-time S&P 500 membership (backtest) | Wikipedia "Historical components of the S&P 500" (changes table; moved off the constituents page in Aug 2026) |
| Issuer BVPS and ROE (QA reference), insurer CSM (manual) | Bank and insurer supplementary financial packs |

Raw responses are cached under `data/raw/<source>/<date>/`. Parsed data goes to DuckDB.

## Layout
```
src/valuation/
  providers/  yahoo.py, sec_edgar.py, boc.py,  fetch (cached) + pure parsers
              rates.py
  ingest/     pipeline.py                      universe -> DB
  models/     book_value.py, eligibility.py,   model code (specs: docs/models/*.md)
              residual_income.py (A), peer_regression.py (B)
  backtest/   membership.py, data.py,          Phase 8: PIT S&P 500 membership, backtest DB build,
              engine.py, metrics.py,           per-date runner, metrics, report
              runner.py, report.py
  ttm.py                                       point-in-time TTM flows + DPS_TTM
  engine.py                                    DB -> model inputs -> valuations table
config/       liquidity.yaml                   eligibility thresholds
  report/     screen.py                        CSV + Parquet
  qa/         crosscheck.py                    external cross-check
universe/     reference.csv                    14-ticker initial-testing subset (~2%)
              tsx_composite.csv, sp500.csv     full lists (iShares XIC holdings, Wikipedia)
              issuer_sources.csv               issuer BVPS + ROE disclosures for QA
              insurer_csm.csv                  manual insurer CSM after tax (Model A/B input)
```

*Not investment advice.*
