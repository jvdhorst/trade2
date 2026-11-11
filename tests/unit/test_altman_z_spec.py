"""Spec tests for Model G, Altman Z'' distress score / veto (docs/models/altman_z.md v0.1.0).

Written from the spec only (not from the implementation). Goldens G1-G7 are the spec's worked
examples; relative tolerance 1e-9 (absolute 1e-12 for zero), and the spec's full float reprs of
Z'' are also checked bit for bit (§Formulas, "Evaluation order"). API fixed by the spec:
    GInputs (frozen dataclass), GResult(ticker, market, signal, no_signal_reason, x1, x2, x3, x4,
        z, zone, total_liabilities, cfo_to_debt, distress_veto, flags)
    compute_metrics(inp) -> GMetrics(x1, x2, x3, x4, z, total_liabilities, cfo_to_debt)
    zone(z) -> "distress" | "grey" | "safe";  map_signal(z) -> "SELL" | "HOLD"
    evaluate(inp) -> GResult;  run_altman_z(inputs) -> list[GResult] (same order)
`hypothesis` is not a dependency, so the property tests are seeded random loops.

The module is imported softly so the FROZEN_V2 checks (which need no Model G code) run before
Model G exists; every other test fails (AssertionError) until it is built.
"""

from __future__ import annotations

import dataclasses
import math
import random

import pytest

from valuation.models.frozen import (
    FROZEN_SHA256,
    FROZEN_V2,
    FROZEN_VERSIONS,
    canonical_json,
    params_sha256,
)


class _Missing:
    def __getattr__(self, name):
        raise AssertionError("src/valuation/models/altman_z.py (Model G) is not implemented yet")


try:
    from valuation.models import altman_z as az
    HAVE_G = True
except ImportError:  # not built yet: every test using `az` fails, the FROZEN checks still run
    az = _Missing()
    HAVE_G = False

REL = 1e-9
FROZEN_SHA = "6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e"
G_LABELS = {"HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
REASONS = {"EXCLUDED_SECTOR", "NO_DATA", "NON_POSITIVE_ASSETS", "NON_POSITIVE_LIABILITIES"}
MONEY = ("total_assets", "current_assets", "current_liabilities", "retained_earnings",
         "ebit_ttm", "total_equity", "cfo_ttm", "total_debt")
REQUIRED = ("total_assets", "current_assets", "current_liabilities", "retained_earnings",
            "ebit_ttm", "total_equity")
X_FIELDS = ("x1", "x2", "x3", "x4", "z", "total_liabilities", "cfo_to_debt")

# Spec goldens (§Worked examples): CA, CL, TA, RE, EBIT, equity
G1 = dict(current_assets=600.0, current_liabilities=500.0, total_assets=2000.0,
          retained_earnings=400.0, ebit_ttm=180.0, total_equity=800.0)
G2 = dict(current_assets=450.0, current_liabilities=600.0, total_assets=2000.0,
          retained_earnings=-100.0, ebit_ttm=20.0, total_equity=300.0)
G4 = dict(total_assets=1000.0, current_assets=300.0, current_liabilities=350.0,
          retained_earnings=-400.0, ebit_ttm=150.0, total_equity=-200.0)
G5 = dict(current_assets=900.0, current_liabilities=400.0, total_assets=2000.0,
          retained_earnings=800.0, ebit_ttm=300.0, total_equity=1200.0)
Z_G1 = 2.2847999999999997
Z_G2 = -0.40250588235294105
Z_G4 = -0.7990000000000002
Z_G5 = 5.527


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def opt(x):
    return None if x is None else approx(x)


def make(ticker="G", base=G1, **kw):
    d = dict(ticker=ticker, market="US", sector="Industrials", eligible=True, cfo_ttm=None,
             total_debt=None, flags=frozenset())
    d.update(base)
    d.update(kw)
    return az.GInputs(**d)


def ev(**kw):
    return az.evaluate(make(**kw))


def check(r, *, signal, reason=None, veto=None, flags=frozenset(), zone=None, **vals):
    assert r.signal == signal
    assert r.no_signal_reason == reason
    assert r.distress_veto is veto
    assert set(r.flags) == set(flags)
    assert r.zone == zone
    for k, v in vals.items():
        got = getattr(r, k)
        if v is None:
            assert got is None, k
        else:
            assert got == approx(v), k


def same_result(a, b):
    for f in ("ticker", "market", "signal", "no_signal_reason", "zone", "distress_veto"):
        assert getattr(a, f) == getattr(b, f), f
    for f in X_FIELDS:
        va, vb = getattr(a, f), getattr(b, f)
        assert (va is None) == (vb is None), f
        if va is not None:
            assert va == pytest.approx(vb, rel=REL, abs=1e-12), f
    assert set(a.flags) == set(b.flags)


G1_VALS = dict(total_liabilities=1200.0, x1=0.05, x2=0.2, x3=0.09, x4=0.6666666666666666,
               z=Z_G1)
G2_VALS = dict(total_liabilities=1700.0, x1=-0.075, x2=-0.05, x3=0.01, x4=0.17647058823529413,
               z=Z_G2)
G4_VALS = dict(total_liabilities=1200.0, x1=-0.05, x2=-0.4, x3=0.15, x4=-0.16666666666666666,
               z=Z_G4)
G5_VALS = dict(total_liabilities=800.0, x1=0.25, x2=0.4, x3=0.15, x4=1.5, z=Z_G5)


# ---------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------


def test_constants():
    assert az.MODEL_VERSION == "altman_z/0.1.0"
    assert az.COEF_X1 == 6.56
    assert az.COEF_X2 == 3.26
    assert az.COEF_X3 == 6.72
    assert az.COEF_X4 == 1.05
    assert az.DISTRESS_Z == 1.10
    assert az.SAFE_Z == 2.60
    assert az.OVERRIDE_CFO_DEBT == 0.25
    assert az.EXCLUDED_SECTORS == frozenset({"Financials", "Utilities", "Real Estate"})
    assert isinstance(az.EXCLUDED_SECTORS, frozenset)


def test_inputs_defaults_and_frozen():
    inp = az.GInputs(ticker="X", market="US", sector="Industrials", eligible=True)
    for f in MONEY:
        assert getattr(inp, f) is None, f  # never defaulted to 0
    assert inp.flags == frozenset()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.total_assets = 1.0
    names = {f.name for f in dataclasses.fields(az.GInputs)}
    assert names >= {"ticker", "market", "sector", "eligible", "flags", *MONEY}
    assert not any("fx" in n for n in names)  # §Inputs: no FX input


def test_defaults_give_no_data_never_zero_filled():
    r = az.evaluate(az.GInputs(ticker="X", market="US", sector="Industrials", eligible=True))
    check(r, signal="NO_SIGNAL", reason="NO_DATA", **{f: None for f in X_FIELDS})


def test_result_fields():
    r = ev()
    for f in ("ticker", "market", "signal", "no_signal_reason", "x1", "x2", "x3", "x4", "z",
              "zone", "total_liabilities", "cfo_to_debt", "distress_veto", "flags"):
        assert hasattr(r, f), f
    assert (r.ticker, r.market) == ("G", "US")


def test_compute_metrics_g1():
    m = az.compute_metrics(make())
    for k, v in G1_VALS.items():
        assert getattr(m, k) == approx(v), k
    assert m.cfo_to_debt is None
    m = az.compute_metrics(make(cfo_ttm=50.0, total_debt=1000.0))
    assert m.cfo_to_debt == approx(0.05)


def test_compute_metrics_container_fields():
    m = az.compute_metrics(make(base=G2))
    for f in X_FIELDS:
        assert hasattr(m, f), f


@pytest.mark.parametrize("market", ["LSE", "", None, "us", "NYSE"])
def test_value_error_on_bad_market(market):
    # G7: market "LSE" -> ValueError. SPEC-AMBIGUITY: the spec does not say whether GInputs
    # construction or evaluate raises; either is accepted.
    with pytest.raises(ValueError):
        az.evaluate(make(market=market))


def test_tsx_market_accepted():
    r = ev(market="TSX")
    assert (r.market, r.signal) == ("TSX", "HOLD")
    assert r.z == approx(Z_G1)


# ---------------------------------------------------------------------------
# Goldens G1-G5
# ---------------------------------------------------------------------------


def test_spec_floats_follow_evaluation_order():
    # the spec's full reprs are what Python's left-to-right sum gives (sanity of the goldens)
    assert 6.56 * 0.05 + 3.26 * 0.2 + 6.72 * 0.09 + 1.05 * (800.0 / 1200.0) == Z_G1
    assert 6.56 * ((450.0 - 600.0) / 2000.0) + 3.26 * (-100.0 / 2000.0) + \
        6.72 * (20.0 / 2000.0) + 1.05 * (300.0 / 1700.0) == Z_G2


def test_g1_grey_hold():
    r = ev()
    check(r, signal="HOLD", veto=False, zone="grey", cfo_to_debt=None, **G1_VALS)
    assert r.z == Z_G1  # bit for bit
    assert "NO_OVERRIDE_DATA" not in r.flags  # not distress


def test_g1_with_cfo_and_debt_unchanged():
    r = ev(cfo_ttm=50.0, total_debt=1000.0)
    check(r, signal="HOLD", veto=False, zone="grey", cfo_to_debt=0.05, **G1_VALS)


def test_g2_distress_sell():
    r = ev(base=G2)
    check(r, signal="SELL", veto=True, zone="distress", flags={"NO_OVERRIDE_DATA"},
          cfo_to_debt=None, **G2_VALS)
    assert r.z == Z_G2


def test_g2b_low_cfo_to_debt_sell_no_flag():
    r = ev(base=G2, cfo_ttm=50.0, total_debt=1000.0)
    check(r, signal="SELL", veto=True, zone="distress", cfo_to_debt=0.05, **G2_VALS)


@pytest.mark.parametrize("cfo,debt", [(50.0, 0.0), (None, 1000.0), (50.0, None),
                                      (50.0, -10.0), (None, None)])
def test_g2c_no_override_data(cfo, debt):
    r = ev(base=G2, cfo_ttm=cfo, total_debt=debt)
    check(r, signal="SELL", veto=True, zone="distress", flags={"NO_OVERRIDE_DATA"},
          cfo_to_debt=None, **G2_VALS)


def test_negative_cfo_sell_stands_without_flag():
    r = ev(base=G2, cfo_ttm=-50.0, total_debt=1000.0)
    check(r, signal="SELL", veto=True, zone="distress", cfo_to_debt=-0.05, **G2_VALS)


def test_g3_cash_rich_override():
    r = ev(base=G2, cfo_ttm=300.0, total_debt=1000.0)
    check(r, signal="HOLD", veto=False, zone="distress", flags={"CASH_RICH_OVERRIDE"},
          cfo_to_debt=0.3, **G2_VALS)
    assert r.z == Z_G2  # z unchanged by the override


def test_g3_exactly_threshold_does_not_override():
    r = ev(base=G2, cfo_ttm=250.0, total_debt=1000.0)
    assert r.cfo_to_debt == 0.25
    check(r, signal="SELL", veto=True, zone="distress", cfo_to_debt=0.25, **G2_VALS)


def test_override_just_above_threshold():
    debt = 1000.0
    cfo = math.nextafter(250.0, math.inf)
    assert cfo / debt > 0.25
    r = ev(base=G2, cfo_ttm=cfo, total_debt=debt)
    check(r, signal="HOLD", veto=False, zone="distress", flags={"CASH_RICH_OVERRIDE"},
          cfo_to_debt=cfo / debt, **G2_VALS)


def test_override_never_applies_outside_distress():
    for base in (G1, G5):
        r = ev(base=base, cfo_ttm=900.0, total_debt=1000.0)
        assert r.signal == "HOLD"
        assert set(r.flags) == set()


def test_g4_negative_equity():
    r = ev(base=G4)
    check(r, signal="SELL", veto=True, zone="distress",
          flags={"NEG_EQUITY", "NO_OVERRIDE_DATA"}, cfo_to_debt=None, **G4_VALS)
    assert r.z == Z_G4


def test_g4_negative_equity_override():
    r = ev(base=G4, cfo_ttm=400.0, total_debt=1000.0)
    check(r, signal="HOLD", veto=False, zone="distress",
          flags={"NEG_EQUITY", "CASH_RICH_OVERRIDE"}, cfo_to_debt=0.4, **G4_VALS)


def test_g5_safe():
    r = ev(base=G5)
    check(r, signal="HOLD", veto=False, zone="safe", cfo_to_debt=None, **G5_VALS)


# ---------------------------------------------------------------------------
# G6 precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("eligible", [False, None, 1, "True"])
def test_g6a_not_eligible_still_computes(eligible):
    r = ev(eligible=eligible, sector="Financials")
    check(r, signal="NOT_ELIGIBLE", reason=None, veto=None, zone="grey", **G1_VALS)
    assert r.z == Z_G1


def test_not_eligible_distress_no_override_flags():
    for kw in (dict(), dict(cfo_ttm=300.0, total_debt=1000.0)):
        r = ev(base=G2, eligible=False, **kw)
        assert r.signal == "NOT_ELIGIBLE"
        assert r.zone == "distress"
        assert r.distress_veto is None
        assert set(r.flags) == set()
    # cfo_to_debt is informational for any name
    assert ev(base=G2, eligible=False, cfo_ttm=300.0, total_debt=1000.0).cfo_to_debt == \
        approx(0.3)


@pytest.mark.parametrize("sector", ["Financials", "Utilities", "Real Estate"])
def test_g6b_excluded_sector(sector):
    r = ev(sector=sector)
    check(r, signal="NO_SIGNAL", reason="EXCLUDED_SECTOR", veto=None, zone="grey", **G1_VALS)


def test_excluded_distress_name_no_override_flags():
    r = ev(base=G2, sector="Utilities")
    check(r, signal="NO_SIGNAL", reason="EXCLUDED_SECTOR", veto=None, zone="distress",
          **G2_VALS)


@pytest.mark.parametrize("sector", [None, ""])
def test_g6c_missing_sector_excluded(sector):
    r = ev(sector=sector)
    assert (r.signal, r.no_signal_reason, r.distress_veto) == \
        ("NO_SIGNAL", "EXCLUDED_SECTOR", None)


def test_g6d_excluded_before_no_data():
    r = ev(retained_earnings=None, sector="Utilities")
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert r.x2 is None and r.z is None and r.zone is None


def test_not_eligible_before_excluded_and_no_data():
    r = ev(eligible=False, sector=None, total_assets=None)
    assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)


def test_g6e_current_liabilities_none():
    r = ev(current_liabilities=None)
    check(r, signal="NO_SIGNAL", reason="NO_DATA", veto=None, zone=None, x1=None, z=None,
          x2=0.2, x3=0.09, x4=0.6666666666666666, total_liabilities=1200.0)


def test_g6f_ebit_none():
    r = ev(ebit_ttm=None)
    check(r, signal="NO_SIGNAL", reason="NO_DATA", zone=None, x3=None, z=None, x1=0.05, x2=0.2,
          x4=0.6666666666666666, total_liabilities=1200.0)


def test_g6f_equity_none():
    r = ev(total_equity=None)
    check(r, signal="NO_SIGNAL", reason="NO_DATA", zone=None, x4=None, total_liabilities=None,
          z=None, x1=0.05, x2=0.2, x3=0.09)


# field set to None -> the X values that must be None (others still computed)
NONE_EFFECT = {
    "total_assets": ("x1", "x2", "x3", "x4", "total_liabilities"),
    "current_assets": ("x1",),
    "current_liabilities": ("x1",),
    "retained_earnings": ("x2",),
    "ebit_ttm": ("x3",),
    "total_equity": ("x4", "total_liabilities"),
}


@pytest.mark.parametrize("field", REQUIRED)
def test_no_data_each_required_input(field):
    r = ev(**{field: None})
    assert (r.signal, r.no_signal_reason, r.distress_veto) == ("NO_SIGNAL", "NO_DATA", None)
    assert r.z is None and r.zone is None
    for f in ("x1", "x2", "x3", "x4", "total_liabilities"):
        if f in NONE_EFFECT[field]:
            assert getattr(r, f) is None, f
        else:
            assert getattr(r, f) == approx(G1_VALS[f]), f
    assert "NO_OVERRIDE_DATA" not in r.flags


def test_no_data_before_non_positive_assets():
    r = ev(total_assets=0.0, current_liabilities=None)
    assert r.no_signal_reason == "NO_DATA"


def test_g6g_zero_assets():
    r = ev(total_assets=0.0)
    check(r, signal="NO_SIGNAL", reason="NON_POSITIVE_ASSETS", zone=None, x1=None, x2=None,
          x3=None, x4=None, z=None, total_liabilities=-800.0)


def test_negative_assets():
    r = ev(total_assets=-5.0)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_ASSETS")
    assert r.x1 is None and r.x4 is None and r.z is None


def test_g6h_zero_assets_before_liabilities():
    r = ev(total_assets=0.0, total_equity=-100.0)
    check(r, signal="NO_SIGNAL", reason="NON_POSITIVE_ASSETS", flags={"NEG_EQUITY"}, zone=None,
          x1=None, x2=None, x3=None, x4=None, z=None, total_liabilities=100.0)


def test_g6i_zero_liabilities():
    r = ev(total_equity=2000.0)
    check(r, signal="NO_SIGNAL", reason="NON_POSITIVE_LIABILITIES", zone=None, x4=None, z=None,
          total_liabilities=0.0, x1=0.05, x2=0.2, x3=0.09)


def test_g6j_negative_liabilities():
    r = ev(total_equity=2100.0)
    check(r, signal="NO_SIGNAL", reason="NON_POSITIVE_LIABILITIES", zone=None, x4=None, z=None,
          total_liabilities=-100.0, x1=0.05, x2=0.2, x3=0.09)


@pytest.mark.parametrize("base,zone_", [(G1, "grey"), (G5, "safe")])
def test_g6k_cfo_debt_never_block(base, zone_):
    r = ev(base=base, cfo_ttm=None, total_debt=None)
    assert (r.signal, r.no_signal_reason, r.zone, r.distress_veto) == ("HOLD", None, zone_, False)


def test_neg_equity_flag_whatever_the_signal():
    for kw in (dict(eligible=False), dict(sector="Financials"), dict(ebit_ttm=None),
               dict(total_assets=0.0)):
        r = ev(base=G4, **kw)
        assert "NEG_EQUITY" in r.flags, kw
    assert "NEG_EQUITY" not in ev(total_equity=0.0).flags  # 0 is not < 0
    assert "NEG_EQUITY" not in ev(total_equity=None).flags


def test_pass_through_flags():
    pt = frozenset({"PIT_ESTIMATED", "FLOW_CCY_MISMATCH", "EBIT_PERIOD_MISMATCH"})
    r = ev(flags=pt)
    assert set(r.flags) == set(pt) and r.signal == "HOLD"
    r = ev(ebit_ttm=None, flags=frozenset({"FLOW_CCY_MISMATCH"}))
    assert set(r.flags) == {"FLOW_CCY_MISMATCH"} and r.no_signal_reason == "NO_DATA"
    r = ev(base=G2, flags=frozenset({"PIT_ESTIMATED"}))
    assert set(r.flags) == {"PIT_ESTIMATED", "NO_OVERRIDE_DATA"}


def test_run_altman_z_order_and_identity():
    inputs = [make("G1"), make("G2", base=G2), make("G6e", current_liabilities=None),
              make("G6a", eligible=False, sector="Financials")]
    res = az.run_altman_z(inputs)
    assert isinstance(res, list) and len(res) == 4
    assert [r.ticker for r in res] == ["G1", "G2", "G6e", "G6a"]
    assert [r.signal for r in res] == ["HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"]
    for i, r in zip(inputs, res):
        same_result(r, az.evaluate(i))
    assert az.run_altman_z([]) == []


# ---------------------------------------------------------------------------
# G7 boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("z,zone_,sig", [
    (1.10, "grey", "HOLD"),
    (1.0999999, "distress", "SELL"),
    (2.60, "grey", "HOLD"),
    (2.6000001, "safe", "HOLD"),
    (math.nextafter(1.10, -math.inf), "distress", "SELL"),
    (math.nextafter(1.10, math.inf), "grey", "HOLD"),
    (math.nextafter(2.60, math.inf), "safe", "HOLD"),
    (math.nextafter(2.60, -math.inf), "grey", "HOLD"),
    (-5.0, "distress", "SELL"),
    (0.0, "distress", "SELL"),
    (50.0, "safe", "HOLD"),
])
def test_g7_boundaries(z, zone_, sig):
    assert az.zone(z) == zone_
    assert az.map_signal(z) == sig


def test_map_signal_never_buy_on_grid():
    for i in range(-2000, 2001):
        z = i / 250.0
        assert az.map_signal(z) in {"SELL", "HOLD"}
        assert (az.map_signal(z) == "SELL") == (az.zone(z) == "distress")


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

SECTORS = ["Industrials", "Energy", "Information Technology", "Financials", "Utilities",
           "Real Estate", None, ""]


def rand_money(rng, lo, hi, p_none=0.05):
    if rng.random() < p_none:
        return None
    if rng.random() < 0.03:
        return 0.0
    return rng.uniform(lo, hi)


def rand_input(rng, ticker="R", clean=False):
    ta = rng.uniform(10, 5000) if clean else rand_money(rng, -100, 5000)
    eq_hi = ta * 0.95 if (clean and ta) else 6000
    d = dict(
        ticker=ticker, market=rng.choice(["US", "TSX"]),
        sector="Industrials" if clean else rng.choice(SECTORS),
        eligible=True if clean else rng.choice([True, True, True, False, None]),
        total_assets=ta,
        current_assets=rng.uniform(0, 3000) if clean else rand_money(rng, 0, 3000),
        current_liabilities=rng.uniform(0, 3000) if clean else rand_money(rng, 0, 3000),
        retained_earnings=rng.uniform(-3000, 3000) if clean else rand_money(rng, -3000, 3000),
        ebit_ttm=rng.uniform(-500, 800) if clean else rand_money(rng, -500, 800),
        total_equity=rng.uniform(-2000, eq_hi) if clean else rand_money(rng, -2000, eq_hi),
        cfo_ttm=rand_money(rng, -300, 1500, p_none=0.2),
        total_debt=rand_money(rng, -10, 3000, p_none=0.2),
        flags=frozenset(rng.sample(["PIT_ESTIMATED", "FLOW_CCY_MISMATCH",
                                    "EBIT_PERIOD_MISMATCH"], rng.randint(0, 2))))
    return az.GInputs(**d)


def reference(i):
    """Spec §Formulas / §Signal and precedence, written independently."""
    ta, eq = i.total_assets, i.total_equity
    tl = ta - eq if ta is not None and eq is not None else None
    pos = ta is not None and ta > 0
    x1 = (i.current_assets - i.current_liabilities) / ta if pos and None not in (
        i.current_assets, i.current_liabilities) else None
    x2 = i.retained_earnings / ta if pos and i.retained_earnings is not None else None
    x3 = i.ebit_ttm / ta if pos and i.ebit_ttm is not None else None
    x4 = eq / tl if pos and tl is not None and tl > 0 else None
    z = None
    if None not in (x1, x2, x3, x4):
        z = az.COEF_X1 * x1 + az.COEF_X2 * x2 + az.COEF_X3 * x3 + az.COEF_X4 * x4
    c2d = i.cfo_ttm / i.total_debt if (i.cfo_ttm is not None and i.total_debt is not None
                                        and i.total_debt > 0) else None
    zone_ = None if z is None else ("distress" if z < 1.10 else "grey" if z <= 2.60 else "safe")
    flags = set(i.flags)
    if eq is not None and eq < 0:
        flags.add("NEG_EQUITY")
    reason = None
    if i.eligible is not True:
        sig = "NOT_ELIGIBLE"
    elif not i.sector or i.sector in {"Financials", "Utilities", "Real Estate"}:
        sig, reason = "NO_SIGNAL", "EXCLUDED_SECTOR"
    elif any(getattr(i, f) is None for f in REQUIRED):
        sig, reason = "NO_SIGNAL", "NO_DATA"
    elif ta <= 0:
        sig, reason = "NO_SIGNAL", "NON_POSITIVE_ASSETS"
    elif tl <= 0:
        sig, reason = "NO_SIGNAL", "NON_POSITIVE_LIABILITIES"
    else:
        sig = "SELL" if z < 1.10 else "HOLD"
        if zone_ == "distress":
            if c2d is None:
                flags.add("NO_OVERRIDE_DATA")
            elif c2d > 0.25:
                sig = "HOLD"
                flags.add("CASH_RICH_OVERRIDE")
    veto = True if sig == "SELL" else False if sig == "HOLD" else None
    return dict(signal=sig, no_signal_reason=reason, x1=x1, x2=x2, x3=x3, x4=x4, z=z,
                zone=zone_, total_liabilities=tl, cfo_to_debt=c2d, distress_veto=veto,
                flags=flags)


def test_property_matches_reference_bit_for_bit():
    rng = random.Random(1)
    for n in range(3000):
        i = rand_input(rng, f"R{n}", clean=n % 3 == 0)
        r, exp = az.evaluate(i), reference(i)
        for f in ("signal", "no_signal_reason", "zone", "distress_veto"):
            assert getattr(r, f) == exp[f], (n, f)
        for f in X_FIELDS:
            assert getattr(r, f) == exp[f], (n, f)  # exact: §Formulas evaluation order
        assert set(r.flags) == exp["flags"], n


def test_property_signal_implications():
    rng = random.Random(2)
    for n in range(3000):
        r = az.evaluate(rand_input(rng, f"R{n}", clean=n % 2 == 0))
        f = set(r.flags)
        assert r.signal in G_LABELS and r.signal != "BUY"
        assert not str(r.signal).startswith("SCREEN_")
        if r.signal == "SELL":
            assert r.zone == "distress" and "CASH_RICH_OVERRIDE" not in f
        assert (r.signal == "HOLD" and r.zone == "distress") == ("CASH_RICH_OVERRIDE" in f)
        if "NO_OVERRIDE_DATA" in f:
            assert r.signal == "SELL"
        assert (r.distress_veto is True) == (r.signal == "SELL")
        assert (r.distress_veto is False) == (r.signal == "HOLD")
        assert (r.distress_veto is None) == (r.signal in {"NO_SIGNAL", "NOT_ELIGIBLE"})
        assert (r.no_signal_reason is not None) == (r.signal == "NO_SIGNAL")
        assert r.no_signal_reason is None or r.no_signal_reason in REASONS
        assert (r.zone is None) == (r.z is None)


def test_property_never_buy_on_input_grid():
    vals = (-1000.0, -1.0, 0.0, 1.0, 500.0, 3000.0)
    rng = random.Random(3)
    for ca in vals:
        for cl in vals:
            for re_ in vals:
                for eb in vals:
                    i = make(current_assets=ca, current_liabilities=cl, retained_earnings=re_,
                             ebit_ttm=eb, total_equity=rng.choice(vals),
                             total_assets=rng.choice((-5.0, 0.0, 10.0, 2000.0)),
                             cfo_ttm=rng.choice((None, 0.0, 600.0)),
                             total_debt=rng.choice((None, 0.0, 1000.0)))
                    assert az.evaluate(i).signal in {"HOLD", "SELL", "NO_SIGNAL"}


def scale(i, k):
    return dataclasses.replace(i, **{f: (getattr(i, f) * k if getattr(i, f) is not None
                                         else None) for f in MONEY})


def test_property_scaling_invariance():
    rng = random.Random(4)
    for n in range(1500):
        i = rand_input(rng, f"R{n}", clean=n % 2 == 0)
        k = rng.choice([1e-3, 0.37, 2.0, 1e3, 1e6, rng.uniform(0.01, 100)])
        a, b = az.evaluate(i), az.evaluate(scale(i, k))
        for f in ("x1", "x2", "x3", "x4", "z", "cfo_to_debt"):
            va, vb = getattr(a, f), getattr(b, f)
            assert (va is None) == (vb is None), (n, f)
            if va is not None:
                assert vb == pytest.approx(va, rel=1e-9, abs=1e-12), (n, f)
        if a.z is not None and min(abs(a.z - 1.10), abs(a.z - 2.60)) < 1e-9:
            continue  # rounding may cross a boundary
        if a.cfo_to_debt is not None and abs(a.cfo_to_debt - 0.25) < 1e-9:
            continue
        assert (a.zone, a.signal, a.no_signal_reason, a.distress_veto) == \
            (b.zone, b.signal, b.no_signal_reason, b.distress_veto), n
        assert set(a.flags) == set(b.flags), n


def test_property_monotonicity():
    rng = random.Random(5)
    checked = 0
    for n in range(2000):
        i = rand_input(rng, f"R{n}", clean=True)
        base = az.evaluate(i).z
        if base is None:
            continue
        checked += 1
        for f, up in (("current_assets", True), ("retained_earnings", True),
                      ("ebit_ttm", True), ("current_liabilities", False)):
            bump = rng.uniform(0.001, 500)
            z2 = az.evaluate(dataclasses.replace(i, **{f: getattr(i, f) + bump})).z
            assert z2 is not None
            assert (z2 >= base) if up else (z2 <= base), (n, f)
    assert checked > 1000


def test_property_independence():
    rng = random.Random(6)
    for _ in range(200):
        inputs = [rand_input(rng, f"R{j:02d}", clean=rng.random() < 0.5)
                  for j in range(rng.randint(1, 15))]
        alone = {i.ticker: az.evaluate(i) for i in inputs}
        shuffled = inputs[:]
        rng.shuffle(shuffled)
        res = az.run_altman_z(shuffled)
        assert [r.ticker for r in res] == [i.ticker for i in shuffled]
        for r in res:
            same_result(r, alone[r.ticker])


# ---------------------------------------------------------------------------
# FROZEN_V2: Model G is not part of the freeze (spec header)
# ---------------------------------------------------------------------------


def test_frozen_versions_exclude_model_g():
    assert "altman_z/0.1.0" not in FROZEN_VERSIONS
    assert not any(str(v).startswith("altman_z") for v in FROZEN_VERSIONS)
    if HAVE_G:
        assert az.MODEL_VERSION not in FROZEN_VERSIONS


def test_frozen_params_have_no_model_g():
    assert "altman_z" not in FROZEN_V2
    assert "altman" not in canonical_json(FROZEN_V2).lower()


def test_frozen_hash_unchanged_with_model_g_imported():
    # the hash test in test_frozen_v2_spec.py stays as is; importing Model G must not move it
    assert FROZEN_SHA256 == FROZEN_SHA
    assert params_sha256(FROZEN_V2) == FROZEN_SHA
    from valuation.models.frozen import build_frozen
    assert params_sha256(build_frozen()) == FROZEN_SHA
