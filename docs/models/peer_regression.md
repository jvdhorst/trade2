# Model spec: Quality-adjusted peer P/B (`peer_regression`, v0.2.0) — Model B, book branch (diagnostic only)

Owner: AN · Tests: QA (`tests/unit/test_peer_regression_spec.py`) · Code: `src/valuation/models/peer_regression.py` ·
Source: proposal `docs/models/proposals/buy_hold_sell.md` §0, §3 (task P7-1). The EV/Sales branch is Phase 10.
**v0.2.0: status `diagnostic`** (§Revision v0.2.0): shown for context, never counted toward the Phase 10 gate or vote.

## Purpose
*Is this stock's P/B high or low **for its ROE**, compared with its sector today?* Per GICS sector, a robust line
`ln(P/B) = a + β·ROE` is fitted; a name far below the line is cheap for its profitability. The model is relative, so it
says nothing about whether the whole sector is cheap, and it doesn't depend on the level of `r` (unlike Model A).

> **Screening signal only**, not investment advice. Labels exactly `"BUY"`, `"HOLD"`, `"SELL"`, `"NO_SIGNAL"`,
> `"NOT_ELIGIBLE"`; `SCREEN_` is reserved for the Phase 10 vote. Same disclaimer and report as Model A
> (`residual_income.md` §Report v2).

## Constants
`MIN_FIT = 12`, `MIN_R2 = 0.20`, `WINSOR = (5, 95)`, `BUY_PCT = 0.20`, `SELL_PCT = 0.80`, `BUY_UPSIDE = 0.15`,
`SELL_UPSIDE = -0.15`, `BOOK_SECTORS = frozenset({"Financials", "Utilities", "Real Estate", "Energy", "Materials"})`,
(v0.2.0) `EV_SUB_INDUSTRIES = frozenset({"Transaction & Payment Processing Services", "Financial Exchanges & Data"})`
(exact GICS sub-industry names, as in the backtest's Q4 diagnostic), `STATUS = "diagnostic"`.

## Inputs (`PBObs`, one per ticker, all at the same `as_of`)
| Field | Meaning |
|---|---|
| `ticker`, `sector` | GICS sector (exact name; `None`/`""` → `NO_SECTOR`) |
| `price` | Close ≤ as_of, trading ccy; must be > 0 (else `ValueError`) |
| `bvps` | **Model A's `b0`** (trading ccy; CSM-adjusted for CSM insurers); float or None |
| `roe` | **Model A's `roe_raw`**: TTM basis, unclipped, no 3-year average, CSM-adjusted equity; None if unavailable |
| `eligible` | bool \| None; anything but `True` → `NOT_ELIGIBLE` |
| `flags` | frozenset, passed through (default ∅) |
| `needs_csm` | True when Model A's reason is `NEEDS_CSM_INPUT` (default False) |
| `sub_industry` | v0.2.0: GICS sub-industry (exact name) or None (default None; TSX names have none) |

Decision: Model B uses exactly Model A's book and ROE so both models see the same company (MFC's P/B uses adjusted
book). P/B and ROE are currency-free, so TSX and US names are pooled per sector. There is no P/B cap in Model B
(Theil–Sen is robust to a few extreme points).

## Sets (per book sector s)
```
pb_i      = price_i / bvps_i                                   (only when bvps_i > 0)
ev_name   = sub_industry ∈ EV_SUB_INDUSTRIES                                          (v0.2.0)
fit set   F = {i in s : eligible is True, not ev_name, not needs_csm, bvps > 0, roe is not None, roe >= 0}
scored S  = {i in s : eligible is True, not ev_name, not needs_csm, bvps > 0, roe is not None}  (F ⊆ S; adds roe < 0)
n_fit = |F|, n_scored = |S|
```

## Fit (`fit_sector(obs) -> SectorFit`)
`obs` = the observations of **one** sector (empty, or mixed sectors → `ValueError`); it builds F itself.
1. `lo, hi = winsor_bounds([roe_i for i in F])` = `numpy.percentile(x, [5, 95])` (default linear interpolation).
   `winsor_bounds([])` → `ValueError`.
2. `x_i = min(max(roe_i, lo), hi)` (`roe_w`), applied to every name with a roe (fit, scored and ineligible).
   `y_i = ln(pb_i)`, not winsorized.
3. `β, a = theil_sen(x_F, y_F)`: β = `numpy.median` of `(y_j − y_i)/(x_j − x_i)` over all pairs i < j with
   `x_i ≠ x_j` (pairs with equal x, incl. ties created by winsorizing, are skipped); `a = numpy.median(y − β·x)` over F.
   No pair with distinct x → `ValueError`. This equals `scipy.stats.theilslopes(y, x, method="joint")` (slope,
   intercept); verified to the last printed digit on G2, G2b and G4. The scipy default `method="separate"` is **not** used.
4. `ŷ_i = a + β·x_i`; `R² = 1 − Σ(y − ŷ)² / Σ(y − ȳ)²` over F (may be negative).

Every SectorFit field is computed when possible: `lo, hi` if n_fit ≥ 1; `slope, intercept` if F has a pair with
distinct x; `r2` if slope exists and `Σ(y − ȳ)² > 1e-12`; else None. `status` = the **first** failing check:

| Status | Condition |
|---|---|
| `FEW_NAMES` | `n_fit < 12` |
| `NO_X_VARIATION` | all `x_i` in F equal |
| `ZERO_VARIANCE` | `Σ(y − ȳ)² ≤ 1e-12` (floating-point zero: identical P/Bs leave a rounding residue) |
| `LOW_R2` | `r2 < 0.20` |
| `OK` | otherwise (`n_fit = 12` and `r2 = 0.20` pass) |

## Per-name outputs (only when the sector's status is `OK`, bvps > 0, roe not None, not needs_csm)
```
fitted_pb      = exp(a + β·roe_w)
fair_value     = bvps × fitted_pb                     (trading ccy)
residual       = ln(pb) − (a + β·roe_w)
implied_upside = fair_value/price − 1                 (= exp(−residual) − 1)
resid_pct      = count(j in S, j ≠ i, residual_j < residual_i) / (n_scored − 1)     only for i in S
                 (0 = cheapest vs fit; ties share a value; n_scored = 1 → None, unreachable when status OK)
```
Ineligible names with data get fitted_pb, fair_value, residual and implied_upside (for the human) but
`resid_pct = None`; they never enter F or S. `pb` is reported whenever bvps > 0; `roe_w` whenever lo/hi and roe exist.

## Signal and precedence
`map_signal(resid_pct, upside)`: `"BUY"` if `resid_pct ≤ 0.20 and upside ≥ 0.15`; elif `"SELL"` if
`resid_pct ≥ 0.80 and upside ≤ −0.15`; else `"HOLD"`. For each name the first matching row wins:

| # | Signal / reason | Condition |
|---|---|---|
| 1 | `NOT_ELIGIBLE` | `eligible is not True` (no_signal_reason None) |
| 2 | `NO_SIGNAL` / `NO_SECTOR` | sector None or `""` |
| 3 | `NO_SIGNAL` / `EV_BRANCH_PENDING` | sector not in BOOK_SECTORS, or (v0.2.0) `sub_industry ∈ EV_SUB_INDUSTRIES` (Phase 10) |
| 4 | `NO_SIGNAL` / `NEEDS_CSM_INPUT` | needs_csm |
| 5 | `NO_SIGNAL` / `NEG_BOOK` | bvps None or ≤ 0 |
| 6 | `NO_SIGNAL` / `NO_ROE` | roe None (covers NO_TTM, NEG_AVG_EQUITY, CCY_MISMATCH upstream) |
| 7 | `NO_SIGNAL` / `NO_FIT` | sector status ≠ OK |
| 8 | `map_signal(resid_pct, implied_upside)` | |

Model flags added to the passed-through `flags`: `ROE_WINSORIZED` (`roe_w != roe`), `NEG_ROE_UNFITTED` (in S, not in F).

## Worked examples (golden values; relative tolerance 1e-9, use the exact expressions where given)
**G1 (proposal).** a = −0.30, β = 8.0, roe 0.12, bvps 40, price 56 → fitted ln P/B = −0.30 + 8.0×0.12 = 0.66,
fitted_pb **exp(0.66) = 1.9347923344020315**, fair_value **40·exp(0.66) = 77.39169337608126**, residual
**ln(1.4) − 0.66 = −0.323527763378787**, implied_upside **40·exp(0.66)/56 − 1 = 0.38199452457287975**. Matches the
proposal (1.935, 77.39, −0.3235, +38.2%).

**G1b small functions.** `theil_sen([1,2,3,4], [1,3,2,5]) = (7/6, 1/12)`; `theil_sen([1,1,2], [0,1,3]) = (2.5, −2.0)`
(the pair with equal x is skipped); `theil_sen([1,1], [0,1])` → `ValueError`.
`winsor_bounds([0.0, 0.1, 0.2, 0.3, 0.4]) = (0.02, 0.38)`. `map_signal(0.20, 0.15) = "BUY"`, `(0.2001, 0.5) = "HOLD"`,
`(0.0, 0.1499) = "HOLD"`, `(0.80, −0.15) = "SELL"`, `(0.7999, −0.5) = "HOLD"`, `(1.0, −0.1499) = "HOLD"`.

**G2 full sector (Financials, all bvps 20.0 except U17 and U18).** Input order as listed, plus `T01` (Industrials, price 50,
bvps 10, roe 0.2, eligible) and `T02` (sector None, price 50, bvps 10, roe 0.2, eligible) at the end.

| Ticker | price | roe | elig | other | pb | roe_w | fitted_pb | residual | upside | resid_pct | signal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| U01 | 18 | 0.02 | T | | 0.9 | 0.0395 W | 1.004857315928173 | −0.11020607288875167 | 0.11650812880908124 | 3/14 | HOLD |
| U02 | 21 | 0.05 | T | | 1.05 | 0.05 | 1.0887993870061192 | −0.03628544518600568 | 0.03695179714868502 | 4/14 | HOLD |
| U03 | 25 | 0.07 | T | | 1.25 | 0.07 | 1.2685756761495808 | −0.014751204945060936 | 0.014860540919664711 | 7/14 | HOLD |
| U04 | 19 | 0.08 | T | dup x | 0.95 | 0.08 | 1.3693063937629153 | −0.36559762409873764 | 0.44137515132938465 | 2/14 | **BUY** |
| U05 | 30 | 0.08 | T | dup x | 1.5 | 0.08 | 1.3693063937629153 | 0.09116077839697734 | −0.0871290708247231 | 13/14 | HOLD |
| U06 | 30 | 0.09 | T | | 1.5 | 0.09 | 1.4780355916101569 | 0.01475120494506088 | −0.014642938926562099 | 8/14 | HOLD |
| U07 | 33 | 0.10 | T | | 1.65 | 0.10 | 1.595398385647669 | 0.03365181129746908 | −0.03309188748626124 | 11/14 | HOLD |
| U08 | 36 | 0.11 | T | | 1.8 | 0.11 | 1.7220803229470059 | 0.04425361483518264 | −0.043288709473885745 | 12/14 | HOLD |
| U09 | 52 | 0.12 | T | | 2.6 | 0.12 | 1.8588213861563885 | 0.33556882150858347 | −0.28506869763215825 | 14/14 | **SELL** |
| U10 | 41 | 0.13 | T | | 2.05 | 0.13 | 2.006420315934756 | 0.02148759617954732 | −0.021258382470850767 | 10/14 | HOLD |
| U11 | 44 | 0.14 | T | | 2.2 | 0.14 | 2.165739276606875 | 0.015695589941584265 | −0.015573056087784143 | 9/14 | HOLD |
| U12 | 49 | 0.16 | T | | 2.45 | 0.16 | 2.523333682929258 | −0.029492892769882983 | 0.029932115481329902 | 5/14 | HOLD |
| U13 | 60 | 0.40 | T | outlier | 3.0 | 0.244 W | 4.794269422270641 | −0.4688090456545069 | 0.598089807423547 | 1/14 | **BUY** |
| U14 | 46 | 0.15 | T | | 2.3 | 0.15 | 2.3377088922928304 | −0.016262220939498473 | 0.01639517056210016 | 6/14 | HOLD |
| U15 | 9 | −0.05 | T | neg ROE | 0.45 | 0.0395 W | 1.004857315928173 | −0.803353253448697 | 1.2330162576181625 | 0 | **BUY** |
| U16 | 30 | 0.10 | **F** | | 1.5 | 0.10 | 1.595398385647669 | −0.06165836850685569 | 0.06359892376511267 | None | NOT_ELIGIBLE |
| U17 | 30 | 0.10 | T | bvps −5 | None | 0.10 | None | None | None | None | NO_SIGNAL / NEG_BOOK |
| U18 | 30 | None | T | needs_csm, bvps None | None | None | None | None | None | None | NO_SIGNAL / NEEDS_CSM_INPUT |
| T01 | | | T | | 5.0 | | None | | | | NO_SIGNAL / EV_BRANCH_PENDING |
| T02 | | | T | | 5.0 | | None | | | | NO_SIGNAL / NO_SECTOR |

W = `ROE_WINSORIZED` flag; U15 also has `NEG_ROE_UNFITTED`. fair_value = 20 × fitted_pb. F = U01–U14 (n_fit **14**;
U15 roe < 0, U16 ineligible, U17 bvps ≤ 0, U18 needs CSM are out), S = F + U15 (n_scored **15**, divisor 14).
`SectorFit("Financials")`: lo **0.02 + 0.65×0.03 = 0.0395**, hi **0.16 + 0.35×0.24 = 0.244**
(numpy: 0.24399999999999994), 90 slope pairs (91 minus the U04/U05 tie), slope **7.640957345191646**, intercept
**−0.2969722579041446**, r2 **0.7449629739969964**, status **OK**. U08 has resid_pct 0.857 ≥ 0.80 but upside −4.3%
→ HOLD (minimum-gap rule). `run_peer_regression` returns the fits dict with the single key `"Financials"`.

**G2b n_fit boundary.** G2 with U01 and U02 ineligible → n_fit **12** → status OK: lo 0.0755, hi 0.268 (numpy
0.26799999999999985), slope **6.642676700452296**, intercept **−0.16349238213274048**, r2 **0.5358034854710718**.

**G3 too few names.** G2 with U01, U02 and U03 ineligible → n_fit **11** → status **FEW_NAMES**; every eligible
Financials name with data → NO_SIGNAL / NO_FIT (fitted values and resid_pct None); U01–U03 → NOT_ELIGIBLE.
U17, U18 keep their earlier reasons (precedence).

**G4 low R².** Sector Energy, 12 eligible names N01–N12, bvps 10.0, roe 0.02, 0.04, …, 0.24 (step 0.02), prices
12, 25, 14, 22, 11, 19, 30, 13, 16, 28, 15, 18 → n_fit 12, lo **0.031**, hi **0.229** (numpy 0.22899999999999998),
slope **1.1467076579307753**, intercept **0.29643019419066896**, r2 **−0.13506065275017787** → status **LOW_R2**; all
12 names NO_SIGNAL / NO_FIT with `n_fit = 12` and `r2` as above in each PBResult.

**G5 (v0.2.0) payments / exchanges leave the book branch.** G2 plus `U19` (Financials, sub_industry
`"Financial Exchanges & Data"`, price 300, bvps 20, roe 0.45, eligible) and `U20` (Financials, sub_industry
`"Transaction & Payment Processing Services"`, price 400, bvps 10, roe 0.60, eligible) → both **NO_SIGNAL /
EV_BRANCH_PENDING**, every per-name output None except `pb` (15.0, 40.0); the Financials SectorFit and every other row
are **identical to G2** (n_fit 14, slope 7.640957345191646). In v0.1.0 they would have entered F (n_fit 16) and
steepened the slope, the P7-14 distortion. `sub_industry` `"Asset Management & Custody Banks"` or None → unchanged
v0.1.0 treatment.

## Property expectations
- v0.2.0: adding a name with `sub_industry ∈ EV_SUB_INDUSTRIES` (any values) never changes any SectorFit or any other
  name's result.
- Multiplying every price in a sector by k > 0: intercept → intercept + ln k (G2 × 2: 0.39617492265580084 =
  −0.2969722579041446 + ln 2), slope, r2, residuals, resid_pct, upside and signals unchanged.
- Adding an ineligible, a needs_csm, a NEG_BOOK, a roe-None or a non-book-sector name never changes any SectorFit.
- Adding a negative-ROE eligible name never changes the fit (except `n_scored` + 1), but does change resid_pct of the others (it is in S).
- Permuting the input order permutes the results identically; fits are unchanged.
- `implied_upside == exp(−residual) − 1` and `fair_value/price == fitted_pb/pb` (1e-12).
- Results are in input order, one per PBObs; each name's `n_fit`, `r2` come from its sector's fit (None for non-book sectors).

## API contract (SW and QA)
```python
from valuation.models.peer_regression import (
    PBObs, SectorFit, PBResult, MIN_FIT, MIN_R2, WINSOR, BOOK_SECTORS, EV_SUB_INDUSTRIES, STATUS, MODEL_VERSION,
    theil_sen, winsor_bounds, fit_sector, map_signal, run_peer_regression,
)
```
- `theil_sen(x, y) -> tuple[float, float]` (slope, intercept); `winsor_bounds(x) -> tuple[float, float]`.
- `PBObs(ticker, sector, price, bvps, roe, eligible, flags=frozenset(), needs_csm=False, sub_industry=None)`: frozen dataclass.
- `SectorFit`: `sector, n_fit, n_scored, slope, intercept, r2, lo, hi, status` (`"OK"` or a status code above).
- `run_peer_regression(obs) -> tuple[list[PBResult], dict[str, SectorFit]]`: one fit per book sector present in obs
  (incl. sectors with n_fit 0 → FEW_NAMES), no entries for other sectors.
- `PBResult`: `ticker, sector, signal, no_signal_reason, pb, roe, roe_w, fitted_pb, fair_value, residual, resid_pct,
  implied_upside, n_fit, r2, flags`. `map_signal(resid_pct, upside) -> str`. `MODEL_VERSION = "peer_regression/0.2.0"`.

## Data mapping and look-ahead (engine, SW)
The engine first runs Model A for every ticker at `as_of`, then builds one PBObs per ticker from it: `bvps = b0`,
`roe = roe_raw`, `needs_csm = (no_signal_reason == "NEEDS_CSM_INPUT")`, `eligible` and `sector` as for Model A, `price`
the same close, `sub_industry` = `securities.gics_sub_industry` (v0.2.0; current GICS applied to history, as for sectors).
Report (v0.2.0): the disclaimer line adds `Model B is diagnostic only.`; columns unchanged. All of these come from data with `filed_date ≤ as_of` and prices ≤ as_of (Model A's mapping), so the
regression never sees later data. All names of one `as_of` run are fitted together, TSX and US pooled.

## External check (P7-4)
For one sector (Financials on the run date) QA exports `ticker, pb, roe, eligible` and reproduces lo/hi, slope,
intercept and every residual independently (brute-force pairwise slopes in numpy or pandas, `numpy.median`) to 1e-6, and
compares slope and intercept with `scipy.stats.theilslopes(y, x, method="joint")` (scipy is a dev dependency,
not a project dependency). Results: `docs/qa/`.

## Known limitation (for PM review)
Scored names with roe < 0 are predicted at `roe_w = lo` (the sector's 5th-percentile ROE), so a loss-maker trading
well below book can rank as the cheapest name (U15 in G2: BUY, upside +123%). This follows the proposal (score but don't
fit, winsorize regressors). The `NEG_ROE_UNFITTED` flag makes it visible; a rule change (e.g. cap such names at HOLD)
needs a PM decision.

## Revision v0.2.0 (revision round v2; evidence: `docs/qa/backtest_model_b.md`, phase8-validation §5 Q4, §6.4)
**R1 payments, exchanges and data leave the book branch (P7-14).** Their equity book is not an operating asset (fee
businesses with goodwill and buybacks; P/B 7–100), so a P/B~ROE line through them is meaningless and they steepen the
Financials slope for everyone else (phase7 §6e: ALL +466%). They wait for the EV branch, like other asset-light names.
*Evidence:* Financials IC −0.078 / spread −3.7% as specified; −0.009 / −0.9% with these two sub-industries out of the
fit. The sub-industry split (MIN_FIT 12) never takes effect in the S&P 500, so it is not pursued. *Limitation:* TSX
names have no sub-industry and stay in the book branch (e.g. X.TO); backlog. *Overfitting risk:* low to medium: the
exclusion is economic and was proposed in Phase 7 before the backtest, but the exact two sub-industries are the ones
the diagnostic measured.

**R2 Model B becomes diagnostic only (not retired, not sign-flipped).** The sign problem is general, not a Financials
artefact: 12m IC −0.054 overall and negative at 3, 6 and 12m; the plain low-P/B baseline is negative too (−0.037);
R1 brings Financials to about zero, not positive. "Dear for its ROE" kept winning in 2014–2019 and reversed in
2020–2021: a value-regime effect that a structural fix can't remove. Flipping the sign would be pure in-sample fitting
and contradicts the economics. Retiring it would lose useful context (where a name sits vs its sector's P/B~ROE line,
which the human reads next to Model A). So B keeps running and is shown, is labelled diagnostic in the report, is still
measured in the backtest and the single holdout run (for information), and **never counts toward the Phase 10 gate**
(`revision_v2_freeze.md` §4). Re-promotion needs a new spec and a fresh out-of-sample period. Overfitting risk: none
(no parameter changed for performance).

## Changelog
- **0.2.0:** revision round v2: `EV_SUB_INDUSTRIES` → `EV_BRANCH_PENDING` (P7-14), `sub_industry` input, `STATUS =
  "diagnostic"`, G5. Fit maths unchanged.
- **0.1.0:** initial version, book branch from proposal §3. Proposal G1 arithmetic verified, no corrections.
  QA review: ZERO_VARIANCE uses a 1e-12 tolerance; the "negative-ROE name never changes the fit" property excludes
  `n_scored`, which counts it.
