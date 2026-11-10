"""Spec tests for Model A, residual income value (docs/models/residual_income.md, v0.1.0;
updated for v0.2.0 where the spec changed: see `# v0.2.0` comments and
test_residual_income_v2_spec.py).

Written from the spec only (not from the implementation). Golden values are taken
from the spec's worked examples G1-G7 (all re-derived independently by QA with a
plain-Python implementation of §Formulas before being written here) or expressed
as exact arithmetic expressions, asserted with relative tolerance 1e-9.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from datetime import date

import pytest

import valuation.models.residual_income as ri_mod
from valuation.models.residual_income import (
    ERP,
    HORIZON,
    MODEL_VERSION,
    ROE_CAP,
    ROE_FLOOR,
    RIInputs,
    RIResult,
    clip_roe,
    evaluate,
    fade_path,
    justified_pb,
    map_signal,
    retention,
    ri_value,
    roe_raw,
)

REL = 1e-9
AS_OF = date(2026, 11, 9)
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
SPOT = "ROE_SPOT_EQUITY"

NUMERIC_FIELDS = (
    "roe_raw", "roe0", "r", "b", "eps_ttm", "b0", "pb", "v_ri", "mos", "v_ri_up", "mos_up",
    "v_ri_down", "mos_down", "justified_pb",
)
PER_SHARE_FIELDS = NUMERIC_FIELDS  # all are per-share, ratios or rates (currency-free or trading)


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def ref_ri(b0, roe0, r, b, horizon=10):
    """Independent re-implementation of §Formulas ri_value, used for cross-checks only."""
    B, V = b0, b0
    for t in range(1, horizon + 1):
        roe_t = r + (roe0 - r) * (horizon - t) / horizon
        V += (roe_t - r) * B / (1 + r) ** t
        B = B * (1 + roe_t * b)
    return V


# --- G3 base case ---------------------------------------------------------
BASE = dict(
    ticker="BANK", as_of=AS_OF, market="US", sector="Financials", eligible=True, rf=0.042,
    ni_common_ttm=13.0e9, common_equity_end=100.0e9, common_equity_start=92.0e9,
    shares_outstanding=2.0e9, fx_rate=1.0, bvps=50.0, dps_ttm=2.6, price=75.0,
)
BASE_ROE = 13 / 96
BASE_V = 58.567222546400856
BASE_V_UP = 56.408233106641376
BASE_V_DOWN = 60.85172204359812
BASE_JPB = 1.8349358974358971

# --- G4 FX, USD reporter on TSX (Materials) -------------------------------
G4 = dict(
    ticker="MAT.TO", as_of=AS_OF, market="TSX", sector="Materials", eligible=True, rf=0.032,
    ni_common_ttm=1.0e9, common_equity_end=10.0e9, common_equity_start=9.0e9,
    shares_outstanding=5.0e8, fx_rate=1.40, bvps=28.0, dps_ttm=1.12, price=30.0,
    filed_estimated=True, roe_history=(),
)

# --- G6 insurer CSM -------------------------------------------------------
G6 = dict(
    ticker="INS.TO", as_of=AS_OF, market="TSX", sector="Financials", eligible=True, rf=0.032,
    ni_common_ttm=6.0e9, common_equity_end=44.2e9, common_equity_start=42.0e9,
    shares_outstanding=1.7e9, fx_rate=1.0, bvps=26.0, dps_ttm=1.70, price=45.0,
    filed_estimated=True, csm_relevant=True, csm_after_tax=19.55e9,
)


def make(base=None, **overrides) -> RIInputs:
    kwargs = dict(BASE if base is None else base)
    kwargs.update(overrides)
    return RIInputs(**kwargs)


def run(base=None, **overrides) -> RIResult:
    return evaluate(make(base, **overrides))


def assert_values_equal(a: RIResult, b: RIResult, fields=NUMERIC_FIELDS):
    for f in fields:
        va, vb = getattr(a, f), getattr(b, f)
        if va is None or vb is None:
            assert va is None and vb is None, f
        else:
            assert va == approx(vb), f


# ---------------------------------------------------------------------------
# Constants and API contract
# ---------------------------------------------------------------------------


def test_model_version():
    assert MODEL_VERSION == "residual_income/0.2.0"  # v0.2.0: revision round v2


def test_exported_constants():
    assert ERP == 0.05
    assert HORIZON == 10
    assert ROE_FLOOR == -0.10
    assert ROE_CAP == 0.35


@pytest.mark.parametrize("name,value", [
    ("RETENTION_DEFAULT", 0.6), ("G_CAP", 0.04), ("R_SHIFT", 0.01), ("BUY_MOS", 0.25),
    ("SELL_MOS", -0.20), ("PB_MAX", 8.0), ("PB_MAX_ASSET_LIGHT", 3.0),
])
def test_other_constants(name, value):
    # Named in §Constants (not in the API import list): checked as module attributes.
    assert getattr(ri_mod, name) == value


def test_sector_sets():
    assert set(ri_mod.BOOK_SECTORS) == {
        "Financials", "Utilities", "Real Estate", "Energy", "Materials"}
    assert set(ri_mod.CYCLICAL_SECTORS) == {"Energy", "Materials"}
    assert set(ri_mod.ASSET_LIGHT_SECTORS) == {
        "Information Technology", "Communication Services", "Health Care", "Industrials",
        "Consumer Discretionary", "Consumer Staples"}


def test_inputs_defaults():
    inp = RIInputs(
        ticker="X", as_of=AS_OF, price=10.0, bvps=5.0, sector="Financials", eligible=True,
        rf=0.04, ni_common_ttm=1.0, common_equity_end=10.0, common_equity_start=9.0,
        shares_outstanding=2.0, market="US",
    )
    assert inp.fx_rate == 1.0
    assert inp.dps_ttm == 0.0
    assert inp.ttm_flags == frozenset()
    assert inp.data_flags == frozenset()
    assert inp.filed_estimated is False
    assert tuple(inp.roe_history) == ()
    assert inp.csm_relevant is False
    assert inp.csm_after_tax is None
    assert inp.ni_currency is None
    assert inp.reporting_currency is None


def test_result_is_frozen_and_has_contract_fields():
    r = run()
    names = {f.name for f in dataclasses.fields(r)}
    assert names >= {
        "ticker", "as_of", "market", "sector", "price", "signal", "no_signal_reason",
        "roe_raw", "roe0", "roe_basis", "r", "b", "eps_ttm", "b0", "pb", "v_ri", "mos",
        "v_ri_up", "mos_up", "v_ri_down", "mos_down", "justified_pb", "flags",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.signal = "BUY"
    assert isinstance(r.flags, frozenset)
    assert (r.ticker, r.as_of, r.market, r.sector) == ("BANK", AS_OF, "US", "Financials")
    assert r.price == approx(75.0)


# ---------------------------------------------------------------------------
# G1 proposal example
# ---------------------------------------------------------------------------

G1_FADE = [0.1345, 0.129, 0.1235, 0.118, 0.1125, 0.107, 0.1015, 0.096, 0.0905, 0.085]
G1_B_PREV = [50, 54.035, 58.217309, 62.53121160, 66.95842138, 71.47811482, 76.06700979,
             80.69949069, 85.34778135, 89.98216588]
G1_PV = [2.281105991, 2.019613923, 1.754783903, 1.488990170, 1.224585771, 0.9638675104,
         0.7090422612, 0.4621953517, 0.2252616608, 0.0]


def test_g1_fade_path():
    path = fade_path(0.14, 0.085)
    assert len(path) == 10
    for got, exp in zip(path, G1_FADE):
        assert got == approx(exp)
    assert path[-1] == approx(0.085)


def test_g1_fade_path_horizon_argument():
    path = fade_path(0.14, 0.085, horizon=4)
    assert len(path) == 4
    for t, got in enumerate(path, 1):
        assert got == approx(0.085 + (0.14 - 0.085) * (4 - t) / 4)


def test_g1_year_table():
    """Rebuild the year table from the model's fade path; must match the printed table."""
    B, total = 50.0, 50.0
    for t, roe_t in enumerate(fade_path(0.14, 0.085), 1):
        assert B == pytest.approx(G1_B_PREV[t - 1], rel=1e-9)
        pv = (roe_t - 0.085) * B / 1.085**t
        assert pv == pytest.approx(G1_PV[t - 1], rel=1e-9, abs=1e-12)
        total += pv
        B *= 1 + roe_t * 0.6
    assert ri_value(50, 0.14, 0.085, 0.6) == approx(total)
    assert 50 + sum(G1_PV) == pytest.approx(61.12944654266581, rel=1e-9)


@pytest.mark.parametrize("r,expected", [
    (0.085, 61.12944654266581), (0.095, 58.845620190495296), (0.075, 63.54734542151645),
])
def test_g1_ri_value(r, expected):
    assert ri_value(50, 0.14, r, 0.6) == approx(expected)
    assert ri_value(50, 0.14, r, 0.6, horizon=10) == approx(expected)


def test_g1_justified_pb():
    assert justified_pb(0.14, 0.085, 0.6) == approx((0.14 - 0.04) / (0.085 - 0.04))
    assert justified_pb(0.14, 0.085, 0.6) == approx(2.2222222222222222)


@pytest.mark.parametrize("price,mos,mos_up,mos_down,signal", [
    (60, 0.0188241090444, -0.0192396634917, 0.0591224236919, "HOLD"),
    (48, 0.273530136306, 0.225950420635, None, "BUY"),
    (80, -0.235881918217, None, -0.205658182231, "SELL"),
])
def test_g1_mos_and_signal(price, mos, mos_up, mos_down, signal):
    v = ri_value(50, 0.14, 0.085, 0.6)
    vu = ri_value(50, 0.14, 0.095, 0.6)
    vd = ri_value(50, 0.14, 0.075, 0.6)
    m, mu, md = v / price - 1, vu / price - 1, vd / price - 1
    # The spec prints 12 significant figures here.
    assert m == pytest.approx(mos, rel=1e-10)
    if mos_up is not None:
        assert mu == pytest.approx(mos_up, rel=1e-10)
    if mos_down is not None:
        assert md == pytest.approx(mos_down, rel=1e-10)
    assert map_signal(m, mu, md) == signal


# ---------------------------------------------------------------------------
# G2 function edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("x,expected", [
    (0.50, (0.35, True)), (-0.30, (-0.10, True)), (0.35, (0.35, False)),
    (-0.10, (-0.10, False)), (0.12, (0.12, False)), (0.0, (0.0, False)),
])
def test_clip_roe(x, expected):
    val, clipped = clip_roe(x)
    assert val == approx(expected[0])
    assert bool(clipped) is expected[1]


@pytest.mark.parametrize("dps,eps,expected", [
    (1.0, -0.5, 0.6), (1.0, 0.0, 0.6), (3.0, 2.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 1.0),
    (0.8, 2.0, 1 - 0.8 / 2.0), (0.0, -1.0, 0.6),
])
def test_retention(dps, eps, expected):
    assert retention(dps, eps) == approx(expected)


def test_ri_value_edges():
    assert ri_value(50, 0.085, 0.085, 0.6) == approx(50)
    assert ri_value(50, 0.14, 0.085, 0.6, horizon=1) == approx(50)
    assert ri_value(50, 0.14, 0.085, 0.0) == approx(59.320679683271095)
    assert ri_value(50, 0.14, 0.085, 1.0) == approx(62.58698765156571)


def test_ri_value_matches_reference_loop():
    for b0, roe0, r, b in [(50, 0.14, 0.085, 0.6), (12.3, -0.07, 0.1, 0.2), (80, 0.3, 0.06, 1.0)]:
        assert ri_value(b0, roe0, r, b) == approx(ref_ri(b0, roe0, r, b))
        assert ri_value(b0, roe0, r, b, horizon=5) == approx(ref_ri(b0, roe0, r, b, 5))


def test_justified_pb_edges():
    assert justified_pb(0.14, 0.03, 0.6) is None  # r < g
    assert justified_pb(0.14, 0.04, 0.6) is None  # r = g (g capped at 0.04)
    assert justified_pb(0.14, 0.085, 0.0) == approx(0.14 / 0.085)
    # uncapped g: 0.5 * 0.06 = 0.03
    assert justified_pb(0.06, 0.09, 0.5) == approx((0.06 - 0.03) / (0.09 - 0.03))
    # may be negative
    assert justified_pb(-0.10, 0.092, 0.6) == approx((-0.10 + 0.06) / (0.092 + 0.06))


def test_roe_raw_edges():
    val, flags = roe_raw(10, 100, None)
    assert val == approx(0.1)
    assert set(flags) == {SPOT}
    assert roe_raw(10, 100) == roe_raw(10, 100, None)  # ce_start defaults to None
    val, flags = roe_raw(10, 100, -120)
    assert val is None and set(flags) == set()
    val, flags = roe_raw(None, 100, 90)
    assert val is None and set(flags) == set()
    val, flags = roe_raw(10, None, 90)
    assert val is None and set(flags) == set()
    val, flags = roe_raw(10, 100, 90)
    assert val == approx(10 / 95) and set(flags) == set()
    # avg exactly 0 -> None; spot equity <= 0 -> None but keeps the flag
    val, flags = roe_raw(10, 100, -100)
    assert val is None and set(flags) == set()
    val, flags = roe_raw(10, -5, None)
    assert val is None and set(flags) == {SPOT}


@pytest.mark.parametrize("args,expected", [
    ((0.25, 0.01, 0.4), "BUY"),
    ((0.25, 0.0, 0.4), "HOLD"),
    ((0.2499, 0.2, 0.3), "HOLD"),
    ((-0.20, -0.3, -0.01), "SELL"),
    ((-0.20, -0.3, 0.0), "HOLD"),
    ((-0.21, -0.25, 0.01), "HOLD"),  # non-robust SELL
    ((0.30, -0.01, 0.6), "HOLD"),  # non-robust BUY
    ((-0.1999, -0.3, -0.1), "HOLD"),
    ((0.0, -0.05, 0.05), "HOLD"),
])
def test_map_signal(args, expected):
    assert map_signal(*args) == expected


# ---------------------------------------------------------------------------
# G3 end-to-end, US bank
# ---------------------------------------------------------------------------


def test_g3_base():
    r = run()
    assert r.signal == "SELL"
    assert r.no_signal_reason is None
    assert r.roe_raw == approx(BASE_ROE)
    assert r.roe0 == approx(0.13541666666666666)
    assert r.roe_basis == "ttm"
    assert r.r == approx(0.092)
    assert r.eps_ttm == approx(6.5)
    assert r.b == approx(1 - 2.6 / 6.5)
    assert r.b0 == approx(50.0)
    assert r.pb == approx(1.5)
    assert r.v_ri == approx(BASE_V)
    assert r.v_ri_up == approx(BASE_V_UP)
    assert r.v_ri_down == approx(BASE_V_DOWN)
    assert r.mos == approx(-0.2191036993813219)
    assert r.mos_up == approx(-0.24789022524478166)
    assert r.mos_down == approx(-0.1886437060853584)
    assert r.justified_pb == approx(BASE_JPB)
    assert r.flags == frozenset({"SELL_ZONE"})  # v0.2.0: model SELL carries SELL_ZONE


def test_g3_price_45_buy():
    r = run(price=45.0)
    assert r.mos == approx(0.30149383436446353)
    assert r.mos_up == approx(0.25351629125869724)
    assert r.v_ri == approx(BASE_V)
    assert r.pb == approx(0.9)
    assert r.signal == "BUY"


def test_g3_price_52_hold():
    r = run(price=52.0)
    assert r.mos == approx(0.12629274127693946)
    assert r.signal == "HOLD"


def test_g3_spot_equity():
    r = run(common_equity_start=None)
    assert r.roe_raw == approx(0.13)
    assert r.roe0 == approx(0.13)
    assert r.v_ri == approx(57.455952111741375)
    assert r.mos == approx(-0.23392063851011502)
    assert r.mos_down == approx(-0.20406076860388633)
    assert r.signal == "SELL"
    assert r.flags == frozenset({SPOT, "SELL_ZONE"})  # v0.2.0: model SELL carries SELL_ZONE


def test_g3_clip_high():
    r = run(ni_common_ttm=48.0e9)
    assert r.roe_raw == approx(0.5)
    assert r.roe0 == approx(0.35)
    assert r.eps_ttm == approx(24.0)
    assert r.b == approx(1 - 2.6 / 24)
    assert r.v_ri == approx(128.46557945890916)
    assert r.mos == approx(0.7128743927854555)
    assert r.signal == "HOLD"  # v0.2.0: BUY on a clipped ROE is blocked
    assert r.flags == frozenset({"ROE_CLIPPED", "BUY_BLOCKED_CLIP"})  # v0.2.0: R2


def test_g3_clip_low():
    r = run(ni_common_ttm=-28.8e9)
    assert r.roe_raw == approx(-0.3)
    assert r.roe0 == approx(-0.10)
    assert r.eps_ttm == approx(-14.4)
    assert r.b == approx(0.6)
    assert r.v_ri == approx(20.257657687590946)
    assert r.justified_pb == approx(-0.26315789473684215)
    assert r.signal == "SELL"
    assert r.flags == frozenset({"ROE_CLIPPED", "SELL_ZONE"})  # v0.2.0: SELL_ZONE on model SELL


def test_g3_loss_without_clip():
    r = run(ni_common_ttm=-2.0e9)
    assert r.roe0 == approx(-0.020833333333333332)
    assert r.eps_ttm == approx(-1.0)
    assert r.b == approx(0.6)
    assert r.v_ri == approx(31.060323406323064)
    assert r.signal == "SELL"
    assert r.flags == frozenset({"SELL_ZONE"})  # v0.2.0: SELL_ZONE on model SELL


def test_sensitivity_reruns_with_shifted_fade_target():
    """r +- 1pp re-runs ri_value with the shifted r everywhere (fade and discounting)."""
    r = run()
    assert r.v_ri_up == approx(ri_value(r.b0, r.roe0, r.r + 0.01, r.b))
    assert r.v_ri_down == approx(ri_value(r.b0, r.roe0, r.r - 0.01, r.b))
    assert r.v_ri_up == approx(ref_ri(50.0, BASE_ROE, 0.102, 0.6))
    assert r.v_ri_down == approx(ref_ri(50.0, BASE_ROE, 0.082, 0.6))

    # A wrong variant that fades to the base r but discounts at r+1pp must differ.
    def discount_only(b0, roe0, r, rd, b):
        B, V = b0, b0
        for t in range(1, 11):
            roe_t = r + (roe0 - r) * (10 - t) / 10
            V += (roe_t - rd) * B / (1 + rd) ** t
            B *= 1 + roe_t * b
        return V

    assert r.v_ri_up != approx(discount_only(50.0, BASE_ROE, 0.092, 0.102, 0.6))
    assert r.mos_up == approx(r.v_ri_up / 75.0 - 1)
    assert r.mos_down == approx(r.v_ri_down / 75.0 - 1)


# ---------------------------------------------------------------------------
# G4 FX and G5 cyclical average
# ---------------------------------------------------------------------------


def test_g4_fx_usd_reporter_on_tsx():
    r = run(G4)
    assert r.eps_ttm == approx(1e9 / 5e8 * 1.4)
    assert r.eps_ttm == approx(2.8)
    assert r.b == approx(1 - 1.12 / 2.8)
    assert r.roe_raw == approx(1 / 9.5)
    assert r.roe0 == approx(0.10526315789473684)
    assert r.roe_basis == "ttm"
    assert r.r == approx(0.082)
    assert r.b0 == approx(28.0)
    assert r.pb == approx(30.0 / 28.0)
    assert r.v_ri == approx(30.56336853495171)
    assert r.mos == approx(0.0187789511650569)
    assert r.mos_up == approx(-0.01932920160717344)
    assert r.signal == "HOLD"
    assert r.justified_pb == approx(1.553884711779448)
    assert r.flags == frozenset({"CYCLICAL_ROE", "PIT_ESTIMATED"})


def test_g5_cyclical_three_year_average():
    r = run(G4, roe_history=(0.06, 0.02))
    assert r.roe_basis == "3y_avg"
    assert r.roe0 == approx((1 / 9.5 + 0.06 + 0.02) / 3)
    assert r.roe0 == approx(0.06175438596491228)
    assert r.roe_raw == approx(1 / 9.5)  # unchanged
    assert r.v_ri == approx(25.869054656427853)
    assert r.mos == approx(-0.1376981781190716)
    assert r.signal == "HOLD"
    assert r.flags == frozenset({"CYCLICAL_ROE", "PIT_ESTIMATED"})


def test_g5_three_year_average_uses_only_two_most_recent():
    r = run(G4, roe_history=(0.06, 0.02, 0.30, None))
    assert r.roe0 == approx((1 / 9.5 + 0.06 + 0.02) / 3)
    assert r.roe_basis == "3y_avg"


def test_g5_clip_after_average():
    r = run(G4, roe_history=(0.50, 0.45))
    assert r.roe_basis == "3y_avg"
    assert r.roe0 == approx(0.35)
    assert r.roe_raw == approx(1 / 9.5)
    assert r.flags >= {"ROE_CLIPPED", "CYCLICAL_ROE"}
    assert r.v_ri == approx(66.4010543305594)
    assert r.signal == "HOLD"  # v0.2.0: BUY on a clipped ROE is blocked
    assert "BUY_BLOCKED_CLIP" in r.flags  # v0.2.0: R2


def test_g5_no_clip_flag_when_raw_would_clip_but_average_does_not():
    # ROE_raw 0.5 alone would clip; the average (0.5 + 0.1 + 0.1)/3 does not.
    r = run(G4, ni_common_ttm=4.75e9, roe_history=(0.1, 0.1))
    assert r.roe_raw == approx(0.5)
    assert r.roe0 == approx((0.5 + 0.1 + 0.1) / 3)
    assert "ROE_CLIPPED" not in r.flags


@pytest.mark.parametrize("hist", [(0.06,), (0.06, None), (None, 0.02), ()])
def test_g5_incomplete_history_falls_back_to_ttm(hist):
    r = run(G4, roe_history=hist)
    g4 = run(G4)
    assert r.roe_basis == "ttm"
    assert r.signal == g4.signal == "HOLD"
    assert r.flags == g4.flags
    assert_values_equal(r, g4)


def test_history_ignored_outside_cyclical_sectors():
    r = run(roe_history=(0.30, 0.30))
    base = run()
    assert r.roe_basis == "ttm"
    assert_values_equal(r, base)
    assert r.signal == "SELL"


def test_cyclical_flag_energy_whatever_basis():
    r = run(sector="Energy")
    assert "CYCLICAL_ROE" in r.flags
    assert r.roe_basis == "ttm"
    r = run(sector="Energy", roe_history=(0.1, 0.1))
    assert "CYCLICAL_ROE" in r.flags
    assert r.roe_basis == "3y_avg"


# ---------------------------------------------------------------------------
# G6 insurer CSM
# ---------------------------------------------------------------------------


def test_g6_csm_adjusted():
    r = run(G6)
    assert r.b0 == approx((44.2e9 + 19.55e9) / 1.7e9)
    assert r.b0 == approx(37.5)
    assert r.pb == approx(1.2)
    assert r.roe_raw == approx(6.0 / (43.1 + 19.55))
    assert r.roe_raw == approx(0.09577015163607343)
    assert r.roe0 == approx(0.09577015163607343)
    assert r.eps_ttm == approx(3.5294117647058822)
    assert r.b == approx(1 - 1.70 / (6 / 1.7))
    assert r.r == approx(0.082)
    assert r.v_ri == approx(39.47586691199252)
    assert r.mos == approx(-0.12275851306683294)
    assert r.signal == "HOLD"
    assert r.flags == frozenset({"CSM_ADJUSTED", "PIT_ESTIMATED"})


def test_g6_csm_with_fx():
    r = run(G6, fx_rate=1.25, price=45.0 * 1.25)
    assert r.b0 == approx((44.2e9 + 19.55e9) / 1.7e9 * 1.25)


def test_g6_csm_spot_equity():
    r = run(G6, common_equity_start=None)
    assert r.roe_raw == approx(6.0e9 / (44.2e9 + 19.55e9))
    assert r.flags >= {"CSM_ADJUSTED", SPOT}


def test_g6_csm_missing_needs_input():
    r = run(G6, csm_after_tax=None)
    assert r.signal == "NO_SIGNAL"
    assert r.no_signal_reason == "NEEDS_CSM_INPUT"
    for f in ("b0", "pb", "roe_raw", "roe0", "v_ri", "mos", "v_ri_up", "v_ri_down"):
        assert getattr(r, f) is None, f
    assert r.eps_ttm == approx(3.5294117647058822)
    assert r.b == approx(1 - 1.70 / (6 / 1.7))
    assert "CSM_ADJUSTED" not in r.flags
    assert "PIT_ESTIMATED" in r.flags


def test_csm_not_relevant_uses_bvps():
    r = run(G6, csm_relevant=False, csm_after_tax=None)
    assert r.b0 == approx(26.0)
    assert r.roe_raw == approx(6.0 / 43.1)
    assert "CSM_ADJUSTED" not in r.flags
    assert r.no_signal_reason is None


# ---------------------------------------------------------------------------
# G7 reasons and boundaries
# ---------------------------------------------------------------------------


def test_g7_neg_book():
    r = run(bvps=-1.0)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_BOOK")
    for f in ("pb", "v_ri", "mos", "v_ri_up", "mos_up", "v_ri_down", "mos_down"):
        assert getattr(r, f) is None, f
    assert r.b0 == approx(-1.0)
    assert r.roe0 == approx(BASE_ROE)
    assert r.b == approx(0.6)
    assert r.justified_pb == approx(BASE_JPB)


def test_zero_book_is_neg_book():
    r = run(bvps=0.0)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_BOOK")
    assert r.pb is None and r.v_ri is None


def test_g7_pb_exactly_8_passes():
    r = run(price=400.0)
    assert r.pb == approx(8.0)
    assert r.signal == "SELL"
    assert r.no_signal_reason is None


def test_g7_pb_too_high():
    r = run(price=400.01)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "PB_TOO_HIGH")
    assert r.v_ri == approx(BASE_V)
    assert r.mos == approx(-0.853585603993898)
    assert r.mos_up is not None and r.mos_down is not None


def test_g7_asset_light_pb_exactly_3_passes():
    r = run(sector="Health Care", price=150.0)
    assert r.pb == approx(3.0)
    assert r.signal == "SELL"


@pytest.mark.parametrize("sector", ["Health Care", "Industrials", "Information Technology",
                                    "Communication Services", "Consumer Discretionary",
                                    "Consumer Staples"])
def test_g7_asset_light(sector):
    r = run(sector=sector, price=150.5)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "ASSET_LIGHT")
    assert r.v_ri == approx(BASE_V)
    assert r.mos == approx(BASE_V / 150.5 - 1)


@pytest.mark.parametrize("sector", ["Financials", "Utilities", "Real Estate", "Energy",
                                    "Materials"])
def test_book_sector_not_asset_light(sector):
    r = run(sector=sector, price=150.5)  # pb 3.01 is fine for a book sector
    assert r.no_signal_reason is None
    assert r.signal in {"BUY", "HOLD", "SELL"}


def test_g7_no_ttm():
    r = run(ni_common_ttm=None, ttm_flags=frozenset({"NO_TTM"}))
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_TTM")
    assert r.eps_ttm is None and r.b is None and r.roe0 is None and r.roe_raw is None
    assert r.pb == approx(1.5)
    assert r.v_ri is None and r.mos is None
    assert "NO_TTM" in r.flags


def test_no_ttm_flag_added_even_if_not_in_ttm_flags():
    r = run(ni_common_ttm=None)
    assert "NO_TTM" in r.flags
    assert r.no_signal_reason == "NO_TTM"


def test_g7_no_equity():
    r = run(common_equity_end=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_EQUITY")
    assert r.roe_raw is None and r.roe0 is None and r.v_ri is None
    assert r.eps_ttm == approx(6.5)
    assert r.pb == approx(1.5)


def test_g7_neg_avg_equity():
    r = run(common_equity_start=-120.0e9)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_AVG_EQUITY")
    assert r.roe_raw is None and r.roe0 is None and r.v_ri is None
    assert SPOT not in r.flags


def test_g7_no_rf():
    r = run(rf=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_RF")
    assert r.roe0 == approx(BASE_ROE)
    assert r.b == approx(0.6)
    assert r.r is None and r.v_ri is None and r.mos is None
    assert r.justified_pb is None
    assert r.pb == approx(1.5)


@pytest.mark.parametrize("sector", [None, "", "Banks", "financials", "Unknown"])
def test_g7_no_sector(sector):
    r = run(sector=sector)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_SECTOR")
    assert_values_equal(r, run())  # values as BASE
    assert r.v_ri == approx(BASE_V)


def test_g7_ccy_mismatch():
    r = run(ni_currency="CAD", reporting_currency="USD")
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "CCY_MISMATCH")
    assert r.eps_ttm is None and r.b is None and r.roe_raw is None and r.roe0 is None
    assert r.pb == approx(1.5)


@pytest.mark.parametrize("ni_ccy,rep_ccy", [("USD", "USD"), ("USD", None), (None, "USD")])
def test_ccy_no_mismatch(ni_ccy, rep_ccy):
    r = run(ni_currency=ni_ccy, reporting_currency=rep_ccy)
    assert r.signal == "SELL"
    assert r.v_ri == approx(BASE_V)


def test_g7_eligible_none_is_not_eligible():
    r = run(eligible=None)
    assert r.signal == "NOT_ELIGIBLE"
    assert r.no_signal_reason is None
    assert_values_equal(r, run())


def test_eligible_false_is_not_eligible():
    r = run(eligible=False)
    assert r.signal == "NOT_ELIGIBLE"
    assert r.no_signal_reason is None
    assert r.v_ri == approx(BASE_V)
    assert r.mos == approx(-0.2191036993813219)


def test_g7_not_eligible_beats_neg_book():
    r = run(eligible=False, bvps=-1.0)
    assert r.signal == "NOT_ELIGIBLE"
    assert r.no_signal_reason is None


def test_g7_fair_value_book():
    r = run(sector="Real Estate", market="TSX")
    assert r.signal == "SELL"
    assert r.flags == frozenset({"FAIR_VALUE_BOOK", "SELL_ZONE"})  # v0.2.0: SELL_ZONE


def test_real_estate_us_no_fair_value_flag():
    r = run(sector="Real Estate", market="US")
    assert "FAIR_VALUE_BOOK" not in r.flags


# ---------------------------------------------------------------------------
# Precedence: every pair of consecutive reasons, and all at once
# ---------------------------------------------------------------------------

COND = {
    "NO_SECTOR": dict(sector=None),
    "NEEDS_CSM_INPUT": dict(csm_relevant=True, csm_after_tax=None),
    "NEG_BOOK": dict(bvps=-1.0),
    "PB_TOO_HIGH": dict(price=401.0),
    "ASSET_LIGHT": dict(sector="Health Care", price=151.0),
    "CCY_MISMATCH": dict(ni_currency="CAD", reporting_currency="USD"),
    "NO_TTM": dict(ni_common_ttm=None),
    "NO_EQUITY": dict(common_equity_end=None),
    "NEG_AVG_EQUITY": dict(common_equity_start=-120.0e9),
    "NO_RF": dict(rf=None),
}


@pytest.mark.parametrize("reason", list(COND))
def test_each_reason_alone(reason):
    r = run(**COND[reason])
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", reason)


@pytest.mark.parametrize("first,second", [
    ("NO_SECTOR", "NEEDS_CSM_INPUT"),
    ("NO_SECTOR", "NEG_BOOK"),
    ("NEEDS_CSM_INPUT", "NEG_BOOK"),
    ("NEEDS_CSM_INPUT", "PB_TOO_HIGH"),
    ("NEG_BOOK", "CCY_MISMATCH"),
    ("NEG_BOOK", "NO_RF"),
    ("PB_TOO_HIGH", "ASSET_LIGHT"),
    ("PB_TOO_HIGH", "NO_TTM"),
    ("ASSET_LIGHT", "CCY_MISMATCH"),
    ("CCY_MISMATCH", "NO_TTM"),
    ("CCY_MISMATCH", "NO_RF"),
    ("NO_TTM", "NO_EQUITY"),
    ("NO_EQUITY", "NEG_AVG_EQUITY"),
    ("NO_EQUITY", "NO_RF"),
    ("NEG_AVG_EQUITY", "NO_RF"),
])
def test_precedence_pairs(first, second):
    kw = {**COND[second], **COND[first]}  # first's settings win on a shared key (sector)
    r = run(**kw)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", first)


def test_precedence_all_conditions():
    kw = {}
    for name in ["NO_RF", "NEG_AVG_EQUITY", "NO_EQUITY", "NO_TTM", "CCY_MISMATCH",
                 "PB_TOO_HIGH", "NEG_BOOK", "NEEDS_CSM_INPUT", "NO_SECTOR"]:
        kw.update(COND[name])
    r = run(**kw)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_SECTOR")
    r = run(eligible=False, **kw)
    assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)
    r = run(eligible=None, **kw)
    assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)


def test_csm_applies_but_ce_end_missing_is_neg_book():
    """Literal reading: CSM applies -> B_0 needs CE_end; B_0 None -> NEG_BOOK (#3) wins."""
    r = run(G6, common_equity_end=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_BOOK")


# ---------------------------------------------------------------------------
# Outputs still computed on NOT_ELIGIBLE / NO_SIGNAL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    dict(price=400.01),  # PB_TOO_HIGH
    dict(sector="Health Care", price=150.5),  # ASSET_LIGHT
    dict(sector=None),  # NO_SECTOR
    dict(eligible=False),  # NOT_ELIGIBLE
])
def test_outputs_kept_on_withheld_signal(overrides):
    r = run(**overrides)
    assert r.signal in {"NO_SIGNAL", "NOT_ELIGIBLE"}
    for f in ("roe_raw", "roe0", "r", "b", "eps_ttm", "b0", "pb", "v_ri", "mos", "v_ri_up",
              "mos_up", "v_ri_down", "mos_down", "justified_pb"):
        assert getattr(r, f) is not None, f
    assert r.v_ri == approx(BASE_V)
    assert r.mos == approx(BASE_V / r.price - 1)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_flag_pit_estimated():
    # v0.2.0: BASE is a model SELL, so SELL_ZONE is set too
    assert run(filed_estimated=True).flags == frozenset({"PIT_ESTIMATED", "SELL_ZONE"})


def test_flags_pass_through():
    r = run(ttm_flags=frozenset({"STALE_TTM"}), data_flags=frozenset({"ROE_PERIOD_MISMATCH"}))
    # v0.2.0: BASE is a model SELL, so SELL_ZONE is set too
    assert r.flags == frozenset({"STALE_TTM", "ROE_PERIOD_MISMATCH", "SELL_ZONE"})
    assert r.signal == "SELL"


def test_flags_union():
    r = run(G6, common_equity_start=None, ni_common_ttm=60e9,
            ttm_flags=frozenset({"STALE_TTM"}), data_flags=frozenset({"X_FLAG"}))
    # v0.2.0: ROE clipped at 0.35 maps to BUY (mos 1.297) -> blocked: BUY_BLOCKED_CLIP
    assert r.flags == frozenset({"CSM_ADJUSTED", "PIT_ESTIMATED", SPOT, "ROE_CLIPPED",
                                 "STALE_TTM", "X_FLAG", "BUY_BLOCKED_CLIP"})


@pytest.mark.parametrize("flag_overrides", [
    dict(filed_estimated=True),
    dict(ttm_flags=frozenset({"STALE_TTM"})),
    dict(data_flags=frozenset({"ROE_PERIOD_MISMATCH"})),
])
@pytest.mark.parametrize("price", [45.0, 52.0, 75.0, 400.01])
def test_flags_never_change_signal_or_numbers(flag_overrides, price):
    a = run(price=price)
    b = run(price=price, **flag_overrides)
    assert a.signal == b.signal
    assert a.no_signal_reason == b.no_signal_reason
    assert_values_equal(a, b)
    assert b.flags > a.flags


def test_flag_only_condition_removed_g4():
    a = run(G4)
    b = run(G4, filed_estimated=False)
    assert a.signal == b.signal
    assert_values_equal(a, b)
    assert a.flags - b.flags == {"PIT_ESTIMATED"}


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

LABEL_CASES = [
    {}, dict(price=45.0), dict(price=52.0), dict(eligible=False), dict(bvps=-1.0),
    dict(ni_common_ttm=None), dict(sector=None), dict(price=400.01),
    dict(ni_common_ttm=48.0e9), dict(ni_common_ttm=-28.8e9),
]


@pytest.mark.parametrize("overrides", LABEL_CASES)
def test_labels_exact_and_never_screen_prefixed(overrides):
    r = run(**overrides)
    assert r.signal in LABELS
    assert not r.signal.startswith("SCREEN_")
    assert r.no_signal_reason is None or not r.no_signal_reason.startswith("SCREEN_")
    assert (r.no_signal_reason is not None) == (r.signal == "NO_SIGNAL")


def test_map_signal_labels():
    grid = [-0.5, -0.2, -0.1, 0.0, 0.1, 0.25, 0.5]
    for args in itertools.product(grid, repeat=3):
        assert map_signal(*args) in {"BUY", "HOLD", "SELL"}


# ---------------------------------------------------------------------------
# ValueError cases from the Inputs table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    dict(price=0.0), dict(price=-1.0),
    dict(shares_outstanding=0.0), dict(shares_outstanding=-2.0e9),
    dict(fx_rate=0.0), dict(fx_rate=-1.4),
    dict(market="NYSE"), dict(market="tsx"), dict(market=""),
])
def test_value_errors(overrides):
    with pytest.raises(ValueError):
        evaluate(make(**overrides))


# ---------------------------------------------------------------------------
# Property expectations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("k", [0.5, 3.0, 1e6])
def test_property_scaling_in_b0(k):
    for roe0, r, b in [(0.14, 0.085, 0.6), (-0.05, 0.1, 0.3), (0.35, 0.03, 1.0)]:
        assert ri_value(k * 50, roe0, r, b) == approx(k * ri_value(50, roe0, r, b))


@pytest.mark.parametrize("r", [0.03, 0.05, 0.085, 0.12, 0.15])
def test_property_roe_equals_r_gives_book(r):
    for b in (0.0, 0.5, 1.0):
        assert ri_value(37.0, r, r, b) == approx(37.0)
    assert fade_path(0.2, r)[-1] == approx(r)
    assert fade_path(-0.1, r)[-1] == approx(r)


def test_property_monotonic_in_roe0():
    roes = [-0.10 + i * 0.005 for i in range(91)]  # -0.10 .. 0.35
    for r in (0.03, 0.06, 0.09, 0.12, 0.15):
        for b in (0.0, 0.25, 0.5, 0.75, 1.0):
            vals = [ri_value(40.0, x, r, b) for x in roes]
            assert all(v2 > v1 for v1, v2 in zip(vals, vals[1:])), (r, b)


@pytest.mark.parametrize("k", [0.5, 2.0, 3.0])
def test_property_price_scaling(k):
    a = run()
    b = run(price=75.0 * k)
    assert b.v_ri == approx(a.v_ri)
    assert 1 + b.mos == approx((1 + a.mos) / k)
    assert 1 + b.mos_up == approx((1 + a.mos_up) / k)
    assert 1 + b.mos_down == approx((1 + a.mos_down) / k)


@pytest.mark.parametrize("base,scale_fields", [
    (BASE, ("ni_common_ttm", "common_equity_end", "common_equity_start")),
    (G4, ("ni_common_ttm", "common_equity_end", "common_equity_start")),
    (G6, ("ni_common_ttm", "common_equity_end", "common_equity_start", "csm_after_tax")),
])
@pytest.mark.parametrize("c", [1.25, 0.8, 1.0 / 1.37])
def test_property_fx_invariance(base, scale_fields, c):
    """Express NI, CE (and CSM) in another currency: amounts / c, fx_rate * c."""
    orig = run(base)
    kw = dict(base)
    for f in scale_fields:
        kw[f] = kw[f] / c
    kw["fx_rate"] = base.get("fx_rate", 1.0) * c
    moved = evaluate(RIInputs(**kw))
    assert moved.signal == orig.signal
    assert moved.no_signal_reason == orig.no_signal_reason
    assert_values_equal(moved, orig, PER_SHARE_FIELDS)
    assert moved.flags == orig.flags


def test_mos_definition_consistency():
    for kw in (BASE, G4, G6):
        r = run(kw)
        assert r.mos == approx(r.v_ri / r.price - 1)
        assert r.pb == approx(r.price / r.b0)
        assert r.r == approx(kw["rf"] + ERP)
        assert math.isfinite(r.v_ri)
