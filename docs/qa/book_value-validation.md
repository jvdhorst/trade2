# QA validation: `book_value` v0.1.0

Run date: 2026-11-09 · Universe: `universe/reference.csv` (14 tickers, the ~2% initial-testing subset)
Command: `valuation ingest && valuation value && valuation crosscheck` (all with `--as-of 2026-11-09`)

## 1. Spec ↔ implementation
- `tests/unit/test_book_value_spec.py` has 82 tests. QA wrote them from `docs/models/book_value.md` only, without
  reading the code. They cover worked examples Ex 1–7, look-ahead and amendment selection, the stale boundary, peer
  fallback, ranking, property tests, and error cases. **All pass.**
- QA's spec review found 9 ambiguities. AN resolved them in the spec: the tie-break rule, empty peer fields for
  FEW_PEERS and NEG_BOOK, where ValueError is raised, a look-ahead guard in compute, display-only decimals, and
  Ex 2 flags. Tests for these were added.
- Parser and pipeline tests: `tests/unit/test_providers.py` and `tests/integration/test_pipeline_offline.py`.
  They are offline and check FX conversion, source freshness, look-ahead in prices and filings, share-class
  conversion, idempotent ingest and the report round-trip.

## 2. External cross-check vs Yahoo `bookValue` (tolerance ±5%)
`d_bvps` = ours / Yahoo − 1. For USD reporters listed on the TSX, Yahoo quotes BV in CAD, which we match
after conversion.

| Ticker | Src | Period | Our BVPS | Yahoo BV | d_bvps | Result |
|---|---|---|---|---|---|---|
| AAPL | EDGAR | 2026-09-26 | 7.71 | 7.71 | +0.01% | pass |
| HD | EDGAR | 2026-08-02 | 16.66 | 16.65 | +0.03% | pass |
| JPM | EDGAR | 2026-09-30 | 132.24 | 132.30 | −0.05% | pass |
| BRK-B | Yahoo | 2026-06-30 | 348.26 | 332.44¹ | +4.8% | pass (period differs) |
| RY.TO | Yahoo | 2026-07-31 | 96.70 | 96.73 | −0.03% | pass |
| ENB.TO | Yahoo | 2026-03-31 | 26.63 | 27.26 | −2.3% | pass |
| CNQ.TO | Yahoo | 2026-06-30 | 22.62 | 22.94 | −1.4% | pass |
| ABX.TO | Yahoo (USD→CAD) | 2026-06-30 | 23.41 | 23.30 | +0.5% | pass |
| NTR.TO | Yahoo (USD→CAD) | 2026-06-30 | 76.43 | 76.02 | +0.5% | pass |
| SHOP.TO | Yahoo (USD→CAD) | 2026-06-30 | 13.89 | 13.92 | −0.2% | pass |
| BN.TO | Yahoo (USD→CAD) | 2026-06-30 | 26.85 | 26.72 | +0.5% | pass |
| **TD.TO** | Yahoo | 2026-07-31 | 69.66 | 75.62 | −7.9% | **open** |
| **BNS.TO** | Yahoo | 2026-07-31 | 64.99 | 73.15 | −11.2% | **open** |
| **MFC.TO** | Yahoo | 2026-06-30 | 28.04 | 31.24 | −10.2% | **open** |

¹ Yahoo quotes BRK's book value per class-A share (498,663). We divide it by 1,500 to compare per B share.
Yahoo's `priceToBook` for BRK-B (0.0011) mixes the B price with A-share book value and is wrong.

**11/14 pass.**

## 3. Misses found and fixed during validation
1. **JPM +6%.** JPM doesn't tag `PreferredStockValue` after 2009, so preferreds counted as 0. Fix: a fallback chain
   ending in `PreferredStockLiquidationPreferenceValue` ($21.2B). Now −0.05%.
2. **BRK-B used a 2011 filing.** Multi-class cover-page share counts are dimensional and missing from companyfacts,
   so the newest *complete* EDGAR filing was from 2011. `STALE_FILING` caught it. Fixes: the freshest source wins
   (EDGAR on ties), and a `share_conversion` column (1,500 B per A) is added to the universe.
3. **Yahoo throttling.** yfinance silently returned empty profiles under HTTP 401/429, and ingest reported
   "0 errors". Fixes: validation plus retry with backoff, empty responses are never cached, and failures are
   reported per ticker.

## 4. Open: TD, BNS, MFC (hypothesis: Yahoo definition, not our error)
- BNS: Yahoo's 73.15 = (total equity − NCI) / shares = (90,622 − 1,471) / 1,218.8M. Yahoo **includes** ~$9.9B of
  preferred shares and other equity instruments (LRCNs/AT1). We exclude them, as banks do when reporting common BVPS.
- TD: Yahoo's 75.62 sits between common-only (69.66) and including preferreds (77.52), so the exact definition is unclear.
- RY: Yahoo excludes preferreds and matches us. So Yahoo is **inconsistent across banks**, and it cannot be the
  final reference for financials.
- **Action (P2-6, human):** compare our BVPS against the "book value per common share" line in the
  Q3 2026 supplementary financial packs for TD, BNS and RY, and the Q2 2026 pack for MFC. Tolerance ±3%.

## 5. Known limitations
- TSX `filed_date` is estimated (period end + 45 days), so look-ahead protection for TSX names is approximate.
- Yahoo sector/industry is not GICS. With only 14 tickers, most peer groups fall below 5. FEW_PEERS and
  INDUSTRY_FALLBACK dominate, and the peer fair value **is not meaningful until the full universe is loaded (P1-2)**.
  Per-company BVPS and P/B are validated independently of this.
- Yahoo's sector is sometimes missing (RY.TO came back without it on some calls). The ticker is still valued
  but gets no peer group.
