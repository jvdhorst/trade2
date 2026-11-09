# Model spec: Book Value vs Share Price (`book_value`, v0.2.0)

Owner: AN · Tests: QA (`tests/unit/test_book_value_spec.py`) · Code: `src/valuation/models/book_value.py`

## Purpose
This is the first model, chosen because it is simple to check. Every number can be compared with a company
filing or a public data site. It answers one question: *how does the market price compare with the
accounting net worth per share, and compared with industry peers?*

**Caveat (also shown in the report):** P/B is meaningful mainly for financials and asset-heavy
firms (banks, insurers, energy, materials, REITs). For asset-light firms (software, consumer
brands) book value understates economic value, so the result is only a sanity metric.

## Inputs (per ticker, at valuation date `as_of`)
| Field | Meaning | Unit |
|---|---|---|
| `price` | Close price on or before `as_of` | trading currency |
| `trading_currency` | Currency of the listing (CAD for `.TO`, USD for US) | ISO code |
| `reporting_currency` | Currency of the financial statements | ISO code |
| `total_equity` | Total equity **including** non-controlling interest | reporting ccy |
| `noncontrolling_interest` | Minority interest (default 0) | reporting ccy |
| `preferred_equity` | Preferred shares and other equity instruments in equity, such as LRCNs and AT1 (default 0) | reporting ccy |
| `goodwill`, `intangibles` | For tangible book (default 0) | reporting ccy |
| `shares_outstanding` | Common shares, **all classes**, raw count | shares |
| `fx_rate` | Trading-ccy units per 1 reporting-ccy unit (1.0 if same currency) | ratio |
| `period_end`, `filed_date` | Balance-sheet date; date the data became public | date |
| `share_classes` | Number of common share classes summed into `shares_outstanding` (default 1) | int |
| `sector`, `industry` | Peer grouping keys (may be missing). **v0.2.0:** `sector` = GICS sector from the universe file when present, else the Yahoo sector. `industry` = Yahoo industry | str |

### Filing selection (no look-ahead)
`select_latest_filing(filings, as_of)` returns the filing with the **latest `period_end`** among those
with **`filed_date <= as_of`**. If two candidates share a `period_end`, pick the one with the later
`filed_date` (an amendment). Return `None` if none qualify. Filings with `filed_date > as_of` must never be used.

## Per-company formulas
```
common_equity   = total_equity − noncontrolling_interest − preferred_equity      (reporting ccy)
bvps            = common_equity / shares_outstanding × fx_rate                    (trading ccy)
tangible_equity = common_equity − goodwill − intangibles
tbvps           = tangible_equity / shares_outstanding × fx_rate                  (trading ccy)
pb              = price / bvps          if bvps > 0, else None
```
`compute_book_value` raises `ValueError` if `shares_outstanding <= 0`, `price <= 0`, or `filed_date > as_of`
(the caller must select a filing with `select_latest_filing`; this is a second guard against look-ahead).

### Flags (per company)
| Flag | Condition |
|---|---|
| `NEG_BOOK` | `bvps <= 0`. P/B is not meaningful and the ticker is excluded from peer medians and ranking |
| `STALE_FILING` | `(as_of − period_end).days > 456` (≈ 15 months) |
| `FX_CONVERTED` | `reporting_currency != trading_currency` |
| `MULTI_CLASS` | `share_classes > 1` |

## Peer-relative fair value
Inputs: the list of per-company results for one `as_of`. Let a result be **valid** when `pb is not None`.

For each valid company *c*:
1. `industry peers` = other valid companies with the same non-empty `industry`.
2. If there are at least `min_peers` (default **5**) industry peers, use them (`peer_level = "industry"`).
3. Otherwise, use other valid companies with the same non-empty `sector`. If there are at least `min_peers`, use those
   (`peer_level = "sector"`, add flag `INDUSTRY_FALLBACK`).
4. Otherwise: `fair_value = upside = pb_percentile = peer_median_pb = peer_level = None`, `n_peers = 0`,
   flag `FEW_PEERS`.

With a peer set P (excluding c itself):
```
peer_median_pb = median(pb of P)                   (statistics.median: mean of the middle two values for an even count)
fair_value     = bvps_c × peer_median_pb           (trading ccy)
upside         = fair_value / price_c − 1
pb_percentile  = count(p in P with pb_p < pb_c) / |P|     (0 = cheapest vs peers)
```
Invalid companies (`NEG_BOOK`) get the same empty values (`n_peers = 0`, everything else None) but **no**
`FEW_PEERS` flag, and they are never peers.

**Ranking (report):** sort companies with a fair value by `upside` descending, **ties broken by ticker
ascending**. All others come after them, in ticker order.

## Worked examples (golden values for QA)
Monetary inputs are in raw units. Printed decimals are for display only (6 significant figures). Tests should
assert against the exact expressions, e.g. `1.6/1.2 - 1`, with relative tolerance 1e-9.

**Ex 1: simple.** total_equity 1000, NCI 0, pref 0, goodwill 200, intangibles 50, shares 100,
price 15, same currency →
common_equity 1000, bvps **10**, tbvps **7.5**, pb **1.5**, flags ∅.

**Ex 2: bank with preferreds and NCI.** total_equity 120 000, NCI 2 000, pref 8 000, shares 1 400,
price 140 →
common_equity 110 000, bvps **78.5714** (=110000/1400), pb **1.78182** (=140/78.5714…), flags ∅.

**Ex 3: FX conversion.** reporting USD, trading CAD, fx_rate 1.40, total_equity 500, shares 50,
price 21 →
bvps **14.0** CAD (10 USD × 1.40), pb **1.5**, flags {`FX_CONVERTED`}.

**Ex 4: negative equity.** total_equity −200, shares 100, price 300 →
bvps **−2**, pb **None**, flags {`NEG_BOOK`}.

**Ex 5: look-ahead.** Filing A: period_end 2025-12-31, filed 2026-02-20. Filing B: period_end 2026-03-31,
filed 2026-05-01. `as_of` 2026-04-15 → **A**. `as_of` 2026-05-01 → **B**. `as_of` 2026-01-15 → **None**.
Amendment: A′ has period_end 2025-12-31 and filed 2026-03-10. `as_of` 2026-04-15 with {A, A′, B} → **A′**.

**Ex 6: stale.** period_end 2025-01-31, as_of 2026-05-03 (457 days) → `STALE_FILING`.
as_of 2026-05-02 (456 days) → no flag.

**Ex 7: peers.** All companies have price 50 and the same currency, so `bvps = 50 / pb`.

| Ticker | Sector | Industry | pb |
|---|---|---|---|
| A | Financials | Banks | 1.0 |
| B | Financials | Banks | 1.2 |
| C | Financials | Banks | 1.4 |
| D | Financials | Banks | 1.6 |
| E | Financials | Banks | 1.8 |
| F | Financials | Banks | 2.0 |
| G | Financials | Insurance | 1.0 |
| H | Financials | Insurance | 3.0 |
| I | Energy | Oil & Gas | 1.0 |
| N | Financials | Banks | NEG_BOOK (bvps −1) |

Expected:
- **A**: industry peers B–F (5) → median 1.6, bvps 50, fair_value **80**, upside **0.6**, pb_percentile **0.0**, peer_level industry.
- **D**: peers A,B,C,E,F → median 1.4, bvps 31.25, fair_value **43.75**, upside **−0.125**, pb_percentile **0.6**.
- **F**: peers A–E → median 1.4, bvps 25, fair_value **35**, upside **−0.3**, pb_percentile **1.0**.
- **G**: 1 industry peer → sector fallback. Peers A–F and H (7), median **1.6**, bvps 50, fair_value **80**,
  upside **0.6**, flags ⊇ {`INDUSTRY_FALLBACK`}, peer_level sector, pb_percentile **0.0**.
- **H**: sector peers A–G (7): pbs [1.0,1.2,1.4,1.6,1.8,2.0,1.0] → median **1.4**, bvps 16.6667,
  fair_value **23.3333**, upside **−0.533333**, pb_percentile **1.0**.
- **I**: no peers → fair_value None, flags ⊇ {`FEW_PEERS`}.
- **N**: fair_value None and not counted as a peer by anyone. A still has exactly 5 peers.
- **B**: upside 1.6/1.2 − 1 = **0.333333**. **C**: 1.6/1.4 − 1 = **0.142857**. **E**: 1.4/1.8 − 1 = **−0.222222**.
- Ranking order: A, G, B, C, D, E, F, H, then I, N (alphabetical). A and G tie on upside 0.6, so break ties by ticker ascending.

## Property expectations
- Doubling `shares_outstanding` halves `bvps` and doubles `pb`.
- Expressing equity in another currency with a matching `fx_rate` leaves `bvps` and `pb` unchanged.
- `fair_value / price == peer_median_pb / pb` for valid companies with peers.

## Data mapping (SW, for the record)
| Input | SEC EDGAR (US) | Yahoo (TSX, fallback) |
|---|---|---|
| total_equity | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`, else `StockholdersEquity` + `MinorityInterest` | `Total Equity Gross Minority Interest`, else `Stockholders Equity` + `Minority Interest` |
| noncontrolling_interest | `MinorityInterest` | `Minority Interest` |
| preferred_equity | `PreferredStockValue`, then `…ValueOutstanding`, then `…LiquidationPreferenceValue` | `Stockholders Equity` − `Common Stock Equity` (prefs + other equity instruments), else `Preferred Stock Equity` |
| goodwill / intangibles | `Goodwill` / `IntangibleAssetsNetExcludingGoodwill` | `Goodwill` / `Other Intangible Assets` |
| shares_outstanding | dei `EntityCommonStockSharesOutstanding` (same filing), else `CommonStockSharesOutstanding` | `Ordinary Shares Number` |
| filed_date | actual `filed` | **estimated** as period_end + 45 days |

The source with the freshest `period_end` wins, and EDGAR wins ties. `shares_outstanding` is multiplied by the
universe's `share_conversion` (listed shares per reported share; BRK-B = 1500) before it enters the model.

## External cross-check
Reference set: RY.TO, TD.TO, BNS.TO (banks with preferreds), ENB.TO, CNQ.TO, ABX.TO, NTR.TO
(USD reporters), BN.TO (multi-class, large NCI), SHOP.TO (asset-light, USD reporter), JPM, BRK-B, AAPL, HD
(HD has had negative or low equity).

- Compare `bvps` with Yahoo `bookValue` and `pb` with Yahoo `priceToBook`. Tolerance ±5%. Explain every miss
  (common causes: Yahoo excludes or includes preferreds differently, uses a different share count date,
  or does not FX-convert).
- Issuer check (automated): `valuation crosscheck --issuer` reads "book value per (common) share" from the
  PDFs listed in `universe/issuer_sources.csv`. Tolerance ±3%.
- Results: `docs/qa/book_value-validation.md`.
- Hand-check three banks against the BVPS figure printed in their quarterly supplementary pack. Tolerance ±3%.

## API contract (for SW and QA)
```python
from valuation.models.book_value import (
    Filing, BookValueInputs, BookValueResult, PeerValuation,
    select_latest_filing, compute_book_value, peer_relative, rank, MODEL_VERSION,
)
```
- `Filing(period_end: date, filed_date: date, data: dict)`
- `BookValueInputs`: dataclass with the fields in the Inputs table plus `ticker: str` and `as_of: date`.
  Defaults: NCI/pref/goodwill/intangibles 0, fx_rate 1.0, share_classes 1, sector/industry None.
- `compute_book_value(inputs) -> BookValueResult` with fields `ticker, as_of, price, currency (trading),
  common_equity, bvps, tbvps, pb, flags: frozenset[str], sector, industry`.
- `peer_relative(results: list[BookValueResult], min_peers: int = 5) -> list[PeerValuation]`, same order as input.
  `PeerValuation` fields: `ticker, result, peer_level, n_peers, peer_median_pb, fair_value, upside,
  pb_percentile, flags: frozenset[str]` (the company flags plus any peer flags).
- `rank(valuations: list[PeerValuation]) -> list[PeerValuation]` sorts according to the ranking rule above.
- `MODEL_VERSION = "book_value/0.2.0"`

## Changelog
- **0.2.0:** peer `sector` uses GICS (iShares XIC / S&P 500 constituent data) instead of Yahoo's non-GICS
  sector, which was sometimes missing (RY.TO). The formulas are unchanged.
- **0.1.0:** initial version.
