"""Spec tests for Model B v0.2.0 (docs/models/peer_regression.md, revision round v2).

Written from the spec only: G5 (payments / exchanges leave the book branch), the
EV-sub-industry property, STATUS, EV_SUB_INDUSTRIES and MODEL_VERSION. G2 inputs and
expected rows are reused from tests/unit/test_peer_regression_spec.py (spec G2 table).
"""

from __future__ import annotations

import dataclasses
import random

import pytest
from test_peer_regression_spec import (  # same-directory spec module (G2 data)
    G2_EXPECTED,
    G2_INTERCEPT,
    G2_R2,
    G2_SLOPE,
    approx,
    assert_fit_equal,
    g2_obs,
)

from valuation.models.peer_regression import (
    EV_SUB_INDUSTRIES,
    MODEL_VERSION,
    STATUS,
    PBObs,
    run_peer_regression,
)

EXCH = "Financial Exchanges & Data"
PAY = "Transaction & Payment Processing Services"
RESULT_FIELDS = ("ticker", "sector", "signal", "no_signal_reason", "pb", "roe", "roe_w",
                 "fitted_pb", "fair_value", "residual", "resid_pct", "implied_upside", "n_fit",
                 "r2", "flags")


def u19():
    return PBObs("U19", "Financials", 300.0, 20.0, 0.45, True, sub_industry=EXCH)


def u20():
    return PBObs("U20", "Financials", 400.0, 10.0, 0.60, True, sub_industry=PAY)


def assert_result_equal(a, b):
    for f in RESULT_FIELDS:
        va, vb = getattr(a, f), getattr(b, f)
        if isinstance(va, float) or isinstance(vb, float):
            assert va is not None and vb is not None, f
            assert va == approx(vb), (a.ticker, f)
        else:
            assert va == vb, (a.ticker, f)


@pytest.fixture(scope="module")
def g2():
    return run_peer_regression(g2_obs())


@pytest.fixture(scope="module")
def g5():
    return run_peer_regression(g2_obs() + [u19(), u20()])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_model_version_status_ev_set():
    assert MODEL_VERSION == "peer_regression/0.2.0"
    assert STATUS == "diagnostic"
    assert EV_SUB_INDUSTRIES == frozenset({PAY, EXCH})


def test_pbobs_sub_industry_default():
    o = PBObs("X", "Financials", 10.0, 5.0, 0.1, True)
    assert o.sub_industry is None
    o = PBObs("X", "Financials", 10.0, 5.0, 0.1, True, frozenset(), False, EXCH)
    assert o.sub_industry == EXCH
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.sub_industry = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# G5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ticker,pb", [("U19", 15.0), ("U20", 40.0)])
def test_g5_ev_names_pending(g5, ticker, pb):
    r = {x.ticker: x for x in g5[0]}[ticker]
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EV_BRANCH_PENDING")
    assert r.pb == approx(pb)
    for f in ("roe_w", "fitted_pb", "fair_value", "residual", "resid_pct", "implied_upside"):
        assert getattr(r, f) is None, f
    assert "ROE_WINSORIZED" not in r.flags


def test_g5_fit_identical_to_g2(g2, g5):
    f5, f2 = g5[1]["Financials"], g2[1]["Financials"]
    assert set(g5[1]) == {"Financials"}
    assert f5.n_fit == 14
    assert f5.n_scored == 15
    assert f5.slope == approx(G2_SLOPE)
    assert f5.slope == approx(7.640957345191646)
    assert f5.intercept == approx(G2_INTERCEPT)
    assert f5.r2 == approx(G2_R2)
    assert f5.status == "OK"
    assert_fit_equal(f5, f2)


def test_g5_other_rows_identical_to_g2(g2, g5):
    r2 = {x.ticker: x for x in g2[0]}
    r5 = {x.ticker: x for x in g5[0]}
    assert [x.ticker for x in g5[0]] == [o.ticker for o in g2_obs()] + ["U19", "U20"]
    for t, r in r2.items():
        assert_result_equal(r5[t], r)
    for t, exp in G2_EXPECTED.items():
        assert r5[t].signal == exp[6], t


@pytest.mark.parametrize("sub", ["Asset Management & Custody Banks", None,
                                 "financial exchanges & data"])
def test_g5_other_sub_industry_unchanged_treatment(sub):
    # Not an EV sub-industry -> v0.1.0 treatment: the name enters F (here n_fit 15).
    extra = PBObs("U21", "Financials", 30.0, 20.0, 0.10, True, sub_industry=sub)
    res, fits = run_peer_regression(g2_obs() + [extra])
    assert fits["Financials"].n_fit == 15
    r = {x.ticker: x for x in res}["U21"]
    assert r.no_signal_reason is None
    assert r.fitted_pb is not None


def test_ev_name_in_non_book_sector_is_pending():
    res, fits = run_peer_regression([PBObs("V", "Information Technology", 300.0, 10.0, 0.3, True,
                                           sub_industry=PAY)])
    assert (res[0].signal, res[0].no_signal_reason) == ("NO_SIGNAL", "EV_BRANCH_PENDING")
    assert fits == {}


def test_ev_name_precedence():
    # NOT_ELIGIBLE and NO_SECTOR come first; EV_BRANCH_PENDING beats NEEDS_CSM / NEG_BOOK / NO_ROE.
    obs = [
        PBObs("A", "Financials", 10.0, 5.0, 0.1, False, sub_industry=PAY),
        PBObs("B", "Financials", 10.0, None, None, True, needs_csm=True, sub_industry=EXCH),
        PBObs("C", "Financials", 10.0, -5.0, 0.1, True, sub_industry=EXCH),
        PBObs("D", "Financials", 10.0, 5.0, None, True, sub_industry=PAY),
    ]
    res = {r.ticker: r for r in run_peer_regression(obs)[0]}
    assert (res["A"].signal, res["A"].no_signal_reason) == ("NOT_ELIGIBLE", None)
    for t in "BCD":
        assert (res[t].signal, res[t].no_signal_reason) == ("NO_SIGNAL", "EV_BRANCH_PENDING"), t


# ---------------------------------------------------------------------------
# Property: EV names never change any fit or other name's result
# ---------------------------------------------------------------------------


def _random_ev(rng, i):
    return PBObs(f"EV{i}", rng.choice(["Financials", "Utilities", "Energy", None]),
                 rng.uniform(1, 500), rng.choice([None, -3.0, rng.uniform(0.5, 50)]),
                 rng.choice([None, rng.uniform(-0.3, 1.5)]), rng.choice([True, False, None]),
                 frozenset(), rng.choice([False, True]), rng.choice(sorted(EV_SUB_INDUSTRIES)))


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_property_ev_names_change_nothing(g2, seed):
    rng = random.Random(seed)
    base = g2_obs()
    extra = [_random_ev(rng, i) for i in range(rng.randint(1, 6))]
    obs = base[:]
    for e in extra:
        obs.insert(rng.randint(0, len(obs)), e)
    res, fits = run_peer_regression(obs)
    ref_res, ref_fits = g2
    by = {r.ticker: r for r in res}
    for r in ref_res:
        assert_result_equal(by[r.ticker], r)
    # Only Financials has book names in G2; an EV name in another book sector may add a
    # (FEW_NAMES, n_fit 0) fit for that sector, but Financials is unchanged.
    assert_fit_equal(fits["Financials"], ref_fits["Financials"])
    for s, f in fits.items():
        if s != "Financials":
            assert f.n_fit == 0 and f.n_scored == 0 and f.status == "FEW_NAMES"
    for e in extra:
        r = by[e.ticker]
        assert r.signal in {"NOT_ELIGIBLE", "NO_SIGNAL"}
        assert r.fitted_pb is None and r.resid_pct is None
