"""Shared provider types and the raw-response cache.

Every provider is split into *fetch* (network, cached to data/raw) and *parse*
(pure functions over the raw JSON) so parsers can be tested offline on fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# Canonical fundamental items used by the models.
TOTAL_EQUITY = "total_equity"  # incl. non-controlling interest
NCI = "noncontrolling_interest"
PREFERRED = "preferred_equity"
GOODWILL = "goodwill"
INTANGIBLES = "intangibles"
SHARES = "shares_outstanding"
# Data batch 2 balance-sheet items (spec docs/models/ttm.md §5.2); absent, never 0, when untagged.
TOTAL_ASSETS = "total_assets"
TOTAL_DEBT = "total_debt"
CASH = "cash"
# Data batch 3 balance-sheet items (spec docs/models/ttm.md §6, Model G); absent, never 0.
CURRENT_ASSETS = "current_assets"
CURRENT_LIABILITIES = "current_liabilities"
RETAINED_EARNINGS = "retained_earnings"  # negative for an accumulated deficit
ITEMS = (TOTAL_EQUITY, NCI, PREFERRED, GOODWILL, INTANGIBLES, SHARES, TOTAL_ASSETS, TOTAL_DEBT,
         CASH, CURRENT_ASSETS, CURRENT_LIABILITIES, RETAINED_EARNINGS)
# Flow items: one discrete fiscal quarter each (spec docs/models/ttm.md §1, §5.1).
NI_COMMON_Q = "ni_common_q"
CFO_Q = "cfo_q"
CAPEX_Q = "capex_q"  # positive outflow
REVENUE_Q = "revenue_q"
GROSS_PROFIT_Q = "gross_profit_q"
EBIT_Q = "ebit_q"
FLOW_ITEMS = (NI_COMMON_Q, CFO_Q, CAPEX_Q, REVENUE_Q, GROSS_PROFIT_Q, EBIT_Q)


@dataclass(frozen=True)
class Fact:
    ticker: str
    period_end: date
    filed_date: date
    item: str
    value: float
    unit: str  # currency code, or "shares"
    source: str
    filed_estimated: bool = False


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    date: date
    close: float
    adj_close: float | None
    volume: float | None
    source: str


@dataclass(frozen=True)
class Dividend:
    ticker: str
    ex_date: date
    amount: float  # per share, split-adjusted, trading currency
    currency: str | None
    source: str


class RawCache:
    """Immutable-by-day cache of raw provider responses under data/raw/<source>/<day>/."""

    def __init__(self, root: str | Path = "data/raw", day: date | None = None):
        self.root = Path(root)
        self.day = (day or date.today()).isoformat()

    def path(self, source: str, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.root / source / self.day / f"{safe}.json"

    def get(self, source: str, key: str) -> Any | None:
        p = self.path(source, key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, source: str, key: str, payload: Any) -> None:
        p = self.path(source, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, default=str))
