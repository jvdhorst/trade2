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
| P1-9 | S&P 500 ingest run (503) | SW | ≥95% valued; run on request | done (P6-1: 503/503) |
| P1-11 | Faster ingestion: batch prices, timeseries fundamentals, EDGAR frames, refresh policy, threads | SW/QA | Facts identical to the old route; daily run ≤2 min | done |
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
| P2-9 | Rank only in P/B-meaningful sectors; others unranked | AN | Spec v0.3.0 + tests | superseded by P7-5 (Model A sector applicability, Model B book sectors) |
| P2-8 | Non-common equity input (insurers' participating policyholders' equity) | AN | Spec + source identified | todo (MFC CSM-adjusted book is +1.4% vs issuer because of it) |

## Phases 6–10: BUY / HOLD / SELL models
Plan: [`docs/pm_plan_buy_hold_sell.md`](pm_plan_buy_hold_sell.md) · Proposal: [`docs/models/proposals/buy_hold_sell.md`](models/proposals/buy_hold_sell.md)

| ID | Task | Owner | Status |
|---|---|---|---|
| P6-0 | AN proposal on main | PM | done (this PR) |
| P6-1 | S&P 500 into the main DB | SW | done (503/503 valued; TSX 215/215) |
| P6-2 | 13-month price/volume history + daily volume | SW | done (spark has no volume → chart API; 2y backfill; daily run 251 s for 718) |
| P6-3 | Quarterly NI to common + TTM engine | SW/QA | done (AAPL/JPM TTM exact vs 10-K/10-Q; spec `docs/models/ttm.md`) |
| P6-4 | Dividends per share | SW | done (DPS_TTM; vs Yahoo checked, issuer ±2% check open → P6-7) |
| P6-5 | Risk-free rates (BoC 10y, FRED DGS10) | SW | done (`rates_daily`, 2013→) |
| P6-6 | Eligibility spec + filter | AN/QA/SW | done (TSX 9.3% removed, S&P 0.2%) |
| P6-7 | DPS_TTM vs issuer-declared dividends for the reference set (±2%) | QA | todo |
| P6-8 | Yahoo balance-sheet filed date: apply the download-date cap used for NI (ttm.md v0.1.1) | SW | todo |
| P6-9 | Report files per universe (a second `value` run on the same day overwrites the first) | SW | todo |
| P7-1 | Specs `residual_income.md` (A) and `peer_regression.md` (B book branch) | AN | done (proposal goldens reproduced: V_RI 61.13 / 58.85, fitted P/B 1.935, FV 77.39, +38.2%) |
| P7-2 | QA tests from spec, before code (incl. Theil–Sen vs scipy, look-ahead, CSM, report) | QA | done (335 new tests) |
| P7-3 | `models/residual_income.py`, `models/peer_regression.py`, signal mapping | SW | done (tests green) |
| P7-4 | External checks: issuer ROE ±1.5pp, regression reproduced, `docs/qa/phase7-validation.md` | QA | done (RY/TD/BNS ROE within 0.2pp) |
| P7-5 | Report v2 `screen_signals.{csv,parquet}` with disclaimer, `signals` table | SW | done |
| P7-6 | Insurer CSM via manual `universe/insurer_csm.csv` | QA/human | MFC done; **SLF, GWO, IAG need human input** (NO_SIGNAL / NEEDS_CSM_INPUT) |
| P7-7 | TSX sector label "Communication" (iShares) ≠ GICS "Communication Services": 5 names get NO_SECTOR | SW | todo |
| P7-8 | PM decision: Model B BUY on loss-makers scored at the 5th-pct ROE (NEG_ROE_UNFITTED, 7 BUYs) | PM/AN | backtest: inconclusive (89 obs, median −1.4%, hit 48%) |
| P7-9 | PM/backtest: Model A gives SELL on 309/718 (no terminal value, T = 10); review only after Phase 8, no tuning | PM | backtest done: SELLs ≈ sector (hit 50.6%), P/B > 2.9 SELLs beat sector +2.1% → P8-7 |
| P7-10 | Fill SLF/GWO/IAG CSM after tax each quarter (issuer SIPs) | human | todo |
| P7-11 | One-off gains drive 5 of 8 Model A BUYs (ECHO, FIS, FCR-UN, APA, SMCI): rule for a dominant quarter (AN) | AN/PM | backtest supports it: clipped BUYs hit 44% vs 58% → P8-7 |
| P7-12 | SMCI: Yahoo tags US$4.23B preferred at 2026-06-30, EDGAR none; read the 10-Q | QA | todo |
| P7-13 | 52/53-week filers: Yahoo vs EDGAR quarter ends 1–5 days apart give 22 false ROE_PERIOD_MISMATCH (spec change) | AN/SW | todo |
| P7-14 | Model B Financials slope steepened by payments/ratings/brokers (MA, MCO, V, AMP, AON): ALL +466%; sub-industry peers? | AN/PM | backtest: hurts (Fin IC −0.078); sub-industry split never reaches 12 names; excluding payments/exchanges → IC ≈ 0 → P8-7 |
| P8-0 | Spec `docs/models/backtest.md` (calendar, holdout lock, metrics, pass bar, goldens) | AN | done (v0.1.1 after QA review) |
| P8-1 | PIT S&P 500 membership from Wikipedia (changes table moved to "Historical components of the S&P 500") | SW/QA | done (792 intervals, 503–508 members/date; real add/remove dates tested) |
| P8-2 | Backtest DB + engine: as-traded prices 2012→, splits, EDGAR history, live run_signals per month-end, resumable; look-ahead tests | SW/QA | done (225 spec tests; 154 dates in ~9.5 min wall) |
| P8-3 | Run + report: `docs/qa/backtest_model_a.md`, `backtest_model_b.md`, `phase8-validation.md` | QA | done: **A FAIL, B FAIL** (in-sample; holdout kept locked) |
| P8-4 | Delisted-price source (e.g. Stooq, new host) to remove survivorship bias: 158/258 removed names lack prices (11% of member-dates) | PM/SW | todo |
| P8-5 | Predecessor-CIK map for reorganised filers (XOM, BLK, DIS, AVGO, LIN) | SW | todo |
| P8-6 | Share count for multi-class EDGAR filers (META, EL, MKC, STZ, LEN, HRL, TSN, …): spec a fallback (e.g. weighted-average basic shares) | AN/SW | todo |
| P8-7 | PM decision on the backtest proposals (phase8-validation §6): Model A SELL rule, one-off ROE rule (P7-11), Model B Financials payments/exchanges (P7-14) | PM/AN | specced in revision round v2 (R2-0), PM review in the PR |
| P8-8 | Live first load: bulk upserts instead of `executemany` (36 min for 503 names) | SW | done (P9: S&P 500 first load 502 s incl. 2y history; TSX 200 s) |
| P8-9 | Historical E3/E2 thresholds are nominal (US$5B cap fails ~10 names/date in 2014); consider indexed/relative thresholds for history | AN/PM | todo |
| P8-10 | Benchmark yields and returns (S&P 500 TR, SPY/IVV/VOO, S&P/TSX, XIC/XIU/ZCN): spec `docs/models/benchmarks.md`; report block + backtest excess-return column | QA/SW | done (P9-5) |
| P9-1 | Data batch 2: CFO, capex, revenue, GP, EBIT quarters + TTM; assets, debt (D1–D5), cash; spec `ttm.md` 0.2.0 | SW/QA | done (AAPL/JPM TTM exact vs 10-K/10-Q; CNR/SU exact vs Yahoo trailing) |
| P9-2 | Spec `docs/models/composite.md` (Model C) | AN | done (v0.1.0, D1–D12) |
| P9-3 | Model C tests from spec, `models/composite.py`, run_signals + screen_signals `c_*` | QA/SW | done (90 spec tests) |
| P9-4 | Backtest Model C + per-factor spread/IC, `docs/qa/backtest_model_c.md` | QA | done: **C FAIL** (IC 0.008, t 0.28; 6/10 years) |
| P9-5 | Benchmarks: ingest, `benchmarks_daily`, report block + `benchmarks.csv`, backtest excess vs SPX_TR / XIC | QA/SW | done (XIC/XIU exact vs BlackRock; SSGA/Vanguard/ishares.com unreachable) |
| P9-6 | PM decision: eligibility `min_history_sessions` 273 → 274 so MOM (t−273) is always defined for E6-passing names (composite D5) | PM | specced: eligibility 0.1.1 (R2-0) |
| P9-7 | Source choice prefers Yahoo for 52/53-week EDGAR filers (Yahoo labels the quarter at month-end, e.g. AAPL 09-30 vs 09-26), so flows/NI use an estimated filed date; related to P7-13 | AN/SW | todo |
| P9-8 | E/P vs Yahoo `trailingPE` and SPY/IVV/VOO issuer distribution checks blocked (quoteSummary crumb 401; ssga.com, vanguard.com, ishares.com not reachable) | QA/PM | blocked |
| P9-9 | Model C pools TSX + US quintiles: TSX gets 29% BUY vs US 16% on 2026-11-10 (anomaly, not tuned) | PM/AN | specced: composite 0.2.0 per-market cross-section (R2-0) |
| P9-10 | EDGAR `total_debt` absent for ~60 US non-financials (no D1–D5 tag); `−leverage` missing there | AN/SW | todo |
| P10 | Model B EV/Sales branch + vote | AN/QA/SW | blocked (gate per `revision_v2_freeze.md` §4: ≥ 2 of A 0.2.0, C 0.2.0, H 0.1.0 pass; B diagnostic, never counts) |

## Revision round v2 (frozen protocol: [`docs/models/revision_v2_freeze.md`](models/revision_v2_freeze.md), params SHA-256 `6c6354c5…088e`)
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| R2-0 | Specs: `residual_income` 0.2.0, `peer_regression` 0.2.0, `composite` 0.2.0, `eligibility` 0.1.1, `shareholder_yield` 0.1.0, freeze doc | AN | PR reviewed by PM/QA | done (this PR) |
| R2-1 | QA spec tests before code: A G8 + changed G3/G5; B G5 + EV-sub-industry property; C G2-v2, G4, market separation; H G1–G10 + properties; eligibility 274; `FROZEN_V2` canonical SHA-256 = frozen value | QA | Tests written from specs, red before R2-2 | done: 7 new spec files (300 tests) + v0.2 updates of existing tests; 1325 green |
| R2-2 | SW: `ni_quarters` + one-off rule + `screen_signal` (A); `sub_industry` (B); `market`, bp gating, per-market stats (C); `models/shareholder_yield.py` + engine mapping (same-source shares, split factor); `liquidity.yaml` 274; report columns (`a_model_signal`, `h_*`, B diagnostic disclaimer) | SW | R2-1 green, ruff clean | done (claude/r2-implementation) |
| R2-3 | SW: harness additions (freeze §3): model H + H_BBY/H_DY diagnostics, A backtest label = model label, holdout guard, thresholds hash incl. H | SW/QA | Guard tested (second holdout run refused) | done: `backtest run --holdout --freeze …` one-shot guard (`backtest/holdout.py`), tested with fixtures only; holdout NOT run |
| R2-4 | QA: in-sample US backtest of the revised models, `docs/qa/backtest_v2_insample.md`; no change afterwards | QA | Report with both hashes | in-sample run done: `docs/qa/r2-insample.md` (in-sample only, not the verdict) |
| R2-5 | QA: **single** holdout run (`report --holdout --today 2026-11-10`), `docs/qa/holdout_v2.md`; pass/fail per model vs the unchanged bar, plus the H confirmation test (freeze §4.7) | QA | User go-ahead before the run; one `kind='holdout'` row | todo: **waiting for the user's go-ahead** (R2-4 done, H test pre-registered) |
| R2-6 | PM: Phase 10 gate decision from R2-5 (≥ 2 of A, C, H pass); failed models stay "unvalidated" | PM | Decision log entry | todo |
| R2-7 | TSX GICS sub-industry for Financials (no source today: X.TO etc. stay in B's book branch) | SW/AN | Mapping file or source | todo |

## Model J: EBIT yield + return on capital (spec [`docs/models/ebit_yield_roc.md`](models/ebit_yield_roc.md) v0.1.0; not frozen, not in FROZEN_V2)
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| J-0 | Spec `ebit_yield_roc` 0.1.0 from proposal §7: EV, EY, financing-side IC, ROC, joint rank (min ranks; ties by rank_EY then ticker), per-market ranking, Financials/Utilities/Real Estate excluded, NO_SIGNAL gates, rf-floored mapping, goldens G1–G7 | AN | Arithmetic verified by throwaway script | done (claude/model-j) |
| J-1 | QA spec tests before code review: goldens, ties, gates and precedence, rf threshold, FX, per-market independence, properties, engine PIT | QA | Written from the spec only | done (claude/model-j) |
| J-2 | SW: `models/ebit_yield_roc.py`, `run_signals` mapping (EBIT via `flow_ttm`, balance sheet behind `bvps`), `j_*` columns in `screen_signals`, disclaimer "Model J is new and unvalidated (not backtested)"; no vote | SW | Suite + ruff green, FROZEN_V2 unchanged | done (claude/model-j) |
| J-3 | QA external checks: EBIT TTM vs 10-K/10-Q (EDGAR, exact); EV vs Yahoo `enterpriseValue` (±10%) | QA | `docs/qa/model_j_validation.md` | partial: EBIT TTM exact for AAPL, MSFT, CAT ([`docs/qa/model_j_validation.md`](qa/model_j_validation.md)); Yahoo EV check blocked (Yahoo HTTP 429 from the cloud container) |
| J-4 | Live J signal counts per market + top 10 BUYs (unvalidated) | SW | Live DB run | todo: blocked in the cloud session (no caches, Yahoo throttled); run locally with `valuation value` |
| J-5 | **J backtest: deferred per user.** Not in any in-sample or holdout run; the holdout and the frozen R2 models are untouched. A later run needs a pre-registered hypothesis (proposal §7: top-minus-bottom 3–6%/yr, IC 0.04–0.06; EY alone vs joint rank) and a user go-ahead | PM/QA | User go-ahead | deferred |
| J-7 | Data anomalies from J-3 (not fixed): CAT tags debt only in 10-Ks (10-Q quarters → J `NO_DATA`; carry-forward is a spec decision); CAT FY2025 D4 debt likely understated (current portion untagged) | AN/SW | Decision + ttm.md rule change | todo |
| J-6 | 3-year average EBIT for `CYCLICAL_EBIT` names (needs > 2 years of TSX history); operating leases in EV | AN/SW | Spec 0.2.0 | todo |

## Model G: Altman Z'' distress veto (spec [`docs/models/altman_z.md`](models/altman_z.md) v0.1.0; not frozen, not in FROZEN_V2)
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| G-0 | Data batch 3 (`ttm.md` 0.3.0 §6): `current_assets` (`AssetsCurrent` / `quarterlyCurrentAssets`), `current_liabilities` (`LiabilitiesCurrent` / `quarterlyCurrentLiabilities`), `retained_earnings` (`RetainedEarningsAccumulatedDeficit` / `quarterlyRetainedEarnings`); same filing / source rule as §5.2; goldens G15–G18; cache keys bumped (EDGAR `CIK…_v4`, Yahoo `timeseries_v4`) | SW (spec), QA (fixtures), SW (code) | `tests/unit/test_data_batch3_spec.py` green | done (claude/model-g) |
| G-1 | Spec `altman_z` 0.1.0 from proposal §4: X1–X4, Z'' (fixed evaluation order), zones 1.10 / 2.60 (both grey), Financials/Utilities/Real Estate excluded, precedence, SELL if Z'' < 1.10 else HOLD (no BUY), `CASH_RICH_OVERRIDE` (CFO_TTM/total_debt > 0.25: SELL → HOLD, veto off, zone unchanged, flagged), `NO_OVERRIDE_DATA`, `NEG_EQUITY`, `distress_veto`; goldens G1–G7 | AN | Arithmetic verified by throwaway script | done (claude/model-g) |
| G-2 | QA spec tests before code: goldens, zone boundaries, override (strict 0.25), precedence, negative equity, missing data, properties, engine PIT | QA | Written from the spec only | done (claude/model-g) |
| G-3 | SW: `models/altman_z.py`, `run_signals` mapping (balance sheet behind `bvps`; EBIT/CFO via `flow_ttm`), `g_*` columns incl. `g_distress_veto` in `screen_signals`, disclaimer "Model G is new and unvalidated (not backtested)"; no vote | SW | Suite + ruff green, FROZEN_V2 unchanged | done (claude/model-g) |
| G-4 | QA external checks: X_i numerators/denominators vs 10-K/10-Q (exact), Z'' recomputed (1e-9) | QA | `docs/qa/model_g_validation.md` | partial: all X_i inputs exact and Z'' bit-identical for AAPL, MSFT, CAT, SBUX ([`docs/qa/model_g_validation.md`](qa/model_g_validation.md)); Yahoo/TSX path blocked (HTTP 429 from the cloud container) |
| G-5 | Live G counts per market and distress list (unvalidated); run once `valuation ingest --refresh-fundamentals` so existing DBs get the batch-3 items | SW | Live DB run | todo: blocked in the cloud session (no caches, Yahoo throttled); run locally |
| G-6 | **G backtest: deferred per user.** Not in any in-sample or holdout run; the holdout and frozen R2 models are untouched. A later run needs a pre-registered hypothesis (proposal §4: distress bucket lags sector median > 3%/yr, hit rate > 55%; veto improves the combined BUY bucket's 12m return in ≥ 60% of years) and a user go-ahead | PM/QA | User go-ahead | deferred |
| G-7 | Veto in the combined vote (proposal §5): `g_distress_veto` is reported only; the vote is not built | PM/AN | Phase 10 design | todo (after Phase 10 gate) |
| G-9 | Data anomalies from G-4 (not fixed): SBUX is HOLD only via the override (CFO/debt 0.375); with its 9,156M operating-lease liabilities counted as debt it would be 0.222 → SELL, and Yahoo `TotalDebt` sometimes includes leases (EDGAR vs Yahoo can flip the signal); CAT tags no debt in 10-Qs (a distressed filer like it would get `NO_OVERRIDE_DATA`); no G-specific staleness flag (SBUX balance sheet 136 days old at as_of) | AN/SW | Decision (leases in the override, debt carry-forward, staleness) | todo |
| G-8 | Override for debt-free distressed names (0.1.0 cannot override when total_debt ≤ 0 or missing: `NO_OVERRIDE_DATA`) | AN | Spec 0.2.0 decision | todo |

## Models D, E, F: dividend sustainability, Piotroski F-score, accruals (specs v0.1.0; not frozen, not in FROZEN_V2)
Specs: [`dividend_sustainability.md`](models/dividend_sustainability.md), [`piotroski.md`](models/piotroski.md),
[`accruals.md`](models/accruals.md). No new data: every input is already stored (ttm.md §5–§6, dividends, splits).
| ID | Task | Owner | Acceptance | Status |
|---|---|---|---|---|
| DEF-0 | Specs from proposal §1–§3: D (payout, g_sust capped at 6%, g_hist / DIV_CUT, DY, implied return k, spread = k − r; V_DDM informational), E (9 binary signals, prior TTM from quarters ≤ t−4q, `PRIOR_TA_FALLBACK`, n_scored ≥ 8, Financials excluded), F (accrual ratio, percentile per (market, sector) ≥ 12 names, Financials excluded); NO_SIGNAL precedence, PIT rules, goldens | AN | Arithmetic verified by throwaway scripts | done (claude/models-def) |
| DEF-1 | QA spec tests written from the specs only (unit + engine PIT/report) | QA | `tests/unit/test_{dividend_sustainability,piotroski,accruals}*_spec.py` green | done (claude/models-def) |
| DEF-2 | SW: `models/dividend_sustainability.py`, `models/piotroski.py`, `models/accruals.py`; `run_signals` mapping (same-source prior filings by window, prior-year flow TTM, ex-date count, dividend coverage guard); `d_*`, `e_*`, `f_*` columns; disclaimer "Model D/E/F is new and unvalidated (not backtested)"; no vote | SW | Suite + ruff green, FROZEN_V2 unchanged | done (claude/models-def) |
| DEF-3 | QA external checks (targeted EDGAR fetches, 2–3 US names per model) | QA | [`docs/qa/models_def_validation.md`](qa/models_def_validation.md) | done: E and F inputs exact for AAPL, MSFT, CAT, KO (signals and ratios bit-identical); D inputs exact for KO, JNJ, MSFT, DPS within ±2% for all 4; PEP fails (no NI TTM, DEF-9); Yahoo `quoteSummary` comparisons blocked (HTTP 401) |
| DEF-9 | **Data anomaly (not fixed): 16-week fiscal quarters.** PEP's Q4 (112 days) is outside the 80–100-day quarter window (`ttm.QUARTER_DAYS`, parser), so no flow TTM exists: PEP is NO_TTM / NO_DATA in A, H, J, G, D, E, F. Affects every 52/53-week filer with a 16-week quarter | AN/SW | ttm.md rule change (e.g. allow 80–120 days for a fiscal Q4) | todo |
| DEF-10 | **Data anomaly (not fixed): D DPS window drift.** A quarterly payer whose ex-date falls 366–370 days before as_of has 3 payments in the window against 4 a year earlier, so a false `DIV_CUT` fires (AAPL at 2026-11-12: g_hist −0.22 despite a raised dividend); the mirror case is 5 payments (spec G-E1) | AN | D 0.2.0: annualise by payment count or widen the window | todo |
| DEF-11 | Data anomalies (not fixed): KO and JNJ no longer tag `CommonStockDividendsPerShareDeclared` (QA used `…CashPaid`; KO has old facts in unit `pure`); CAT tags no debt in 10-Qs (E signal 5 unscored, n_scored 8); CAT gross profit is derived REV − COST including Cat Financial revenue | AN/SW | Decisions | todo |
| DEF-4 | **D/E/F backtests deferred; to be run locally by the user.** Not in any in-sample or holdout run; the holdout and the frozen R2 models (A 0.2.0, C 0.2.0, H 0.1.0, B diagnostic) are untouched. Each needs a pre-registered hypothesis (proposal §1–§3, recorded in each spec) before any run | user / PM / QA | User run + pre-registration | deferred |
| DEF-5 | Live D/E/F counts per market (unvalidated): run locally `valuation ingest` then `valuation value` | user | Live DB run | todo: blocked in the cloud session (no live DB, Yahoo throttled) |
| DEF-6 | E `ANNUAL_PRIOR`: store annual flow totals (Yahoo `annual*`, EDGAR FY durations) so TSX names (Yahoo ~5 quarters) can get a prior TTM; until then most TSX names are E `NO_SIGNAL / F_INCOMPLETE` | AN/SW | ttm.md + piotroski.md 0.2.0 | todo |
| DEF-7 | D `g_hist` over 3 years (needs dividend history > 2 years, proposal §13); D yield-trap detection beyond realised cuts | AN/SW | Spec 0.2.0 | todo |
| DEF-8 | F: IFRS 16 / ASC 842 CFO break (2019) must be noted in any F backtest; TSX sectors often < 12 names (`SMALL_SECTOR`) | AN/QA | Backtest pre-registration | todo |

## Decisions log
- 2026-11-09: initial testing uses a ~2% subset of the universe (14 tickers, `universe/reference.csv`).
  The peer fair value is therefore mostly FEW_PEERS until P1-2.
- 2026-11-09: data provider choice deferred; free sources behind a parse/fetch split.

- 2026-11-09: issuer filings are the primary reference for financials; Yahoo `bookValue` is secondary
  (wrong for TD, BNS, MFC).

- 2026-11-09: expanded to the full TSX Composite (user request). S&P 500 not yet ingested.

- 2026-11-09: BUY/HOLD/SELL plan approved with PM defaults: FRED host allowed, AN's parameters
  as the starting config (no tuning on the holdout), a survivor-biased US backtest accepted as a stated
  limitation, and one session plus one PR per phase.

- 2026-11-09: Phase 6. History backfill uses `range=2y`, not 13 months (13mo returns ~272 sessions, E6 needs 273).
  Eligibility removal rates came in at TSX 9.3% / S&P 0.2%, below the expected 15–25% / <10%. Reported to PM,
  thresholds not tuned. FRED only answers curl-style User-Agents (documented in `providers/rates.py`).

- 2026-11-09: Phase 7. Model A + Model B book branch, report v2 (no vote). Parameters as approved, not tuned.
  ROE_PERIOD_MISMATCH on 124 TSX / 78 US names traces back to P6-8 (TSX balance-sheet filed date) and to Yahoo NI
  being newer than EDGAR's balance sheet for US names; flagged, not changed.
- 2026-11-09: AN's 10 additional models proposal (`docs/models/proposals/additional_models.md`) accepted
  as a proposal. Only the new document is taken; the planning-doc edits on `claude/an-additional-models`
  are dropped. Its shortlist (H shareholder yield, J EBIT yield + ROC, G Altman Z'' veto) is for after Phase 10.
- 2026-11-09: insurers without a sourced CSM (SLF, GWO, IAG) stay `NO_SIGNAL` / `NEEDS_CSM_INPUT` (user
  decision). No manual inputs will be added for now.

- 2026-11-10: Phase 8. Backtest harness built (own DB `data/backtest.duckdb`). US in-sample 2014-01…2023-10:
  Model A FAIL (12m IC 0.007, t 0.13, spread > 0 in 3/10 years), Model B FAIL (IC −0.054, t −0.86, 4/10). Both stay
  "unvalidated"; vote stays off. Holdout (last 36 month-ends) kept locked for the first frozen-threshold re-test of a
  revised model. Results are a survivor-biased upper bound (158/258 removed names lack prices). No parameter changed.

- 2026-11-10: Phase 9. Data batch 2 (ttm 0.2.0), Model C composite 0.1.0, benchmarks. US in-sample: Model C FAIL
  (12m IC 0.0081, t 0.28; spread > 0 in 6/10 years, mean −1.89%); factor ICs V −0.056, Q 0.022, M 0.001, R 0.038.
  BUY 12m excess vs SPX_TR: A −3.1%, B −1.1%, C −1.9% (equal-weighted universe −1.0%). Holdout still locked; no
  parameter changed. Vote stays off (0 of 3 pass).

- 2026-11-10: Revision round v2 specced (AN, docs only): A 0.2.0 (SELL information only; one-off quarter rule),
  B 0.2.0 (payments/exchanges to EV branch; **diagnostic only**), C 0.2.0 (B/P in book sectors only; per-market
  cross-section), eligibility E6 274, new Model H shareholder yield 0.1.0. Parameters frozen (SHA-256
  `6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e`). Holdout still unspent; one joint run after R2-4.
- 2026-11-10: R2 merged (PR #9). In-sample, A 0.2.0, C 0.2.0 and H 0.1.0 all fail the bar's IC leg, so the unchanged
  bar cannot produce a PASS from the holdout. User approved a pre-registered H confirmation test (freeze §4.7: holdout
  12m IC > 0, BUY − SELL spread > 0, BUY excess vs SPX_TR > 0), recorded before any holdout result existed. Holdout
  run deferred until the user gives the go-ahead. Phase 10 stays blocked (a confirmed H is one model).
- 2026-11-11: Model J (EBIT yield + ROC, `ebit_yield_roc` 0.1.0) specced, tested and wired into `screen_signals`
  (`j_*` columns), shortlist item 2. Ranking per market (as C 0.2.0); not sector-neutral by design. Parameters are the
  proposal's starting values, never tuned on the holdout; J is not part of FROZEN_V2. **J backtest deferred per user**;
  J is labelled new and unvalidated. No vote.

- 2026-11-11: H confirmation test merged (freeze §4.7, PR #10). Addendum (freeze §4a,
  `docs/models/holdout_h_preregistration.md`): INCONCLUSIVE if any criterion has fewer than 18 holdout dates, plus the
  evaluation detail and consequences. Both were registered before any holdout computation. The holdout run still
  waits for the user's go-ahead.

## Next up (PM selection)
1. Phase 6 (data batch 1 + eligibility filter). P2-9 sector gating is superseded by P7-5.
2. Phase 3 spec (AN): peer multiples, including an insurer-appropriate measure (adjusted BV with CSM).
3. P2-8 non-common equity input.
- 2026-11-11: Model G (Altman Z'' distress veto, `altman_z` 0.1.0) and data batch 3 (ttm 0.3.0: current assets,
  current liabilities, retained earnings) specced, tested and wired into `screen_signals` (`g_*` columns incl.
  `g_distress_veto`), shortlist item 3. No BUY; SELL in the distress zone unless `CASH_RICH_OVERRIDE`. Parameters are
  Altman's published values and the proposal's override threshold, never tuned; G is not part of FROZEN_V2.
  **G backtest deferred per user**; G is labelled new and unvalidated. No vote; the veto blocks nothing yet.
- 2026-11-12: Models D (`dividend_sustainability` 0.1.0), E (`piotroski` 0.1.0) and F (`accruals` 0.1.0) specced,
  tested (QA from the specs) and wired into `screen_signals` (`d_*`, `e_*`, `f_*` columns). Parameters are the
  proposal's starting values, never tuned on the holdout; D/E/F are not part of FROZEN_V2. F ranks per (market,
  sector), a stated deviation from the proposal's pooled wording (consistent with C 0.2.0 / J). **D/E/F backtests
  deferred; to be run locally by the user.** All three are labelled new and unvalidated. No vote.
