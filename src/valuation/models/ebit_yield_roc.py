"""Model J: EBIT yield + return on capital (Greenblatt-style). Spec: docs/models/ebit_yield_roc.md
(v0.1.0). Parameters are starting values from the proposal, never tuned on the holdout. Not part of
FROZEN_V2. New and unvalidated: not backtested.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import rankdata

MODEL_VERSION = "ebit_yield_roc/0.1.0"
BUY_PCT = 0.20
SELL_PCT = 0.80
BUY_SPREAD = 0.03
SELL_SPREAD = 0.0
MIN_UNIVERSE = 12
EXCLUDED_SECTORS = frozenset({"Financials", "Utilities", "Real Estate"})
CYCLICAL_SECTORS = frozenset({"Energy", "Materials"})

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
NEG_IC = "NEG_IC"
CYCLICAL_EBIT = "CYCLICAL_EBIT"
TOP_PCT_LOW_YIELD = "TOP_PCT_LOW_YIELD"
MARKETS = ("TSX", "US")


@dataclass(frozen=True)
class JInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    market_cap: float | None = None
    fx_rate: float = 1.0
    ebit_ttm: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    total_equity: float | None = None
    preferred: float = 0.0
    nci: float = 0.0
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class JMetrics:
    ev: float | None
    ey: float | None
    ic: float | None
    roc: float | None


@dataclass(frozen=True)
class JResult:
    ticker: str
    market: str
    signal: str
    no_signal_reason: str | None
    ev: float | None
    ey: float | None
    ic: float | None
    roc: float | None
    rank_ey: int | None
    rank_roc: int | None
    combined: int | None
    position: int | None
    n: int | None
    pct: float | None
    flags: frozenset[str]


def _validate(inp: JInputs) -> None:
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    if inp.fx_rate is None or not inp.fx_rate > 0:
        raise ValueError(f"{inp.ticker}: fx_rate must be > 0")


def compute_metrics(inp: JInputs) -> JMetrics:
    """EV, EY, IC, ROC (reporting ccy); None where an input is missing or a guard fails."""
    _validate(inp)
    pref, nci = inp.preferred or 0.0, inp.nci or 0.0
    ev = ic = ey = roc = None
    if None not in (inp.market_cap, inp.total_debt, inp.cash):
        ev = inp.market_cap / inp.fx_rate + inp.total_debt + pref + nci - inp.cash
    if None not in (inp.total_equity, inp.total_debt, inp.cash):
        ic = inp.total_equity + inp.total_debt - inp.cash
    if inp.ebit_ttm is not None:
        if ev is not None and ev > 0:
            ey = inp.ebit_ttm / ev
        if ic is not None and ic > 0:
            roc = inp.ebit_ttm / ic
    return JMetrics(ev, ey, ic, roc)


def map_signal(pct: float, ey: float, rf: float) -> str:
    if pct <= BUY_PCT and ey >= rf + BUY_SPREAD:
        return BUY
    if pct > SELL_PCT and ey <= rf + SELL_SPREAD:
        return SELL
    return HOLD


def _gate(inp: JInputs, m: JMetrics) -> tuple[str | None, str | None]:
    """(signal, reason) for precedence steps 1-6; (None, None) when the name enters R_m."""
    if inp.eligible is not True:
        return NOT_ELIGIBLE, None
    if not inp.sector or inp.sector in EXCLUDED_SECTORS:
        return NO_SIGNAL, "EXCLUDED_SECTOR"
    if None in (inp.market_cap, inp.ebit_ttm, inp.total_debt, inp.cash, inp.total_equity):
        return NO_SIGNAL, "NO_DATA"
    if not inp.ebit_ttm > 0:
        return NO_SIGNAL, "NON_POSITIVE_EBIT"
    if not m.ev > 0:
        return NO_SIGNAL, "NON_POSITIVE_EV"
    if not m.ic > 0:
        return NO_SIGNAL, "NEG_IC"
    return None, None


def run_ebit_yield_roc(inputs: Sequence[JInputs], rf: Mapping[str, float | None],
                       min_universe: int = MIN_UNIVERSE) -> list[JResult]:
    """One JResult per input, same order. Ranks, n and pct per market (spec: Ranking set)."""
    metrics = [compute_metrics(i) for i in inputs]
    gates = [_gate(i, m) for i, m in zip(inputs, metrics, strict=True)]
    ranked: dict[int, tuple[int, int, int, int, int, float]] = {}
    for mkt in MARKETS:
        idx = [k for k, i in enumerate(inputs) if i.market == mkt and gates[k][0] is None]
        if not idx:
            continue
        r_ey = rankdata(-np.array([metrics[k].ey for k in idx]), method="min").astype(int)
        r_roc = rankdata(-np.array([metrics[k].roc for k in idx]), method="min").astype(int)
        order = sorted(range(len(idx)), key=lambda j: (r_ey[j] + r_roc[j], r_ey[j],
                                                       inputs[idx[j]].ticker))
        n = len(idx)
        for pos, j in enumerate(order, start=1):
            ranked[idx[j]] = (int(r_ey[j]), int(r_roc[j]), int(r_ey[j] + r_roc[j]), pos, n,
                              pos / n)

    out = []
    for k, (inp, m) in enumerate(zip(inputs, metrics, strict=True)):
        flags = set(inp.flags)
        if m.ic is not None and m.ic <= 0:
            flags.add(NEG_IC)
        if inp.sector in CYCLICAL_SECTORS:
            flags.add(CYCLICAL_EBIT)
        signal, reason = gates[k]
        rank_ey = rank_roc = combined = position = n = None
        pct = None
        if k in ranked:
            rank_ey, rank_roc, combined, position, n, pct = ranked[k]
            rf_m = rf.get(inp.market)
            if n < min_universe:
                signal, reason = NO_SIGNAL, "SMALL_UNIVERSE"
            elif rf_m is None:
                signal, reason = NO_SIGNAL, "NO_RF"
            else:
                signal = map_signal(pct, m.ey, rf_m)
                if pct <= BUY_PCT and signal != BUY:
                    flags.add(TOP_PCT_LOW_YIELD)
        out.append(JResult(inp.ticker, inp.market, signal, reason, m.ev, m.ey, m.ic, m.roc,
                           rank_ey, rank_roc, combined, position, n, pct, frozenset(flags)))
    return out
