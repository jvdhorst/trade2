"""SEC EDGAR XBRL companyfacts: point-in-time US fundamentals with real filing dates.

SEC asks automated clients to identify themselves; set VALUATION_SEC_USER_AGENT
to "<app name> <contact email>" for production use.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date

import requests

from .base import GOODWILL, INTANGIBLES, NCI, PREFERRED, SHARES, TOTAL_EQUITY, Fact, RawCache

SOURCE = "sec_edgar"
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"}
URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{frame}.json"


def recent_frames(as_of: date, n: int = 2) -> list[str]:
    """Instant frames for the last n completed calendar quarters, newest first ('CY2026Q3I')."""
    y, q = as_of.year, (as_of.month - 1) // 3  # q = last completed quarter (0 -> previous year Q4)
    out = []
    for _ in range(n):
        if q == 0:
            y, q = y - 1, 4
        out.append(f"CY{y}Q{q}I")
        q -= 1
    return out


def parse_frame_accessions(payload: dict) -> dict[str, str]:
    """{10-digit CIK: accession} for every filer in a frames response."""
    return {str(d["cik"]).zfill(10): d["accn"] for d in payload.get("data", [])}

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


def trim_companyfacts(payload: dict) -> dict:
    """Keep only the concepts parse_companyfacts reads (raw values, unchanged)."""
    facts = payload.get("facts", {})
    gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})
    return {
        "cik": payload.get("cik"), "entityName": payload.get("entityName"),
        "facts": {
            "us-gaap": {c: gaap[c] for c in CONCEPTS if c in gaap},
            "dei": {c: dei[c] for c in ("EntityCommonStockSharesOutstanding",) if c in dei},
        },
    }


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
    def __init__(self, cache: RawCache, pause: float = 0.15):
        from .yahoo import RateLimiter

        self.cache = cache
        self.pause = pause
        self.limiter = RateLimiter(1 / pause if pause else 0)
        self.ua = os.environ.get("VALUATION_SEC_USER_AGENT", "trade2-valuation-screener research")

    def _get(self, url: str) -> dict:
        self.limiter.wait()
        r = requests.get(url, headers={"User-Agent": self.ua}, timeout=60)
        r.raise_for_status()
        return r.json()

    def latest_accessions(self, as_of: date) -> dict[str, str]:
        """Latest accession per CIK, from the stockholders'-equity frames of recent quarters.

        One call per quarter covers every US filer, so it's cheap to detect who filed something new.
        """
        latest: dict[str, str] = {}
        for frame in reversed(recent_frames(as_of)):  # oldest first; newer overwrite
            key = f"frame_{EQUITY_PARENT}_{frame}"
            raw = self.cache.get(SOURCE, key)
            if raw is None:
                try:
                    raw = self._get(FRAMES_URL.format(concept=EQUITY_PARENT, frame=frame))
                except requests.HTTPError:  # frame not published yet
                    continue
                self.cache.put(SOURCE, key, raw)
            latest.update(parse_frame_accessions(raw))
        return latest

    def companyfacts(self, cik: str) -> dict:
        cik = str(cik).zfill(10)
        raw = self.cache.get(SOURCE, f"CIK{cik}")
        if raw is None:
            self.limiter.wait()
            r = requests.get(URL.format(cik=cik), headers={"User-Agent": self.ua}, timeout=60)
            r.raise_for_status()
            raw = trim_companyfacts(r.json())  # 3-9 MB -> only the concepts we parse
            self.cache.put(SOURCE, f"CIK{cik}", raw)
        return raw
