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
ITEMS = (TOTAL_EQUITY, NCI, PREFERRED, GOODWILL, INTANGIBLES, SHARES)
# Flow items: one discrete fiscal quarter each (spec docs/models/ttm.md).
NI_COMMON_Q = "ni_common_q"


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
