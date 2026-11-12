"""Model D: dividend sustainability and implied return. Spec: docs/models/dividend_sustainability.md
(v0.1.0). Parameters are starting values from the proposal, never tuned on the holdout. Not part of
FROZEN_V2. New and unvalidated: not backtested. V_DDM / MoS are informational only. No vote.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

MODEL_VERSION = "dividend_sustainability/0.1.0"
ERP = 0.05
G_FLOOR = 0.0
G_CAP = 0.06
DIV_CUT_G = -0.02
BUY_SPREAD = 0.02
SELL_SPREAD = -0.02
MAX_PAYOUT_BUY = 0.75
MAX_PAYOUT = 1.0
MIN_EX_DATES = 2
EXCLUDED_SECTORS = frozenset({"Real Estate"})
PREV_DAYS = 365  # engine only
COVERAGE_GRACE_DAYS = 7  # engine only

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
G_CAPPED, G_FLOORED = "G_CAPPED", "G_FLOORED"
DIV_CUT, NEW_PAYER, NO_DIV_HISTORY = "DIV_CUT", "NEW_PAYER", "NO_DIV_HISTORY"
NEG_EARNINGS, IRREGULAR_DIV = "NEG_EARNINGS", "IRREGULAR_DIV"
BUY_BLOCKED_PAYOUT, BUY_BLOCKED_DIV_CUT = "BUY_BLOCKED_PAYOUT", "BUY_BLOCKED_DIV_CUT"
MARKETS = ("TSX", "US")


@dataclass(frozen=True)
class DInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    price: float
    dps_ttm: float | None = None
    dps_ttm_prev: float | None = None
    n_ex_dates: int = 0
    ni_common_ttm: float | None = None
    shares_outstanding: float | None = None
    fx_rate: float = 1.0
    roe_raw: float | None = None
    rf: float | None = None
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DMetrics:
    eps: float | None
    payout: float | None
    roe0: float | None
    g_raw: float | None
    g_sust: float | None
    g_hist: float | None
    dy: float | None
    k: float | None
    r: float | None
    spread: float | None
    v_ddm: float | None
    mos: float | None
    div_cut: bool


@dataclass(frozen=True)
class DResult:
    ticker: str
    market: str
    signal: str
    no_signal_reason: str | None
    eps: float | None
    payout: float | None
    roe0: float | None
    g_raw: float | None
    g_sust: float | None
    g_hist: float | None
    dy: float | None
    k: float | None
    r: float | None
    spread: float | None
    v_ddm: float | None
    mos: float | None
    flags: frozenset[str]


def _validate(inp: DInputs) -> None:
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    if inp.price is None or not inp.price > 0:
        raise ValueError(f"{inp.ticker}: price must be > 0")
    if inp.fx_rate is None or not inp.fx_rate > 0:
        raise ValueError(f"{inp.ticker}: fx_rate must be > 0")
    if inp.n_ex_dates is None or inp.n_ex_dates < 0:
        raise ValueError(f"{inp.ticker}: n_ex_dates must be >= 0")


def compute_metrics(inp: DInputs) -> DMetrics:
    """Spec Formulas, in the stated evaluation order; None where an input is missing."""
    _validate(inp)
    dps = inp.dps_ttm
    eps = None
    if (inp.ni_common_ttm is not None and inp.shares_outstanding is not None
            and inp.shares_outstanding > 0):
        eps = inp.ni_common_ttm / inp.shares_outstanding * inp.fx_rate
    payout = dps / eps if dps is not None and eps is not None and eps > 0 else None
    roe0 = inp.roe_raw
    g_raw = roe0 * (1.0 - payout) if roe0 is not None and payout is not None else None
    g_sust = min(max(g_raw, G_FLOOR), G_CAP) if g_raw is not None else None
    g_hist = None
    if dps is not None and inp.dps_ttm_prev is not None and inp.dps_ttm_prev > 0:
        g_hist = dps / inp.dps_ttm_prev - 1.0
    div_cut = g_hist is not None and g_hist < DIV_CUT_G
    dy = dps / inp.price if dps is not None else None
    k = dy * (1.0 + g_sust) + g_sust if dy is not None and g_sust is not None else None
    r = inp.rf + ERP if inp.rf is not None else None
    spread = k - r if k is not None and r is not None else None
    v_ddm = mos = None
    if dps is not None and g_sust is not None and r is not None and r - g_sust > 0:
        v_ddm = dps * (1.0 + g_sust) / (r - g_sust)
        mos = v_ddm / inp.price - 1.0
    return DMetrics(eps, payout, roe0, g_raw, g_sust, g_hist, dy, k, r, spread, v_ddm, mos,
                    div_cut)


def map_signal(payout: float, spread: float, div_cut: bool) -> str:
    if payout > MAX_PAYOUT or spread <= SELL_SPREAD:
        return SELL
    if spread >= BUY_SPREAD and payout <= MAX_PAYOUT_BUY and not div_cut:
        return BUY
    return HOLD


def _gate(inp: DInputs, m: DMetrics) -> tuple[str | None, str | None]:
    """(signal, reason) for precedence rows 1-9; (None, None) when the name reaches row 10."""
    if inp.eligible is not True:
        return NOT_ELIGIBLE, None
    if not inp.sector or inp.sector in EXCLUDED_SECTORS:
        return NO_SIGNAL, "EXCLUDED_SECTOR"
    if inp.dps_ttm is None:
        return NO_SIGNAL, "NO_DATA"
    if not inp.dps_ttm > 0:
        return NO_SIGNAL, "NO_DIVIDEND"
    if inp.n_ex_dates < MIN_EX_DATES:
        return NO_SIGNAL, "IRREGULAR_DIV"
    if (inp.ni_common_ttm is None or inp.shares_outstanding is None
            or not inp.shares_outstanding > 0):
        return NO_SIGNAL, "NO_DATA"
    if not m.eps > 0:
        return NO_SIGNAL, "NEG_EARNINGS"
    if inp.roe_raw is None:
        return NO_SIGNAL, "NO_ROE"
    if inp.rf is None:
        return NO_SIGNAL, "NO_RF"
    return None, None


def evaluate(inp: DInputs) -> DResult:
    m = compute_metrics(inp)
    flags = set(inp.flags)
    if m.g_raw is not None:
        if m.g_raw > G_CAP:
            flags.add(G_CAPPED)
        elif m.g_raw < G_FLOOR:
            flags.add(G_FLOORED)
    if m.div_cut:
        flags.add(DIV_CUT)
    if inp.dps_ttm_prev is None:
        flags.add(NO_DIV_HISTORY)
    elif inp.dps_ttm_prev <= 0 and inp.dps_ttm is not None and inp.dps_ttm > 0:
        flags.add(NEW_PAYER)
    if m.eps is not None and m.eps <= 0:
        flags.add(NEG_EARNINGS)
    if inp.dps_ttm is not None and inp.dps_ttm > 0 and inp.n_ex_dates < MIN_EX_DATES:
        flags.add(IRREGULAR_DIV)
    signal, reason = _gate(inp, m)
    if signal is None:
        signal = map_signal(m.payout, m.spread, m.div_cut)
        if signal == HOLD and m.spread >= BUY_SPREAD:
            if m.payout > MAX_PAYOUT_BUY:
                flags.add(BUY_BLOCKED_PAYOUT)
            if m.div_cut:
                flags.add(BUY_BLOCKED_DIV_CUT)
    return DResult(inp.ticker, inp.market, signal, reason, m.eps, m.payout, m.roe0, m.g_raw,
                   m.g_sust, m.g_hist, m.dy, m.k, m.r, m.spread, m.v_ddm, m.mos,
                   frozenset(flags))


def run_dividend_sustainability(inputs: Sequence[DInputs]) -> list[DResult]:
    """One DResult per input, same order. Per name: no cross-section."""
    return [evaluate(i) for i in inputs]
