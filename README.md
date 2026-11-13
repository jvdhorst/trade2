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
uv run valuation value                                # screen_book_value + screen_signals (Models A/B/C) + benchmarks.csv in reports/<date>/
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
| US fundamentals | new ticker, or a new accession for the CIK in the EDGAR frames API (1 call per quarter covers every US filer) | EDGAR companyfacts, trimmed to the parsed concepts before caching (balance sheet incl. assets/debt/cash, current assets/liabilities, retained earnings + quarterly NI, CFO, capex, revenue, gross profit, EBIT; tag chains in [`ttm.md`](docs/models/ttm.md) §5–§6) |
| Benchmarks | every run (9 chart calls) | Yahoo chart API, 6 years of daily bars + distributions for the keys in [`universe/benchmarks.csv`](universe/benchmarks.csv) → `benchmarks_daily`, `benchmark_dists` |
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
`disclaimer`) says so. One row per ticker with three independent model views and no combined vote (that comes in Phase 10, gated on ≥ 2
models passing the backtest):

| Model | Spec | Signal rule |
|---|---|---|
| A, residual income | [`residual_income.md`](docs/models/residual_income.md) | ROE_0 = TTM NI to common / avg common equity, clipped to [−10%, 35%], fading linearly to `r = 10y yield + 5%` over 10 years. BUY if MoS ≥ +25% and still > 0 at r+1pp; SELL if MoS ≤ −20% and still < 0 at r−1pp |
| B, P/B ~ ROE (book branch) | [`peer_regression.md`](docs/models/peer_regression.md) | Theil–Sen `ln(P/B) ~ ROE` per GICS sector (Financials, Utilities, Real Estate, Energy, Materials), TSX + US pooled, n ≥ 12 and R² ≥ 0.20. BUY if residual percentile ≤ 20% and upside ≥ +15%; SELL if ≥ 80% and ≤ −15% |
| C, value–quality–momentum–risk composite | [`composite.md`](docs/models/composite.md) | Metrics E/P, B/P, FCF/P · ROE, GP/A, −accruals, −leverage · 12-1 momentum · −252d volatility; 5/95 winsorized (pooled), z-scored within GICS sector (TSX + US pooled), equal-weighted factors, ≥ 3 of 4 required. BUY = top quintile of S, SELL = bottom quintile; BUY vetoed to HOLD on distress (bvps ≤ 0 and NI < 0) or momentum in the bottom 10% |

The file header also carries a benchmark block (S&P 500 TR, SPY/IVV/VOO, S&P/TSX, XIC/XIU/ZCN: trailing distribution
yield vs the 10-year, 1m–5y total returns; spec [`benchmarks.md`](docs/models/benchmarks.md)); full metrics are in
`reports/<date>/benchmarks.csv`.

Each model column is `BUY`, `HOLD`, `SELL`, `NO_SIGNAL` (with a reason code) or `NOT_ELIGIBLE` (fails the
eligibility filter). IFRS 17 life insurers need the after-tax CSM, a manual input in
[`universe/insurer_csm.csv`](universe/insurer_csm.csv) (`--csm`). A listed insurer with a blank value gets `NO_SIGNAL`
/ `NEEDS_CSM_INPUT` until a human fills it in. Full per-ticker inputs are in the DuckDB table `signals` (`payload_json`).

### Backtest (Phase 8): `valuation backtest build|run|report|fit`
Spec [`docs/models/backtest.md`](docs/models/backtest.md). It has its own DuckDB, `data/backtest.duckdb`, so the live DB is never touched.
```bash
uv run valuation backtest build                  # PIT S&P 500 membership (Wikipedia), daily prices 2012→ (as traded),
                                                 # splits, dividends, EDGAR history, 10y yields, USD/CAD
uv run valuation backtest benchmarks             # benchmark series 2012→ for the excess-vs-benchmark column
uv run valuation backtest run --workers 4        # in-sample month-ends 2014→ (holdout dates untouched): labels A, B, C, H (+ unvalidated J, G, D, E, F) per date, fwd returns 1/3/6/12m (resumable)
uv run valuation backtest report                 # in-sample metrics + diagnostics → reports/backtest/*.md
uv run valuation backtest fit                    # per-company model fit (see below)
# ONE-SHOT holdout (revision round v2): refused if the code's parameter SHA-256 differs from the freeze doc,
# or docs/qa/holdout_v2.md / a holdout bt_runs row already exists. Runs all dates and reports in one go.
uv run valuation backtest run --holdout --freeze docs/models/revision_v2_freeze.md --today 2026-11-10
uv run valuation backtest run --market TSX       # supplementary: current TSX constituents (survivor-biased, short history)
uv run valuation backtest report --market TSX
```
How it works:
- Each month-end runs the live `run_signals` unchanged, using only prices dated ≤ d and facts filed ≤ d. Estimated Yahoo filing dates get an extra 30 days.
- Forward 3/6/12-month total returns come from `adj_close`, measured from the next session.
- The metrics are: the sector-neutral BUY − SELL spread per year, hit rates against the sector median, the Spearman rank IC with a Newey–West t, turnover, and the pass bar. A low-P/B baseline is reported alongside.
- Removed index members that Yahoo no longer serves have no prices. US results are therefore labelled a **survivor-biased upper bound**, and the size of the gap is reported.

- Revision round v2: Model A shows a model SELL as HOLD + `SELL_ZONE` (`a_signal`; the model label is `a_model_signal` and is what the backtest uses), Model B is **diagnostic only**, Model C runs per market with B/P in book sectors only, and Model H (shareholder yield, `h_*` columns) is new.
- Model J (EBIT yield + return on capital, Greenblatt-style, `j_*` columns: `j_signal`, `j_no_signal_reason`, `j_ey`, `j_roc`, `j_ev`, `j_rank_ey`, `j_rank_roc`, `j_pct`) ranks EBIT_TTM/EV and EBIT_TTM/invested capital jointly per market, excluding Financials, Utilities and Real Estate. BUY needs the top quintile **and** EY ≥ 10y + 3pp; SELL needs the bottom quintile **and** EY ≤ 10y. **J is new and unvalidated: it has not been backtested** (deferred per user). Spec: [`docs/models/ebit_yield_roc.md`](docs/models/ebit_yield_roc.md). Frozen parameters: `valuation.models.FROZEN_V2`, SHA-256 in `docs/models/revision_v2_freeze.md`.
- Model G (Altman Z'' distress score, `g_*` columns: `g_signal`, `g_no_signal_reason`, `g_z`, `g_zone`, `g_x1`–`g_x4`, `g_cfo_to_debt`, `g_distress_veto`) scores Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4 from the balance sheet (working capital, retained earnings, EBIT TTM, equity / liabilities over total assets), excluding Financials, Utilities and Real Estate. SELL when Z'' < 1.10 (distress), otherwise HOLD; **there is no BUY**. A distressed name with CFO_TTM / total debt > 0.25 is shown as HOLD with the flag `CASH_RICH_OVERRIDE` (zone still reads distress). `g_distress_veto` is reported for a future combined vote; no vote exists. **G is new and unvalidated: it has not been backtested** (deferred per user). It needs data batch 3 (current assets, current liabilities, retained earnings, [`ttm.md`](docs/models/ttm.md) §6): on an existing database run `uv run valuation ingest --refresh-fundamentals` once. Spec: [`docs/models/altman_z.md`](docs/models/altman_z.md).
- Models D, E and F (`d_*`, `e_*`, `f_*` columns) are **new and unvalidated: none has been backtested** (deferred; to be run locally by the user). None has a vote, and none needs new data.
  - **D, dividend sustainability** ([`dividend_sustainability.md`](docs/models/dividend_sustainability.md)): payout = DPS_TTM / EPS_TTM, g_sust = ROE × (1 − payout) capped at 6%, implied return k = DY × (1 + g) + g, spread = k − (10y + 5%). SELL if payout > 1 or spread ≤ −2pp; BUY if spread ≥ +2pp, payout ≤ 0.75 and no dividend cut. Columns: `d_signal`, `d_no_signal_reason`, `d_payout`, `d_g_sust`, `d_g_hist`, `d_dy`, `d_k`, `d_spread`, `d_v_ddm`, `d_mos`. `d_v_ddm` and `d_mos` are informational only. Non-payers, loss-makers, Real Estate and special-dividend-only names get NO_SIGNAL.
  - **E, Piotroski F-score** ([`piotroski.md`](docs/models/piotroski.md)): nine 0/1 accounting signals against the prior year (`e_signals`, e.g. `111111110`). BUY if F ≥ 8 (≥ 7 when only 8 are scored); SELL if F ≤ 2. NO_SIGNAL when fewer than 8 are scored, and for Financials. Most TSX names are `F_INCOMPLETE`, because Yahoo has about 5 quarters and the prior TTM needs 8. Columns: `e_signal`, `e_no_signal_reason`, `e_f_score`, `e_n_scored`, `e_signals`.
  - **F, accruals** ([`accruals.md`](docs/models/accruals.md)): accrual ratio = (NI_TTM − CFO_TTM) / average total assets, ranked within GICS sector per market (at least 12 names). SELL needs the top quintile and a ratio ≥ +0.05; BUY needs the bottom quintile, a ratio ≤ −0.05 and NI > 0. Financials are excluded. Columns: `f_signal`, `f_no_signal_reason`, `f_accrual_ratio`, `f_acc_pct`, `f_cash_conv`, `f_n`.
  - Run locally (the cloud session has no live DB):
    ```bash
    uv run valuation ingest --universe universe/tsx_composite.csv   # and universe/sp500.csv; add --refresh-fundamentals once on an old DB
    uv run valuation value                                          # writes reports/<date>/screen_signals.{csv,parquet} incl. d_*/e_*/f_*
    ```
- Model C is reported with its four factors as diagnostics (C_V, C_Q, C_M, C_R: each factor's own spread and IC), and every model's BUY / SELL / ALL buckets get an **excess return vs the market benchmark** (`SPX_TR` for US, `XIC` for TSX), alongside the pass bar.

### Per-company model fit: `valuation backtest fit`
Spec [`docs/models/per_company_fit.md`](docs/models/per_company_fit.md) (`fit/0.1.0`). The backtest has **two modes**
that read the same stored labels and returns, written once by `backtest run`:
1. **Whole-market** (`backtest report`): the pooled pass bar of `backtest.md`, unchanged (it reads only the 3/6/12-month
   returns). `--extra-models` adds diagnostic rows for the unvalidated J, G, D, E, F (never a verdict; refused with
   `--holdout`).
2. **Per-company** (`backtest fit`): for every (S&P 500 company, model) pair, a strategy that steps out of the stock on
   the model's SELL (`SELL_EXIT`, primary; `BUY_ONLY` reported) is compared with holding it, on 1-month returns, 10 bp
   per switch, cash 0. **Select** in 2014-01…2019-10 (one-sided t-test, Benjamini–Hochberg q ≤ 0.10 over all pairs),
   **confirm** in 2020-01…2023-08 (excess ≥ 1%/yr). Nothing after 2023-10-31 is read (holdout locked; `fit` refuses
   `--holdout`, `--market TSX` and a `--today` before 2026-11-01). Thresholds are fixed in the spec (fit params hash
   `4d0a9e57…b926`), never tuned.

Output: DuckDB `company_model_fit` + `fit_runs` (backtest DB), `reports/backtest/company_model_fit.csv` (latest run;
copy `company_model_fit_<run_id>.csv`) and a summary `company_model_fit_<run_id>.md` (status counts, CONFIRMED pairs,
label coverage per model). `valuation value` reads that CSV (`--fit PATH`, default
`reports/backtest/company_model_fit.csv`) and appends to `screen_signals` the columns `<m>_fit` (the company's
SELL_EXIT status: CONFIRMED / NOT_CONFIRMED / INSUFFICIENT / NOT_TESTED), `<m>_highlight` (CONFIRMED and a current
BUY/HOLD/SELL; A uses `a_model_signal`), `highlight` (any of A, C, H, J, G, D, E, F; B is diagnostic) and
`highlight_models`. Without the CSV, or for a stale model version or a non-US row, every `<m>_fit` is NOT_TESTED.
*CONFIRMED means "worked in both in-sample windows", not a forecast; survivor-biased; all models stay unvalidated.*

**Full local command sequence** (run on your own machine: it needs the network and the real DBs; the cloud
container never runs these, it only runs the offline fixture tests):
```bash
uv sync
uv run valuation init
uv run valuation ingest --universe universe/sp500.csv           # live DB (also universe/tsx_composite.csv);
                                                                # --refresh-fundamentals once on an old DB
uv run valuation backtest build                                 # backtest DB data/backtest.duckdb (network; rebuild
                                                                # if it predates data batches 2-3, else J-F are NO_SIGNAL)
uv run valuation backtest benchmarks                            # optional: excess-vs-benchmark column
uv run valuation backtest run --market US --workers 4           # labels A,B,C,H + J,G,D,E,F and 1/3/6/12m returns,
                                                                # in-sample only; first run after this change
                                                                # re-runs every date once (stamp change), resumable
uv run valuation backtest report --market US                    # whole-market report (unchanged); add
                                                                # --extra-models for J-F diagnostics
uv run valuation backtest fit --market US                       # per-company fit -> company_model_fit
                                                                # + reports/backtest/company_model_fit.csv
uv run valuation value --fit reports/backtest/company_model_fit.csv   # screen_signals with <m>_fit / highlight
```

Results: [`docs/qa/phase8-validation.md`](docs/qa/phase8-validation.md) (A, B), [`docs/qa/phase9-validation.md`](docs/qa/phase9-validation.md) (data batch 2, C, benchmarks).

Per-ticker fetches run on 4 threads (`--workers`), with a shared rate limit (Yahoo ≈4 req/s, SEC ≤10 req/s).
Fetch history is in the `fetch_log` table.

For SEC EDGAR in production, set `VALUATION_SEC_USER_AGENT="<app> <contact email>"`, as SEC's fair-access policy asks.

## Nightly updates (local workstation)
Run the incremental update on weekdays after the market close and Yahoo's settlement, around **19:30 America/Toronto**.
Prices update every run. Fundamentals and profiles refresh only when due, so a normal night takes a few minutes. Before
the first scheduled run (and after new data fields are added, e.g. Model G), run once by hand:
`uv run valuation ingest --refresh-fundamentals --universe universe/tsx_composite.csv` and the same for
`universe/sp500.csv`.

**Linux / macOS (cron).** Open the crontab with `crontab -e` and add the line below. Cron uses the machine's local time;
the line assumes Toronto time. Replace both paths: cron's PATH is minimal, so use the full path from `which uv`. Create
the log folder once with `mkdir -p logs`. On a Mac, cron fires only while the machine is awake.
```bash
30 19 * * 1-5 cd /path/to/trade2 && /path/to/uv run valuation ingest --universe universe/tsx_composite.csv && /path/to/uv run valuation ingest --universe universe/sp500.csv && /path/to/uv run valuation value >> logs/nightly.log 2>&1
```

**Windows (Task Scheduler).** Run this in PowerShell, with your own path:
```powershell
schtasks /Create /TN "trade2 nightly" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 19:30 /TR "powershell -NoProfile -Command \"cd C:\path\to\trade2; uv run valuation ingest --universe universe/tsx_composite.csv; uv run valuation ingest --universe universe/sp500.csv; uv run valuation value\""
```

**Check that it ran:** look at `logs/nightly.log`, the newest `reports/<date>/` folder, and the `fetch_log` table in
`data/valuation.duckdb`. The nightly job runs ingest and valuation only; it never runs a backtest or touches the
holdout. Fuller scripts (lock file, retry on Yahoo HTTP 429, log rotation, systemd/launchd templates) are planned in
`docs/ops/nightly_update.md`.

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
              residual_income.py (A), peer_regression.py (B), composite.py (C)
  backtest/   membership.py, data.py,          Phase 8: PIT S&P 500 membership, backtest DB build,
              engine.py, metrics.py,           per-date runner, metrics, report
              runner.py, report.py
  ttm.py                                       point-in-time TTM flows + DPS_TTM
  benchmarks.py                                benchmark yields/returns, ingest, report block
  engine.py                                    DB -> model inputs -> valuations table
config/       liquidity.yaml                   eligibility thresholds
  report/     screen.py                        CSV + Parquet
  qa/         crosscheck.py                    external cross-check
universe/     reference.csv                    14-ticker initial-testing subset (~2%)
              tsx_composite.csv, sp500.csv     full lists (iShares XIC holdings, Wikipedia)
              issuer_sources.csv               issuer BVPS + ROE disclosures for QA
              insurer_csm.csv                  manual insurer CSM after tax (Model A/B input)
              benchmarks.csv                   benchmark keys/symbols/roles (spec benchmarks.md)
```

## Nightly updates
To refresh market data automatically every weekday evening on your own machine (Linux, macOS or Windows), schedule
[`scripts/nightly_update.sh`](scripts/nightly_update.sh) or [`scripts/nightly_update.ps1`](scripts/nightly_update.ps1).
They ingest both universes and run `valuation value`, with a lock, one retry, logs in `logs/` and no backtests.
Setup for cron, systemd, launchd and Task Scheduler: [`docs/ops/nightly_update.md`](docs/ops/nightly_update.md).

*Not investment advice.*
