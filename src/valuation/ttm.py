"""Point-in-time trailing-twelve-month flows and DPS. Spec: docs/models/ttm.md (v0.1.0)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

QUARTER_DAYS = (80, 100)
STALE_DAYS = 456  # ~15 months, as book_value's STALE_FILING
NO_TTM = "NO_TTM"
STALE_TTM = "STALE_TTM"


@dataclass(frozen=True)
class TTMResult:
    value: float | None
    period_end: date | None = None
    filed_date: date | None = None
    quarter_ends: tuple[date, ...] = ()
    flags: frozenset[str] = field(default_factory=frozenset)
    quarter_values: tuple[float, ...] = ()  # the 4 summed values, same order as quarter_ends


def is_quarter_gap(days: int) -> bool:
    return QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def latest_known(quarters: Iterable[Any], as_of: date) -> dict[date, Any]:
    """Per period_end, the row with the latest filed_date <= as_of."""
    latest: dict[date, Any] = {}
    for q in quarters:
        if q.filed_date > as_of:
            continue
        cur = latest.get(q.period_end)
        if cur is None or q.filed_date > cur.filed_date:
            latest[q.period_end] = q
    return latest


def ttm(quarters: Iterable[Any], as_of: date) -> TTMResult:
    latest = latest_known(quarters, as_of)
    ends = sorted(latest, reverse=True)[:4]
    if len(ends) < 4 or not all(is_quarter_gap((ends[i] - ends[i + 1]).days) for i in range(3)):
        return TTMResult(None, flags=frozenset({NO_TTM}))
    rows = [latest[e] for e in ends]
    flags = {STALE_TTM} if (as_of - ends[0]).days > STALE_DAYS else set()
    return TTMResult(
        value=sum(r.value for r in rows), period_end=ends[0],
        filed_date=max(r.filed_date for r in rows), quarter_ends=tuple(ends),
        flags=frozenset(flags), quarter_values=tuple(r.value for r in rows),
    )


def dps_ttm(dividends: Iterable[Any], as_of: date) -> float:
    start = as_of - timedelta(days=365)
    return float(sum(d.amount for d in dividends if start < d.ex_date <= as_of))
