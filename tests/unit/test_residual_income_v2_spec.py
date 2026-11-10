"""Spec tests for Model A v0.2.0 (docs/models/residual_income.md, revision round v2).

Written from the spec only (not from the implementation): G8 one-off quarter / SELL zone
table, the changed G3 clip and G5 cases, the SELL_ZONE / screen_signal properties and the
dominant-quarter rule. G8 values were re-derived by QA with a plain-Python reference of
§Formulas before being written here. Relative tolerance 1e-9.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from valuation.models.residual_income import (
    DOMINANT_SHARE,
    MODEL_VERSION,
    RIInputs,
    evaluate,
)

REL = 1e-9
AS_OF = date(2026, 11, 9)
E9 = 1e9

BASE = dict(
    ticker="BANK", as_of=AS_OF, market="US", sector="Financials", eligible=True, rf=0.042,
    ni_common_ttm=13.0e9, common_equity_end=100.0e9, common_equity_start=92.0e9,
    shares_outstanding=2.0e9, fx_rate=1.0, bvps=50.0, dps_ttm=2.6, price=75.0,
)
G4 = dict(
    ticker="MAT.TO", as_of=AS_OF, market="TSX", sector="Materials", eligible=True, rf=0.032,
    ni_common_ttm=1.0e9, common_equity_end=10.0e9, common_equity_start=9.0e9,
    shares_outstanding=5.0e8, fx_rate=1.40, bvps=28.0, dps_ttm=1.12, price=30.0,
    filed_estimated=True, roe_history=(),
)
BASE_ROE = 0.13541666666666666
BASE_V = 58.567222546400856


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def run(base=None, **overrides):
    kw = dict(BASE if base is None else base)
    kw.update(overrides)
    return evaluate(RIInputs(**kw))


def q(*vals):
    return tuple(v * E9 for v in vals)


# ---------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------


def test_model_version_and_dominant_share():
    assert MODEL_VERSION == "residual_income/0.2.0"
    assert DOMINANT_SHARE == 0.5


def test_inputs_default_ni_quarters_none():
    inp = RIInputs(**BASE)
    assert inp.ni_quarters is None


def test_result_has_v2_fields():
    names = {f.name for f in dataclasses.fields(run())}
    assert names >= {"screen_signal", "ni_norm", "roe_norm", "one_off", "signal"}


# ---------------------------------------------------------------------------
# G8 one-off quarter, SELL zone
# ---------------------------------------------------------------------------

# (ni_quarters e9, ni, price, one_off, ni_norm, roe_raw, roe0, v_ri, mos, signal, screen, flags)
G8 = [
    ((3.25, 3.25, 3.25, 3.25), 13.0e9, 75.0, False, 13.0e9, BASE_ROE, BASE_ROE, BASE_V,
     -0.2191036993813219, "SELL", "HOLD", {"SELL_ZONE"}),
    ((1.5, 2.0, 3.0, 6.5), 13.0e9, 45.0, False, 13.0e9, BASE_ROE, BASE_ROE, BASE_V,
     0.30149383436446353, "BUY", "BUY", set()),
    ((1.5, 1.5, 2.0, 8.0), 13.0e9, 45.0, True, 6666666666.666667, BASE_ROE,
     0.06944444444444445, 45.845100042050326, 0.018780000934451646, "HOLD", "HOLD",
     {"ONE_OFF_QUARTER"}),
    ((1.5, 1.5, 2.0, 8.0), 13.0e9, 30.0, True, 6666666666.666667, BASE_ROE,
     0.06944444444444445, 45.845100042050326, 0.5281700014016775, "BUY", "BUY",
     {"ONE_OFF_QUARTER"}),
    ((-1, -1, -1, 16), 13.0e9, 75.0, True, -4.0e9, BASE_ROE, -0.041666666666666664,
     28.034918814775413, -0.6262010824696611, "SELL", "HOLD", {"ONE_OFF_QUARTER", "SELL_ZONE"}),
    ((12, 12, 12, 12), 48.0e9, 75.0, False, 48.0e9, 0.5, 0.35, 128.46557945890916,
     0.7128743927854555, "HOLD", "HOLD", {"ROE_CLIPPED", "BUY_BLOCKED_CLIP"}),
]


@pytest.mark.parametrize("row", G8, ids=[f"g8_{i}" for i in range(len(G8))])
def test_g8_table(row):
    nq, ni, price, one_off, ni_norm, roe_raw, roe0, v_ri, mos, sig, screen, flags = row
    r = run(ni_common_ttm=ni, ni_quarters=q(*nq), price=price)
    assert r.one_off is one_off
    assert r.ni_norm == approx(ni_norm)
    assert r.roe_raw == approx(roe_raw)
    assert r.roe0 == approx(roe0)
    assert r.v_ri == approx(v_ri)
    assert r.mos == approx(mos)
    assert r.signal == sig
    assert r.screen_signal == screen
    assert r.flags == frozenset(flags)
    assert r.no_signal_reason is None
    # eps_ttm uses the reported NI; b from it
    assert r.eps_ttm == approx(ni / 2.0e9)


def test_g8_roe_norm_on_one_off():
    r = run(ni_quarters=q(1.5, 1.5, 2.0, 8.0))
    assert r.roe_norm == approx(0.06944444444444445)
    assert r.roe_raw == approx(BASE_ROE)
    assert r.b == approx(0.6)


def test_g8_roe_norm_equals_raw_without_one_off():
    r = run(ni_quarters=q(3.25, 3.25, 3.25, 3.25))
    assert r.roe_norm == approx(r.roe_raw)


def test_g8_one_off_sensitivity_and_mos_up():
    r = run(ni_quarters=q(1.5, 1.5, 2.0, 8.0), price=30.0)
    assert r.v_ri_up == approx(44.16687484558917)
    assert r.v_ri_down == approx(47.620985649177435)
    assert r.mos_up == approx(0.472229161519639)
    assert r.b == approx(0.6)


# ---------------------------------------------------------------------------
# Changed G3 / G5 cases
# ---------------------------------------------------------------------------


def test_g3_clip_buy_blocked():
    r = run(ni_common_ttm=48.0e9)
    assert r.roe_raw == approx(0.5)
    assert r.roe0 == approx(0.35)
    assert r.eps_ttm == approx(24.0)
    assert r.b == approx(1 - 2.6 / 24)
    assert r.v_ri == approx(128.46557945890916)
    assert r.mos == approx(0.7128743927854555)
    assert r.signal == "HOLD"
    assert r.screen_signal == "HOLD"
    assert r.flags == frozenset({"ROE_CLIPPED", "BUY_BLOCKED_CLIP"})


def test_g3_clip_low_still_sell_zone():
    r = run(ni_common_ttm=-28.8e9)
    assert r.roe0 == approx(-0.10)
    assert r.v_ri == approx(20.257657687590946)
    assert r.signal == "SELL"
    assert r.screen_signal == "HOLD"
    assert r.flags == frozenset({"ROE_CLIPPED", "SELL_ZONE"})


def test_g5_clip_after_average_buy_blocked():
    r = run(G4, roe_history=(0.50, 0.45))
    assert r.roe_basis == "3y_avg"
    assert r.roe0 == approx(0.35)
    assert r.roe_raw == approx(1 / 9.5)
    assert r.v_ri == approx(66.4010543305594)
    assert r.signal == "HOLD"
    assert r.screen_signal == "HOLD"
    assert r.flags >= {"ROE_CLIPPED", "CYCLICAL_ROE", "BUY_BLOCKED_CLIP"}


def test_g5_unchanged_cases():
    r = run(G4, roe_history=(0.06, 0.02))
    assert r.roe0 == approx(0.06175438596491228)
    assert r.v_ri == approx(25.869054656427853)
    assert (r.signal, r.screen_signal) == ("HOLD", "HOLD")
    assert "BUY_BLOCKED_CLIP" not in r.flags


def test_buy_blocked_clip_only_on_buy():
    # A clipped ROE that maps to HOLD or SELL never gets BUY_BLOCKED_CLIP.
    for price in (128.0, 200.0):
        r = run(ni_common_ttm=48.0e9, price=price)
        assert "ROE_CLIPPED" in r.flags
        assert "BUY_BLOCKED_CLIP" not in r.flags


def test_unclipped_buy_stays_buy():
    r = run(price=45.0)
    assert (r.signal, r.screen_signal) == ("BUY", "BUY")
    assert r.flags == frozenset()


# ---------------------------------------------------------------------------
# SELL zone properties
# ---------------------------------------------------------------------------

CASES = [
    {}, dict(price=45.0), dict(price=52.0), dict(price=80.0), dict(price=400.0),
    dict(price=400.01), dict(common_equity_start=None), dict(ni_common_ttm=48.0e9),
    dict(ni_common_ttm=-28.8e9), dict(ni_common_ttm=-2.0e9), dict(eligible=False),
    dict(bvps=-1.0), dict(sector="Real Estate", market="TSX"), dict(rf=None),
    dict(sector="Health Care", price=150.0), dict(ni_quarters=q(-1, -1, -1, 16)),
    dict(ni_quarters=q(1.5, 1.5, 2.0, 8.0), price=30.0),
]


@pytest.mark.parametrize("overrides", CASES)
def test_screen_signal_never_sell_and_sell_zone_iff_sell(overrides):
    r = run(**overrides)
    assert r.screen_signal != "SELL"
    assert (r.signal == "SELL") == ("SELL_ZONE" in r.flags)
    if r.signal == "SELL":
        assert r.screen_signal == "HOLD"
    else:
        assert r.screen_signal == r.signal


def test_sell_zone_on_g3_base():
    r = run()
    assert r.signal == "SELL"
    assert r.screen_signal == "HOLD"
    assert "SELL_ZONE" in r.flags


def test_not_eligible_and_no_signal_screen_equal_signal():
    r = run(eligible=None)
    assert r.signal == r.screen_signal == "NOT_ELIGIBLE"
    r = run(rf=None)
    assert r.signal == r.screen_signal == "NO_SIGNAL"
    assert "SELL_ZONE" not in r.flags


# ---------------------------------------------------------------------------
# Dominant-quarter rule
# ---------------------------------------------------------------------------


def test_exactly_half_is_not_one_off():
    r = run(ni_quarters=q(1.5, 2.0, 3.0, 6.5))
    assert r.one_off is False
    assert "ONE_OFF_QUARTER" not in r.flags
    assert r.roe0 == approx(BASE_ROE)


def test_just_above_half_is_one_off():
    r = run(ni_quarters=(1.5e9, 2.0e9, 2.999e9, 6.501e9))
    assert r.one_off is True
    assert "ONE_OFF_QUARTER" in r.flags
    assert r.ni_norm == approx((13.0e9 - 6.501e9) * 4 / 3)


@pytest.mark.parametrize("nq", [
    None, (), q(13.0), q(4.0, 9.0), q(1.0, 1.0, 11.0), q(1.0, 1.0, 1.0, 1.0, 9.0),
])
def test_no_test_without_exactly_four_quarters(nq):
    r = run(ni_quarters=nq)
    base = run()
    assert r.one_off is False
    assert "ONE_OFF_QUARTER" not in r.flags
    assert r.ni_norm == approx(13.0e9)
    assert r.roe0 == approx(base.roe0)
    assert r.v_ri == approx(base.v_ri)
    assert r.signal == base.signal


def test_quarter_order_irrelevant():
    a = run(ni_quarters=q(8.0, 1.5, 2.0, 1.5))
    b = run(ni_quarters=q(1.5, 1.5, 2.0, 8.0))
    assert a.one_off is b.one_off is True
    assert a.ni_norm == approx(b.ni_norm)
    assert a.v_ri == approx(b.v_ri)


def test_non_positive_ni_never_one_off():
    r = run(ni_common_ttm=-2.0e9, ni_quarters=q(-1.0, -1.0, -1.0, 1.0))
    assert r.one_off is False
    assert r.roe0 == approx(-0.020833333333333332)
    r = run(ni_common_ttm=0.0, ni_quarters=q(-1.0, -1.0, 1.0, 1.0))
    assert r.one_off is False


@pytest.mark.parametrize("nq", [q(1.5, 1.5, 2.0, 8.0), q(1.5, 2.0, 3.0, 6.5),
                                q(-1, -1, -1, 16), q(3.25, 3.25, 3.25, 3.25)])
@pytest.mark.parametrize("k", [1e-3, 0.37, 5.0, 1e3])
def test_scale_invariance_of_one_off(nq, k):
    a = run(ni_quarters=nq)
    b = run(ni_common_ttm=13.0e9 * k, ni_quarters=tuple(x * k for x in nq))
    assert a.one_off is b.one_off


@pytest.mark.parametrize("nq", [q(1.5, 1.5, 2.0, 8.0), q(-1, -1, -1, 16), q(0.1, 0.1, 0.1, 12.7),
                                q(2.0, 2.0, 2.0, 7.0)])
def test_ni_norm_le_ni_and_roe_raw_unchanged(nq):
    r = run(ni_quarters=nq)
    assert r.one_off is True
    assert r.ni_norm <= 13.0e9
    assert r.roe_raw == approx(BASE_ROE)
    assert r.eps_ttm == approx(6.5)
    assert r.b == approx(0.6)


def test_roe_history_not_normalised_cyclical():
    # One-off applies to the current TTM only; the 3y average uses ROE_norm + raw history.
    nq = (0.1e9, 0.1e9, 0.1e9, 0.7e9)  # sums to 1.0e9; 0.7 > 0.5
    r = run(G4, ni_quarters=nq, roe_history=(0.06, 0.02))
    roe_norm = (1.0e9 - 0.7e9) * 4 / 3 / 9.5e9
    assert r.one_off is True
    assert r.roe_norm == approx(roe_norm)
    assert r.roe0 == approx((roe_norm + 0.06 + 0.02) / 3)
    assert r.roe_basis == "3y_avg"
    assert r.roe_raw == approx(1 / 9.5)
