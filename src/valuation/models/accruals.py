"""Model F: accruals / earnings quality. Spec: docs/models/accruals.md (v0.1.0). Parameters are
starting values from the proposal, never tuned on the holdout. Not part of FROZEN_V2. New and
unvalidated: not backtested. No vote.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.stats import rankdata

MODEL_VERSION = "accruals/0.1.0"
BUY_PCT = 0.20
SELL_PCT = 0.80
BUY_RATIO = -0.05
SELL_RATIO = 0.05
MIN_SECTOR = 12
EXCLUDED_SECTORS = frozenset({"Financials"})
PREV_WINDOW_DAYS = (350, 380)  # engine only: TA_{t-4q} window, as Model H

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
F_PERIOD_MISMATCH = "F_PERIOD_MISMATCH"
F_NO_PRIOR_TA = "F_NO_PRIOR_TA"
F_LOSS_BUY_BLOCKED = "F_LOSS_BUY_BLOCKED"
F_PRIOR_CCY_MISMATCH = "F_PRIOR_CCY_MISMATCH"
MARKETS = ("TSX", "US")


@dataclass(frozen=True)
class FInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    ni_ttm: float | None = None
    cfo_ttm: float | None = None
    total_assets_t: float | None = None
    total_assets_prev: float | None = None
    period_end_t: date | None = None
    ni_period_end: date | None = None
    cfo_period_end: date | None = None
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class FMetrics:
    accruals: float | None
    avg_total_assets: float | None
    accrual_ratio: float | None
    cash_conv: float | None
    period_ok: bool


@dataclass(frozen=True)
class FResult:
    ticker: str
    market: str
    sector: str | None
    signal: str
    no_signal_reason: str | None
    accruals: float | None
    avg_total_assets: float | None
    accrual_ratio: float | None
    cash_conv: float | None
    acc_pct: float | None
    n: int | None
    flags: frozenset[str]


def compute_metrics(inp: FInputs) -> FMetrics:
    """Accruals, avg TA, accrual ratio, cash conversion (spec Formulas, evaluation order)."""
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    period_ok = (inp.period_end_t is not None and inp.ni_period_end == inp.period_end_t
                 and inp.cfo_period_end == inp.period_end_t)
    ni, cfo = inp.ni_ttm, inp.cfo_ttm
    accruals = ni - cfo if ni is not None and cfo is not None and period_ok else None
    avg = None
    if inp.total_assets_t is not None and inp.total_assets_prev is not None:
        avg = (inp.total_assets_t + inp.total_assets_prev) / 2
    ratio = accruals / avg if accruals is not None and avg is not None and avg > 0 else None
    cash_conv = cfo / ni if accruals is not None and ni > 0 else None
    return FMetrics(accruals, avg, ratio, cash_conv, period_ok)


def map_signal(pct: float, ratio: float, ni: float) -> str:
    if pct >= SELL_PCT and ratio >= SELL_RATIO:
        return SELL
    if pct <= BUY_PCT and ratio <= BUY_RATIO and ni > 0:
        return BUY
    return HOLD


def acc_percentiles(values: Mapping[str, float]) -> dict[str, float | None]:
    """(rank - 1) / (n - 1), average ranks ascending (0 = most negative); None for all if n < 2."""
    keys = list(values)
    n = len(keys)
    if n < 2:
        return {k: None for k in keys}
    ranks = rankdata(np.array([values[k] for k in keys], dtype=float), method="average")
    return {k: float((r - 1) / (n - 1)) for k, r in zip(keys, ranks, strict=True)}


def _gate(inp: FInputs, m: FMetrics) -> tuple[str | None, str | None]:
    """(signal, reason) for precedence rows 1-4; (None, None) when the name enters R_g."""
    if inp.eligible is not True:
        return NOT_ELIGIBLE, None
    if not inp.sector or inp.sector in EXCLUDED_SECTORS:
        return NO_SIGNAL, "EXCLUDED_SECTOR"
    if (None in (inp.ni_ttm, inp.cfo_ttm, inp.total_assets_t, inp.total_assets_prev)
            or not m.period_ok):
        return NO_SIGNAL, "NO_DATA"
    if not m.avg_total_assets > 0:
        return NO_SIGNAL, "NON_POSITIVE_ASSETS"
    return None, None


def run_accruals(inputs: Sequence[FInputs], min_sector: int = MIN_SECTOR) -> list[FResult]:
    """One FResult per input, same order. Percentiles per (market, sector) group."""
    if min_sector < 2:
        raise ValueError("min_sector must be >= 2")
    metrics = [compute_metrics(i) for i in inputs]
    gates = [_gate(i, m) for i, m in zip(inputs, metrics, strict=True)]
    groups: dict[tuple[str, str], dict[int, float]] = {}
    for k, (inp, m) in enumerate(zip(inputs, metrics, strict=True)):
        if gates[k][0] is None:
            groups.setdefault((inp.market, inp.sector), {})[k] = m.accrual_ratio
    pct: dict[int, float | None] = {}
    size: dict[int, int] = {}
    for members in groups.values():
        pct.update(acc_percentiles(members))
        size.update({k: len(members) for k in members})

    out = []
    for k, (inp, m) in enumerate(zip(inputs, metrics, strict=True)):
        flags = set(inp.flags)
        if inp.ni_ttm is not None and inp.cfo_ttm is not None and not m.period_ok:
            flags.add(F_PERIOD_MISMATCH)
        if inp.total_assets_t is not None and inp.total_assets_prev is None:
            flags.add(F_NO_PRIOR_TA)
        signal, reason = gates[k]
        p, n = pct.get(k), size.get(k)
        if k in size:
            if n < min_sector:
                signal, reason = NO_SIGNAL, "SMALL_SECTOR"
            else:
                signal = map_signal(p, m.accrual_ratio, inp.ni_ttm)
                if (p <= BUY_PCT and m.accrual_ratio <= BUY_RATIO and inp.ni_ttm <= 0):
                    flags.add(F_LOSS_BUY_BLOCKED)
        out.append(FResult(inp.ticker, inp.market, inp.sector, signal, reason, m.accruals,
                           m.avg_total_assets, m.accrual_ratio, m.cash_conv, p, n,
                           frozenset(flags)))
    return out
