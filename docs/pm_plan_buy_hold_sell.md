# PM plan: implementing the BUY / HOLD / SELL proposal (approved 2026-11-09)

## Context
AN delivered `docs/models/proposals/buy_hold_sell.md` (branch `claude/an-buy-hold-sell-proposal`). It contains:
- **Eligibility filter ("well-traded").** Median daily traded value over the last 63 sessions ≥ C$5M (TSX) or
  US$25M (US); market cap ≥ C$1B / US$5B; price ≥ C$2 / US$5; active trading; ≥ 13 months of history.
- **Model A: residual income.** An ROE-justified intrinsic value with a 10-year fade to the cost of equity
  `r = 10y yield + 5% ERP` (no beta). BUY if margin of safety ≥ 25% and robust to r+1pp; SELL if ≤ −20% and robust
  to r−1pp.
- **Model B: quality-adjusted peer multiple.** A per-sector Theil–Sen regression: ln(P/B) on ROE for book-anchored
  sectors, ln(EV/Sales) on margin and growth for the rest. BUY/SELL = cheapest/dearest 20% of residuals, with at
  least ±15% implied upside/downside.
- **Model C: value–quality–momentum–risk composite.** Sector-neutral z-scores, equal weights, quintile bands, with
  vetoes against distress and value traps.
- **Combination:** a vote among applicable models. Disagreement is shown, not averaged.
- **Backtest:** month-end from 2014, point-in-time universe, a 36-month holdout, and a pass bar of spread > 0 in
  ≥ 60% of years with 12-month IC > 0.03 (t > 2).
- **Recommended order:** filter + data → A (+ B's P/B branch) → C → B's EV branch → vote.

PM's view: the order is right. It builds on validated code (`book_value`, issuer checks), front-loads the cheapest
data, and keeps the vote off until models are validated. The adjustments below de-risk two things AN flagged but
didn't schedule: daily **volume** (the spark batch has none) and the **backtest**, which has to exist before any
signal is trusted.

## Phases (each ends with QA sign-off; specs before code, QA tests from spec as before)

### Phase 6: Data batch 1 + eligibility filter (SW, QA)
| ID | Task | Owner | Acceptance |
|---|---|---|---|
| P6-0 | Merge AN proposal doc into `main` | PM | Doc on main |
| P6-1 | S&P 500 into the main DB (pooled peers need it) | SW | ≥95% of 503 valued |
| P6-2 | Price history: 13-month `close/adj_close/volume` backfill via the Yahoo chart API, plus daily volume. Spike first: check whether spark can return volume; if not, use the 5-day chart per ticker at 4 req/s (~3 min/day) | SW | `prices_daily` has volume; daily run ≤ 5 min |
| P6-3 | Quarterly NI to common + TTM engine (Q4 = FY − Q1..Q3 for EDGAR, NO_TTM flag) | SW/QA | Golden TTM fixtures incl. Q4 derivation; AAPL/JPM TTM NI vs 10-K ±0.5% |
| P6-4 | Dividends per share (chart `events=div`) | SW | DPS_TTM for reference set vs issuer ±2% |
| P6-5 | Risk-free: BoC 10y (existing host), US 10y from FRED `fredgraph.csv?id=DGS10`, which needs **new host `fred.stlouisfed.org`** | SW/PM | Table `rates_daily` |
| P6-6 | Spec `docs/models/eligibility.md` (AN) → implement filter + `liquidity.yaml` thresholds; report columns `eligible` and `elig_fail_reason` | AN/QA/SW | QA reports the removal rate per rule (expect <10% S&P, 15–25% TSX) |

### Phase 7: Model A + Model B (P/B ~ ROE branch) (AN → QA → SW)
| ID | Task | Owner | Acceptance |
|---|---|---|---|
| P7-1 | Specs `docs/models/residual_income.md` and `peer_regression.md` (golden examples from the proposal: V_RI = 61.13; fitted P/B 1.935) | AN | Spec reviewed by QA |
| P7-2 | QA tests from spec (golden, sensitivity, fade, clip flags, R²/n gates, Theil–Sen vs `scipy.stats.theilslopes`) | QA | Written before code |
| P7-3 | Implement `models/residual_income.py`, `models/peer_regression.py`, and the signal mapping | SW | QA tests green |
| P7-4 | External checks: ROE_0 vs bank supplementary packs (extend `issuer_sources.csv` with an ROE line, ±1.5pp); one sector's regression reproduced in a spreadsheet | QA | Documented in `docs/qa/` |
| P7-5 | Report v2: per-model signal columns, `NO_SIGNAL`/`NOT_ELIGIBLE`, disclaimer; replaces P2-9 gating | SW | `screen_signals.csv/parquet` |
| P7-6 | Insurer CSM (manual, MFC/SLF/GWO/IFC) via issuer packs | QA/human | Adjusted BVPS used for insurers |

### Phase 8: Backtest harness (moved earlier than AN's §7, so A/B are validated before C is built) (SW, QA)
| ID | Task | Owner | Acceptance |
|---|---|---|---|
| P8-1 | Point-in-time S&P 500 membership from the Wikipedia changes table | SW | Membership on any month-end since 2014 |
| P8-2 | Engine: month-end rebalance, `filed_date ≤ date`, forward 3/6/12-month total returns, metrics (spread, hit rate, rank IC with Newey–West t), holdout lock | SW/QA | Deterministic re-run; look-ahead unit tests |
| P8-3 | Run A and B (US first; TSX short-history caveat). Results labelled "survivor-biased upper bound" until delisted prices exist | QA | `docs/qa/backtest_<model>.md` pass/fail vs bar |

### Phase 9: Data batch 2 + Model C (AN → QA → SW)
CFO, capex, gross profit, total assets, total debt, cash. Then spec, tests, implement the composite with vetoes,
backtest it, and report each factor's spread.

### Phase 10: Model B EV/Sales branch + vote
Only after ≥ 2 models pass the backtest bar. Vote rules as in proposal §5. Report shows agreement and the strongest
objection.

### Revision round v2 (after Phases 8–9: A, B, C all FAIL in-sample)
Specs only, from in-sample evidence and economics; frozen in `docs/models/revision_v2_freeze.md` (params SHA-256
`6c6354c5…088e`). A 0.2.0: SELL shown as HOLD (information only), one-off-quarter ROE rule. B 0.2.0: payments /
exchanges to the EV branch, **diagnostic only**. C 0.2.0: B/P in book sectors only, per-market quintiles, E6 274.
New H 0.1.0 shareholder yield (no new data). Then QA tests → SW → in-sample report → **one** holdout run for all
models, verdict against the unchanged bar; a fail leaves the model "unvalidated" and the holdout spent. Phase 10 only if
≥ 2 of A, C, H pass (backlog R2-0 … R2-6).

## Decisions (approved by the user with the PM defaults)
1. **Approve new host `fred.stlouisfed.org`** for the US 10-year yield (alternative: treasury.gov).
2. **Accept AN's parameters as the starting config** (ERP 5%, the thresholds above). They'll be revisited only after
   the backtest, never tuned on the holdout.
3. Backtest data limits: **accept a survivor-biased US backtest for now** and record it as a limitation. Delisted
   prices (e.g. Stooq) or a paid provider are a Phase 5 provider decision.
4. Execution: **one PM-coordinated session per phase**, titled `<role> - <request>` (e.g. `SW - P6 data batch 1`),
   working on a feature branch with a PR into `main` per phase.

## Verification (per phase)
- `uv run pytest -q` and `ruff` green in CI. Spec-driven QA tests are written before the implementation.
- Phase 6: daily run ≤ 5 min for 718 tickers including volume. Filter removal rates are reported.
- Phase 7: golden values exact, issuer ROE within ±1.5pp, `screen_signals.csv` produced with disclaimers.
- Phase 8: backtest reproducible, look-ahead tests pass, pass/fail per model documented before Phase 9 starts.
