"""Spec tests for Model H, shareholder yield (docs/models/shareholder_yield.md v0.1.0).

Written from the spec only (not from the implementation). Goldens G1-G10 (incl. G7b) were
re-derived by QA with a plain-Python reference of §Formulas; relative tolerance 1e-9
(absolute 1e-12 for zero). Agreed API (the spec fixes SYInputs and the module):
    compute_sy(inp: SYInputs) -> SYResult
    SYResult: ticker, signal, no_signal_reason, dy, bby, sy, eps_ttm, payout, funded, flags
    sy_percentiles(values: dict[str, float]) -> dict[str, float | None]
"""

from __future__ import annotations

import itertools

import pytest

from valuation.models.shareholder_yield import (
    BUY_SY,
    MAX_ABS_BBY,
    MAX_PAYOUT,
    MODEL_VERSION,
    PREV_WINDOW_DAYS,
    SELL_SY,
    SYInputs,
    SYResult,
    compute_sy,
    map_signal,
    sy_percentiles,
)

REL = 1e-9
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def opt(x):
    return None if x is None else approx(x)


def make(**kw) -> SYInputs:
    base = dict(ticker="T", market="US", sector="Industrials", eligible=True, price=100.0,
                dps_ttm=0.0, shares_t=None, shares_prev=None, split_factor=1.0,
                ni_common_ttm=None, fx_rate=1.0, flags=frozenset())
    base.update(kw)
    return SYInputs(**base)


def run(**kw) -> SYResult:
    return compute_sy(make(**kw))


# ---------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------


def test_constants():
    assert MODEL_VERSION == "shareholder_yield/0.1.0"
    assert BUY_SY == 0.05
    assert SELL_SY == -0.03
    assert MAX_ABS_BBY == 0.15
    assert MAX_PAYOUT == 1.0
    assert tuple(PREV_WINDOW_DAYS) == (350, 380)


def test_inputs_defaults():
    inp = SYInputs(ticker="T", market="US", sector=None, eligible=True, price=10.0)
    assert inp.dps_ttm == 0.0
    assert inp.shares_t is None and inp.shares_prev is None
    assert inp.split_factor == 1.0
    assert inp.ni_common_ttm is None
    assert inp.fx_rate == 1.0
    assert inp.flags == frozenset()


def test_result_fields():
    r = run(shares_t=1.0e9, shares_prev=1.0e9, ticker="ABC")
    for f in ("ticker", "signal", "no_signal_reason", "dy", "bby", "sy", "eps_ttm", "payout",
              "funded", "flags"):
        assert hasattr(r, f), f
    assert r.ticker == "ABC"
    assert isinstance(r.flags, frozenset)


# ---------------------------------------------------------------------------
# Goldens G1-G10
# ---------------------------------------------------------------------------

# name: (inputs, dy, bby, sy, eps, payout, funded, signal, reason, flags)
GOLDENS = {
    "G1": (dict(price=80.0, dps_ttm=2.40, shares_prev=1050e6, shares_t=1008e6,
                ni_common_ttm=4200e6),
           0.03, 0.04, 0.07, 4.166666666666667, 0.576, True, "BUY", None, set()),
    "G2": (dict(price=50.0, dps_ttm=0.0, shares_prev=200e6, shares_t=212e6, ni_common_ttm=300e6),
           0.0, -0.06, -0.06, 1.4150943396226414, 0.0, True, "SELL", None, set()),
    "G4": (dict(price=100.0, dps_ttm=2.0, shares_prev=1000e6, shares_t=970e6,
                ni_common_ttm=5000e6),
           0.02, 0.03, 0.05, 5.154639175257732, 0.38799999999999996, True, "BUY", None, set()),
    "G5": (dict(price=100.0, dps_ttm=3.0, shares_prev=1000e6, shares_t=980e6,
                ni_common_ttm=-100e6),
           0.03, 0.02, 0.05, -0.10204081632653061, None, False, "HOLD", None, {"NOT_FUNDED"}),
    "G6": (dict(price=100.0, dps_ttm=6.0, shares_prev=1000e6, shares_t=980e6,
                ni_common_ttm=5000e6),
           0.06, 0.02, 0.08, 5.1020408163265305, 1.176, False, "HOLD", None, {"NOT_FUNDED"}),
    "G7": (dict(price=40.0, dps_ttm=0.0, shares_prev=1000e6, shares_t=850e6, ni_common_ttm=1e9),
           0.0, 0.15, 0.15, 1.1764705882352942, 0.0, True, "BUY", None, set()),
    "G7b": (dict(price=40.0, dps_ttm=0.0, shares_prev=1000e6, shares_t=849e6, ni_common_ttm=1e9),
            0.0, 0.151, 0.151, 1e9 / 849e6, 0.0, True, "NO_SIGNAL", "CORPORATE_ACTION", None),
    "G8": (dict(price=40.0, dps_ttm=0.4, shares_prev=500e6, split_factor=2.0, shares_t=980e6,
                ni_common_ttm=1e9),
           0.01, 0.02, 0.03, 1.0204081632653061, 0.392, True, "HOLD", None, {"SPLIT_ADJUSTED"}),
    "G9": (dict(market="TSX", price=30.0, dps_ttm=1.12, shares_prev=5.0e8, shares_t=4.9e8,
                ni_common_ttm=1.0e9, fx_rate=1.40),
           0.037333333333333336, 0.02, 0.05733333333333333, 2.857142857142857, 0.392, True,
           "BUY", None, set()),
    "G10": (dict(price=50.0, dps_ttm=0.0, shares_prev=1000e6, shares_t=1030e6, ni_common_ttm=1e9),
            0.0, -0.03, -0.03, 0.970873786407767, 0.0, True, "SELL", None, set()),
}


@pytest.mark.parametrize("name", list(GOLDENS))
def test_goldens(name):
    kw, dy, bby, sy, eps, payout, funded, sig, reason, flags = GOLDENS[name]
    r = run(**kw)
    assert r.dy == approx(dy)
    assert r.bby == approx(bby)
    assert r.sy == approx(sy)
    assert r.eps_ttm == opt(eps)
    assert r.payout == opt(payout)
    assert bool(r.funded) is funded
    assert (r.signal, r.no_signal_reason) == (sig, reason)
    if flags is not None:
        assert r.flags == frozenset(flags)


def test_g3_corporate_action():
    r = run(price=50.0, dps_ttm=0.0, shares_prev=200e6, shares_t=240e6)
    assert r.dy == approx(0.0)
    assert r.bby == approx(-0.2)
    assert r.sy == approx(-0.2)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "CORPORATE_ACTION")
    assert r.eps_ttm is None and r.payout is None
    assert not r.funded
    assert "NO_TTM" in r.flags


def test_g8_without_split_factor_is_corporate_action():
    kw = dict(GOLDENS["G8"][0], split_factor=1.0)
    r = run(**kw)
    assert r.bby == approx(-0.96)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "CORPORATE_ACTION")
    assert "SPLIT_ADJUSTED" not in r.flags


def test_bby_boundary_negative_side():
    r = run(price=50.0, shares_prev=1000e6, shares_t=1150e6, ni_common_ttm=1e9)
    assert r.bby == approx(-0.15)
    assert r.no_signal_reason is None  # 0.15 exactly passes
    assert r.signal == "SELL"
    r = run(price=50.0, shares_prev=1000e6, shares_t=1151e6, ni_common_ttm=1e9)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "CORPORATE_ACTION")


# ---------------------------------------------------------------------------
# Extra cases from the spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shares", [
    dict(shares_prev=None, shares_t=1000e6), dict(shares_prev=1000e6, shares_t=None),
    dict(shares_prev=None, shares_t=None), dict(shares_prev=0.0, shares_t=1000e6),
    dict(shares_prev=1000e6, shares_t=0.0), dict(shares_prev=-5.0, shares_t=1000e6),
    dict(shares_prev=1000e6, shares_t=-1.0),
])
def test_no_shares(shares):
    r = run(price=80.0, dps_ttm=2.4, ni_common_ttm=4200e6, **shares)
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_SHARES")
    assert r.dy == approx(0.03)  # DY needs no share counts


def test_no_shares_bby_none():
    r = run(price=80.0, dps_ttm=2.4, shares_prev=None, shares_t=1008e6, ni_common_ttm=4200e6)
    assert r.bby is None and r.sy is None
    assert r.eps_ttm == approx(4.166666666666667)


def test_eligible_none_not_eligible_outputs_computed():
    kw = GOLDENS["G1"][0]
    r = run(eligible=None, **kw)
    assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)
    assert r.dy == approx(0.03) and r.bby == approx(0.04) and r.sy == approx(0.07)
    assert r.eps_ttm == approx(4.166666666666667) and r.payout == approx(0.576)


@pytest.mark.parametrize("eligible", [False, None, 1, "True"])
def test_not_eligible_precedence(eligible):
    # NOT_ELIGIBLE beats NO_SHARES and CORPORATE_ACTION
    for kw in (dict(shares_prev=None, shares_t=1e9), GOLDENS["G7b"][0], GOLDENS["G1"][0]):
        r = run(eligible=eligible, **kw)
        assert (r.signal, r.no_signal_reason) == ("NOT_ELIGIBLE", None)


def test_outputs_on_corporate_action():
    r = run(**GOLDENS["G7b"][0])
    assert r.dy == approx(0.0) and r.bby == approx(0.151) and r.sy == approx(0.151)
    assert r.eps_ttm == approx(1e9 / 849e6) and r.payout == approx(0.0)


def test_no_shares_beats_corporate_action():
    # shares_t <= 0 would give |BBY| >= 1 -- NO_SHARES (#2) comes first
    r = run(shares_prev=1000e6, shares_t=0.0, ni_common_ttm=1e9)
    assert r.no_signal_reason == "NO_SHARES"


@pytest.mark.parametrize("args,expected", [
    ((0.0499, True), "HOLD"), ((-0.0299, False), "HOLD"), ((0.06, False), "HOLD"),
    ((0.05, True), "BUY"), ((0.05, False), "HOLD"), ((-0.03, True), "SELL"),
    ((-0.03, False), "SELL"), ((0.5, True), "BUY"), ((0.0, True), "HOLD"), ((-0.5, False), "SELL"),
])
def test_map_signal(args, expected):
    assert map_signal(*args) == expected


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_no_ttm_flag_and_never_buy():
    r = run(price=80.0, dps_ttm=2.4, shares_prev=1050e6, shares_t=1008e6, ni_common_ttm=None)
    assert r.sy == approx(0.07)
    assert r.eps_ttm is None and r.payout is None and not r.funded
    assert r.signal == "HOLD"
    assert {"NO_TTM", "NOT_FUNDED"} <= r.flags


def test_no_ttm_sell_possible():
    r = run(price=50.0, shares_prev=200e6, shares_t=212e6, ni_common_ttm=None)
    assert r.signal == "SELL"
    assert "NO_TTM" in r.flags
    assert "NOT_FUNDED" not in r.flags  # SY < 0.05


def test_not_funded_only_when_sy_reaches_buy():
    r = run(price=100.0, dps_ttm=3.0, shares_prev=1000e6, shares_t=990e6, ni_common_ttm=-100e6)
    assert r.sy == approx(0.04)
    assert r.signal == "HOLD"
    assert "NOT_FUNDED" not in r.flags


def test_payout_exactly_one_is_funded():
    # EPS 5.0 (5000e6 / 1000e6), dps 5.0 -> payout 1.0 = MAX_PAYOUT -> funded
    r = run(price=100.0, dps_ttm=5.0, shares_prev=1000e6, shares_t=1000e6, ni_common_ttm=5000e6)
    assert r.payout == approx(1.0)
    assert r.funded
    assert r.signal == "BUY"


def test_zero_eps_not_funded():
    r = run(price=100.0, dps_ttm=6.0, shares_prev=1000e6, shares_t=1000e6, ni_common_ttm=0.0)
    assert r.eps_ttm == approx(0.0)
    assert r.payout is None
    assert not r.funded
    assert r.signal == "HOLD" and "NOT_FUNDED" in r.flags


@pytest.mark.parametrize("split", [2.0, 0.5, 3.0])
def test_split_adjusted_flag(split):
    r = run(price=40.0, shares_prev=1000e6 / split, split_factor=split, shares_t=1000e6,
            ni_common_ttm=1e9)
    assert r.bby == approx(0.0)
    assert "SPLIT_ADJUSTED" in r.flags


def test_pass_through_flags():
    r = run(flags=frozenset({"PIT_ESTIMATED", "X"}), **GOLDENS["G1"][0])
    assert r.flags == frozenset({"PIT_ESTIMATED", "X"})
    assert r.signal == "BUY"


# ---------------------------------------------------------------------------
# ValueError cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kw", [
    dict(market="NYSE"), dict(market="tsx"), dict(market=""), dict(price=0.0), dict(price=-1.0),
    dict(split_factor=0.0), dict(split_factor=-2.0), dict(fx_rate=0.0), dict(fx_rate=-1.4),
])
def test_value_errors(kw):
    # SPEC-AMBIGUITY: constructor or compute_sy may raise; either is accepted.
    with pytest.raises(ValueError):
        compute_sy(make(**{**GOLDENS["G1"][0], **kw}))


# ---------------------------------------------------------------------------
# Property expectations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(GOLDENS))
@pytest.mark.parametrize("k", [0.1, 3.0, 1000.0])
def test_property_scaling(name, k):
    kw = GOLDENS[name][0]
    base = run(**kw)
    a = run(**{**kw, "price": kw["price"] * k, "dps_ttm": kw["dps_ttm"] * k})
    assert a.dy == approx(base.dy)
    b = run(**{**kw, "shares_prev": kw["shares_prev"] * k, "shares_t": kw["shares_t"] * k})
    assert b.bby == approx(base.bby)
    # reporting currency: NI / c with fx * c
    c = run(**{**kw, "ni_common_ttm": kw["ni_common_ttm"] / k,
               "fx_rate": kw.get("fx_rate", 1.0) * k})
    assert c.eps_ttm == approx(base.eps_ttm)
    assert c.signal == base.signal


ORDER = {"SELL": 0, "HOLD": 1, "BUY": 2}


@pytest.mark.parametrize("funded_ni", [5000e6, -100e6, None])
def test_property_monotone_in_dy_and_bby(funded_ni):
    # shares_t 1000e6 fixed so EPS (and funded) is fixed; vary dps (DY) and shares_prev (BBY).
    dps_grid = [0.0, 0.5, 1.0, 2.0, 3.0, 4.0]
    prev_grid = [870e6, 900e6, 960e6, 990e6, 1000e6, 1010e6, 1030e6, 1060e6, 1100e6, 1170e6]
    for dps in dps_grid:
        labels = []
        for prev in sorted(prev_grid):  # increasing shares_prev -> increasing BBY
            r = run(price=100.0, dps_ttm=dps, shares_prev=prev, shares_t=1000e6,
                    ni_common_ttm=funded_ni)
            if r.signal in ORDER:
                labels.append(ORDER[r.signal])
        assert labels == sorted(labels), dps
    for prev in prev_grid:
        labels = []
        for dps in dps_grid:
            r = run(price=100.0, dps_ttm=dps, shares_prev=prev, shares_t=1000e6,
                    ni_common_ttm=funded_ni)
            if r.signal in ORDER:
                labels.append(ORDER[r.signal])
        assert labels == sorted(labels), prev


@pytest.mark.parametrize("dps,prev", list(itertools.product(
    [0.0, 1.0, 5.0, 20.0], [850e6, 900e6, 1000e6, 1100e6])))
def test_property_ni_none_never_buy(dps, prev):
    r = run(price=100.0, dps_ttm=dps, shares_prev=prev, shares_t=1000e6, ni_common_ttm=None)
    assert r.signal != "BUY"
    assert r.signal in LABELS


def test_labels_never_screen_prefixed():
    for kw, *_ in GOLDENS.values():
        r = run(**kw)
        assert r.signal in LABELS and not r.signal.startswith("SCREEN_")
        assert (r.no_signal_reason is not None) == (r.signal == "NO_SIGNAL")


# ---------------------------------------------------------------------------
# sy_percentiles (informational sy_pct)
# ---------------------------------------------------------------------------


def test_sy_percentiles_ties():
    got = sy_percentiles({"a": 0.01, "b": 0.05, "c": 0.05, "d": -0.02})
    # ranks d 1, a 2, b 3.5, c 3.5 -> (rank - 1)/(n - 1)
    assert got == {"d": approx(0.0), "a": approx(1 / 3), "b": approx(2.5 / 3),
                   "c": approx(2.5 / 3)}


def test_sy_percentiles_ordered():
    got = sy_percentiles({"x": 0.3, "y": -0.1, "z": 0.1})
    assert got == {"y": approx(0.0), "z": approx(0.5), "x": approx(1.0)}


def test_sy_percentiles_small():
    assert sy_percentiles({"a": 0.07}) == {"a": None}
    assert sy_percentiles({}) == {}
