# Proposal: BUY / HOLD / SELL screening models (draft v0.1, for PM review)

Owner: AN · Status: **proposal**, not a spec. Each model accepted by the PM gets its own `docs/models/<model>.md`
in the `book_value.md` format (inputs, formulas, flags, golden examples, API contract).

> **Screening signals only.** BUY / HOLD / SELL means "worth a human look first / no action suggested / the price looks
> high for the fundamentals". It is a way to sort the queue for human review. It is **not investment advice**, not
> a price forecast and not a trade instruction. The report must print this next to every signal.

Labels in the report: `SCREEN_BUY`, `SCREEN_HOLD`, `SCREEN_SELL`, and `NO_SIGNAL` (model not applicable, too few
peers, or data missing). A ticker that is not well-traded gets `NOT_ELIGIBLE` and no signal.

---

## 0. Shared conventions (all three models)

| Item | Definition |
|---|---|
| Point in time | Every input is selected with `filed_date <= as_of` (`select_latest_filing`). Prices are `<= as_of`. TSX `filed_date` is still estimated (period end + 45 days), so TSX gets the flag `PIT_ESTIMATED`. |
| TTM flow | Sum of the last 4 **discrete** quarters, all with `filed_date <= as_of`. EDGAR has no Q4 10-Q, so Q4 = FY − (Q1+Q2+Q3), checked against the 10-K duration. If fewer than 4 quarters are found → `NO_TTM` and no flow-based metric. |
| Average equity | `avg_common_equity = (CE_t + CE_{t−4q}) / 2`. `CE` is `common_equity` from `book_value`, i.e. after NCI and preferreds. |
| Currency | Ratios are computed in reporting currency. Per-share values are converted with `fx_daily` as in `book_value`. |
| Peer group | GICS sector from the universe file. TSX and US are pooled, because ratios are currency-free. Industry-level groups are used only where ≥ 12 names exist. |
| Cost of equity `r` | `r = rf_local + ERP`, with `rf` = the 10-year government yield on `as_of` (Canada: BoC Valet `BD.CDN.10YR.DQ.YLD`; US: FRED `DGS10`, as built in Phase 6) and `ERP = 5.0%`. No beta: betas are noisy, hard to check and add a fitted parameter. Sensitivity at ±1pp is always reported. |

## 1. "Well-traded" (eligibility filter)

The filter has two jobs. It keeps out names a buy-and-hold investor can't build a position in, and it keeps out
names whose prices are stale or easy to move, which would add noise to the price-based ratios.

| Rule | TSX (CAD) | US (USD) | Why |
|---|---|---|---|
| E1 Index member on `as_of` | TSX Composite | S&P 500 | Universe already defined. Point-in-time membership is needed for backtests (§6). |
| E2 Median daily traded value, last 63 sessions | ≥ **C$5M** | ≥ **US$25M** | `MDTV = median(close_d × volume_d)`. A median ignores one-off block days. Yahoo volume for `.TO` covers the TSX venue only and leaves out alternative venues, so the TSX bar is set lower on purpose. |
| E3 Float-free market cap | ≥ **C$1B** | ≥ **US$5B** | `price × shares_outstanding` (all classes, already computed). Keeps out the smallest index tail. |
| E4 Price | ≥ C$2 | ≥ US$5 | Avoids tick-size noise and penny-stock dynamics. |
| E5 Active trading | ≤ 3 zero-volume days in 63 sessions, and the last close ≤ 5 sessions old | same | Catches halts and stale quotes. |
| E6 History | ≥ 273 sessions of `adj_close` (12 months + 1 month skip) | same | Model C needs momentum and volatility. If this fails, Model C gives `NO_SIGNAL` but A and B may still run. |

- Multi-class names (BN, BRK-B, CTC-A): test the **listed class we report on**. Market cap stays the all-class value.
- Thresholds are config values (`liquidity.yaml`) and are recorded in `runs`. QA reports how many names each rule
  removes. The expected removal rate is under 10% of the S&P 500 and about 15–25% of the TSX. If it is much
  higher, the thresholds get reviewed.
- **Data gap:** the daily spark batch returns closes but not volume, and only for 1 month. We need 13 months of
  daily `close/adj_close/volume` from the Yahoo chart API (existing host): one backfill, then a 5-day incremental.

---

## 2. Model A: Residual income value (intrinsic, "ROE-justified book")

**Purpose.** This is the direct successor to `book_value`. It asks: *given the return this company earns on its book
equity, how much is that book worth?* A bank on 1.0× book that earns ROE = r is fairly priced. One that earns 2r
is cheap at 1.0×. This fixes the known problem that a low P/B is often justified by a low ROE.

**Formulas** (per share, reporting ccy, converted at the end):
```
ROE_0   = clip( NI_common_TTM / avg_common_equity , −0.10, 0.35 )     # clip limits one-offs; the flag records it
ROE_t   = r + (ROE_0 − r) × (T − t)/T        for t = 1..T,  T = 10     # linear fade to r → no terminal value
b       = clip( 1 − DPS_TTM / EPS_TTM , 0, 1 )                          # retention; 0.6 if EPS_TTM ≤ 0
B_0     = bvps (from book_value);  B_t = B_{t−1} × (1 + ROE_t × b)
V_RI    = B_0 + Σ_{t=1..T} (ROE_t − r) × B_{t−1} / (1+r)^t
MoS     = V_RI / price − 1
```
The fade sends excess returns to zero by year 10, which is a conservative, competition-based assumption. With no
terminal value, there's no guessed perpetual growth rate. A cross-check value printed alongside is the single-stage
`justified_PB = (ROE_0 − g)/(r − g)`, with `g = b × ROE_0` capped at 4%.

**Inputs and sources.** `bvps`, `price`, `fx` exist. **NEW:** `NI_common_TTM` (EDGAR `NetIncomeLossAvailableToCommonStockholdersBasic`,
else `NetIncomeLoss` − preferred dividends; Yahoo `quarterlyNetIncomeCommonStockholders`). `DPS_TTM` (Yahoo chart
`events=div`, existing host). `EPS_TTM` (derived as NI / shares). `rf` (BoC Valet existing; US 10y is new: FRED `DGS10` or
treasury.gov). For insurers we also need **CSM after tax** for adjusted book (issuer packs, manual; links to P2-8).

**Applicability.** Ranked: Financials (banks, insurers using CSM-adjusted book, asset managers flagged), Utilities,
Real Estate (flag `FAIR_VALUE_BOOK` for IFRS REITs), Energy, Materials (flag `CYCLICAL_ROE`; use the
average ROE over the last 3 fiscal years when available). Not ranked (`NO_SIGNAL`): `P/B > 8` or `bvps ≤ 0`, where buybacks
or intangibles make book meaningless (AAPL, HD), plus IT, Communication Services and Health Care unless P/B ≤ 3.

**Mapping.**

| Signal | Rule | Reasoning |
|---|---|---|
| BUY | `MoS ≥ +25%` **and** `MoS(r + 1pp) > 0` | One point of `r` moves V by roughly 3–10% (about 4% in the example below), and inputs carry ±5% error. 25% is a margin of safety that survives both. |
| SELL | `MoS ≤ −20%` **and** `MoS(r − 1pp) < 0` | Asymmetric on purpose: we prefer not to flag good franchises as SELL for model noise. |
| HOLD | otherwise | |

**Failure modes.** One-off gains or losses in TTM NI (clip + flag `ROE_CLIPPED`). Cyclical peak ROE in commodities.
Book distorted by buybacks or AOCI swings (banks' unrealised bond losses). Insurers without CSM look expensive.
`r` set without beta treats a utility and a miner the same (sensitivity columns make this visible). The model knows
nothing about growth beyond the retained earnings.

**Worked example.** B_0 = 50, ROE_0 = 14%, payout 40% (b = 0.6), rf 3.5% → r = 8.5%, T = 10.
ROE_1 = 13.45%, RI_1 PV = (0.1345 − 0.085) × 50 / 1.085 = 2.281. Then B_1 = 54.03, ROE_2 = 12.9%, PV = 2.020, …,
ROE_10 = 8.5% (RI = 0). **V_RI = 61.13** (at r = 9.5%: 58.85). Justified P/B check: g = min(0.6 × 0.14, 0.04) = 4% → (0.14 − 0.04)/(0.085 − 0.04) = 2.22×.
- Price 60 → MoS +1.9% → **HOLD**.
- Price 48 → MoS +27.4% and +22.6% at r + 1pp → **BUY**.

**QA.**
- External: ROE_0 vs the issuer's reported ROE in the bank supplementary packs (±1.5pp, definitions differ) and vs
  Yahoo `returnOnEquity` (secondary). V_RI is recomputed in a spreadsheet for 3 golden tickers.
- Backtest: see §6. Model-specific check: within book-anchored sectors, the BUY − SELL spread should beat plain
  low-P/B buckets from `book_value`. If it doesn't, the ROE adjustment adds nothing.

---

## 3. Model B: Quality-adjusted peer multiple (relative, cross-sectional regression)

**Purpose.** *Is this stock's multiple high or low **for its profitability**, compared with its sector today?* It is
relative, so it says nothing about whether the whole market is cheap. That makes it robust to the level of `r`,
which Model A depends on.

**Formulas.** Per GICS sector *s* on `as_of`, fit a robust line (Theil–Sen, which needs no tuning and is reproducible) on the valid names:

| Sector type | Regression | Fair price |
|---|---|---|
| Book-anchored (Fin., Util., RE, Energy, Materials) | `ln(P/B)_i = a_s + β_s × ROE_i` | `bvps × exp(a_s + β_s ROE_i)` |
| All others | `ln(EV/Sales)_i = a_s + β_s × EBIT_margin_i + γ_s × rev_growth_i` | `(EV_fit − net_debt) / shares` |

```
EV            = market_cap + total_debt + NCI + preferred − cash                (reporting ccy)
EBIT_margin   = EBIT_TTM / Revenue_TTM ;   rev_growth = Revenue_TTM / Revenue_TTM(−4q) − 1
residual_i    = ln(actual multiple_i) − fitted_i
implied_upside_i = exp(−residual_i) − 1          (book branch = fair/price − 1 exactly)
resid_pct_i   = percentile of residual_i within sector (0 = cheapest vs fit)
```
Validity: a sector needs ≥ 12 valid names and R² ≥ 0.20, otherwise `NO_FIT` → `NO_SIGNAL`. Exclude a name from the
fit (but still score it) if ROE or margin < 0, P/B ≤ 0 or EV ≤ 0. Winsorize regressors at the sector's 5th/95th percentile.

**Inputs and sources.** Existing: price, shares, equity, NCI, preferreds, bvps. **NEW:** NI (as in A), `Revenue`,
`EBIT` (`OperatingIncomeLoss`; Yahoo `quarterlyOperatingIncome`), `total_debt`, `cash` (EDGAR `LongTermDebt*`,
`DebtCurrent`, `CashAndCashEquivalentsAtCarryingValue`; Yahoo `quarterlyTotalDebt`, `quarterlyCashAndCashEquivalents`).
For banks and insurers, only the P/B branch applies (EV and revenue aren't meaningful for them).

**Mapping.** Use **percentile bands with a minimum gap**. A relative model has no absolute zero, so the bands give a
stable share of names per sector, and the gap stops tightly clustered sectors from producing signals out of noise.

| Signal | Rule |
|---|---|
| BUY | `resid_pct ≤ 0.20` **and** `implied_upside ≥ +15%` |
| SELL | `resid_pct ≥ 0.80` **and** `implied_upside ≤ −15%` |
| HOLD | otherwise |

**Failure modes.** A low R² means profitability doesn't explain multiples in that sector (gated). Peak-cycle margins
look "cheap". Revenue definitions vary (gross vs net revenue for brokers and pipelines). IFRS vs GAAP mixes in
pooled sectors. Conglomerates and holdcos (BN, POW) sit in the wrong peer group. A whole sector can be mispriced
and the model won't notice. TSX's thin sectors are rescued only by pooling with US names.

**Worked example (book branch).** The Financials fit gives a = −0.30, β = 8.0. A bank has ROE 12%, bvps 40, price 56
(P/B 1.40). Fitted ln(P/B) = −0.30 + 0.96 = 0.66 → **fitted P/B 1.935**, fair price **77.39**,
residual = ln(1.40) − 0.66 = −0.3235 → implied upside **+38.2%**. If its residual is in the cheapest 20% of the sector
→ **BUY**.

**QA.**
- External: our trailing P/E, EV/EBITDA and EV/Sales vs Yahoo `trailingPE`, `enterpriseToEbitda` and `enterpriseToRevenue` (±10%, explain misses).
  The regression is reproduced for one sector in a spreadsheet (`LINEST` on ln multiple), matching a, β and the residuals to 1e-6.
  `scipy.stats.theilslopes` is the reference implementation.
- Backtest: §6, run on sector-neutral buckets by construction. Also track R² over time per sector. A sector whose
  fit is unstable goes to the gate list.

---

## 4. Model C: Value–Quality–Momentum–Risk composite (factor score)

**Purpose.** This model doesn't produce a fair value. It ranks stocks on four well-documented, separately checkable
traits: cheap (Value), profitable and conservatively financed (Quality), not in a persistent downtrend (Momentum,
which guards against value traps), and not extremely volatile (Risk). It covers **every** sector, including the
asset-light ones where A and B are weak, and it is the easiest model to backtest.

**Formulas.** For each metric: winsorize at the 5th/95th percentile of the eligible universe, then take the z-score **within
GICS sector**. A factor score is the mean of its available metric z-scores (at least 1 metric required).
```
Value    : E/P = EPS_TTM/price ;  B/P = bvps/price ;  FCF/P = (CFO_TTM − capex_TTM)/market_cap   (non-financials)
Quality  : ROE = NI_TTM/avg_CE ;  GP/A = gross_profit_TTM/total_assets (non-fin.) ;
           −Accruals = −(NI_TTM − CFO_TTM)/total_assets (non-fin.) ;  −Leverage = −total_debt/total_assets (non-fin.)
Momentum : MOM = adj_close(t−21) / adj_close(t−273) − 1            (12-1 month, skip the latest month)
Risk     : −VOL = −stdev(daily log returns, 252d) × √252
Composite S = 0.25·V + 0.25·Q + 0.25·M + 0.25·R      (needs ≥ 3 of 4 factors, else NO_SIGNAL)
```
Equal weights are a deliberate choice: they aren't fitted, so they can't overfit the backtest, and every reviewer
can redo them by hand.

**Inputs.** Available now: price, adj_close (needs 13-month history), bvps, market cap. **NEW:** NI, CFO, capex, gross
profit, total assets, total debt (EDGAR / Yahoo timeseries, see §8).

**Mapping.** Quintiles of S across the whole eligible universe (S is already sector-neutral, so this is fair across sectors):

| Signal | Rule | Vetoes |
|---|---|---|
| BUY | top 20% of S | Not if `bvps ≤ 0` and NI_TTM < 0 (distress), or if MOM is in the bottom 10% |
| SELL | bottom 20% of S | |
| HOLD | middle 60% | |

The vetoes are hard rules because composites can hide a single catastrophic trait.

**Failure modes.** The factors are cyclical: value and momentum can lag for years, so a backtest needs ≥ 10 years.
Momentum crashes at market turns. Sector-neutral z-scores in small sectors (TSX pooled with US is a must). The model
is descriptive, not causal: a high score explains nothing about *why* a stock is cheap. Correlated metrics (E/P
and ROE) double-count earnings quality.

**Worked example** (sector-neutral z-scores already computed):

| Ticker | V | Q | M | R | S | Pct in universe | Signal |
|---|---|---|---|---|---|---|---|
| X | +1.2 | +0.8 | +0.4 | +0.2 | **+0.65** | 92% | **BUY** |
| Y | +1.6 | −0.6 | −1.9 (bottom 10%) | −0.4 | −0.33 | 30% | HOLD (would also be vetoed from BUY) |
| Z | −1.0 | −0.5 | −0.8 | −1.1 | **−0.85** | 6% | **SELL** |

**QA.**
- External: MOM recomputed from Yahoo's adjusted-close CSV for 5 tickers (exact). VOL vs a spreadsheet `STDEV.S × √252`.
  E/P vs 1/Yahoo `trailingPE` (±10%). Golden z-score file of 20 names checked in.
- Backtest: §6. Also report each factor's own spread, so a dead factor is visible rather than hidden in the composite.

---

## 5. Combining the signals

**Don't average fair values.** A gives an absolute value, B a relative one and C a rank, so their units and errors
differ. Use a **vote** among the models that are *applicable* to the ticker:

| Applicable models | SCREEN_BUY | SCREEN_SELL |
|---|---|---|
| 3 | ≥ 2 BUY and 0 SELL | ≥ 2 SELL and 0 BUY |
| 2 (e.g. asset-light: B + C) | both BUY | both SELL |
| ≤ 1 | `NO_SIGNAL` (show the single model's view, unranked) | |

The report shows all three model signals, an `agreement` column (e.g. `2/3`), and the strongest objection, such as
"A: SELL, MoS −24%". Ranking within SCREEN_BUY uses composite S, then MoS. Disagreement is useful information for the
human analyst, so it is displayed, not hidden.

## 6. Backtest design (shared by all models)

| Element | Design |
|---|---|
| Dates | Month-end rebalance, 2014-01 → latest. **Holdout:** the last 36 months are not looked at until thresholds are frozen. |
| Universe | **Point-in-time** index membership on each date. For the S&P 500, rebuild from the Wikipedia change table. For the TSX, use iShares XIC dated holdings files. Names that later leave the index stay in, with their returns, until they leave. |
| Survivorship | Need prices for delisted and acquired names (Yahoo drops many). Without that source, report results as "survivor-biased upper bound" and state it in the result. The US is first, because EDGAR keeps delisted filers' fundamentals. |
| Look-ahead | Fundamentals with `filed_date ≤ date` only. TSX uses the estimated filed date + an extra 30-day lag in backtests (conservative). The liquidity filter and thresholds use only data up to `date`. No restated data: EDGAR original filing values, not later amendments, unless `filed ≤ date`. |
| Returns | Forward 3, 6 and 12-month **total** return (adj_close), in local currency, measured from the next trading day after the signal. |
| Metrics | (1) BUY − SELL equal-weighted spread, sector-neutral, per year. (2) Hit rate = % of BUYs beating their sector median; % of SELLs lagging it. (3) Spearman rank IC of the continuous output (MoS, −residual, S) vs forward return, with a Newey–West t-stat for overlapping 12-month windows. (4) Signal turnover per month. |
| Pass bar (proposal) | Spread > 0 in ≥ 60% of years **and** mean 12-month IC > 0.03 with t > 2 in-sample, and same sign in the holdout. A model that fails stays in the report as "unvalidated" and is excluded from the vote. |

## 7. Recommendation: build order

1. **Liquidity filter + data batch 1** (volume/adj_close backfill, NI, dividends, US 10y). This is cheap, all from
   existing hosts except one, and every model needs it.
2. **Model A first.** It extends `book_value` with only 2 new fields, fixes the known low-ROE value trap, has
   golden-example maths that QA can test exactly, and has the strongest external check (issuer-reported ROE in bank
   packs). It also delivers P2-9's sector gating. Ship **Model B's P/B ~ ROE branch** together with it, because it
   uses the same inputs.
3. **Model C second.** It covers the whole universe including asset-light names, half its inputs are prices, and it's
   the most backtestable. It needs data batch 2 (CFO, capex, gross profit, assets, debt).
4. **Model B's EV/Sales branch last.** It has the most definitional risk (debt, revenue tags) and the weakest fit in
   some sectors.
5. Turn on the vote (§5) only after ≥ 2 models pass the backtest bar.

## 8. Data-gap table

| Field | Models | Free source (US / TSX) | Effort |
|---|---|---|---|
| 13 months of daily close, adj_close, volume | Filter, C | Yahoo chart (existing host); **as built:** `range=2y`, since 13 months gives ~272 sessions and E6 needs 273 / same | S (done, Phase 6) |
| Net income to common, quarterly | A, B, C | EDGAR `NetIncomeLossAvailableToCommonStockholdersBasic` / `NetIncomeLoss`; Yahoo `quarterlyNetIncomeCommonStockholders` | S (TTM/Q4 logic: M) |
| Dividends per share | A | Yahoo chart `events=div` (both) | S |
| US 10y yield | A | FRED `DGS10` or treasury.gov (**new host**) | S |
| Canada 10y yield | A | BoC Valet `BD.CDN.10YR.DQ.YLD` (existing) | S |
| Revenue, operating income (EBIT) | B, C | EDGAR `Revenues` / `RevenueFromContractWithCustomer…`, `OperatingIncomeLoss`; Yahoo `quarterlyTotalRevenue`, `quarterlyOperatingIncome` | M (tag variety) |
| Gross profit, total assets | C | EDGAR `GrossProfit`, `Assets`; Yahoo `quarterlyGrossProfit`, `quarterlyTotalAssets` | S |
| Operating cash flow, capex | C | EDGAR `NetCashProvidedByUsedInOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment`; Yahoo `quarterlyOperatingCashFlow`, `quarterlyCapitalExpenditure` | S |
| Total debt, cash | B, C | EDGAR `LongTermDebt*`, `DebtCurrent`, `CashAndCashEquivalentsAtCarryingValue`; Yahoo `quarterlyTotalDebt`, `quarterlyCashAndCashEquivalents` | M (debt tag chains) |
| Insurer CSM (after tax) | A, B | Issuer supplementary packs only (manual, as in `issuer_sources.csv`) | M, a handful of names |
| Point-in-time index membership | Backtest | Wikipedia S&P 500 changes (existing source); iShares XIC dated holdings | M (TSX: L if dated files are incomplete) |
| Delisted and acquired prices | Backtest | No reliable free source; candidates: Stooq (**new host**), EDGAR for fundamentals only | L, may stay a stated limitation |
| TSX filing dates | All (PIT) | SEDAR+ (no free API) | L, keep the estimate + lag |
| Analyst estimates | **Not used** | Not free and not point-in-time; forward-looking inputs would also hurt checkability | n/a |

Note: Yahoo's free timeseries gives TSX only a few years of quarterly history, so TSX backtests will be short. The
US (EDGAR, 2009+) is the primary backtest market. This strengthens the case for the Phase 5 paid-provider decision.
