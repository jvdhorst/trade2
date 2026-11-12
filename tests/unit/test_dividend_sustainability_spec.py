"""Spec tests for Model D, dividend sustainability and implied return
(docs/models/dividend_sustainability.md v0.1.0).

Written from the spec only (not from the implementation). API under test, as the spec names it:
    DInputs, DResult, DMetrics, compute_metrics, map_signal, evaluate,
    run_dividend_sustainability, and the constants in §Constants.
Goldens: relative tolerance 1e-9 (spec §Worked examples); the IEEE boundary notes (G13 spread,
G16 g_hist) are asserted bit for bit, as the spec requires the exact evaluation order.
"""

from __future__ import annotations

import dataclasses
import inspect
import random

import pytest

from valuation.models.dividend_sustainability import (
    BUY_SPREAD,
    DIV_CUT_G,
    ERP,
    EXCLUDED_SECTORS,
    G_CAP,
    G_FLOOR,
    MAX_PAYOUT,
    MAX_PAYOUT_BUY,
    MIN_EX_DATES,
    MODEL_VERSION,
    SELL_SPREAD,
    DInputs,
    DMetrics,
    DResult,
    compute_metrics,
    evaluate,
    map_signal,
    run_dividend_sustainability,
)

REL = 1e-9


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-15)


# Spec §Worked examples defaults: market US, sector Consumer Staples, eligible True, fx 1.0,
# shares 100e6, n_ex_dates 4, rf 0.035; G1 values.
BASE = dict(ticker="G1", market="US", sector="Consumer Staples", eligible=True, price=45.0,
            dps_ttm=2.0, dps_ttm_prev=1.9, n_ex_dates=4, ni_common_ttm=400e6,
            shares_outstanding=100e6, fx_rate=1.0, roe_raw=0.16, rf=0.035)


def mk(**changes) -> DInputs:
    return DInputs(**{**BASE, **changes})


def ev(**changes) -> DResult:
    return evaluate(mk(**changes))


def flags(r) -> set[str]:
    return set(r.flags)


METRIC_FIELDS = ("eps", "payout", "roe0", "g_raw", "g_sust", "g_hist", "dy", "k", "r", "spread",
                 "v_ddm", "mos")


def check(obj, **expected):
    """Every given field equals the golden (None exactly, floats within REL)."""
    for name, want in expected.items():
        got = getattr(obj, name)
        if want is None:
            assert got is None, (name, got)
        else:
            assert got is not None, name
            assert got == approx(want), (name, got, want)


# G1 metrics (spec table), reused by the cases that say "metrics as G1".
G1_METRICS = dict(eps=4.0, payout=0.5, roe0=0.16, g_raw=0.08, g_sust=0.06,
                  g_hist=0.05263157894736836, dy=0.044444444444444446, k=0.10711111111111112,
                  r=0.085, spread=0.02211111111111111, v_ddm=84.79999999999998,
                  mos=0.8844444444444441)


# ----------------------------------------------------------------------------------------------
# Constants and API shape
# ----------------------------------------------------------------------------------------------

def test_constants():
    assert MODEL_VERSION == "dividend_sustainability/0.1.0"
    assert ERP == 0.05
    assert G_FLOOR == 0.0
    assert G_CAP == 0.06
    assert DIV_CUT_G == -0.02
    assert BUY_SPREAD == 0.02
    assert SELL_SPREAD == -0.02
    assert MAX_PAYOUT_BUY == 0.75
    assert MAX_PAYOUT == 1.0
    assert MIN_EX_DATES == 2
    assert EXCLUDED_SECTORS == frozenset({"Real Estate"})


def test_inputs_defaults_and_frozen():
    inp = DInputs(ticker="X", market="US", sector="Utilities", eligible=True, price=10.0)
    assert inp.dps_ttm is None
    assert inp.dps_ttm_prev is None
    assert inp.n_ex_dates == 0
    assert inp.ni_common_ttm is None
    assert inp.shares_outstanding is None
    assert inp.fx_rate == 1.0
    assert inp.roe_raw is None
    assert inp.rf is None
    assert set(inp.flags) == set()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.price = 11.0  # type: ignore[misc]


def test_result_and_metrics_fields():
    assert [f.name for f in dataclasses.fields(DResult)] == [
        "ticker", "market", "signal", "no_signal_reason", *METRIC_FIELDS, "flags"]
    assert [f.name for f in dataclasses.fields(DMetrics)] == [*METRIC_FIELDS, "div_cut"]


def test_map_signal_reads_only_payout_spread_div_cut():
    # V_DDM / MoS are informational only: map_signal has no parameter for them.
    assert list(inspect.signature(map_signal).parameters) == ["payout", "spread", "div_cut"]


# ----------------------------------------------------------------------------------------------
# G1 (proposal golden) and G2–G11, G14, G15
# ----------------------------------------------------------------------------------------------

def test_g1_metrics():
    m = compute_metrics(mk())
    check(m, **G1_METRICS)
    assert m.div_cut is False


def test_g1_buy():
    r = ev()
    assert (r.ticker, r.market) == ("G1", "US")
    assert (r.signal, r.no_signal_reason) == ("BUY", None)
    check(r, **G1_METRICS)
    assert flags(r) == {"G_CAPPED"}


def test_g1_r_is_exact_literal():
    # spec: 0.035 + 0.05 == 0.085 exactly in IEEE
    assert ev().r == 0.085


GOLDEN_ROWS = [
    # (case, changes, expected metrics, signal, flags)
    ("G2", dict(price=50.0),
     dict(dy=0.04, k=0.10239999999999999, spread=0.017399999999999985, v_ddm=84.79999999999998,
          mos=0.6959999999999997, g_sust=0.06),
     "HOLD", {"G_CAPPED"}),
    ("G3", dict(ni_common_ttm=180e6, roe_raw=0.072),
     dict(eps=1.8, payout=1.1111111111111112, g_raw=-0.008000000000000004, g_sust=0.0,
          k=0.044444444444444446, spread=-0.04055555555555556, v_ddm=23.52941176470588,
          mos=-0.47712418300653603),
     "SELL", {"G_FLOORED"}),
    ("G3b", dict(ni_common_ttm=180e6, roe_raw=0.072, price=15.0),
     dict(payout=1.1111111111111112, g_sust=0.0, dy=0.13333333333333333, k=0.13333333333333333,
          spread=0.048333333333333325, v_ddm=23.52941176470588, mos=0.5686274509803921),
     "SELL", {"G_FLOORED"}),
    ("G4", dict(dps_ttm_prev=2.1),
     dict(g_hist=-0.04761904761904767, spread=0.02211111111111111),
     "HOLD", {"G_CAPPED", "DIV_CUT", "BUY_BLOCKED_DIV_CUT"}),
    ("G5", dict(price=100.0, ni_common_ttm=250e6, roe_raw=0.1),
     dict(eps=2.5, payout=0.8, g_raw=0.019999999999999997, g_sust=0.019999999999999997, dy=0.02,
          k=0.0404, spread=-0.04460000000000001, v_ddm=31.384615384615383,
          mos=-0.6861538461538461),
     "SELL", set()),
    ("G6", dict(price=25.0, dps_ttm=1.5, dps_ttm_prev=1.4, ni_common_ttm=250e6, roe_raw=0.12),
     dict(eps=2.5, payout=0.6, g_raw=0.048, g_sust=0.048, g_hist=0.0714285714285714, dy=0.06,
          k=0.11088, spread=0.02588, v_ddm=42.486486486486484, mos=0.6994594594594594),
     "BUY", set()),
    ("G7", dict(price=25.0, ni_common_ttm=250e6, roe_raw=0.2),
     dict(payout=0.8, g_sust=0.039999999999999994, dy=0.08, k=0.1232, spread=0.0382,
          v_ddm=46.222222222222214, mos=0.8488888888888886),
     "HOLD", {"BUY_BLOCKED_PAYOUT"}),
    ("G8", dict(dps_ttm=4.0, dps_ttm_prev=3.8),
     dict(payout=1.0, g_raw=0.0, g_sust=0.0, dy=0.08888888888888889, k=0.08888888888888889,
          spread=0.003888888888888886, v_ddm=47.05882352941176, mos=0.04575163398692794),
     "HOLD", set()),
    ("G9", dict(dps_ttm=1.5, dps_ttm_prev=1.4, ni_common_ttm=300e6, roe_raw=0.12),
     dict(eps=3.0, payout=0.5, g_raw=0.06, g_sust=0.06, dy=0.03333333333333333,
          k=0.09533333333333333, spread=0.01033333333333332, v_ddm=63.59999999999998,
          mos=0.4133333333333329),
     "HOLD", set()),
    ("G10", dict(market="TSX", price=63.0, dps_ttm=2.8, dps_ttm_prev=2.66, ni_common_ttm=400e6,
                 fx_rate=1.4, roe_raw=0.16, rf=0.035),
     dict(eps=5.6, payout=0.5, g_sust=0.06, g_hist=0.05263157894736836, dy=0.04444444444444444,
          k=0.1071111111111111, spread=0.022111111111111095, v_ddm=118.71999999999996,
          mos=0.8844444444444437),
     "BUY", {"G_CAPPED"}),
    ("G11", dict(rf=0.005),
     dict(r=0.055, g_sust=0.06, v_ddm=None, mos=None, k=0.10711111111111112,
          spread=0.052111111111111115),
     "BUY", {"G_CAPPED"}),
    ("G14", dict(dps_ttm_prev=0.0),
     {**G1_METRICS, "g_hist": None},
     "BUY", {"G_CAPPED", "NEW_PAYER"}),
    ("G15", dict(dps_ttm_prev=None),
     {**G1_METRICS, "g_hist": None},
     "BUY", {"G_CAPPED", "NO_DIV_HISTORY"}),
]


@pytest.mark.parametrize("case,changes,metrics,signal,want_flags", GOLDEN_ROWS,
                         ids=[g[0] for g in GOLDEN_ROWS])
def test_goldens(case, changes, metrics, signal, want_flags):
    r = ev(**changes)
    assert (r.signal, r.no_signal_reason) == (signal, None), case
    check(r, **metrics)
    assert flags(r) == want_flags, case
    m = compute_metrics(mk(**changes))
    check(m, **metrics)


def test_g3_g8_g9_clip_bounds():
    # G8: g_raw == 0.0 exactly -> no G_FLOORED; G9: g_raw == 0.06 -> no G_CAPPED
    g8 = ev(dps_ttm=4.0, dps_ttm_prev=3.8)
    assert g8.g_raw == 0.0 and "G_FLOORED" not in flags(g8)
    g9 = ev(dps_ttm=1.5, dps_ttm_prev=1.4, ni_common_ttm=300e6, roe_raw=0.12)
    assert g9.g_raw == 0.06 and "G_CAPPED" not in flags(g9)


def test_g3b_payout_above_one_sells_whatever_spread_no_buy_blocked():
    r = ev(ni_common_ttm=180e6, roe_raw=0.072, price=15.0)
    assert r.spread >= 0.02
    assert r.signal == "SELL"
    assert not flags(r) & {"BUY_BLOCKED_PAYOUT", "BUY_BLOCKED_DIV_CUT"}


def test_g4_div_cut_metric():
    m = compute_metrics(mk(dps_ttm_prev=2.1))
    assert m.div_cut is True


def test_g10_fx_same_ratios_as_g1():
    g1, g10 = ev(), ev(market="TSX", price=63.0, dps_ttm=2.8, dps_ttm_prev=2.66, fx_rate=1.4)
    assert g10.market == "TSX"
    for f in ("payout", "g_sust", "g_hist", "dy", "k", "spread", "mos"):
        assert getattr(g10, f) == approx(getattr(g1, f)), f
    assert g10.signal == g1.signal == "BUY"


def test_g11_v_ddm_never_gates():
    r = ev(rf=0.005)
    assert r.v_ddm is None and r.mos is None
    assert r.signal == "BUY"


def test_g14_g15_metrics_div_cut_false():
    assert compute_metrics(mk(dps_ttm_prev=0.0)).div_cut is False
    assert compute_metrics(mk(dps_ttm_prev=None)).div_cut is False


def test_negative_prev_is_new_payer():
    # spec: "dps_ttm_prev <= 0": g_hist None, div_cut False, NEW_PAYER when dps_ttm > 0
    r = ev(dps_ttm_prev=-0.5)
    assert r.g_hist is None
    assert r.signal == "BUY"
    assert flags(r) == {"G_CAPPED", "NEW_PAYER"}


# ----------------------------------------------------------------------------------------------
# G12 / G13 / G16 boundaries (IEEE)
# ----------------------------------------------------------------------------------------------

G12 = [
    ((0.75, 0.02, False), "BUY"),
    ((0.7500001, 0.03, False), "HOLD"),
    ((0.5, 0.0199999, False), "HOLD"),
    ((0.5, 0.02, True), "HOLD"),
    ((1.0, 0.05, False), "HOLD"),
    ((1.0000001, 0.05, False), "SELL"),
    ((0.5, -0.02, False), "SELL"),
    ((1.0, -0.0199999, False), "HOLD"),
    ((1.5, 0.05, True), "SELL"),
]


@pytest.mark.parametrize("args,want", G12, ids=[str(a) for a, _ in G12])
def test_g12_map_signal_boundaries(args, want):
    assert map_signal(*args) == want


def test_g13_fragile_constructed_spread():
    r = ev(price=40.0, rf=0.043)
    check(r, dy=0.05, k=0.113, r=0.093)
    assert r.r == 0.043 + 0.05
    assert r.spread == 0.020000000000000004  # exact repr (spec G13), not 0.02
    assert r.signal == "BUY"


G16 = [
    (0.98, -0.020000000000000018, dict(payout=0.245, dy=0.049, k=0.11194000000000001,
                                       spread=0.026940000000000006),
     "HOLD", {"G_CAPPED", "DIV_CUT", "BUY_BLOCKED_DIV_CUT"}),
    (0.99, -0.010000000000000009, dict(payout=0.2475, dy=0.0495, k=0.11247,
                                       spread=0.027469999999999994),
     "BUY", {"G_CAPPED"}),
    (0.97, -0.030000000000000027, dict(payout=0.2425, dy=0.0485, k=0.11141000000000001,
                                       spread=0.026410000000000003),
     "HOLD", {"G_CAPPED", "DIV_CUT", "BUY_BLOCKED_DIV_CUT"}),
]


@pytest.mark.parametrize("dps,g_hist,metrics,signal,want_flags", G16,
                         ids=[str(g[0]) for g in G16])
def test_g16_div_cut_boundary(dps, g_hist, metrics, signal, want_flags):
    r = ev(price=20.0, dps_ttm=dps, dps_ttm_prev=1.0)
    assert r.g_hist == g_hist  # exact repr: a nominal -2% is a cut in IEEE
    check(r, **metrics)
    assert r.signal == signal
    assert flags(r) == want_flags
    assert compute_metrics(mk(price=20.0, dps_ttm=dps, dps_ttm_prev=1.0)).div_cut is (
        g_hist < -0.02)


# ----------------------------------------------------------------------------------------------
# G17 precedence
# ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("eligible", [False, None])
def test_g17a_not_eligible_metrics_computed(eligible):
    r = ev(eligible=eligible, sector="Real Estate")
    assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)
    check(r, **G1_METRICS)


def test_g17a_eligible_must_be_true_exactly():
    r = ev(eligible=1)  # anything but True -> NOT_ELIGIBLE
    assert r.signal == "NOT_ELIGIBLE"


def test_g17b_excluded_sector_metrics_as_g1():
    r = ev(sector="Real Estate")
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    check(r, **G1_METRICS)
    assert "G_CAPPED" in flags(r)


@pytest.mark.parametrize("sector", [None, ""])
def test_g17c_missing_sector_excluded(sector):
    r = ev(sector=sector)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")


def test_excluded_sector_case_sensitive():
    # spec: exact, case-sensitive "Real Estate"
    assert ev(sector="real estate").signal == "BUY"


def test_g17d_excluded_before_no_data():
    r = ev(dps_ttm=None, sector="Real Estate")
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")


def test_g17e_dps_none_no_data():
    r = ev(dps_ttm=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    check(r, payout=None, g_raw=None, g_sust=None, g_hist=None, dy=None, k=None, spread=None,
          v_ddm=None, mos=None, eps=4.0, r=0.085)
    assert "NO_DIV_HISTORY" not in flags(r)
    assert not flags(r) & {"NEW_PAYER", "IRREGULAR_DIV", "DIV_CUT", "G_CAPPED"}


def test_g17f_no_dividend():
    r = ev(dps_ttm=0.0, n_ex_dates=0)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DIVIDEND")
    check(r, payout=0.0, g_raw=0.16, g_sust=0.06, g_hist=-1.0, dy=0.0, k=0.06,
          spread=-0.02500000000000001, v_ddm=0.0, mos=-1.0)
    assert flags(r) == {"G_CAPPED", "DIV_CUT"}


def test_g17g_no_dividend_before_no_data():
    r = ev(dps_ttm=0.0, n_ex_dates=0, ni_common_ttm=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DIVIDEND")
    assert r.eps is None
    assert "DIV_CUT" in flags(r)  # g_hist = 0/1.9 - 1 = -1.0 still computed


def test_g17h_irregular_div():
    r = ev(n_ex_dates=1)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "IRREGULAR_DIV")
    assert flags(r) == {"IRREGULAR_DIV", "G_CAPPED"}
    check(r, **G1_METRICS)


def test_g17i_irregular_before_neg_earnings():
    r = ev(n_ex_dates=1, ni_common_ttm=-50e6, roe_raw=-0.02)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "IRREGULAR_DIV")
    assert flags(r) == {"IRREGULAR_DIV", "NEG_EARNINGS"}


def test_g17j_ni_none_no_data():
    r = ev(ni_common_ttm=None, roe_raw=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    check(r, eps=None, payout=None, g_raw=None, g_sust=None, k=None, spread=None, v_ddm=None,
          mos=None, dy=0.044444444444444446, g_hist=0.05263157894736836, r=0.085)


@pytest.mark.parametrize("shares", [None, 0, 0.0, -1e6])
def test_g17k_shares_missing_no_data(shares):
    r = ev(shares_outstanding=shares)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert r.eps is None


def test_g17l_neg_earnings():
    r = ev(ni_common_ttm=-50e6, roe_raw=-0.02)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_EARNINGS")
    assert "NEG_EARNINGS" in flags(r)
    check(r, eps=-0.5, payout=None, g_sust=None, k=None, spread=None, dy=0.044444444444444446)


def test_g17m_zero_earnings():
    r = ev(ni_common_ttm=0.0, roe_raw=0.0)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NEG_EARNINGS")
    assert "NEG_EARNINGS" in flags(r)
    assert r.eps == 0.0
    assert r.payout is None


def test_g17n_no_roe():
    r = ev(roe_raw=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_ROE")
    check(r, payout=0.5, roe0=None, g_raw=None, g_sust=None, k=None, spread=None, v_ddm=None,
          mos=None)


def test_g17o_no_roe_before_no_rf():
    r = ev(roe_raw=None, rf=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_ROE")


def test_g17p_no_rf():
    r = ev(rf=None)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_RF")
    check(r, g_sust=0.06, k=0.10711111111111112, r=None, spread=None, v_ddm=None, mos=None)
    assert flags(r) == {"G_CAPPED"}


def test_g17q_financials_included():
    r = ev(sector="Financials")
    assert (r.signal, r.no_signal_reason) == ("BUY", None)
    check(r, **G1_METRICS)


def test_dps_prev_not_required_for_signal():
    assert ev(dps_ttm_prev=None).signal == "BUY"


VALUE_ERRORS = [
    ("market LSE", dict(market="LSE")),
    ("price 0", dict(price=0.0)),
    ("price negative", dict(price=-1.0)),
    ("price None", dict(price=None)),
    ("fx 0", dict(fx_rate=0.0)),
    ("fx negative", dict(fx_rate=-1.4)),
    ("n_ex_dates -1", dict(n_ex_dates=-1)),
]


# SPEC-AMBIGUITY: the spec says compute_metrics and evaluate raise the ValueErrors; it does not
# say whether DInputs itself validates on construction. Both are accepted: construction is
# inside the pytest.raises block.
@pytest.mark.parametrize("case,changes", VALUE_ERRORS, ids=[v[0] for v in VALUE_ERRORS])
def test_g17r_value_errors(case, changes):
    with pytest.raises(ValueError):
        evaluate(mk(**changes))
    with pytest.raises(ValueError):
        compute_metrics(mk(**changes))


def test_value_error_even_when_not_eligible():
    # validation is not skipped by the precedence table (precedence decides labels only)
    with pytest.raises(ValueError):
        evaluate(mk(market="LSE", eligible=False))


def test_run_returns_one_result_per_input_in_order():
    inputs = [mk(ticker="G1"), mk(ticker="G3", ni_common_ttm=180e6, roe_raw=0.072),
              mk(ticker="G17e", dps_ttm=None), mk(ticker="G17a", eligible=False,
                                                    sector="Real Estate")]
    out = run_dividend_sustainability(inputs)
    assert isinstance(out, list)
    assert [r.ticker for r in out] == ["G1", "G3", "G17e", "G17a"]
    assert [r.signal for r in out] == ["BUY", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"]
    assert out == [evaluate(i) for i in inputs]
    assert run_dividend_sustainability([]) == []


# ----------------------------------------------------------------------------------------------
# Flag rules
# ----------------------------------------------------------------------------------------------

def test_pass_through_flags():
    pt = frozenset({"PIT_ESTIMATED", "NO_TTM", "STALE_TTM", "ROE_SPOT_EQUITY", "CSM_ADJUSTED"})
    r = ev(flags=pt)
    assert r.signal == "BUY"
    assert flags(r) == set(pt) | {"G_CAPPED"}
    r2 = ev(flags=frozenset({"PIT_ESTIMATED"}), dps_ttm=None)
    assert "PIT_ESTIMATED" in flags(r2)


def test_buy_blocked_both_flags():
    # G7 with a cut: payout 0.8 > 0.75 and div_cut, spread 0.0382 >= 0.02 -> HOLD, both blocks
    r = ev(price=25.0, ni_common_ttm=250e6, roe_raw=0.2, dps_ttm_prev=2.1)
    check(r, payout=0.8, g_hist=-0.04761904761904767, spread=0.0382)
    assert r.signal == "HOLD"
    assert flags(r) == {"BUY_BLOCKED_PAYOUT", "BUY_BLOCKED_DIV_CUT", "DIV_CUT"}


def test_buy_blocked_only_on_row_10():
    # G4 (cut, spread >= 0.02) but stopped earlier in the table: no BUY_BLOCKED_* flags
    for changes, label in ((dict(sector="Real Estate"), "NO_SIGNAL"),
                           (dict(eligible=False), "NOT_ELIGIBLE"),
                           (dict(n_ex_dates=1), "NO_SIGNAL"),
                           (dict(rf=None), "NO_SIGNAL")):
        r = ev(dps_ttm_prev=2.1, **changes)
        assert r.signal == label, changes
        assert "DIV_CUT" in flags(r), changes
        assert not flags(r) & {"BUY_BLOCKED_PAYOUT", "BUY_BLOCKED_DIV_CUT"}, changes
    # G7 with payout 0.8 but excluded: no BUY_BLOCKED_PAYOUT
    r = ev(price=25.0, ni_common_ttm=250e6, roe_raw=0.2, sector="Real Estate")
    assert "BUY_BLOCKED_PAYOUT" not in flags(r)


def test_buy_blocked_needs_spread_at_least_buy_spread():
    # HOLD with payout > 0.75 and spread < 0.02 (G8: payout 1.0, spread 0.0039): no block flag
    r = ev(dps_ttm=4.0, dps_ttm_prev=3.8)
    assert r.signal == "HOLD" and r.spread < 0.02
    assert "BUY_BLOCKED_PAYOUT" not in flags(r)
    # HOLD by spread with a cut: DIV_CUT but no BUY_BLOCKED_DIV_CUT (G2 with a cut)
    r = ev(price=50.0, dps_ttm_prev=2.1)
    assert r.signal == "HOLD"
    assert "DIV_CUT" in flags(r) and "BUY_BLOCKED_DIV_CUT" not in flags(r)


def test_div_cut_on_sell_still_flagged():
    r = ev(ni_common_ttm=180e6, roe_raw=0.072, dps_ttm_prev=2.1)
    assert r.signal == "SELL"
    assert flags(r) == {"G_FLOORED", "DIV_CUT"}


def test_g_floored_not_set_when_g_raw_missing():
    r = ev(roe_raw=None)
    assert not flags(r) & {"G_CAPPED", "G_FLOORED"}


def test_new_payer_not_set_without_dividend():
    # NEW_PAYER only when dps_ttm > 0
    r = ev(dps_ttm=0.0, dps_ttm_prev=0.0, n_ex_dates=0)
    assert r.no_signal_reason == "NO_DIVIDEND"
    assert "NEW_PAYER" not in flags(r)
    assert r.g_hist is None


def test_no_div_history_set_whatever_signal():
    r = ev(dps_ttm_prev=None, eligible=False)
    assert r.signal == "NOT_ELIGIBLE"
    assert "NO_DIV_HISTORY" in flags(r)
    # SPEC-AMBIGUITY: G17e says NO_DIV_HISTORY is "not set (prev given)", implying the flag
    # follows dps_ttm_prev None alone; with dps_ttm None *and* prev None the spec is silent,
    # so that combination is not asserted.


def test_irregular_div_flag_needs_positive_dps():
    r = ev(dps_ttm=0.0, n_ex_dates=0)
    assert "IRREGULAR_DIV" not in flags(r)
    r = ev(n_ex_dates=0)  # dps > 0, no ex-date count: irregular
    assert r.no_signal_reason == "IRREGULAR_DIV"
    assert "IRREGULAR_DIV" in flags(r)


def test_neg_earnings_flag_whatever_signal():
    r = ev(ni_common_ttm=-50e6, roe_raw=-0.02, sector="Real Estate")
    assert r.no_signal_reason == "EXCLUDED_SECTOR"
    assert "NEG_EARNINGS" in flags(r)


def test_metrics_never_filled_in():
    # no monetary input is defaulted to 0: missing NI leaves eps None, not 0
    r = evaluate(DInputs(ticker="X", market="US", sector="Utilities", eligible=True,
                         price=10.0))
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    for f in ("eps", "payout", "roe0", "g_raw", "g_sust", "g_hist", "dy", "k", "r", "spread",
              "v_ddm", "mos"):
        assert getattr(r, f) is None, f


# ----------------------------------------------------------------------------------------------
# Property expectations (deterministic random grid; no hypothesis dependency)
# ----------------------------------------------------------------------------------------------

def _random_inputs(n=600, seed=20261112):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ni = rng.choice([None, 0.0]) if rng.random() < 0.05 else rng.uniform(-2e8, 1.5e9)
        roe = None
        if ni is not None and rng.random() > 0.05:
            # roe_raw has the sign of NI (avg equity > 0), as the spec's G_FLOORED note assumes
            roe = abs(rng.uniform(0.0, 0.4)) * (1 if ni > 0 else -1 if ni < 0 else 0)
        dps = rng.choice([None, 0.0]) if rng.random() < 0.05 else rng.uniform(0.05, 12.0)
        prev = rng.choice([None, 0.0, rng.uniform(0.05, 12.0), rng.uniform(0.05, 12.0)])
        out.append(DInputs(
            ticker=f"T{i}", market=rng.choice(["US", "TSX"]),
            sector=rng.choice(["Utilities", "Financials", "Energy", "Real Estate"]),
            eligible=rng.random() > 0.05, price=rng.uniform(2.0, 300.0), dps_ttm=dps,
            dps_ttm_prev=prev, n_ex_dates=rng.choice([0, 1, 2, 4, 5, 12]), ni_common_ttm=ni,
            shares_outstanding=rng.choice([None, rng.uniform(1e7, 2e9)])
            if rng.random() < 0.05 else rng.uniform(1e7, 2e9),
            fx_rate=rng.choice([1.0, 1.4, 1 / 1.4]), roe_raw=roe,
            rf=None if rng.random() < 0.03 else rng.uniform(0.0, 0.08)))
    return out


INPUTS = _random_inputs()
RANK = {"SELL": 0, "HOLD": 1, "BUY": 2}


def _row10(r):
    return r.signal in RANK


def test_property_signal_implications():
    for inp in INPUTS:
        r = evaluate(inp)
        assert r.signal in {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
        assert (r.signal == "NO_SIGNAL") == (r.no_signal_reason is not None)
        if r.signal == "BUY":
            assert r.spread >= 0.02 and r.payout <= 0.75 and "DIV_CUT" not in flags(r)
        if r.signal == "SELL":
            assert r.payout > 1.0 or r.spread <= -0.02
        if _row10(r) and "G_FLOORED" in flags(r):
            assert r.signal == "SELL"
        if _row10(r):
            assert r.signal == map_signal(r.payout, r.spread, "DIV_CUT" in flags(r))


def test_property_bounds():
    for inp in INPUTS:
        r = evaluate(inp)
        if r.g_sust is not None:
            assert 0.0 <= r.g_sust <= 0.06
        if r.k is not None:
            assert r.k >= r.dy
        if r.g_raw is not None:
            assert ("G_CAPPED" in flags(r)) == (r.g_raw > 0.06)
            assert ("G_FLOORED" in flags(r)) == (r.g_raw < 0.0)


SCALE_INVARIANT = ("payout", "g_raw", "g_sust", "g_hist", "dy", "k", "spread", "mos")


def _same(a, b, fields):
    for f in fields:
        x, y = getattr(a, f), getattr(b, f)
        if x is None or y is None:
            assert x is None and y is None, f
        else:
            assert y == approx(x), f


def _scaled(inp, c):
    def s(v):
        return None if v is None else v * c
    return dataclasses.replace(inp, price=inp.price * c, dps_ttm=s(inp.dps_ttm),
                               dps_ttm_prev=s(inp.dps_ttm_prev), ni_common_ttm=s(inp.ni_common_ttm))


@pytest.mark.parametrize("c", [0.5, 3.0, 7.3])
def test_property_money_scaling(c):
    for inp in INPUTS:
        a, b = evaluate(inp), evaluate(_scaled(inp, c))
        _same(a, b, SCALE_INVARIANT)
        assert a.signal == b.signal and a.no_signal_reason == b.no_signal_reason
        assert flags(a) == flags(b)
        if a.eps is not None:
            assert b.eps == approx(a.eps * c)
        if a.v_ddm is not None:
            assert b.v_ddm == approx(a.v_ddm * c)


# SPEC-AMBIGUITY: the property says "changing the reporting currency with a matching fx_rate
# (NI / fx fixed)", but eps = NI / shares * fx, so a currency change that leaves eps unchanged
# keeps NI * fx fixed (NI' = NI * c, fx' = fx / c). Tested with NI * fx fixed.
@pytest.mark.parametrize("c", [1.4, 1 / 1.4, 7.0])
def test_property_reporting_currency_change(c):
    for inp in INPUTS:
        ni = None if inp.ni_common_ttm is None else inp.ni_common_ttm * c
        b = evaluate(dataclasses.replace(inp, ni_common_ttm=ni, fx_rate=inp.fx_rate / c))
        a = evaluate(inp)
        _same(a, b, METRIC_FIELDS)
        assert (a.signal, a.no_signal_reason, flags(a)) == (b.signal, b.no_signal_reason,
                                                            flags(b))


@pytest.mark.parametrize("factor", [0.97, 0.8, 0.5])
def test_property_lower_price_never_worsens(factor):
    for inp in INPUTS:
        a = evaluate(inp)
        b = evaluate(dataclasses.replace(inp, price=inp.price * factor))
        if _row10(a) and _row10(b):
            assert RANK[b.signal] >= RANK[a.signal], inp.ticker


@pytest.mark.parametrize("bump", [0.001, 0.01, 0.03])
def test_property_higher_rf_never_improves(bump):
    for inp in INPUTS:
        if inp.rf is None:
            continue
        a = evaluate(inp)
        b = evaluate(dataclasses.replace(inp, rf=inp.rf + bump))
        if _row10(a) and _row10(b):
            assert RANK[b.signal] <= RANK[a.signal], inp.ticker


def test_property_signal_independent_of_v_ddm_mos():
    # v_ddm / mos are never read: the signal equals map_signal on (payout, spread, div_cut)
    # whatever v_ddm is (None in G11, positive mos with SELL in G3b, HOLD with mos 0.85 in G7).
    for changes in (dict(rf=0.005), dict(ni_common_ttm=180e6, roe_raw=0.072, price=15.0),
                    dict(price=25.0, ni_common_ttm=250e6, roe_raw=0.2)):
        r = ev(**changes)
        m = compute_metrics(mk(**changes))
        assert r.signal == map_signal(m.payout, m.spread, m.div_cut)
    assert ev(ni_common_ttm=180e6, roe_raw=0.072, price=15.0).mos > 0  # SELL despite mos > 0


def test_property_independent_names():
    out = run_dividend_sustainability(INPUTS)
    assert out == [evaluate(i) for i in INPUTS]
    assert run_dividend_sustainability(INPUTS[::-1]) == out[::-1]
