"""Model G: Altman Z'' distress score / veto. Spec: docs/models/altman_z.md (v0.1.0). Parameters are
fixed starting values from the proposal (Altman's published coefficients and zones), never tuned.
Not part of FROZEN_V2. New and unvalidated: not backtested. There is no BUY and no vote; the
distress_veto column is reported for a future vote only.

Screening signal only, not investment advice. Labels: HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

MODEL_VERSION = "altman_z/0.1.0"
COEF_X1 = 6.56
COEF_X2 = 3.26
COEF_X3 = 6.72
COEF_X4 = 1.05
DISTRESS_Z = 1.10
SAFE_Z = 2.60
OVERRIDE_CFO_DEBT = 0.25
EXCLUDED_SECTORS = frozenset({"Financials", "Utilities", "Real Estate"})

HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
DISTRESS, GREY, SAFE = "distress", "grey", "safe"
NEG_EQUITY = "NEG_EQUITY"
CASH_RICH_OVERRIDE = "CASH_RICH_OVERRIDE"
NO_OVERRIDE_DATA = "NO_OVERRIDE_DATA"
MARKETS = ("TSX", "US")


@dataclass(frozen=True)
class GInputs:
    ticker: str
    market: str
    sector: str | None
    eligible: bool | None
    total_assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    retained_earnings: float | None = None
    ebit_ttm: float | None = None
    total_equity: float | None = None
    cfo_ttm: float | None = None
    total_debt: float | None = None
    flags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GMetrics:
    x1: float | None
    x2: float | None
    x3: float | None
    x4: float | None
    z: float | None
    total_liabilities: float | None
    cfo_to_debt: float | None


@dataclass(frozen=True)
class GResult:
    ticker: str
    market: str
    signal: str
    no_signal_reason: str | None
    x1: float | None
    x2: float | None
    x3: float | None
    x4: float | None
    z: float | None
    zone: str | None
    total_liabilities: float | None
    cfo_to_debt: float | None
    distress_veto: bool | None
    flags: frozenset[str]


def compute_metrics(inp: GInputs) -> GMetrics:
    """X1-X4, Z'', TL, CFO/debt; None where an input is missing or a guard fails (spec Formulas)."""
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")
    ta, eq = inp.total_assets, inp.total_equity
    tl = ta - eq if ta is not None and eq is not None else None
    x1 = x2 = x3 = x4 = z = None
    if ta is not None and ta > 0:
        if inp.current_assets is not None and inp.current_liabilities is not None:
            x1 = (inp.current_assets - inp.current_liabilities) / ta
        if inp.retained_earnings is not None:
            x2 = inp.retained_earnings / ta
        if inp.ebit_ttm is not None:
            x3 = inp.ebit_ttm / ta
        if tl is not None and tl > 0:
            x4 = eq / tl
        if None not in (x1, x2, x3, x4):
            z = COEF_X1 * x1 + COEF_X2 * x2 + COEF_X3 * x3 + COEF_X4 * x4
    cfo_to_debt = None
    if inp.cfo_ttm is not None and inp.total_debt is not None and inp.total_debt > 0:
        cfo_to_debt = inp.cfo_ttm / inp.total_debt
    return GMetrics(x1, x2, x3, x4, z, tl, cfo_to_debt)


def zone(z: float | None) -> str | None:
    if z is None:
        return None
    if z < DISTRESS_Z:
        return DISTRESS
    return SAFE if z > SAFE_Z else GREY


def map_signal(z: float) -> str:
    return SELL if z < DISTRESS_Z else HOLD


def _gate(inp: GInputs, m: GMetrics) -> tuple[str | None, str | None]:
    """(signal, reason) for precedence rows 1-5; (None, None) when the name reaches row 6."""
    if inp.eligible is not True:
        return NOT_ELIGIBLE, None
    if not inp.sector or inp.sector in EXCLUDED_SECTORS:
        return NO_SIGNAL, "EXCLUDED_SECTOR"
    if None in (inp.total_assets, inp.current_assets, inp.current_liabilities,
                inp.retained_earnings, inp.ebit_ttm, inp.total_equity):
        return NO_SIGNAL, "NO_DATA"
    if not inp.total_assets > 0:
        return NO_SIGNAL, "NON_POSITIVE_ASSETS"
    if not m.total_liabilities > 0:
        return NO_SIGNAL, "NON_POSITIVE_LIABILITIES"
    return None, None


def evaluate(inp: GInputs) -> GResult:
    m = compute_metrics(inp)
    flags = set(inp.flags)
    if inp.total_equity is not None and inp.total_equity < 0:
        flags.add(NEG_EQUITY)
    z_zone = zone(m.z)
    signal, reason = _gate(inp, m)
    veto = None
    if signal is None:
        signal = map_signal(m.z)
        if z_zone == DISTRESS:
            if m.cfo_to_debt is None:
                flags.add(NO_OVERRIDE_DATA)
            elif m.cfo_to_debt > OVERRIDE_CFO_DEBT:
                signal = HOLD  # zone, z and X1-X4 stay as computed: the override is visible
                flags.add(CASH_RICH_OVERRIDE)
        veto = signal == SELL
    return GResult(inp.ticker, inp.market, signal, reason, m.x1, m.x2, m.x3, m.x4, m.z, z_zone,
                   m.total_liabilities, m.cfo_to_debt, veto, frozenset(flags))


def run_altman_z(inputs: Sequence[GInputs]) -> list[GResult]:
    """One GResult per input, same order. Per name: no cross-section."""
    return [evaluate(i) for i in inputs]
