"""Model E: Piotroski F-score. Spec: docs/models/piotroski.md (v0.1.0). Parameters are starting
values from the proposal, never tuned on the holdout. Not part of FROZEN_V2. New and unvalidated:
not backtested. No vote.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

MODEL_VERSION = "piotroski/0.1.0"
N_SIGNALS = 9
MIN_SCORED = 8
BUY_F_9 = 8
BUY_F_8 = 7
SELL_F = 2
SHARE_TOL_NUM = 201
SHARE_TOL_DEN = 200
PRIOR_WINDOW_DAYS = (350, 380)
PRIOR2_WINDOW_DAYS = (715, 745)
EXCLUDED_SECTORS = frozenset({"Financials"})

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
PRIOR_TA_FALLBACK = "PRIOR_TA_FALLBACK"
ANNUAL_PRIOR = "ANNUAL_PRIOR"  # reserved, never set in 0.1.0
E_PERIOD_MISMATCH = "E_PERIOD_MISMATCH"
PRIOR_CCY_MISMATCH = "PRIOR_CCY_MISMATCH"
MARKETS = ("TSX", "US")


@dataclass(frozen=True)
class EInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    ni_ttm: float | None = None
    ni_ttm_prior: float | None = None
    cfo_ttm: float | None = None
    gp_ttm: float | None = None
    gp_ttm_prior: float | None = None
    rev_ttm: float | None = None
    rev_ttm_prior: float | None = None
    ta_t: float | None = None
    ta_4q: float | None = None
    ta_8q: float | None = None
    debt_t: float | None = None
    debt_4q: float | None = None
    ca_t: float | None = None
    cl_t: float | None = None
    ca_4q: float | None = None
    cl_4q: float | None = None
    shares_t: float | None = None
    shares_4q: float | None = None
    split_factor: float = 1.0
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class EMetrics:
    signals: tuple[int | None, ...]
    f_score: int | None
    n_scored: int
    roa: float | None
    roa_prior: float | None
    lev_t: float | None
    lev_prior: float | None
    cr_t: float | None
    cr_prior: float | None
    shares_prev_adj: float | None
    gm_t: float | None
    gm_prior: float | None
    ato_t: float | None
    ato_prior: float | None
    prior_ta_fallback: bool


@dataclass(frozen=True)
class EResult:
    ticker: str
    market: str
    signal: str
    no_signal_reason: str | None
    f_score: int | None
    n_scored: int
    signals: tuple[int | None, ...]
    roa: float | None
    roa_prior: float | None
    lev_t: float | None
    lev_prior: float | None
    cr_t: float | None
    cr_prior: float | None
    shares_prev_adj: float | None
    gm_t: float | None
    gm_prior: float | None
    ato_t: float | None
    ato_prior: float | None
    flags: frozenset[str]


def _q(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b is not None and b > 0 else None


def _cmp(a, b, op) -> int | None:
    if a is None or b is None:
        return None
    return int(op(a, b))


def compute_metrics(inp: EInputs) -> EMetrics:
    """Ratios and the nine 0/1/None signals (spec Formulas)."""
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    if inp.split_factor is None or not inp.split_factor > 0:
        raise ValueError(f"{inp.ticker}: split_factor must be > 0")
    ta_8q_eff = inp.ta_8q if inp.ta_8q is not None else inp.ta_4q
    roa, roa_prior = _q(inp.ni_ttm, inp.ta_4q), _q(inp.ni_ttm_prior, ta_8q_eff)
    lev_t, lev_prior = _q(inp.debt_t, inp.ta_t), _q(inp.debt_4q, inp.ta_4q)
    cr_t, cr_prior = _q(inp.ca_t, inp.cl_t), _q(inp.ca_4q, inp.cl_4q)
    gm_t, gm_prior = _q(inp.gp_ttm, inp.rev_ttm), _q(inp.gp_ttm_prior, inp.rev_ttm_prior)
    ato_t, ato_prior = _q(inp.rev_ttm, inp.ta_4q), _q(inp.rev_ttm_prior, ta_8q_eff)
    sp = (inp.shares_4q * inp.split_factor
          if inp.shares_4q is not None and inp.shares_4q > 0 else None)

    s1 = None if roa is None else int(roa > 0)
    s2 = None if inp.cfo_ttm is None else int(inp.cfo_ttm > 0)
    s3 = _cmp(roa, roa_prior, lambda a, b: a > b)
    s4 = _cmp(inp.cfo_ttm, inp.ni_ttm, lambda a, b: a > b)
    s5 = _cmp(lev_t, lev_prior, lambda a, b: a < b)
    s6 = _cmp(cr_t, cr_prior, lambda a, b: a > b)
    s7 = None
    if inp.shares_t is not None and inp.shares_t > 0 and sp is not None:
        s7 = int(inp.shares_t * SHARE_TOL_DEN <= sp * SHARE_TOL_NUM)
    s8 = _cmp(gm_t, gm_prior, lambda a, b: a > b)
    s9 = _cmp(ato_t, ato_prior, lambda a, b: a > b)
    signals = (s1, s2, s3, s4, s5, s6, s7, s8, s9)
    scored = [s for s in signals if s is not None]
    n = len(scored)
    f = sum(scored) if n else None
    fallback = inp.ta_8q is None and (s3 is not None or s9 is not None)
    return EMetrics(signals, f, n, roa, roa_prior, lev_t, lev_prior, cr_t, cr_prior, sp,
                    gm_t, gm_prior, ato_t, ato_prior, fallback)


def map_signal(f_score: int, n_scored: int) -> str:
    if n_scored not in (8, 9):
        raise ValueError(f"n_scored must be 8 or 9, got {n_scored!r}")
    if f_score >= (BUY_F_9 if n_scored == 9 else BUY_F_8):
        return BUY
    return SELL if f_score <= SELL_F else HOLD


def evaluate(inp: EInputs) -> EResult:
    m = compute_metrics(inp)
    flags = set(inp.flags)
    if m.prior_ta_fallback:
        flags.add(PRIOR_TA_FALLBACK)
    reason = None
    if inp.eligible is not True:
        signal = NOT_ELIGIBLE
    elif not inp.sector or inp.sector in EXCLUDED_SECTORS:
        signal, reason = NO_SIGNAL, "EXCLUDED_SECTOR"
    elif m.n_scored < MIN_SCORED:
        signal, reason = NO_SIGNAL, "F_INCOMPLETE"
    else:
        signal = map_signal(m.f_score, m.n_scored)
    return EResult(inp.ticker, inp.market, signal, reason, m.f_score, m.n_scored, m.signals,
                   m.roa, m.roa_prior, m.lev_t, m.lev_prior, m.cr_t, m.cr_prior,
                   m.shares_prev_adj, m.gm_t, m.gm_prior, m.ato_t, m.ato_prior,
                   frozenset(flags))


def run_piotroski(inputs: Sequence[EInputs]) -> list[EResult]:
    """One EResult per input, same order. Per name: no cross-section."""
    return [evaluate(i) for i in inputs]


def signals_str(signals: tuple[int | None, ...]) -> str:
    """Report form: '1', '0', '-' (unscored) per signal, in order 1..9."""
    return "".join("-" if s is None else str(s) for s in signals)
