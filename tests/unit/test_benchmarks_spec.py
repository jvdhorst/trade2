"""Spec tests for benchmark yields and returns (docs/models/benchmarks.md v0.1.1).

Written from the spec only (API contract, Definitions, Golden example, Failure modes), plus the
backtest excess functions in valuation.backtest.metrics:
    benchmark_excess(obs, bench_fwd, allowed_dates=None)
        -> date, horizon, bucket, n, bucket_ret, bench_ret, excess
    yearly_benchmark_excess(by_date) -> year, horizon, bucket, n_dates, excess
The module is imported per test (fixture), so a missing module or function fails only the
tests that need it.
"""

from __future__ import annotations

import importlib
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from valuation.backtest import metrics

REL = 1e-9
NAN = float("nan")
AS_OF = date(2026, 11, 10)
CSV = Path(__file__).resolve().parents[2] / "universe" / "benchmarks.csv"


@pytest.fixture
def bm():
    return importlib.import_module("valuation.benchmarks")


def approx(x):
    return pytest.approx(x, rel=REL)


def D(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Golden example
# ---------------------------------------------------------------------------

GOLDEN_DISTS = [
    (D("2025-11-10"), 1.50),  # exactly 365 days back: excluded (window open on the left)
    (D("2025-12-15"), 1.60),
    (D("2026-03-16"), 1.70),
    (D("2026-06-15"), 1.75),
    (D("2026-09-15"), 1.80),
    (D("2026-11-11"), 9.99),  # after as_of: excluded (look-ahead)
]


def test_golden_dist_ttm(bm):
    assert bm.dist_ttm(GOLDEN_DISTS, AS_OF) == approx(6.85)


def test_golden_dist_yield(bm):
    assert bm.dist_yield(6.85, 500.0) == approx(0.0137)


def test_golden_yield_spread(bm):
    assert bm.yield_spread(0.0137, 0.04) == approx(-0.0263)


def test_golden_tr_12m(bm):
    levels = [(D("2025-11-10"), 400.0), (D("2026-11-10"), 452.0)]
    assert bm.total_return(levels, AS_OF, 12) == approx(0.13)


def test_golden_tr_ann_3y(bm):
    levels = [(D("2023-11-10"), 300.0), (D("2026-11-10"), 452.0)]
    tr = bm.total_return(levels, AS_OF, 36)
    assert tr == approx(452 / 300 - 1)
    assert bm.annualize(tr, 36) == pytest.approx(0.146408, abs=1e-6)


def test_golden_excess(bm):
    assert bm.excess(0.18, 0.13) == approx(0.05)


# ---------------------------------------------------------------------------
# dist_ttm, dist_yield, yield_spread, excess
# ---------------------------------------------------------------------------


def test_dist_ttm_window_edges(bm):
    assert bm.dist_ttm([(D("2025-11-11"), 1.0), (AS_OF, 2.0)], AS_OF) == approx(3.0)
    assert bm.dist_ttm([(D("2025-11-10"), 1.0)], AS_OF) == 0.0


def test_dist_ttm_none_is_zero(bm):
    assert bm.dist_ttm([], AS_OF) == 0.0


@pytest.mark.parametrize("close", [0.0, -5.0, None])
def test_dist_yield_none_for_bad_close(bm, close):
    assert bm.dist_yield(6.85, close) is None


def test_yield_spread_none(bm):
    assert bm.yield_spread(None, 0.04) is None
    assert bm.yield_spread(0.0137, None) is None


@pytest.mark.parametrize(("a", "b"), [(None, 0.1), (0.1, None), (NAN, 0.1), (0.1, NAN)])
def test_excess_none(bm, a, b):
    assert bm.excess(a, b) is None


# ---------------------------------------------------------------------------
# months_back, level_on, total_return, annualize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("d", "h", "expected"), [
    ("2026-03-31", 1, "2026-02-28"),
    ("2024-03-31", 1, "2024-02-29"),  # leap year
    ("2026-11-10", 12, "2025-11-10"),
    ("2026-11-10", 36, "2023-11-10"),
    ("2026-01-15", 1, "2025-12-15"),
    ("2026-05-31", 3, "2026-02-28"),
])
def test_months_back(bm, d, h, expected):
    assert bm.months_back(D(d), h) == D(expected)


def test_level_on_falls_back_to_last_date(bm):
    levels = [(D("2026-11-09"), 101.0), (D("2026-11-06"), 100.0)]  # order of input irrelevant
    assert bm.level_on(levels, D("2026-11-08")) == approx(100.0)  # Sunday -> Friday
    assert bm.level_on(levels, D("2026-11-09")) == approx(101.0)
    assert bm.level_on(levels, D("2026-12-31")) == approx(101.0)
    assert bm.level_on(levels, D("2026-11-05")) is None
    assert bm.level_on([], AS_OF) is None


def test_total_return_non_trading_start_falls_back(bm):
    # months_back(2026-11-10, 1) = 2026-10-10 (Saturday) -> close of Friday 2026-10-09.
    levels = [(D("2026-10-09"), 440.0), (D("2026-10-12"), 1.0), (AS_OF, 452.0)]
    assert bm.total_return(levels, AS_OF, 1) == approx(452 / 440 - 1)


def test_total_return_none_without_history(bm):
    levels = [(D("2026-01-02"), 400.0), (AS_OF, 452.0)]
    assert bm.total_return(levels, AS_OF, 12) is None


def test_total_return_none_for_nonpositive_start(bm):
    levels = [(D("2025-11-10"), 0.0), (AS_OF, 452.0)]
    assert bm.total_return(levels, AS_OF, 12) is None


def test_total_return_ignores_levels_after_as_of(bm):
    base_ = [(D("2025-11-10"), 400.0), (AS_OF, 452.0)]
    assert bm.total_return(base_ + [(D("2026-11-11"), 900.0)], AS_OF, 12) == approx(0.13)
    # SPEC-AMBIGUITY: level(as_of) read as level_on(levels, as_of) (last date <= as_of).
    # Only a level after as_of at the end date -> the end level is the last one on or before.
    assert bm.total_return([(D("2025-11-10"), 400.0), (D("2026-11-11"), 900.0)], AS_OF, 12) == \
        approx(0.0)


def test_annualize(bm):
    assert bm.annualize(0.10, 12) == approx(0.10)
    assert bm.annualize(0.21, 24) == approx(0.1)
    assert bm.annualize(0.10, 6) is None
    assert bm.annualize(0.10, 1) is None


def test_horizons(bm):
    assert tuple(bm.HORIZONS_M) == (1, 3, 6, 12, 36, 60)


# ---------------------------------------------------------------------------
# special_dist (Failure modes: SPECIAL_DIST)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("amounts", "expected"), [
    ([1.0, 1.0, 1.0, 3.0], True),    # 3 > 2 x median(1, 1, 1)
    ([1.0, 1.1, 0.9, 2.5], True),
    ([1.0, 1.0, 2.0], False),        # exactly 2x is not "more than"
    ([1.60, 1.70, 1.75, 1.80], False),
    ([5.0], False),                  # needs >= 2 payments
    ([], False),
    ([1.0, 3.0], True),              # median of the other payment = 1.0
    ([1.0, 2.0], False),
])
def test_special_dist(bm, amounts, expected):
    assert bm.special_dist(amounts) is expected


# ---------------------------------------------------------------------------
# load_benchmarks, primary_return_benchmark (real universe/benchmarks.csv)
# ---------------------------------------------------------------------------


def test_load_benchmarks_real_file(bm):
    b = bm.load_benchmarks(str(CSV))
    assert [x.key for x in b] == ["SPX_TR", "SPX", "SPY", "IVV", "VOO", "TSX", "XIC", "XIU", "ZCN"]
    first = b[0]
    assert isinstance(first, bm.Benchmark)
    assert (first.key, first.symbol, first.market, first.role, first.note) == (
        "SPX_TR", "^SP500TR", "US", "primary_return", "S&P 500 total-return index")
    assert {x.key: x.symbol for x in b}["XIC"] == "XIC.TO"
    assert {x.market for x in b} == {"US", "TSX"}


def test_primary_return_benchmark(bm):
    assert bm.primary_return_benchmark("US") == "SPX_TR"
    assert bm.primary_return_benchmark("TSX") == "XIC"


# ---------------------------------------------------------------------------
# benchmark_metrics on a small synthetic series
# ---------------------------------------------------------------------------

LEVELS = [  # (date, close, adj_close)
    (D("2021-11-10"), 260.0, 250.0),
    (D("2023-11-10"), 310.0, 300.0),
    (D("2025-11-10"), 410.0, 400.0),
    (D("2026-05-08"), 420.0, 410.0),  # 6m back = 2026-05-10 (Sunday) -> this Friday
    (D("2026-08-10"), 430.0, 420.0),
    (D("2026-10-09"), 445.0, 440.0),  # 1m back = 2026-10-10 (Saturday) -> this Friday
    (D("2026-11-10"), 500.0, 452.0),
]
KEYS = {"close", "dist_ttm", "dist_yield", "yield_spread", "special_dist", "tr_1m", "tr_3m",
        "tr_6m", "tr_12m", "tr_ann_3y", "tr_ann_5y"}


def check_metrics(m):
    assert set(m) == KEYS
    assert m["close"] == approx(500.0)
    assert m["dist_ttm"] == approx(6.85)
    assert m["dist_yield"] == approx(0.0137)
    assert m["special_dist"] is False
    assert m["tr_1m"] == approx(452 / 440 - 1)
    assert m["tr_3m"] == approx(452 / 420 - 1)
    assert m["tr_6m"] == approx(452 / 410 - 1)
    assert m["tr_12m"] == approx(0.13)
    assert m["tr_ann_3y"] == pytest.approx(0.146408, abs=1e-6)
    assert m["tr_ann_5y"] == approx((452 / 250) ** (1 / 5) - 1)


def test_benchmark_metrics(bm):
    m = bm.benchmark_metrics(LEVELS, GOLDEN_DISTS, AS_OF, rf=0.04)
    check_metrics(m)
    assert m["yield_spread"] == approx(-0.0263)


def test_benchmark_metrics_without_rf(bm):
    m = bm.benchmark_metrics(LEVELS, GOLDEN_DISTS, AS_OF)
    assert m["yield_spread"] is None
    assert m["dist_yield"] == approx(0.0137)


def test_benchmark_metrics_look_ahead(bm):
    later_levels = LEVELS + [(D("2026-11-11"), 900.0, 900.0), (D("2027-01-04"), 1.0, 1.0)]
    later_dists = GOLDEN_DISTS + [(D("2026-12-15"), 50.0)]
    m = bm.benchmark_metrics(later_levels, later_dists, AS_OF, rf=0.04)
    check_metrics(m)
    assert m["yield_spread"] == approx(-0.0263)


def test_benchmark_metrics_short_history(bm):
    m = bm.benchmark_metrics(LEVELS[2:], GOLDEN_DISTS, AS_OF, rf=0.04)  # starts 2025-11-10
    assert m["tr_12m"] == approx(0.13)
    assert m["tr_ann_3y"] is None
    assert m["tr_ann_5y"] is None


def test_benchmark_metrics_special_dist_flag(bm):
    dists = GOLDEN_DISTS + [(D("2026-10-15"), 5.0)]
    m = bm.benchmark_metrics(LEVELS, dists, AS_OF)
    assert m["special_dist"] is True
    assert m["dist_ttm"] == approx(11.85)


# ---------------------------------------------------------------------------
# Backtest excess: forward_returns on the benchmark + benchmark_excess
# ---------------------------------------------------------------------------

D1, D2 = D("2025-01-31"), D("2025-02-28")
# Sessions: t0(D1)=02-03, t1(D1)=05-01 (target 04-30); t0(D2)=03-03, t1(D2)=06-02 (target 05-31).
BENCH_PRICES = pd.DataFrame(
    [("SPX_TR", D("2025-02-03"), 100.0), ("SPX_TR", D("2025-03-03"), 110.0),
     ("SPX_TR", D("2025-05-01"), 105.0), ("SPX_TR", D("2025-06-02"), 121.0),
     ("SPX_TR", D("2025-06-30"), 121.0)],
    columns=["ticker", "date", "adj_close"])

OBS = pd.DataFrame([
    (D1, "A", 3, "BUY", 0.10),
    (D1, "B", 3, "SELL", -0.02),
    (D1, "C", 3, "HOLD", 0.04),
    (D2, "A", 3, "BUY", 0.20),
    (D2, "B", 3, "BUY", NAN),     # no return: does not count
    (D2, "C", 3, "SELL", 0.06),
], columns=["date", "ticker", "horizon", "label", "ret"])

EXPECTED = {  # (date, bucket): (n, bucket_ret, bench_ret, excess)
    (D1, "BUY"): (1, 0.10, 0.05, 0.05),
    (D1, "SELL"): (1, -0.02, 0.05, -0.07),
    (D1, "ALL"): (3, 0.04, 0.05, -0.01),
    (D2, "BUY"): (1, 0.20, 0.10, 0.10),
    (D2, "SELL"): (1, 0.06, 0.10, -0.04),
    (D2, "ALL"): (2, 0.13, 0.10, 0.03),
}


def bench_fwd():
    f = metrics.forward_returns(BENCH_PRICES, [D1, D2], horizons=(3,))
    return f[["date", "horizon", "ret"]].reset_index(drop=True)


def test_bench_forward_returns_via_harness():
    f = bench_fwd().set_index("date")["ret"]
    assert f[D1] == approx(105 / 100 - 1)
    assert f[D2] == approx(121 / 110 - 1)


def by_key(df):
    return {(r.date, r.bucket): r for r in df.itertuples(index=False)}


def test_benchmark_excess_hand_example():
    out = metrics.benchmark_excess(OBS, bench_fwd())
    assert list(out.columns) == ["date", "horizon", "bucket", "n", "bucket_ret", "bench_ret",
                                 "excess"]
    assert len(out) == 6
    got = by_key(out)
    assert set(got) == set(EXPECTED)
    for k, (n, br, bench, ex) in EXPECTED.items():
        r = got[k]
        assert r.horizon == 3
        assert r.n == n
        assert r.bucket_ret == approx(br)
        assert r.bench_ret == approx(bench)
        assert r.excess == pytest.approx(ex, abs=1e-12)


def test_benchmark_excess_allowed_dates():
    out = metrics.benchmark_excess(OBS, bench_fwd(), allowed_dates=[D2])
    assert set(out["date"]) == {D2}
    assert len(out) == 3


def test_benchmark_excess_empty_bucket_and_missing_bench():
    obs = pd.DataFrame([(D1, "A", 3, "BUY", 0.10), (D1, "B", 3, "HOLD", 0.00),
                        (D2, "A", 3, "BUY", 0.20)],
                       columns=["date", "ticker", "horizon", "label", "ret"])
    bf = pd.DataFrame([(D1, 3, 0.05), (D2, 3, NAN)], columns=["date", "horizon", "ret"])
    got = by_key(metrics.benchmark_excess(obs, bf))
    assert got[(D1, "SELL")].n == 0 and math.isnan(got[(D1, "SELL")].excess)
    assert got[(D1, "ALL")].excess == pytest.approx(0.05 - 0.05, abs=1e-12)
    assert got[(D2, "BUY")].n == 1 and math.isnan(got[(D2, "BUY")].excess)
    assert math.isnan(got[(D2, "BUY")].bench_ret)


def test_yearly_benchmark_excess():
    rows = []
    for d, b, ex in [
        (D1, "BUY", 0.05), (D1, "SELL", -0.07), (D1, "ALL", -0.01),
        (D2, "BUY", 0.10), (D2, "SELL", NAN), (D2, "ALL", 0.03),
        (D("2026-01-30"), "BUY", NAN), (D("2026-01-30"), "SELL", 0.02),
        (D("2026-01-30"), "ALL", NAN),
    ]:
        rows.append((d, 3, b, 1, NAN, NAN, ex))
    by_date = pd.DataFrame(rows, columns=["date", "horizon", "bucket", "n", "bucket_ret",
                                          "bench_ret", "excess"])
    ys = metrics.yearly_benchmark_excess(by_date)
    assert list(ys.columns) == ["year", "horizon", "bucket", "n_dates", "excess"]
    got = {(int(r.year), r.bucket): r for r in ys.itertuples(index=False)}
    # Years without any defined excess for a bucket are omitted (2026 BUY and ALL).
    assert set(got) == {(2025, "BUY"), (2025, "SELL"), (2025, "ALL"), (2026, "SELL")}
    assert got[(2025, "BUY")].excess == approx(0.075)
    assert got[(2025, "ALL")].excess == approx(0.01)
    assert got[(2025, "SELL")].excess == approx(-0.07)
    assert got[(2026, "SELL")].excess == approx(0.02)
    assert got[(2025, "BUY")].n_dates == 2
    # SPEC-AMBIGUITY: n_dates is read as the number of dates with a defined (non-NaN) excess
    # ("mean over the dates of that year with both defined"), not all dates of the year.
    assert got[(2025, "SELL")].n_dates == 1


def test_yearly_from_hand_example():
    ys = metrics.yearly_benchmark_excess(metrics.benchmark_excess(OBS, bench_fwd()))
    got = {r.bucket: r.excess for r in ys.itertuples(index=False)}
    assert got["BUY"] == approx((0.05 + 0.10) / 2)
    assert got["SELL"] == approx((-0.07 - 0.04) / 2)
    assert got["ALL"] == approx((-0.01 + 0.03) / 2)
