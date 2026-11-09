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

## 4. Issuer cross-check: resolved (P2-6)
`valuation crosscheck --issuer` reads "book value per (common) share" straight from each issuer's supplementary
pack (sources: `universe/issuer_sources.csv`). Tolerance ±3%.

| Ticker | Period | Issuer BVPS | Ours | diff | Yahoo `bookValue` |
|---|---|---|---|---|---|
| RY.TO | 2026-07-31 | 96.73 | 96.70 | −0.03% | 96.73 |
| TD.TO | 2026-07-31 | 69.69 | 69.66 | −0.04% | 75.62 (wrong) |
| BNS.TO | 2026-07-31 | 64.99 | 64.99 | 0.00% | 73.15 (wrong: includes preferreds/LRCNs) |
| MFC.TO | 2026-06-30 | 27.48 | 28.04 | +2.04% | 31.24 (wrong) |

**Conclusion:** our common-equity definition matches what issuers report. Yahoo's `bookValue` is **not a reliable
reference for financials** (it is inconsistent across banks). It stays as a secondary check, and issuer filings are the
primary reference.

**MFC +2.0%, explained:** Yahoo's total equity includes **participating policyholders' equity** (C$937M at Q2), and
Yahoo's "Common Stock Equity" also includes it. Removing it gives (54,927 − 1,666 − 6,660 − 937) / 1,661.9M =
27.48, an exact match. Yahoo's "Other Equity Interest" field holds it for MFC, but that field means different things
for other issuers (for RY it is LRCNs already counted in preferreds), so it can't simply be subtracted everywhere.
Follow-up for AN: add an input for non-common equity such as participating policyholders' equity, which needs a
source that tags it.

The MFC pack was uploaded by the user because www.manulife.com blocks automated fetches. It lives in
`data/issuer/` (not committed). The hand-read value is recorded in `issuer_sources.csv`, so the check still runs
without the file.

**Analyst note (insurers):** under IFRS 17, Manulife also reports an *adjusted* BVPS (41.78 at Q3) that adds the
post-tax contractual service margin (future profit already locked in). Plain P/B therefore overstates how expensive
insurers look. Address this in Phase 3 (an insurer-specific multiple).

## 5. Full TSX Composite run (2026-11-09, book_value 0.2.0)
`valuation ingest/value --universe universe/tsx_composite.csv`

- **Coverage:** 215/215 valued (100%). 204 have a peer fair value: 95 with industry peers, 109 using the sector
  fallback. 9 have FEW_PEERS and 2 have NEG_BOOK (e.g. BBD-B). Per ticker: `reports/2026-11-09/coverage_book_value.csv`.
- **Issuer check:** 4/4 still pass.
- **Yahoo agreement (±5%):** 172/215 (80%). Full table: `reports/2026-11-09/crosscheck_yahoo.csv`.
- **Bug found and fixed:** Yahoo reports "Stockholders Equity" = 0 for partnerships (BIP-UN, BEP-UN), so the
  derived preferred equity went negative and inflated BVPS 2.3×. Preferred is now total − NCI − common, and
  negatives are rejected. Results for all previously correct names are unchanged.

### Outliers for triage (not auto-fixed)
| Ticker | Ours vs Yahoo | Likely cause | Next step |
|---|---|---|---|
| BIP-UN, BEP-UN | −17%, −20% | Brookfield partnerships: several unit classes (LP units, exchangeable shares, redeemable partnership units held as NCI) | Issuer check against "equity per unit"; may need a unit-class conversion |
| POW | −38% | Holdco: Yahoo's "Preferred Stock Equity" (C$10.5B) looks too large for Power Corp's own preferreds | Issuer check (Power Corp supplementary) |
| CLS | −56% | Ours = US$2.48B equity / 115M shares, which looks right. Yahoo's BV of 69.2 is inconsistent | Spot-check the 10-Q |
| AAUC, MDA, GFL, DML, BAM | −20 to −34% | Unknown. Possibly NCI or preferred definitions, or a newer quarter at Yahoo | QA triage |

The rest of the misses (34 names) are 5–20%. Most are FX-converted USD reporters or financials, where Yahoo's
definition is known to differ.

### Analyst observations on the screen
- **Book value only works for some sectors.** The top raw upsides come from asset-light companies valued against
  high-P/B peer medians: LSPD +928% (software median 9.9×), LNR, MG and CTC-A against a 7-name Consumer
  Discretionary median of 7.0×. These are model artifacts, not opportunities. Proposal (AN): rank only where P/B
  is a meaningful anchor (Financials, Real Estate, Energy, Utilities, Materials), and show the other sectors
  unranked until Phase 3 multiples.
- **Peer groups mix taxonomies.** The sector is GICS but the industry is Yahoo's, so LSPD is a GICS "Financials"
  name (payments) whose industry peers are software companies. A consistent sub-industry source for TSX is needed.
- **No profitability adjustment yet.** Low P/B can be justified by low ROE. The ROE-justified P/B (Phase 3) is the fix.

## 6. Known limitations
- TSX `filed_date` is estimated (period end + 45 days), so look-ahead protection for TSX names is approximate.
- From v0.2.0 the peer sector is GICS (from the universe files). Industry is still Yahoo's. With only 14 tickers,
  most groups fall below 5, so the peer fair value **is not meaningful until a full-universe ingest (P1-8)**.
  Per-company BVPS and P/B are validated independently of this.
- Yahoo's sector is sometimes missing (RY.TO). This no longer matters for peers now that GICS comes from the
  universe file.
