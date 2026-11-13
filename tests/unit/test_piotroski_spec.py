"""Spec tests for Model E, Piotroski F-score (docs/models/piotroski.md v0.1.0).

Written from the spec only (not from the implementation). Goldens E1-E13 are the spec's worked
examples; relative tolerance 1e-9 (absolute 1e-12 for zero). API fixed by the spec:
    EInputs (frozen dataclass), EResult(ticker, market, signal, no_signal_reason, f_score,
        n_scored, signals, roa, roa_prior, lev_t, lev_prior, cr_t, cr_prior, shares_prev_adj,
        gm_t, gm_prior, ato_t, ato_prior, flags)
    compute_metrics(inp) -> EMetrics(signals, f_score, n_scored, roa, roa_prior, lev_t,
        lev_prior, cr_t, cr_prior, shares_prev_adj, gm_t, gm_prior, ato_t, ato_prior,
        prior_ta_fallback)
    map_signal(f_score, n_scored) -> "BUY" | "HOLD" | "SELL" (ValueError unless n is 8 or 9)
    evaluate(inp) -> EResult;  run_piotroski(inputs) -> list[EResult] (same order)
`hypothesis` is not a dependency, so the property tests are seeded random loops; they also
compare against an independent re-implementation of §Formulas written from the spec text.

The module is imported softly so this file collects before Model E exists; every test fails
(AssertionError) until it is built.
"""

from __future__ import annotations

import dataclasses
import random

import pytest


class _Missing:
    def __getattr__(self, name):
        raise AssertionError("src/valuation/models/piotroski.py (Model E) is not implemented yet")


try:
    from valuation.models import piotroski as pm
except ImportError:  # not built yet: every test fails with a clear message
    pm = _Missing()

REL = 1e-9
E_LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
FLOW = ("ni_ttm", "ni_ttm_prior", "cfo_ttm", "gp_ttm", "gp_ttm_prior", "rev_ttm",
        "rev_ttm_prior")
BAL = ("ta_t", "ta_4q", "ta_8q", "debt_t", "debt_4q", "ca_t", "cl_t", "ca_4q", "cl_4q")
MONEY = FLOW + BAL
SHARES = ("shares_t", "shares_4q")
RATIOS = ("roa", "roa_prior", "lev_t", "lev_prior", "cr_t", "cr_prior", "shares_prev_adj",
          "gm_t", "gm_prior", "ato_t", "ato_prior")
RESULT_FIELDS = ("ticker", "market", "signal", "no_signal_reason", "f_score", "n_scored",
                 "signals") + RATIOS + ("flags",)
PASS_FLAGS = frozenset({"PIT_ESTIMATED", "E_PERIOD_MISMATCH", "FLOW_CCY_MISMATCH",
                        "PRIOR_CCY_MISMATCH"})
FB = "PRIOR_TA_FALLBACK"

# E1 (proposal golden), $M
E1 = dict(ni_ttm=120.0, ni_ttm_prior=100.0, cfo_ttm=150.0, ta_t=1000.0, ta_4q=950.0,
          ta_8q=900.0, debt_t=300.0, debt_4q=310.0, ca_t=400.0, cl_t=200.0, ca_4q=380.0,
          cl_4q=200.0, shares_t=100e6, shares_4q=101e6, gp_ttm=420.0, rev_ttm=1200.0,
          gp_ttm_prior=400.0, rev_ttm_prior=1150.0)
E6 = dict(ni_ttm=-50.0, ni_ttm_prior=-20.0, cfo_ttm=-30.0, ta_t=1000.0, ta_4q=950.0,
          ta_8q=900.0, debt_t=400.0, debt_4q=310.0, ca_t=300.0, cl_t=250.0, ca_4q=380.0,
          cl_4q=200.0, shares_t=110e6, shares_4q=100e6, gp_ttm=300.0, rev_ttm=1200.0,
          gp_ttm_prior=400.0, rev_ttm_prior=1150.0)
ALL_NONE = {k: None for k in MONEY + SHARES}

E1_RATIOS = dict(roa=0.12631578947368421, roa_prior=0.1111111111111111, lev_t=0.3,
                 lev_prior=0.3263157894736842, cr_t=2.0, cr_prior=1.9,
                 shares_prev_adj=101000000.0, gm_t=0.35, gm_prior=0.34782608695652173,
                 ato_t=1.263157894736842, ato_prior=1.2777777777777777)
E6_RATIOS = dict(roa=-0.05263157894736842, roa_prior=-0.022222222222222223, lev_t=0.4,
                 lev_prior=0.3263157894736842, cr_t=1.2, cr_prior=1.9,
                 shares_prev_adj=100000000.0, gm_t=0.25, gm_prior=0.34782608695652173,
                 ato_t=1.263157894736842, ato_prior=1.2777777777777777)


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def make(base=E1, ticker="TEST", market="US", sector="Industrials", eligible=True,
         split_factor=1.0, flags=frozenset(), **changes):
    vals = {k: None for k in MONEY + SHARES}
    vals.update(base)
    vals.update(changes)
    return pm.EInputs(ticker=ticker, market=market, sector=sector, eligible=eligible,
                      split_factor=split_factor, flags=frozenset(flags), **vals)


def sig(s):
    """Spec signal string ('1', '0', '-') -> tuple of 1 / 0 / None."""
    assert len(s) == 9
    return tuple(None if c == "-" else int(c) for c in s)


def sigstr(t):
    return "".join("-" if v is None else str(v) for v in t)


def result_tuple(r):
    return tuple(tuple(getattr(r, f)) if f == "signals" else
                 frozenset(getattr(r, f)) if f == "flags" else getattr(r, f)
                 for f in RESULT_FIELDS)


def check(r, signals, f, n, signal, reason=None, flags=frozenset()):
    assert sigstr(tuple(r.signals)) == signals
    assert len(tuple(r.signals)) == 9
    assert r.f_score == f
    assert r.n_scored == n
    assert r.signal == signal
    assert r.no_signal_reason == reason
    assert set(r.flags) == set(flags)


def check_ratios(r, expected):
    for k, v in expected.items():
        got = getattr(r, k)
        if v is None:
            assert got is None, k
        else:
            assert got == approx(v), k


# ---------------------------------------------------------------------------------------------
# Constants and API
# ---------------------------------------------------------------------------------------------

def test_constants():
    assert pm.MODEL_VERSION == "piotroski/0.1.0"
    assert pm.N_SIGNALS == 9
    assert pm.MIN_SCORED == 8
    assert pm.BUY_F_9 == 8
    assert pm.BUY_F_8 == 7
    assert pm.SELL_F == 2
    assert pm.SHARE_TOL_NUM == 201
    assert pm.SHARE_TOL_DEN == 200
    assert tuple(pm.PRIOR_WINDOW_DAYS) == (350, 380)
    assert tuple(pm.PRIOR2_WINDOW_DAYS) == (715, 745)
    assert pm.EXCLUDED_SECTORS == frozenset({"Financials"})


def test_inputs_frozen_dataclass_and_defaults():
    inp = pm.EInputs(ticker="X", market="US", sector="Industrials", eligible=True)
    assert dataclasses.is_dataclass(inp)
    for k in MONEY + SHARES:
        assert getattr(inp, k) is None, k  # no monetary input is defaulted to 0
    assert inp.split_factor == 1.0
    assert frozenset(inp.flags) == frozenset()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.ni_ttm = 1.0  # type: ignore[misc]


def test_result_and_metrics_fields():
    r = pm.evaluate(make())
    for f in RESULT_FIELDS:
        assert hasattr(r, f), f
    m = pm.compute_metrics(make())
    for f in ("signals", "f_score", "n_scored", "prior_ta_fallback") + RATIOS:
        assert hasattr(m, f), f


@pytest.mark.parametrize("market", ["LSE", "", None, "us"])
def test_bad_market_value_error(market):
    # E12i. SPEC-AMBIGUITY: the spec does not say whether EInputs or evaluate raises;
    # construction happens inside the raises block so either is accepted.
    with pytest.raises(ValueError):
        pm.evaluate(make(market=market))


@pytest.mark.parametrize("sf", [0.0, -1.0])
def test_split_factor_non_positive_value_error(sf):
    # E9: split_factor 0 or -1 -> ValueError (same construction/evaluate ambiguity as market).
    with pytest.raises(ValueError):
        pm.evaluate(make(split_factor=sf))


# ---------------------------------------------------------------------------------------------
# E1, E2: proposal goldens
# ---------------------------------------------------------------------------------------------

def test_e1_proposal_golden():
    r = pm.evaluate(make())
    check(r, "111111110", 8, 9, "BUY")
    check_ratios(r, E1_RATIOS)
    assert (r.ticker, r.market) == ("TEST", "US")
    assert isinstance(tuple(r.signals)[0], int)


def test_e1_metrics_and_bit_exact_reprs():
    m = pm.compute_metrics(make())
    assert tuple(m.signals) == sig("111111110")
    assert (m.f_score, m.n_scored, m.prior_ta_fallback) == (8, 9, False)
    # §Evaluation order: one division each, so the spec's reprs reproduce bit for bit
    for k, v in E1_RATIOS.items():
        assert getattr(m, k) == v, k


def test_e2_fallback_variant():
    r = pm.evaluate(make(ta_8q=None))
    check(r, "111111111", 9, 9, "BUY", flags={FB})
    check_ratios(r, {**E1_RATIOS, "roa_prior": 0.10526315789473684,
                     "ato_prior": 1.2105263157894737})
    assert pm.compute_metrics(make(ta_8q=None)).prior_ta_fallback is True


def test_e2b_fallback_feeds_signal_9_only():
    r = pm.evaluate(make(ta_8q=None, ni_ttm_prior=None))
    check(r, "11-111111", 8, 8, "BUY", flags={FB})
    assert r.roa_prior is None
    assert r.ato_prior == approx(1.2105263157894737)


# ---------------------------------------------------------------------------------------------
# E3-E8
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sector", ["Real Estate", "Industrials"])
def test_e3_n8_without_gross_profit(sector):
    r = pm.evaluate(make(sector=sector, gp_ttm=None, gp_ttm_prior=None))
    check(r, "1111111-0", 7, 8, "BUY")
    assert r.gm_t is None and r.gm_prior is None


def test_e4_n8_f6_tie_hold():
    r = pm.evaluate(make(sector="Real Estate", gp_ttm=None, gp_ttm_prior=None, ca_t=380.0))
    check(r, "1111101-0", 6, 8, "HOLD")
    assert r.cr_t == approx(1.9) and r.cr_prior == approx(1.9)


def test_e5_n9_f7_hold():
    check(pm.evaluate(make(ca_t=380.0)), "111110110", 7, 9, "HOLD")


def test_e6_sell():
    r = pm.evaluate(make(E6))
    check(r, "000100000", 1, 9, "SELL")
    check_ratios(r, E6_RATIOS)


def test_e6b_sell_edge():
    check(pm.evaluate(make(E6, cfo_ttm=10.0)), "010100000", 2, 9, "SELL")


def test_e6c_hold():
    r = pm.evaluate(make(E6, cfo_ttm=10.0, rev_ttm=1300.0))
    check(r, "010100001", 3, 9, "HOLD")
    assert r.gm_t == approx(0.23076923076923078)
    assert r.ato_t == approx(1.368421052631579)


def test_e6d_sell_at_n8():
    r = pm.evaluate(make(E6, cfo_ttm=10.0, gp_ttm=None))
    check(r, "0101000-0", 2, 8, "SELL")
    assert r.gm_t is None


def test_e7_n7_incomplete_ratios_still_output():
    r = pm.evaluate(make(gp_ttm=None, cl_t=None))
    check(r, "11111-1-0", 6, 7, "NO_SIGNAL", "F_INCOMPLETE")
    assert r.cr_t is None and r.gm_t is None
    assert r.gm_prior == approx(0.34782608695652173)
    assert r.cr_prior == approx(1.9)


def test_e8_typical_tsx_name_no_fallback_flag():
    r = pm.evaluate(make(market="TSX", ni_ttm_prior=None, gp_ttm_prior=None,
                         rev_ttm_prior=None, ta_8q=None, flags={"PIT_ESTIMATED"}))
    check(r, "11-1111--", 6, 6, "NO_SIGNAL", "F_INCOMPLETE", flags={"PIT_ESTIMATED"})
    assert r.market == "TSX"
    assert r.roa_prior is None and r.gm_prior is None and r.ato_prior is None
    assert pm.compute_metrics(make(ni_ttm_prior=None, gp_ttm_prior=None, rev_ttm_prior=None,
                                   ta_8q=None)).prior_ta_fallback is False


# ---------------------------------------------------------------------------------------------
# E9: signal 7 boundary (IEEE)
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("shares_t, shares_4q, sf, signals, f, n, label, prev_adj", [
    (100.5e6, 100e6, 1.0, "111111110", 8, 9, "BUY", 100000000.0),          # a: exactly +0.5%
    (100500001.0, 100e6, 1.0, "111111010", 7, 9, "HOLD", 100000000.0),     # b
    (100.5e6, 50e6, 2.0, "111111110", 8, 9, "BUY", 100000000.0),           # c: 2:1 split
    (101505000.0, 101e6, 1.0, "111111110", 8, 9, "BUY", 101000000.0),      # d
    (100e6, 0.0, 1.0, "111111-10", 7, 8, "BUY", None),                     # e: shares_4q 0
    (100e6, None, 1.0, "111111-10", 7, 8, "BUY", None),                    # e: shares_4q None
    (None, 101e6, 1.0, "111111-10", 7, 8, "BUY", 101000000.0),             # f: shares_t None
    (0.0, 101e6, 1.0, "111111-10", 7, 8, "BUY", 101000000.0),              # shares_t not > 0
    (100e6, -5.0, 1.0, "111111-10", 7, 8, "BUY", None),                    # shares_4q <= 0
], ids=["a", "b", "c", "d", "e0", "eNone", "f", "t0", "4qneg"])
def test_e9_signal7_boundary(shares_t, shares_4q, sf, signals, f, n, label, prev_adj):
    r = pm.evaluate(make(shares_t=shares_t, shares_4q=shares_4q, split_factor=sf))
    check(r, signals, f, n, label)
    if prev_adj is None:
        assert r.shares_prev_adj is None
    else:
        assert r.shares_prev_adj == prev_adj  # exact (whole numbers)


def test_e9a_naive_form_would_fail():
    # Documents the spec's reason for the cross-multiplied form.
    assert not (100.5e6 <= 100e6 * 1.005)
    assert 100.5e6 * 200 <= 100e6 * 201
    assert tuple(pm.evaluate(make(shares_t=100.5e6, shares_4q=100e6)).signals)[6] == 1


def test_e9_no_shares_limit_output():
    assert not hasattr(pm.evaluate(make()), "shares_limit")


# ---------------------------------------------------------------------------------------------
# E10: ties (strict) and E11: guards
# ---------------------------------------------------------------------------------------------

E10 = {
    "a": (dict(debt_4q=285.0), "111101110", 7, "HOLD"),
    "b": (dict(debt_t=0.0, debt_4q=0.0), "111101110", 7, "HOLD"),
    "c": (dict(debt_t=0.0), "111111110", 8, "BUY"),
    "d": (dict(gp_ttm_prior=402.5), "111111100", 7, "HOLD"),
    "e": (dict(ni_ttm_prior=120.0, ta_8q=950.0), "110111111", 8, "BUY"),
    "f": (dict(rev_ttm_prior=1200.0, ta_8q=950.0), "111111110", 8, "BUY"),
    "g": (dict(cfo_ttm=120.0), "111011110", 7, "HOLD"),
    "h": (dict(cfo_ttm=0.0), "101011110", 6, "HOLD"),
    "i": (dict(ni_ttm=0.0), "010111110", 6, "HOLD"),
}


@pytest.mark.parametrize("case", sorted(E10))
def test_e10_ties_score_zero(case):
    changes, signals, f, label = E10[case]
    r = pm.evaluate(make(**changes))
    check(r, signals, f, 9, label)


def test_e10_tie_values():
    r = pm.evaluate(make(debt_4q=285.0))
    assert r.lev_t == r.lev_prior == 0.3
    r = pm.evaluate(make(debt_t=0.0, debt_4q=0.0))
    assert r.lev_t == 0.0 and r.lev_prior == 0.0  # 0 debt is a value, not missing
    r = pm.evaluate(make(debt_t=0.0))
    assert r.lev_t == 0.0 and r.lev_prior == approx(0.3263157894736842)
    r = pm.evaluate(make(gp_ttm_prior=402.5))
    assert r.gm_t == r.gm_prior == 0.35
    r = pm.evaluate(make(ni_ttm_prior=120.0, ta_8q=950.0))
    assert r.roa == r.roa_prior == 0.12631578947368421
    assert r.ato_prior == approx(1.2105263157894737)
    r = pm.evaluate(make(rev_ttm_prior=1200.0, ta_8q=950.0))
    assert r.ato_t == r.ato_prior == 1.263157894736842
    assert r.roa_prior == approx(0.10526315789473684)
    assert r.gm_prior == approx(0.3333333333333333)
    r = pm.evaluate(make(ni_ttm=0.0))
    assert r.roa == 0.0


E11 = {
    "a": (dict(ta_4q=0.0), "-1-1-111-", 5, 5, "NO_SIGNAL", "F_INCOMPLETE", set()),
    "b": (dict(ta_4q=0.0, ta_8q=None), "-1-1-111-", 5, 5, "NO_SIGNAL", "F_INCOMPLETE", set()),
    "c0": (dict(ta_8q=0.0), "11-11111-", 7, 7, "NO_SIGNAL", "F_INCOMPLETE", set()),
    "cneg": (dict(ta_8q=-5.0), "11-11111-", 7, 7, "NO_SIGNAL", "F_INCOMPLETE", set()),
    "d": (dict(ta_t=0.0), "1111-1110", 7, 8, "BUY", None, set()),
    "e_cl_t": (dict(cl_t=0.0), "11111-110", 7, 8, "BUY", None, set()),
    "e_cl_4q": (dict(cl_4q=-1.0), "11111-110", 7, 8, "BUY", None, set()),
    "f": (dict(rev_ttm=0.0), "1111111-0", 7, 8, "BUY", None, set()),
    "g": (dict(rev_ttm_prior=0.0), "1111111-1", 8, 8, "BUY", None, set()),
    "h": (dict(ni_ttm=None), "-1--11110", 5, 6, "NO_SIGNAL", "F_INCOMPLETE", set()),
}


@pytest.mark.parametrize("case", sorted(E11))
def test_e11_guards_unscored_never_zero(case):
    changes, signals, f, n, label, reason, flags = E11[case]
    check(pm.evaluate(make(**changes)), signals, f, n, label, reason, flags)


def test_e11a_ratio_details():
    r = pm.evaluate(make(ta_4q=0.0))
    assert r.roa is None and r.lev_prior is None and r.ato_t is None
    assert r.roa_prior == approx(0.1111111111111111)  # ratio output although signal 3 unscored
    assert r.ato_prior == approx(1.2777777777777777)
    r = pm.evaluate(make(ta_4q=0.0, ta_8q=None))
    assert r.roa_prior is None and r.ato_prior is None  # fallback to ta_4q = 0 fails the guard


def test_e11c_present_non_positive_ta_8q_not_replaced():
    for v in (0.0, -5.0):
        r = pm.evaluate(make(ta_8q=v))
        assert r.roa_prior is None and r.ato_prior is None
        assert FB not in set(r.flags)
        assert pm.compute_metrics(make(ta_8q=v)).prior_ta_fallback is False


def test_e11_other_ratio_details():
    assert pm.evaluate(make(ta_t=0.0)).lev_t is None
    r = pm.evaluate(make(cl_4q=-1.0))
    assert r.cr_prior is None and r.cr_t == approx(2.0)
    assert pm.evaluate(make(cl_t=0.0)).cr_t is None
    r = pm.evaluate(make(rev_ttm=0.0))
    assert r.gm_t is None and r.ato_t == 0.0
    r = pm.evaluate(make(rev_ttm_prior=0.0))
    assert r.gm_prior is None and r.ato_prior == 0.0


def test_e11i_everything_none():
    r = pm.evaluate(make(ALL_NONE))
    check(r, "---------", None, 0, "NO_SIGNAL", "F_INCOMPLETE")
    for k in RATIOS:
        assert getattr(r, k) is None, k
    m = pm.compute_metrics(make(ALL_NONE))
    assert m.f_score is None and m.n_scored == 0 and m.prior_ta_fallback is False


# ---------------------------------------------------------------------------------------------
# E12: precedence; run_piotroski
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("eligible", [False, None])
def test_e12a_not_eligible_metrics_output(eligible):
    r = pm.evaluate(make(eligible=eligible, sector="Financials"))
    check(r, "111111110", 8, 9, "NOT_ELIGIBLE", None)
    check_ratios(r, E1_RATIOS)


def test_e12a_not_eligible_non_bool_truthy():
    # "anything but True" -> NOT_ELIGIBLE
    assert pm.evaluate(make(eligible=1)).signal == "NOT_ELIGIBLE"  # type: ignore[arg-type]


def test_e12b_not_eligible_before_incomplete():
    r = pm.evaluate(make(eligible=False, gp_ttm=None, cl_t=None))
    check(r, "11111-1-0", 6, 7, "NOT_ELIGIBLE", None)


def test_e12c_financials_excluded_metrics_output():
    r = pm.evaluate(make(sector="Financials"))
    check(r, "111111110", 8, 9, "NO_SIGNAL", "EXCLUDED_SECTOR")
    check_ratios(r, E1_RATIOS)


@pytest.mark.parametrize("sector", [None, ""])
def test_e12d_missing_sector_excluded(sector):
    r = pm.evaluate(make(sector=sector))
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert (r.f_score, r.n_scored) == (8, 9)


def test_e12e_excluded_before_incomplete():
    r = pm.evaluate(make(ALL_NONE, sector="Financials"))
    check(r, "---------", None, 0, "NO_SIGNAL", "EXCLUDED_SECTOR")


def test_e12f_excluded_fallback_flag_still_set():
    r = pm.evaluate(make(sector="Financials", ta_8q=None))
    check(r, "111111111", 9, 9, "NO_SIGNAL", "EXCLUDED_SECTOR", flags={FB})


def test_e12f_not_eligible_fallback_flag_still_set():
    r = pm.evaluate(make(eligible=False, ta_8q=None))
    check(r, "111111111", 9, 9, "NOT_ELIGIBLE", None, flags={FB})


def test_e12g_incomplete():
    r = pm.evaluate(make(gp_ttm=None, cl_t=None))
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "F_INCOMPLETE")


@pytest.mark.parametrize("sector", ["Real Estate", "Utilities"])
def test_e12h_real_estate_utilities_scored(sector):
    check(pm.evaluate(make(sector=sector)), "111111110", 8, 9, "BUY")


def test_run_piotroski_order_and_independence():
    inputs = [make(ticker="E1"), make(ticker="E7", gp_ttm=None, cl_t=None),
              make(ticker="E12A", eligible=False, sector="Financials"),
              make(E6, ticker="E6")]
    out = pm.run_piotroski(inputs)
    assert isinstance(out, list) and len(out) == 4
    assert [r.ticker for r in out] == ["E1", "E7", "E12A", "E6"]
    assert [r.signal for r in out] == ["BUY", "NO_SIGNAL", "NOT_ELIGIBLE", "SELL"]
    for inp, r in zip(inputs, out):
        assert result_tuple(r) == result_tuple(pm.evaluate(inp))
    rev = pm.run_piotroski(list(reversed(inputs)))
    assert [result_tuple(r) for r in rev] == [result_tuple(r) for r in reversed(out)]
    assert pm.run_piotroski([]) == []


def test_run_piotroski_markets_independent():
    a = make(ticker="A", market="TSX")
    b = make(E6, ticker="B", market="US")
    alone = result_tuple(pm.run_piotroski([a])[0])
    assert result_tuple(pm.run_piotroski([b, a, b])[1]) == alone


def test_run_piotroski_duplicate_tickers_one_result_each():
    inputs = [make(ticker="X"), make(E6, ticker="X")]
    out = pm.run_piotroski(inputs)
    assert [r.signal for r in out] == ["BUY", "SELL"]


# ---------------------------------------------------------------------------------------------
# E13: map_signal
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("f, n, expected", [
    (8, 9, "BUY"), (7, 9, "HOLD"), (9, 9, "BUY"), (7, 8, "BUY"), (6, 8, "HOLD"), (8, 8, "BUY"),
    (2, 9, "SELL"), (3, 9, "HOLD"), (2, 8, "SELL"), (3, 8, "HOLD"), (0, 9, "SELL"),
    (0, 8, "SELL"), (1, 8, "SELL"), (6, 9, "HOLD"),
])
def test_e13_map_signal(f, n, expected):
    assert pm.map_signal(f, n) == expected


@pytest.mark.parametrize("f, n", [(5, 7), (9, 10), (0, 0), (2, 1), (6, 6)])
def test_e13_map_signal_value_error(f, n):
    with pytest.raises(ValueError):
        pm.map_signal(f, n)


# ---------------------------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------------------------

def test_pass_through_flags():
    r = pm.evaluate(make(flags=PASS_FLAGS))
    assert set(r.flags) == set(PASS_FLAGS)
    assert r.signal == "BUY"  # pass-through flags do not change the signal
    r = pm.evaluate(make(flags=PASS_FLAGS, ta_8q=None))
    assert set(r.flags) == set(PASS_FLAGS) | {FB}


def test_e_period_and_prior_ccy_mismatch_model_semantics():
    # SPEC-AMBIGUITY: at model level E_PERIOD_MISMATCH / PRIOR_CCY_MISMATCH are only passed
    # through (the engine sets the affected inputs to None); the model never adds or removes
    # them and they do not affect the signal by themselves.
    for flag in ("E_PERIOD_MISMATCH", "PRIOR_CCY_MISMATCH", "FLOW_CCY_MISMATCH"):
        r = pm.evaluate(make(flags={flag}))
        assert set(r.flags) == {flag} and r.signal == "BUY"
        r = pm.evaluate(make(flags={flag}, ni_ttm=None))  # how the engine maps it
        check(r, "-1--11110", 5, 6, "NO_SIGNAL", "F_INCOMPLETE", flags={flag})
    assert set(pm.evaluate(make()).flags) == set()


def test_prior_ccy_mismatch_engine_mapping_t4q_shares_kept():
    # Engine mapping of PRIOR_CCY_MISMATCH on the t-4q filing: monetary items None, shares kept.
    r = pm.evaluate(make(ta_4q=None, debt_4q=None, ca_4q=None, cl_4q=None,
                         flags={"PRIOR_CCY_MISMATCH"}))
    check(r, "-1-1--11-", 4, 4, "NO_SIGNAL", "F_INCOMPLETE", flags={"PRIOR_CCY_MISMATCH"})


def test_fallback_flag_only_when_scored_3_or_9():
    cases = [
        (dict(ta_8q=None), True),
        (dict(ta_8q=None, ni_ttm_prior=None), True),                  # signal 9 only
        (dict(ta_8q=None, rev_ttm_prior=None), True),                 # signal 3 only
        (dict(ta_8q=None, ni_ttm_prior=None, rev_ttm_prior=None), False),
        (dict(ta_8q=None, ni_ttm=None, rev_ttm=None), False),         # roa, ato_t None
        (dict(ta_8q=None, ta_4q=None), False),                        # nothing to fall back to
        (dict(ta_8q=0.0), False),
        (dict(), False),
    ]
    for changes, expected in cases:
        r = pm.evaluate(make(**changes))
        assert (FB in set(r.flags)) is expected, changes
        assert pm.compute_metrics(make(**changes)).prior_ta_fallback is expected, changes


def test_reserved_and_reason_flags_never_set():
    for inp in _golden_inputs():
        flags = set(pm.evaluate(inp).flags)
        assert "ANNUAL_PRIOR" not in flags
        assert "F_INCOMPLETE" not in flags  # reason only
        assert "EXCLUDED_SECTOR" not in flags


# ---------------------------------------------------------------------------------------------
# Property expectations
# ---------------------------------------------------------------------------------------------

def _golden_inputs():
    out = [make(), make(ta_8q=None), make(ta_8q=None, ni_ttm_prior=None),
           make(sector="Real Estate", gp_ttm=None, gp_ttm_prior=None), make(ca_t=380.0),
           make(E6), make(E6, cfo_ttm=10.0), make(E6, cfo_ttm=10.0, rev_ttm=1300.0),
           make(E6, cfo_ttm=10.0, gp_ttm=None), make(gp_ttm=None, cl_t=None),
           make(ALL_NONE), make(eligible=False), make(sector="Financials", ta_8q=None)]
    out += [make(**c[0]) for c in E10.values()]
    out += [make(**c[0]) for c in E11.values()]
    return out


def _q(a, b):
    return a / b if a is not None and b is not None and b > 0 else None


def _reference(d, eligible=True, sector="Industrials", split_factor=1.0):
    """Independent re-implementation of §Formulas / §Signal and precedence."""
    g = {k: d.get(k) for k in MONEY + SHARES}
    ta8 = g["ta_8q"] if g["ta_8q"] is not None else g["ta_4q"]
    roa, roa_p = _q(g["ni_ttm"], g["ta_4q"]), _q(g["ni_ttm_prior"], ta8)
    lev_t, lev_p = _q(g["debt_t"], g["ta_t"]), _q(g["debt_4q"], g["ta_4q"])
    cr_t, cr_p = _q(g["ca_t"], g["cl_t"]), _q(g["ca_4q"], g["cl_4q"])
    gm_t, gm_p = _q(g["gp_ttm"], g["rev_ttm"]), _q(g["gp_ttm_prior"], g["rev_ttm_prior"])
    ato_t, ato_p = _q(g["rev_ttm"], g["ta_4q"]), _q(g["rev_ttm_prior"], ta8)
    s4 = g["shares_4q"]
    prev = s4 * split_factor if s4 is not None and s4 > 0 else None
    cfo, ni, st = g["cfo_ttm"], g["ni_ttm"], g["shares_t"]

    def b(scored, cond):
        return (1 if cond() else 0) if scored else None

    signals = (
        b(roa is not None, lambda: roa > 0),
        b(cfo is not None, lambda: cfo > 0),
        b(roa is not None and roa_p is not None, lambda: roa > roa_p),
        b(cfo is not None and ni is not None, lambda: cfo > ni),
        b(lev_t is not None and lev_p is not None, lambda: lev_t < lev_p),
        b(cr_t is not None and cr_p is not None, lambda: cr_t > cr_p),
        b(st is not None and st > 0 and prev is not None, lambda: st * 200 <= prev * 201),
        b(gm_t is not None and gm_p is not None, lambda: gm_t > gm_p),
        b(ato_t is not None and ato_p is not None, lambda: ato_t > ato_p),
    )
    scored = [s for s in signals if s is not None]
    n = len(scored)
    f = sum(scored) if n else None
    fb = g["ta_8q"] is None and (signals[2] is not None or signals[8] is not None)
    if eligible is not True:
        label, reason = "NOT_ELIGIBLE", None
    elif not sector or sector in {"Financials"}:
        label, reason = "NO_SIGNAL", "EXCLUDED_SECTOR"
    elif n < 8:
        label, reason = "NO_SIGNAL", "F_INCOMPLETE"
    else:
        bar = 8 if n == 9 else 7
        label = "BUY" if f >= bar else "SELL" if f <= 2 else "HOLD"
        reason = None
    ratios = dict(roa=roa, roa_prior=roa_p, lev_t=lev_t, lev_prior=lev_p, cr_t=cr_t,
                  cr_prior=cr_p, shares_prev_adj=prev, gm_t=gm_t, gm_prior=gm_p, ato_t=ato_t,
                  ato_prior=ato_p)
    return signals, f, n, label, reason, fb, ratios


def _random_inputs(rng):
    d = {}
    for base_key, base_val in E1.items():
        u = rng.random()
        if u < 0.12:
            d[base_key] = None
        elif u < 0.18:
            d[base_key] = 0.0
        elif u < 0.24:
            d[base_key] = -abs(base_val) * rng.uniform(0.01, 1.0)
        elif base_key in SHARES:
            d[base_key] = float(rng.randint(1, 10 ** 9))
        else:
            d[base_key] = round(base_val * rng.uniform(0.5, 1.5), rng.choice([0, 1, 3]))
    return d


def _random_cases(n=400, seed=20261112):
    rng = random.Random(seed)
    for _ in range(n):
        d = _random_inputs(rng)
        elig = rng.choice([True, True, True, False, None])
        sector = rng.choice(["Industrials", "Industrials", "Real Estate", "Financials", None])
        sf = rng.choice([1.0, 1.0, 2.0, 0.5, 3.0])
        yield d, elig, sector, sf


def test_property_matches_reference_formulas():
    for d, elig, sector, sf in _random_cases():
        r = pm.evaluate(make(d, eligible=elig, sector=sector, split_factor=sf))
        signals, f, n, label, reason, fb, ratios = _reference(d, elig, sector, sf)
        assert tuple(r.signals) == signals, d
        assert (r.f_score, r.n_scored) == (f, n), d
        assert (r.signal, r.no_signal_reason) == (label, reason), d
        assert (FB in set(r.flags)) is fb, d
        for k, v in ratios.items():
            got = getattr(r, k)
            assert (got is None) == (v is None), (k, d)
            if v is not None:
                assert got == approx(v), (k, d)


def test_property_invariants():
    results = [pm.evaluate(i) for i in _golden_inputs()]
    results += [pm.evaluate(make(d, eligible=e, sector=s, split_factor=sf))
                for d, e, s, sf in _random_cases(200, seed=7)]
    for r in results:
        s = tuple(r.signals)
        assert len(s) == 9 and all(v in (0, 1, None) for v in s)
        assert all(v is None or isinstance(v, int) for v in s)
        scored = [v for v in s if v is not None]
        assert r.n_scored == len(scored)
        assert r.f_score == (sum(scored) if scored else None)
        if r.f_score is not None:
            assert 0 <= r.f_score <= r.n_scored <= 9
        assert r.signal in E_LABELS
        if r.signal == "BUY":
            assert r.n_scored >= 8 and r.f_score >= 7
            if r.n_scored == 9:
                assert r.f_score >= 8
        if r.signal == "SELL":
            assert r.n_scored >= 8 and r.f_score <= 2
        if r.n_scored < 8:
            assert r.signal not in {"BUY", "HOLD", "SELL"}
        assert (r.signal == "NO_SIGNAL") == (r.no_signal_reason is not None)
        if r.no_signal_reason is not None:
            assert r.no_signal_reason in {"EXCLUDED_SECTOR", "F_INCOMPLETE"}


def test_property_fallback_flag_implies_missing_ta_8q():
    for d, e, s, sf in _random_cases(300, seed=11):
        inp = make(d, eligible=e, sector=s, split_factor=sf)
        r = pm.evaluate(inp)
        if FB in set(r.flags):
            assert inp.ta_8q is None
            sig_ = tuple(r.signals)
            assert sig_[2] is not None or sig_[8] is not None


def test_property_none_never_adds_a_scored_signal():
    bases = [dict(E1), dict(E6), {**E1, "ta_4q": 0.0}, {**E1, "rev_ttm": 0.0},
             {**E1, "gp_ttm": None, "cl_t": None}]
    rng = random.Random(3)
    bases += [_random_inputs(rng) for _ in range(60)]
    for base in bases:
        b = pm.evaluate(make(base))
        bs = tuple(b.signals)
        for k in MONEY + SHARES:
            if k == "ta_8q" or base.get(k) is None:
                continue  # ta_8q: None triggers the (flagged) fallback
            r = pm.evaluate(make(base, **{k: None}))
            rs = tuple(r.signals)
            assert r.n_scored <= b.n_scored, (k, base)
            for i in range(9):
                # unscored stays unscored; a scored signal either keeps its value or becomes
                # unscored: a missing input is never scored as 0
                assert rs[i] == bs[i] or rs[i] is None, (k, i, base)


def _scale(d, factor, keys):
    return {k: (v * factor if (k in keys and v is not None) else v) for k, v in d.items()}


@pytest.mark.parametrize("k", [-3, -1, 1, 5, 20])
def test_property_power_of_two_scaling_bit_identical(k):
    rng = random.Random(k + 100)
    bases = [dict(E1), dict(E6), {**E1, "debt_4q": 285.0}, {**E1, "ta_8q": None}]
    bases += [_random_inputs(rng) for _ in range(40)]
    for base in bases:
        a = pm.evaluate(make(base))
        b = pm.evaluate(make(_scale(base, 2.0 ** k, MONEY)))
        assert tuple(a.signals) == tuple(b.signals), base
        for f in RATIOS:
            assert getattr(a, f) == getattr(b, f), (f, base)  # bit-identical
        c = pm.evaluate(make(_scale(base, 2.0 ** k, SHARES)))
        assert tuple(c.signals)[6] == tuple(a.signals)[6], base


@pytest.mark.parametrize("factor", [3.0, 0.1, 7.5, 1e3])
def test_property_any_scaling_signals_unchanged_away_from_ties(factor):
    for base in (E1, E6, {**E6, "cfo_ttm": 10.0, "rev_ttm": 1300.0}, {**E1, "ta_8q": None}):
        a = pm.evaluate(make(base))
        b = pm.evaluate(make(_scale(base, factor, MONEY)))
        assert tuple(a.signals) == tuple(b.signals)
        assert (a.f_score, a.signal) == (b.f_score, b.signal)


def test_property_raising_ni_monotone_signals_1_3():
    for base in (E1, E6, {**E1, "ta_8q": None}):
        prev = None
        for ni in [-500.0, -100.0, -1.0, 0.0, 1e-9, 50.0, 106.0, 120.0, 150.0, 1e4]:
            s = tuple(pm.evaluate(make(base, ni_ttm=ni)).signals)
            if prev is not None:
                for i in (0, 2):
                    assert not (prev[i] == 1 and s[i] == 0), (ni, i)
            prev = s


def test_property_raising_shares_t_never_turns_signal7_on():
    for s4, sf in ((100e6, 1.0), (50e6, 2.0), (101e6, 1.0)):
        prev = None
        for st in [1.0, 50e6, 100e6, 100.5e6, 100500001.0, 101505000.0, 101505001.0, 2e8]:
            s7 = tuple(pm.evaluate(make(shares_t=st, shares_4q=s4, split_factor=sf)).signals)[6]
            if prev is not None:
                assert not (prev == 0 and s7 == 1), st
            prev = s7


def test_property_result_depends_only_on_own_inputs():
    rng = random.Random(42)
    inputs = [make(d, ticker=f"T{i}", market=rng.choice(["US", "TSX"]), eligible=e, sector=s,
                   split_factor=sf)
              for i, (d, e, s, sf) in enumerate(_random_cases(30, seed=5))]
    alone = [result_tuple(pm.evaluate(i)) for i in inputs]
    order = list(range(len(inputs)))
    rng.shuffle(order)
    out = pm.run_piotroski([inputs[i] for i in order])
    for pos, i in enumerate(order):
        assert result_tuple(out[pos]) == alone[i]
