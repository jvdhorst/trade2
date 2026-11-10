"""Spec tests for Model B, quality-adjusted peer P/B (docs/models/peer_regression.md, v0.1.0).

Written from the spec only (not from the implementation). Golden values from the
spec's worked examples G1-G4 were re-derived independently by QA (numpy brute force
and scipy) before being written here. Theil-Sen is additionally checked against
`scipy.stats.theilslopes(..., method="joint")` and a brute-force numpy
implementation in this file on seeded random data (with ties in x).
"""

from __future__ import annotations

import itertools
import math
import random

import numpy as np
import pytest
from scipy.stats import theilslopes

from valuation.models.peer_regression import (
    BOOK_SECTORS,
    MIN_FIT,
    MIN_R2,
    MODEL_VERSION,
    WINSOR,
    PBObs,
    PBResult,  # noqa: F401  (part of the API contract; import must succeed)
    SectorFit,  # noqa: F401  (part of the API contract; import must succeed)
    fit_sector,
    map_signal,
    run_peer_regression,
    theil_sen,
    winsor_bounds,
)

REL = 1e-9
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
W = "ROE_WINSORIZED"
NEG = "NEG_ROE_UNFITTED"


def approx(x, rel=REL):
    return pytest.approx(x, rel=rel, abs=1e-12)


# ---------------------------------------------------------------------------
# G2 data
# ---------------------------------------------------------------------------

# ticker: (price, roe, eligible, bvps, needs_csm)
G2_ROWS = [
    ("U01", 18, 0.02, True, 20.0, False),
    ("U02", 21, 0.05, True, 20.0, False),
    ("U03", 25, 0.07, True, 20.0, False),
    ("U04", 19, 0.08, True, 20.0, False),
    ("U05", 30, 0.08, True, 20.0, False),
    ("U06", 30, 0.09, True, 20.0, False),
    ("U07", 33, 0.10, True, 20.0, False),
    ("U08", 36, 0.11, True, 20.0, False),
    ("U09", 52, 0.12, True, 20.0, False),
    ("U10", 41, 0.13, True, 20.0, False),
    ("U11", 44, 0.14, True, 20.0, False),
    ("U12", 49, 0.16, True, 20.0, False),
    ("U13", 60, 0.40, True, 20.0, False),
    ("U14", 46, 0.15, True, 20.0, False),
    ("U15", 9, -0.05, True, 20.0, False),
    ("U16", 30, 0.10, False, 20.0, False),
    ("U17", 30, 0.10, True, -5.0, False),
    ("U18", 30, None, True, None, True),
]

# ticker: (pb, roe_w, fitted_pb, residual, upside, resid_pct, signal, flags)
G2_EXPECTED = {
    "U01": (0.9, 0.0395, 1.004857315928173, -0.11020607288875167, 0.11650812880908124,
            3 / 14, "HOLD", {W}),
    "U02": (1.05, 0.05, 1.0887993870061192, -0.03628544518600568, 0.03695179714868502,
            4 / 14, "HOLD", set()),
    "U03": (1.25, 0.07, 1.2685756761495808, -0.014751204945060936, 0.014860540919664711,
            7 / 14, "HOLD", set()),
    "U04": (0.95, 0.08, 1.3693063937629153, -0.36559762409873764, 0.44137515132938465,
            2 / 14, "BUY", set()),
    "U05": (1.5, 0.08, 1.3693063937629153, 0.09116077839697734, -0.0871290708247231,
            13 / 14, "HOLD", set()),
    "U06": (1.5, 0.09, 1.4780355916101569, 0.01475120494506088, -0.014642938926562099,
            8 / 14, "HOLD", set()),
    "U07": (1.65, 0.10, 1.595398385647669, 0.03365181129746908, -0.03309188748626124,
            11 / 14, "HOLD", set()),
    "U08": (1.8, 0.11, 1.7220803229470059, 0.04425361483518264, -0.043288709473885745,
            12 / 14, "HOLD", set()),
    "U09": (2.6, 0.12, 1.8588213861563885, 0.33556882150858347, -0.28506869763215825,
            14 / 14, "SELL", set()),
    "U10": (2.05, 0.13, 2.006420315934756, 0.02148759617954732, -0.021258382470850767,
            10 / 14, "HOLD", set()),
    "U11": (2.2, 0.14, 2.165739276606875, 0.015695589941584265, -0.015573056087784143,
            9 / 14, "HOLD", set()),
    "U12": (2.45, 0.16, 2.523333682929258, -0.029492892769882983, 0.029932115481329902,
            5 / 14, "HOLD", set()),
    "U13": (3.0, 0.244, 4.794269422270641, -0.4688090456545069, 0.598089807423547,
            1 / 14, "BUY", {W}),
    "U14": (2.3, 0.15, 2.3377088922928304, -0.016262220939498473, 0.01639517056210016,
            6 / 14, "HOLD", set()),
    "U15": (0.45, 0.0395, 1.004857315928173, -0.803353253448697, 1.2330162576181625,
            0.0, "BUY", {W, NEG}),
    "U16": (1.5, 0.10, 1.595398385647669, -0.06165836850685569, 0.06359892376511267,
            None, "NOT_ELIGIBLE", set()),
}

G2_SLOPE = 7.640957345191646
G2_INTERCEPT = -0.2969722579041446
G2_R2 = 0.7449629739969964
G2_LO = 0.02 + 0.65 * 0.03
G2_HI = 0.16 + 0.35 * 0.24


def obs_from(rows, sector="Financials", inelig=(), flags=None):
    out = []
    for t, price, roe, elig, bvps, csm in rows:
        out.append(PBObs(t, sector, float(price), bvps, roe, elig and t not in inelig,
                         frozenset() if flags is None else flags.get(t, frozenset()), csm))
    return out


def g2_obs(inelig=()):
    obs = obs_from(G2_ROWS, inelig=inelig)
    obs.append(PBObs("T01", "Industrials", 50.0, 10.0, 0.2, True))
    obs.append(PBObs("T02", None, 50.0, 10.0, 0.2, True))
    return obs


def by_ticker(results):
    return {r.ticker: r for r in results}


def fin_rows(obs):
    return [o for o in obs if o.sector == "Financials"]


def fit_tuple(f):
    return (f.sector, f.n_fit, f.n_scored, f.slope, f.intercept, f.r2, f.lo, f.hi, f.status)


def assert_fit_equal(a, b, rel=REL, ignore_n_scored=False):
    assert a.sector == b.sector
    assert a.n_fit == b.n_fit
    if not ignore_n_scored:
        assert a.n_scored == b.n_scored
    assert a.status == b.status
    for f in ("slope", "intercept", "r2", "lo", "hi"):
        va, vb = getattr(a, f), getattr(b, f)
        if va is None or vb is None:
            assert va is None and vb is None, f
        else:
            assert va == approx(vb, rel), f


# ---------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------


def test_model_version():
    assert MODEL_VERSION == "peer_regression/0.2.0"  # v0.2.0: revision round v2


def test_constants():
    assert MIN_FIT == 12
    assert MIN_R2 == 0.20
    assert tuple(WINSOR) == (5, 95)
    assert BOOK_SECTORS == frozenset(
        {"Financials", "Utilities", "Real Estate", "Energy", "Materials"})


@pytest.mark.parametrize("name,value", [
    ("BUY_PCT", 0.20), ("SELL_PCT", 0.80), ("BUY_UPSIDE", 0.15), ("SELL_UPSIDE", -0.15),
])
def test_other_constants(name, value):
    import valuation.models.peer_regression as pr

    assert getattr(pr, name) == value


def test_pbobs_defaults_and_frozen():
    o = PBObs("X", "Financials", 10.0, 5.0, 0.1, True)
    assert o.flags == frozenset()
    assert o.needs_csm is False
    with pytest.raises(AttributeError):
        o.price = 3.0  # frozen dataclass


# ---------------------------------------------------------------------------
# G1 / G1b
# ---------------------------------------------------------------------------


def test_g1_proposal_example():
    a, beta, roe, bvps, price = -0.30, 8.0, 0.12, 40.0, 56.0
    fitted = math.exp(a + beta * roe)
    assert fitted == approx(1.9347923344020315)
    assert bvps * fitted == approx(77.39169337608126)
    assert math.log(price / bvps) - (a + beta * roe) == approx(-0.323527763378787)
    assert bvps * fitted / price - 1 == approx(0.38199452457287975)


def test_g1b_theil_sen():
    s, i = theil_sen([1, 2, 3, 4], [1, 3, 2, 5])
    assert s == approx(7 / 6)
    assert i == approx(1 / 12)
    s, i = theil_sen([1, 1, 2], [0, 1, 3])
    assert s == approx(2.5)
    assert i == approx(-2.0)


def test_g1b_theil_sen_no_distinct_x():
    with pytest.raises(ValueError):
        theil_sen([1, 1], [0, 1])
    with pytest.raises(ValueError):
        theil_sen([0.1, 0.1, 0.1], [1.0, 2.0, 3.0])


def test_g1b_winsor_bounds():
    lo, hi = winsor_bounds([0.0, 0.1, 0.2, 0.3, 0.4])
    assert lo == approx(0.02)
    assert hi == approx(0.38)
    with pytest.raises(ValueError):
        winsor_bounds([])


def test_winsor_bounds_matches_numpy_percentile():
    rng = random.Random(7)
    for n in (1, 2, 5, 13, 40):
        x = [rng.uniform(-0.1, 0.4) for _ in range(n)]
        lo, hi = winsor_bounds(x)
        elo, ehi = np.percentile(x, [5, 95])
        assert lo == approx(float(elo))
        assert hi == approx(float(ehi))


@pytest.mark.parametrize("args,expected", [
    ((0.20, 0.15), "BUY"), ((0.2001, 0.5), "HOLD"), ((0.0, 0.1499), "HOLD"),
    ((0.80, -0.15), "SELL"), ((0.7999, -0.5), "HOLD"), ((1.0, -0.1499), "HOLD"),
    ((0.0, 1.0), "BUY"), ((1.0, -1.0), "SELL"), ((0.5, 0.0), "HOLD"),
])
def test_g1b_map_signal(args, expected):
    assert map_signal(*args) == expected


# ---------------------------------------------------------------------------
# G2 full sector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def g2():
    return run_peer_regression(g2_obs())


def test_g2_fit(g2):
    _, fits = g2
    assert set(fits) == {"Financials"}
    f = fits["Financials"]
    assert f.sector == "Financials"
    assert f.n_fit == 14
    assert f.n_scored == 15
    assert f.lo == approx(G2_LO)
    assert f.lo == approx(0.0395)
    assert f.hi == approx(G2_HI)
    assert f.hi == approx(0.24399999999999994)
    assert f.slope == approx(G2_SLOPE)
    assert f.intercept == approx(G2_INTERCEPT)
    assert f.r2 == approx(G2_R2)
    assert f.status == "OK"


def test_g2_results_in_input_order(g2):
    results, _ = g2
    assert [r.ticker for r in results] == [o.ticker for o in g2_obs()]


@pytest.mark.parametrize("ticker", list(G2_EXPECTED))
def test_g2_scored_and_ineligible_rows(g2, ticker):
    results, _ = g2
    r = by_ticker(results)[ticker]
    pb, roe_w, fitted, resid, upside, pct, signal, flags = G2_EXPECTED[ticker]
    row = next(x for x in G2_ROWS if x[0] == ticker)
    assert r.sector == "Financials"
    assert r.roe == approx(row[2])
    assert r.pb == approx(pb)
    assert r.roe_w == approx(roe_w)
    assert r.fitted_pb == approx(fitted)
    assert r.fair_value == approx(20.0 * fitted)
    assert r.residual == approx(resid)
    assert r.implied_upside == approx(upside)
    if pct is None:
        assert r.resid_pct is None
    else:
        assert r.resid_pct == approx(pct)
    assert r.signal == signal
    assert r.no_signal_reason is None
    assert set(r.flags) == flags
    assert r.n_fit == 14
    assert r.r2 == approx(G2_R2)


def test_g2_u08_minimum_gap_rule(g2):
    r = by_ticker(g2[0])["U08"]
    assert r.resid_pct >= 0.80
    assert r.implied_upside > -0.15
    assert r.signal == "HOLD"


def test_g2_neg_book(g2):
    r = by_ticker(g2[0])["U17"]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_BOOK")
    assert r.pb is None
    assert r.roe_w == approx(0.10)
    for f in ("fitted_pb", "fair_value", "residual", "implied_upside", "resid_pct"):
        assert getattr(r, f) is None, f


def test_g2_needs_csm(g2):
    r = by_ticker(g2[0])["U18"]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEEDS_CSM_INPUT")
    for f in ("pb", "roe_w", "fitted_pb", "fair_value", "residual", "implied_upside",
              "resid_pct"):
        assert getattr(r, f) is None, f


@pytest.mark.parametrize("ticker,reason", [("T01", "EV_BRANCH_PENDING"), ("T02", "NO_SECTOR")])
def test_g2_non_book_and_no_sector(g2, ticker, reason):
    r = by_ticker(g2[0])[ticker]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", reason)
    assert r.pb == approx(5.0)
    assert r.fitted_pb is None
    assert r.fair_value is None
    assert r.resid_pct is None
    assert r.n_fit is None
    assert r.r2 is None


def test_g2_identities(g2):
    for r in g2[0]:
        if r.residual is not None:
            assert r.implied_upside == pytest.approx(math.exp(-r.residual) - 1, abs=1e-12)


def test_g2_fair_value_over_price(g2):
    prices = {o.ticker: o.price for o in g2_obs()}
    for r in g2[0]:
        if r.fair_value is not None:
            assert r.fair_value / prices[r.ticker] == pytest.approx(r.fitted_pb / r.pb, abs=1e-12)
            assert r.implied_upside == approx(r.fair_value / prices[r.ticker] - 1)


def test_g2_independent_bruteforce(g2):
    """Recompute G2 with this file's numpy brute force and compare with the model."""
    _, fits = g2
    F = [(p, roe) for t, p, roe, e, bv, c in G2_ROWS if e and bv and bv > 0 and roe is not None
         and roe >= 0 and not c]
    x = np.array([roe for _, roe in F])
    y = np.log(np.array([p / 20.0 for p, _ in F]))
    lo, hi = np.percentile(x, [5, 95])
    xw = np.clip(x, lo, hi)
    slope, intercept = brute_theil_sen(xw, y)
    f = fits["Financials"]
    assert f.slope == approx(slope)
    assert f.intercept == approx(intercept)
    yhat = intercept + slope * xw
    r2 = 1 - ((y - yhat) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    assert f.r2 == approx(r2)


# ---------------------------------------------------------------------------
# G2b, G3, G4 and other statuses
# ---------------------------------------------------------------------------


def test_g2b_n_fit_boundary_passes():
    results, fits = run_peer_regression(g2_obs(inelig=("U01", "U02")))
    f = fits["Financials"]
    assert f.n_fit == 12
    assert f.n_scored == 13
    assert f.status == "OK"
    assert f.lo == approx(0.0755)
    assert f.hi == approx(0.268)
    assert f.slope == approx(6.642676700452296)
    assert f.intercept == approx(-0.16349238213274048)
    assert f.r2 == approx(0.5358034854710718)
    res = by_ticker(results)
    for t in ("U01", "U02"):
        assert res[t].signal == "NOT_ELIGIBLE"
        assert res[t].resid_pct is None
        assert res[t].fitted_pb is not None  # still computed for the human
    assert all(res[t].signal in {"BUY", "HOLD", "SELL"} for t in
               [f"U{i:02d}" for i in range(3, 16)])


def test_g3_few_names():
    results, fits = run_peer_regression(g2_obs(inelig=("U01", "U02", "U03")))
    f = fits["Financials"]
    assert f.n_fit == 11
    assert f.status == "FEW_NAMES"
    assert f.lo is not None and f.hi is not None  # computed when n_fit >= 1
    assert f.slope is not None  # F has distinct x
    res = by_ticker(results)
    for t in [f"U{i:02d}" for i in range(4, 16)]:
        r = res[t]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_FIT"), t
        for fld in ("fitted_pb", "fair_value", "residual", "implied_upside", "resid_pct"):
            assert getattr(r, fld) is None, (t, fld)
        assert r.n_fit == 11
    for t in ("U01", "U02", "U03", "U16"):
        assert res[t].signal == "NOT_ELIGIBLE"
        assert res[t].no_signal_reason is None
    assert res["U17"].no_signal_reason == "NEG_BOOK"
    assert res["U18"].no_signal_reason == "NEEDS_CSM_INPUT"


G4_PRICES = [12, 25, 14, 22, 11, 19, 30, 13, 16, 28, 15, 18]
G4_ROES = [0.02 * i for i in range(1, 13)]


def g4_obs():
    return [PBObs(f"N{i + 1:02d}", "Energy", float(p), 10.0, roe, True)
            for i, (p, roe) in enumerate(zip(G4_PRICES, G4_ROES))]


def test_g4_low_r2():
    results, fits = run_peer_regression(g4_obs())
    assert set(fits) == {"Energy"}
    f = fits["Energy"]
    assert f.n_fit == 12
    assert f.lo == approx(0.031)
    assert f.hi == approx(0.229)
    assert f.slope == approx(1.1467076579307753)
    assert f.intercept == approx(0.29643019419066896)
    assert f.r2 == approx(-0.13506065275017787)
    assert f.status == "LOW_R2"
    assert len(results) == 12
    for r in results:
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_FIT")
        assert r.n_fit == 12
        assert r.r2 == approx(-0.13506065275017787)
        assert r.fitted_pb is None and r.resid_pct is None


def test_fit_sector_direct_g2_and_g4():
    f = fit_sector(fin_rows(g2_obs()))
    assert fit_tuple(f)[:3] == ("Financials", 14, 15)
    assert f.slope == approx(G2_SLOPE)
    assert f.status == "OK"
    f = fit_sector(g4_obs())
    assert f.status == "LOW_R2"
    assert f.n_scored == 12


def test_r2_boundary_sides():
    """r2 just above MIN_R2 -> OK, just below -> LOW_R2 (found by bisection on noise)."""
    base = [(f"Z{i:02d}", 10.0 * (1 + 3 * r), r) for i, r in
            enumerate([0.02 * k for k in range(1, 13)])]

    def fit_for(noise):
        obs = [PBObs(t, "Utilities", p * (1 + (noise if i % 2 else -noise)), 10.0, r, True)
               for i, (t, p, r) in enumerate(base)]
        return obs, fit_sector(obs)

    # The boundary r2 == 0.20 exactly is not constructible in floating point in general;
    # check both sides of it instead.
    _, f_hi = fit_for(0.0)
    assert f_hi.r2 is not None and f_hi.r2 >= 0.20 and f_hi.status == "OK"
    lo_n, hi_n = 0.0, 0.9
    for _ in range(80):
        mid = (lo_n + hi_n) / 2
        if fit_for(mid)[1].r2 >= 0.20:
            lo_n = mid
        else:
            hi_n = mid
    f_ok = fit_for(lo_n)[1]
    f_low = fit_for(hi_n)[1]
    assert f_ok.r2 >= 0.20 and f_ok.status == "OK"
    assert f_low.r2 < 0.20 and f_low.status == "LOW_R2"


def test_no_x_variation():
    obs = [PBObs(f"X{i:02d}", "Utilities", 10.0 + i, 10.0, 0.08, True) for i in range(12)]
    f = fit_sector(obs)
    assert f.n_fit == 12
    assert f.status == "NO_X_VARIATION"
    assert f.lo == approx(0.08) and f.hi == approx(0.08)
    assert f.slope is None and f.intercept is None and f.r2 is None
    results, fits = run_peer_regression(obs)
    assert fits["Utilities"].status == "NO_X_VARIATION"
    assert all(r.no_signal_reason == "NO_FIT" for r in results)


def test_few_names_beats_no_x_variation():
    obs = [PBObs(f"X{i:02d}", "Utilities", 10.0 + i, 10.0, 0.08, True) for i in range(11)]
    assert fit_sector(obs).status == "FEW_NAMES"


def test_zero_variance():
    # pb = 1 for all -> y = ln 1 = 0 exactly, so sum((y - ybar)^2) is exactly 0 in floating
    # point (with pb 1.5 the mean of 12 equal logs can differ from them by an ulp).
    obs = [PBObs(f"Y{i:02d}", "Real Estate", 10.0, 10.0, 0.01 * (i + 1), True)
           for i in range(12)]
    f = fit_sector(obs)
    assert f.status == "ZERO_VARIANCE"
    assert f.slope == pytest.approx(0.0, abs=1e-12)
    assert f.intercept == pytest.approx(0.0, abs=1e-12)
    assert f.r2 is None


def test_sector_with_no_fit_names():
    obs = [PBObs("A1", "Utilities", 10.0, 5.0, 0.1, False),
           PBObs("A2", "Utilities", 10.0, -5.0, 0.1, True)]
    results, fits = run_peer_regression(obs)
    f = fits["Utilities"]
    assert f.n_fit == 0
    assert f.n_scored == 0
    assert f.status == "FEW_NAMES"
    assert f.lo is None and f.hi is None and f.slope is None and f.intercept is None
    assert f.r2 is None
    r = by_ticker(results)
    assert r["A1"].signal == "NOT_ELIGIBLE"
    assert r["A1"].roe_w is None  # no lo/hi
    assert r["A1"].pb == approx(2.0)
    assert r["A2"].no_signal_reason == "NEG_BOOK"


def test_fits_only_for_book_sectors_present():
    obs = g2_obs() + g4_obs() + [PBObs("IT1", "Information Technology", 10.0, 2.0, 0.3, True)]
    _, fits = run_peer_regression(obs)
    assert set(fits) == {"Financials", "Energy"}


def test_fit_sector_value_errors():
    with pytest.raises(ValueError):
        fit_sector([])
    with pytest.raises(ValueError):
        fit_sector(fin_rows(g2_obs()) + g4_obs())


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra,signal,reason", [
    (PBObs("P1", None, 10.0, 5.0, 0.1, False), "NOT_ELIGIBLE", None),
    (PBObs("P2", "Health Care", 10.0, None, None, None, needs_csm=True), "NOT_ELIGIBLE", None),
    (PBObs("P3", None, 10.0, None, None, True, needs_csm=True), "NO_SIGNAL", "NO_SECTOR"),
    (PBObs("P4", "", 10.0, 5.0, 0.1, True), "NO_SIGNAL", "NO_SECTOR"),
    (PBObs("P5", "Health Care", 10.0, None, None, True, needs_csm=True),
     "NO_SIGNAL", "EV_BRANCH_PENDING"),
    (PBObs("P6", "Financials", 10.0, None, None, True, needs_csm=True),
     "NO_SIGNAL", "NEEDS_CSM_INPUT"),
    (PBObs("P7", "Financials", 10.0, 5.0, 0.1, True, needs_csm=True),
     "NO_SIGNAL", "NEEDS_CSM_INPUT"),
    (PBObs("P8", "Financials", 10.0, 0.0, None, True), "NO_SIGNAL", "NEG_BOOK"),
    (PBObs("P9", "Financials", 10.0, None, 0.1, True), "NO_SIGNAL", "NEG_BOOK"),
    (PBObs("P10", "Financials", 10.0, 5.0, None, True), "NO_SIGNAL", "NO_ROE"),
])
def test_precedence(extra, signal, reason):
    results, fits = run_peer_regression(g2_obs() + [extra])
    r = by_ticker(results)[extra.ticker]
    assert (r.signal, r.no_signal_reason) == (signal, reason)
    assert_fit_equal(fits["Financials"], run_peer_regression(g2_obs())[1]["Financials"])


def test_no_roe_beats_no_fit():
    obs = g2_obs(inelig=("U01", "U02", "U03")) + [PBObs("P", "Financials", 10.0, 5.0, None, True)]
    results, fits = run_peer_regression(obs)
    assert fits["Financials"].status == "FEW_NAMES"
    assert by_ticker(results)["P"].no_signal_reason == "NO_ROE"


def test_ineligible_in_failed_sector_is_not_eligible():
    results, _ = run_peer_regression(g4_obs() + [PBObs("Q", "Energy", 10.0, 5.0, 0.1, False)])
    r = by_ticker(results)["Q"]
    assert r.signal == "NOT_ELIGIBLE"
    assert r.fitted_pb is None  # sector status is not OK


def test_flags_pass_through():
    obs = obs_from(G2_ROWS, flags={"U02": frozenset({"STALE_TTM"}),
                                   "U13": frozenset({"ROE_CLIPPED"})})
    results, _ = run_peer_regression(obs)
    r = by_ticker(results)
    assert set(r["U02"].flags) == {"STALE_TTM"}
    assert set(r["U13"].flags) == {"ROE_CLIPPED", W}


def test_labels(g2):
    for r in g2[0]:
        assert r.signal in LABELS
        assert not r.signal.startswith("SCREEN_")
        assert (r.no_signal_reason is not None) == (r.signal == "NO_SIGNAL")


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_price_must_be_positive(price):
    with pytest.raises(ValueError):
        run_peer_regression(g2_obs() + [PBObs("BAD", "Financials", price, 5.0, 0.1, True)])


# ---------------------------------------------------------------------------
# Property expectations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("k", [2.0, 0.37, 11.0])
def test_property_price_scaling(g2, k):
    obs = [PBObs(o.ticker, o.sector, o.price * k, o.bvps, o.roe, o.eligible, o.flags,
                 o.needs_csm) for o in g2_obs()]
    results, fits = run_peer_regression(obs)
    f, f0 = fits["Financials"], g2[1]["Financials"]
    assert f.intercept == approx(f0.intercept + math.log(k))
    assert f.slope == approx(f0.slope)
    assert f.r2 == approx(f0.r2)
    for r, r0 in zip(results, g2[0]):
        assert r.signal == r0.signal
        assert r.no_signal_reason == r0.no_signal_reason
        for fld in ("residual", "resid_pct", "implied_upside", "fitted_pb"):
            v, v0 = getattr(r, fld), getattr(r0, fld)
            if v0 is None:
                assert v is None
            elif fld == "fitted_pb":
                assert v == approx(v0 * k)
            else:
                assert v == pytest.approx(v0, rel=1e-9, abs=1e-12), (r.ticker, fld)


def test_property_g2_times_two_intercept():
    _, fits = run_peer_regression([PBObs(o.ticker, o.sector, o.price * 2, o.bvps, o.roe,
                                         o.eligible, o.flags, o.needs_csm) for o in g2_obs()])
    assert fits["Financials"].intercept == approx(0.39617492265580084)


@pytest.mark.parametrize("extra", [
    PBObs("I1", "Financials", 999.0, 1.0, 0.33, False),  # ineligible
    PBObs("I2", "Financials", 1.0, 30.0, 0.01, True, needs_csm=True),  # needs CSM
    PBObs("I3", "Financials", 50.0, -3.0, 0.2, True),  # NEG_BOOK
    PBObs("I4", "Financials", 50.0, 10.0, None, True),  # roe None
    PBObs("I5", "Health Care", 50.0, 1.0, 0.5, True),  # non-book sector
    PBObs("I6", None, 50.0, 1.0, 0.5, True),  # no sector
])
def test_property_added_names_never_change_fit(g2, extra):
    results, fits = run_peer_regression(g2_obs() + [extra])
    assert set(fits) == set(g2[1])
    assert_fit_equal(fits["Financials"], g2[1]["Financials"])
    base = by_ticker(g2[0])
    for r in results:
        if r.ticker in base:
            assert r.resid_pct == base[r.ticker].resid_pct
            assert r.signal == base[r.ticker].signal


def test_property_negative_roe_name_changes_resid_pct_not_fit(g2):
    extra = PBObs("NEGX", "Financials", 5.0, 20.0, -0.08, True)
    results, fits = run_peer_regression(g2_obs() + [extra])
    f, f0 = fits["Financials"], g2[1]["Financials"]
    assert_fit_equal(f, f0, ignore_n_scored=True)
    assert f.n_scored == f0.n_scored + 1
    new, old = by_ticker(results), by_ticker(g2[0])
    assert NEG in new["NEGX"].flags
    assert new["NEGX"].resid_pct is not None
    changed = [t for t in old if old[t].resid_pct is not None
               and new[t].resid_pct != pytest.approx(old[t].resid_pct)]
    assert changed  # divisor is now 15
    for t in old:  # fitted values unchanged
        if old[t].fitted_pb is not None:
            assert new[t].fitted_pb == approx(old[t].fitted_pb)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_property_permutation_invariance(g2, seed):
    obs = g2_obs()
    perm = list(range(len(obs)))
    random.Random(seed).shuffle(perm)
    results, fits = run_peer_regression([obs[i] for i in perm])
    assert [r.ticker for r in results] == [obs[i].ticker for i in perm]
    assert_fit_equal(fits["Financials"], g2[1]["Financials"])
    base = by_ticker(g2[0])
    for r in results:
        b = base[r.ticker]
        assert r.signal == b.signal and r.no_signal_reason == b.no_signal_reason
        for fld in ("pb", "roe_w", "fitted_pb", "fair_value", "residual", "resid_pct",
                    "implied_upside"):
            v, v0 = getattr(r, fld), getattr(b, fld)
            assert (v is None) == (v0 is None), fld
            if v is not None:
                assert v == approx(v0), fld
        assert set(r.flags) == set(b.flags)


def test_property_resid_pct_ties_share_value():
    # Two scored names with identical (price, roe) have identical residuals.
    obs = fin_rows(g2_obs()) + [PBObs("U07B", "Financials", 33.0, 20.0, 0.10, True)]
    results, _ = run_peer_regression(obs)
    r = by_ticker(results)
    assert r["U07"].resid_pct == r["U07B"].resid_pct
    below = sum(1 for x in results if x.resid_pct is not None and x.residual < r["U07"].residual)
    assert r["U07"].resid_pct == approx(below / 15)


# ---------------------------------------------------------------------------
# Theil-Sen vs scipy (joint) and an independent brute force
# ---------------------------------------------------------------------------


def brute_theil_sen(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    slopes = [(y[j] - y[i]) / (x[j] - x[i])
              for i, j in itertools.combinations(range(len(x)), 2) if x[i] != x[j]]
    if not slopes:
        raise ValueError("no pair with distinct x")
    b = float(np.median(slopes))
    return b, float(np.median(y - b * x))


def _g2_xy(rows, inelig=()):
    F = [(p, roe) for t, p, roe, e, bv, c in rows
         if e and t not in inelig and bv and bv > 0 and roe is not None and roe >= 0 and not c]
    x = np.array([roe for _, roe in F])
    lo, hi = np.percentile(x, [5, 95])
    return np.clip(x, lo, hi), np.log(np.array([p / 20.0 for p, _ in F]))


def _datasets():
    sets = {
        "G2": _g2_xy(G2_ROWS),
        "G2b": _g2_xy(G2_ROWS, inelig=("U01", "U02")),
        "G4": (np.clip(np.array(G4_ROES), *np.percentile(G4_ROES, [5, 95])),
               np.log(np.array(G4_PRICES, float) / 10.0)),
    }
    for seed in range(8):
        rng = np.random.default_rng(1000 + seed)
        n = int(rng.integers(12, 61))
        x = np.round(rng.uniform(-0.02, 0.30, n), 2)  # rounding -> ties in x
        x[: max(2, n // 6)] = x[0]  # force a block of ties
        y = -0.3 + 7.0 * x + rng.normal(0, 0.25, n)
        sets[f"rand{seed}_n{n}"] = (x, y)
    return sets


DATASETS = _datasets()


@pytest.mark.parametrize("name", list(DATASETS))
def test_theil_sen_matches_scipy_joint_and_bruteforce(name):
    x, y = DATASETS[name]
    s, i = theil_sen(list(map(float, x)), list(map(float, y)))
    ref = theilslopes(y, x, method="joint")
    bs, bi = brute_theil_sen(x, y)
    assert s == pytest.approx(ref[0], rel=1e-12, abs=1e-12)
    assert i == pytest.approx(ref[1], rel=1e-12, abs=1e-12)
    assert s == pytest.approx(bs, rel=1e-12, abs=1e-12)
    assert i == pytest.approx(bi, rel=1e-12, abs=1e-12)


def test_theil_sen_has_ties_in_random_data():
    for name, (x, _) in DATASETS.items():
        if name.startswith("rand"):
            assert len(set(x.tolist())) < len(x)


def test_theil_sen_intercept_is_joint_not_separate():
    """The joint intercept differs from scipy's default (separate) on some data; we follow joint."""
    x, y = DATASETS["G2"]
    s, i = theil_sen(list(map(float, x)), list(map(float, y)))
    assert s == approx(G2_SLOPE)
    assert i == approx(G2_INTERCEPT)
    differs = False
    for name, (xx, yy) in DATASETS.items():
        j = theilslopes(yy, xx, method="joint")
        sep = theilslopes(yy, xx, method="separate")
        if abs(j[1] - sep[1]) > 1e-9:
            differs = True
            ts = theil_sen(list(map(float, xx)), list(map(float, yy)))
            assert ts[1] == pytest.approx(j[1], rel=1e-12, abs=1e-12), name
    assert differs
