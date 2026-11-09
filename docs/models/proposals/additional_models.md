# Proposal: 10 additional BUY / HOLD / SELL screening models (draft v0.1, for PM review)

Owner: AN · Status: **proposal**, not a spec. Companion to [`buy_hold_sell.md`](buy_hold_sell.md) (Models A, B, C). Each
model accepted by the PM gets its own `docs/models/<model>.md` in the `book_value.md` format.

> **Screening signals for human review, not investment advice.** Every signal below means "worth a human look
> first / nothing suggested / the price looks high for the fundamentals". Nothing here is a forecast, a trade
> instruction or a recommendation to buy or sell any security. The report prints this next to every signal.

Shared conventions are those of the A/B/C proposal §0: `filed_date ≤ as_of` for every input (TSX gets
`PIT_ESTIMATED`), TTM = 4 discrete quarters (`ttm.md`), `avg_X = (X_t + X_{t−4q}) / 2`, `r = rf_local + 5% ERP`,
GICS sector pooled across TSX and US, labels `SCREEN_BUY/HOLD/SELL`, `NO_SIGNAL`, `NOT_ELIGIBLE`. "Phase 9" fields
are CFO, capex, gross profit, total assets, total debt, cash, revenue, EBIT. "NEW" fields are listed in §13.
Thresholds are starting values (config), never tuned on the holdout (PM decision 2026-11-09).

| # | Model | Type | One line |
|---|---|---|---|
| D | Dividend sustainability and implied return | intrinsic (Gordon) | Is the dividend covered, and does `DY + g` beat the cost of equity? |
| E | Piotroski F-score | financial strength | Nine 0/1 accounting signals; 8–9 strong, 0–2 weak |
| F | Accruals (earnings quality) | quality | Earnings far above cash flow fade; far below often recover |
| G | Altman Z'' distress overlay | risk veto | Below the distress zone → SELL, never BUY |
| H | Shareholder yield | payout behaviour | Dividends + net buybacks per unit of price; net issuers → SELL |
| I | Reverse DCF | intrinsic (implied expectations) | What FCF growth does the price need, versus what the firm delivered? |
| J | EBIT yield + return on capital | relative value × quality | Greenblatt-style double rank |
| K | 52-week-high proximity + trend | price-only | Near the high and above trend → BUY; deep drawdown below trend → SELL |
| L | Sector-specific: banks P/TBV ~ ROTCE, REITs P/FFO | intrinsic / relative, sector | The two sectors where generic multiples mislead |
| M | Multiple mean reversion vs own history | time-series | z-score of today's P/B (or P/E) against the firm's own 5-year history |

---

## 1. Model D: Dividend sustainability and implied return

**Purpose.** For dividend payers, the dividend is the most checkable cash number there is. The model asks whether
earnings cover it, and whether the dividend yield plus a sustainable growth rate exceeds the cost of equity.
Evidence: Gordon growth model (Gordon 1962); payout-ratio and dividend-coverage screens are commonly cited; Arnott
& Asness (2003) find higher payout ratios preceded *higher*, not lower, earnings growth.

**Formulas.**
```
EPS_TTM   = NI_common_TTM / shares_outstanding
payout    = DPS_TTM / EPS_TTM                          (None if EPS_TTM ≤ 0 → flag NEG_EARNINGS)
ROE_0     = NI_common_TTM / avg_common_equity          (as Model A, unclipped here)
g_sust    = clip( ROE_0 × (1 − payout), 0, 0.06 )      flag G_CAPPED when clipped
g_hist    = DPS_TTM / DPS_TTM(as_of − 365d) − 1        flag DIV_CUT if g_hist < −0.02
DY        = DPS_TTM / price
k         = DY × (1 + g_sust) + g_sust                 implied return (Gordon: P = D1 / (k − g))
spread    = k − r                                      r = rf + 0.05
V_DDM     = DPS_TTM × (1 + g_sust) / (r − g_sust)      informational only; MoS = V_DDM / price − 1
```
The signal uses `spread`, not `V_DDM`: with `r − g` near 3%, a 1pp change in `g` moves `V_DDM` by ~30%, but moves
`k` by only ~1pp. The 6% cap keeps `g` below `r` and reflects that no large company compounds dividends faster
than nominal GDP for long.

**Inputs.**

| Field | Source | Point in time |
|---|---|---|
| `DPS_TTM`, `DPS_TTM(−1y)` | existing (`dividends`, `ttm.dps_ttm`) | ex-dates ≤ as_of; the −1y value is the same function at `as_of − 365d` |
| `NI_common_TTM`, `avg_common_equity`, `shares` | existing (`ttm`, `book_value`) | `filed_date ≤ as_of` |
| `price`, `rf` | existing | ≤ as_of |

**Applicability.** All sectors with `DPS_TTM > 0`. `NO_SIGNAL`: `DPS_TTM = 0`; `EPS_TTM ≤ 0` (payout undefined;
flag shown); Real Estate (payout against EPS is meaningless under depreciation → Model L2 instead);
fewer than 2 ex-dates in the TTM window (special dividend only, flag `IRREGULAR_DIV`).

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| SELL | `payout > 1.0` **or** `spread ≤ −2.0pp` | The dividend exceeds earnings, or the price implies a return clearly below `r` |
| BUY | `spread ≥ +2.0pp` **and** `payout ≤ 0.75` **and** not `DIV_CUT` | 2pp exceeds the ±1pp `r` sensitivity and the error in `g`; the payout cap requires room to keep paying |
| HOLD | otherwise | |

**Failure modes.** `g_sust` is driven by ROE, so it inherits Model A's one-off-NI problem. Yield traps: a high
`DY` from a falling price (the `DIV_CUT` flag only catches cuts that already happened). Semi-annual payers and
USD-declared dividends on `.TO` listings (the chart gives CAD, consistent with the price, see Phase 6 QA). Banks'
payout is regulated, so `payout ≤ 0.75` is almost always true for them.

**Golden test (inputs made up).** price 45, DPS_TTM 2.00, DPS_TTM(−1y) 1.90, NI_TTM 400M, shares 100M,
avg_common_equity 2,500M, rf 3.5%.

| Step | Value |
|---|---|
| EPS_TTM | 4.00 |
| payout | 0.50 |
| ROE_0 | 0.16 |
| g_sust | 0.16 × 0.5 = 0.08 → **0.06** (`G_CAPPED`) |
| g_hist | 2.00/1.90 − 1 = **+0.052632** (no cut) |
| DY | 2/45 = **0.044444** |
| k | 0.044444 × 1.06 + 0.06 = **0.107111** |
| r | 0.085 |
| spread | **+2.2111pp** |
| V_DDM, MoS | 2.12/0.025 = **84.80**; +0.884444 (informational) |
| **Signal** | **BUY** (spread ≥ 2pp, payout 0.50 ≤ 0.75, no cut) |

Variants: price 50 → DY 0.04, k 0.1024, spread +1.74pp → **HOLD**. EPS 1.80 with DPS 2.00 → payout 1.1111 →
**SELL** whatever the spread. `as_of` before an ex-date → that dividend is not in `DPS_TTM` (look-ahead).

**External cross-check.** `DY` vs Yahoo `trailingAnnualDividendYield` (±5%, Yahoo lags one payment, see Phase 6 QA);
`payout` vs Yahoo `payoutRatio` (±10%); dividends per share vs the issuer's declared dividends (±2%, P6-7).

**Backtest hypothesis.** Among payers, a positive `spread` BUY−SELL 12-month spread of 2–4%/yr, with the SELL side
(payout > 1) doing most of the work (dividend cuts follow). IC vs 12-month return expected 0.02–0.04. Pass bar:
proposal §6 (spread > 0 in ≥ 60% of years, IC > 0.03, t > 2). Specific check: the `payout > 1` SELL bucket should
show a higher subsequent dividend-cut rate than the rest; if it doesn't, the coverage rule is not informative.

**Effort:** S. **Data cost:** none (all fields exist).

---

## 2. Model E: Piotroski F-score

**Purpose.** Nine binary accounting signals on profitability, leverage/liquidity and operating efficiency, summed.
It is a financial-strength screen that separates improving from deteriorating firms, and it needs no price.
Evidence: Piotroski (2000), *Value Investing: The Use of Historical Financial Statement Information to Separate
Winners from Losers*; the effect is strongest among cheap (high book-to-market) stocks, and weaker in large caps
(commonly cited follow-ups).

**Formulas.** Current = TTM flows ending at the latest quarter `t`; prior = the TTM ending at `t − 4q`.
Balance-sheet items at `t`, `t−4q` and `t−8q`. If `TA_{t−8q}` is missing, use `TA_{t−4q}` in its place and set
`PRIOR_TA_FALLBACK`.

| # | Signal = 1 when | Group |
|---|---|---|
| 1 | `ROA = NI_TTM / TA_{t−4q} > 0` | profitability |
| 2 | `CFO_TTM > 0` | |
| 3 | `ROA > ROA_prior`, `ROA_prior = NI_TTM(−4q) / TA_{t−8q}` | |
| 4 | `CFO_TTM > NI_TTM` (negative accruals) | |
| 5 | `total_debt_t / TA_t < total_debt_{t−4q} / TA_{t−4q}` | leverage/liquidity |
| 6 | `CA_t / CL_t > CA_{t−4q} / CL_{t−4q}` | |
| 7 | `shares_t ≤ shares_{t−4q} × 1.005` (no net issuance; 0.5% tolerance for option dilution) | |
| 8 | `GP_TTM / Rev_TTM > GP_TTM(−4q) / Rev_TTM(−4q)` | efficiency |
| 9 | `Rev_TTM / TA_{t−4q} > Rev_TTM(−4q) / TA_{t−8q}` | |

`F = Σ signals`, `n_scored` = signals with all inputs present. `n_scored < 8` → `NO_SIGNAL` (flag `F_INCOMPLETE`).

**Inputs.**

| Field | Source | Point in time |
|---|---|---|
| `NI_common_TTM` (current, −4q), `shares` | existing | `filed_date ≤ as_of`; the prior TTM is `ttm()` on quarters ending ≤ `t − 4q` |
| `CFO_TTM`, `total_assets`, `total_debt`, `gross_profit_TTM`, `revenue_TTM` | Phase 9 | same |
| `current_assets`, `current_liabilities` | **NEW**: EDGAR `AssetsCurrent`, `LiabilitiesCurrent`; Yahoo `quarterlyCurrentAssets`, `quarterlyCurrentLiabilities` | same |

**Applicability.** All sectors except Financials (no gross margin, current ratio or meaningful CFO) → `NO_SIGNAL`.
Real Estate scored only if `gross_profit` is tagged (otherwise `n_scored = 8`). TSX names need 8 quarters of
flows; Yahoo's free series gives ~5, so the prior TTM may come from fiscal-year totals (flag `ANNUAL_PRIOR`) or
the name is `NO_SIGNAL` until a provider decision.

**Mapping.**

| Signal | `n_scored = 9` | `n_scored = 8` | Reasoning |
|---|---|---|---|
| BUY | `F ≥ 8` | `F ≥ 7` | Piotroski's "high" group |
| SELL | `F ≤ 2` | `F ≤ 2` | his "low" group; with one signal missing the bar stays absolute |
| HOLD | otherwise | otherwise | |

**Failure modes.** Binary signals lose magnitude (a 0.01pp margin gain scores like a 5pp one). Acquisitions break
every Δ signal (asset base jumps). Signal 7 penalises stock-funded M&A, which may be fine. Designed for value
stocks; among expensive growth names a high F says little about the price. Tag variety for `GrossProfit`.

**Golden test ($M, made up).** NI_TTM 120 (prior 100), CFO_TTM 150, TA_t 1,000, TA_{t−4q} 950, TA_{t−8q} 900,
total_debt 300 (prior 310), CA 400 / CL 200 (prior 380 / 200), shares 100M (prior 101M), GP_TTM 420 on Rev_TTM
1,200 (prior 400 on 1,150).

| # | Intermediate | Signal |
|---|---|---|
| 1 | ROA = 120/950 = 0.126316 | 1 |
| 2 | CFO 150 | 1 |
| 3 | ROA_prior = 100/900 = 0.111111 < 0.126316 | 1 |
| 4 | 150 > 120 | 1 |
| 5 | 300/1000 = 0.30 < 310/950 = 0.326316 | 1 |
| 6 | 2.00 > 1.90 | 1 |
| 7 | 100 ≤ 101.505 | 1 |
| 8 | 0.35 > 400/1150 = 0.347826 | 1 |
| 9 | 1200/950 = 1.263158 vs 1150/900 = 1.277778 | **0** |
| | **F = 8, n_scored = 9** | **BUY** |

Variant: `TA_{t−8q}` missing → prior uses TA_{t−4q} = 950: ROA_prior 0.105263 (signal 3 still 1), asset turnover
prior 1150/950 = 1.210526 < 1.263158 → signal 9 = **1**, **F = 9**, flag `PRIOR_TA_FALLBACK`, BUY. The fallback
changes the score, so it must be visible.

**External cross-check.** No free published F-score with a stated point-in-time basis; cross-check the components:
`ROA`, gross margin and current ratio vs the issuer's 10-K/10-Q or MD&A ratios (±1% relative); CFO vs the cash-flow
statement (exact). Secondary: Yahoo `returnOnAssets`, `grossMargins`, `currentRatio` (±10%).

**Backtest hypothesis.** In this large-cap, liquid universe the original 23%/yr high-minus-low spread will not
appear; expect 2–5%/yr for F ≥ 8 minus F ≤ 2 with a small, positive IC (0.02–0.04). Pass bar: §6. Specific check: the
spread must be larger within the cheapest half of each sector (by B/P) than in the dearest half, as in the paper.

**Effort:** M (9 signals, prior-TTM logic). **Data cost:** Phase 9 + 2 NEW balance-sheet items (free, S).

---

## 3. Model F: Accruals (earnings quality)

**Purpose.** Earnings have a cash part and an accrual part; the accrual part is less persistent. Firms whose
earnings far exceed cash flow tend to see earnings fade and prices follow; the reverse for firms whose cash flow
exceeds earnings. Evidence: Sloan (1996); Hribar & Collins (2002) show the cash-flow-statement definition of
accruals is more reliable than balance-sheet changes. The anomaly has weakened since publication (commonly cited),
which is why the SELL side carries the weight here.

**Formulas.**
```
accruals_TTM  = NI_common_TTM − CFO_TTM                 (cash-flow-statement definition)
accrual_ratio = accruals_TTM / avg_total_assets          avg = (TA_t + TA_{t−4q}) / 2
cash_conv     = CFO_TTM / NI_common_TTM                  informational, None if NI ≤ 0
acc_pct       = percentile of accrual_ratio within GICS sector (pooled), 0 = most negative
```
Sector-neutral because depreciation makes accruals structurally negative in asset-heavy sectors. A sector needs ≥ 12
valid names, else `NO_SIGNAL`.

**Inputs.** `NI_common_TTM` (existing); `CFO_TTM`, `total_assets` at t and t−4q (Phase 9). All `filed_date ≤ as_of`.

**Applicability.** All sectors except Financials (CFO is dominated by deposit and trading flows). Real Estate
scored within its own sector.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| SELL | `acc_pct ≥ 0.80` **and** `accrual_ratio ≥ +0.05` | top quintile of the sector *and* an absolute floor, so a benign sector produces no SELLs out of noise |
| BUY | `acc_pct ≤ 0.20` **and** `accrual_ratio ≤ −0.05` **and** `NI_common_TTM > 0` | conservative earnings with real profits; the NI condition keeps out loss-makers whose negative accruals are write-downs |
| HOLD | otherwise | |

**Failure modes.** Large non-cash impairments make accruals very negative (flagged as BUY material only if NI > 0).
Working-capital builds for growth look like "bad" accruals. Acquisitions (purchase accounting) distort both NI
and CFO. Lease accounting changes (IFRS 16 / ASC 842) shifted CFO upward in 2019; a backtest must note the break.
Overlap: Model C uses `−accruals/TA` as one quality metric; this model isolates it and gives it a SELL rule.

**Golden test ($M, made up).** TA_t 4,000, TA_{t−4q} 3,600 → avg 3,800. NI_TTM 500 in all three cases.

| Case | CFO_TTM | accruals | accrual_ratio | cash_conv | acc_pct (given) | Signal |
|---|---|---|---|---|---|---|
| 1 | 320 | 180 | **+0.047368** | 0.64 | 0.90 | **HOLD** (below the +0.05 floor) |
| 2 | 280 | 220 | **+0.057895** | 0.56 | 0.90 | **SELL** |
| 3 | 760 | −260 | **−0.068421** | 1.52 | 0.10 | **BUY** |

**External cross-check.** `NI − CFO` vs the issuer's cash-flow statement (exact, both lines are printed); CFO vs
Yahoo `operatingCashflow` (TTM, ±5%).

**Backtest hypothesis.** Sector-neutral SELL bucket underperforms its sector median by 2–4%/yr; the BUY bucket's
edge is small or nil. IC 0.02–0.03 (weak). Pass bar: §6, with the model allowed to pass on the SELL side alone
(hit rate of SELLs lagging the sector median > 55% in ≥ 60% of years), in which case it enters the vote as a
SELL-only model.

**Effort:** S. **Data cost:** Phase 9 only.

---

## 4. Model G: Altman Z'' distress overlay

**Purpose.** A bankruptcy-risk score that acts as a veto: a distressed balance sheet should block a BUY from any
value model (the classic value trap) and is a SELL candidate on its own. Evidence: Altman (1968) Z-score;
the Z'' variant for non-manufacturers (Altman 1995, and the 2000 revisit) with zones 1.10 / 2.60 is commonly cited.

**Formulas.** Z'' (no sales term, book equity, so it applies to service and asset-light firms too):
```
X1 = (current_assets − current_liabilities) / total_assets
X2 = retained_earnings / total_assets
X3 = EBIT_TTM / total_assets
X4 = total_equity / total_liabilities,   total_liabilities = total_assets − total_equity (incl. NCI)
Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
zone: Z'' < 1.10 distress ; 1.10 ≤ Z'' ≤ 2.60 grey ; Z'' > 2.60 safe
```

**Inputs.**

| Field | Source | Point in time |
|---|---|---|
| `total_assets`, `EBIT_TTM` | Phase 9 | `filed_date ≤ as_of` |
| `total_equity` (incl. NCI) | existing (`book_value`) | same |
| `current_assets`, `current_liabilities` | NEW (shared with Model E) | same |
| `retained_earnings` | **NEW**: EDGAR `RetainedEarningsAccumulatedDeficit`; Yahoo `quarterlyRetainedEarnings` | same |

**Applicability.** All sectors except Financials (Altman excludes them), Utilities and Real Estate (regulated or
asset-backed leverage and negative working capital are normal; the zones were not calibrated for them) → `NO_SIGNAL`.
Negative total equity: `X4 < 0`, the formula still works and lands in distress unless X1–X3 are very strong.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| SELL | `Z'' < 1.10` | distress zone |
| HOLD | otherwise | strength alone says nothing about price, so there is **no BUY** |
| Vote veto | `Z'' < 1.10` blocks `SCREEN_BUY` from the combined vote (proposal §5) | hard rule, like Model C's vetoes |

**Failure modes.** Buyback-heavy firms with negative retained earnings and equity (e.g. large US consumer brands)
score "distressed" while generating huge cash: the veto needs an override when `CFO_TTM / total_debt > 0.25`
(flag `CASH_RICH_OVERRIDE`, shown, not silent). Z'' was fitted decades ago on US industrials. Goodwill-heavy
balance sheets inflate X4.

**Golden test ($M, made up).**

| Case | CA | CL | TA | RE | EBIT_TTM | Equity | X1 | X2 | X3 | X4 | Z'' | Zone / signal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 600 | 500 | 2,000 | 400 | 180 | 800 | 0.05 | 0.20 | 0.09 | 800/1200 = 0.666667 | 0.328 + 0.652 + 0.6048 + 0.70 = **2.2848** | grey → **HOLD** |
| 2 | 450 | 600 | 2,000 | −100 | 20 | 300 | −0.075 | −0.05 | 0.01 | 300/1700 = 0.176471 | −0.492 − 0.163 + 0.0672 + 0.185294 = **−0.402506** | distress → **SELL**, veto on |

**External cross-check.** Each X_i's numerator and denominator vs the 10-K/10-Q balance sheet (exact). Z'' itself
against a spreadsheet recomputation (1e-9). Yahoo has no Z-score.

**Backtest hypothesis.** The distress bucket (small, maybe 2–5% of eligible names) should lag its sector median by
> 3%/yr with a hit rate > 55%, and names it vetoes from `SCREEN_BUY` should have lower forward returns than the
un-vetoed BUYs. Pass bar: the veto improves the combined BUY bucket's 12-month return in ≥ 60% of years; IC is not
the right metric for a veto and is reported only.

**Effort:** S. **Data cost:** Phase 9 + 3 NEW items (2 shared with E).

---

## 5. Model H: Shareholder yield

**Purpose.** Cash returned to shareholders per unit of price, counting buybacks as well as dividends, and
penalising net share issuance. Evidence: Boudoukh, Michaely, Richardson & Roberts (2007), *On the Importance of
Measuring Payout Yield*; Pontiff & Woodgate (2008) on share issuance predicting returns; "shareholder yield"
popularised by Faber (commonly cited).

**Formulas.**
```
DY          = DPS_TTM / price
BBY         = (shares_{t−4q} − shares_t) / shares_{t−4q}       net buyback yield (negative = issuance)
SY          = DY + BBY
EPS_TTM     = NI_common_TTM / shares_t ;  payout = DPS_TTM / EPS_TTM
funded      = EPS_TTM > 0 and payout ≤ 1.0                      v1; Phase 9: (DPS_TTM × shares_t + buyback cash) ≤ FCF_TTM
sy_pct      = percentile of SY in the eligible universe (informational)
```
Shares are the all-class count used by `book_value` (after `share_conversion`), at the two balance-sheet dates.

**Inputs.** All existing: `DPS_TTM`, `price`, `shares` at t and t−4q, `NI_common_TTM`. Optional later: buyback
cash (**NEW**, EDGAR `PaymentsForRepurchaseOfCommonStock`; Yahoo `quarterlyRepurchaseOfCapitalStock`) to tell
buybacks from other share-count changes.

**Applicability.** All sectors. `NO_SIGNAL` if `|BBY| > 0.15` (merger, spin-off or recapitalisation; flag
`CORPORATE_ACTION`) or shares at t−4q missing.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `SY ≥ 0.05` **and** `funded` | 5% is roughly rf + ERP − typical growth; funded stops debt-financed payouts being read as strength |
| SELL | `SY ≤ −0.03` | issuance of > 3% a year with no dividend to offset it is the best-documented negative here |
| HOLD | otherwise | |

**Failure modes.** Share count includes shares issued for acquisitions (correctly negative, but the acquisition
may be good). Buybacks at high prices destroy value; the model can't tell. Dual-class conversions change counts.
Banks' buybacks are regulator-gated. Yahoo's quarterly share count for TSX names is sometimes a stale annual figure.

**Golden test (made up).** price 80, DPS_TTM 2.40, shares 1,050M → 1,008M, NI_TTM 4,200M.

| Step | Value |
|---|---|
| DY | 2.40/80 = **0.03** |
| BBY | 42/1050 = **0.04** |
| SY | **0.07** |
| EPS_TTM, payout | 4,200/1,008 = 4.166667; 2.40/4.166667 = **0.576** → funded |
| **Signal** | **BUY** |

Variants: no dividend, shares 200M → 212M: BBY −0.06, SY **−0.06** → **SELL**. Shares 200M → 240M: BBY −0.20 →
`CORPORATE_ACTION`, **NO_SIGNAL**.

**External cross-check.** Share counts vs the 10-Q cover page / issuer's quarterly report (exact); `DY` vs Yahoo
(±5%); for US names, net buyback yield vs (repurchases − issuance) in the cash-flow statement divided by market
cap (±2pp, the two definitions differ by timing and price).

**Backtest hypothesis.** SY top-vs-bottom 12-month spread 3–5%/yr, mostly from the issuer (SELL) side; IC 0.03–0.05.
Pass bar: §6. Specific check: `BBY` alone must have a positive IC; if only `DY` does, Model D already has it.

**Effort:** S. **Data cost:** none.

---

## 6. Model I: Reverse DCF (implied FCF growth)

**Purpose.** Instead of guessing growth to get a value, take the price as given and solve for the growth it
implies, then compare that with what the company has actually delivered. Evidence: Rappaport & Mauboussin,
*Expectations Investing* (2001) (commonly cited); the mechanics are a standard two-stage DCF.

**Formulas.** Equity-side, per share, cost of equity `r = rf + 5%`, horizon `T = 10`, terminal growth `g_T = 2.5%`:
```
FCF_0      = (CFO_TTM − capex_TTM) / shares                 treated as flow to equity (net borrowing ignored)
V(g)       = Σ_{t=1..T} FCF_0 (1+g)^t / (1+r)^t  +  FCF_0 (1+g)^T (1+g_T) / (r − g_T) / (1+r)^T
g_implied  = the g in [−0.50, 0.50] with V(g) = price      (bisection, 200 steps or |V − price| < 1e-6)
hist_g     = (Rev_TTM / Rev_TTM(−3y))^(1/3) − 1             3-year revenue CAGR (revenue is less noisy than FCF)
gap        = g_implied − hist_g
```
Also reported: `g_implied` at `r ± 1pp`, and `V(0)` (value with no growth).

**Inputs.**

| Field | Source | Point in time |
|---|---|---|
| `CFO_TTM`, `capex_TTM`, `Rev_TTM` (now and −3y) | Phase 9 (revenue −3y: EDGAR has it; TSX from Yahoo `annualTotalRevenue`, same endpoint, flag `ANNUAL_HIST`) | `filed_date ≤ as_of` |
| `shares`, `price`, `rf` | existing | ≤ as_of |

**Applicability.** `NO_SIGNAL`: Financials (CFO meaningless), Real Estate (capex = acquisitions), `FCF_0 ≤ 0`,
`hist_g` unavailable. Energy and Materials: use the 3-year average FCF when available (flag `CYCLICAL_FCF`).

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `gap ≤ −3pp` **and** `g_implied ≤ 0.08` | the market needs less growth than the firm delivered, and the absolute hurdle is modest |
| SELL | `gap ≥ +5pp` **and** `g_implied ≥ 0.12` | the price needs growth well above both history and what large firms sustain |
| HOLD | otherwise | |

Asymmetric bands because `hist_g` is backward-looking: demanding less than history is a cheaper bet than
demanding more.

**Failure modes.** FCF is lumpy (capex cycles, working capital); one bad year flips the sign. Ignoring net borrowing
flatters net borrowers. Revenue growth ≠ FCF growth (margin expansion). Stock-based compensation inflates CFO for
tech names (flag `SBC_HEAVY` once SBC is ingested; not in v1). Sensitivity to `r` is large for high-`g_implied`
names, hence the sensitivity columns.

**Golden test (made up).** price 100, FCF_0 5.00 per share, rf 3.5% → r 8.5%, g_T 2.5%, T 10.

| Step | Value |
|---|---|
| g_implied | **0.045557** (V = 100.000000 at this g; bisection) |
| check points | V(0.05) = 103.4718, V(0.06) = 111.7377, V(0) = 70.5853 |
| decomposition at g_implied | PV of years 1–10 = 41.0178; TV = 133.3579; PV(TV) = 58.9822; sum 100.0000 |
| g_implied at r + 1pp (9.5%) | **0.067017** |
| hist_g (given) | Rev 1,000 → 1,295.03 over 3y → **0.090** |
| gap | −0.044443 → ≤ −3pp, and g_implied ≤ 8% → **BUY** |

Variant: price 60 → g_implied **−0.021475** (the market prices a shrinking business), gap −11pp → BUY if FCF is
not cyclical. hist_g 4% with price 100 → gap +0.56pp → **HOLD**.

**External cross-check.** FCF_0 vs the cash-flow statement (CFO and capex lines, exact; definitions: capex =
purchases of PP&E only, state it). `g_implied` recomputed in a spreadsheet with `RATE`-free bisection or Goal Seek
(|Δ| < 1e-4). Revenue CAGR vs 10-K revenues (exact).

**Backtest hypothesis.** The `gap` rank IC vs 12-month return 0.02–0.04; the BUY bucket's edge should be
concentrated in low-`g_implied` names (a value effect in disguise). Pass bar: §6. Specific check: `gap` must add
IC over plain FCF yield (`FCF_0 / price`); if it doesn't, use FCF yield in Model C instead and drop this model.

**Effort:** L (solver, 3-year revenue history, cyclical handling). **Data cost:** Phase 9 + revenue history.

---

## 7. Model J: EBIT yield + return on capital (Greenblatt-style)

**Purpose.** Buy good businesses (high return on capital) at cheap prices (high pre-tax earnings yield on
enterprise value), ranked jointly. Enterprise value makes capital structure comparable across firms, which P/E
and P/B do not. Evidence: Greenblatt (2005), *The Little Book That Beats the Market*; Gray & Carlisle (2012),
*Quantitative Value*, find the EBIT/EV leg does most of the work (commonly cited).

**Formulas.**
```
EV    = market_cap + total_debt + preferred + NCI − cash              (reporting ccy, as Model B)
EY    = EBIT_TTM / EV
IC    = total_equity (incl. NCI, preferred) + total_debt − cash         financing-side invested capital
ROC   = EBIT_TTM / IC
rank_EY, rank_ROC = 1 for the highest, within the scored universe
combined = rank_EY + rank_ROC ;  order by combined, ties by rank_EY ;  pct = position / n
```
Greenblatt's ROC uses net working capital + net fixed assets; the financing-side IC is the equivalent that can be
read off any balance sheet and checked (stated deviation).

**Inputs.** `market_cap`, `total_equity`, `preferred`, `NCI` (existing); `EBIT_TTM`, `total_debt`, `cash` (Phase 9).

**Applicability.** Scored universe = eligible names excluding Financials, Utilities and Real Estate (as Greenblatt
does). `NO_SIGNAL` if `EBIT_TTM ≤ 0`, `EV ≤ 0` or `IC ≤ 0` (flag `NEG_IC`: negative-equity, cash-rich firms), or if
the scored universe has < 12 names.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `pct ≤ 0.20` **and** `EY ≥ rf + 3pp` | top quintile of the joint rank and an absolute yield floor over the 10-year bond |
| SELL | `pct > 0.80` **and** `EY ≤ rf` | bottom quintile and a pre-tax yield below the government bond |
| HOLD | otherwise | |

**Failure modes.** Peak-cycle EBIT (Energy, Materials) ranks high on both legs at the worst time (flag
`CYCLICAL_EBIT`; 3-year average EBIT when available). Operating leases and pensions are debt-like but absent from
`total_debt`. Negative IC after buybacks. EBIT tag variety (`OperatingIncomeLoss` vs derived). Not sector-neutral by
design, so the BUY list tilts toward whichever sector is cheap.

**Golden test (5-name scored universe, $M, rf 3.5%).**

| Name | EBIT | market cap | debt | cash | EV | EY | equity | IC | ROC | rank_EY | rank_ROC | combined | pct | Signal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P | 300 | 2,000 | 700 | 200 | 2,500 | 0.12 | 500 | 1,000 | 0.30 | 1 | 2 | 3 | 0.2 | **BUY** (EY ≥ 0.065) |
| Q | 200 | 3,500 | 600 | 100 | 4,000 | 0.05 | 0 | 500 | 0.40 | 4 | 1 | 5 | 0.4 | HOLD |
| R | 150 | 1,200 | 400 | 100 | 1,500 | 0.10 | 1,200 | 1,500 | 0.10 | 2 | 4 | 6 | 0.6 | HOLD |
| T | 400 | 4,000 | 1,500 | 500 | 5,000 | 0.08 | 1,000 | 2,000 | 0.20 | 3 | 3 | 6 | 0.8 | HOLD (0.8 is not > 0.8) |
| S | 100 | 4,500 | 700 | 200 | 5,000 | 0.02 | 1,500 | 2,000 | 0.05 | 5 | 5 | 10 | 1.0 | **SELL** (EY ≤ 0.035) |

(Preferred and NCI are 0 for all five. R and T tie on combined = 6; R's better EY rank places it first.)
In production the universe is ≥ 12 names; the example only fixes the ranking and tie rules.

**External cross-check.** `EV` vs Yahoo `enterpriseValue` (±10%; Yahoo's debt definition differs); `EBIT_TTM` vs
the income statement's operating income (10-K/10-Q, exact for US); `EY` vs 1 / Yahoo `enterpriseToEbitda` only as a
sanity bound (EBIT < EBITDA).

**Backtest hypothesis.** Top-minus-bottom quintile 12-month spread 3–6%/yr; IC 0.04–0.06, the strongest of the ten
in this universe. Pass bar: §6. Specific check: EY alone vs the joint rank; if ROC adds no IC, keep the simpler
model.

**Effort:** M. **Data cost:** Phase 9 only.

---

## 8. Model K: 52-week-high proximity + trend (price-only)

**Purpose.** A price-only signal that needs no accounting data and covers every eligible name: prices near their
52-week high tend to keep rising (investors anchor on the high and under-react), and prices far below it and below
trend signal that the market is still absorbing bad news, which is the value-trap case for Models A, D, J and M.
Evidence: George & Hwang (2004), *The 52-Week High and Momentum Investing*; a moving-average trend filter is
commonly cited (e.g. Faber 2007 uses a 10-month average).

**Formulas.** Sessions = trading days of the ticker, `adj_close`, all `≤ as_of`:
```
H52     = max(adj_close over the last high_window sessions, incl. as_of)      high_window = 252
N       = adj_close_t / H52                                                      ∈ (0, 1]
SMA     = mean(adj_close over the last sma_window sessions)                     sma_window = 200
trend   = adj_close_t / SMA − 1
dd      = N − 1                                                                   drawdown from the high (informational)
```
`adj_close` is used so splits don't create false highs; dividend adjustment shifts both numerator and denominator
of `N` slightly against the raw-close definition (noted in the cross-check tolerance).

**Inputs.** `adj_close` history (existing, 2-year backfill). Requires `model_c_history_ok` (E6, ≥ 273 sessions),
else `NO_SIGNAL` with `SHORT_HISTORY`.

**Applicability.** All sectors.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `N ≥ 0.95` **and** `trend > 0` | within 5% of the high and above the 200-day average |
| SELL | `N ≤ 0.75` **and** `trend < 0` | a drawdown of 25%+ while still below trend |
| HOLD | otherwise | |

**Failure modes.** In a broad bear market most names are SELL and in a melt-up most are BUY: the signal is not
sector-neutral or market-neutral on purpose (it is a timing guard), but the report should show the share of
BUY/SELL per run so a regime is visible. Momentum crashes at turns (2009, 2020). Whipsaws near 0.95/0.75. Takeover
targets sit at the high with no upside left. As a value screener's component, it should mainly *veto*, not lead.

**Golden test.** Config override `high_window = 5`, `sma_window = 4`. Ten sessions of `adj_close`:
100, 102, 104, 103, 105, 107, 106, 108, 110, 109.

| Step | Value |
|---|---|
| H52 (last 5: 107, 106, 108, 110, 109) | **110** |
| N | 109/110 = **0.990909** |
| SMA (last 4: 106, 108, 110, 109) | 433/4 = **108.25** |
| trend | 109/108.25 − 1 = **+0.006928** |
| **Signal** | **BUY** |

SELL case: 100, 80, 76, 74, 72 → H52 **100**, N **0.72**, SMA (80, 76, 74, 72) **75.5**, trend **−0.046358** →
**SELL**. Look-ahead: adding a bar dated after `as_of` with adj_close 200 changes nothing.

**External cross-check.** `H52` vs Yahoo `fiftyTwoWeekHigh` (±3%: Yahoo uses intraday highs and raw prices);
`SMA` vs Yahoo `twoHundredDayAverage` (±1%); both recomputed exactly from Yahoo's adjusted-close CSV for 5 tickers.

**Backtest hypothesis.** BUY−SELL 12-month spread 4–8%/yr with high volatility and at least one losing year in
three; IC 0.03–0.05 at 6 months, lower at 12. Pass bar: §6. Specific check: the SELL bucket must lag its sector
median over the next 6 months in ≥ 60% of years, which is its job as a veto.

**Effort:** S. **Data cost:** none.

---

## 9. Model L: Sector-specific valuation (banks, REITs)

Generic multiples mislead in two sectors that together are ~60 US + ~30 TSX names. Each branch is its own signal;
the model's signal is the branch's.

### L1 Banks: P/TBV justified by ROTCE
**Purpose.** Banks are valued on tangible common equity and the return on it; every large bank prints ROTCE and
TBVPS in its supplementary pack, so this is the most externally checkable model proposed. Evidence: the justified
P/B from the Gordon model, `(ROE − g)/(r − g)`, is standard in bank equity research (commonly cited).

**Formulas.**
```
TCE_t    = common_equity − goodwill − intangibles          (= tangible_equity in book_value; tbvps exists)
ROTCE    = NI_common_TTM / avg_TCE
payout   = DPS_TTM / EPS_TTM ;  b = 1 − payout ;  g = clip(b × ROTCE, 0, 0.04)
M_fair   = (ROTCE − g) / (r − g)                           justified P/TBV
fair     = tbvps × M_fair ;  MoS = fair / price − 1         also at r ± 1pp
```
**Inputs.** All existing: `tbvps`, `goodwill`, `intangibles`, `NI_common_TTM`, `DPS_TTM`, `shares`, `rf`.
**NEW (TSX only):** GICS sub-industry to identify banks (the XIC file has sector only); interim: a `bank` flag in the
universe file. Optional NEW: CET1 ratio from issuer packs for a capital guard.

**Applicability.** GICS sub-industries Diversified Banks, Regional Banks (US: 25 names; TSX: the Big Six plus
CWB/LB/EQB-type names). `NO_SIGNAL` if `tbvps ≤ 0` or `ROTCE ≤ r − 0.04` (the single-stage formula breaks when
ROTCE ≤ g).

**Mapping.** As Model A: BUY `MoS ≥ +25%` and `MoS(r+1pp) > 0`; SELL `MoS ≤ −20%` and `MoS(r−1pp) < 0`; else HOLD.

**Golden test ($M, made up).** NI_TTM 16,000; TCE 110,000 (prior 100,000); shares 1,750M; DPS_TTM 4.40; price 120;
rf 3.5%.

| Step | Value |
|---|---|
| avg_TCE, ROTCE | 105,000; 16,000/105,000 = **0.152381** |
| tbvps, P/TBV | 110,000/1,750 = **62.857143**; 120/62.857143 = 1.909091 |
| EPS, payout, b | 9.142857; 0.481250; 0.518750 |
| g | 0.51875 × 0.152381 = 0.079048 → **0.04** (capped) |
| M_fair (r 8.5%) | 0.112381/0.045 = **2.497354** |
| fair, MoS | **156.9766**; **+0.308138** |
| M_fair, fair, MoS at r 9.5% | 2.043290; 128.4354; **+0.070295** |
| **Signal** | **BUY** (MoS ≥ 25% and still positive at r + 1pp) |

**External cross-check.** `ROTCE` and `TBVPS` vs the bank's supplementary pack (RY, TD, BNS, JPM print both):
ROTCE ±1.5pp (definitions differ on AOCI and average vs period-end), TBVPS ±3% (extends `issuer_sources.csv`, P7-4).

### L2 REITs: P/FFO vs REIT peers
**Purpose.** REIT earnings are depressed by depreciation on appreciating assets; funds from operations (FFO) is
the sector's accepted earnings measure. Evidence: Nareit FFO definition (commonly cited).
```
FFO_TTM    = NI_common_TTM + D&A_TTM − gains_on_sale_TTM (+ impairments_TTM where tagged)
FFOps      = FFO_TTM / shares ;  P/FFO = price / FFOps ;  ffo_payout = DPS_TTM / FFOps
pffo_pct   = percentile of P/FFO among REITs with FFO > 0 (pooled TSX + US, ≥ 12 names), 0 = cheapest
```
**Inputs.** NEW: `D&A_TTM` (EDGAR `DepreciationDepletionAndAmortization` / `DepreciationAndAmortization`; Yahoo
`quarterlyDepreciationAndAmortization`); `gains_on_sale` (EDGAR `GainsLossesOnSalesOfInvestmentRealEstate` /
`GainLossOnSaleOfProperties`, sparse; absent → 0 with flag `FFO_APPROX`). Issuer supplementals print FFO per
share and are the cross-check (±5%, mainly the gains and impairment adjustments).

**Mapping.** BUY `pffo_pct ≤ 0.20` and `ffo_payout ≤ 0.85`; SELL `pffo_pct ≥ 0.80` **or** `ffo_payout > 1.0`;
else HOLD. `NO_SIGNAL`: FFO ≤ 0, mortgage REITs (GICS puts them in Financials), fewer than 12 REITs.

**Golden test ($M, made up).** NI 200, D&A 300, gains 50, shares 150M, price 36, DPS 2.52 → FFO **450**, FFOps
**3.00**, P/FFO **12.0**, ffo_payout **0.84**; with `pffo_pct = 0.15` (given) → **BUY**. Same with gains missing
→ FFO 500, FFOps 3.3333, P/FFO 10.8, flag `FFO_APPROX`.

**Failure modes (both).** Banks: AOCI swings in TCE; ROTCE at the top of the credit cycle; `g` cap hides
differences. REITs: FFO ignores maintenance capex (AFFO would be better but is not tagged); external growth by
issuance; Canadian REITs report under IFRS with fair-value marks in NI (gains line is essential there).

**Backtest hypothesis.** L1: spread beats plain P/TBV buckets by ≥ 1%/yr (same test as A vs P/B); if not, A covers
banks. L2: P/FFO quintile spread 2–4%/yr within REITs. Pass bar: §6 per branch; small samples mean wide t-stats, so
the holdout sign matters more than the in-sample t.

**Effort:** L1 S (fields exist), L2 M (two NEW tags, FFO definitions). **Data cost:** TSX sub-industry; D&A; gains.

---

## 10. Model M: Multiple mean reversion vs own history

**Purpose.** The other models compare a firm with peers or with an intrinsic value; this one compares it with
*itself*. A firm trading far below its own 5-year valuation range, with its profitability intact, is a different
kind of cheap than a firm that is cheap versus peers. Evidence: valuation-ratio mean reversion at the aggregate
level (Campbell & Shiller 1998); firm-level reversion of multiples is commonly cited but weaker, and it is
conditional on fundamentals not having changed, hence the ROE guard.

**Formulas.** Monthly observations at month-ends `m` over the last `hist_months = 60` months:
```
multiple_m = P/B_m   for book-anchored sectors (Fin., Util., RE, Energy, Materials)        bvps PIT at m
           = P/E_m   otherwise, P/E_m = price_m / EPS_TTM_m, kept only when EPS_TTM_m > 0
z          = ( ln(multiple_now) − mean(ln multiple_m) ) / stdev_sample(ln multiple_m)      needs ≥ 36 obs
roe_guard  = ROE_now ≥ 0.8 × median(ROE_m)                                                 profitability intact
```
Point-in-time at each `m`: `bvps_m` and `EPS_TTM_m` use the filing with `filed_date ≤ m`, exactly as `book_value`
and `ttm` do today, and `price_m` is the last close ≤ m.

**Inputs.**

| Field | Source | Point in time |
|---|---|---|
| Month-end prices, 5y+ | **NEW extension**: Yahoo chart `range=10y` (existing host; one backfill call per ticker) | ≤ m |
| `bvps_m`, `EPS_TTM_m`, `ROE_m` history | existing engine on historical filings. EDGAR: full history. TSX: Yahoo gives ~2y quarterly / ~4y annual → mostly `NO_SIGNAL` (`SHORT_HISTORY`) until a provider decision | `filed_date ≤ m` |

**Applicability.** All sectors; `NO_SIGNAL` with < 36 valid observations, `bvps ≤ 0` (book branch) or
`EPS_TTM ≤ 0` (earnings branch).

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `z ≤ −1.0` **and** `roe_guard` | one standard deviation cheap versus its own history, with profitability intact |
| SELL | `z ≥ +1.0` | one standard deviation dear; no guard, since a de-rating needs no broken fundamentals |
| HOLD | otherwise | |

**Failure modes.** Regime changes: a firm whose business changed (spin-off, merger) has a meaningless history
(flag when `|Δ total_assets| > 30%` in the window). Secular de-ratings look cheap for years. Survivorship: the
history exists only for names still listed. 60 months is one cycle at most. The ROE guard uses the same noisy
TTM NI as elsewhere.

**Golden test.** Override `hist_months = 12`, `min_obs = 12`. Book branch. Historical P/B (12 month-ends):
1.20, 1.30, 1.25, 1.40, 1.35, 1.50, 1.45, 1.30, 1.20, 1.10, 1.15, 1.25. Now: P/B 0.95; ROE_now 0.12; ROE history
median 0.13 (from 0.12, 0.13, 0.14, 0.13, 0.11).

| Step | Value |
|---|---|
| mean(ln P/B) | **0.248695** (exp → 1.282350) |
| stdev_sample(ln P/B) | **0.093374** |
| ln(0.95) | −0.051293 |
| z | (−0.051293 − 0.248695)/0.093374 = **−3.212770** |
| roe_guard | 0.12 ≥ 0.8 × 0.13 = 0.104 → true |
| **Signal** | **BUY** |

Variant: now P/B 1.60 → z **+2.370144** → **SELL**. ROE_now 0.10 with P/B 0.95 → guard fails → HOLD.

**External cross-check.** Each historical `P/B_m` vs `price_m / bvps_m` with bvps from the filing effective at m
(`book_value` is already validated against issuer packs); for US names, 3 historical quarters' BVPS vs the 10-Q
(±1%). `stdev_sample` vs a spreadsheet `STDEV.S` on the log multiples (1e-9).

**Backtest hypothesis.** IC 0.02–0.04 at 12 months, higher at 24–36 months (reversion is slow); the BUY bucket's
edge depends on the ROE guard. Pass bar: §6 at 12 months, and the guard must raise the BUY bucket's return versus
the unguarded z ≤ −1 bucket; if it does not, the model is the weaker twin of Model B and is dropped.

**Effort:** M (historical engine runs, monthly). **Data cost:** 10-year price backfill (free); TSX fundamental history
(provider decision, Phase 5).

---

## 11. Comparison of all 13 models

Correlation with A/B/C is a prior for the human reader; the backtest (§6 of the A/B/C proposal) measures it.

| Model | Type | Data beyond today | Sector coverage | Checkability | Overlap with A / B / C |
|---|---|---|---|---|---|
| A residual income | intrinsic | none (Phase 6 done) | book-anchored | high (issuer ROE) | — |
| B peer regression | relative | Phase 9 (EV branch) | all | high (spreadsheet fit) | — |
| C composite | factor rank | Phase 9 | all | medium (many inputs) | — |
| D dividend | intrinsic | none | payers, ex-RE | high (declared DPS) | A: shares ROE; C: low |
| E Piotroski | strength | Phase 9 + 2 NEW | ex-Financials | medium (9 lines) | C: quality factor, moderate |
| F accruals | quality | Phase 9 | ex-Financials | high (2 lines) | C: contains it, high on that metric |
| G Altman Z'' | risk veto | Phase 9 + 3 NEW | ex-Fin/Util/RE | high (formula) | C: leverage metric, low |
| H shareholder yield | payout | none | all | high (share counts) | D: DY component; A/B/C: low |
| I reverse DCF | intrinsic | Phase 9 + rev history | ex-Fin/RE | medium (solver) | C: FCF/P, moderate; A: low |
| J EBIT yield + ROC | relative × quality | Phase 9 | ex-Fin/Util/RE | high (EV, EBIT) | B EV branch: moderate; C value: moderate |
| K 52-week high | price-only | none | all | high (prices) | C momentum: high |
| L1 banks | intrinsic, sector | none (+TSX sub-industry) | banks | **highest** (packs print ROTCE, TBVPS) | A: high for banks (tangible variant) |
| L2 REITs | relative, sector | 2 NEW tags | REITs | high (supplementals print FFO) | B: low (B uses P/B for RE) |
| M mean reversion | time-series | 10y prices, PIT history | all (US first) | medium (history runs) | B: low (peer vs self); A: low |

## 12. Recommended shortlist: build after Phase 10

| Order | Model | Why |
|---|---|---|
| 1 | **H shareholder yield** | Zero new data, every sector, orthogonal to A/B/C (payout behaviour, not valuation), S effort, strongest SELL evidence (issuance). Can be built the day Phase 10 closes. |
| 2 | **J EBIT yield + ROC** | Phase 9 data only; covers the asset-light sectors where A is `NO_SIGNAL`; best-evidenced single value-quality screen; also an independent check on B's EV/Sales branch. |
| 3 | **G Altman Z'' veto** | The vote has no distress veto beyond C's `bvps ≤ 0 & NI < 0`. Three cheap NEW fields, S effort, and Model E then costs only the scoring logic (same NEW fields). |

Not shortlisted and why: K is cheap but duplicates C's momentum and should wait for C's factor-level backtest; D
is cheap but is mostly A's ROE plus a coverage rule, so run it as a report column first; L1 adds the strongest
external check but is A on tangible book for 30 names, so fold it into A's spec as a bank branch; I and M are the
most expensive and should follow the provider decision.

**Considered, not proposed.** Graham number (`√(22.5 × EPS × BVPS)` is the geometric mean of P/E and P/B, so it is
a weaker Model C value factor); low-volatility / QARP (C already has Risk and Quality); energy EV/reserves (reserves
exist only in annual report text, not in structured free data); insider trading and short interest (not free and
not reliably point-in-time); analyst estimates (not free, not PIT, and forward-looking inputs hurt checkability).

## 13. Data gaps (NEW fields)

| Field | Models | Free source (US / TSX) | Effort |
|---|---|---|---|
| Current assets, current liabilities | E, G | EDGAR `AssetsCurrent`, `LiabilitiesCurrent` / Yahoo `quarterlyCurrentAssets`, `quarterlyCurrentLiabilities` | S |
| Retained earnings | G | EDGAR `RetainedEarningsAccumulatedDeficit` / Yahoo `quarterlyRetainedEarnings` | S |
| Depreciation & amortisation (TTM) | L2 | EDGAR `DepreciationDepletionAndAmortization`, `DepreciationAndAmortization` / Yahoo `quarterlyDepreciationAndAmortization` | S |
| Gains on sale of real estate | L2 | EDGAR `GainsLossesOnSalesOfInvestmentRealEstate`, `GainLossOnSaleOfProperties` (sparse) / none; issuer FFO as reference | M |
| Buyback cash (optional) | H | EDGAR `PaymentsForRepurchaseOfCommonStock` / Yahoo `quarterlyRepurchaseOfCapitalStock` | S |
| Revenue history, 3+ fiscal years | I | EDGAR (exists) / Yahoo `annualTotalRevenue` (same endpoint) | S |
| Dividend history > 2 years | D (`g_hist` over 3y, later) | Yahoo chart `range=10y&events=div` (existing host) | S |
| Month-end prices, 10 years | M | Yahoo chart `range=10y` (existing host), one backfill | S |
| TSX GICS sub-industry | L1, L2 | not in the iShares file; TMX sector pages or a hand-kept flag in `universe/tsx_composite.csv` | M (manual) |
| Bank CET1 ratio (optional guard) | L1 | issuer supplementary packs (as `issuer_sources.csv`) | M, few names |
| TSX fundamental history > 2 years | E, M, I | no free source (Yahoo ~2y quarterly); provider decision (Phase 5) | L |
| Stock-based compensation (later flag) | I | EDGAR `ShareBasedCompensation` / Yahoo `quarterlyStockBasedCompensation` | S |

## 14. Statement

All signals in this document are **screening signals for human review**. They sort a queue of well-traded
stocks for an analyst to look at. They are not investment advice, not forecasts and not instructions to buy or
sell any security, and the report must say so next to every signal.
