"""Spec tests for Report v2 (docs/models/residual_income.md §Report v2, §Insurer CSM, §Data mapping;
docs/models/peer_regression.md §Data mapping and look-ahead).

Written from the specs and the SW engine API agreed for P7-5:
    valuation.engine.run_signals(con, as_of, tickers=None, liquidity=None,
                                 csm_path="universe/insurer_csm.csv") -> (DataFrame, info)
    valuation.report.screen.write_signals(df, as_of, out_root="reports", disclaimer=None)
        -> (csv_path, parquet_path)
Each test builds a small in-memory DuckDB with rows inserted directly by SQL.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pyarrow.parquet as pq
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

REL = 1e-9
AS_OF = date(2026, 11, 30)
EARLY = date(2026, 11, 1)  # before the 2026-09-30 quarter / balance sheet were filed
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}

COLUMNS = [
    "ticker", "market", "sector", "eligible", "elig_fail_reason", "price", "bvps", "pb", "roe0",
    "a_signal", "a_no_signal_reason", "a_mos", "a_v_ri", "a_mos_up", "a_mos_down",
    "a_justified_pb", "b_signal", "b_no_signal_reason", "b_implied_upside", "b_resid_pct",
    "b_fitted_pb", "b_fair_value", "flags",
]

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
# Permissive liquidity: every name with a traded bar passes; a name whose last bar has
# volume 0 fails E2 (mdtv 0 < 1).
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

BAR_DATES = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)] + \
            [date(2026, 11, 23) + timedelta(days=i) for i in range(5)]
QUARTERS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
            date(2026, 9, 30)]
FILED = {q: q + timedelta(days=36) for q in QUARTERS}  # 2026-09-30 -> filed 2026-11-05
DIV_DATES = [date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15)]

CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def ref_ri(b0, roe0, r, b, horizon=10):
    B, V = b0, b0
    for t in range(1, horizon + 1):
        roe_t = r + (roe0 - r) * (horizon - t) / horizon
        V += (roe_t - r) * B / (1 + r) ** t
        B = B * (1 + roe_t * b)
    return V


# ---------------------------------------------------------------------------
# DB builders
# ---------------------------------------------------------------------------


def add_security(con, ticker, currency, sector, exchange=None):
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [ticker, exchange or ("TSX" if currency == "CAD" else "NYSE"), ticker, currency,
         currency, sector, None, 1, 1.0, sector])


def add_bars(con, ticker, close, volume=1e6, dates=BAR_DATES):
    for d in dates:
        con.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?)",
                    [ticker, d, close, close, volume, "test"])


def add_fact(con, ticker, period_end, filed, item, value, unit, source="sec_edgar", est=False):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [ticker, period_end, filed, item, value, unit, source, est])


def add_bs(con, ticker, period_end, filed, equity, shares, ccy, source="sec_edgar", est=False):
    add_fact(con, ticker, period_end, filed, "total_equity", equity, ccy, source, est)
    add_fact(con, ticker, period_end, filed, "shares_outstanding", shares, "shares", source, est)


def add_q(con, ticker, period_end, filed, value, ccy, source="sec_edgar", est=False):
    add_fact(con, ticker, period_end, filed, "ni_common_q", value, ccy, source, est)


def add_div(con, ticker, ex_date, amount, ccy):
    con.execute("INSERT INTO dividends VALUES (?,?,?,?,?)", [ticker, ex_date, amount, ccy, "test"])


def add_rate(con, series, d, rate):
    con.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [d, series, rate, "test"])


def add_bank(con, ticker, *, ce_start=True, volume=1e6, sector="Financials"):
    """The spec's G3 BASE bank in DB form (US, USD)."""
    add_security(con, ticker, "USD", sector)
    add_bars(con, ticker, 75.0, volume)
    ni = {QUARTERS[0]: 2.5e9, QUARTERS[1]: 3.0e9, QUARTERS[2]: 3.2e9, QUARTERS[3]: 3.3e9,
          QUARTERS[4]: 3.5e9}  # last four sum to 13.0e9; the four before to 12.0e9
    for q, v in ni.items():
        add_q(con, ticker, q, FILED[q], v, "USD")
    equity = {date(2026, 6, 30): 98e9, date(2026, 9, 30): 100e9}
    if ce_start:
        equity.update({date(2025, 6, 30): 90e9, date(2025, 9, 30): 92e9})
    for pe, eq in equity.items():
        add_bs(con, ticker, pe, pe + timedelta(days=36), eq, 2.0e9, "USD")
    for d in DIV_DATES:
        add_div(con, ticker, d, 0.65, "USD")


def add_insurer(con, ticker="INS.TO"):
    """The spec's G6 insurer in DB form (TSX, CAD)."""
    add_security(con, ticker, "CAD", "Financials")
    add_bars(con, ticker, 45.0)
    for q in QUARTERS[1:]:
        add_q(con, ticker, q, FILED[q], 1.5e9, "CAD")
    add_bs(con, ticker, date(2026, 9, 30), FILED[date(2026, 9, 30)], 44.2e9, 1.7e9, "CAD")
    add_bs(con, ticker, date(2025, 9, 30), FILED[date(2025, 9, 30)], 42.0e9, 1.7e9, "CAD")
    for d in DIV_DATES:
        add_div(con, ticker, d, 0.425, "CAD")


def add_rates(con):
    add_rate(con, "US10Y", date(2026, 10, 30), 0.042)
    add_rate(con, "CA10Y", date(2026, 10, 30), 0.032)
    con.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])


@pytest.fixture
def con():
    c = connect(":memory:")
    add_rates(c)
    add_bank(c, "BANK")
    add_bank(c, "SPOT", ce_start=False)
    add_bank(c, "ILLQ", volume=0.0)  # fails E2 -> NOT_ELIGIBLE
    add_insurer(c)
    add_security(c, "NOBS", "USD", "Financials")  # prices, no balance sheet
    add_bars(c, "NOBS", 30.0)
    add_security(c, "NOPX", "USD", "Utilities")  # nothing at all
    yield c
    c.close()


@pytest.fixture
def csm(tmp_path):
    def write(rows=()):
        p = tmp_path / f"csm_{len(list(tmp_path.iterdir()))}.csv"
        p.write_text(CSM_HEADER + "".join(r + "\n" for r in rows))
        return str(p)
    return write


def run(con, csm_path, as_of=AS_OF, tickers=None):
    return run_signals(con, as_of, tickers=tickers, liquidity=LIQ, csm_path=csm_path)


# ---------------------------------------------------------------------------
# helpers on report rows
# ---------------------------------------------------------------------------


def blank(x):
    if x is None:
        return True
    if isinstance(x, str):
        return x == ""
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def flagset(x):
    return set() if blank(x) else {f for f in str(x).split(",") if f}


def row(df, ticker):
    sub = df[df["ticker"] == ticker]
    assert len(sub) == 1, ticker
    return sub.iloc[0].to_dict()


def assert_rows_equal(a, b):
    assert set(a) == set(b)
    for k in a:
        va, vb = a[k], b[k]
        if blank(va) or blank(vb):
            assert blank(va) and blank(vb), k
        elif isinstance(va, float) or isinstance(vb, float):
            assert float(va) == approx(float(vb)), k
        else:
            assert va == vb, k


# ---------------------------------------------------------------------------
# Shape of the report
# ---------------------------------------------------------------------------


def test_columns_exact_and_in_spec_order(con, csm):
    df, _ = run(con, csm())
    assert list(df.columns) == COLUMNS


def test_one_row_per_security_sorted(con, csm):
    df, _ = run(con, csm())
    assert list(df["ticker"]) == sorted(["BANK", "SPOT", "ILLQ", "INS.TO", "NOBS", "NOPX"])
    assert df["ticker"].is_unique


def test_tickers_argument_restricts_rows(con, csm):
    df, _ = run(con, csm(), tickers={"SPOT", "BANK"})
    assert list(df["ticker"]) == ["BANK", "SPOT"]
    assert list(df.columns) == COLUMNS


def test_labels_no_vote_no_screen_prefix(con, csm):
    df, _ = run(con, csm())
    for col in ("a_signal", "b_signal"):
        assert set(df[col]) <= LABELS
        assert not any(str(v).startswith("SCREEN_") for v in df[col])
    for col in ("a_no_signal_reason", "b_no_signal_reason"):
        assert not any(str(v).startswith("SCREEN_") for v in df[col] if not blank(v))
    assert not any("vote" in c.lower() or "combined" in c.lower() for c in df.columns)


def test_reason_present_iff_no_signal(con, csm):
    df, _ = run(con, csm())
    for m in ("a", "b"):
        for sig, reason in zip(df[f"{m}_signal"], df[f"{m}_no_signal_reason"]):
            assert (sig == "NO_SIGNAL") == (not blank(reason)), (m, sig, reason)


def test_info_dict(con, csm):
    df, info = run(con, csm())
    assert isinstance(info["run_id"], str) and info["run_id"]
    assert isinstance(info["fits"], dict)
    assert "Financials" in info["fits"]
    assert info["fits"]["Financials"].status == "FEW_NAMES"
    counts = info["counts"]
    assert set(counts) >= {"A", "B"}
    for model, col in (("A", "a_signal"), ("B", "b_signal")):
        for market in ("TSX", "US"):
            got = {k: v for k, v in counts[model].get(market, {}).items() if v}
            exp = df[df["market"] == market][col].value_counts().to_dict()
            assert got == exp, (model, market)


def test_runs_twice_on_same_connection(con, csm):
    p = csm()
    df1, info1 = run(con, p)
    df2, info2 = run(con, p)
    assert info1["run_id"] != info2["run_id"]
    for t in df1["ticker"]:
        assert_rows_equal(row(df1, t), row(df2, t))


# ---------------------------------------------------------------------------
# (a) US bank reproducing G3 BASE
# ---------------------------------------------------------------------------


def test_us_bank_g3_base(con, csm):
    df, _ = run(con, csm())
    r = row(df, "BANK")
    assert r["market"] == "US"
    assert r["sector"] == "Financials"
    assert bool(r["eligible"]) is True
    assert blank(r["elig_fail_reason"])
    assert r["price"] == approx(75.0)
    assert r["bvps"] == approx(50.0)
    assert r["pb"] == approx(1.5)
    assert r["roe0"] == approx(13 / 96)
    assert r["a_signal"] == "SELL"
    assert blank(r["a_no_signal_reason"])
    assert r["a_v_ri"] == approx(58.567222546400856)
    assert r["a_mos"] == approx(-0.2191036993813219)
    assert r["a_mos_up"] == approx(-0.24789022524478166)
    assert r["a_mos_down"] == approx(-0.1886437060853584)
    assert r["a_justified_pb"] == approx(1.8349358974358971)
    # Financials has < 12 fit names in this DB -> Model B has no fit.
    assert (r["b_signal"], r["b_no_signal_reason"]) == ("NO_SIGNAL", "NO_FIT")
    assert blank(r["b_fitted_pb"]) and blank(r["b_resid_pct"])
    assert flagset(r["flags"]) == set()


def test_ineligible_name_keeps_numbers(con, csm):
    df, _ = run(con, csm())
    r = row(df, "ILLQ")
    assert bool(r["eligible"]) is False
    assert "E2" in str(r["elig_fail_reason"])
    assert r["a_signal"] == "NOT_ELIGIBLE" and blank(r["a_no_signal_reason"])
    assert r["b_signal"] == "NOT_ELIGIBLE" and blank(r["b_no_signal_reason"])
    assert r["a_v_ri"] == approx(58.567222546400856)  # still computed for the human


# ---------------------------------------------------------------------------
# (b) No look-ahead
# ---------------------------------------------------------------------------


def _add_future_data(con):
    # quarter and balance sheet with period_end after as_of
    add_q(con, "BANK", date(2026, 12, 31), date(2027, 2, 10), 9.0e9, "USD")
    add_bs(con, "BANK", date(2026, 12, 31), date(2027, 2, 10), 500e9, 2.0e9, "USD")
    # restatements of the current period, filed after as_of
    add_q(con, "BANK", date(2026, 9, 30), date(2026, 12, 15), 9.0e9, "USD")
    add_bs(con, "BANK", date(2026, 9, 30), date(2026, 12, 15), 10e9, 2.0e9, "USD")
    # price, rate, FX and dividend dated after as_of
    add_bars(con, "BANK", 999.0, 5e6, dates=[date(2026, 12, 1), date(2026, 12, 2)])
    add_rate(con, "US10Y", date(2026, 12, 1), 0.09)
    add_rate(con, "CA10Y", date(2026, 12, 1), 0.09)
    con.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 12, 1), "USDCAD", 2.0])
    add_div(con, "BANK", date(2026, 12, 10), 5.0, "USD")
    add_div(con, "INS.TO", date(2026, 12, 10), 5.0, "CAD")


def test_no_look_ahead_later_data_changes_nothing(con, csm):
    p = csm()
    before, _ = run(con, p)
    _add_future_data(con)
    after, _ = run(con, p)
    assert list(before["ticker"]) == list(after["ticker"])
    for t in before["ticker"]:
        assert_rows_equal(row(before, t), row(after, t))
    assert row(after, "BANK")["a_v_ri"] == approx(58.567222546400856)


def test_no_look_ahead_earlier_as_of_uses_earlier_ttm(con, csm):
    df, _ = run(con, csm(), as_of=EARLY)
    r = row(df, "BANK")
    # q0 = 2026-06-30 (the 2026-09-30 quarter was filed 2026-11-05): TTM 12.0e9,
    # CE_end 98e9 (2026-06-30), CE_start 90e9 (2025-06-30), bvps 98e9/2e9 = 49.
    roe = 12.0e9 / ((98e9 + 90e9) / 2)
    b = 1 - 2.6 / (12.0e9 / 2.0e9)
    assert r["bvps"] == approx(49.0)
    assert r["pb"] == approx(75.0 / 49.0)
    assert r["roe0"] == approx(roe)
    v = ref_ri(49.0, roe, 0.092, b)
    assert r["a_v_ri"] == approx(v)
    assert r["a_mos"] == approx(v / 75.0 - 1)
    assert r["a_v_ri"] != approx(58.567222546400856)


def test_no_look_ahead_earlier_as_of_with_future_data(con, csm):
    p = csm()
    before, _ = run(con, p, as_of=EARLY)
    _add_future_data(con)
    after, _ = run(con, p, as_of=EARLY)
    for t in before["ticker"]:
        assert_rows_equal(row(before, t), row(after, t))


# ---------------------------------------------------------------------------
# (c) CE_start missing
# ---------------------------------------------------------------------------


def test_spot_equity_flag(con, csm):
    df, _ = run(con, csm())
    r = row(df, "SPOT")
    assert "ROE_SPOT_EQUITY" in flagset(r["flags"])
    assert r["roe0"] == approx(0.13)
    assert r["a_v_ri"] == approx(57.455952111741375)
    assert r["a_signal"] == "SELL"
    assert "ROE_SPOT_EQUITY" not in flagset(row(df, "BANK")["flags"])


# ---------------------------------------------------------------------------
# (d) Insurer CSM file
# ---------------------------------------------------------------------------

G6_B0 = (44.2e9 + 19.55e9) / 1.7e9  # 37.5
G6_V = 39.47586691199252


def test_insurer_without_file_row_is_unadjusted(con, csm):
    df, _ = run(con, csm())
    r = row(df, "INS.TO")
    assert r["market"] == "TSX"
    assert r["bvps"] == approx(26.0)
    assert "CSM_ADJUSTED" not in flagset(r["flags"])
    assert r["a_no_signal_reason"] != "NEEDS_CSM_INPUT"


def test_insurer_csm_adjusted(con, csm):
    p = csm(["INS.TO,2026-09-30,2026-11-10,19550000000,37.40,test,"])
    df, _ = run(con, p)
    r = row(df, "INS.TO")
    assert "CSM_ADJUSTED" in flagset(r["flags"])
    assert r["bvps"] == approx(G6_B0)
    assert r["bvps"] == approx(37.5)
    assert r["pb"] == approx(1.2)
    assert r["roe0"] == approx(0.09577015163607343)
    assert r["a_v_ri"] == approx(G6_V)
    assert r["a_mos"] == approx(-0.12275851306683294)
    assert r["a_signal"] == "HOLD"
    assert r["b_no_signal_reason"] != "NEEDS_CSM_INPUT"
    # a ticker not in the file is unaffected
    b = row(df, "BANK")
    assert b["bvps"] == approx(50.0)
    assert "CSM_ADJUSTED" not in flagset(b["flags"])
    assert b["a_v_ri"] == approx(58.567222546400856)


def test_insurer_csm_latest_filed_row_used(con, csm):
    p = csm([
        "INS.TO,2026-09-30,2026-11-10,10000000000,,test,first",
        "INS.TO,2026-09-30,2026-11-20,19550000000,,test,revised",
        "INS.TO,2026-09-30,2026-12-05,5000000000,,test,after as_of",
        "INS.TO,2026-06-30,2026-08-10,1000000000,,test,other period",
    ])
    df, _ = run(con, p)
    r = row(df, "INS.TO")
    assert r["bvps"] == approx(G6_B0)
    assert r["a_v_ri"] == approx(G6_V)


@pytest.mark.parametrize("rows", [
    ["INS.TO,2026-09-30,2026-12-05,19550000000,,test,filed after as_of"],
    ["INS.TO,2026-06-30,2026-08-10,19550000000,,test,another period"],
    ["INS.TO,2026-09-30,2026-11-10,,,test,blank = needs human input"],
    ["INS.TO,2026-12-31,2026-11-10,19550000000,,test,future period"],
])
def test_insurer_csm_missing_needs_input(con, csm, rows):
    df, _ = run(con, csm(rows))
    r = row(df, "INS.TO")
    assert (r["a_signal"], r["a_no_signal_reason"]) == ("NO_SIGNAL", "NEEDS_CSM_INPUT")
    assert (r["b_signal"], r["b_no_signal_reason"]) == ("NO_SIGNAL", "NEEDS_CSM_INPUT")
    assert blank(r["bvps"]) and blank(r["pb"]) and blank(r["roe0"]) and blank(r["a_v_ri"])
    assert "CSM_ADJUSTED" not in flagset(r["flags"])
    b = row(df, "BANK")  # not in the file: unaffected
    assert b["a_signal"] == "SELL" and b["bvps"] == approx(50.0)


def test_insurer_csm_row_filed_after_earlier_as_of(con, csm):
    """A row public on 2026-11-10 doesn't exist for an as_of before it."""
    p = csm(["INS.TO,2026-09-30,2026-11-10,19550000000,,test,"])
    df, _ = run(con, p, as_of=date(2026, 11, 8))  # balance sheet 2026-09-30 is public
    r = row(df, "INS.TO")
    assert r["a_no_signal_reason"] == "NEEDS_CSM_INPUT"
    assert r["b_no_signal_reason"] == "NEEDS_CSM_INPUT"


# ---------------------------------------------------------------------------
# (e) Model B uses Model A's b0 and roe_raw (spec G2 sector built in the DB)
# ---------------------------------------------------------------------------

G2 = [  # ticker, price, roe, eligible
    ("U01", 18, 0.02, True), ("U02", 21, 0.05, True), ("U03", 25, 0.07, True),
    ("U04", 19, 0.08, True), ("U05", 30, 0.08, True), ("U06", 30, 0.09, True),
    ("U07", 33, 0.10, True), ("U08", 36, 0.11, True), ("U09", 52, 0.12, True),
    ("U10", 41, 0.13, True), ("U11", 44, 0.14, True), ("U12", 49, 0.16, True),
    ("U13", 60, 0.40, True), ("U14", 46, 0.15, True), ("U15", 9, -0.05, True),
    ("U16", 30, 0.10, False),
]
# ticker: (fitted_pb, upside, resid_pct, signal)
G2_B = {
    "U01": (1.004857315928173, 0.11650812880908124, 3 / 14, "HOLD"),
    "U02": (1.0887993870061192, 0.03695179714868502, 4 / 14, "HOLD"),
    "U03": (1.2685756761495808, 0.014860540919664711, 7 / 14, "HOLD"),
    "U04": (1.3693063937629153, 0.44137515132938465, 2 / 14, "BUY"),
    "U05": (1.3693063937629153, -0.0871290708247231, 13 / 14, "HOLD"),
    "U06": (1.4780355916101569, -0.014642938926562099, 8 / 14, "HOLD"),
    "U07": (1.595398385647669, -0.03309188748626124, 11 / 14, "HOLD"),
    "U08": (1.7220803229470059, -0.043288709473885745, 12 / 14, "HOLD"),
    "U09": (1.8588213861563885, -0.28506869763215825, 14 / 14, "SELL"),
    "U10": (2.006420315934756, -0.021258382470850767, 10 / 14, "HOLD"),
    "U11": (2.165739276606875, -0.015573056087784143, 9 / 14, "HOLD"),
    "U12": (2.523333682929258, 0.029932115481329902, 5 / 14, "HOLD"),
    "U13": (4.794269422270641, 0.598089807423547, 1 / 14, "BUY"),
    "U14": (2.3377088922928304, 0.01639517056210016, 6 / 14, "HOLD"),
    "U15": (1.004857315928173, 1.2330162576181625, 0.0, "BUY"),
    "U16": (1.595398385647669, 0.06359892376511267, None, "NOT_ELIGIBLE"),
}


@pytest.fixture
def g2con():
    c = connect(":memory:")
    add_rates(c)
    for t, price, roe, elig in G2:
        add_security(c, t, "USD", "Financials")
        add_bars(c, t, float(price), 1e6 if elig else 0.0)
        for q in QUARTERS[1:]:
            add_q(c, t, q, FILED[q], float(round(roe * 5e9)), "USD")  # TTM = roe * 20e9
        for pe in (date(2025, 9, 30), date(2026, 9, 30)):
            add_bs(c, t, pe, pe + timedelta(days=36), 20e9, 1e9, "USD")  # bvps 20
    yield c
    c.close()


def test_model_b_in_report_reproduces_g2(g2con, csm):
    df, info = run(g2con, csm())
    f = info["fits"]["Financials"]
    assert f.n_fit == 14
    assert f.n_scored == 15
    assert f.status == "OK"
    assert f.slope == approx(7.640957345191646)
    assert f.intercept == approx(-0.2969722579041446)
    assert f.r2 == approx(0.7449629739969964)
    for t, (fitted, upside, pct, signal) in G2_B.items():
        r = row(df, t)
        assert r["bvps"] == approx(20.0), t
        assert r["b_fitted_pb"] == approx(fitted), t
        assert r["b_fair_value"] == approx(20.0 * fitted), t
        assert r["b_implied_upside"] == approx(upside), t
        if pct is None:
            assert blank(r["b_resid_pct"]), t
        else:
            assert r["b_resid_pct"] == approx(pct), t
        assert r["b_signal"] == signal, t


def test_model_b_uses_roe_raw_not_clipped_roe0(g2con, csm):
    df, _ = run(g2con, csm())
    u13 = row(df, "U13")
    assert u13["roe0"] == approx(0.35)  # Model A clips 0.40
    assert {"ROE_CLIPPED", "ROE_WINSORIZED"} <= flagset(u13["flags"])
    u15 = row(df, "U15")
    assert {"ROE_WINSORIZED", "NEG_ROE_UNFITTED"} <= flagset(u15["flags"])


def test_model_b_counts(g2con, csm):
    df, info = run(g2con, csm())
    got = {k: v for k, v in info["counts"]["B"]["US"].items() if v}
    assert got == {"BUY": 3, "SELL": 1, "HOLD": 11, "NOT_ELIGIBLE": 1}


def test_model_b_pools_tsx_and_us(g2con, csm):
    """Moving U07 to the TSX (same ratios in CAD) leaves the Financials fit unchanged."""
    df0, info0 = run(g2con, csm())
    g2con.execute("UPDATE securities SET ticker='U07.TO', currency='CAD', "
                  "financial_currency='CAD', exchange='TSX' WHERE ticker='U07'")
    for tbl in ("prices_daily", "fundamentals"):
        g2con.execute(f"UPDATE {tbl} SET ticker='U07.TO' WHERE ticker='U07'")
    g2con.execute("UPDATE fundamentals SET unit='CAD' WHERE ticker='U07.TO' AND unit='USD'")
    df1, info1 = run(g2con, csm())
    f0, f1 = info0["fits"]["Financials"], info1["fits"]["Financials"]
    assert f1.n_fit == f0.n_fit
    assert f1.slope == approx(f0.slope)
    assert f1.intercept == approx(f0.intercept)
    r = row(df1, "U07.TO")
    assert r["market"] == "TSX"
    assert r["b_resid_pct"] == approx(11 / 14)


# ---------------------------------------------------------------------------
# (f) No balance sheet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["NOBS", "NOPX"])
def test_no_balance_sheet(con, csm, ticker):
    df, _ = run(con, csm())
    r = row(df, ticker)
    if not blank(r["eligible"]) and bool(r["eligible"]):
        expected = ("NO_SIGNAL", "NO_DATA")
    else:
        expected = ("NOT_ELIGIBLE", None)
    for m in ("a", "b"):
        sig, reason = r[f"{m}_signal"], r[f"{m}_no_signal_reason"]
        assert sig == expected[0], m
        if expected[1] is None:
            assert blank(reason), m
        else:
            assert reason == expected[1], m
    assert blank(r["bvps"]) and blank(r["pb"]) and blank(r["a_v_ri"])


# ---------------------------------------------------------------------------
# (g) CSV comment line and parquet metadata
# ---------------------------------------------------------------------------


def disclaimer(as_of):
    return ("Screening signals for human review, not investment advice. Models: "
            f"residual_income/0.1.0, peer_regression/0.1.0. as_of {as_of.isoformat()}. "
            "No combined vote (Phase 10).")


def test_write_signals_files(con, csm, tmp_path):
    df, _ = run(con, csm())
    out = tmp_path / "reports"
    paths = write_signals(df, AS_OF, out_root=out)
    csv_path = next(p for p in paths if str(p).endswith(".csv"))
    pq_path = next(p for p in paths if str(p).endswith(".parquet"))
    assert csv_path == out / AS_OF.isoformat() / "screen_signals.csv"
    assert pq_path == out / AS_OF.isoformat() / "screen_signals.parquet"

    first = csv_path.read_text().splitlines()[0]
    assert first == "# " + disclaimer(AS_OF)
    assert first.startswith("# Screening signals for human review, not investment advice.")
    back = pd.read_csv(csv_path, skiprows=1)
    assert list(back.columns) == COLUMNS
    assert list(back["ticker"]) == list(df["ticker"])

    table = pq.read_table(pq_path)
    assert table.column_names == COLUMNS
    assert table.num_rows == len(df)
    meta = table.schema.metadata
    assert b"disclaimer" in meta
    text = meta[b"disclaimer"].decode()
    assert "not investment advice" in text
    assert text.lstrip("# ") == disclaimer(AS_OF)
    assert "SCREEN_" not in csv_path.read_text()


def test_written_values_round_trip(con, csm, tmp_path):
    df, _ = run(con, csm())
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    back = pd.read_csv(csv_path, skiprows=1)
    r = back[back["ticker"] == "BANK"].iloc[0]
    assert r["a_signal"] == "SELL"
    assert float(r["a_v_ri"]) == pytest.approx(58.567222546400856, rel=1e-6)
    pdf = pq.read_table(pq_path).to_pandas()
    r = pdf[pdf["ticker"] == "BANK"].iloc[0]
    assert r["a_v_ri"] == approx(58.567222546400856)


def test_nan_safe_numbers(con, csm):
    df, _ = run(con, csm())
    for col in ("a_v_ri", "a_mos", "pb", "bvps"):
        for v in df[col]:
            assert blank(v) or math.isfinite(float(v))
