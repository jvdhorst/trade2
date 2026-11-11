"""Spec tests for Model J in the engine and report (docs/models/ebit_yield_roc.md v0.1.0,
§Data mapping and look-ahead, §Report and backtest).

Written from the spec only, with the engine API used by test_signals_report_spec.py:
    run_signals(con, as_of, tickers=None, liquidity=None, csm_path=...) -> (DataFrame, info)
    write_signals(df, as_of, out_root=...) -> (csv_path, parquet_path)
The fixture universe has fewer than 12 names, so ranked names are NO_SIGNAL / SMALL_UNIVERSE.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

REL = 1e-9
AS_OF = date(2026, 11, 30)
EARLY = date(2026, 11, 1)  # before the 2026-09-30 quarter / balance sheet were filed
J_COLUMNS = ["j_signal", "j_no_signal_reason", "j_ey", "j_roc", "j_ev", "j_rank_ey",
             "j_rank_roc", "j_pct"]

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

BAR_DATES = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)] + \
            [date(2026, 11, 23) + timedelta(days=i) for i in range(5)]
QUARTERS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
            date(2026, 9, 30)]
FILED = {q: q + timedelta(days=36) for q in QUARTERS}  # 2026-09-30 -> filed 2026-11-05
CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"


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


def add_fact(con, ticker, period_end, filed, item, value, unit):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [ticker, period_end, filed, item, value, unit, "sec_edgar", False])


def add_bs(con, ticker, period_end, *, equity=0.5e9, shares=1e8, debt=0.7e9, cash=0.2e9,
           ccy="USD", filed=None):
    filed = filed or period_end + timedelta(days=36)
    add_fact(con, ticker, period_end, filed, "total_equity", equity, ccy)
    add_fact(con, ticker, period_end, filed, "shares_outstanding", shares, "shares")
    if debt is not None:
        add_fact(con, ticker, period_end, filed, "total_debt", debt, ccy)
    if cash is not None:
        add_fact(con, ticker, period_end, filed, "cash", cash, ccy)


def add_company(con, ticker, sector="Industrials", *, ebit=(35e6, 75e6, 75e6, 75e6, 75e6),
                ebit_ccy="USD", debt=0.7e9, quarters=QUARTERS):
    """Price 20 x 1e8 shares = cap 2e9; debt 0.7e9, cash 0.2e9, equity 0.5e9 (G1's P x 1e6).
    At AS_OF: EBIT TTM 300e6 -> EV 2.5e9, EY 0.12, IC 1e9, ROC 0.3.
    At EARLY: EBIT TTM 260e6 (2025-09-30..2026-06-30) -> EY 0.104, ROC 0.26."""
    add_security(con, ticker, "USD", sector)
    add_bars(con, ticker, 20.0)
    for pe in (date(2026, 6, 30), date(2026, 9, 30)):
        add_bs(con, ticker, pe, debt=debt)
    for q, v in zip(quarters, ebit):
        add_fact(con, ticker, q, FILED[q], "ebit_q", v, ebit_ccy)
        add_fact(con, ticker, q, FILED[q], "ni_common_q", 50e6, "USD")


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "US10Y", 0.035,
                                                            "test"])
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "CA10Y", 0.03,
                                                            "test"])
    c.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])
    add_company(c, "IND")
    add_company(c, "FIN", sector="Financials")
    add_company(c, "NRG", sector="Energy")
    add_company(c, "NODEBT", debt=None)
    add_company(c, "CCY", ebit_ccy="CAD")
    add_company(c, "LAG", quarters=QUARTERS[:-1], ebit=(75e6,) * 4)  # EBIT through 2026-06-30
    add_security(c, "NOBS", "USD", "Industrials")  # prices, no balance sheet
    add_bars(c, "NOBS", 30.0)
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


def test_j_columns_after_h_before_flags(con, csm):
    df, info = run(con, csm)
    cols = list(df.columns)
    i = cols.index("j_signal")
    assert cols[i:i + 8] == J_COLUMNS
    # altman_z.md 0.1.0 §Report: the g_* columns now sit between j_* and flags
    assert cols[i + 8] == "g_signal" and cols[-1] == "flags"
    assert max(k for k, c in enumerate(cols) if c.startswith("h_")) == i - 1
    assert "J" in info["counts"]
    assert not any(c.startswith("j_") and "vote" in c for c in cols)


def test_ranked_name_values_small_universe(con, csm):
    df, _ = run(con, csm)
    r = row(df, "IND")
    assert r["j_ev"] == approx(2.5e9)
    assert r["j_ey"] == approx(0.12)
    assert r["j_roc"] == approx(0.3)
    assert (r["j_signal"], r["j_no_signal_reason"]) == ("NO_SIGNAL", "SMALL_UNIVERSE")
    assert not blank(r["j_rank_ey"]) and not blank(r["j_pct"])  # informational ranks
    assert 0 < float(r["j_pct"]) <= 1


def test_ebit_quarter_filed_after_as_of_not_used(con, csm):
    before, _ = run(con, csm)
    # next quarter and a restatement of the current one, both filed after as_of
    add_fact(con, "IND", date(2026, 12, 31), date(2027, 2, 10), "ebit_q", 900e6, "USD")
    add_fact(con, "IND", date(2026, 9, 30), date(2026, 12, 15), "ebit_q", 900e6, "USD")
    after, _ = run(con, csm)
    for c in J_COLUMNS + ["flags"]:
        a, b = row(before, "IND")[c], row(after, "IND")[c]
        assert (blank(a) and blank(b)) or a == (approx(b) if isinstance(b, float) else b), c
    assert row(after, "IND")["j_ey"] == approx(0.12)


def test_earlier_as_of_uses_earlier_ebit_quarters(con, csm):
    df, _ = run(con, csm, as_of=EARLY)  # 2026-09-30 quarter (filed 2026-11-05) not yet public
    r = row(df, "IND")
    assert r["j_ev"] == approx(2.5e9)
    assert r["j_ey"] == approx(0.104)
    assert r["j_roc"] == approx(0.26)
    assert "EBIT_PERIOD_MISMATCH" not in flagset(r["flags"])


def test_financials_excluded(con, csm):
    df, _ = run(con, csm)
    r = row(df, "FIN")
    assert (r["j_signal"], r["j_no_signal_reason"]) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert r["j_ey"] == approx(0.12)  # metrics still computed
    assert blank(r["j_rank_ey"]) and blank(r["j_pct"])


def test_cyclical_flag_in_report(con, csm):
    df, _ = run(con, csm)
    assert "CYCLICAL_EBIT" in flagset(row(df, "NRG")["flags"])
    assert "CYCLICAL_EBIT" not in flagset(row(df, "IND")["flags"])


def test_missing_debt_is_no_data_not_zero(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NODEBT")
    assert (r["j_signal"], r["j_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    assert blank(r["j_ev"]) and blank(r["j_ey"]) and blank(r["j_roc"])


def test_ebit_currency_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "CCY")
    assert (r["j_signal"], r["j_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    assert "FLOW_CCY_MISMATCH" in flagset(r["flags"])
    assert blank(r["j_ey"])


def test_ebit_period_mismatch_flag_not_gate(con, csm):
    df, _ = run(con, csm)
    r = row(df, "LAG")  # EBIT TTM ends 2026-06-30, balance sheet 2026-09-30
    assert "EBIT_PERIOD_MISMATCH" in flagset(r["flags"])
    assert r["j_ey"] == approx(0.12)
    assert r["j_no_signal_reason"] == "SMALL_UNIVERSE"


def test_name_without_balance_sheet_like_h(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NOBS")
    if not blank(r["eligible"]) and bool(r["eligible"]):
        assert (r["j_signal"], r["j_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    else:
        assert r["j_signal"] == "NOT_ELIGIBLE" and blank(r["j_no_signal_reason"])
    assert (r["j_signal"], blank(r["j_no_signal_reason"])) == \
        (r["h_signal"], blank(r["h_no_signal_reason"]))
    assert blank(r["j_ev"]) and blank(r["j_ey"])


def test_disclaimer_mentions_model_j(con, csm, tmp_path):
    df, _ = run(con, csm)
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    first = csv_path.read_text().splitlines()[0]
    assert "ebit_yield_roc/0.1.0" in first
    assert "Model J is new and unvalidated (not backtested)." in first
    back = pd.read_csv(csv_path, skiprows=1)
    assert [c for c in back.columns if c.startswith("j_")] == J_COLUMNS
