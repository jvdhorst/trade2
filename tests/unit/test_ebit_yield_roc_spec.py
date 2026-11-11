"""Spec tests for Model J, EBIT yield + return on capital (docs/models/ebit_yield_roc.md v0.1.0).

Written from the spec only (not from the implementation). Goldens G1-G7 are the spec's worked
examples; relative tolerance 1e-9 (absolute 1e-12 for zero). API fixed by the spec:
    JInputs (frozen dataclass), JResult(ticker, market, signal, no_signal_reason, ev, ey, ic, roc,
        rank_ey, rank_roc, combined, position, n, pct, flags)
    compute_metrics(inp) -> per-name metrics (ev, ey, ic, roc)
    map_signal(pct, ey, rf) -> label
    run_ebit_yield_roc(inputs, rf, min_universe=MIN_UNIVERSE) -> list[JResult] (same order)
`hypothesis` is not a dependency, so the property tests are seeded random loops.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from valuation.models.ebit_yield_roc import (
    BUY_PCT,
    BUY_SPREAD,
    CYCLICAL_SECTORS,
    EXCLUDED_SECTORS,
    MIN_UNIVERSE,
    MODEL_VERSION,
    SELL_PCT,
    SELL_SPREAD,
    JInputs,
    JResult,
    compute_metrics,
    map_signal,
    run_ebit_yield_roc,
)

REL = 1e-9
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
RF = {"US": 0.035}
MONEY = ("market_cap", "ebit_ttm", "total_debt", "cash", "total_equity", "preferred", "nci")
REPORTING = ("ebit_ttm", "total_debt", "cash", "total_equity", "preferred", "nci")
RANK_FIELDS = ("rank_ey", "rank_roc", "combined", "position", "n", "pct")


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def opt(x):
    return None if x is None else approx(x)


def make(ticker="P", **kw) -> JInputs:
    base = dict(ticker=ticker, market="US", sector="Industrials", eligible=True,
                market_cap=2000.0, fx_rate=1.0, ebit_ttm=300.0, total_debt=700.0, cash=200.0,
                total_equity=500.0, preferred=0.0, nci=0.0, flags=frozenset())
    base.update(kw)
    return JInputs(**base)


def metrics(inp):
    """compute_metrics returns (ev, ey, ic, roc).
    SPEC-AMBIGUITY: the spec names the four metrics but not the container; we accept a 4-tuple
    in that order or an object/mapping with attributes/keys ev, ey, ic, roc."""
    m = compute_metrics(inp)
    if isinstance(m, dict):
        return m["ev"], m["ey"], m["ic"], m["roc"]
    if all(hasattr(m, k) for k in ("ev", "ey", "ic", "roc")):
        return m.ev, m.ey, m.ic, m.roc
    ev, ey, ic, roc = m
    return ev, ey, ic, roc


def run(inputs, rf=RF, min_universe=None):
    if min_universe is None:
        return run_ebit_yield_roc(inputs, rf)
    return run_ebit_yield_roc(inputs, rf, min_universe=min_universe)


def by(results):
    return {r.ticker: r for r in results}


# cap, debt, cash, equity, EBIT
G1 = {"P": (2000, 700, 200, 500, 300), "Q": (3500, 600, 100, 0, 200),
      "R": (1200, 400, 100, 1200, 150), "T": (4000, 1500, 500, 1000, 400),
      "S": (4500, 700, 200, 1500, 100)}
# EV, EY, IC, ROC, rank_ey, rank_roc, combined, position, pct, signal
G1_OUT = {
    "P": (2500, 0.12, 1000, 0.3, 1, 2, 3, 1, 0.2, "BUY"),
    "Q": (4000, 0.05, 500, 0.4, 4, 1, 5, 2, 0.4, "HOLD"),
    "R": (1500, 0.1, 1500, 0.1, 2, 4, 6, 3, 0.6, "HOLD"),
    "T": (5000, 0.08, 2000, 0.2, 3, 3, 6, 4, 0.8, "HOLD"),
    "S": (5000, 0.02, 2000, 0.05, 5, 5, 10, 5, 1.0, "SELL"),
}


def g1_name(t, suffix="", **kw):
    cap, debt, cash, eq, ebit = G1[t]
    return make(t + suffix, market_cap=float(cap), total_debt=float(debt), cash=float(cash),
                total_equity=float(eq), ebit_ttm=float(ebit), **kw)


def g1(**kw):
    return [g1_name(t, **kw) for t in G1]


def assert_g1(res, signals=True):
    for t, (ev, ey, ic, roc, rey, rroc, comb, pos, pct, sig) in G1_OUT.items():
        r = res[t]
        assert r.ev == approx(ev), t
        assert r.ey == approx(ey), t
        assert r.ic == approx(ic), t
        assert r.roc == approx(roc), t
        assert (r.rank_ey, r.rank_roc, r.combined, r.position, r.n) == (rey, rroc, comb, pos, 5), t
        assert r.pct == approx(pct), t
        if signals:
            assert r.signal == sig, t
            assert r.no_signal_reason is None, t


def same_result(a, b, rel=REL):
    assert a.ticker == b.ticker
    assert a.signal == b.signal, a.ticker
    assert a.no_signal_reason == b.no_signal_reason, a.ticker
    for f in ("ev", "ey", "ic", "roc", "pct"):
        va, vb = getattr(a, f), getattr(b, f)
        assert (va is None) == (vb is None), (a.ticker, f)
        if va is not None:
            assert va == pytest.approx(vb, rel=rel, abs=1e-12), (a.ticker, f)
    for f in ("rank_ey", "rank_roc", "combined", "position", "n"):
        assert getattr(a, f) == getattr(b, f), (a.ticker, f)
    assert set(a.flags) == set(b.flags), a.ticker


def assert_unranked(r):
    for f in RANK_FIELDS:
        assert getattr(r, f) is None, (r.ticker, f)


# ---------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------


def test_constants():
    assert MODEL_VERSION == "ebit_yield_roc/0.1.0"
    assert BUY_PCT == 0.20
    assert SELL_PCT == 0.80
    assert BUY_SPREAD == 0.03
    assert SELL_SPREAD == 0.0
    assert MIN_UNIVERSE == 12
    assert EXCLUDED_SECTORS == frozenset({"Financials", "Utilities", "Real Estate"})
    assert CYCLICAL_SECTORS == frozenset({"Energy", "Materials"})


def test_inputs_defaults_and_frozen():
    inp = JInputs(ticker="X", market="US", sector="Industrials", eligible=True)
    assert inp.market_cap is None
    assert inp.fx_rate == 1.0
    assert inp.ebit_ttm is None and inp.total_debt is None and inp.cash is None
    assert inp.total_equity is None
    assert inp.preferred == 0.0 and inp.nci == 0.0
    assert inp.flags == frozenset()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.ticker = "Y"  # type: ignore[misc]


def test_defaults_give_no_data_never_zero_filled():
    r = run([JInputs(ticker="X", market="US", sector="Industrials", eligible=True)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert r.ev is None and r.ey is None and r.ic is None and r.roc is None


def test_result_fields():
    r = run([make()])[0]
    assert isinstance(r, JResult)
    for f in ("ticker", "market", "signal", "no_signal_reason", "ev", "ey", "ic", "roc",
              "rank_ey", "rank_roc", "combined", "position", "n", "pct", "flags"):
        assert hasattr(r, f), f
    assert r.ticker == "P" and r.market == "US"


def test_one_result_per_input_same_order():
    inputs = g1()[::-1] + [make("Z", eligible=False)]
    res = run(inputs, min_universe=5)
    assert [r.ticker for r in res] == [i.ticker for i in inputs]
    assert run([], RF) == []


# ---------------------------------------------------------------------------
# G1 / G2
# ---------------------------------------------------------------------------


def test_g1_compute_metrics():
    for t, (ev, ey, ic, roc, *_rest) in G1_OUT.items():
        m = metrics(g1_name(t))
        assert m == (approx(ev), approx(ey), approx(ic), approx(roc)), t


def test_g1_golden():
    res = by(run(g1(), min_universe=5))
    assert_g1(res)
    assert "TOP_PCT_LOW_YIELD" not in set(res["P"].flags)
    for r in res.values():
        assert r.signal in LABELS


def test_g1_input_order_irrelevant():
    base = by(run(g1(), min_universe=5))
    rng = random.Random(7)
    for _ in range(10):
        inputs = g1()
        rng.shuffle(inputs)
        res = by(run(inputs, min_universe=5))
        for t in G1:
            same_result(res[t], base[t])


def test_g1_zero_equity_is_not_a_gate():
    q = by(run(g1(), min_universe=5))["Q"]
    assert q.ic == approx(500.0) and q.rank_roc == 1


def test_g2_default_min_universe_small_universe():
    res = by(run(g1()))
    assert_g1(res, signals=False)
    for r in res.values():
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_UNIVERSE")


# ---------------------------------------------------------------------------
# G3 rf thresholds and map_signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rf,p_sig,p_top,s_sig", [
    (0.09, "BUY", False, "SELL"),
    (0.0901, "HOLD", True, "SELL"),
    (0.095, "HOLD", True, "SELL"),
    (0.02, "BUY", False, "SELL"),
    (0.0199, "BUY", False, "HOLD"),
])
def test_g3_rf_thresholds(rf, p_sig, p_top, s_sig):
    res = by(run(g1(), {"US": rf}, min_universe=5))
    assert res["P"].signal == p_sig
    assert ("TOP_PCT_LOW_YIELD" in set(res["P"].flags)) is p_top
    assert res["S"].signal == s_sig
    assert "TOP_PCT_LOW_YIELD" not in set(res["S"].flags)
    for t in ("Q", "R", "T"):
        assert res[t].signal == "HOLD", t
        assert res[t].no_signal_reason is None
    assert_g1(res, signals=False)  # ranks do not depend on rf


def test_map_signal_spec_cases():
    assert map_signal(0.2, 0.065, 0.035) == "BUY"
    assert map_signal(0.2000001, 0.5, 0.0) == "HOLD"
    assert map_signal(0.8000001, 0.035, 0.035) == "SELL"


@pytest.mark.parametrize("pct,ey,rf,exp", [
    (0.2, 0.12, 0.09, "BUY"),          # ey == rf + 0.03 exactly
    (0.2, 0.1201, 0.0901, "BUY"),
    (0.2, 0.12, 0.0901, "HOLD"),       # ey < rf + 0.03
    (0.1, 0.0649, 0.035, "HOLD"),
    (0.8, 0.0, 0.035, "HOLD"),         # pct 0.8 is not > 0.8
    (1.0, 0.035, 0.035, "SELL"),       # ey == rf
    (1.0, 0.0351, 0.035, "HOLD"),
    (0.81, -1.0, 0.035, "SELL"),
    (0.5, 0.5, 0.035, "HOLD"),
    (0.5, -0.5, 0.035, "HOLD"),
    (0.05, 0.02, 0.02, "HOLD"),        # top pct but low yield: not SELL either
])
def test_map_signal_boundaries(pct, ey, rf, exp):
    assert map_signal(pct, ey, rf) == exp


def test_float_sums_used_by_spec():
    assert 0.035 + 0.03 == 0.065
    assert 0.09 + 0.03 == 0.12


# ---------------------------------------------------------------------------
# G4 FX and input validation
# ---------------------------------------------------------------------------


def test_g4_fx():
    inp = make("P.TO", market="TSX", market_cap=2800.0, fx_rate=1.4)
    ev, ey, ic, roc = metrics(inp)
    assert 2800.0 / 1.4 == 2000.0000000000002
    assert ev == approx(2500.0)
    assert ey == approx(0.12)
    assert ic == approx(1000.0)
    assert roc == approx(0.3)
    p = metrics(make())
    assert (ev, ey, ic, roc) == (approx(p[0]), approx(p[1]), approx(p[2]), approx(p[3]))


def test_g4_fx_in_cross_section():
    tsx = [g1_name(t, ".TO", market="TSX") for t in G1]
    tsx[0] = make("P.TO", market="TSX", market_cap=2800.0, fx_rate=1.4)
    res = by(run(tsx, {"TSX": 0.035}, min_universe=5))
    for t, out in G1_OUT.items():
        r = res[t + ".TO"]
        assert r.market == "TSX"
        assert r.ey == approx(out[1]) and r.roc == approx(out[3])
        assert (r.position, r.signal) == (out[7], out[9])


@pytest.mark.parametrize("kw", [
    dict(market="LSE"), dict(market="us"), dict(market=""), dict(fx_rate=0.0),
    dict(fx_rate=-1.4),
])
def test_value_error(kw):
    with pytest.raises(ValueError):
        inp = make(**kw)
        compute_metrics(inp)
        run([inp])


# ---------------------------------------------------------------------------
# G5 precedence and reasons
# ---------------------------------------------------------------------------


def test_g5a_not_eligible_still_computes():
    for elig in (False, None):
        r = run([make("A", eligible=elig, sector="Financials")])[0]
        assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)
        assert r.ev == approx(2500.0) and r.ey == approx(0.12)
        assert r.ic == approx(1000.0) and r.roc == approx(0.3)
        assert_unranked(r)


def test_not_eligible_is_identity_check():
    # spec: "eligible is not True" -> NOT_ELIGIBLE, so a truthy non-bool is not eligible.
    r = run([make("A", eligible=1)])[0]
    assert r.signal == "NOT_ELIGIBLE"


@pytest.mark.parametrize("sector", ["Financials", "Utilities", "Real Estate", None, ""])
def test_g5bc_excluded_sector(sector):
    r = run([make("B", sector=sector)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert r.ev == approx(2500.0) and r.ey == approx(0.12)
    assert r.ic == approx(1000.0) and r.roc == approx(0.3)
    assert_unranked(r)


def test_g5d_excluded_before_no_data():
    r = run([make("D", ebit_ttm=None, sector="Utilities")])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")


def test_g5e_cash_none():
    r = run([make("E", cash=None)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert r.ev is None and r.ey is None and r.ic is None and r.roc is None
    assert "NEG_IC" not in set(r.flags)
    assert_unranked(r)


@pytest.mark.parametrize("field,exp", [
    # which metrics stay computable when one input is None (ev, ey, ic, roc)
    ("market_cap", (None, None, 1000.0, 0.3)),
    ("ebit_ttm", (2500.0, None, 1000.0, None)),
    ("total_debt", (None, None, None, None)),
    ("cash", (None, None, None, None)),
    ("total_equity", (2500.0, 0.12, None, None)),
])
def test_no_data_each_input(field, exp):
    inp = make("N", **{field: None})
    r = run([inp])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    got = (r.ev, r.ey, r.ic, r.roc)
    assert got == tuple(opt(x) for x in exp)
    assert metrics(inp) == tuple(opt(x) for x in exp)
    assert_unranked(r)


def test_no_data_before_non_positive_ebit():
    r = run([make("N", cash=None, ebit_ttm=-5.0)])[0]
    assert r.no_signal_reason == "NO_DATA"


def test_g5f_zero_ebit():
    r = run([make("F", ebit_ttm=0.0)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_EBIT")
    assert r.ey == approx(0.0) and r.roc == approx(0.0)
    assert r.ev == approx(2500.0) and r.ic == approx(1000.0)
    assert_unranked(r)


def test_g5g_negative_ebit_before_ev():
    r = run([make("G", ebit_ttm=-10.0, market_cap=100.0, total_debt=0.0, cash=105.0,
                  total_equity=10.0)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_EBIT")
    assert r.ev == approx(-5.0) and r.ic == approx(-95.0)
    assert r.ey is None and r.roc is None
    assert "NEG_IC" in set(r.flags)


def test_g5h_non_positive_ev_before_neg_ic():
    r = run([make("H", ebit_ttm=10.0, market_cap=100.0, total_debt=0.0, cash=150.0,
                  total_equity=50.0)])[0]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_EV")
    assert r.ev == approx(-50.0) and r.ic == approx(-100.0)
    assert r.ey is None and r.roc is None
    assert "NEG_IC" in set(r.flags)


def test_g5i_neg_ic():
    for mu in (None, 1):  # NEG_IC (row 6) comes before SMALL_UNIVERSE (row 7)
        r = run([make("I", ebit_ttm=100.0, market_cap=3000.0, total_debt=500.0, cash=100.0,
                      total_equity=-800.0)], min_universe=mu)[0]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_IC")
        assert r.ev == approx(3400.0) and r.ey == approx(0.029411764705882353)
        assert r.ic == approx(-400.0) and r.roc is None
        assert "NEG_IC" in set(r.flags)
        assert_unranked(r)


def test_zero_ev_and_zero_ic_boundaries():
    r = run([make("Z", market_cap=100.0, total_debt=0.0, cash=100.0, total_equity=500.0)])[0]
    assert (r.no_signal_reason, r.ev, r.ey) == ("NON_POSITIVE_EV", approx(0.0), None)
    r = run([make("Z", total_equity=0.0, total_debt=100.0, cash=100.0, market_cap=2000.0)])[0]
    assert (r.no_signal_reason, r.ic, r.roc) == ("NEG_IC", approx(0.0), None)
    assert "NEG_IC" in set(r.flags)


def test_g5j_cyclical_flag():
    inputs = g1()
    inputs[0] = g1_name("P", sector="Energy")
    res = by(run(inputs, min_universe=5))
    assert_g1(res)
    assert "CYCLICAL_EBIT" in set(res["P"].flags)
    for t in ("Q", "R", "T", "S"):
        assert "CYCLICAL_EBIT" not in set(res[t].flags)
    inputs[0] = g1_name("P", sector="Materials")
    assert "CYCLICAL_EBIT" in set(by(run(inputs, min_universe=5))["P"].flags)


G5_EXTRA = [
    make("Xa", eligible=False, sector="Financials"),
    make("Xa2", eligible=None),
    make("Xb", sector="Financials"),
    make("Xc", sector=None),
    make("Xc2", sector=""),
    make("Xd", ebit_ttm=None, sector="Utilities"),
    make("Xe", cash=None),
    make("Xf", ebit_ttm=0.0),
    make("Xg", ebit_ttm=-10.0, market_cap=100.0, total_debt=0.0, cash=105.0, total_equity=10.0),
    make("Xh", ebit_ttm=10.0, market_cap=100.0, total_debt=0.0, cash=150.0, total_equity=50.0),
    make("Xi", ebit_ttm=100.0, market_cap=3000.0, total_debt=500.0, cash=100.0,
         total_equity=-800.0),
]
G5_REASONS = {"Xa": None, "Xa2": None, "Xb": "EXCLUDED_SECTOR", "Xc": "EXCLUDED_SECTOR",
              "Xc2": "EXCLUDED_SECTOR", "Xd": "EXCLUDED_SECTOR", "Xe": "NO_DATA",
              "Xf": "NON_POSITIVE_EBIT", "Xg": "NON_POSITIVE_EBIT", "Xh": "NON_POSITIVE_EV",
              "Xi": "NEG_IC"}


def test_g5_extras_do_not_change_g1():
    res = by(run(g1() + G5_EXTRA, min_universe=5))
    assert_g1(res)
    for t, reason in G5_REASONS.items():
        r = res[t]
        assert r.signal == ("NOT_ELIGIBLE" if t.startswith("Xa") else "NO_SIGNAL"), t
        assert r.no_signal_reason == reason, t
        assert_unranked(r)
    # interleaved order too
    inputs = [x for pair in zip(G5_EXTRA, g1() + [None] * 10) for x in pair if x is not None]
    assert_g1(by(run(inputs, min_universe=5)))


def test_small_universe_before_no_rf():
    for rf in ({}, {"US": None}):
        res = run(g1(), rf)
        assert {r.no_signal_reason for r in res} == {"SMALL_UNIVERSE"}


@pytest.mark.parametrize("rf", [{}, {"US": None}, {"TSX": 0.03}])
def test_no_rf(rf):
    res = by(run(g1(), rf, min_universe=5))
    assert_g1(res, signals=False)  # ranks still output
    for r in res.values():
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_RF")


def test_reason_present_iff_no_signal():
    res = run(g1() + G5_EXTRA, min_universe=5) + run(g1()) + run(g1(), {}, min_universe=5)
    for r in res:
        assert r.signal in LABELS
        assert (r.signal == "NO_SIGNAL") == (r.no_signal_reason is not None), r.ticker
        assert not str(r.signal).startswith("SCREEN_")


def test_pass_through_flags():
    pt = frozenset({"PIT_ESTIMATED", "NO_TTM", "STALE_TTM", "FLOW_CCY_MISMATCH",
                    "EBIT_PERIOD_MISMATCH"})
    inputs = g1()
    inputs[0] = g1_name("P", flags=pt)
    inputs[4] = g1_name("S", flags=frozenset({"PIT_ESTIMATED"}))
    res = by(run(inputs + [make("X", eligible=False, flags=pt),
                           make("Y", ebit_ttm=None, flags=frozenset({"NO_TTM"}))],
                 min_universe=5))
    assert pt <= set(res["P"].flags)
    assert res["P"].signal == "BUY"  # flags are informational, not gates
    assert "PIT_ESTIMATED" in set(res["S"].flags)
    assert pt <= set(res["X"].flags)
    assert "NO_TTM" in set(res["Y"].flags)
    assert res["Y"].no_signal_reason == "NO_DATA"
    assert not ({"PIT_ESTIMATED", "NO_TTM"} & set(res["Q"].flags))


def test_preferred_and_nci_enter_ev_not_ic():
    inp = make(preferred=50.0, nci=30.0)
    ev, ey, ic, roc = metrics(inp)
    assert ev == approx(2580.0)
    assert ey == approx(300.0 / 2580.0)
    assert ic == approx(1000.0)
    assert roc == approx(0.3)


# ---------------------------------------------------------------------------
# G6 ties
# ---------------------------------------------------------------------------


def test_g6_ties():
    inputs = g1()
    inputs.insert(1, dataclasses.replace(g1_name("P"), ticker="O"))
    assert [i.ticker for i in inputs[:2]] == ["P", "O"]
    exp = {  # rank_ey, rank_roc, combined, position, pct, signal
        "O": (1, 2, 3, 1, 0.16666666666666666, "BUY"),
        "P": (1, 2, 3, 2, 0.3333333333333333, "HOLD"),
        "Q": (5, 1, 6, 3, 0.5, "HOLD"),
        "R": (3, 5, 8, 4, 0.6666666666666666, "HOLD"),
        "T": (4, 4, 8, 5, 0.8333333333333334, "HOLD"),
        "S": (6, 6, 12, 6, 1.0, "SELL"),
    }
    for order in (inputs, inputs[::-1]):
        res = by(run(order, min_universe=6))
        for t, (rey, rroc, comb, pos, pct, sig) in exp.items():
            r = res[t]
            assert (r.rank_ey, r.rank_roc, r.combined, r.position, r.n) == \
                (rey, rroc, comb, pos, 6), t
            assert r.pct == approx(pct), t
            assert r.signal == sig, t
        assert "TOP_PCT_LOW_YIELD" not in set(res["P"].flags)  # pct 0.33 is not top quintile


def test_min_rank_three_way_tie():
    # three identical EY / ROC values share rank 1; the next value gets rank 4
    names = [make(t, market_cap=1000.0, total_debt=0.0, cash=0.0, total_equity=1000.0,
                  ebit_ttm=100.0) for t in ("C", "A", "B")]
    names.append(make("D", market_cap=1000.0, total_debt=0.0, cash=0.0, total_equity=1000.0,
                      ebit_ttm=50.0))
    res = by(run(names, min_universe=4))
    for t in "ABC":
        assert (res[t].rank_ey, res[t].rank_roc, res[t].combined) == (1, 1, 2)
    assert (res["D"].rank_ey, res["D"].rank_roc, res["D"].combined) == (4, 4, 8)
    assert [res[t].position for t in "ABCD"] == [1, 2, 3, 4]


def test_combined_tie_broken_by_rank_ey_before_ticker():
    # A: (rank_ey 2, rank_roc 1); Z: (rank_ey 1, rank_roc 2); combined 3 each -> Z first
    a = make("A", market_cap=1000.0, total_debt=0.0, cash=0.0, total_equity=200.0, ebit_ttm=80.0)
    z = make("Z", market_cap=500.0, total_debt=0.0, cash=0.0, total_equity=1000.0, ebit_ttm=80.0)
    res = by(run([a, z], min_universe=2))
    assert (res["Z"].rank_ey, res["Z"].rank_roc) == (1, 2)
    assert (res["A"].rank_ey, res["A"].rank_roc) == (2, 1)
    assert (res["Z"].position, res["A"].position) == (1, 2)


# ---------------------------------------------------------------------------
# G7 market separation, SMALL_UNIVERSE 11 vs 12
# ---------------------------------------------------------------------------


def g7_us(drop=()):
    return [make(f"U{i:02d}", market_cap=1000.0, total_debt=0.0, cash=0.0, total_equity=1000.0,
                 ebit_ttm=10.0 * i) for i in range(1, 13) if i not in drop]


def g7_tsx():
    return [g1_name(t, ".TO", market="TSX") for t in G1]


G7_RF = {"US": 0.035, "TSX": 0.03}


def test_g7_market_separation():
    res = by(run(g7_us() + g7_tsx(), G7_RF))
    for i in range(1, 13):
        r = res[f"U{i:02d}"]
        assert r.ey == approx(0.01 * i) and r.roc == approx(0.01 * i)
        assert (r.rank_ey, r.rank_roc, r.position, r.n) == (13 - i, 13 - i, 13 - i, 12)
        assert r.pct == approx((13 - i) / 12)
        exp = "BUY" if i >= 11 else ("SELL" if i <= 3 else "HOLD")
        assert r.signal == exp, r.ticker
    assert res["U12"].pct == approx(0.08333333333333333)
    assert res["U11"].pct == approx(0.16666666666666666)
    assert res["U03"].pct == approx(0.8333333333333334)
    for t in G1:
        r = res[t + ".TO"]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_UNIVERSE")
        assert r.n == 5


def test_g7_tsx_changes_never_change_us():
    base = by(run(g7_us() + g7_tsx(), G7_RF))
    variants = [
        (g7_us(), {"US": 0.035}),
        (g7_us() + g7_tsx()[:2], G7_RF),
        (g7_us() + g7_tsx(), {"US": 0.035, "TSX": 0.5}),
        (g7_us() + g7_tsx(), {"US": 0.035, "TSX": None}),
        (g7_tsx() + g7_us() + [make(f"V{i}.TO", market="TSX") for i in range(20)], G7_RF),
    ]
    for inputs, rf in variants:
        res = by(run(inputs, rf))
        for i in range(1, 13):
            same_result(res[f"U{i:02d}"], base[f"U{i:02d}"])


def test_small_universe_11_vs_12():
    res = run(g7_us(drop=(1,)) + g7_tsx(), G7_RF)
    for r in res:
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_UNIVERSE")
        if r.market == "US":
            assert r.n == 11 and r.position is not None
    # 12 is enough
    assert {r.signal for r in run(g7_us(), G7_RF)} == {"BUY", "HOLD", "SELL"}
    # explicit min_universe boundary
    assert {r.no_signal_reason for r in run(g1(), RF, min_universe=6)} == {"SMALL_UNIVERSE"}
    assert {r.no_signal_reason for r in run(g1(), RF, min_universe=5)} == {None}


def test_ineligible_names_do_not_count_toward_n():
    us = g7_us()
    us[0] = dataclasses.replace(us[0], eligible=False)
    res = by(run(us, G7_RF))
    assert res["U01"].signal == "NOT_ELIGIBLE"
    assert res["U02"].n == 11 and res["U02"].no_signal_reason == "SMALL_UNIVERSE"


# ---------------------------------------------------------------------------
# Property tests (seeded random loops)
# ---------------------------------------------------------------------------

SECTORS = ["Industrials", "Energy", "Materials", "Information Technology", "Health Care",
           "Consumer Discretionary"]


def rand_name(rng, ticker, market, ranked=True):
    cap = rng.uniform(100, 10000)
    debt = rng.uniform(0, 3000)
    cash = rng.uniform(0, 1500)
    eq = rng.uniform(-200, 5000)
    ebit = rng.uniform(1, 1200)
    kw = dict(market=market, sector=rng.choice(SECTORS), market_cap=cap, total_debt=debt,
              cash=cash, total_equity=eq, ebit_ttm=ebit, preferred=rng.choice([0.0, 25.0]),
              nci=rng.choice([0.0, rng.uniform(0, 100)]), fx_rate=rng.choice([1.0, 1.37]))
    if not ranked:
        kind = rng.randrange(8)
        if kind == 0:
            kw["eligible"] = rng.choice([False, None])
        elif kind == 1:
            kw["sector"] = rng.choice(sorted(EXCLUDED_SECTORS) + [None, ""])
        elif kind == 2:
            kw[rng.choice(["market_cap", "ebit_ttm", "total_debt", "cash", "total_equity"])] = None
        elif kind == 3:
            kw["ebit_ttm"] = rng.choice([0.0, -rng.uniform(1, 500)])
        elif kind == 4:
            kw.update(market_cap=10.0, total_debt=0.0, cash=500.0, total_equity=1000.0)
        elif kind == 5:
            kw.update(total_equity=-5000.0, total_debt=10.0, cash=10.0)
        elif kind == 6:
            kw.update(market_cap=10.0, total_debt=0.0, cash=500.0, total_equity=100.0)
        else:
            kw.update(total_equity=0.0, total_debt=100.0, cash=100.0)
    return make(ticker, **kw)


def rand_universe(rng, n_us=None, n_tsx=None, outsiders=True):
    n_us = rng.randint(0, 25) if n_us is None else n_us
    n_tsx = rng.randint(0, 25) if n_tsx is None else n_tsx
    names = [rand_name(rng, f"U{i:03d}", "US") for i in range(n_us)]
    names += [rand_name(rng, f"T{i:03d}.TO", "TSX") for i in range(n_tsx)]
    if outsiders:
        names += [rand_name(rng, f"X{i:03d}", rng.choice(["US", "TSX"]), ranked=False)
                  for i in range(rng.randint(0, 8))]
    rng.shuffle(names)
    return names


def rand_rf(rng):
    return {"US": rng.choice([rng.uniform(0.0, 0.08), None]) if rng.random() < 0.15
            else rng.uniform(0.0, 0.08),
            "TSX": rng.uniform(0.0, 0.08)}


def in_r(inp):
    """Reference membership of R_m (spec Cross-section step 1)."""
    if inp.eligible is not True or not inp.sector or inp.sector in EXCLUDED_SECTORS:
        return False
    if any(getattr(inp, f) is None for f in ("market_cap", "ebit_ttm", "total_debt", "cash",
                                             "total_equity")):
        return False
    ev = inp.market_cap / inp.fx_rate + inp.total_debt + inp.preferred + inp.nci - inp.cash
    ic = inp.total_equity + inp.total_debt - inp.cash
    return inp.ebit_ttm > 0 and ev > 0 and ic > 0


def reference(inputs, rf, min_universe=MIN_UNIVERSE):
    """Plain-Python reference of the spec's cross-section and precedence."""
    out = {}
    for m in ("US", "TSX"):
        rs = [i for i in inputs if i.market == m and in_r(i)]
        ey, roc = {}, {}
        for i in rs:
            ev = i.market_cap / i.fx_rate + i.total_debt + i.preferred + i.nci - i.cash
            ey[i.ticker] = i.ebit_ttm / ev
            roc[i.ticker] = i.ebit_ttm / (i.total_equity + i.total_debt - i.cash)
        rey = {t: 1 + sum(v > ey[t] for v in ey.values()) for t in ey}
        rroc = {t: 1 + sum(v > roc[t] for v in roc.values()) for t in roc}
        order = sorted(ey, key=lambda t: (rey[t] + rroc[t], rey[t], t))
        n = len(order)
        for pos, t in enumerate(order, 1):
            pct = pos / n
            r = rf.get(m)
            if n < min_universe:
                sig = "SMALL_UNIVERSE"
            elif r is None:
                sig = "NO_RF"
            else:
                sig = ("BUY" if pct <= 0.2 and ey[t] >= r + 0.03
                       else "SELL" if pct > 0.8 and ey[t] <= r else "HOLD")
            out[t] = (rey[t], rroc[t], rey[t] + rroc[t], pos, n, pct, sig, ey[t], roc[t])
    return out


def test_property_matches_reference():
    rng = random.Random(20261111)
    for _ in range(200):
        inputs = rand_universe(rng)
        rf = rand_rf(rng)
        mu = rng.choice([MIN_UNIVERSE, MIN_UNIVERSE, 1, 5])
        res = by(run(inputs, rf, min_universe=mu))
        ref = reference(inputs, rf, mu)
        for inp in inputs:
            r = res[inp.ticker]
            if inp.ticker not in ref:
                assert_unranked(r)
                assert r.signal not in {"BUY", "HOLD", "SELL"}
                continue
            rey, rroc, comb, pos, n, pct, sig, ey, roc = ref[inp.ticker]
            assert (r.rank_ey, r.rank_roc, r.combined, r.position, r.n) == \
                (rey, rroc, comb, pos, n)
            assert r.pct == approx(pct)
            assert r.ey == approx(ey) and r.roc == approx(roc)
            if sig in ("SMALL_UNIVERSE", "NO_RF"):
                assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", sig)
            else:
                assert (r.signal, r.no_signal_reason) == (sig, None)


def test_property_positions_permutation_and_pct():
    rng = random.Random(1)
    for _ in range(200):
        inputs = rand_universe(rng)
        res = run(inputs, rand_rf(rng), min_universe=rng.choice([1, 12]))
        for m in ("US", "TSX"):
            ranked = [r for r in res if r.market == m and r.position is not None]
            n = len(ranked)
            assert sorted(r.position for r in ranked) == list(range(1, n + 1))
            for r in ranked:
                assert r.n == n
                assert 0 < r.pct <= 1
                assert r.pct == approx(r.position / n)
                assert r.combined == r.rank_ey + r.rank_roc
                assert 1 <= r.rank_ey <= n and 1 <= r.rank_roc <= n


def test_property_signal_implications():
    rng = random.Random(2)
    for _ in range(300):
        inputs = rand_universe(rng)
        rf = rand_rf(rng)
        res = run(inputs, rf, min_universe=rng.choice([1, 5, 12]))
        for r in res:
            if r.signal == "BUY":
                assert r.ey >= rf[r.market] + 0.03 and r.pct <= 0.20
            if r.signal == "SELL":
                assert r.ey <= rf[r.market] and r.pct > 0.80
            # SPEC-AMBIGUITY: TOP_PCT_LOW_YIELD is defined with "so HOLD", read as: set only
            # when map_signal ran (not on SMALL_UNIVERSE / NO_RF), and then iff the condition.
            if "TOP_PCT_LOW_YIELD" in set(r.flags):
                assert r.signal == "HOLD"
                assert r.pct <= 0.20 and r.ey < rf[r.market] + 0.03
            if r.signal == "HOLD" and r.pct <= 0.20 and r.ey < rf[r.market] + 0.03:
                assert "TOP_PCT_LOW_YIELD" in set(r.flags)
            if r.ic is not None and r.ic <= 0:
                assert "NEG_IC" in set(r.flags)
            else:
                assert "NEG_IC" not in set(r.flags)


def test_property_small_universe_no_labels():
    rng = random.Random(3)
    for _ in range(200):
        inputs = rand_universe(rng, n_us=rng.randint(0, 11), n_tsx=rng.randint(0, 11))
        res = run(inputs, rand_rf(rng))
        assert not {r.signal for r in res} & {"BUY", "HOLD", "SELL"}


def scale(inp, k, fields=MONEY):
    return dataclasses.replace(inp, **{f: (None if getattr(inp, f) is None
                                           else getattr(inp, f) * k) for f in fields})


def test_property_scaling_invariance():
    rng = random.Random(4)
    for _ in range(150):
        inputs = rand_universe(rng)
        rf = rand_rf(rng)
        base = by(run(inputs, rf))
        k = rng.choice([1000.0, 1e-3, 3.7, 0.25, 1e6])
        res = by(run([scale(i, k) for i in inputs], rf))
        for t, b in base.items():
            r = res[t]
            assert (r.signal, r.no_signal_reason) == (b.signal, b.no_signal_reason), t
            for f in ("rank_ey", "rank_roc", "combined", "position", "n"):
                assert getattr(r, f) == getattr(b, f), (t, f)
            for f in ("ey", "roc", "pct"):
                assert (getattr(r, f) is None) == (getattr(b, f) is None), (t, f)
                if getattr(b, f) is not None:
                    assert getattr(r, f) == pytest.approx(getattr(b, f), rel=1e-9, abs=1e-12)


def test_property_reporting_currency_invariance():
    rng = random.Random(5)
    for _ in range(150):
        inputs = rand_universe(rng)
        rf = rand_rf(rng)
        base = by(run(inputs, rf))
        c = rng.choice([1.35, 0.74, 150.0, 7.1])  # new reporting unit = old / c
        moved = [dataclasses.replace(scale(i, c, REPORTING), fx_rate=i.fx_rate / c)
                 for i in inputs]
        res = by(run(moved, rf))
        for t, b in base.items():
            r = res[t]
            assert (r.signal, r.position, r.rank_ey, r.rank_roc) == \
                (b.signal, b.position, b.rank_ey, b.rank_roc), t
            for f in ("ey", "roc", "pct"):
                if getattr(b, f) is None:
                    assert getattr(r, f) is None
                else:
                    assert getattr(r, f) == pytest.approx(getattr(b, f), rel=1e-9, abs=1e-12)


def test_property_monotone_in_ebit():
    rng = random.Random(6)
    for _ in range(200):
        inputs = rand_universe(rng, outsiders=False)
        rf = rand_rf(rng)
        base = by(run(inputs, rf, min_universe=1))
        ranked = [i for i in inputs if base[i.ticker].position is not None]
        if not ranked:
            continue
        target = rng.choice(ranked)
        k = rng.uniform(1.0001, 5.0)
        bumped = [dataclasses.replace(i, ebit_ttm=i.ebit_ttm * k) if i is target else i
                  for i in inputs]
        res = by(run(bumped, rf, min_universe=1))
        a, b = base[target.ticker], res[target.ticker]
        assert b.position is not None
        assert b.rank_ey <= a.rank_ey
        assert b.rank_roc <= a.rank_roc
        assert b.position <= a.position


def test_property_outsiders_do_not_affect_others():
    rng = random.Random(7)
    for _ in range(200):
        core = rand_universe(rng, outsiders=False)
        rf = rand_rf(rng)
        mu = rng.choice([1, 5, 12])
        base = by(run(core, rf, min_universe=mu))
        extra = [rand_name(rng, f"Y{i:03d}", rng.choice(["US", "TSX"]), ranked=False)
                 for i in range(rng.randint(1, 10))]
        mixed = core + extra
        rng.shuffle(mixed)
        res = by(run(mixed, rf, min_universe=mu))
        for t, b in base.items():
            same_result(res[t], b)
        for e in extra:
            assert_unranked(res[e.ticker])


def test_property_per_market_independence():
    rng = random.Random(8)
    for _ in range(150):
        inputs = rand_universe(rng)
        rf = rand_rf(rng)
        base = by(run(inputs, rf))
        us = [i for i in inputs if i.market == "US"]
        other = [dataclasses.replace(i, ticker="W" + i.ticker)
                 for i in rand_universe(rng, n_us=0) if i.market == "TSX"]
        res = by(run(us + other, {"US": rf["US"], "TSX": rng.uniform(0, 0.1)}))
        for i in us:
            same_result(res[i.ticker], base[i.ticker])
