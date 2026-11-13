"""Spec tests for Model F in the engine and report (docs/models/accruals.md v0.1.0,
§Data mapping and look-ahead, §Report and backtest).

Written from the spec only, with the engine API used by test_altman_z_engine_spec.py:
    run_signals(con, as_of, tickers=None, liquidity=None, csm_path=...) -> (DataFrame, info)
    write_signals(df, as_of, out_root=...) -> (csv_path, parquet_path)
The store is EDGAR-like: balance-sheet filings per (period_end, filed_date) and discrete quarterly
`ni_common_q` / `cfo_q` facts (each quarter = TTM / 4). A real F signal needs a (market, sector)
group of >= 12 names, so the twelve G2 X1 names A01..A12 (US Industrials, TA_t 1100 at
2026-09-30, TA_prev 900 at 2025-09-30) are built in a loop; amounts are the spec goldens in
$M x 1e6, so every ratio equals the golden's (relative tolerance 1e-9). Probes of the
prior-TA window and of the look-ahead rules sit in a separate "Utilities" group (or are NO_DATA
in Industrials, so they are outside R_g and must not change n = 12).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pyarrow.parquet as pq
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

REL = 1e-9
M = 1e6
AS_OF = date(2026, 11, 30)
EARLY = date(2026, 11, 1)  # before the 2026-09-30 quarter / balance sheet (filed 2026-11-05)
T = date(2026, 9, 30)
PREV = date(2025, 9, 30)  # t - 365 days, inside [t - 380, t - 350]
# accruals.md §Report: after the g_* (and d_*, e_*) columns, before flags, in this order
F_COLUMNS = ["f_signal", "f_no_signal_reason", "f_accrual_ratio", "f_acc_pct", "f_cash_conv",
             "f_n"]
F_LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

BAR_DATES = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)] + \
            [date(2026, 11, 23) + timedelta(days=i) for i in range(5)]
QUARTERS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), T]
FILED = {q: q + timedelta(days=36) for q in QUARTERS}  # 2026-09-30 -> filed 2026-11-05
CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"

# G2 X1: name -> (NI TTM, CFO TTM, accrual_ratio, cash_conv, acc_pct, signal, model flags)
X1 = {
    "A01": (100, 220, -0.12, 2.2, 0.0, "BUY", set()),
    "A02": (-20, 60, -0.08, None, 0.09090909090909091, "HOLD", {"F_LOSS_BUY_BLOCKED"}),
    "A03": (100, 130, -0.03, 1.3, 0.18181818181818182, "HOLD", set()),
    "A04": (100, 120, -0.02, 1.2, 0.2727272727272727, "HOLD", set()),
    "A05": (100, 110, -0.01, 1.1, 0.36363636363636365, "HOLD", set()),
    "A06": (100, 100, 0.0, 1.0, 0.45454545454545453, "HOLD", set()),
    "A07": (100, 90, 0.01, 0.9, 0.5454545454545454, "HOLD", set()),
    "A08": (100, 80, 0.02, 0.8, 0.6363636363636364, "HOLD", set()),
    "A09": (100, 70, 0.03, 0.7, 0.7272727272727273, "HOLD", set()),
    "A10": (100, 60, 0.04, 0.6, 0.8181818181818182, "HOLD", set()),
    "A11": (100, 40, 0.06, 0.4, 0.9090909090909091, "SELL", set()),
    "A12": (50, -50, 0.1, -1.0, 1.0, "SELL", set()),
}
F_MODEL_FLAGS = {"F_PERIOD_MISMATCH", "F_NO_PRIOR_TA", "F_LOSS_BUY_BLOCKED",
                 "F_PRIOR_CCY_MISMATCH"}


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def add_security(con, ticker, currency, sector):
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [ticker, "TSX" if currency == "CAD" else "NYSE", ticker, currency, currency, sector,
         None, 1, 1.0, sector])


def add_bars(con, ticker, close, volume=1e6, dates=BAR_DATES):
    for d in dates:
        con.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?)",
                    [ticker, d, close, close, volume, "test"])


def add_fact(con, ticker, period_end, filed, item, value, unit, source="sec_edgar",
             estimated=False):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [ticker, period_end, filed, item, value, unit, source, estimated])


def add_bs(con, ticker, period_end, total_assets, *, filed=None, ccy="USD",
           source="sec_edgar"):
    """One balance-sheet filing: shares 1e8, equity 500 and total_assets ($M; None = absent)."""
    filed = filed or period_end + timedelta(days=36)
    add_fact(con, ticker, period_end, filed, "shares_outstanding", 1e8, "shares", source)
    add_fact(con, ticker, period_end, filed, "total_equity", 500 * M, ccy, source)
    if total_assets is not None:
        add_fact(con, ticker, period_end, filed, "total_assets", total_assets * M, ccy, source)


def add_flow(con, ticker, item, ttm, ccy="USD", quarters=QUARTERS):
    for q in quarters:
        add_fact(con, ticker, q, FILED[q], item, ttm / 4 * M, ccy)


def add_name(con, ticker, ni, cfo, sector="Industrials", *, prev=((PREV, 900),), cfo_ccy="USD",
             cfo_quarters=QUARTERS):
    """Price 20 x 1e8 shares; balance sheets 2026-06-30 (TA 1000) and 2026-09-30 (TA 1100);
    `prev` = extra (period_end, TA) EDGAR filings (filed period_end + 36 days)."""
    add_security(con, ticker, "USD", sector)
    add_bars(con, ticker, 20.0)
    add_bs(con, ticker, date(2026, 6, 30), 1000)
    add_bs(con, ticker, T, 1100)
    for pe, ta in prev:
        add_bs(con, ticker, pe, ta)
    add_flow(con, ticker, "ni_common_q", ni)
    add_flow(con, ticker, "cfo_q", cfo, cfo_ccy, cfo_quarters)


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "US10Y", 0.035,
                                                            "test"])
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "CA10Y", 0.03,
                                                            "test"])
    c.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])
    for name, (ni, cfo, *_rest) in X1.items():
        add_name(c, name, ni, cfo)
    # NO_DATA probes inside Industrials (A01's flows): outside R_g, so X1 keeps n = 12
    add_name(c, "NOPRV", 100, 220, prev=())                        # no prior balance sheet
    add_name(c, "PLAG", 100, 220, cfo_quarters=QUARTERS[:-1])      # CFO TTM ends 2026-06-30
    add_name(c, "CCCY", 100, 220, cfo_ccy="CAD")                   # CFO in CAD, BS in USD
    add_name(c, "YPRV", 100, 220, prev=())                         # prior TA only on Yahoo
    add_bs(c, "YPRV", PREV, 900, source="yahoo", filed=PREV + timedelta(days=45))
    add_name(c, "PCCY", 100, 220, prev=())                         # prior filing in CAD
    add_bs(c, "PCCY", PREV, 900, ccy="CAD")
    add_name(c, "FIN1", 100, 220, sector="Financials")
    # prior-TA window probes (Utilities: SMALL_SECTOR when the prior TA is found)
    add_name(c, "W350", 100, 220, "Utilities", prev=((T - timedelta(days=350), 900),))
    add_name(c, "W380", 100, 220, "Utilities", prev=((T - timedelta(days=380), 900),))
    add_name(c, "W349", 100, 220, "Utilities", prev=((T - timedelta(days=349), 900),))
    add_name(c, "W381", 100, 220, "Utilities", prev=((T - timedelta(days=381), 900),))
    add_name(c, "WTWO", 100, 220, "Utilities",
             prev=((date(2025, 9, 20), 900), (date(2025, 10, 10), 500)))  # latest pe wins
    add_name(c, "WREST", 100, 220, "Utilities")                     # restated, filed later
    add_bs(c, "WREST", PREV, 700, filed=date(2026, 2, 1))
    add_name(c, "WNOTA", 100, 220, "Utilities",
             prev=((PREV, 900), (date(2025, 10, 10), None)))        # latest has no TA
    add_name(c, "WLATE", 100, 220, "Utilities", prev=())            # prior filed after as_of
    add_bs(c, "WLATE", PREV, 900, filed=date(2026, 12, 1))
    yield c
    c.close()


@pytest.fixture
def csm(tmp_path):
    p = tmp_path / "csm.csv"
    p.write_text(CSM_HEADER)
    return str(p)


def run(con, csm, as_of=AS_OF):
    return run_signals(con, as_of, liquidity=LIQ, csm_path=csm)


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


def assert_x1(df):
    for name, (_ni, _cfo, ratio, cc, pct, sig, fl) in X1.items():
        r = row(df, name)
        assert (r["f_signal"], blank(r["f_no_signal_reason"])) == (sig, True), name
        assert r["f_accrual_ratio"] == approx(ratio), name
        assert blank(r["f_cash_conv"]) if cc is None else r["f_cash_conv"] == approx(cc), name
        assert r["f_acc_pct"] == approx(pct), name
        assert r["f_n"] == 12, name
        assert flagset(r["flags"]) & F_MODEL_FLAGS == fl, name


def assert_no_data(r, flags, cash_conv=None):
    assert (r["f_signal"], r["f_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    assert blank(r["f_accrual_ratio"]) and blank(r["f_acc_pct"]) and blank(r["f_n"])
    if cash_conv is None:
        assert blank(r["f_cash_conv"])
    else:
        assert r["f_cash_conv"] == approx(cash_conv)
    assert flags <= flagset(r["flags"])


# ---------------------------------------------------------------- report columns

def test_f_columns_after_g_before_flags(con, csm):
    df, _ = run(con, csm)
    cols = list(df.columns)
    i = cols.index("f_signal")
    assert cols[i:i + 6] == F_COLUMNS
    assert cols[i + 6] == "flags" and cols[-1] == "flags"
    g_last = max(k for k, c in enumerate(cols) if c.startswith("g_"))
    assert g_last < i  # D and E columns may sit in between (order g -> d -> e -> f -> flags)
    assert not any(c.startswith("f_") for c in cols if c not in F_COLUMNS)
    assert not any(c.startswith("f_") and "vote" in c for c in cols)


# ---------------------------------------------------------------- X1 through the engine

def test_x1_signals_via_engine(con, csm):
    df, _ = run(con, csm)
    for name in X1:
        assert row(df, name)["eligible"] in (True, "True") or bool(row(df, name)["eligible"])
    assert_x1(df)


def test_labels_consistent(con, csm):
    df, _ = run(con, csm)
    for _, r in df.iterrows():
        sig = r["f_signal"]
        assert sig in F_LABELS, r["ticker"]
        assert (sig == "NO_SIGNAL") == (not blank(r["f_no_signal_reason"])), r["ticker"]
        assert not str(sig).startswith("SCREEN_")
        if "F_LOSS_BUY_BLOCKED" in flagset(r["flags"]):
            assert sig == "HOLD"


def test_financials_excluded_metrics_computed(con, csm):
    df, _ = run(con, csm)
    r = row(df, "FIN1")
    assert (r["f_signal"], r["f_no_signal_reason"]) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert r["f_accrual_ratio"] == approx(-0.12)
    assert r["f_cash_conv"] == approx(2.2)
    assert blank(r["f_acc_pct"]) and blank(r["f_n"])


# ---------------------------------------------------------------- data mapping

def test_missing_prior_ta_no_data(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NOPRV")  # no spot fallback
    assert_no_data(r, {"F_NO_PRIOR_TA"}, cash_conv=2.2)  # accruals and cash_conv still computed
    assert "F_PERIOD_MISMATCH" not in flagset(r["flags"])


def test_cfo_period_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "PLAG")  # CFO TTM period_end 2026-06-30 != t 2026-09-30
    assert_no_data(r, {"F_PERIOD_MISMATCH"})
    assert "F_NO_PRIOR_TA" not in flagset(r["flags"])


def test_cfo_flow_ccy_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "CCCY")  # cfo_ttm None -> NO_DATA; no period flag (cfo_ttm is None)
    assert_no_data(r, {"FLOW_CCY_MISMATCH"})
    assert "F_PERIOD_MISMATCH" not in flagset(r["flags"])


def test_prior_ta_same_source_only(con, csm):
    df, _ = run(con, csm)
    r = row(df, "YPRV")  # bs_source EDGAR; the only prior TA is Yahoo -> never mixed
    assert_no_data(r, {"F_NO_PRIOR_TA"}, cash_conv=2.2)


def test_prior_ta_currency_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "PCCY")
    # SPEC-AMBIGUITY: F_PRIOR_CCY_MISMATCH makes total_assets_prev None; F_NO_PRIOR_TA's rule
    # (TA_t present, TA_prev None, "whatever the signal") then also fires. Both are expected.
    assert_no_data(r, {"F_PRIOR_CCY_MISMATCH", "F_NO_PRIOR_TA"}, cash_conv=2.2)


@pytest.mark.parametrize("ticker,found", [("W350", True), ("W380", True), ("W349", False),
                                          ("W381", False)])
def test_prior_window_inclusive_350_380(con, csm, ticker, found):
    df, _ = run(con, csm)
    r = row(df, ticker)
    if found:
        assert (r["f_signal"], r["f_no_signal_reason"]) == ("NO_SIGNAL", "SMALL_SECTOR")
        assert r["f_accrual_ratio"] == approx(-0.12)
        assert "F_NO_PRIOR_TA" not in flagset(r["flags"])
    else:
        assert_no_data(r, {"F_NO_PRIOR_TA"}, cash_conv=2.2)


def test_prior_window_latest_period_end(con, csm):
    df, _ = run(con, csm)
    r = row(df, "WTWO")  # 2025-09-20 (TA 900) and 2025-10-10 (TA 500): latest -> avg 800
    assert r["f_accrual_ratio"] == approx(-120 / 800)


def test_prior_window_equal_period_end_later_filed(con, csm):
    df, _ = run(con, csm)
    r = row(df, "WREST")  # 2025-09-30 filed 2025-11-05 (900) and 2026-02-01 (700) -> 700
    assert r["f_accrual_ratio"] == approx(-120 / 900)


def test_prior_window_requires_total_assets(con, csm):
    df, _ = run(con, csm)
    r = row(df, "WNOTA")  # 2025-10-10 filing has no TA -> 2025-09-30 (900) is used
    assert r["f_accrual_ratio"] == approx(-0.12)
    assert "F_NO_PRIOR_TA" not in flagset(r["flags"])


def test_prior_filed_after_as_of_not_used(con, csm):
    df, _ = run(con, csm)
    assert_no_data(row(df, "WLATE"), {"F_NO_PRIOR_TA"}, cash_conv=2.2)
    later, _ = run(con, csm, as_of=date(2026, 12, 2))  # now public
    assert row(later, "WLATE")["f_accrual_ratio"] == approx(-0.12)


# ---------------------------------------------------------------- look-ahead

def test_later_filings_after_as_of_not_used(con, csm):
    before, _ = run(con, csm)
    late = date(2027, 2, 5)
    # a later quarter + balance sheet and a restated 2026-09-30 filing, all filed after as_of
    add_bs(con, "A01", date(2026, 12, 31), 99999, filed=late)
    add_bs(con, "A01", T, 1, filed=date(2026, 12, 10))
    add_fact(con, "A01", date(2026, 12, 31), late, "ni_common_q", 9999 * M, "USD")
    add_fact(con, "A01", date(2026, 12, 31), late, "cfo_q", -9999 * M, "USD")
    add_fact(con, "A01", T, date(2026, 12, 10), "cfo_q", -9999 * M, "USD")
    after, _ = run(con, csm)
    for c in F_COLUMNS + ["flags"]:
        a, b = row(before, "A01")[c], row(after, "A01")[c]
        assert (blank(a) and blank(b)) or a == (approx(b) if isinstance(b, float) else b), c
    assert_x1(after)


def test_earlier_as_of_window_moves_with_t(con, csm):
    df, _ = run(con, csm, as_of=EARLY)
    # t = 2026-06-30 (the 2026-09-30 filing and quarter are not public); the 2025-09-30 filing
    # is 273 days before t, outside [t - 380, t - 350] -> no prior TA. Flows end 2026-06-30 too
    # (equal quarters), so period_ok holds and cash_conv is still 2.2.
    for name in ("A01", "A03", "A12"):
        r = row(df, name)
        assert_no_data(r, {"F_NO_PRIOR_TA"}, cash_conv=X1[name][3])
        assert "F_PERIOD_MISMATCH" not in flagset(r["flags"])


def test_earlier_as_of_with_prior_year_filing(con, csm):
    # give the X1 names a 2025-06-30 balance sheet (TA 600): at EARLY, avg = (1000 + 600) / 2
    for name in X1:
        add_bs(con, name, date(2025, 6, 30), 600)
    df, _ = run(con, csm, as_of=EARLY)
    for name, (ni, cfo, _ratio, _cc, pct, _sig, _fl) in X1.items():
        r = row(df, name)
        assert r["f_accrual_ratio"] == approx((ni - cfo) / 800), name
        assert r["f_acc_pct"] == approx(pct), name  # same order of ratios
        assert r["f_n"] == 12
    assert row(df, "A01")["f_signal"] == "BUY"  # -120/800 = -0.15
    assert row(df, "A12")["f_signal"] == "SELL"  # 100/800 = 0.125
    assert row(df, "A11")["f_signal"] == "SELL"  # 60/800 = 0.075


# ---------------------------------------------------------------- written report

def test_disclaimer_and_written_columns(con, csm, tmp_path):
    df, _ = run(con, csm)
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    first = csv_path.read_text().splitlines()[0]
    assert "accruals/0.1.0" in first
    assert "Model F is new and unvalidated (not backtested)." in first
    back = pd.read_csv(csv_path, skiprows=1)
    cols = list(back.columns)
    assert [c for c in cols if c.startswith("f_")] == F_COLUMNS
    i = cols.index("f_signal")
    assert cols[i:i + 7] == F_COLUMNS + ["flags"]
    assert not any("vote" in c.lower() for c in cols)
    assert "SCREEN_" not in csv_path.read_text()
    table = pq.read_table(pq_path)
    pcols = table.column_names
    j = pcols.index("f_signal")
    assert pcols[j:j + 7] == F_COLUMNS + ["flags"]
    meta = table.schema.metadata or {}
    text = meta.get(b"disclaimer", b"").decode()
    assert "accruals/0.1.0" in text
    assert "Model F is new and unvalidated (not backtested)." in text
    b = back[back["ticker"] == "A11"].iloc[0]
    assert b["f_signal"] == "SELL" and int(b["f_n"]) == 12
