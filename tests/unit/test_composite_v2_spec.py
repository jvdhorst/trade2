"""Spec tests for Model C v0.2.0 (docs/models/composite.md, revision round v2).

Written from the spec only: G2-v2 (B/P only in BOOK_SECTORS), G4 (cross-section per market),
the market-separation property, `market` validation, stats keyed by market and
MODEL_VERSION. G2 inputs come from tests/unit/test_composite_spec.py (spec G2 table); all
G2-v2 and G4 numbers were re-derived by QA with an independent numpy/scipy script.
Printed golden values have 6 decimals: absolute tolerance 1e-6 (1e-12 for repr values).
"""

from __future__ import annotations

import dataclasses

import pytest
from test_composite_spec import G2, TICKERS, Z  # same-directory spec module (G2 data)

from valuation.models.composite import (
    BOOK_SECTORS,
    MARKETS,
    METRICS,
    MODEL_VERSION,
    CompositeInputs,
    raw_metrics,
    run_composite,
)

TOL = 1e-6
N = None
IND, FIN = "Industrials", "Financials"
FACTOR_KEYS = ("value", "quality", "momentum", "risk")


def approx(x, tol=TOL):
    return pytest.approx(x, abs=tol)


def opt(x):
    return None if x is None else approx(x)


# ---------------------------------------------------------------------------
# Constants and inputs
# ---------------------------------------------------------------------------


def test_constants_v2():
    assert MODEL_VERSION == "composite/0.2.0"
    assert BOOK_SECTORS == frozenset(
        {"Financials", "Utilities", "Real Estate", "Energy", "Materials"})
    assert tuple(MARKETS) == ("TSX", "US")


def test_inputs_market_default_us():
    assert CompositeInputs("T", IND, True, True).market == "US"
    assert CompositeInputs("T", IND, True, True, market="TSX").market == "TSX"


@pytest.mark.parametrize("market", ["NYSE", "tsx", "", None, "CA"])
def test_market_invalid_raises(market):
    # SPEC-AMBIGUITY: constructor or run_composite may raise; either is accepted.
    with pytest.raises(ValueError):
        inp = CompositeInputs("T", IND, True, True, market=market, price=10.0, mom=0.1, vol=0.2)
        run_composite([inp])


BP_FULL = dict(price=50.0, market_cap=10000.0, bvps=20.0, ni_ttm=800.0, roe=0.18, mom=0.25,
               vol=0.19)


@pytest.mark.parametrize("sector", ["Industrials", "Information Technology", "Health Care",
                                    "Communication Services", "Consumer Discretionary",
                                    "Consumer Staples", None, "", "Banks", "financials"])
def test_bp_none_outside_book_sectors(sector):
    m, flags = raw_metrics(CompositeInputs("T", sector, True, True, **BP_FULL))
    assert m["bp"] is None
    assert m["ep"] == approx(0.08, 1e-12)
    assert flags == frozenset()  # no flag for the gated bp


@pytest.mark.parametrize("sector", sorted(BOOK_SECTORS))
def test_bp_kept_in_book_sectors(sector):
    m, _ = raw_metrics(CompositeInputs("T", sector, True, True, **BP_FULL))
    assert m["bp"] == approx(0.4, 1e-12)


# ---------------------------------------------------------------------------
# G2-v2
# ---------------------------------------------------------------------------

G2V2_BP_BOUNDS = (0.3883333333333333, 1.258333333333333)
G2V2_FIN_BP = (4, 0.795, 0.31246999855986)
G2V2_Z_BP = {"F1": -0.304029, "F2": 0.122678, "F3": 1.482809, "F4": -1.301458}
OTHER_BOUNDS = {  # as in v0.1.0 G2
    "ep": (0.0021875, 0.1185), "fcfp": (-0.003, 0.176667), "roe": (0.054, 0.176),
    "gpa": (0.389583, 0.652083), "neg_accruals": (0.001042, 0.202679),
    "neg_leverage": (-0.770833, -0.125), "mom": (-0.295, 0.4075), "neg_vol": (-0.395, -0.1555),
}
OTHER_STATS = {  # (sector, metric): (n, mean, std), as in v0.1.0 G2 (no Industrials bp)
    (FIN, "ep"): (4, 0.087542, 0.019733), (FIN, "roe"): (4, 0.101, 0.038118),
    (FIN, "mom"): (5, -0.038, 0.123028), (FIN, "neg_vol"): (5, -0.248, 0.031875),
    (IND, "ep"): (6, 0.04717, 0.039703), (IND, "fcfp"): (5, 0.082733, 0.072106),
    (IND, "roe"): (5, 0.1192, 0.046761), (IND, "gpa"): (2, 0.520833, 0.13125),
    (IND, "neg_accruals"): (6, 0.0606, 0.068892), (IND, "neg_leverage"): (6, -0.413194, 0.218216),
    (IND, "mom"): (7, 0.044643, 0.220107), (IND, "neg_vol"): (7, -0.2615, 0.087936),
}
# V, Q, M, R, n, S, pct, mom_pct, signal, reason, model flags, value factor_pct
G2V2 = {
    "I1": (0.394486, 0.618791, 0.932987, 0.813089, 4, 0.689838, 0.888889, 0.909091, "BUY", N,
           set(), 0.888889),
    "I2": (-0.100039, 0.211711, 1.648547, 1.205419, 4, 0.741410, 1.0, 1.0, "HOLD", N,
           {"VETO_DISTRESS"}, 0.444444),
    "I3": (1.526557, 0.923871, -1.543079, 1.154245, 4, 0.515399, 0.777778, 0.0, "HOLD", N,
           set(), 1.0),
    "I4": (-0.138615, -0.241872, 0.024339, -0.437817, 4, -0.198491, 0.333333, 0.545455, "HOLD",
           N, {"FCF_PERIOD_MISMATCH"}, 0.333333),
    "I5": (N, N, 0.251501, -0.210380, 2, N, N, 0.818182, "NO_SIGNAL", "FEW_FACTORS",
           {"NO_TTM"}, N),
    "I6": (-0.720583, -0.471855, -0.429985, -1.006411, 4, -0.657209, 0.222222, 0.363636, "HOLD",
           N, set(), 0.111111),
    "I7": (-1.031113, -0.970076, -0.884309, -1.518145, 4, -1.100911, 0.0, 0.181818, "SELL", N,
           set(), 0.0),
    "X1": (N, N, N, N, 0, N, N, N, "NOT_ELIGIBLE", N, set(), N),
    "F1": (-0.089724, -0.026234, 0.796564, 1.192166, 4, 0.468193, 0.666667, 0.636364, "HOLD", N,
           set(), 0.555556),
    "F2": (-0.256449, -0.288576, 0.959128, 0.878438, 4, 0.323135, 0.555556, 0.727273, "HOLD", N,
           set(), 0.222222),
    "F3": (0.212461, -1.233006, -0.503948, -1.631385, 4, -0.788970, 0.111111, 0.272727, "SELL",
           N, set(), 0.777778),
    "F4": (0.133713, 1.547816, -1.723179, -0.062746, 4, -0.026099, 0.444444, 0.090909, "HOLD", N,
           set(), 0.666667),
    "F5": (N, N, 0.471436, -0.376473, 2, N, N, 0.454545, "NO_SIGNAL", "FEW_FACTORS", {"NO_TTM"},
           N),
}


def _z_v2(t):
    z = {m: v for m, v in zip(METRICS, Z[t]) if v is not None and m != "bp"}
    if t in G2V2_Z_BP:
        z["bp"] = G2V2_Z_BP[t]
    return z


@pytest.fixture(scope="module")
def g2v2():
    res, stats = run_composite(G2, min_sector_names=3)
    return {r.ticker: r for r in res}, stats, res


def test_g2v2_all_us():
    assert all(i.market == "US" for i in G2)


def test_g2v2_bp_raw_gating(g2v2):
    by, _, _ = g2v2
    for t in ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "X1"):
        assert by[t].metrics["bp"] is None, t
        assert "bp" not in by[t].winsorized and "bp" not in by[t].z, t
    for t, v in {"F1": 0.7, "F2": 0.833333, "F3": 1.333333, "F4": 0.333333}.items():
        assert by[t].metrics["bp"] == approx(v), t
    assert by["F5"].metrics["bp"] is None


def test_g2v2_bounds(g2v2):
    _, stats, _ = g2v2
    assert set(stats.bounds) == {("US", m) for m in METRICS}
    lo, hi = stats.bounds[("US", "bp")]
    assert lo == pytest.approx(G2V2_BP_BOUNDS[0], abs=1e-12)
    assert hi == pytest.approx(G2V2_BP_BOUNDS[1], abs=1e-12)
    for m, (lo, hi) in OTHER_BOUNDS.items():
        assert stats.bounds[("US", m)] == (approx(lo), approx(hi)), m
    assert by_f3_bp_clipped(g2v2) == approx(1.258333)


def by_f3_bp_clipped(g2v2):
    by = g2v2[0]
    assert by["F4"].winsorized["bp"] == approx(0.388333)
    return by["F3"].winsorized["bp"]


def test_g2v2_sector_stats(g2v2):
    _, stats, _ = g2v2
    exp_keys = {("US", s, m) for (s, m) in OTHER_STATS} | {("US", FIN, "bp")}
    assert set(stats.sectors) == exp_keys
    s = stats.sectors[("US", FIN, "bp")]
    assert s.n == G2V2_FIN_BP[0] and s.used is True
    assert s.mean == pytest.approx(G2V2_FIN_BP[1], abs=1e-12)
    assert s.std == pytest.approx(G2V2_FIN_BP[2], abs=1e-12)
    for (sec, m), (n, mean, std) in OTHER_STATS.items():
        st = stats.sectors[("US", sec, m)]
        assert st.n == n and st.mean == approx(mean) and st.std == approx(std), (sec, m)
        assert st.used is ((sec, m) != (IND, "gpa")), (sec, m)


def test_g2v2_zscores(g2v2):
    by, _, _ = g2v2
    for t in TICKERS:
        exp = {} if t == "X1" else _z_v2(t)
        assert by[t].z == {m: approx(v) for m, v in exp.items()}, t


@pytest.mark.parametrize("t", list(G2V2))
def test_g2v2_table(g2v2, t):
    v, q, mo, r, n, s, pct, mp, sig, reason, flags, vpct = G2V2[t]
    x = g2v2[0][t]
    assert x.value == opt(v)
    assert x.quality == opt(q)
    assert x.momentum == opt(mo)
    assert x.risk == opt(r)
    assert x.n_factors == n
    assert x.score == opt(s)
    assert x.pct == opt(pct)
    assert x.mom_pct == opt(mp)
    assert (x.signal, x.no_signal_reason) == (sig, reason)
    assert x.flags == frozenset(flags)
    assert x.factor_pct["value"] == opt(vpct)


def test_g2v2_i1_value_check(g2v2):
    assert g2v2[0]["I1"].value == approx((0.826879 - 0.037907) / 2)


# ---------------------------------------------------------------------------
# G4 two markets
# ---------------------------------------------------------------------------


def _g4(t, market, sector, price, cap, bvps, ni, roe, mom, vol):
    return CompositeInputs(t, sector, True, True, market=market, price=float(price),
                           market_cap=float(cap), fx_rate=1.0, bvps=float(bvps), ni_ttm=float(ni),
                           roe=roe, mom=mom, vol=vol)


G4 = [
    _g4("U1", "US", IND, 50, 10000, 20, 800, 0.18, 0.25, 0.20),
    _g4("U2", "US", IND, 40, 8000, 10, 300, 0.10, 0.05, 0.25),
    _g4("U3", "US", IND, 30, 6000, 25, 600, 0.15, -0.10, 0.30),
    _g4("U4", "US", IND, 60, 12000, 12, 400, 0.12, 0.40, 0.22),
    _g4("F1", "US", FIN, 100, 50000, 70, 4500, 0.10, 0.06, 0.21),
    _g4("F2", "US", FIN, 60, 20000, 50, 1500, 0.09, 0.08, 0.22),
    _g4("F3", "US", FIN, 45, 9000, 60, 600, 0.05, -0.10, 0.30),
    _g4("T1", "TSX", IND, 20, 2000, 15, 250, 0.14, 0.30, 0.24),
    _g4("T2", "TSX", IND, 35, 3500, 10, 140, 0.08, -0.20, 0.35),
    _g4("T3", "TSX", IND, 15, 1500, 12, 210, 0.16, 0.10, 0.28),
]
G4_BOUNDS = {
    ("US", "ep"): (0.034583333333333334, 0.097), ("US", "bp"): (0.7133333333333333,
                                                                1.2833333333333332),
    ("US", "roe"): (0.062, 0.171), ("US", "mom"): (-0.1, 0.355), ("US", "neg_vol"): (-0.3, -0.203),
    ("TSX", "ep"): (0.0485, 0.1385), ("TSX", "roe"): (0.086, 0.158), ("TSX", "mom"): (-0.17, 0.28),
    ("TSX", "neg_vol"): (-0.343, -0.244),
}
# V, Q, M, R, S, pct, mom_pct, signal
G4_TABLE = {
    "U1": (0.658373, 1.311816, 0.631810, 1.092763, 0.923691, 1.0, 0.833333, "BUY"),
    "U2": (-0.919866, -1.293469, -0.504028, -0.183258, -0.725155, 0.166667, 0.333333, "SELL"),
    "U3": (1.289669, 0.541239, -1.355906, -1.540728, -0.266432, 0.333333, 0.083333, "HOLD"),
    "U4": (-1.028176, -0.559586, 1.228125, 0.631223, 0.067896, 0.5, 1.0, "HOLD"),
    "F1": (0.193046, 0.994832, 0.579324, 0.827606, 0.648702, 0.833333, 0.5, "BUY"),
    "F2": (-0.339250, 0.373062, 0.827606, 0.579324, 0.360185, 0.666667, 0.666667, "HOLD"),
    "F3": (0.146205, -1.367894, -1.406930, -1.406930, -1.008887, 0.0, 0.083333, "SELL"),
    "T1": (0.529908, 0.392232, 1.135550, 1.099853, 0.789386, 1.0, 1.0, "BUY"),
    "T2": (-1.400471, -1.372813, -1.297771, -1.319824, -1.347720, 0.0, 0.0, "SELL"),
    "T3": (0.870563, 0.980581, 0.162221, 0.219971, 0.558334, 0.5, 0.5, "HOLD"),
}


@pytest.fixture(scope="module")
def g4():
    res, stats = run_composite(G4, min_sector_names=3)
    return {r.ticker: r for r in res}, stats, res


def test_g4_bounds_per_market(g4):
    _, stats, _ = g4
    assert set(stats.bounds) == set(G4_BOUNDS)  # no TSX bp: no TSX book-sector name
    for key, (lo, hi) in G4_BOUNDS.items():
        assert stats.bounds[key][0] == pytest.approx(lo, abs=1e-12), key
        assert stats.bounds[key][1] == pytest.approx(hi, abs=1e-12), key


def test_g4_stats_keys(g4):
    _, stats, _ = g4
    exp = ({("US", FIN, m) for m in ("ep", "bp", "roe", "mom", "neg_vol")}
           | {("US", IND, m) for m in ("ep", "roe", "mom", "neg_vol")}
           | {("TSX", IND, m) for m in ("ep", "roe", "mom", "neg_vol")})
    assert set(stats.sectors) == exp
    assert stats.sectors[("US", IND, "ep")].n == 4
    assert stats.sectors[("TSX", IND, "ep")].n == 3
    assert stats.sectors[("US", FIN, "bp")].n == 3


@pytest.mark.parametrize("t", list(G4_TABLE))
def test_g4_table(g4, t):
    v, q, mo, r, s, pct, mp, sig = G4_TABLE[t]
    x = g4[0][t]
    for got, exp, name in ((x.value, v, "V"), (x.quality, q, "Q"), (x.momentum, mo, "M"),
                           (x.risk, r, "R"), (x.score, s, "S"), (x.pct, pct, "pct"),
                           (x.mom_pct, mp, "mom_pct")):
        assert got == approx(exp), (t, name)
    assert x.n_factors == 4
    assert (x.signal, x.no_signal_reason) == (sig, None)
    assert x.flags == frozenset()
    if x.sector == IND:
        assert x.metrics["bp"] is None


def test_g4_f1_value_check(g4):
    f1 = g4[0]["F1"]
    assert f1.z["ep"] == approx(1.323501) and f1.z["bp"] == approx(-0.937410)
    assert f1.value == approx((1.323501 - 0.937410) / 2)


# ---------------------------------------------------------------------------
# Market separation property
# ---------------------------------------------------------------------------


def _same_result(a, b):
    for f in ("ticker", "signal", "no_signal_reason", "n_factors", "flags", "factor_labels"):
        assert getattr(a, f) == getattr(b, f), (a.ticker, f)
    for f in ("score", "pct", "value", "quality", "momentum", "risk", "mom_pct"):
        x, y = getattr(a, f), getattr(b, f)
        assert (x is None) == (y is None), (a.ticker, f)
        if x is not None:
            assert x == pytest.approx(y, abs=1e-12), (a.ticker, f)
    for f in ("winsorized", "z", "factor_pct"):
        x, y = getattr(a, f), getattr(b, f)
        assert set(x) == set(y), (a.ticker, f)
        for k in x:
            assert (x[k] is None) == (y[k] is None)
            if x[k] is not None:
                assert x[k] == pytest.approx(y[k], abs=1e-12), (a.ticker, f, k)


def _market_stats(stats, market):
    b = {k: v for k, v in stats.bounds.items() if k[0] == market}
    s = {k: (v.n, v.mean, v.std, v.used) for k, v in stats.sectors.items() if k[0] == market}
    return b, s


def _same_stats(s1, s2, market):
    b1, t1 = _market_stats(s1, market)
    b2, t2 = _market_stats(s2, market)
    assert set(b1) == set(b2) and set(t1) == set(t2)
    for k in b1:
        assert b1[k] == pytest.approx(b2[k], abs=1e-12), k
    for k in t1:
        assert t1[k][0] == t2[k][0] and t1[k][3] == t2[k][3], k
        assert t1[k][1:3] == pytest.approx(t2[k][1:3], abs=1e-12), k


def _changes(market):
    other = [i for i in G4 if i.market == market]
    t = other[0]
    return [
        [dataclasses.replace(i, mom=-0.9, vol=0.9) if i.ticker == t.ticker else i for i in G4],
        [dataclasses.replace(i, ni_ttm=-5000.0) if i.ticker == t.ticker else i for i in G4],
        [i for i in G4 if i.ticker != t.ticker],
        G4 + [CompositeInputs(f"N{market}", FIN, True, True, market=market, price=10.0,
                              market_cap=1000.0, bvps=30.0, ni_ttm=500.0, roe=0.3, mom=0.9,
                              vol=0.1)],
        [i for i in G4 if i.market != market],
    ]


@pytest.mark.parametrize("changed,kept", [("TSX", "US"), ("US", "TSX")])
def test_property_market_separation(g4, changed, kept):
    by, stats, _ = g4
    for inputs in _changes(changed):
        res, stats2 = run_composite(inputs, min_sector_names=3)
        rb = {r.ticker: r for r in res}
        for t, r in by.items():
            if r.ticker in rb and next(i for i in G4 if i.ticker == t).market == kept:
                _same_result(rb[t], r)
        _same_stats(stats, stats2, kept)


def test_property_market_separation_changes_own_market(g4):
    # Sanity: the TSX change above does move TSX results (otherwise the property is vacuous).
    by, _, _ = g4
    inputs = [dataclasses.replace(i, mom=-0.9, vol=0.9) if i.ticker == "T1" else i for i in G4]
    res, _ = run_composite(inputs, min_sector_names=3)
    assert {r.ticker: r for r in res}["T1"].score != pytest.approx(by["T1"].score, abs=1e-6)


def test_single_market_us_g4_equals_us_part(g4):
    by, _, _ = g4
    res, _ = run_composite([i for i in G4 if i.market == "US"], min_sector_names=3)
    for r in res:
        _same_result(r, by[r.ticker])


def test_few_names_counted_per_market():
    # TSX: A has V, M, R; B has M, R; C has V only -> P_TSX = {A} -> FEW_NAMES, although the
    # US market (G4's US names) has 7 names with S. G4's TSX names are left out (else |P_TSX| = 4).
    a = CompositeInputs("TA", "Energy", True, True, market="TSX", price=10.0, market_cap=100.0,
                        ni_ttm=10.0, mom=0.3, vol=0.2)
    b = CompositeInputs("TB", "Energy", True, True, market="TSX", mom=0.1, vol=0.4)
    c = CompositeInputs("TC", "Energy", True, True, market="TSX", price=10.0, market_cap=100.0,
                        ni_ttm=2.0)
    res, _ = run_composite([*(x for x in G4 if x.market == "US"), a, b, c], min_sector_names=2)
    by = {r.ticker: r for r in res}
    assert by["TA"].n_factors == 3
    assert (by["TA"].signal, by["TA"].no_signal_reason) == ("NO_SIGNAL", "FEW_NAMES")
    assert by["TA"].pct is None
    assert (by["TB"].signal, by["TB"].no_signal_reason) == ("NO_SIGNAL", "FEW_FACTORS")
    assert by["U1"].signal == "BUY"


def test_bp_none_for_every_non_book_name_in_runs(g2v2, g4):
    for by in (g2v2[0], g4[0]):
        for x in by.values():
            if x.sector not in BOOK_SECTORS:
                assert x.metrics["bp"] is None, x.ticker
                assert "bp" not in x.z and "bp" not in x.winsorized, x.ticker
