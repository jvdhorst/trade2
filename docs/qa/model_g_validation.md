# Model G validation (external checks), as of 2026-11-11

Owner: QA · Spec: [`altman_z.md`](../models/altman_z.md) 0.1.0 §Report and backtest, "External checks"; data rules
[`ttm.md`](../models/ttm.md) §5.2 and §6. This is a screening-signal validation only, not investment advice. G has
never been backtested, and nothing here is a return test. No backtest, holdout or frozen model was run or touched.

## 0. Summary
| Check (spec) | Result |
|---|---|
| Every X_i numerator and denominator (CA, CL, RE, TA, EBIT TTM, equity, TL) vs the 10-K / 10-Q, exact, 2–3 US names | **PASS: exact** for AAPL, MSFT, CAT and SBUX (4 of 4, 24 of 24 inputs) |
| Z'' recomputed independently by hand formula, to 1e-9 | **PASS**: \|Δz\| = 0 for all 4 names (bit-identical) |
| External Z'' (Yahoo has no Z-score) | **n/a** by spec |
| Yahoo cross-check of the TSX path (`quarterlyCurrentAssets` / `CurrentLiabilities` / `RetainedEarnings`) | **blocked**: Yahoo throttled (HTTP 429) from the cloud container; not attempted |
| Live `valuation value` run (report columns `g_*`) | **blocked**: no live DB in the container (no `data/` directory); see §5 |
| Spec tests | `tests/unit/test_altman_z_spec.py` (84) + `test_altman_z_engine_spec.py` (15), green |

| Ticker | BS date (form, filed) | X numerators / denominators match? | Z'' | Zone | Signal | cfo_to_debt | Veto |
|---|---|---|---|---|---|---|---|
| AAPL | 2026-09-26 (10-K, 2026-11-03) | yes, all exact | 3.2024 | safe | HOLD | 1.8112 | False |
| MSFT | 2026-09-30 (10-Q, 2026-10-28) | yes, all exact | 4.7780 | safe | HOLD | 5.5206 | False |
| CAT | 2026-09-30 (10-Q, 2026-11-04) | yes, all exact | 4.4553 | safe | HOLD | None (no debt tag) | False |
| SBUX | 2026-06-28 (10-Q, 2026-07-29) | yes, all exact | −1.0534 | distress | **HOLD** (`CASH_RICH_OVERRIDE`) | 0.3755 | False |

SBUX was added as the negative-retained-earnings name (spec §Risks, "buyback-heavy firms"). Among the candidates checked,
MCD and HD have positive RE (MCD has negative equity only), and AAPL's RE turned positive in 2026 (§4.4).

Environment: no live DB and no live `valuation value` run. Yahoo was not contacted in any way. EDGAR
(`data.sec.gov`) is reachable; requests used the provider's default generic User-Agent
(`trade2-valuation-screener research`).

## 1. Method
A throwaway script (not committed) did the following per name, with as_of 2026-11-11:
1. **Download:** `companyfacts/CIK##########.json` from EDGAR, saved untrimmed in its own directory.
2. **Pipeline:** passed the JSON through `sec_edgar.trim_companyfacts` (what `EdgarProvider.companyfacts` caches),
   then parsed it with `parse_companyfacts` + `parse_quarterly_ni` + `parse_quarterly_flows`. It loaded the facts
   with `ingest.pipeline.upsert_facts` into `store.db.connect(":memory:")`. It selected the balance-sheet filing as
   `build_inputs` does: `bv.select_latest_filing(engine._filings(con, t, "sec_edgar"), as_of)`. EBIT TTM and CFO TTM
   came from `engine.flow_ttm(con, t, "ebit_q" / "cfo_q", as_of)`, the call behind `engine._flow`. From these it
   built `GInputs` exactly as `run_signals` maps them (market US, the GICS sector from `universe/reference.csv` or
   the obvious one, eligible True, plus `EBIT_PERIOD_MISMATCH` if the TTM end ≠ the BS date), then ran
   `valuation.models.altman_z.evaluate`.
3. **Filing (independent):** worked from the *untrimmed* JSON, without any repo code. It considered forms 10-K / 10-Q
   filed ≤ as_of. The balance-sheet accession is the one with the latest equity date
   (`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` or `StockholdersEquity`), with ties
   going to the latest filed. At that accession and date it read `AssetsCurrent`, `LiabilitiesCurrent`,
   `RetainedEarningsAccumulatedDeficit` and `Assets`. Equity is
   `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`, else `StockholdersEquity` +
   `MinorityInterest`. EBIT TTM from `OperatingIncomeLoss` is the latest 10-K annual value + the latest later 10-Q YTD −
   that 10-Q's prior-year YTD comparative. When no 10-Q follows the 10-K, it is the FY value alone. Periods were
   matched on start/end dates, never on `fy`/`fp`.
4. **Compare:** each input with `==` (exact float equality). It then recomputed TL = TA − equity,
   X1 = (CA − CL)/TA, X2 = RE/TA, X3 = EBIT/TA, X4 = equity/TL and Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4 by hand,
   from the filing values and with literal coefficients (not the module's constants), and compared them to the module.

## 2. Inputs: pipeline vs filing (USD $M; every row matched exactly)
| Ticker | Accession (BS) | CA | CL | RE | TA | Equity (rule) | TL = TA − Eq |
|---|---|---|---|---|---|---|---|
| AAPL | 0000320193-26-000026 | 173,857 | 169,915 | 13,700 | 404,548 | 112,093 (`StockholdersEquity`, no NCI tag) | 292,455 |
| MSFT | 0001193125-26-436133 | 186,137 | 151,968 | 354,795 | 779,403 | 470,509 (`StockholdersEquity`, no NCI tag) | 308,894 |
| CAT | 0000018230-26-000057 | 57,834 | 40,666 | 73,428 | 106,843 | 21,482 (incl. NCI tag; `MinorityInterest` −1) | 85,361 |
| SBUX | 0000829224-26-000130 | 7,534.2 | 9,916.9 | −8,542.5 | 28,294.7 | −7,667.1 (incl. NCI tag; `MinorityInterest` 7.2) | 35,961.8 |

EBIT TTM (`OperatingIncomeLoss`), pipeline = filing in every case:
| Ticker | Pipeline TTM (discrete quarters, $M) | Filing derivation | TTM |
|---|---|---|---|
| AAPL | 50,852 + 35,885 + 35,695 + 35,631 (to 2026-09-26) | FY2026 10-K 2025-09-28..2026-09-26 (accn 0000320193-26-000026) | 158,063 |
| MSFT | 38,275 + 38,398 + 40,603 + 45,915 (to 2026-09-30) | FY2026 10-K 155,237 (0001193125-26-323660) + 3M 2026 45,915 − 3M 2025 37,961 (0001193125-26-436133) | 163,191 |
| CAT | 2,660 + 3,085 + 4,295 + 4,214 (to 2026-09-30) | FY2025 10-K 11,151 (0000018230-26-000008) + 9M 2026 11,594 − 9M 2025 8,491 (0000018230-26-000057) | 14,254 |
| SBUX | 278.2 + 890.8 + 828.1 + 980.4 (to 2026-06-28) | FY2025 10-K 2,936.6 (0000829224-25-000114) + 39w 2026 2,699.3 − 39w 2025 2,658.4 (0000829224-26-000130) | 2,977.5 |

For all four names the TTM period_end equals the BS date, so there is no `EBIT_PERIOD_MISMATCH`. The TTM flags are
none, the source is `sec_edgar` and the currency is USD (no `FLOW_CCY_MISMATCH`).

Override inputs (pipeline; not part of the exactness check): CFO TTM AAPL 145,097, MSFT 195,878, CAT 14,184,
SBUX 4,985.9. `total_debt`: AAPL 80,111 (D3), MSFT 35,481 (D3), CAT **None**, SBUX 13,278.6 (D3: `LongTermDebt`; no
CP / ST borrowings tagged).

## 3. Results: module vs hand recomputation
| Ticker | X1 | X2 | X3 | X4 | Z'' (module = hand, full repr) | \|Δz\| | Zone | Signal | cfo_to_debt | Veto | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | 0.009744208 | 0.033864955 | 0.390715070 | 0.383282898 | `3.2023740728074634` | 0 | safe | HOLD | 1.811199 | False | – |
| MSFT | 0.043839965 | 0.455213798 | 0.209379487 | 1.523205371 | `4.777982940670578` | 0 | safe | HOLD | 5.520645 | False | – |
| CAT | 0.160684369 | 0.687251388 | 0.133410705 | 0.251660594 | `4.455292546387891` | 0 | safe | HOLD | None | False | – |
| SBUX | −0.084210117 | −0.301911665 | 0.105231722 | −0.213201230 | `-1.0533545183506612` | 0 | distress | HOLD | 0.375484 | False | `CASH_RICH_OVERRIDE`, `NEG_EQUITY` |

X1–X4 and TL also matched bit for bit, not only to 1e-9. SBUX follows the spec's G4 pattern (a negative-equity
buyback brand) on real data: the zone stays `"distress"` and the override is visible.

## 4. Data anomalies (not fixed in code; for SW / AN)
1. **SBUX's distress call depends on the debt definition (TSX/Yahoo inconsistency).** SBUX is HOLD only because
   cfo_to_debt = 4,985.9 / 13,278.6 = 0.375 > 0.25. The same 10-Q tags `OperatingLeaseLiability` 9,155.5. On a
   lease-inclusive debt figure, like the one Yahoo `TotalDebt` uses for some issuers (`ttm.md` §5.2), the ratio is
   4,985.9 / 22,434.1 = 0.222 < 0.25, and the signal would be **SELL**. EDGAR names never include leases, while
   Yahoo-sourced names (TSX) may, so the override can flip with the source for the same economics. This is the known
   gap in the spec's Data mapping, now shown on a real name. Whether D-rules should add operating leases for the
   override is an AN decision.
2. **CAT: `total_debt` None in every 10-Q** (no D1–D5 concept outside 10-Ks, as in Model J §4.1), so cfo_to_debt is
   None for 3 of 4 quarters. This is harmless today (CAT is safe), but a distressed name that files this way gets
   `NO_OVERRIDE_DATA` and SELL stands even if it is cash-rich. The override is unavailable for such filers outside
   10-K season.
3. **SBUX balance sheet is 136 days old at as_of.** SBUX's FY2026 ended 2026-09-27 and its 10-K had not been filed by
   2026-11-11, so the BS and EBIT TTM both end 2026-06-28. They are consistent (no mismatch flag) and correct as of
   as_of. G has no staleness flag of its own (only the book_value / TTM 456-day rules apply). Not an error; noted for
   readers of the report.
4. **AAPL retained earnings change sign over time.** AAPL reported `RetainedEarningsAccumulatedDeficit` negative from
   the FY2023 10-K (−214) through the Q1 FY2026 10-Q (−2,177; low of −19,154 at 2024-09-28), and positive since
   Q2 FY2026 (12,359; now 13,700). Values are stored as reported, as specified. X2 for AAPL was negative for about
   two years, which shows the buyback-RE risk in spec §Risks on a name that is otherwise very strong.
5. **NCI handling differs by filer, but the result is consistent.** CAT and SBUX tag equity including NCI directly (CAT's
   `MinorityInterest` is −1, negative NCI, stored as reported). AAPL and MSFT tag only `StockholdersEquity` and have no
   `MinorityInterest`, so equity = parent equity. TL = TA − equity, therefore, puts NCI on the equity side for every
   name, as the spec intends. No issue found.
6. **Tag coverage is complete in recent filings.** For all four names, every 10-K/10-Q filed 2023-01-01..2026-11-11
   carries `AssetsCurrent`, `LiabilitiesCurrent`, `RetainedEarningsAccumulatedDeficit` and `Assets` at the
   balance-sheet date, with no 10-Q gaps. The only gap seen: MSFT has no `RetainedEarningsAccumulatedDeficit` in six
   2011–2012 filings (0001193125-11-015947 … 0001193125-12-316848). That would make MSFT `NO_DATA` for those dates
   in any future G backtest. Not relevant to the live screen.
7. **CAT's consolidated captive finance arm** (Cat Financial) puts its borrowings in TL, which depresses X4 (0.25).
   Z'' is still safe at 4.46. This is a structural caveat of applying Z'' to consolidated industrials with finance
   subsidiaries, not a data error.
8. As in Model J §4.3, the companyfacts `fy`/`fp` fields describe the filing, not the period. Both the pipeline and
   this check key on start/end dates.

## 5. Blocked checks and how to run them locally
**Yahoo cross-check of the TSX path.** Status: **blocked: Yahoo throttled (HTTP 429) from the cloud container; not
attempted.** On a machine where Yahoo responds:
1. `uv run valuation ingest --as-of 2026-11-11` (add `--refresh-fundamentals` if the refresh policy would skip a name; the
   `timeseries_v4` cache key already forces one refetch of the batch-3 types), then `uv run valuation value --as-of 2026-11-11`. This
   writes `reports/2026-11-11/screen_signals.csv` with `g_signal, g_no_signal_reason, g_z, g_zone, g_x1..g_x4,
   g_cfo_to_debt, g_distress_veto`.
2. For 2–3 non-financial TSX names (e.g. CNR.TO, CP.TO, ATD.TO), compare the stored `current_assets`,
   `current_liabilities`, `retained_earnings`, `total_assets` and `total_equity` (DuckDB `fundamentals`, source
   `yahoo`, at the selected `period_end`) to Yahoo's quarterly balance sheet ("Current Assets", "Current Liabilities",
   "Retained Earnings", "Total Assets", "Total Equity Gross Minority Interest") for the same quarter end. All should
   match exactly, with flag `PIT_ESTIMATED` on the row.
3. Recompute Z'' from those five values plus `g` EBIT TTM with the formula in §1 step 4. Check |Δz| ≤ 1e-9 vs `g_z`.
   For TSX names, also record whether Yahoo `TotalDebt` includes leases (§4.1).

**Live DB run for the US names.** Status: **not run** (no `data/` directory in the container). Locally:
`uv run valuation ingest --as-of 2026-11-11`, then `uv run valuation value --as-of 2026-11-11`. In
`reports/2026-11-11/screen_signals.csv`, the rows AAPL, MSFT, CAT and SBUX should show the `g_*` values in §3
(the same EDGAR facts, assuming no restatement filed after this check). SBUX comes from `universe/sp500.csv`
(it is not in `universe/reference.csv`). Spec tests:
`uv run pytest -q tests/unit/test_altman_z_spec.py tests/unit/test_altman_z_engine_spec.py`.
