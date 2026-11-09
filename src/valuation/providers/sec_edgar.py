"""SEC EDGAR XBRL companyfacts: point-in-time US fundamentals with real filing dates.

SEC asks automated clients to identify themselves; set VALUATION_SEC_USER_AGENT
to "<app name> <contact email>" for production use.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import date

import requests

from .base import GOODWILL, INTANGIBLES, NCI, PREFERRED, SHARES, TOTAL_EQUITY, Fact, RawCache

SOURCE = "sec_edgar"
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"}
URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

EQUITY_TOTAL = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
EQUITY_PARENT = "StockholdersEquity"
CONCEPTS = {
    EQUITY_TOTAL: "USD",
    EQUITY_PARENT: "USD",
    "MinorityInterest": "USD",
    "PreferredStockValue": "USD",
    "PreferredStockValueOutstanding": "USD",
    "PreferredStockLiquidationPreferenceValue": "USD",
    "Goodwill": "USD",
    "IntangibleAssetsNetExcludingGoodwill": "USD",
    "CommonStockSharesOutstanding": "shares",
}


# Issuers tag preferred equity differently (JPM: liquidation preference only).
PREFERRED_CHAIN = (
    "PreferredStockValue",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
)


def _first(at, concepts):
    for c in concepts:
        v = at(c)
        if v is not None:
            return v
    return None


def parse_companyfacts(ticker: str, payload: dict) -> list[Fact]:
    """One filing per accession number, at that filing's primary balance-sheet date."""
    gaap = payload.get("facts", {}).get("us-gaap", {})
    dei = payload.get("facts", {}).get("dei", {})

    # accn -> concept -> end -> value ; accn -> filed
    by_accn: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    filed: dict[str, str] = {}
    for concept, unit in CONCEPTS.items():
        for f in gaap.get(concept, {}).get("units", {}).get(unit, []):
            if f.get("form") not in FORMS:
                continue
            by_accn[f["accn"]][concept][f["end"]] = float(f["val"])
            filed[f["accn"]] = f["filed"]
    cover_shares: dict[str, float] = {}
    for f in dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []):
        if f.get("form") in FORMS:
            cover_shares[f["accn"]] = float(f["val"])

    facts: list[Fact] = []
    for accn, concepts in by_accn.items():
        eq_ends = concepts.get(EQUITY_TOTAL) or concepts.get(EQUITY_PARENT)
        if not eq_ends:
            continue
        end = max(eq_ends)

        def at(c: str, end: str = end, concepts=concepts) -> float | None:
            return concepts.get(c, {}).get(end)

        mi = at("MinorityInterest")
        total = at(EQUITY_TOTAL)
        if total is None:
            parent = at(EQUITY_PARENT)
            total = None if parent is None else parent + (mi or 0.0)
        shares = cover_shares.get(accn) or at("CommonStockSharesOutstanding")
        items = {
            TOTAL_EQUITY: total,
            NCI: mi,
            PREFERRED: _first(at, PREFERRED_CHAIN),
            GOODWILL: at("Goodwill"),
            INTANGIBLES: at("IntangibleAssetsNetExcludingGoodwill"),
            SHARES: shares,
        }
        pe, fd = date.fromisoformat(end), date.fromisoformat(filed[accn])
        for item, v in items.items():
            if v is not None:
                facts.append(Fact(ticker, pe, fd, item, v,
                                  "shares" if item == SHARES else "USD", SOURCE, False))
    return facts


class EdgarProvider:
    def __init__(self, cache: RawCache, pause: float = 0.2):
        self.cache = cache
        self.pause = pause
        self.ua = os.environ.get("VALUATION_SEC_USER_AGENT", "trade2-valuation-screener research")

    def companyfacts(self, cik: str) -> dict:
        cik = str(cik).zfill(10)
        raw = self.cache.get(SOURCE, f"CIK{cik}")
        if raw is None:
            time.sleep(self.pause)
            r = requests.get(URL.format(cik=cik), headers={"User-Agent": self.ua}, timeout=30)
            r.raise_for_status()
            raw = r.json()
            self.cache.put(SOURCE, f"CIK{cik}", raw)
        return raw
