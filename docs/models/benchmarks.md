# Spec: market benchmark yields and returns (`benchmarks`, v0.1.0)

Owner: AN · Status: **spec**, ready for QA tests and SW · Code (planned): `src/valuation/benchmarks.py`

> Benchmarks give context for the screening signals. They are not signals themselves and not investment advice.

## Purpose
Every model signal so far is *relative*: sector-neutral spreads, peer residuals, BUY versus SELL buckets. A human
reviewer also needs the *absolute* yardstick: what the plain index, or a cheap ETF tracking it, yields and returns
over the same window. This spec defines that yardstick, so that:
1. the daily report header shows benchmark yields and returns next to the screen;
2. the backtest (Phase 8, `backtest.md`) reports each model's BUY bucket **excess return versus the benchmark of
   its market**, in addition to the sector-neutral BUY−SELL spread;
3. Model A and the proposed Model D can be sanity-checked against the market's own yield (`rf` vs index yield).

## Benchmarks
Prices and distributions come from the Yahoo chart API (existing provider, `events=div`). The availability below
was checked on 2026-11-10.

| Key | Symbol | What | Currency | History | Role |
|---|---|---|---|---|---|
| `SPX_TR` | `^SP500TR` | S&P 500 total-return index (dividends reinvested) | USD | 1988→ | **primary US return benchmark** |
| `SPX` | `^GSPC` | S&P 500 price index | USD | 1984→ | price-only reference |
| `SPY` | `SPY` | SPDR S&P 500 ETF | USD | 1993→ | investable; **primary US yield benchmark** |
| `IVV` | `IVV` | iShares Core S&P 500 ETF | USD | 2000→ | investable cross-check |
| `VOO` | `VOO` | Vanguard S&P 500 ETF | USD | 2010→ | investable cross-check |
| `TSX` | `^GSPTSE` | S&P/TSX Composite price index | CAD | 1984→ | price-only reference |
| `XIC` | `XIC.TO` | iShares Core S&P/TSX Capped Composite ETF | CAD | 2001→ | **primary TSX return and yield benchmark** (tracks our TSX universe) |
| `XIU` | `XIU.TO` | iShares S&P/TSX 60 ETF | CAD | 1999→ | large-cap cross-check |
| `ZCN` | `ZCN.TO` | BMO S&P/TSX Capped Composite ETF | CAD | 2010→ | investable cross-check |

The S&P/TSX total-return index is not available free (`^TXCT` → not found). For the TSX, total return uses XIC's
`adj_close` (distributions reinvested, net of a ~0.06% fee). This is stated in the report.

Configuration lives in `universe/benchmarks.csv` (key, symbol, market, role), so adding a benchmark needs no code change.

## Definitions (all point in time: prices and ex-dates ≤ `as_of`)
```
dist_TTM(as_of)  = Σ distribution amounts with as_of − 365d < ex_date ≤ as_of          (ETFs only)
dist_yield       = dist_TTM / close(as_of)                                               trailing distribution yield
yield_spread     = dist_yield − rf_10y(market)                                           vs BoC (CAD) or FRED DGS10 (USD)
TR(h)            = level(as_of) / level(as_of − h) − 1     level = TR index, else ETF adj_close
TR_ann(h)        = (1 + TR(h))^(12/h_months) − 1           for h ≥ 12 months
excess(bucket,h) = mean forward return of the bucket over h − TR(h) of its market's primary benchmark
```
- `level(as_of − h)` uses the last close on or before that date.
- Horizons: 1, 3, 6 and 12 months, and 3 and 5 years annualized.
- Indices pay no distributions, so `dist_yield` is reported for ETFs only. `SPX` and `TSX` show price return only.
- Report precision: yields in % to 2 decimals, returns in % to 1 decimal.

**Not a yield measure here:** SEC 30-day yield and the ETF provider's "distribution yield" (annualized last payment)
differ by definition. We use the trailing 12-month figure because it can be recomputed from public ex-dates.

## Outputs
1. Table `benchmarks_daily(date, key, close, adj_close, dist_ttm, dist_yield, rf_10y, yield_spread)`.
2. Report header block in `screen_signals.csv` (comment lines after the disclaimer), plus
   `reports/<date>/benchmarks.csv` with the full metrics: yields, spreads, TR(1m..5y).
3. Backtest (`backtest.md`): an `excess_vs_benchmark` column per model, horizon and year, with primary
   benchmark `SPX_TR` for US and `XIC` for TSX. This is reported alongside the pass bar, not as a new pass criterion.
   The PM decides whether to make it one.

## Golden example (made-up numbers; QA turns this into a unit test)
ETF with close 500.00 on `as_of` = 2026-11-10. Distributions with ex-dates 2025-12-15 (1.60), 2026-03-16 (1.70),
2026-06-15 (1.75) and 2026-09-15 (1.80). One more distribution of 1.50 has ex-date 2025-11-10, which is exactly
365 days back, so it is **excluded** (the window is open on the left). A distribution with ex-date 2026-11-11 is
after `as_of`, so it is excluded (look-ahead). rf_10y = 4.00%.

| Step | Value |
|---|---|
| dist_TTM | 1.60 + 1.70 + 1.75 + 1.80 = **6.85** |
| dist_yield | 6.85 / 500 = **0.013700** (1.37%) |
| yield_spread | 0.0137 − 0.04 = **−2.63pp** |
| TR(12m), adj_close 400.00 → 452.00 | **+0.130000** |
| TR_ann(3y), adj_close 300.00 → 452.00 | (452/300)^(1/3) − 1 = **+0.146408** |
| excess(BUY bucket, 12m), bucket mean +18.0% | 0.18 − 0.13 = **+0.050000** |

## External cross-checks (QA)
| Check | Source | Tolerance |
|---|---|---|
| SPY, IVV, VOO trailing 12-month distributions | issuer distribution history pages (SSGA, iShares, Vanguard) | exact per payment; yield ±0.05pp |
| XIC, XIU trailing distributions | BlackRock Canada distribution history (`www.blackrock.com`, allowed host) | exact per payment |
| TR consistency | 12-month SPY `adj_close` return vs `^SP500TR` return | within 0.25pp (expense ratio + dividend timing) |
| Index level | `^GSPC`, `^GSPTSE` close vs another public quote for 3 dates | ±0.1% |

## Failure modes
- ETF distributions include return of capital and occasional special or year-end capital-gain distributions
  (common for Canadian ETFs in December). These inflate `dist_yield`. Flag `SPECIAL_DIST` when a payment is more
  than 2× the median of the other payments in the window.
- Yahoo `adj_close` is rebased after each distribution. Recompute TR from the cached series on the same day; never
  mix days.
- Currency: US benchmarks are in USD and TSX ones in CAD. Excess returns compare each market with its own benchmark,
  in local currency; no FX conversion.
- Using the investable ETF as the TSX return benchmark builds its fee into the benchmark. This is small (~0.06%/yr)
  and stated.

## Effort
S. All hosts are already allowed and the chart provider already returns dividends (Phase 6). The work is a small
ingest step, a metrics function, a report block and a backtest column.
