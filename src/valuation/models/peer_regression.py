"""Model B (book branch): per-sector Theil-Sen ln(P/B) ~ ROE. Spec: docs/models/peer_regression.md.

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

MODEL_VERSION = "peer_regression/0.2.0"
STATUS = "diagnostic"  # v0.2.0: shown for context, never counted toward the Phase 10 gate

MIN_FIT = 12
MIN_R2 = 0.20
WINSOR = (5, 95)
BUY_PCT = 0.20
SELL_PCT = 0.80
BUY_UPSIDE = 0.15
SELL_UPSIDE = -0.15
ZERO_SS = 1e-12  # sum of squares treated as zero (spec: ZERO_VARIANCE)
BOOK_SECTORS = frozenset({"Financials", "Utilities", "Real Estate", "Energy", "Materials"})
# v0.2.0 (P7-14): fee businesses whose equity book is not an operating asset -> EV branch
EV_SUB_INDUSTRIES = frozenset({"Transaction & Payment Processing Services",
                               "Financial Exchanges & Data"})

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"
ROE_WINSORIZED = "ROE_WINSORIZED"
NEG_ROE_UNFITTED = "NEG_ROE_UNFITTED"


@dataclass(frozen=True)
class PBObs:
    ticker: str
    sector: str | None
    price: float
    bvps: float | None
    roe: float | None
    eligible: bool | None
    flags: frozenset[str] = field(default_factory=frozenset)
    needs_csm: bool = False
    sub_industry: str | None = None  # v0.2.0


def _ev(o: PBObs) -> bool:
    return o.sub_industry in EV_SUB_INDUSTRIES


@dataclass(frozen=True)
class SectorFit:
    sector: str
    n_fit: int
    n_scored: int
    slope: float | None
    intercept: float | None
    r2: float | None
    lo: float | None
    hi: float | None
    status: str


@dataclass(frozen=True)
class PBResult:
    ticker: str
    sector: str | None
    signal: str
    no_signal_reason: str | None
    pb: float | None
    roe: float | None
    roe_w: float | None
    fitted_pb: float | None
    fair_value: float | None
    residual: float | None
    resid_pct: float | None
    implied_upside: float | None
    n_fit: int | None
    r2: float | None
    flags: frozenset[str]


def theil_sen(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """Median pairwise slope (pairs with equal x skipped); intercept = median(y - slope*x)."""
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    i, j = np.triu_indices(len(xa), k=1)
    dx = xa[j] - xa[i]
    keep = dx != 0
    if not keep.any():
        raise ValueError("theil_sen needs at least one pair with distinct x")
    slope = float(np.median((ya[j] - ya[i])[keep] / dx[keep]))
    return slope, float(np.median(ya - slope * xa))


def winsor_bounds(x: Sequence[float]) -> tuple[float, float]:
    if len(x) == 0:
        raise ValueError("winsor_bounds of an empty list")
    lo, hi = np.percentile(np.asarray(x, dtype=float), list(WINSOR))
    return float(lo), float(hi)


def map_signal(resid_pct: float, upside: float) -> str:
    if resid_pct <= BUY_PCT and upside >= BUY_UPSIDE:
        return BUY
    if resid_pct >= SELL_PCT and upside <= SELL_UPSIDE:
        return SELL
    return HOLD


def _scored(o: PBObs) -> bool:
    return (o.eligible is True and not _ev(o) and not o.needs_csm
            and o.bvps is not None and o.bvps > 0
            and o.roe is not None)


def _in_fit(o: PBObs) -> bool:
    return _scored(o) and o.roe >= 0


def fit_sector(obs: Sequence[PBObs]) -> SectorFit:
    if not obs:
        raise ValueError("fit_sector needs at least one observation")
    sectors = {o.sector for o in obs}
    if len(sectors) != 1:
        raise ValueError(f"fit_sector got mixed sectors: {sorted(map(str, sectors))}")
    sector = obs[0].sector
    fit = [o for o in obs if _in_fit(o)]
    n_fit, n_scored = len(fit), sum(_scored(o) for o in obs)
    lo = hi = slope = intercept = r2 = None
    x = y = None
    if fit:
        lo, hi = winsor_bounds([o.roe for o in fit])
        x = np.clip(np.array([o.roe for o in fit]), lo, hi)
        y = np.log(np.array([o.price / o.bvps for o in fit]))
    no_x_var = x is None or len(set(x.tolist())) < 2
    ss_tot = None
    if not no_x_var:
        slope, intercept = theil_sen(x, y)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        if ss_tot > ZERO_SS:
            r2 = 1 - float(np.sum((y - (intercept + slope * x)) ** 2)) / ss_tot
    if n_fit < MIN_FIT:
        status = "FEW_NAMES"
    elif no_x_var:
        status = "NO_X_VARIATION"
    elif ss_tot is None or ss_tot <= ZERO_SS:
        status = "ZERO_VARIANCE"
    elif r2 < MIN_R2:
        status = "LOW_R2"
    else:
        status = "OK"
    return SectorFit(sector, n_fit, n_scored, slope, intercept, r2, lo, hi, status)


def run_peer_regression(obs: Sequence[PBObs]) -> tuple[list[PBResult], dict[str, SectorFit]]:
    by_sector: dict[str, list[PBObs]] = {}
    for o in obs:
        if o.price is None or o.price <= 0:
            raise ValueError(f"{o.ticker}: price must be > 0")
        if o.sector in BOOK_SECTORS and not _ev(o):
            by_sector.setdefault(o.sector, []).append(o)
    fits = {s: fit_sector(group) for s, group in by_sector.items()}

    # residuals first (needed for percentiles within the scored set)
    pre: dict[int, dict] = {}
    for k, o in enumerate(obs):
        fit = None if _ev(o) else fits.get(o.sector)
        d: dict = {"pb": o.price / o.bvps if o.bvps is not None and o.bvps > 0 else None,
                   "roe_w": None, "fitted_pb": None, "fair_value": None, "residual": None,
                   "implied_upside": None}
        if fit is not None and o.roe is not None and fit.lo is not None:
            d["roe_w"] = min(max(o.roe, fit.lo), fit.hi)
        if (fit is not None and fit.status == "OK" and d["pb"] is not None
                and d["roe_w"] is not None and not o.needs_csm):
            ln_fit = fit.intercept + fit.slope * d["roe_w"]
            d["fitted_pb"] = math.exp(ln_fit)
            d["fair_value"] = o.bvps * d["fitted_pb"]
            d["residual"] = math.log(d["pb"]) - ln_fit
            d["implied_upside"] = d["fair_value"] / o.price - 1
        pre[k] = d

    scored_res: dict[str, list[float]] = {}
    for k, o in enumerate(obs):
        if o.sector in fits and _scored(o) and pre[k]["residual"] is not None:
            scored_res.setdefault(o.sector, []).append(pre[k]["residual"])

    results = []
    for k, o in enumerate(obs):
        d, fit = pre[k], (None if _ev(o) else fits.get(o.sector))
        resid_pct = None
        if fit is not None and _scored(o) and d["residual"] is not None:
            others = scored_res[o.sector]
            if len(others) > 1:
                # count of j != i with residual_j < residual_i (i itself is never < itself)
                resid_pct = sum(r < d["residual"] for r in others) / (len(others) - 1)
        flags = set(o.flags)
        if d["roe_w"] is not None and d["roe_w"] != o.roe:
            flags.add(ROE_WINSORIZED)
        if fit is not None and _scored(o) and not _in_fit(o):
            flags.add(NEG_ROE_UNFITTED)

        reason = None
        if o.eligible is not True:
            signal = NOT_ELIGIBLE
        else:
            checks = [
                ("NO_SECTOR", not o.sector),
                ("EV_BRANCH_PENDING", o.sector not in BOOK_SECTORS or _ev(o)),
                ("NEEDS_CSM_INPUT", o.needs_csm),
                ("NEG_BOOK", o.bvps is None or o.bvps <= 0),
                ("NO_ROE", o.roe is None),
                ("NO_FIT", fit is None or fit.status != "OK"),
            ]
            reason = next((c for c, hit in checks if hit), None)
            signal = NO_SIGNAL if reason else map_signal(resid_pct, d["implied_upside"])
        results.append(PBResult(
            ticker=o.ticker, sector=o.sector, signal=signal, no_signal_reason=reason,
            pb=d["pb"], roe=o.roe, roe_w=d["roe_w"], fitted_pb=d["fitted_pb"],
            fair_value=d["fair_value"], residual=d["residual"], resid_pct=resid_pct,
            implied_upside=d["implied_upside"],
            n_fit=fit.n_fit if fit else None, r2=fit.r2 if fit else None,
            flags=frozenset(flags),
        ))
    return results, fits
