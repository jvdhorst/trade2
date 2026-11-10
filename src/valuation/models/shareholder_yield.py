"""Model H: shareholder yield (dividends + net buybacks). Spec: docs/models/shareholder_yield.md
(v0.1.0); parameters frozen in docs/models/revision_v2_freeze.md.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from scipy.stats import rankdata

MODEL_VERSION = "shareholder_yield/0.1.0"
BUY_SY = 0.05
SELL_SY = -0.03
MAX_ABS_BBY = 0.15
MAX_PAYOUT = 1.0
PREV_WINDOW_DAYS = (350, 380)

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
NOT_FUNDED = "NOT_FUNDED"
SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
NO_TTM = "NO_TTM"


@dataclass(frozen=True)
class SYInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    price: float
    dps_ttm: float = 0.0
    shares_t: float | None = None
    shares_prev: float | None = None
    split_factor: float = 1.0
    ni_common_ttm: float | None = None
    fx_rate: float = 1.0
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SYResult:
    ticker: str
    signal: str
    no_signal_reason: str | None
    dy: float | None
    bby: float | None
    sy: float | None
    eps_ttm: float | None
    payout: float | None
    funded: bool
    flags: frozenset[str]


def map_signal(sy: float, funded: bool) -> str:
    if sy >= BUY_SY and funded:
        return BUY
    if sy <= SELL_SY:
        return SELL
    return HOLD


def compute_sy(inp: SYInputs) -> SYResult:
    if inp.market not in ("TSX", "US"):
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    if inp.price is None or not inp.price > 0:
        raise ValueError(f"{inp.ticker}: price must be > 0")
    if inp.split_factor is None or not inp.split_factor > 0:
        raise ValueError(f"{inp.ticker}: split_factor must be > 0")
    if inp.fx_rate is None or not inp.fx_rate > 0:
        raise ValueError(f"{inp.ticker}: fx_rate must be > 0")

    flags = set(inp.flags)
    dps = inp.dps_ttm or 0.0
    dy = dps / inp.price
    shares_ok = (inp.shares_t is not None and inp.shares_t > 0
                 and inp.shares_prev is not None and inp.shares_prev > 0)
    bby = sy = None
    if shares_ok:
        prev_adj = inp.shares_prev * inp.split_factor
        bby = (prev_adj - inp.shares_t) / prev_adj
        sy = dy + bby
    eps = payout = None
    if inp.ni_common_ttm is not None and inp.shares_t is not None and inp.shares_t > 0:
        eps = inp.ni_common_ttm / inp.shares_t * inp.fx_rate
        if eps > 0:
            payout = dps / eps
    funded = eps is not None and eps > 0 and payout is not None and payout <= MAX_PAYOUT
    if inp.split_factor != 1:
        flags.add(SPLIT_ADJUSTED)
    if inp.ni_common_ttm is None:
        flags.add(NO_TTM)

    reason = None
    if inp.eligible is not True:
        signal = NOT_ELIGIBLE
    elif not shares_ok:
        signal, reason = NO_SIGNAL, "NO_SHARES"
    elif abs(bby) > MAX_ABS_BBY:
        signal, reason = NO_SIGNAL, "CORPORATE_ACTION"
    else:
        signal = map_signal(sy, funded)
        if sy >= BUY_SY and not funded:
            flags.add(NOT_FUNDED)
    return SYResult(inp.ticker, signal, reason, dy, bby, sy, eps, payout, funded,
                    frozenset(flags))


def sy_percentiles(values: Mapping[str, float]) -> dict[str, float | None]:
    """(rank − 1)/(n − 1) with average ranks; None for every name when n < 2 (informational)."""
    keys = list(values)
    n = len(keys)
    if n < 2:
        return dict.fromkeys(keys)
    ranks = rankdata([values[k] for k in keys], method="average")
    return {k: float((r - 1) / (n - 1)) for k, r in zip(keys, ranks, strict=True)}
