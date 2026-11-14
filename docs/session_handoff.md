# Session handoff: state as of 2026-11-13 (after #17)

Written by the coordinating PM session so work can continue in a fresh session. Update it when the state changes.

## 1. Goal and approach
- **Goal:** daily screening of **TSX Composite (215) + S&P 500 (503)** "well-traded" stocks, with transparent models
  that give BUY/HOLD/SELL **screening signals** for human review. No trading and no advice.
- **Process:** specs first (AN), QA tests from the specs, SW implementation, external checks against filings, then a
  point-in-time backtest with a fixed pass bar and a locked holdout.

## 2. What is on `main` (PRs #1–#17 merged)
| Area | State |
|---|---|
| Data | Yahoo (prices, TSX fundamentals; throttled in the cloud), SEC EDGAR (US fundamentals with filed dates), BoC/FRED (10y yields, USD/CAD), iShares XIC + Wikipedia (constituents, GICS). Incremental ingest with `fetch_log`. Batches 1–3: NI, dividends, CFO, capex, revenue, GP, EBIT, assets, debt, cash, current assets/liabilities, retained earnings. |
| Models | **A** residual income 0.2.0 (frozen) · **B** P/B~ROE 0.2.0 (diagnostic only) · **C** value/quality/momentum/risk composite 0.2.0 (frozen) · **H** shareholder yield 0.1.0 (frozen) · **J** EBIT yield + ROC 0.1.0 · **G** Altman Z'' distress veto 0.1.0 (no BUY) · **D** dividend sustainability 0.1.0 · **E** Piotroski F 0.1.0 · **F** accruals 0.1.0. J/G/D/E/F are new, **unvalidated and not backtested**. No vote is built. |
| Backtest | `valuation backtest build|run|report|benchmarks|fit`; PIT S&P 500 membership; whole-market pass bar (`docs/models/backtest.md`); benchmarks SPX_TR / XIC; guarded one-shot holdout command. Harness stores signals for A/B/C/H and J/G/D/E/F (unvalidated) plus 1m returns. **Per-company fit** (`docs/models/per_company_fit.md` v0.1.0, #17): SELL_EXIT strategy vs holding, select 2014-01…2019-10 with BH FDR q=0.10, confirm 2020-01…2023-08 (≥1%/yr); `FIT_LOCK_END` 2023-10-31; output `company_model_fit` table + `reports/backtest/company_model_fit.csv`. **Not yet run on real data.** |
| Report | `reports/<date>/screen_signals.{csv,parquet}` with per-model columns and the disclaimer; `benchmarks.csv`; coverage. `valuation value --fit PATH` adds `<m>_fit`, `<m>_highlight`, `highlight`, `highlight_models`. |
| Ops | `scripts/nightly_update.sh/.ps1` + cron/systemd/launchd/Task Scheduler templates, `docs/ops/nightly_update.md` (#15). |
| Tests | 2,135 green. CI runs on every push. Intermediate "tests-first" commits are expected to be red; the user chose to keep CI as is. |

## 3. Evidence so far (US, in-sample 2014-01 … 2023-10; survivor-biased upper bound)
- **Phase 8/9:** A, B and C all **fail** the whole-market bar (12m IC and spread years). Their BUY buckets trail the
  S&P 500 total return by 1–3%/yr. The equal-weight eligible universe trails it by 1%/yr.
- **R2 in-sample** (`docs/qa/r2-insample.md`, revised on the same period):
  - A 0.2.0: IC 0.005.
  - C 0.2.0: IC 0.013.
  - **H: IC 0.030 (t 0.80), spread > 0 in 8/10 years, BUY +0.65%/yr vs SPX_TR.** H's buyback part has IC 0.044.
  - None can pass the bar, whose IC leg is in-sample.
- The holdout is **unspent**. H's pre-registered confirmation test is merged (#10, #11): holdout 12m IC > 0, spread > 0,
  and BUY excess vs SPX_TR > 0; INCONCLUSIVE if < 18 dates. It is **waiting for the user's go-ahead.**

## 4. Work in progress
None. No cloud sessions running, no open PRs, no scheduled checks.

## 5. Decisions waiting on the user
1. **Holdout run** for H's pre-registered test: when?
2. **Local runs** of the backtest + `backtest fit` (sequence in §7), then review `company_model_fit.csv`.
   - **In-sample exit lock** (fixed 2026-11-13, `docs/models/backtest.md` §2): in-sample forward returns whose exit is after
     2023-10-31 are now NaN (stocks and benchmark). Earlier Phase 8/9 and R2 in-sample numbers included them; a local
     re-run will differ slightly (12m metrics now cover 2014-01 … 2022-09).
3. **Optional next work:**
   - H-BBY (buyback-only) as a forward test on month-ends from 2026-11-30.
   - Data fixes before backtests: **DEF-9** (16-week fiscal quarters, e.g. PepsiCo, give no TTM; affects every model), **G-9** (EDGAR vs Yahoo debt definitions can flip G), CAT 10-Qs without debt tags, short TSX history (~5 quarters from Yahoo).
   - Insurers SLF/GWO/IAG stay NO_SIGNAL (user decision).

## 6. User preferences seen so far
- Prefers short status replies. Says "continue", "merge" or "status".
- Wants PRs watched and merged on request.
- Runs heavy data work locally. AN may use Fable.
- Initial testing used a ~2% subset (`universe/reference.csv`); the full universes are now standard.
- **Weekly usage limit** is near (resets around 2026-11-17). Keep sessions economical, and resume sessions stopped by the 5-hour limit with `send_message`.

## 7. Local command sequence (user's machine)
```bash
uv sync
uv run valuation ingest --refresh-fundamentals --universe universe/tsx_composite.csv   # once, then nightly without the flag
uv run valuation ingest --refresh-fundamentals --universe universe/sp500.csv
uv run valuation value          # reports/<date>/screen_signals.csv
uv run valuation backtest build && uv run valuation backtest benchmarks
uv run valuation backtest run --market US --workers 4 && uv run valuation backtest report --market US   # in-sample only
uv run valuation backtest fit --market US                       # per-company fit (first run after #17 recomputes all months)
uv run valuation value --fit reports/backtest/company_model_fit.csv
# ONLY on explicit go-ahead (one shot, spends the holdout):
# uv run valuation backtest run --holdout --freeze docs/models/revision_v2_freeze.md --today 2026-11-10
```
Nightly scheduling: see the README section "Nightly updates".

## 8. Environment (cloud)
- **Environment `trade2`** (`env_01Lr4yLDd5npo7Haw1XZtpk8`) allows: Yahoo query1/query2/fc/finance, data.sec.gov,
  www.sec.gov, www.bankofcanada.ca, fred.stlouisfed.org, en.wikipedia.org, www.blackrock.com, the bank IR sites
  (rbc/td/scotiabank/manulife), wsj, morningstar.
- **Yahoo** throttles the container. **manulife.com** blocks bots; the user uploads that PDF when needed.
- **Force-push** is blocked by the auto-mode classifier unless the user explicitly asks; prefer fast-forward pushes.

## 9. Key documents
- **Plans:** `docs/plan.md`, `docs/pm_plan_buy_hold_sell.md`, `docs/backlog.md` (decisions log + next up).
- **Specs:** `docs/models/*.md`. Proposals: `docs/models/proposals/buy_hold_sell.md` (A/B/C) and `additional_models.md` (D–M).
- **Protocol:** `docs/models/revision_v2_freeze.md`, `docs/models/holdout_h_preregistration.md`.
- **Per-company fit:** `docs/models/per_company_fit.md`.
- **Validation:** `docs/qa/*.md` (phase6–9, r2-insample, model_g/j, models_def, book_value).
