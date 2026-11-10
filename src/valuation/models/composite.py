"""Model C: value-quality-momentum-risk composite. Spec: docs/models/composite.md (v0.1.0).

Screening signal only (BUY / HOLD / SELL sorts the queue for human review); not investment advice.
Pure functions: the engine builds point-in-time `CompositeInputs` (facts filed <= as_of, prices
<= as_of); nothing here sees a date except the period-end equality checks.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.stats import rankdata

MODEL_VERSION = "composite/0.1.0"
WINSOR = (0.05, 0.95)
MIN_SECTOR_NAMES = 5
MIN_FACTORS = 3
BUY_PCT = 0.80
SELL_PCT = 0.20
MOM_VETO_PCT = 0.10
MOM_SKIP = 21
MOM_LOOKBACK = 273
VOL_WINDOW = 252
ANNUALIZE = 252
STD_EPS = 1e-12
FINANCIALS = "Financials"
FACTORS = {"value": ("ep", "bp", "fcfp"),
           "quality": ("roe", "gpa", "neg_accruals", "neg_leverage"),
           "momentum": ("mom",),
           "risk": ("neg_vol",)}
METRICS = tuple(m for ms in FACTORS.values() for m in ms)
NON_FIN_METRICS = ("fcfp", "gpa", "neg_accruals", "neg_leverage")


@dataclass(frozen=True)
class CompositeInputs:
    ticker: str
    sector: str | None
    eligible: bool | None
    history_ok: bool | None
    price: float | None = None
    market_cap: float | None = None
    fx_rate: float = 1.0
    bvps: float | None = None
    ni_ttm: float | None = None
    ni_period_end: date | None = None
    roe: float | None = None
    cfo_ttm: float | None = None
    cfo_period_end: date | None = None
    capex_ttm: float | None = None
    capex_period_end: date | None = None
    gross_profit_ttm: float | None = None
    total_assets: float | None = None
    total_debt: float | None = None
    mom: float | None = None
    vol: float | None = None
    flags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        if self.fx_rate is None or not self.fx_rate > 0:
            raise ValueError(f"{self.ticker}: fx_rate must be > 0")


@dataclass(frozen=True)
class SectorStat:
    n: int
    mean: float
    std: float
    used: bool


@dataclass(frozen=True)
class CompositeStats:
    bounds: dict[str, tuple[float, float]]
    sectors: dict[tuple[str, str], SectorStat]


@dataclass(frozen=True)
class CompositeResult:
    ticker: str
    sector: str | None
    signal: str
    no_signal_reason: str | None
    score: float | None
    pct: float | None
    n_factors: int
    value: float | None
    quality: float | None
    momentum: float | None
    risk: float | None
    mom_pct: float | None
    metrics: dict[str, float | None]
    winsorized: dict[str, float]
    z: dict[str, float]
    factor_pct: dict[str, float | None]
    factor_labels: dict[str, str | None]
    flags: frozenset[str]


# ---------- price metrics ----------

def _check(c: Sequence[float]) -> list[float]:
    c = [float(x) for x in c]
    if any(not x > 0 for x in c):
        raise ValueError("adj_close values must be > 0")
    return c


def momentum(adj_closes: Sequence[float]) -> float | None:
    """adj_close(t-21) / adj_close(t-273) - 1 in the ticker's own sessions; needs 274 bars."""
    c = _check(adj_closes)
    if len(c) < MOM_LOOKBACK + 1:
        return None
    return c[-1 - MOM_SKIP] / c[-1 - MOM_LOOKBACK] - 1


def volatility(adj_closes: Sequence[float]) -> float | None:
    """Sample stdev of the last 252 daily log returns x sqrt(252); needs 253 bars."""
    c = _check(adj_closes)
    if len(c) < VOL_WINDOW + 1:
        return None
    r = np.diff(np.log(np.asarray(c[-(VOL_WINDOW + 1):])))
    return float(np.std(r, ddof=1) * math.sqrt(ANNUALIZE))


# ---------- raw metrics ----------

def raw_metrics(inp: CompositeInputs) -> tuple[dict[str, float | None], frozenset[str]]:
    flags: set[str] = set()
    cap = inp.market_cap / inp.fx_rate if inp.market_cap is not None and inp.market_cap > 0 \
        else None
    ta = inp.total_assets if inp.total_assets is not None and inp.total_assets > 0 else None
    out: dict[str, float | None] = dict.fromkeys(METRICS)
    if inp.ni_ttm is not None and cap is not None:
        out["ep"] = inp.ni_ttm / cap
    if inp.bvps is not None and inp.price is not None and inp.price > 0:
        out["bp"] = inp.bvps / inp.price
    out["roe"] = inp.roe
    out["mom"] = inp.mom
    out["neg_vol"] = None if inp.vol is None else -inp.vol
    if inp.sector != FINANCIALS:
        if inp.cfo_ttm is not None and inp.capex_ttm is not None:
            if inp.cfo_period_end == inp.capex_period_end:
                if cap is not None:
                    out["fcfp"] = (inp.cfo_ttm - inp.capex_ttm) / cap
            else:
                flags.add("FCF_PERIOD_MISMATCH")
        if ta is not None and inp.gross_profit_ttm is not None:
            out["gpa"] = inp.gross_profit_ttm / ta
        if inp.ni_ttm is not None and inp.cfo_ttm is not None:
            if inp.ni_period_end == inp.cfo_period_end:
                if ta is not None:
                    out["neg_accruals"] = -(inp.ni_ttm - inp.cfo_ttm) / ta
            else:
                flags.add("ACCRUALS_PERIOD_MISMATCH")
        if ta is not None and inp.total_debt is not None:
            out["neg_leverage"] = -inp.total_debt / ta
    return out, frozenset(flags)


# ---------- cross-section helpers ----------

def winsor_bounds(values: Sequence[float]) -> tuple[float, float]:
    if len(values) == 0:
        raise ValueError("no values")
    lo, hi = np.percentile(np.asarray(values, dtype=float), [100 * WINSOR[0], 100 * WINSOR[1]])
    return float(lo), float(hi)


def winsorize(values: Sequence[float], lo: float, hi: float) -> list[float]:
    return [float(min(max(v, lo), hi)) for v in values]


def sector_zscores(values: Sequence[float], min_names: int = MIN_SECTOR_NAMES
                   ) -> tuple[float, float, list[float] | None]:
    if len(values) == 0:
        raise ValueError("no values")
    a = np.asarray(values, dtype=float)
    mean, std = float(a.mean()), float(a.std(ddof=0))
    if len(a) < min_names or std <= STD_EPS:
        return mean, std, None
    return mean, std, [float(x) for x in (a - mean) / std]


def pct_rank(values: Mapping[str, float]) -> dict[str, float | None]:
    keys = list(values)
    n = len(keys)
    if n < 2:
        return dict.fromkeys(keys)
    ranks = rankdata([values[k] for k in keys], method="average")
    return {k: float((r - 1) / (n - 1)) for k, r in zip(keys, ranks, strict=True)}


def map_signal(pct: float) -> str:
    if pct >= BUY_PCT:
        return "BUY"
    if pct <= SELL_PCT:
        return "SELL"
    return "HOLD"


def _mean(xs: list[float]) -> float | None:
    return float(sum(xs) / len(xs)) if xs else None


# ---------- the run ----------

def run_composite(inputs: Sequence[CompositeInputs], min_sector_names: int = MIN_SECTOR_NAMES
                  ) -> tuple[list[CompositeResult], CompositeStats]:
    tickers = [i.ticker for i in inputs]
    if len(set(tickers)) != len(tickers):
        raise ValueError("duplicate tickers")
    raw = {i.ticker: raw_metrics(i) for i in inputs}
    in_u = {i.ticker: (i.eligible is True and i.history_ok is True and bool(i.sector))
            for i in inputs}
    U = [i for i in inputs if in_u[i.ticker]]

    # 1. pooled winsorizing per metric
    bounds: dict[str, tuple[float, float]] = {}
    wins: dict[str, dict[str, float]] = {i.ticker: {} for i in U}
    for m in METRICS:
        vals = [(i.ticker, raw[i.ticker][0][m]) for i in U if raw[i.ticker][0][m] is not None]
        if not vals:
            continue
        lo, hi = winsor_bounds([v for _, v in vals])
        bounds[m] = (lo, hi)
        for t, v in vals:
            wins[t][m] = min(max(v, lo), hi)

    # 2. sector z-scores
    sectors: dict[tuple[str, str], SectorStat] = {}
    zs: dict[str, dict[str, float]] = {i.ticker: {} for i in U}
    for s in sorted({i.sector for i in U}):
        names = [i.ticker for i in U if i.sector == s]
        for m in METRICS:
            g = [t for t in names if m in wins[t]]
            if not g:
                continue
            mean, std, z = sector_zscores([wins[t][m] for t in g], min_sector_names)
            sectors[(s, m)] = SectorStat(len(g), mean, std, z is not None)
            if z is not None:
                for t, v in zip(g, z, strict=True):
                    zs[t][m] = v

    # 3-4. factors and S
    fac: dict[str, dict[str, float | None]] = {}
    score: dict[str, float | None] = {}
    for i in U:
        t = i.ticker
        fac[t] = {k: _mean([zs[t][m] for m in ms if m in zs[t]]) for k, ms in FACTORS.items()}
        avail = [v for v in fac[t].values() if v is not None]
        score[t] = _mean(avail) if len(avail) >= MIN_FACTORS else None

    # 5. percentiles
    pct = pct_rank({t: s for t, s in score.items() if s is not None})
    mom_pct = pct_rank({i.ticker: i.mom for i in U if i.mom is not None})
    fpct = {k: pct_rank({t: f[k] for t, f in fac.items() if f[k] is not None}) for k in FACTORS}

    results = []
    for i in inputs:
        t = i.ticker
        metrics, mflags = raw[t]
        flags = set(i.flags) | set(mflags)
        none4 = dict.fromkeys(FACTORS)
        if not in_u[t]:
            if i.eligible is not True:
                sig, reason = "NOT_ELIGIBLE", None
            elif i.history_ok is not True:
                sig, reason = "NO_SIGNAL", "SHORT_HISTORY"
            else:
                sig, reason = "NO_SIGNAL", "NO_SECTOR"
            results.append(CompositeResult(t, i.sector, sig, reason, None, None, 0, None, None,
                                           None, None, None, metrics, {}, {}, dict(none4),
                                           dict(none4), frozenset(flags)))
            continue
        f = fac[t]
        n_f = sum(v is not None for v in f.values())
        p = pct.get(t)
        if n_f < MIN_FACTORS:
            sig, reason, p = "NO_SIGNAL", "FEW_FACTORS", None
        elif p is None:
            sig, reason = "NO_SIGNAL", "FEW_NAMES"
        else:
            sig, reason = map_signal(p), None
            if sig == "BUY":
                mp = mom_pct.get(t)
                distress = (i.bvps is not None and i.bvps <= 0 and i.ni_ttm is not None
                            and i.ni_ttm < 0)
                mom_veto = mp is not None and mp <= MOM_VETO_PCT
                if distress:
                    flags.add("VETO_DISTRESS")
                if mom_veto:
                    flags.add("VETO_MOMENTUM")
                if distress or mom_veto:
                    sig = "HOLD"
        fp = {k: fpct[k].get(t) for k in FACTORS}
        results.append(CompositeResult(
            t, i.sector, sig, reason, score[t], p, n_f, f["value"], f["quality"], f["momentum"],
            f["risk"], mom_pct.get(t), metrics, dict(wins[t]), dict(zs[t]), fp,
            {k: None if v is None else map_signal(v) for k, v in fp.items()}, frozenset(flags)))
    return results, CompositeStats(bounds, sectors)
