"""SEC EDGAR XBRL companyfacts: point-in-time US fundamentals with real filing dates.

SEC asks automated clients to identify themselves; set VALUATION_SEC_USER_AGENT
to "<app name> <contact email>" for production use.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, timedelta

import requests

from .base import (
    CAPEX_Q,
    CASH,
    CFO_Q,
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    EBIT_Q,
    GOODWILL,
    GROSS_PROFIT_Q,
    INTANGIBLES,
    NCI,
    NI_COMMON_Q,
    PREFERRED,
    RETAINED_EARNINGS,
    REVENUE_Q,
    SHARES,
    TOTAL_ASSETS,
    TOTAL_DEBT,
    TOTAL_EQUITY,
    Fact,
    RawCache,
)

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
# Data batch 2 balance-sheet concepts (spec docs/models/ttm.md §5.2).
ASSETS = "Assets"
CASH_CHAIN = ("CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash")
ST_DEBT_CHAIN = ("ShortTermBorrowings", "CommercialPaper")
DEBT_CONCEPTS = ("DebtLongtermAndShorttermCombinedAmount", "LongTermDebtNoncurrent", "DebtCurrent",
                 "LongTermDebt", "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligations",
                 "LongTermDebtAndCapitalLeaseObligationsCurrent", *ST_DEBT_CHAIN)
# Data batch 3 (ttm.md §6): single tags, no chain, absent when untagged.
BATCH3_CONCEPTS = {CURRENT_ASSETS: "AssetsCurrent", CURRENT_LIABILITIES: "LiabilitiesCurrent",
                   RETAINED_EARNINGS: "RetainedEarningsAccumulatedDeficit"}
CONCEPTS.update({c: "USD" for c in (ASSETS, *CASH_CHAIN, *DEBT_CONCEPTS,
                                    *BATCH3_CONCEPTS.values())})


def total_debt(at) -> float | None:
    """Rules D1-D5 of ttm.md §5.2 on one filing's instant values (`at(concept)`)."""
    st = _first(at, ST_DEBT_CHAIN) or 0.0
    if (v := at("DebtLongtermAndShorttermCombinedAmount")) is not None:  # D1
        return v
    ltn = at("LongTermDebtNoncurrent")
    if ltn is not None and (dc := at("DebtCurrent")) is not None:  # D2
        return ltn + dc
    if (v := at("LongTermDebt")) is not None:  # D3
        return v + st
    if ltn is not None:  # D4
        return ltn + (at("LongTermDebtCurrent") or 0.0) + st
    if (v := at("LongTermDebtAndCapitalLeaseObligations")) is not None:  # D5
        return v + (at("LongTermDebtAndCapitalLeaseObligationsCurrent") or 0.0) + st
    return None


# Issuers tag preferred equity differently (JPM: liquidation preference only).
PREFERRED_CHAIN = (
    "PreferredStockValue",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
)


# Income-statement concepts for quarterly NI to common (spec docs/models/ttm.md).
NI_AVAILABLE = "NetIncomeLossAvailableToCommonStockholdersBasic"
NI_TOTAL = "NetIncomeLoss"
PREF_DIV_CHAIN = (
    "PreferredStockDividendsAndOtherAdjustments",
    "PreferredStockDividendsIncomeStatementImpact",
    "DividendsPreferredStock",
)
NI_CONCEPTS = (NI_AVAILABLE, NI_TOTAL, *PREF_DIV_CHAIN)
# Data batch 2 flow chains (spec docs/models/ttm.md §5.1), priority order.
REVENUE_CHAIN = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                 "RevenuesNetOfInterestExpense")
COST_CHAIN = ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")
FLOW_CHAINS: dict[str, tuple[str, ...]] = {
    CFO_Q: ("NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    CAPEX_Q: ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    REVENUE_Q: REVENUE_CHAIN,
    GROSS_PROFIT_Q: ("GrossProfit",),  # + derived REV - COST (chain 2)
    EBIT_Q: ("OperatingIncomeLoss",),
}
FLOW_CONCEPTS = tuple(dict.fromkeys(
    [c for cs in FLOW_CHAINS.values() for c in cs] + list(COST_CHAIN)))
TRIM_CONCEPTS = tuple(dict.fromkeys((*CONCEPTS, *NI_CONCEPTS, *FLOW_CONCEPTS)))


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
            "us-gaap": {c: gaap[c] for c in TRIM_CONCEPTS if c in gaap},
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
            TOTAL_ASSETS: at(ASSETS),
            CASH: _first(at, CASH_CHAIN),
            TOTAL_DEBT: total_debt(at),
            TOTAL_EQUITY: total,
            NCI: mi,
            PREFERRED: _first(at, PREFERRED_CHAIN),
            GOODWILL: at("Goodwill"),
            INTANGIBLES: at("IntangibleAssetsNetExcludingGoodwill"),
            SHARES: shares,
            **{item: at(c) for item, c in BATCH3_CONCEPTS.items()},
        }
        pe, fd = date.fromisoformat(end), date.fromisoformat(filed[accn])
        for item, v in items.items():
            if v is not None:
                facts.append(Fact(ticker, pe, fd, item, v,
                                  "shares" if item == SHARES else "USD", SOURCE, False))
    return facts


def _quarters_from_durations(vals: dict[tuple[date, date], float]) -> dict[date, float]:
    """Discrete quarters {end: value} from one chain's {(start, end): value} (spec steps 1-3)."""
    def is_q(days: int) -> bool:
        return 80 <= days <= 100

    q: dict[date, tuple[date, float]] = {}  # end -> (start, value)
    for (s, e), v in vals.items():  # 1. direct
        if is_q((e - s).days):
            q[e] = (s, v)
    by_start: dict[date, list[date]] = defaultdict(list)
    for s, e in vals:
        by_start[s].append(e)
    derived: dict[date, tuple[date, date, float]] = {}  # 2. difference of cumulatives
    for s, ends in by_start.items():
        for e2 in ends:
            if e2 in q:
                continue
            for e1 in ends:
                if e1 < e2 and (e1 - s).days >= 80 and is_q((e2 - e1).days):
                    cur = derived.get(e2)
                    if cur is None or e1 > cur[0] or (e1 == cur[0] and s > cur[1]):
                        derived[e2] = (e1, s, vals[(s, e2)] - vals[(s, e1)])
    for e2, (e1, _s, v) in derived.items():
        q[e2] = (e1 + timedelta(days=1), v)
    for (s, e), v in vals.items():  # 3. FY minus three quarters
        if e in q or not 340 <= (e - s).days <= 380:
            continue
        chain: list[date] = []
        # q1 starts at s; each next quarter starts 1-4 days after the previous one ends.
        for _ in range(3):
            cands = [qe for qe, (qs, _v) in q.items()
                     if (qs == s if not chain else 1 <= (qs - chain[-1]).days <= 4)]
            if not cands:
                break
            chain.append(min(cands))
        if len(chain) == 3 and is_q((e - chain[-1]).days):
            q[e] = (chain[-1] + timedelta(days=1), v - sum(q[c][1] for c in chain))
    return {e: v for e, (_s, v) in q.items()}


def _durations(gaap: dict, concepts) -> tuple[dict, list[date]]:
    """concept -> list of (filed, accn, (start, end), val) sorted by (filed, accn), and the sorted
    distinct filed dates. Only duration facts of the accepted forms (spec ttm.md §1)."""
    out: dict[str, list] = {}
    filed: set[date] = set()
    for concept in concepts:
        rows = []
        for f in gaap.get(concept, {}).get("units", {}).get("USD", []):
            if f.get("form") not in FORMS or not f.get("start"):
                continue
            fd = date.fromisoformat(f["filed"])
            rows.append((fd, f.get("accn", ""),
                         (date.fromisoformat(f["start"]), date.fromisoformat(f["end"])),
                         float(f["val"])))
            filed.add(fd)
        rows.sort(key=lambda r: (r[0], r[1]))
        out[concept] = rows
    return out, sorted(filed)


def _emit_quarters(ticker: str, item: str, gaap: dict, concepts, chains) -> list[Fact]:
    """Snapshots in increasing filed date (latest filed <= f per (concept, period), ties -> larger
    accn); each chain -> discrete quarters on its own; per quarter end the first chain that has it.
    A fact is emitted when a quarter first appears and whenever its value changes."""
    rows, filed_dates = _durations(gaap, concepts)
    snap: dict[str, dict[tuple[date, date], float]] = {c: {} for c in concepts}
    pos = dict.fromkeys(concepts, 0)
    emitted: dict[date, float] = {}
    out: list[Fact] = []
    for f in filed_dates:
        for c in concepts:  # advance every concept's snapshot to filed date f
            rs, i = rows[c], pos[c]
            while i < len(rs) and rs[i][0] <= f:
                snap[c][rs[i][2]] = rs[i][3]
                i += 1
            pos[c] = i
        quarters = [_quarters_from_durations(chain(snap)) for chain in chains]
        for end in sorted(set().union(*quarters)):
            v = next(q[end] for q in quarters if end in q)
            if end not in emitted or emitted[end] != v:
                emitted[end] = v
                out.append(Fact(ticker, end, f, item, v, "USD", SOURCE, False))
    return out


def _ni_chain2(snap) -> dict[tuple[date, date], float]:
    out = {}
    for period, v in snap[NI_TOTAL].items():
        pref = next((snap[c][period] for c in PREF_DIV_CHAIN if period in snap[c]), 0.0)
        out[period] = v - pref
    return out


def _gp_chain2(snap) -> dict[tuple[date, date], float]:
    """REV - COST for periods where both exist (no default for either)."""
    out = {}
    periods = set().union(*(snap[c] for c in REVENUE_CHAIN))
    for period in periods:
        rev = next((snap[c][period] for c in REVENUE_CHAIN if period in snap[c]), None)
        cost = next((snap[c][period] for c in COST_CHAIN if period in snap[c]), None)
        if rev is not None and cost is not None:
            out[period] = rev - cost
    return out


def parse_quarterly_ni(ticker: str, payload: dict) -> list[Fact]:
    """Point-in-time discrete-quarter NI to common (ni_common_q), spec docs/models/ttm.md §1."""
    gaap = payload.get("facts", {}).get("us-gaap", {})
    return _emit_quarters(ticker, NI_COMMON_Q, gaap, NI_CONCEPTS,
                          [lambda s: s[NI_AVAILABLE], _ni_chain2])


def parse_quarterly_flows(ticker: str, payload: dict) -> list[Fact]:
    """Data batch 2 flow items (cfo_q, capex_q, revenue_q, gross_profit_q, ebit_q), ttm.md §5.1.
    Capex is stored as reported (EDGAR PaymentsTo... = positive outflow)."""
    gaap = payload.get("facts", {}).get("us-gaap", {})
    out: list[Fact] = []
    for item, chain in FLOW_CHAINS.items():
        chains = [lambda s, c=c: s[c] for c in chain]
        concepts = chain
        if item == GROSS_PROFIT_Q:
            chains.append(_gp_chain2)
            concepts = (*chain, *REVENUE_CHAIN, *COST_CHAIN)
        out += _emit_quarters(ticker, item, gaap, concepts, chains)
    return out


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
        key = f"CIK{cik}_v4"  # v4: trimmed set includes data batch 3 (ttm.md §6)
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            self.limiter.wait()
            r = requests.get(URL.format(cik=cik), headers={"User-Agent": self.ua}, timeout=60)
            r.raise_for_status()
            raw = trim_companyfacts(r.json())  # 3-9 MB -> only the concepts we parse
            self.cache.put(SOURCE, key, raw)
        return raw
