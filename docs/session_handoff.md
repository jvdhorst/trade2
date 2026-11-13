# Session handoff: state as of 2026-11-13

Written by the coordinating PM session so work can continue in a fresh session. Update it when the state changes.

## 1. Goal and approach
- **Goal:** daily screening of **TSX Composite (215) + S&P 500 (503)** "well-traded" stocks, with transparent models
  that give BUY/HOLD/SELL **screening signals** for human review. No trading and no advice.
- **Process:** specs first (AN), QA tests from the specs, SW implementation, external checks against filings, then a
  point-in-time backtest with a fixed pass bar and a locked holdout.

## 2. What is on `main` (PRs #1–#14 merged)
| Area | State |
|---|---|
| Data | Yahoo (prices, TSX fundamentals; throttled in the cloud), SEC EDGAR (US fundamentals with filed dates), BoC/FRED (10y yields, USD/CAD), iShares XIC + Wikipedia (constituents, GICS). Incremental ingest with `fetch_log`. Batches 1–3: NI, dividends, CFO, capex, revenue, GP, EBIT, assets, debt, cash, current assets/liabilities, retained earnings. |
| Models | **A** residual income 0.2.0 (frozen) · **B** P/B~ROE 0.2.0 (diagnostic only) · **C** value/quality/momentum/risk composite 0.2.0 (frozen) · **H** shareholder yield 0.1.0 (frozen) · **J** EBIT yield + ROC 0.1.0 · **G** Altman Z'' distress veto 0.1.0 (no BUY) · **D** dividend sustainability 0.1.0 · **E** Piotroski F 0.1.0 · **F** accruals 0.1.0. J/G/D/E/F are new, **unvalidated and not backtested**. No vote is built. |
| Backtest | `valuation backtest build|run|report|benchmarks`; PIT S&P 500 membership; whole-market pass bar (`docs/models/backtest.md`); benchmarks SPX_TR / XIC; guarded one-shot holdout command. |
| Report | `reports/<date>/screen_signals.{csv,parquet}` with per-model columns and the disclaimer; `benchmarks.csv`; coverage. |
| Tests | 1,902 green. CI runs on every push. Intermediate "tests-first" commits are expected to be red; the user chose to keep CI as is. |

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

## 4. Work in progress (cloud sessions; check them with `get_session`, PRs with `list_pull_requests`)
| Session | ID | Branch | Scope |
|---|---|---|---|
| PM - Per-company model fit phase | `session_01TwUhobmujgFvZE4AjG9TKp` | `claude/per-company-fit` | AN (on **Fable**): `docs/models/per_company_fit.md`. Fitness = signal strategy (hold unless SELL; plus a BUY-only variant) vs holding the stock. Select on 2014–2019, confirm on 2020–2023 with FDR. Holdout excluded. SW: two-mode evaluation (whole-market unchanged + per-company), J/G/D/E/F added to the harness, `company_model_fit` table, and screen `<m>_fit` + `highlight` columns. No runs in the cloud. Opens a PR and stops. |
| SW - Nightly local market-data update | `session_01TwUhociYMk8RBcDQZ3a5z5` | `claude/nightly-update` | `scripts/nightly_update.sh/.ps1` (lock, retry, logs, optional notify), cron/systemd/launchd/Task Scheduler templates, `docs/ops/nightly_update.md`. Opens a PR and stops. |

The coordinating session scheduled a check for these PRs (around 06:03 UTC on 2026-11-13). A fresh coordinator should
list open PRs, `subscribe_pr_activity` to them, and summarize them for the user.

## 5. Decisions waiting on the user
1. **Holdout run** for H's pre-registered test: when?
2. **Merging** the per-company-fit and nightly-update PRs once they are open.
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
uv run valuation value --universe universe/sp500.csv          # reports/<date>/screen_signals.csv
uv run valuation backtest build && uv run valuation backtest benchmarks
uv run valuation backtest run --workers 4 && uv run valuation backtest report   # in-sample only
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
- **Validation:** `docs/qa/*.md` (phase6–9, r2-insample, model_g/j, models_def, book_value).
