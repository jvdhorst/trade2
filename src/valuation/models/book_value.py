"""Book value vs share price model. Spec: docs/models/book_value.md (v0.1.0)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

MODEL_VERSION = "book_value/0.1.0"
STALE_DAYS = 456  # ~15 months
DEFAULT_MIN_PEERS = 5

NEG_BOOK = "NEG_BOOK"
STALE_FILING = "STALE_FILING"
FX_CONVERTED = "FX_CONVERTED"
MULTI_CLASS = "MULTI_CLASS"
INDUSTRY_FALLBACK = "INDUSTRY_FALLBACK"
FEW_PEERS = "FEW_PEERS"


@dataclass(frozen=True)
class Filing:
    period_end: date
    filed_date: date
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BookValueInputs:
    ticker: str
    as_of: date
    price: float
    trading_currency: str
    reporting_currency: str
    total_equity: float
    shares_outstanding: float
    period_end: date
    filed_date: date
    noncontrolling_interest: float = 0.0
    preferred_equity: float = 0.0
    goodwill: float = 0.0
    intangibles: float = 0.0
    fx_rate: float = 1.0
    share_classes: int = 1
    sector: str | None = None
    industry: str | None = None


@dataclass(frozen=True)
class BookValueResult:
    ticker: str
    as_of: date
    price: float
    currency: str
    common_equity: float
    bvps: float
    tbvps: float
    pb: float | None
    flags: frozenset[str]
    sector: str | None = None
    industry: str | None = None


@dataclass(frozen=True)
class PeerValuation:
    ticker: str
    result: BookValueResult
    peer_level: str | None
    n_peers: int
    peer_median_pb: float | None
    fair_value: float | None
    upside: float | None
    pb_percentile: float | None
    flags: frozenset[str]


def select_latest_filing(filings: list[Filing], as_of: date) -> Filing | None:
    """Latest period_end among filings public on/before as_of; ties -> latest filed_date."""
    eligible = [f for f in filings if f.filed_date <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.period_end, f.filed_date))


def compute_book_value(inp: BookValueInputs) -> BookValueResult:
    if inp.shares_outstanding <= 0:
        raise ValueError(f"{inp.ticker}: shares_outstanding must be > 0")
    if inp.price <= 0:
        raise ValueError(f"{inp.ticker}: price must be > 0")
    if inp.filed_date > inp.as_of:
        raise ValueError(f"{inp.ticker}: filing {inp.filed_date} is after as_of {inp.as_of}")

    common_equity = inp.total_equity - inp.noncontrolling_interest - inp.preferred_equity
    bvps = common_equity / inp.shares_outstanding * inp.fx_rate
    tangible = common_equity - inp.goodwill - inp.intangibles
    tbvps = tangible / inp.shares_outstanding * inp.fx_rate

    flags: set[str] = set()
    if bvps <= 0:
        flags.add(NEG_BOOK)
    pb = inp.price / bvps if bvps > 0 else None
    if (inp.as_of - inp.period_end).days > STALE_DAYS:
        flags.add(STALE_FILING)
    if inp.reporting_currency != inp.trading_currency:
        flags.add(FX_CONVERTED)
    if inp.share_classes > 1:
        flags.add(MULTI_CLASS)

    return BookValueResult(
        ticker=inp.ticker,
        as_of=inp.as_of,
        price=inp.price,
        currency=inp.trading_currency,
        common_equity=common_equity,
        bvps=bvps,
        tbvps=tbvps,
        pb=pb,
        flags=frozenset(flags),
        sector=inp.sector,
        industry=inp.industry,
    )


def _empty(r: BookValueResult, extra: set[str]) -> PeerValuation:
    return PeerValuation(r.ticker, r, None, 0, None, None, None, None, r.flags | extra)


def peer_relative(
    results: list[BookValueResult], min_peers: int = DEFAULT_MIN_PEERS
) -> list[PeerValuation]:
    valid = [r for r in results if r.pb is not None]
    out: list[PeerValuation] = []
    for r in results:
        if r.pb is None:
            out.append(_empty(r, set()))
            continue
        others = [p for p in valid if p.ticker != r.ticker]
        ind = [p for p in others if r.industry and p.industry == r.industry]
        sec = [p for p in others if r.sector and p.sector == r.sector]
        extra: set[str] = set()
        if len(ind) >= min_peers:
            peers, level = ind, "industry"
        elif len(sec) >= min_peers:
            peers, level = sec, "sector"
            extra.add(INDUSTRY_FALLBACK)
        else:
            out.append(_empty(r, {FEW_PEERS}))
            continue
        pbs = [p.pb for p in peers]
        med = statistics.median(pbs)
        fv = r.bvps * med
        out.append(
            PeerValuation(
                ticker=r.ticker,
                result=r,
                peer_level=level,
                n_peers=len(peers),
                peer_median_pb=med,
                fair_value=fv,
                upside=fv / r.price - 1,
                pb_percentile=sum(1 for x in pbs if x < r.pb) / len(pbs),
                flags=r.flags | extra,
            )
        )
    return out


def rank(valuations: list[PeerValuation]) -> list[PeerValuation]:
    ranked = sorted(
        (v for v in valuations if v.upside is not None), key=lambda v: (-v.upside, v.ticker)
    )
    rest = sorted((v for v in valuations if v.upside is None), key=lambda v: v.ticker)
    return ranked + rest
