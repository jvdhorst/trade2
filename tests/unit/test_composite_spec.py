"""Spec tests for Model C, the VQMR composite (docs/models/composite.md v0.1.0).

Written from the spec text and its golden tables only (not from the implementation).
Printed golden values have 6 decimals; tolerance 1e-6 unless the spec says otherwise.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date

import numpy as np
import pytest

from valuation.models.composite import (
    ANNUALIZE,
    BUY_PCT,
    FACTORS,
    FINANCIALS,
    METRICS,
    MIN_FACTORS,
    MIN_SECTOR_NAMES,
    MODEL_VERSION,
    MOM_LOOKBACK,
    MOM_SKIP,
    MOM_VETO_PCT,
    SELL_PCT,
    STD_EPS,
    VOL_WINDOW,
    WINSOR,
    CompositeInputs,
    CompositeResult,  # noqa: F401  (API contract; import must succeed)
    CompositeStats,  # noqa: F401
    SectorStat,  # noqa: F401
    map_signal,
    momentum,
    pct_rank,
    raw_metrics,
    run_composite,
    sector_zscores,
    volatility,
    winsor_bounds,
    winsorize,
)

TOL = 1e-6
N = None
D = date(2026, 9, 30)
D2 = date(2026, 6, 30)
IND, FIN = "Industrials", "Financials"
FACTOR_KEYS = ("value", "quality", "momentum", "risk")


def approx(x, tol=TOL):
    return pytest.approx(x, abs=tol)


# --------------------------------------------------------------------------- constants

def test_constants():
    assert MODEL_VERSION == "composite/0.1.0"
    assert tuple(WINSOR) == (0.05, 0.95)
    assert MIN_SECTOR_NAMES == 5
    assert MIN_FACTORS == 3
    assert BUY_PCT == 0.80 and SELL_PCT == 0.20 and MOM_VETO_PCT == 0.10
    assert (MOM_SKIP, MOM_LOOKBACK, VOL_WINDOW, ANNUALIZE) == (21, 273, 252, 252)
    assert STD_EPS == 1e-12
    assert FINANCIALS == "Financials"


def test_factors_and_metrics():
    assert dict(FACTORS) == {
        "value": ("ep", "bp", "fcfp"),
        "quality": ("roe", "gpa", "neg_accruals", "neg_leverage"),
        "momentum": ("mom",),
        "risk": ("neg_vol",),
    }
    assert list(FACTORS) == list(FACTOR_KEYS)
    assert tuple(METRICS) == ("ep", "bp", "fcfp", "roe", "gpa", "neg_accruals", "neg_leverage",
                              "mom", "neg_vol")


def test_inputs_defaults_and_frozen():
    inp = CompositeInputs("T", IND, True, True)
    assert inp.fx_rate == 1.0 and inp.flags == frozenset()
    for f in ("price", "market_cap", "bvps", "ni_ttm", "ni_period_end", "roe", "cfo_ttm",
              "cfo_period_end", "capex_ttm", "capex_period_end", "gross_profit_ttm",
              "total_assets", "total_debt", "mom", "vol"):
        assert getattr(inp, f) is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.price = 1.0  # type: ignore[misc]


# --------------------------------------------------------------------------- G3 price metrics

RAMP = [float(k + 1) for k in range(300)]
GEOM = [100 * 1.001 ** k for k in range(274)]
ALT = [100 * math.exp(0.01 * (k % 2)) for k in range(274)]
ALT_VOL = 0.159060989580646


def test_g3_momentum_ramp_catches_off_by_one():
    assert momentum(RAMP) == pytest.approx(9.333333333333334, abs=1e-12)
    assert momentum(RAMP) == pytest.approx(279 / 27 - 1, abs=1e-12)


def test_g3_geometric():
    assert momentum(GEOM) == pytest.approx(1.001 ** 252 - 1, abs=1e-12)
    assert momentum(GEOM) == pytest.approx(0.286434044376152, abs=1e-12)
    assert abs(volatility(GEOM)) < 1e-12


def test_g3_geometric_273_bars():
    assert momentum(GEOM[:273]) is None
    assert abs(volatility(GEOM[:273])) < 1e-12


def test_g3_alternating():
    assert volatility(ALT) == pytest.approx(ALT_VOL, abs=1e-12)
    assert volatility(ALT) == pytest.approx(0.01 * 252 / math.sqrt(251), abs=1e-12)
    assert momentum(ALT) == pytest.approx(0.0, abs=1e-12)


def test_g3_window_edges():
    c = list(ALT)
    c[-254] = 1000.0
    assert volatility(c) == pytest.approx(ALT_VOL, abs=1e-12)
    assert momentum(c) == pytest.approx(0.0, abs=1e-12)
    c = list(ALT)
    c[-253] = 1000.0
    assert volatility(c) == approx(2.308090)


def test_g3_min_bars():
    assert volatility(ALT[-253:]) == pytest.approx(ALT_VOL, abs=1e-12)
    assert volatility(ALT[-252:]) is None
    assert momentum(RAMP[-274:]) is not None
    assert momentum(RAMP[-273:]) is None
    assert momentum([]) is None and volatility([]) is None


def test_g3_matches_numpy_closed_form():
    rng = np.random.default_rng(7)
    c = list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    assert momentum(c) == pytest.approx(c[-22] / c[-274] - 1, abs=1e-12)
    exp = np.std(np.diff(np.log(c[-253:])), ddof=1) * math.sqrt(252)
    assert volatility(c) == pytest.approx(exp, abs=1e-12)


@pytest.mark.parametrize("bad", [0.0, -1.0])
@pytest.mark.parametrize("fn", [momentum, volatility])
def test_g3_non_positive_raises(fn, bad):
    c = list(RAMP)
    c[1] = bad  # outside both windows: any value <= 0 raises
    with pytest.raises(ValueError):
        fn(c)


def test_g3_spec_example_zero_raises():
    with pytest.raises(ValueError):
        momentum([100.0, 0.0] + [100.0] * 298)


def test_price_metrics_scale_invariant():
    rng = np.random.default_rng(3)
    c = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    k = [x * 37.5 for x in c]
    assert momentum(k) == pytest.approx(momentum(c), abs=1e-12)
    assert volatility(k) == pytest.approx(volatility(c), abs=1e-12)


# --------------------------------------------------------------------------- G1c small functions

def test_winsor_bounds_g1c():
    lo, hi = winsor_bounds([1, 2, 3, 4, 100])
    assert (lo, hi) == (approx(1.2, 1e-12), approx(80.8, 1e-12))
    assert winsorize([1, 2, 3, 4, 100], 1.2, 80.8) == [approx(v, 1e-12) for v in
                                                      [1.2, 2, 3, 4, 80.8]]


def test_winsor_bounds_numpy_linear():
    v = [0.3, -1.0, 2.5, 7.0, 0.0, 1.1, 4.4]
    lo, hi = winsor_bounds(v)
    assert lo == pytest.approx(np.percentile(v, 5), abs=1e-12)
    assert hi == pytest.approx(np.percentile(v, 95), abs=1e-12)


def test_winsor_bounds_empty_raises():
    with pytest.raises(ValueError):
        winsor_bounds([])


def test_winsor_single_value():
    assert winsor_bounds([3.0]) == (approx(3.0, 1e-12), approx(3.0, 1e-12))


def test_sector_zscores_g1c():
    mean, std, z = sector_zscores([1, 2, 3, 4, 5])
    assert mean == approx(3.0, 1e-12) and std == approx(math.sqrt(2), 1e-12)
    assert z == [approx(v) for v in [-1.414214, -0.707107, 0, 0.707107, 1.414214]]


def test_sector_zscores_too_few_and_flat():
    mean, std, z = sector_zscores([1, 2, 3, 4])
    assert z is None and mean == approx(2.5, 1e-12) and std == approx(np.std([1, 2, 3, 4]), 1e-12)
    mean, std, z = sector_zscores([2, 2, 2, 2, 2])
    assert z is None and std == approx(0.0, 1e-12)


def test_sector_zscores_min_names_param_and_eps():
    assert sector_zscores([1, 2, 3], min_names=3)[2] is not None
    assert sector_zscores([1, 2], min_names=3)[2] is None
    # std <= STD_EPS (floating zero) -> no z
    assert sector_zscores([1.0, 1.0, 1.0 + 1e-15], min_names=3)[2] is None


def test_sector_zscores_empty_raises():
    with pytest.raises(ValueError):
        sector_zscores([])


def test_sector_zscores_population_std():
    v = [0.1, 0.5, -0.3, 0.9, 0.2, 0.0]
    mean, std, z = sector_zscores(v)
    assert std == pytest.approx(np.std(v, ddof=0), abs=1e-12)
    assert np.mean(z) == pytest.approx(0, abs=1e-12)
    assert np.std(z, ddof=0) == pytest.approx(1, abs=1e-12)


def test_pct_rank_g1c_ties():
    assert pct_rank({"a": 1, "b": 2, "c": 2, "d": 3}) == {
        "a": approx(0), "b": approx(0.5), "c": approx(0.5), "d": approx(1)}


def test_pct_rank_small():
    assert pct_rank({"a": 5}) == {"a": None}
    assert pct_rank({}) == {}
    assert pct_rank({"a": 2.0, "b": 1.0}) == {"a": approx(1.0), "b": approx(0.0)}


def test_pct_rank_matches_rankdata():
    from scipy.stats import rankdata
    vals = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0, 5.0, 3.0]
    d = {f"t{i}": v for i, v in enumerate(vals)}
    r = rankdata(vals, method="average")
    exp = {f"t{i}": (r[i] - 1) / (len(vals) - 1) for i in range(len(vals))}
    got = pct_rank(d)
    assert got == {k: approx(v, 1e-12) for k, v in exp.items()}


def test_pct_rank_monotone():
    base = {f"t{i}": float(i) for i in range(10)}
    p0 = pct_rank(base)["t3"]
    for new in (3.5, 4.0, 8.5, 100.0):
        assert pct_rank({**base, "t3": new})["t3"] >= p0


@pytest.mark.parametrize("pct,label", [
    (0.80, "BUY"), (0.7999, "HOLD"), (1.0, "BUY"), (0.20, "SELL"), (0.2001, "HOLD"),
    (0.0, "SELL"), (0.5, "HOLD"), (0.92, "BUY"), (0.30, "HOLD"), (0.06, "SELL"),
])
def test_map_signal_boundaries(pct, label):
    assert map_signal(pct) == label


# --------------------------------------------------------------------------- G1 (z given)

def _g1_universe():
    s = {"X": float(np.mean([1.2, 0.8, 0.4, 0.2])), "Y": float(np.mean([1.6, -0.6, -1.9, -0.4])),
         "Z": float(np.mean([-1.0, -0.5, -0.8, -1.1]))}
    fill = (list(np.linspace(-3, -2, 3)) + list(np.linspace(-0.8, -0.4, 11))
            + list(np.linspace(-0.3, 0.6, 30)) + list(np.linspace(1, 2, 4)))
    d = {f"f{i}": v for i, v in enumerate(fill)}
    d.update(s)
    assert len(d) == 51
    return s, d


def test_g1_scores_pct_signal():
    s, d = _g1_universe()
    assert s["X"] == approx(0.65) and s["Y"] == approx(-0.325) and s["Z"] == approx(-0.85)
    p = pct_rank(d)
    assert p["X"] == approx(46 / 50) and p["Y"] == approx(15 / 50) and p["Z"] == approx(3 / 50)
    assert [map_signal(p[t]) for t in "XYZ"] == ["BUY", "HOLD", "SELL"]


def test_g1b_and_veto_pct_boundaries():
    # G1b: Y at rank 43 of 51 -> pct 0.84 -> BUY, and its mom_pct 0.08 <= MOM_VETO_PCT (vetoed).
    assert map_signal(42 / 50) == "BUY"
    assert 4 / 50 <= MOM_VETO_PCT
    mom = pct_rank({f"m{i}": float(i) for i in range(51)})
    assert mom["m5"] == approx(0.10) and mom["m5"] <= MOM_VETO_PCT
    assert mom["m6"] == approx(0.12) and mom["m6"] > MOM_VETO_PCT


# --------------------------------------------------------------------------- raw_metrics

def ci(ticker="T", sector=IND, eligible=True, history_ok=True, **kw):
    return CompositeInputs(ticker, sector, eligible, history_ok, **kw)


FULL = dict(price=50.0, market_cap=10000.0, bvps=20.0, ni_ttm=800.0, ni_period_end=D, roe=0.18,
            cfo_ttm=1100.0, cfo_period_end=D, capex_ttm=300.0, capex_period_end=D,
            gross_profit_ttm=3000.0, total_assets=8000.0, total_debt=1600.0, mom=0.25, vol=0.19)


def test_raw_metrics_full_i1():
    m, flags = raw_metrics(ci(**FULL))
    assert set(m) == set(METRICS)
    exp = dict(ep=0.08, bp=0.4, fcfp=0.08, roe=0.18, gpa=0.375, neg_accruals=0.0375,
               neg_leverage=-0.2, mom=0.25, neg_vol=-0.19)
    assert m == {k: approx(v, 1e-12) for k, v in exp.items()}
    assert flags == frozenset()


def test_raw_metrics_all_missing():
    m, flags = raw_metrics(ci())
    assert set(m) == set(METRICS) and all(v is None for v in m.values())
    assert flags == frozenset()


def test_raw_metrics_fx_conversion():
    # cap_rep = market_cap / fx_rate = 10000 (I6)
    m, _ = raw_metrics(ci(**{**FULL, "market_cap": 13500.0, "price": 54.0, "bvps": 27.0,
                             "ni_ttm": 300.0, "cfo_ttm": 450.0, "capex_ttm": 350.0},
                          fx_rate=1.35))
    assert m["ep"] == approx(0.03, 1e-12) and m["fcfp"] == approx(0.01, 1e-12)
    assert m["bp"] == approx(0.5, 1e-12)  # bvps and price both trading ccy: no fx


@pytest.mark.parametrize("fx", [0.0, -1.0])
def test_raw_metrics_fx_non_positive_raises(fx):
    # SPEC-AMBIGUITY: spec says fx_rate must be > 0 "else ValueError" but not whether the
    # dataclass constructor or raw_metrics raises; either is accepted here.
    with pytest.raises(ValueError):
        raw_metrics(ci(**FULL, fx_rate=fx))


@pytest.mark.parametrize("cap", [0.0, -100.0])
def test_raw_metrics_non_positive_cap(cap):
    m, _ = raw_metrics(ci(**{**FULL, "market_cap": cap}))
    assert m["ep"] is None and m["fcfp"] is None
    assert m["bp"] == approx(0.4, 1e-12)


@pytest.mark.parametrize("price", [0.0, -5.0])
def test_raw_metrics_non_positive_price(price):
    m, _ = raw_metrics(ci(**{**FULL, "price": price}))
    assert m["bp"] is None


@pytest.mark.parametrize("ta", [0.0, -10.0])
def test_raw_metrics_non_positive_total_assets(ta):
    m, _ = raw_metrics(ci(**{**FULL, "total_assets": ta}))
    assert m["gpa"] is None and m["neg_accruals"] is None and m["neg_leverage"] is None
    assert m["ep"] == approx(0.08, 1e-12)


def test_raw_metrics_negatives_kept():
    m, _ = raw_metrics(ci(price=40.0, market_cap=8000.0, bvps=-2.0, ni_ttm=-50.0, ni_period_end=D,
                          cfo_ttm=-100.0, cfo_period_end=D, capex_ttm=200.0, capex_period_end=D))
    assert m["ep"] == approx(-0.00625, 1e-12)
    assert m["bp"] == approx(-0.05, 1e-12)
    assert m["fcfp"] == approx(-0.0375, 1e-12)


def test_raw_metrics_roe_mom_vol_passthrough():
    m, _ = raw_metrics(ci(roe=5.0, mom=-0.7, vol=0.4))
    assert m["roe"] == 5.0 and m["mom"] == -0.7 and m["neg_vol"] == approx(-0.4, 1e-15)


def test_raw_metrics_financials_non_fin_missing():
    m, flags = raw_metrics(ci(sector=FIN, **FULL))
    for k in ("fcfp", "gpa", "neg_accruals", "neg_leverage"):
        assert m[k] is None
    assert m["ep"] == approx(0.08, 1e-12) and m["bp"] == approx(0.4, 1e-12)
    assert m["roe"] == 0.18 and m["mom"] == 0.25 and m["neg_vol"] == approx(-0.19, 1e-15)


def test_raw_metrics_fcf_period_mismatch():
    m, flags = raw_metrics(ci(**{**FULL, "capex_period_end": D2}))
    assert m["fcfp"] is None and "FCF_PERIOD_MISMATCH" in flags
    assert "ACCRUALS_PERIOD_MISMATCH" not in flags and m["neg_accruals"] is not None


def test_raw_metrics_accruals_period_mismatch():
    m, flags = raw_metrics(ci(**{**FULL, "ni_period_end": D2}))
    assert m["neg_accruals"] is None and "ACCRUALS_PERIOD_MISMATCH" in flags
    assert "FCF_PERIOD_MISMATCH" not in flags
    assert m["ep"] == approx(0.08, 1e-12)  # ep has no period check


def test_raw_metrics_no_mismatch_flag_when_flow_missing():
    m, flags = raw_metrics(ci(**{**FULL, "capex_ttm": None, "capex_period_end": D2,
                                 "ni_ttm": None, "ni_period_end": D2}))
    assert flags == frozenset()
    assert m["fcfp"] is None and m["neg_accruals"] is None
    # SPEC-AMBIGUITY: both flows present but one period_end None -- flag or not is unspecified.


def test_raw_metrics_no_ttm_name():
    m, _ = raw_metrics(ci(price=25.0, market_cap=5000.0, bvps=12.0, total_assets=1000.0,
                          total_debt=300.0, mom=0.1, vol=0.28, flags=frozenset({"NO_TTM"})))
    assert m["ep"] is None and m["fcfp"] is None and m["roe"] is None
    assert m["gpa"] is None and m["neg_accruals"] is None
    assert m["bp"] == approx(0.48, 1e-12) and m["neg_leverage"] == approx(-0.3, 1e-12)


def test_raw_metrics_computed_for_ineligible():
    m, _ = raw_metrics(ci(eligible=False, history_ok=None, sector=None, **FULL))
    assert m["ep"] == approx(0.08, 1e-12)


# --------------------------------------------------------------------------- G2

def _g2_row(t, sec, elig, price, cap, fx, bvps, ni, roe, cfo, capex, gp, ta, td, mom, vol,
            capex_pe=D, flags=frozenset()):
    return CompositeInputs(
        t, sec, elig, True, price=price, market_cap=cap, fx_rate=fx, bvps=bvps, ni_ttm=ni,
        ni_period_end=D if ni is not None else None, roe=roe, cfo_ttm=cfo,
        cfo_period_end=D if cfo is not None else None, capex_ttm=capex,
        capex_period_end=capex_pe if capex is not None else None, gross_profit_ttm=gp,
        total_assets=ta, total_debt=td, mom=mom, vol=vol, flags=frozenset(flags))


NT = {"NO_TTM"}
G2 = [
    _g2_row("I1", IND, True, 50, 10000, 1, 20, 800, 0.18, 1100, 300, 3000, 8000, 1600, 0.25, 0.19),
    _g2_row("I2", IND, True, 40, 8000, 1, -2, -50, N, 1400, 200, 4000, 6000, 5000, 0.6, 0.15),
    _g2_row("I3", IND, True, 30, 6000, 1, 25, 700, 0.17, 1300, 200, N, 7000, 700, -0.35, 0.16),
    _g2_row("I4", IND, True, 60, 12000, 1, 15, 500, 0.11, 700, 400, N, 10000, 4000, 0.05, 0.3,
            capex_pe=D2),
    _g2_row("I5", IND, True, 25, 5000, 1, 12, N, N, N, N, N, N, N, 0.1, 0.28, flags=NT),
    _g2_row("I6", IND, True, 54, 13500, 1.35, 27, 300, 0.08, 450, 350, N, 9000, 3600, -0.05, 0.35),
    _g2_row("I7", IND, True, 80, 16000, 1, 10, 200, 0.06, 150, 250, N, 12000, 7000, -0.15, 0.45),
    _g2_row("X1", IND, False, 5, 500, 1, 30, -400, -0.5, -100, 50, N, 2000, 1800, -0.8, 1.2),
    _g2_row("F1", FIN, True, 100, 50000, 1, 70, 4500, 0.1, 9000, 500, N, 900000, 100000,
            0.06, 0.21),
    _g2_row("F2", FIN, True, 60, 20000, 1, 50, 1500, 0.09, N, N, N, N, N, 0.08, 0.22),
    _g2_row("F3", FIN, True, 45, 9000, 1, 60, 600, 0.05, N, N, N, N, N, -0.1, 0.3),
    _g2_row("F4", FIN, True, 120, 30000, 1, 40, 3600, 0.16, N, N, N, N, N, -0.25, 0.25),
    _g2_row("F5", FIN, True, 20, 4000, 1, N, N, N, N, N, N, N, N, 0.02, 0.26, flags=NT),
]
TICKERS = [i.ticker for i in G2]

RAW = {  # METRICS order
    "I1": (0.08, 0.4, 0.08, 0.18, 0.375, 0.0375, -0.2, 0.25, -0.19),
    "I2": (-0.00625, -0.05, 0.15, N, 0.666667, 0.241667, -0.833333, 0.6, -0.15),
    "I3": (0.116667, 0.833333, 0.183333, 0.17, N, 0.085714, -0.1, -0.35, -0.16),
    "I4": (0.041667, 0.25, N, 0.11, N, 0.02, -0.4, 0.05, -0.3),
    "I5": (N, 0.48, N, N, N, N, N, 0.1, -0.28),
    "I6": (0.03, 0.5, 0.01, 0.08, N, 0.016667, -0.4, -0.05, -0.35),
    "I7": (0.0125, 0.125, -0.00625, 0.06, N, -0.004167, -0.583333, -0.15, -0.45),
    "X1": (-0.8, 6.0, -0.3, -0.5, N, 0.15, -0.9, -0.8, -1.2),
    "F1": (0.09, 0.7, N, 0.1, N, N, N, 0.06, -0.21),
    "F2": (0.075, 0.833333, N, 0.09, N, N, N, 0.08, -0.22),
    "F3": (0.066667, 1.333333, N, 0.05, N, N, N, -0.1, -0.3),
    "F4": (0.12, 0.333333, N, 0.16, N, N, N, -0.25, -0.25),
    "F5": (N, N, N, N, N, N, N, 0.02, -0.26),
}
BOUNDS = {
    "ep": (0.0021875, 0.1185), "bp": (0.0375, 1.083333), "fcfp": (-0.003, 0.176667),
    "roe": (0.054, 0.176), "gpa": (0.389583, 0.652083), "neg_accruals": (0.001042, 0.202679),
    "neg_leverage": (-0.770833, -0.125), "mom": (-0.295, 0.4075), "neg_vol": (-0.395, -0.1555),
}
CLIPPED = {  # explicit "a -> b" entries of the spec table
    ("I1", "roe"): 0.176, ("I1", "gpa"): 0.389583, ("I2", "ep"): 0.002188,
    ("I2", "bp"): 0.0375, ("I2", "gpa"): 0.652083, ("I2", "neg_accruals"): 0.202679,
    ("I2", "neg_leverage"): -0.770833, ("I2", "mom"): 0.4075, ("I2", "neg_vol"): -0.1555,
    ("I3", "fcfp"): 0.176667, ("I3", "neg_leverage"): -0.125, ("I3", "mom"): -0.295,
    ("I7", "fcfp"): -0.003, ("I7", "neg_accruals"): 0.001042, ("I7", "neg_vol"): -0.395,
    ("F3", "bp"): 1.083333, ("F3", "roe"): 0.054, ("F4", "ep"): 0.1185,
}
SECTOR_STATS = {  # (n, mean, std)
    (FIN, "ep"): (4, 0.087542, 0.019733), (FIN, "bp"): (4, 0.7375, 0.270897),
    (FIN, "roe"): (4, 0.101, 0.038118), (FIN, "mom"): (5, -0.038, 0.123028),
    (FIN, "neg_vol"): (5, -0.248, 0.031875),
    (IND, "ep"): (6, 0.04717, 0.039703), (IND, "bp"): (7, 0.375119, 0.247668),
    (IND, "fcfp"): (5, 0.082733, 0.072106), (IND, "roe"): (5, 0.1192, 0.046761),
    (IND, "gpa"): (2, 0.520833, 0.13125), (IND, "neg_accruals"): (6, 0.0606, 0.068892),
    (IND, "neg_leverage"): (6, -0.413194, 0.218216), (IND, "mom"): (7, 0.044643, 0.220107),
    (IND, "neg_vol"): (7, -0.2615, 0.087936),
}
Z = {  # METRICS order
    "I1": (0.826879, 0.100461, -0.037907, 1.214696, N, -0.335312, 0.976989, 0.932987, 0.813089),
    "I2": (-1.132969, -1.363193, 0.932891, N, N, 2.062346, -1.638923, 1.648547, 1.205419),
    "I3": (1.750396, 1.850116, 1.302719, 1.086383, N, 0.364545, 1.320685, -1.543079, 1.154245),
    "I4": (-0.138615, -0.505189, N, -0.196747, N, -0.589334, 0.060465, 0.024339, -0.437817),
    "I5": (N, 0.423474, N, N, N, N, N, 0.251501, -0.210380),
    "I6": (-0.432461, 0.504228, -1.008706, -0.838311, N, -0.637719, 0.060465, -0.429985, -1.006411),
    "I7": (-0.873230, -1.009897, -1.188997, -1.266021, N, -0.864525, -0.779682, -0.884309,
           -1.518145),
    "X1": (N,) * 9,
    "F1": (0.124582, -0.138429, N, -0.026234, N, N, N, 0.796564, 1.192166),
    "F2": (-0.635577, 0.353762, N, -0.288576, N, N, N, 0.959128, 0.878438),
    "F3": (-1.057888, 1.276621, N, -1.233006, N, N, N, -0.503948, -1.631385),
    "F4": (1.568883, -1.491955, N, 1.547816, N, N, N, -1.723179, -0.062746),
    "F5": (N, N, N, N, N, N, N, 0.471436, -0.376473),
}
# V, Q, M, R, n, S, pct, mom_pct, signal, reason, model flags, labels
FINAL = {
    "I1": (0.296478, 0.618791, 0.932987, 0.813089, 4, 0.665336, 1.0, 0.909091, "BUY", N, set(),
           "BHBH"),
    "I2": (-0.521090, 0.211711, 1.648547, 1.205419, 4, 0.636147, 0.9, 1.0, "HOLD", N,
           {"VETO_DISTRESS"}, "SHBB"),
    "I3": (1.634410, 0.923871, -1.543079, 1.154245, 4, 0.542362, 0.8, 0.0, "HOLD", N,
           {"VETO_MOMENTUM"}, "BBSB"),
    "I4": (-0.321902, -0.241872, 0.024339, -0.437817, 4, -0.244313, 0.3, 0.545455, "HOLD", N,
           {"FCF_PERIOD_MISMATCH"}, "SHHH"),
    "I5": (0.423474, N, 0.251501, -0.210380, 3, 0.154865, 0.5, 0.818182, "HOLD", N, {"NO_TTM"},
           "B-HH"),
    "I6": (-0.312313, -0.471855, -0.429985, -1.006411, 4, -0.555141, 0.2, 0.363636, "SELL", N,
           set(), "HHHS"),
    "I7": (-1.024041, -0.970076, -0.884309, -1.518145, 4, -1.099143, 0.0, 0.181818, "SELL", N,
           set(), "SSSS"),
    "X1": (N, N, N, N, 0, N, N, N, "NOT_ELIGIBLE", N, set(), "----"),
    "F1": (-0.006924, -0.026234, 0.796564, 1.192166, 4, 0.488893, 0.7, 0.636364, "HOLD", N, set(),
           "HHHB"),
    "F2": (-0.140907, -0.288576, 0.959128, 0.878438, 4, 0.352021, 0.6, 0.727273, "HOLD", N, set(),
           "HHBH"),
    "F3": (0.109367, -1.233006, -0.503948, -1.631385, 4, -0.814743, 0.1, 0.272727, "SELL", N,
           set(), "HSHS"),
    "F4": (0.038464, 1.547816, -1.723179, -0.062746, 4, -0.049911, 0.4, 0.090909, "HOLD", N, set(),
           "HBSH"),
    "F5": (N, N, 0.471436, -0.376473, 2, N, N, 0.454545, "NO_SIGNAL", "FEW_FACTORS", {"NO_TTM"},
           "--HH"),
}
FACTOR_PCT = {
    "value": {"I1": 0.8, "I2": 0.1, "I3": 1.0, "I4": 0.2, "I5": 0.9, "I6": 0.3, "I7": 0.0,
              "F1": 0.5, "F2": 0.4, "F3": 0.7, "F4": 0.6},
    "quality": {"I1": 0.777778, "I2": 0.666667, "I3": 0.888889, "I4": 0.444444, "I6": 0.222222,
                "I7": 0.111111, "F1": 0.555556, "F2": 0.333333, "F3": 0.0, "F4": 1.0},
    "momentum": {"I1": 0.818182, "I2": 1.0, "I3": 0.090909, "I4": 0.454545, "I5": 0.545455,
                 "I6": 0.363636, "I7": 0.181818, "F1": 0.727273, "F2": 0.909091, "F3": 0.272727,
                 "F4": 0.0, "F5": 0.636364},
    "risk": {"I1": 0.636364, "I2": 1.0, "I3": 0.818182, "I4": 0.272727, "I5": 0.454545,
             "I6": 0.181818, "I7": 0.090909, "F1": 0.909091, "F2": 0.727273, "F3": 0.0,
             "F4": 0.545455, "F5": 0.363636},
}
LBL = {"B": "BUY", "H": "HOLD", "S": "SELL", "-": None}


def _vals(d, ticker):
    return {m: v for m, v in zip(METRICS, d[ticker]) if v is not None}


@pytest.fixture(scope="module")
def g2():
    res, stats = run_composite(G2, min_sector_names=3)
    return {r.ticker: r for r in res}, stats, res


def test_g2_raw_metrics_direct():
    for inp in G2:
        m, _ = raw_metrics(inp)
        exp = dict(zip(METRICS, RAW[inp.ticker]))
        assert m == {k: (None if v is None else approx(v)) for k, v in exp.items()}, inp.ticker


def test_g2_result_order_and_raw(g2):
    by, _, res = g2
    assert [r.ticker for r in res] == TICKERS
    for t in TICKERS:
        exp = dict(zip(METRICS, RAW[t]))
        assert by[t].metrics == {k: (None if v is None else approx(v)) for k, v in exp.items()}, t
        assert by[t].sector == next(i.sector for i in G2 if i.ticker == t)


def test_g2_bounds(g2):
    _, stats, _ = g2
    assert set(stats.bounds) == set(METRICS)
    for m, (lo, hi) in BOUNDS.items():
        assert stats.bounds[m][0] == approx(lo) and stats.bounds[m][1] == approx(hi), m
    assert stats.bounds["ep"][0] == pytest.approx(0.002187500000000002, abs=1e-12)
    assert stats.bounds["mom"][1] == pytest.approx(0.40749999999999975, abs=1e-12)


def test_g2_winsorized(g2):
    by, stats, _ = g2
    for t in TICKERS:
        if t == "X1":
            assert by[t].winsorized == {}
            continue
        exp = {m: min(max(v, BOUNDS[m][0]), BOUNDS[m][1]) for m, v in _vals(RAW, t).items()}
        assert by[t].winsorized == {m: approx(v) for m, v in exp.items()}, t
    for (t, m), v in CLIPPED.items():
        assert by[t].winsorized[m] == approx(v), (t, m)


def test_g2_sector_stats(g2):
    _, stats, _ = g2
    assert set(stats.sectors) == set(SECTOR_STATS)
    for key, (n, mean, std) in SECTOR_STATS.items():
        s = stats.sectors[key]
        assert s.n == n and s.mean == approx(mean) and s.std == approx(std), key
        assert s.used is (key != (IND, "gpa")), key


def test_g2_zscores(g2):
    by, _, _ = g2
    for t in TICKERS:
        assert by[t].z == {m: approx(v) for m, v in _vals(Z, t).items()}, t


def test_g2_factors_score_signal(g2):
    by, _, _ = g2
    for t, (v, q, mo, r, n, s, pct, mp, sig, reason, _fl, _) in FINAL.items():
        x = by[t]
        for got, exp, name in ((x.value, v, "V"), (x.quality, q, "Q"), (x.momentum, mo, "M"),
                               (x.risk, r, "R"), (x.score, s, "S"), (x.pct, pct, "pct"),
                               (x.mom_pct, mp, "mom_pct")):
            assert got == (None if exp is None else approx(exp)), (t, name)
        assert x.n_factors == n, t
        assert x.signal == sig and x.no_signal_reason == reason, t


def test_g2_flags(g2):
    by, _, _ = g2
    for t, row in FINAL.items():
        assert by[t].flags == frozenset(row[10]), t


def test_g2_factor_pct_and_labels(g2):
    by, _, _ = g2
    for t, row in FINAL.items():
        x = by[t]
        assert set(x.factor_pct) == set(FACTOR_KEYS) and set(x.factor_labels) == set(FACTOR_KEYS)
        for k, ch in zip(FACTOR_KEYS, row[11]):
            assert x.factor_labels[k] == LBL[ch], (t, k)
            exp = FACTOR_PCT[k].get(t)
            assert x.factor_pct[k] == (None if exp is None else approx(exp)), (t, k)


def test_g2_outsider_shape(g2):
    x = g2[0]["X1"]
    assert x.n_factors == 0 and x.score is None and x.pct is None and x.mom_pct is None
    assert x.value is None and x.quality is None and x.momentum is None and x.risk is None
    assert x.winsorized == {} and x.z == {}
    assert all(v is None for v in x.factor_pct.values())
    assert all(v is None for v in x.factor_labels.values())
    assert x.no_signal_reason is None


def test_g2_checks_arithmetic(g2):
    by, _, _ = g2
    assert by["I1"].quality == approx((1.214696 - 0.335312 + 0.976989) / 3)
    assert by["I5"].score == approx((0.423474 + 0.251501 - 0.210380) / 3)


def test_g2_properties_score_mean_and_neutrality(g2):
    by, stats, res = g2
    for x in res:
        if x.score is not None:
            f = [getattr(x, k) for k in FACTOR_KEYS if getattr(x, k) is not None]
            assert x.n_factors == len(f) and x.score == pytest.approx(np.mean(f), abs=1e-12)
    groups: dict = {}
    for x in res:
        for m, z in x.z.items():
            groups.setdefault((x.sector, m), []).append(z)
    for key, zs in groups.items():
        assert np.mean(zs) == pytest.approx(0, abs=1e-12), key
        assert np.std(zs, ddof=0) == pytest.approx(1, abs=1e-12), key


# --------------------------------------------------------------------------- G2b / G2c

def test_g2b_default_min_names():
    res, stats = run_composite(G2)
    by = {r.ticker: r for r in res}
    for t in ("F1", "F2", "F3", "F4"):
        assert (by[t].signal, by[t].no_signal_reason, by[t].n_factors) == \
            ("NO_SIGNAL", "FEW_FACTORS", 2), t
        assert by[t].score is None and by[t].pct is None
    for key in ((FIN, "ep"), (FIN, "bp"), (FIN, "roe")):
        assert stats.sectors[key].used is False
    exp = {"I1": ("BUY", 1.0, set()), "I2": ("HOLD", 0.833333, {"VETO_DISTRESS"}),
           "I3": ("HOLD", 0.666667, set()), "I4": ("HOLD", 0.333333, {"FCF_PERIOD_MISMATCH"}),
           "I5": ("HOLD", 0.5, {"NO_TTM"}), "I6": ("SELL", 0.166667, set()),
           "I7": ("SELL", 0.0, set())}
    for t, (sig, pct, fl) in exp.items():
        assert by[t].signal == sig and by[t].pct == approx(pct) and by[t].flags == frozenset(fl), t
        assert by[t].score == approx(FINAL[t][5]), t
        assert by[t].z == {m: approx(v) for m, v in _vals(Z, t).items()}, t


def test_g2c_x1_eligible_moves_bounds():
    inputs = [dataclasses.replace(i, eligible=True) if i.ticker == "X1" else i for i in G2]
    _, stats = run_composite(inputs, min_sector_names=3)
    assert stats.bounds["bp"][0] == approx(0.04625) and stats.bounds["bp"][1] == approx(3.433333)


def _same(a, b):
    for f in ("ticker", "sector", "signal", "no_signal_reason", "n_factors", "flags"):
        assert getattr(a, f) == getattr(b, f), (a.ticker, f)
    for f in ("score", "pct", "value", "quality", "momentum", "risk", "mom_pct"):
        x, y = getattr(a, f), getattr(b, f)
        assert (x is None) == (y is None) and (x is None or x == pytest.approx(y, abs=1e-12)), \
            (a.ticker, f)
    for f in ("metrics", "winsorized", "z", "factor_pct"):
        x, y = getattr(a, f), getattr(b, f)
        assert set(x) == set(y), (a.ticker, f)
        for k in x:
            assert (x[k] is None) == (y[k] is None), (a.ticker, f, k)
            if x[k] is not None:
                assert x[k] == pytest.approx(y[k], abs=1e-12), (a.ticker, f, k)
    assert a.factor_labels == b.factor_labels


def _same_stats(s1, s2):
    assert set(s1.bounds) == set(s2.bounds) and set(s1.sectors) == set(s2.sectors)
    for m in s1.bounds:
        assert s1.bounds[m] == pytest.approx(s2.bounds[m], abs=1e-12)
    for k in s1.sectors:
        a, b = s1.sectors[k], s2.sectors[k]
        assert a.n == b.n and a.used == b.used
        assert a.mean == pytest.approx(b.mean, abs=1e-12) and a.std == pytest.approx(b.std,
                                                                                     abs=1e-12)


def test_g2c_x1_removed_identical(g2):
    by, stats, _ = g2
    res, stats2 = run_composite([i for i in G2 if i.ticker != "X1"], min_sector_names=3)
    for r in res:
        _same(r, by[r.ticker])
    _same_stats(stats, stats2)


def test_g2c_i1_short_history():
    inputs = [dataclasses.replace(i, history_ok=False) if i.ticker == "I1" else i for i in G2]
    res, stats = run_composite(inputs, min_sector_names=3)
    by = {r.ticker: r for r in res}
    i1 = by["I1"]
    assert (i1.signal, i1.no_signal_reason) == ("NO_SIGNAL", "SHORT_HISTORY")
    assert i1.n_factors == 0 and i1.winsorized == {} and i1.z == {} and i1.score is None
    assert i1.metrics["ep"] == approx(0.08)
    assert stats.sectors[(IND, "bp")].n == 6
    assert (IND, "gpa") in stats.sectors and stats.sectors[(IND, "gpa")].n == 1
    ref, ref_stats = run_composite([i for i in G2 if i.ticker != "I1"], min_sector_names=3)
    for r in ref:
        _same(r, by[r.ticker])
    _same_stats(stats, ref_stats)


# --------------------------------------------------------------------------- properties

def test_permutation_invariance(g2):
    by, stats, _ = g2
    perm = G2[::-1]
    res, stats2 = run_composite(perm, min_sector_names=3)
    assert [r.ticker for r in res] == [i.ticker for i in perm]
    for r in res:
        _same(r, by[r.ticker])
    _same_stats(stats, stats2)


OUTSIDERS = [
    ci("O1", IND, False, True, **{**FULL, "mom": 9.0, "vol": 0.01, "bvps": 900.0}),
    ci("O2", IND, None, None, **FULL),
    ci("O3", FIN, True, False, **{**FULL, "ni_ttm": -1e6}),
    ci("O4", IND, True, None, **FULL),
    ci("O5", None, True, True, **{**FULL, "mom": -5.0}),
    ci("O6", "", True, True, **FULL),
    ci("O7", None, False, False, **FULL),
    ci("O8", None, True, False, **FULL),
]


def test_outsiders_dont_count_and_precedence(g2):
    by, stats, _ = g2
    res, stats2 = run_composite(G2 + OUTSIDERS, min_sector_names=3)
    rb = {r.ticker: r for r in res}
    for t in TICKERS:
        _same(rb[t], by[t])
    _same_stats(stats, stats2)
    exp = {"O1": ("NOT_ELIGIBLE", None), "O2": ("NOT_ELIGIBLE", None),
           "O3": ("NO_SIGNAL", "SHORT_HISTORY"), "O4": ("NO_SIGNAL", "SHORT_HISTORY"),
           "O5": ("NO_SIGNAL", "NO_SECTOR"), "O6": ("NO_SIGNAL", "NO_SECTOR"),
           "O7": ("NOT_ELIGIBLE", None), "O8": ("NO_SIGNAL", "SHORT_HISTORY")}
    for t, (sig, reason) in exp.items():
        x = rb[t]
        assert (x.signal, x.no_signal_reason) == (sig, reason), t
        assert x.n_factors == 0 and x.winsorized == {} and x.z == {} and x.mom_pct is None, t
        assert x.metrics["ep"] is not None or t == "O3"  # raw metrics for every input


def test_scale_invariance(g2):
    by, _, _ = g2
    k = 3.7
    money = ("price", "market_cap", "bvps", "ni_ttm", "cfo_ttm", "capex_ttm", "gross_profit_ttm",
             "total_assets", "total_debt")

    def scale(i):
        return dataclasses.replace(i, **{f: (getattr(i, f) * k if getattr(i, f) is not None
                                             else None) for f in money})
    res, _ = run_composite([scale(i) if i.ticker == "I4" else i for i in G2], min_sector_names=3)
    for r in res:
        _same(r, by[r.ticker])
    # reporting-currency change: reporting amounts x k, fx_rate / k (trading-ccy fields fixed)
    rep = ("ni_ttm", "cfo_ttm", "capex_ttm", "gross_profit_ttm", "total_assets", "total_debt")

    def recur(i):
        d = {f: getattr(i, f) * k for f in rep if getattr(i, f) is not None}
        return dataclasses.replace(i, fx_rate=i.fx_rate / k, **d)
    res, _ = run_composite([recur(i) if i.ticker == "I1" else i for i in G2], min_sector_names=3)
    for r in res:
        _same(r, by[r.ticker])


def test_duplicate_tickers_raise():
    with pytest.raises(ValueError):
        run_composite(G2 + [G2[0]], min_sector_names=3)


# --------------------------------------------------------------------------- vetoes (synthetic)

def _veto_universe(**target):
    """Sector 'Energy': target A0 better than 4 identical mates on ep, bp, roe, risk; all five
    share mom -0.9 (lowest in U; sector std 0 -> no M). z of an outlier among 5 = 2, so A0 has
    V = Q = R = 2, S = 2 > any name of the 20-name sector 'Utilities'. mom_pct(A0) = 2/24."""
    base = dict(price=100.0, market_cap=1000.0, mom=-0.9)
    a0 = ci("A0", "Energy", **{**base, "bvps": -1.0, "ni_ttm": -10.0, "roe": 0.1, "vol": 0.1,
                               **target})
    mates = [ci(f"A{j}", "Energy", **base, bvps=-5.0, ni_ttm=-100.0, roe=-0.1, vol=0.5)
             for j in range(1, 5)]
    others = [ci(f"B{k}", "Utilities", price=100.0, market_cap=1000.0,
                 bvps=float((k * 7) % 20 + 1), ni_ttm=float((k * 3) % 20 + 1),
                 roe=((k * 11) % 20) / 100, vol=0.2 + ((k * 13) % 20) / 100, mom=0.05 * k)
              for k in range(20)]
    res, _ = run_composite([a0, *mates, *others])
    return {r.ticker: r for r in res}


def test_veto_both_flags():
    a0 = _veto_universe()["A0"]
    assert a0.n_factors == 3 and a0.score == approx(2.0) and a0.pct == approx(1.0)
    assert a0.mom_pct == approx(2 / 24)
    assert a0.signal == "HOLD"
    assert {"VETO_DISTRESS", "VETO_MOMENTUM"} <= a0.flags


def test_veto_missing_bvps_never_distress():
    a0 = _veto_universe(bvps=None)["A0"]
    assert a0.pct == approx(1.0) and a0.signal == "HOLD"
    assert "VETO_MOMENTUM" in a0.flags and "VETO_DISTRESS" not in a0.flags


def test_veto_missing_ni_never_distress():
    a0 = _veto_universe(ni_ttm=None)["A0"]
    assert a0.pct == approx(1.0) and a0.signal == "HOLD"
    assert "VETO_MOMENTUM" in a0.flags and "VETO_DISTRESS" not in a0.flags


def test_veto_missing_mom_never_momentum():
    a0 = _veto_universe(mom=None)["A0"]
    assert a0.mom_pct is None and a0.signal == "HOLD"
    assert "VETO_DISTRESS" in a0.flags and "VETO_MOMENTUM" not in a0.flags


def test_no_veto_inputs_missing_is_buy():
    a0 = _veto_universe(mom=None, bvps=None)["A0"]
    assert a0.signal == "BUY" and not ({"VETO_DISTRESS", "VETO_MOMENTUM"} & a0.flags)


def test_distress_needs_bvps_non_positive():
    a0 = _veto_universe(bvps=0.0)["A0"]  # bvps <= 0 inclusive; bp = 0 still beats mates (-0.05)
    assert "VETO_DISTRESS" in a0.flags


def test_mates_no_veto_flags():
    by = _veto_universe()
    for j in range(1, 5):
        x = by[f"A{j}"]
        assert x.score == approx(-0.5) and x.signal != "BUY"
        assert not ({"VETO_DISTRESS", "VETO_MOMENTUM"} & x.flags)


def test_few_names():
    """P = {A} only: A has V, M, R; B has M, R; C has V only (min_sector_names=2)."""
    a = ci("A", "Energy", price=10.0, market_cap=100.0, ni_ttm=10.0, mom=0.3, vol=0.2)
    b = ci("B", "Energy", mom=0.1, vol=0.4)
    c = ci("C", "Energy", price=10.0, market_cap=100.0, ni_ttm=2.0)
    res, _ = run_composite([a, b, c], min_sector_names=2)
    by = {r.ticker: r for r in res}
    assert by["A"].n_factors == 3
    assert (by["A"].signal, by["A"].no_signal_reason) == ("NO_SIGNAL", "FEW_NAMES")
    assert by["A"].pct is None
    assert (by["B"].signal, by["B"].no_signal_reason) == ("NO_SIGNAL", "FEW_FACTORS")
    assert (by["C"].signal, by["C"].no_signal_reason) == ("NO_SIGNAL", "FEW_FACTORS")


def test_empty_run():
    res, stats = run_composite([])
    assert res == [] and stats.bounds == {} and stats.sectors == {}


# --------------------------------------------------------------------------- report columns

def test_engine_signal_columns_include_model_c():
    # SPEC-AMBIGUITY: the spec lists the screen_signals.csv columns; we assume they live in
    # valuation.engine.SIGNAL_COLUMNS (where the A/B columns are).
    from valuation.engine import SIGNAL_COLUMNS
    for col in ("c_signal", "c_no_signal_reason", "c_score", "c_pct", "c_n_factors", "c_value",
                "c_quality", "c_momentum", "c_risk"):
        assert col in SIGNAL_COLUMNS, col
