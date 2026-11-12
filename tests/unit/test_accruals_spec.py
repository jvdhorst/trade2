"""Spec tests for Model F, accruals / earnings quality (docs/models/accruals.md v0.1.0).

Written from the spec only (not from the implementation). Goldens G1-G8 are the spec's worked
examples; relative tolerance 1e-9 (absolute 1e-12 for zero). Values the spec calls exact
(percentiles on 0.2 / 0.8 / k/10, the data-derived floor ratios +-0.05) are checked with ==.
API fixed by the spec:
    FInputs (frozen dataclass), FMetrics(accruals, avg_total_assets, accrual_ratio, cash_conv,
        period_ok), FResult(ticker, market, sector, signal, no_signal_reason, accruals,
        avg_total_assets, accrual_ratio, cash_conv, acc_pct, n, flags)
    compute_metrics(inp) -> FMetrics;  map_signal(pct, ratio, ni) -> "BUY" | "HOLD" | "SELL"
    acc_percentiles(values) -> dict[str, float | None]
    run_accruals(inputs, min_sector=MIN_SECTOR) -> list[FResult] (one per input, same order)
`hypothesis` is not a dependency, so the property tests are seeded random loops.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import date

import pytest

from valuation.models.accruals import (
    BUY_PCT,
    BUY_RATIO,
    EXCLUDED_SECTORS,
    MIN_SECTOR,
    MODEL_VERSION,
    PREV_WINDOW_DAYS,
    SELL_PCT,
    SELL_RATIO,
    FInputs,
    FMetrics,
    FResult,
    acc_percentiles,
    compute_metrics,
    map_signal,
    run_accruals,
)

REL = 1e-9
T = date(2026, 9, 30)
Q2 = date(2026, 6, 30)
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
REASONS = {"EXCLUDED_SECTOR", "NO_DATA", "NON_POSITIVE_ASSETS", "SMALL_SECTOR"}
MODEL_FLAGS = {"F_PERIOD_MISMATCH", "F_NO_PRIOR_TA", "F_LOSS_BUY_BLOCKED"}


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def mk(ticker, ni, cfo, *, ta_t=1100.0, ta_prev=900.0, market="US", sector="Industrials",
       eligible=True, period_end_t=T, ni_period_end=T, cfo_period_end=T, flags=frozenset()):
    return FInputs(ticker=ticker, market=market, sector=sector, eligible=eligible, ni_ttm=ni,
                   cfo_ttm=cfo, total_assets_t=ta_t, total_assets_prev=ta_prev,
                   period_end_t=period_end_t, ni_period_end=ni_period_end,
                   cfo_period_end=cfo_period_end, flags=frozenset(flags))


# G2 X1: (name, NI, CFO, accruals, accrual_ratio, cash_conv, acc_pct, signal, model flags)
X1_ROWS = [
    ("A01", 100, 220, -120, -0.12, 2.2, 0.0, "BUY", set()),
    ("A02", -20, 60, -80, -0.08, None, 0.09090909090909091, "HOLD", {"F_LOSS_BUY_BLOCKED"}),
    ("A03", 100, 130, -30, -0.03, 1.3, 0.18181818181818182, "HOLD", set()),
    ("A04", 100, 120, -20, -0.02, 1.2, 0.2727272727272727, "HOLD", set()),
    ("A05", 100, 110, -10, -0.01, 1.1, 0.36363636363636365, "HOLD", set()),
    ("A06", 100, 100, 0, 0.0, 1.0, 0.45454545454545453, "HOLD", set()),
    ("A07", 100, 90, 10, 0.01, 0.9, 0.5454545454545454, "HOLD", set()),
    ("A08", 100, 80, 20, 0.02, 0.8, 0.6363636363636364, "HOLD", set()),
    ("A09", 100, 70, 30, 0.03, 0.7, 0.7272727272727273, "HOLD", set()),
    ("A10", 100, 60, 40, 0.04, 0.6, 0.8181818181818182, "HOLD", set()),
    ("A11", 100, 40, 60, 0.06, 0.4, 0.9090909090909091, "SELL", set()),
    ("A12", 50, -50, 100, 0.1, -1.0, 1.0, "SELL", set()),
]
X1_NAMES = [r[0] for r in X1_ROWS]
X1_FLOWS = {r[0]: (r[1], r[2]) for r in X1_ROWS}


def x1(**overrides):
    """X1 inputs; overrides maps name -> dict of mk() keyword changes (None value drops it)."""
    out = []
    for name, ni, cfo, *_ in X1_ROWS:
        kw = dict(ni=ni, cfo=cfo)
        kw.update(overrides.get(name, {}))
        if kw.pop("drop", False):
            continue
        out.append(mk(name, kw.pop("ni"), kw.pop("cfo"), **kw))
    return out


def by_ticker(results):
    d = {}
    for r in results:
        assert r.ticker not in d, r.ticker
        d[r.ticker] = r
    return d


def mflags(r):
    return set(r.flags) & MODEL_FLAGS


def assert_x1(res, names=X1_NAMES):
    for name, _ni, _cfo, acc, ratio, cc, pct, sig, fl in X1_ROWS:
        if name not in names:
            continue
        r = res[name]
        assert r.signal == sig, name
        assert r.no_signal_reason is None, name
        assert r.accruals == approx(acc), name
        assert r.avg_total_assets == approx(1000.0), name
        assert r.accrual_ratio == approx(ratio), name
        assert (r.cash_conv is None) if cc is None else r.cash_conv == approx(cc), name
        assert r.acc_pct == approx(pct), name
        assert r.n == 12, name
        assert mflags(r) == fl, name


# ---------------------------------------------------------------- constants and API shape

def test_constants():
    assert MODEL_VERSION == "accruals/0.1.0"
    assert (BUY_PCT, SELL_PCT, BUY_RATIO, SELL_RATIO) == (0.20, 0.80, -0.05, 0.05)
    assert MIN_SECTOR == 12
    assert EXCLUDED_SECTORS == frozenset({"Financials"})
    assert isinstance(EXCLUDED_SECTORS, frozenset)
    assert tuple(PREV_WINDOW_DAYS) == (350, 380)


def test_inputs_frozen_and_defaults():
    inp = FInputs(ticker="X", market="US", sector="Industrials", eligible=True)
    assert inp.ni_ttm is None and inp.cfo_ttm is None
    assert inp.total_assets_t is None and inp.total_assets_prev is None
    assert inp.period_end_t is None and inp.ni_period_end is None and inp.cfo_period_end is None
    assert inp.flags == frozenset()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.ni_ttm = 1.0  # type: ignore[misc]


def test_result_fields():
    # SPEC-AMBIGUITY: the spec names the FResult / FMetrics fields but not their container type;
    # only the attribute names are checked (plus value equality, used by the property tests).
    (r,) = run_accruals([mk("X", 100.0, 220.0)])
    assert isinstance(r, FResult)
    for f in ("ticker", "market", "sector", "signal", "no_signal_reason", "accruals",
              "avg_total_assets", "accrual_ratio", "cash_conv", "acc_pct", "n", "flags"):
        assert hasattr(r, f), f
    m = compute_metrics(mk("X", 100.0, 220.0))
    assert isinstance(m, FMetrics)
    for f in ("accruals", "avg_total_assets", "accrual_ratio", "cash_conv", "period_ok"):
        assert hasattr(m, f), f


def test_no_money_defaulted_to_zero():
    # all monetary inputs None -> everything None, NO_DATA (never computed as 0)
    m = compute_metrics(FInputs(ticker="X", market="US", sector="Industrials", eligible=True))
    assert (m.accruals, m.avg_total_assets, m.accrual_ratio, m.cash_conv) == (None,) * 4
    assert m.period_ok is False
    (r,) = run_accruals([FInputs(ticker="X", market="US", sector="Industrials", eligible=True)])
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")


# ---------------------------------------------------------------- G1 proposal cases

@pytest.mark.parametrize("cfo,acc,ratio,cc,pct,sig", [
    (320, 180, 0.04736842105263158, 0.64, 0.90, "HOLD"),
    (280, 220, 0.05789473684210526, 0.56, 0.90, "SELL"),
    (760, -260, -0.06842105263157895, 1.52, 0.10, "BUY"),
])
def test_g1_proposal_cases(cfo, acc, ratio, cc, pct, sig):
    m = compute_metrics(mk("G1", 500.0, float(cfo), ta_t=4000.0, ta_prev=3600.0))
    assert m.period_ok is True
    assert m.accruals == approx(acc)
    assert m.avg_total_assets == 3800.0
    assert m.accrual_ratio == approx(ratio)
    assert m.accrual_ratio == ratio  # evaluation order: bit for bit
    assert m.cash_conv == approx(cc)
    assert map_signal(pct, m.accrual_ratio, 500.0) == sig


def test_compute_metrics_validates_market():
    with pytest.raises(ValueError):
        compute_metrics(mk("X", 100.0, 220.0, market="LSE"))
    with pytest.raises(ValueError):
        compute_metrics(mk("X", 100.0, 220.0, market="us"))


# ---------------------------------------------------------------- G2 full cross-section

def test_g2_x1_full_cross_section():
    inputs = x1()
    results = run_accruals(inputs)
    assert [r.ticker for r in results] == X1_NAMES
    res = by_ticker(results)
    assert_x1(res)
    for r in results:
        assert (r.market, r.sector) == ("US", "Industrials")


def test_g2_short_decimals_exact():
    for name, ni, cfo, _acc, ratio, *_ in X1_ROWS:
        m = compute_metrics(mk(name, float(ni), float(cfo)))
        assert m.accrual_ratio == ratio, name  # e.g. (100 - 220) / 1000.0 == -0.12


def test_g2_extremes_exact():
    res = by_ticker(run_accruals(x1()))
    assert res["A01"].acc_pct == 0.0 and res["A12"].acc_pct == 1.0
    assert isinstance(res["A01"].acc_pct, float)


# ---------------------------------------------------------------- G3 ties

def test_g3_t1_tie_free_buy():
    res = by_ticker(run_accruals(x1(A03=dict(cfo=160))))
    a03 = res["A03"]
    assert a03.accrual_ratio == approx(-0.06)
    assert a03.cash_conv == approx(1.6)
    assert a03.acc_pct == approx(0.18181818181818182)
    assert a03.signal == "BUY"
    assert_x1(res, names=[n for n in X1_NAMES if n != "A03"])


def test_g3_t2_tie_straddles_band():
    res = by_ticker(run_accruals(x1(A03=dict(cfo=160), A04=dict(cfo=160))))
    for name in ("A03", "A04"):
        assert res[name].accrual_ratio == approx(-0.06)
        assert res[name].acc_pct == approx(0.22727272727272727)
        assert res[name].signal == "HOLD"
        assert res[name].n == 12
    assert res["A03"].acc_pct == res["A04"].acc_pct
    assert_x1(res, names=[n for n in X1_NAMES if n not in ("A03", "A04")])


# ---------------------------------------------------------------- G4 small sector

S1_PCTS = {"A01": 0.0, "A02": 0.1, "A03": 0.2, "A04": 0.3, "A05": 0.4, "A06": 0.5, "A07": 0.6,
           "A08": 0.7, "A09": 0.8, "A10": 0.9, "A11": 1.0}


def test_g4_s1_small_sector():
    results = run_accruals(x1(A12=dict(drop=True)))
    assert len(results) == 11
    for r in results:
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR"), r.ticker
        assert r.n == 11
        assert r.acc_pct == S1_PCTS[r.ticker], r.ticker  # exactly these doubles
        assert "F_LOSS_BUY_BLOCKED" not in r.flags  # row 6 not reached


def test_g4_s1b_min_sector_11():
    res = by_ticker(run_accruals(x1(A12=dict(drop=True)), min_sector=11))
    expect = {"A01": "BUY", "A11": "SELL"}
    for name, r in res.items():
        assert r.signal == expect.get(name, "HOLD"), name
        assert r.no_signal_reason is None
        assert r.acc_pct == S1_PCTS[name]
        assert r.n == 11
    assert mflags(res["A02"]) == {"F_LOSS_BUY_BLOCKED"}
    assert res["A03"].acc_pct == 0.2 and res["A03"].signal == "HOLD"  # ratio -0.03 > -0.05
    assert res["A09"].acc_pct == 0.8 and res["A09"].signal == "HOLD"  # ratio 0.03 < 0.05
    assert all(not mflags(r) for n, r in res.items() if n != "A02")


def test_g4_s2_ineligible_leaves_small_sector():
    res = by_ticker(run_accruals(x1(A12=dict(eligible=False))))
    a12 = res["A12"]
    assert (a12.signal, a12.no_signal_reason) == ("NOT_ELIGIBLE", None)
    assert a12.accrual_ratio == approx(0.1)
    assert a12.cash_conv == approx(-1.0)
    assert a12.accruals == approx(100)
    assert a12.acc_pct is None and a12.n is None
    for name in X1_NAMES[:-1]:
        r = res[name]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR"), name
        assert r.n == 11
        assert r.acc_pct == S1_PCTS[name]


@pytest.mark.parametrize("ms", [1, 0, -3])
def test_g4_min_sector_below_2_raises(ms):
    with pytest.raises(ValueError):
        run_accruals(x1(), min_sector=ms)


def test_min_sector_2_allowed():
    results = run_accruals(x1(), min_sector=2)
    assert_x1(by_ticker(results))


# ---------------------------------------------------------------- G5 boundaries (n = 16)

B_RATIOS = [-0.20, -0.15, -0.10, -0.08, -0.06, -0.04, -0.02, 0.0, 0.01, 0.02, 0.03, 0.06, 0.07,
            0.09, 0.12, 0.20]
B_CFO = [300, 250, 200, 180, 160, 140, 120, 100, 90, 80, 70, 40, 30, 10, -20, -100]
B_PCTS = [0.0, 0.06666666666666667, 0.13333333333333333, 0.2, 0.26666666666666666,
          0.3333333333333333, 0.4, 0.4666666666666667, 0.5333333333333333, 0.6,
          0.6666666666666666, 0.7333333333333333, 0.8, 0.8666666666666667, 0.9333333333333333,
          1.0]
B_SIGNALS = ["BUY"] * 4 + ["HOLD"] * 8 + ["SELL"] * 4


def b_inputs():
    return [mk(f"B{i + 1:02d}", 100.0, float(c), sector="Consumer Discretionary")
            for i, c in enumerate(B_CFO)]


def test_g5_boundaries():
    results = run_accruals(b_inputs())
    assert len(results) == 16
    for i, r in enumerate(results):
        name = f"B{i + 1:02d}"
        assert r.ticker == name
        assert r.accrual_ratio == approx(B_RATIOS[i]), name
        assert r.acc_pct == approx(B_PCTS[i]), name
        assert r.signal == B_SIGNALS[i], name
        assert r.n == 16
        assert r.cash_conv == approx(B_CFO[i] / 100), name
        assert not mflags(r), name
    assert results[3].acc_pct == 0.2  # 3/15 == 0.2 exactly, BUY inclusive
    assert results[12].acc_pct == 0.8  # 12/15 == 0.8 exactly, SELL inclusive


def test_g5_cash_conv_named_values():
    res = by_ticker(run_accruals(b_inputs()))
    assert res["B01"].cash_conv == approx(3.0)
    assert res["B13"].cash_conv == approx(0.3)
    assert res["B15"].cash_conv == approx(-0.2)
    assert res["B16"].cash_conv == approx(-1.0)


@pytest.mark.parametrize("pct,ratio,ni,expected", [
    (0.2, -0.05, 1.0, "BUY"),
    (0.2, -0.0499999, 1.0, "HOLD"),
    (0.2000001, -0.5, 1.0, "HOLD"),
    (0.1, -0.1, 0.0, "HOLD"),
    (0.1, -0.1, -5.0, "HOLD"),
    (0.8, 0.05, 1.0, "SELL"),
    (0.8, 0.0499999, 1.0, "HOLD"),
    (0.7999999, 0.5, 1.0, "HOLD"),
    (0.9, 0.1, -5.0, "SELL"),
    (0.5, 0.0, 1.0, "HOLD"),
])
def test_map_signal_boundaries(pct, ratio, ni, expected):
    assert map_signal(pct, ratio, ni) == expected


def test_data_derived_ratio_exactly_on_floor():
    up = compute_metrics(mk("U", 150.0, 100.0))
    down = compute_metrics(mk("D", 100.0, 150.0))
    assert up.accrual_ratio == 0.05
    assert down.accrual_ratio == -0.05
    assert map_signal(0.8, up.accrual_ratio, 150.0) == "SELL"
    assert map_signal(0.2, down.accrual_ratio, 100.0) == "BUY"


# ---------------------------------------------------------------- G6 market separation

def tsx(k, ni=100.0, cfo=300.0):
    return [mk(f"C{i + 1:02d}.TO", ni, cfo, market="TSX") for i in range(k)]


def test_g6_market_separation_full():
    results = run_accruals(x1() + tsx(12))
    res = by_ticker(results)
    assert_x1(res)
    for i in range(12):
        r = res[f"C{i + 1:02d}.TO"]
        assert r.market == "TSX"
        assert r.accrual_ratio == approx(-0.2)
        assert r.n == 12
        assert r.acc_pct == 0.5
        assert (r.signal, r.no_signal_reason) == ("HOLD", None)


def test_g6_market_separation_small_tsx():
    res = by_ticker(run_accruals(x1() + tsx(5)))
    assert_x1(res)
    for i in range(5):
        r = res[f"C{i + 1:02d}.TO"]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR")
        assert r.n == 5 and r.acc_pct == 0.5


def test_g6_tsx_changes_never_move_us():
    base = by_ticker(run_accruals(x1()))
    rng = random.Random(6)
    for _ in range(20):
        k = rng.randint(0, 20)
        extra = [mk(f"C{i:02d}.TO", rng.uniform(-50, 150), rng.uniform(-100, 400), market="TSX")
                 for i in range(k)]
        res = by_ticker(run_accruals(x1() + extra))
        for name in X1_NAMES:
            assert res[name] == base[name], name


def test_same_ticker_two_markets_two_names():
    # results keyed by input position, not ticker
    inputs = x1() + [mk("A01", 100.0, 300.0, market="TSX")]
    results = run_accruals(inputs)
    assert len(results) == 13
    assert results[0].market == "US" and results[0].signal == "BUY"
    assert results[0].n == 12
    last = results[-1]
    assert (last.ticker, last.market) == ("A01", "TSX")
    assert last.accrual_ratio == approx(-0.2)
    assert (last.signal, last.no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR")
    assert last.n == 1 and last.acc_pct is None  # n < 2


# ---------------------------------------------------------------- G7 sector separation

def copies(prefix, sector):
    return [mk(f"{prefix}{name[1:]}", float(ni), float(cfo), sector=sector)
            for name, ni, cfo, *_ in X1_ROWS]


def test_g7_real_estate_scored_in_own_group():
    res = by_ticker(run_accruals(x1() + copies("R", "Real Estate")))
    assert_x1(res)
    for name in X1_NAMES:
        a, r = res[name], res["R" + name[1:]]
        assert r.sector == "Real Estate"
        assert (r.accrual_ratio, r.acc_pct, r.n, r.signal, r.no_signal_reason) == (
            a.accrual_ratio, a.acc_pct, a.n, a.signal, a.no_signal_reason), name
        assert mflags(r) == mflags(a)


def test_g7_financials_excluded():
    res = by_ticker(run_accruals(x1() + copies("F", "Financials")))
    assert_x1(res)
    for name, _ni, _cfo, acc, ratio, cc, *_ in X1_ROWS:
        r = res["F" + name[1:]]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
        assert r.accruals == approx(acc)
        assert r.accrual_ratio == approx(ratio)
        assert (r.cash_conv is None) if cc is None else r.cash_conv == approx(cc)
        assert r.acc_pct is None and r.n is None
        assert "F_LOSS_BUY_BLOCKED" not in r.flags


def test_g7_energy_small_sector():
    energy = [mk(f"E{i:02d}", 100.0, 300.0, sector="Energy") for i in (1, 2, 3)]
    res = by_ticker(run_accruals(x1() + energy))
    assert_x1(res)
    for i in (1, 2, 3):
        r = res[f"E{i:02d}"]
        assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR")
        assert r.n == 3 and r.acc_pct == 0.5


def test_sector_exact_string_match():
    # SPEC-AMBIGUITY: "exact string match (no case folding)" also for the exclusion list, so
    # "financials" is not excluded; it is its own (US, "financials") group of one -> SMALL_SECTOR.
    res = by_ticker(run_accruals(x1() + [mk("LOW", 100.0, 220.0, sector="financials"),
                                         mk("IND", 100.0, 220.0, sector="industrials")]))
    assert_x1(res)
    for t in ("LOW", "IND"):
        assert (res[t].signal, res[t].no_signal_reason) == ("NO_SIGNAL", "SMALL_SECTOR")
        assert res[t].n == 1 and res[t].acc_pct is None


# ---------------------------------------------------------------- G8 precedence

A01 = dict(ni=100.0, cfo=220.0)


def run_p(**change):
    kw = dict(A01)
    kw.update(change)
    p = mk("P", kw.pop("ni"), kw.pop("cfo"), **kw)
    results = run_accruals(x1() + [p])
    assert len(results) == 13 and results[-1].ticker == "P"
    return by_ticker(results[:-1]), results[-1]


def assert_full_metrics(p):
    assert p.accruals == approx(-120)
    assert p.avg_total_assets == approx(1000.0)
    assert p.accrual_ratio == approx(-0.12)
    assert p.cash_conv == approx(2.2)


@pytest.mark.parametrize("elig", [False, None])
def test_g8a_not_eligible(elig):
    res, p = run_p(eligible=elig, sector="Financials")
    assert (p.signal, p.no_signal_reason) == ("NOT_ELIGIBLE", None)
    assert_full_metrics(p)
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


def test_g8a_truthy_non_true_is_not_eligible():
    # "anything but True": a truthy non-bool must not count as eligible
    res, p = run_p(eligible=1)
    assert (p.signal, p.no_signal_reason) == ("NOT_ELIGIBLE", None)
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


def test_g8b_excluded_sector():
    res, p = run_p(sector="Financials")
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert_full_metrics(p)
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


@pytest.mark.parametrize("sector", [None, ""])
def test_g8c_missing_sector(sector):
    res, p = run_p(sector=sector)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


def test_g8d_excluded_before_no_data():
    res, p = run_p(ta_prev=None, sector="Financials")
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert mflags(p) == {"F_NO_PRIOR_TA"}
    assert_x1(res)


def test_g8e_no_prior_ta():
    res, p = run_p(ta_prev=None)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert mflags(p) == {"F_NO_PRIOR_TA"}
    assert p.accruals == approx(-120)
    assert p.cash_conv == approx(2.2)
    assert p.avg_total_assets is None and p.accrual_ratio is None
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


def test_g8f_cfo_period_mismatch():
    res, p = run_p(cfo_period_end=Q2)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert mflags(p) == {"F_PERIOD_MISMATCH"}
    assert p.accruals is None and p.accrual_ratio is None and p.cash_conv is None
    assert p.avg_total_assets == approx(1000.0)
    assert_x1(res)


def test_g8g_flows_agree_but_not_with_t():
    res, p = run_p(ni_period_end=Q2, cfo_period_end=Q2)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert mflags(p) == {"F_PERIOD_MISMATCH"}
    assert p.accruals is None and p.accrual_ratio is None and p.cash_conv is None
    assert p.avg_total_assets == approx(1000.0)
    assert_x1(res)


def test_g8h_ni_missing():
    res, p = run_p(ni=None)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert "F_PERIOD_MISMATCH" not in p.flags
    assert p.accruals is None and p.accrual_ratio is None and p.cash_conv is None
    assert_x1(res)


def test_g8i_negative_avg_assets():
    res, p = run_p(ta_t=100.0, ta_prev=-300.0)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_ASSETS")
    assert p.avg_total_assets == approx(-100.0)
    assert p.accrual_ratio is None
    assert p.accruals == approx(-120)
    assert p.acc_pct is None and p.n is None
    assert_x1(res)


def test_g8j_zero_avg_assets():
    res, p = run_p(ta_t=0.0, ta_prev=0.0)
    assert (p.signal, p.no_signal_reason) == ("NO_SIGNAL", "NON_POSITIVE_ASSETS")
    assert p.avg_total_assets == 0.0
    assert p.accrual_ratio is None
    assert_x1(res)


def test_g8k_valid_13th_name():
    res, p = run_p()
    expect = {"A02": (0.16666666666666666, "HOLD"), "A03": (0.25, "HOLD"),
              "A04": (0.3333333333333333, "HOLD"), "A05": (0.4166666666666667, "HOLD"),
              "A06": (0.5, "HOLD"), "A07": (0.5833333333333334, "HOLD"),
              "A08": (0.6666666666666666, "HOLD"), "A09": (0.75, "HOLD"),
              "A10": (0.8333333333333334, "HOLD"), "A11": (0.9166666666666666, "SELL"),
              "A12": (1.0, "SELL")}
    for r in (p, res["A01"]):
        assert r.acc_pct == approx(0.041666666666666664)
        assert r.signal == "BUY" and r.n == 13
    assert p.acc_pct == res["A01"].acc_pct
    for name, (pct, sig) in expect.items():
        assert res[name].acc_pct == approx(pct), name
        assert res[name].signal == sig, name
        assert res[name].n == 13
    assert mflags(res["A02"]) == {"F_LOSS_BUY_BLOCKED"}


def test_g8l_bad_market_raises():
    with pytest.raises(ValueError):
        run_p(market="LSE")


# ---------------------------------------------------------------- formulas and flags

def test_period_end_t_none_not_period_ok():
    m = compute_metrics(mk("X", 100.0, 220.0, period_end_t=None))
    assert m.period_ok is False
    assert m.accruals is None and m.accrual_ratio is None and m.cash_conv is None
    assert m.avg_total_assets == approx(1000.0)
    (r,) = run_accruals([mk("X", 100.0, 220.0, period_end_t=None)])
    assert (r.signal, r.no_signal_reason) == ("NO_SIGNAL", "NO_DATA")
    assert "F_PERIOD_MISMATCH" in r.flags


def test_ni_period_end_none_mismatch():
    (r,) = run_accruals([mk("X", 100.0, 220.0, ni_period_end=None)])
    assert r.no_signal_reason == "NO_DATA"
    assert mflags(r) == {"F_PERIOD_MISMATCH"}


def test_cash_conv_none_when_ni_non_positive():
    for ni in (0.0, -20.0):
        m = compute_metrics(mk("X", ni, 60.0))
        assert m.cash_conv is None
        assert m.accruals == approx(ni - 60.0)
        assert m.accrual_ratio == approx((ni - 60.0) / 1000.0)


def test_zero_ni_in_ranking_set_buy_blocked():
    # NI exactly 0 counts as a loss-maker: in R_g, never BUY
    res = by_ticker(run_accruals(x1(A01=dict(ni=0.0, cfo=200.0))))  # ratio -0.2, rank 1
    a01 = res["A01"]
    assert a01.n == 12 and a01.acc_pct == 0.0
    assert a01.signal == "HOLD"
    assert mflags(a01) == {"F_LOSS_BUY_BLOCKED"}


def test_loss_maker_can_be_sell():
    res = by_ticker(run_accruals(x1(A12=dict(ni=-10.0, cfo=-110.0))))  # ratio 0.1, NI < 0
    assert res["A12"].signal == "SELL"
    assert res["A12"].cash_conv is None
    assert not mflags(res["A12"])


def test_flags_whatever_the_signal():
    r1, r2 = run_accruals([mk("X", 100.0, 220.0, eligible=False, cfo_period_end=Q2),
                           mk("Y", 100.0, 220.0, eligible=False, ta_prev=None)])
    assert r1.signal == "NOT_ELIGIBLE" and mflags(r1) == {"F_PERIOD_MISMATCH"}
    assert r2.signal == "NOT_ELIGIBLE" and mflags(r2) == {"F_NO_PRIOR_TA"}


def test_no_prior_ta_flag_needs_ta_t():
    (r,) = run_accruals([mk("X", 100.0, 220.0, ta_t=None, ta_prev=None)])
    assert r.no_signal_reason == "NO_DATA"
    assert "F_NO_PRIOR_TA" not in r.flags
    (r,) = run_accruals([mk("X", 100.0, 220.0, ta_t=None, ta_prev=900.0)])
    assert "F_NO_PRIOR_TA" not in r.flags
    assert r.avg_total_assets is None


@pytest.mark.parametrize("flag", ["PIT_ESTIMATED", "FLOW_CCY_MISMATCH", "NO_TTM", "STALE_TTM",
                                  "F_PRIOR_CCY_MISMATCH"])
def test_pass_through_flags(flag):
    inputs = x1(A05=dict(flags={flag}))
    res = by_ticker(run_accruals(inputs))
    assert flag in res["A05"].flags
    assert all(flag not in res[n].flags for n in X1_NAMES if n != "A05")


def test_loss_buy_blocked_only_at_row_6():
    # pct band and ratio band met, NI <= 0, but rows 1-5 catch the name -> no flag
    res = by_ticker(run_accruals(x1(A12=dict(drop=True))))  # SMALL_SECTOR
    assert "F_LOSS_BUY_BLOCKED" not in res["A02"].flags
    (r,) = run_accruals([mk("X", -20.0, 60.0, eligible=False)])
    assert "F_LOSS_BUY_BLOCKED" not in r.flags


def test_empty_input():
    assert run_accruals([]) == []


def test_bad_market_anywhere_raises():
    with pytest.raises(ValueError):
        run_accruals(x1() + [mk("Z", 100.0, 220.0, market="NYSE", eligible=False)])


# ---------------------------------------------------------------- acc_percentiles

def test_acc_percentiles_x1():
    vals = {name: ratio for name, _ni, _cfo, _acc, ratio, *_ in X1_ROWS}
    pct = acc_percentiles(vals)
    assert set(pct) == set(vals)
    for name, *_rest, p, _sig, _fl in X1_ROWS:
        assert pct[name] == approx(p)


def test_acc_percentiles_ties_and_small():
    pct = acc_percentiles({"a": -0.06, "b": -0.06, "c": 0.1})
    assert pct == {"a": 0.25, "b": 0.25, "c": 1.0}
    assert acc_percentiles({"x": 0.3}) == {"x": None}
    assert acc_percentiles({}) == {}
    assert acc_percentiles({"a": 1.0, "b": 1.0}) == {"a": 0.5, "b": 0.5}


def test_acc_percentiles_order_free():
    vals = {f"N{i}": v for i, v in enumerate(B_RATIOS)}
    items = list(vals.items())
    random.Random(3).shuffle(items)
    assert acc_percentiles(dict(items)) == acc_percentiles(vals)


# ---------------------------------------------------------------- properties

def random_universe(rng, k):
    sectors = ["Industrials", "Energy", "Real Estate", "Financials", None]
    out = []
    for i in range(k):
        out.append(mk(f"N{i:03d}", rng.choice([rng.uniform(-50, 200), 0.0]),
                      rng.uniform(-100, 400), ta_t=rng.uniform(500, 2000),
                      ta_prev=rng.choice([rng.uniform(500, 2000), None]),
                      market=rng.choice(["US", "TSX"]), sector=rng.choice(sectors),
                      eligible=rng.choice([True, True, True, False, None])))
    return out


def test_order_independence_and_same_order():
    rng = random.Random(11)
    for _ in range(10):
        inputs = random_universe(rng, rng.randint(20, 60)) + x1()
        base = run_accruals(inputs)
        assert len(base) == len(inputs)
        assert [r.ticker for r in base] == [i.ticker for i in inputs]
        perm = list(range(len(inputs)))
        rng.shuffle(perm)
        shuffled = run_accruals([inputs[j] for j in perm])
        assert [r.ticker for r in shuffled] == [inputs[j].ticker for j in perm]
        for pos, j in enumerate(perm):
            assert shuffled[pos] == base[j]


def test_scale_invariance():
    rng = random.Random(5)
    base = run_accruals(x1() + b_inputs())
    for k in (2.0 ** 10, 1e-3, 7.3, 1e6):
        idx = rng.randrange(len(base))
        inputs = x1() + b_inputs()
        i = inputs[idx]
        inputs[idx] = dataclasses.replace(
            i, ni_ttm=i.ni_ttm * k, cfo_ttm=i.cfo_ttm * k, total_assets_t=i.total_assets_t * k,
            total_assets_prev=i.total_assets_prev * k)
        scaled = run_accruals(inputs)
        for a, b in zip(base, scaled):
            assert b.accrual_ratio == approx(a.accrual_ratio), (k, a.ticker)
            assert (a.cash_conv is None) == (b.cash_conv is None)
            if a.cash_conv is not None:
                assert b.cash_conv == approx(a.cash_conv)
            assert (b.acc_pct, b.n, b.signal) == (a.acc_pct, a.n, a.signal), (k, a.ticker)


def test_sign_reversal_maps_p_to_one_minus_p():
    for vals in ({f"N{i}": v for i, v in enumerate(B_RATIOS)},
                 {"a": -0.06, "b": -0.06, "c": 0.0, "d": 0.1, "e": 0.1, "f": 0.1}):
        p = acc_percentiles(vals)
        q = acc_percentiles({k: -v for k, v in vals.items()})
        for k in vals:
            assert q[k] == approx(1 - p[k]), k
            assert 0.0 <= p[k] <= 1.0


def test_symmetric_bands_count():
    for n in range(12, 31):
        vals = {f"N{i}": float(i) for i in range(n)}
        p = acc_percentiles(vals)
        buy = sum(1 for v in p.values() if v <= BUY_PCT)
        sell = sum(1 for v in p.values() if v >= SELL_PCT)
        assert buy == sell, n
    p12 = acc_percentiles({f"N{i}": float(i) for i in range(12)})
    assert sum(1 for v in p12.values() if v <= 0.2) == 3
    p16 = acc_percentiles({f"N{i}": float(i) for i in range(16)})
    assert sum(1 for v in p16.values() if v <= 0.2) == 4


def test_lower_cfo_never_lowers_pct():
    prev = None
    for cfo in range(400, -200, -10):  # A06's CFO falling -> accruals rising
        res = by_ticker(run_accruals(x1(A06=dict(cfo=float(cfo)))))
        p = res["A06"].acc_pct
        assert p is not None and res["A06"].n == 12
        if prev is not None:
            assert p >= prev, cfo
        prev = p


def test_signal_implications_random():
    rng = random.Random(23)
    for _ in range(30):
        inputs = random_universe(rng, rng.randint(30, 80))
        ms = rng.choice([2, 5, 12])
        for inp, r in zip(inputs, run_accruals(inputs, min_sector=ms)):
            assert r.signal in LABELS
            assert (r.signal == "NO_SIGNAL") == (r.no_signal_reason is not None)
            assert r.no_signal_reason is None or r.no_signal_reason in REASONS
            if r.signal == "BUY":
                assert r.acc_pct <= 0.20 and r.accrual_ratio <= -0.05 and inp.ni_ttm > 0
            if r.signal == "SELL":
                assert r.acc_pct >= 0.80 and r.accrual_ratio >= 0.05
            if r.acc_pct is not None:
                assert 0.0 <= r.acc_pct <= 1.0
            if r.n is not None and r.n < ms:
                assert r.signal == "NO_SIGNAL"
            if "F_LOSS_BUY_BLOCKED" in r.flags:
                assert r.signal == "HOLD"
            if "F_PERIOD_MISMATCH" in r.flags:
                assert r.signal == "NOT_ELIGIBLE" or r.no_signal_reason in (
                    "NO_DATA", "EXCLUDED_SECTOR")
            if r.signal in ("BUY", "HOLD", "SELL"):
                assert r.n is not None and r.n >= ms


def test_outside_names_never_affect_others():
    base = by_ticker(run_accruals(x1()))
    outsiders = [mk("O1", 100.0, 900.0, eligible=False), mk("O2", 100.0, 900.0, sector=None),
                 mk("O3", 100.0, 900.0, ta_prev=None), mk("O4", 100.0, 900.0, cfo_period_end=Q2),
                 mk("O5", 100.0, 900.0, ta_t=-5000.0), mk("O6", None, 900.0),
                 mk("O7", 100.0, 900.0, sector="Financials")]
    res = by_ticker(run_accruals(outsiders + x1()))
    for name in X1_NAMES:
        assert res[name] == base[name], name
    for o in outsiders:
        assert res[o.ticker].acc_pct is None and res[o.ticker].n is None
