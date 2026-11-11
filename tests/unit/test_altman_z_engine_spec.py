"""Spec tests for Model G in the engine and report (docs/models/altman_z.md v0.1.0,
§Data mapping and look-ahead, §Report and backtest).

Written from the spec only, with the engine API used by test_ebit_yield_roc_engine_spec.py:
    run_signals(con, as_of, tickers=None, liquidity=None, csm_path=...) -> (DataFrame, info)
    write_signals(df, as_of, out_root=...) -> (csv_path, parquet_path)
The store is EDGAR-like: balance-sheet items per (period_end, filed_date) filing and discrete
quarterly `ebit_q` / `cfo_q` facts. Amounts are the spec goldens in $M x 1e6, so every ratio
equals the golden's (relative tolerance 1e-9).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

REL = 1e-9
M = 1e6
AS_OF = date(2026, 11, 30)
EARLY = date(2026, 11, 1)  # before the 2026-09-30 quarter / balance sheet (filed 2026-11-05)
J_COLUMNS = ["j_signal", "j_no_signal_reason", "j_ey", "j_roc", "j_ev", "j_rank_ey",
             "j_rank_roc", "j_pct"]
# altman_z.md §Report: after the j_* columns, before flags
G_COLUMNS = ["g_signal", "g_no_signal_reason", "g_z", "g_zone", "g_x1", "g_x2", "g_x3", "g_x4",
             "g_cfo_to_debt", "g_distress_veto"]

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

BAR_DATES = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)] + \
            [date(2026, 11, 23) + timedelta(days=i) for i in range(5)]
QUARTERS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
            date(2026, 9, 30)]
FILED = {q: q + timedelta(days=36) for q in QUARTERS}  # 2026-09-30 -> filed 2026-11-05
BS_DATES = (date(2026, 6, 30), date(2026, 9, 30))
CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"

# Spec goldens ($M): CA, CL, TA, RE, equity (EBIT TTM from the quarters)
G1_BS = dict(current_assets=600, current_liabilities=500, total_assets=2000,
             retained_earnings=400, total_equity=800)
G2_BS = dict(current_assets=450, current_liabilities=600, total_assets=2000,
             retained_earnings=-100, total_equity=300)
# Earlier balance sheet of GREY (2026-06-30, filed 2026-08-05)
EARLY_BS = dict(current_assets=500, current_liabilities=500, total_assets=2000,
                retained_earnings=300, total_equity=800)
G1_EBIT = (20, 45, 45, 45, 45)  # TTM 180 at AS_OF (G1); 155 at EARLY
G2_EBIT = (5, 5, 5, 5, 5)  # TTM 20 (G2)

Z_G1 = 2.2847999999999997
Z_G2 = -0.40250588235294105
# GREY at EARLY: X1 0, X2 0.15, X3 155/2000, X4 800/1200 (independent recomputation)
Z_EARLY = 6.56 * 0.0 + 3.26 * (300 / 2000) + 6.72 * (155 / 2000) + 1.05 * (800 / 1200)


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


def add_bs(con, ticker, period_end, items, *, debt=None, filed=None, drop=(), ccy="USD"):
    """One balance-sheet filing: shares 1e8 plus the given items ($M), minus `drop`."""
    filed = filed or period_end + timedelta(days=36)
    add_fact(con, ticker, period_end, filed, "shares_outstanding", 1e8, "shares")
    for item, v in items.items():
        if item not in drop:
            add_fact(con, ticker, period_end, filed, item, v * M, ccy)
    if debt is not None:
        add_fact(con, ticker, period_end, filed, "total_debt", debt * M, ccy)


def add_quarters(con, ticker, item, values, ccy="USD", quarters=QUARTERS):
    for q, v in zip(quarters, values):
        add_fact(con, ticker, q, FILED[q], item, v * M, ccy)


def add_company(con, ticker, sector="Industrials", *, bs=G1_BS, early_bs=None, ebit=G1_EBIT,
                ebit_ccy="USD", ebit_quarters=QUARTERS, cfo=None, cfo_ccy="USD", debt=None,
                drop_latest=()):
    """Price 20 x 1e8 shares; balance sheets at 2026-06-30 (`early_bs`, default `bs`) and
    2026-09-30 (`bs`, minus `drop_latest`)."""
    add_security(con, ticker, "USD", sector)
    add_bars(con, ticker, 20.0)
    add_bs(con, ticker, BS_DATES[0], early_bs or bs, debt=debt)
    add_bs(con, ticker, BS_DATES[1], bs, debt=debt, drop=drop_latest)
    add_quarters(con, ticker, "ebit_q", ebit, ebit_ccy, ebit_quarters)
    add_quarters(con, ticker, "ni_common_q", (50,) * 5)
    if cfo is not None:
        add_quarters(con, ticker, "cfo_q", cfo, cfo_ccy)


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "US10Y", 0.035,
                                                            "test"])
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "CA10Y", 0.03,
                                                            "test"])
    c.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])
    add_company(c, "GREY", early_bs=EARLY_BS)                       # G1 -> HOLD
    add_company(c, "DIST", bs=G2_BS, ebit=G2_EBIT, debt=700)        # G2 -> SELL, no CFO
    add_company(c, "RICH", bs=G2_BS, ebit=G2_EBIT, debt=1000, cfo=(75,) * 5)  # G3 override
    add_company(c, "LOWCF", bs=G2_BS, ebit=G2_EBIT, debt=1000, cfo=(12.5,) * 5)  # G2b
    add_company(c, "NOCA", drop_latest=("current_assets",))          # CA only in 06-30 filing
    add_company(c, "FING", sector="Financials")
    add_company(c, "ECCY", bs=G2_BS, ebit=G2_EBIT, ebit_ccy="CAD")
    add_company(c, "CCCY", bs=G2_BS, ebit=G2_EBIT, debt=1000, cfo=(75,) * 5, cfo_ccy="CAD")
    add_company(c, "GLAG", ebit=(45,) * 4, ebit_quarters=QUARTERS[:-1])  # EBIT to 2026-06-30
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


def truth(x):
    # SPEC-AMBIGUITY: §Report does not fix how g_distress_veto (bool | None) is stored in the
    # DataFrame / CSV; a bool (numpy or Python) or the strings "True"/"False" are accepted.
    assert not blank(x)
    if isinstance(x, str):
        assert x in ("True", "False")
        return x == "True"
    return bool(x)


def flagset(x):
    return set() if blank(x) else {f for f in str(x).split(",") if f}


def row(df, ticker):
    sub = df[df["ticker"] == ticker]
    assert len(sub) == 1, ticker
    return sub.iloc[0].to_dict()


def assert_g1(r, z=Z_G1):
    assert (r["g_signal"], blank(r["g_no_signal_reason"])) == ("HOLD", True)
    assert r["g_zone"] == "grey"
    assert r["g_z"] == approx(z)
    assert truth(r["g_distress_veto"]) is False


def test_g_columns_after_j_before_flags(con, csm):
    df, _ = run(con, csm)
    cols = list(df.columns)
    i = cols.index("g_signal")
    assert cols[i:i + 10] == G_COLUMNS
    assert cols[i + 10] == "flags" and cols[-1] == "flags"
    j = cols.index("j_signal")
    assert cols[j:j + 8] == J_COLUMNS and j + 8 == i
    assert not any(c.startswith("g_") and "vote" in c for c in cols)
    assert not any(c.startswith("g_") for c in cols if c not in G_COLUMNS)


def test_g1_grey_hold(con, csm):
    df, _ = run(con, csm)
    r = row(df, "GREY")
    assert truth(r["eligible"]) is True
    assert_g1(r)
    assert r["g_x1"] == approx(0.05)
    assert r["g_x2"] == approx(0.2)
    assert r["g_x3"] == approx(0.09)
    assert r["g_x4"] == approx(800 / 1200)
    assert blank(r["g_cfo_to_debt"])
    assert not flagset(r["flags"]) & {"NO_OVERRIDE_DATA", "CASH_RICH_OVERRIDE", "NEG_EQUITY"}


def test_g2_distress_sell_veto(con, csm):
    df, _ = run(con, csm)
    r = row(df, "DIST")
    assert (r["g_signal"], blank(r["g_no_signal_reason"])) == ("SELL", True)
    assert r["g_zone"] == "distress"
    assert r["g_z"] == approx(Z_G2)
    assert r["g_x1"] == approx(-0.075)
    assert r["g_x2"] == approx(-0.05)
    assert r["g_x3"] == approx(0.01)
    assert r["g_x4"] == approx(300 / 1700)
    assert truth(r["g_distress_veto"]) is True
    assert blank(r["g_cfo_to_debt"])  # debt 700 but no cfo_q facts
    assert "NO_OVERRIDE_DATA" in flagset(r["flags"])  # G flags join flags
    assert "CASH_RICH_OVERRIDE" not in flagset(r["flags"])


def test_g3_override_via_cfo_q_and_total_debt(con, csm):
    df, _ = run(con, csm)
    r = row(df, "RICH")  # CFO TTM 300, debt 1000 -> 0.3 > 0.25
    assert r["g_signal"] == "HOLD"
    assert r["g_zone"] == "distress"  # zone unchanged by the override
    assert r["g_z"] == approx(Z_G2)
    assert r["g_cfo_to_debt"] == approx(0.3)
    assert truth(r["g_distress_veto"]) is False
    assert "CASH_RICH_OVERRIDE" in flagset(r["flags"])
    assert "NO_OVERRIDE_DATA" not in flagset(r["flags"])


def test_g2b_low_cfo_to_debt_sell(con, csm):
    df, _ = run(con, csm)
    r = row(df, "LOWCF")  # CFO TTM 50, debt 1000 -> 0.05
    assert r["g_signal"] == "SELL"
    assert r["g_cfo_to_debt"] == approx(0.05)
    assert truth(r["g_distress_veto"]) is True
    assert not flagset(r["flags"]) & {"CASH_RICH_OVERRIDE", "NO_OVERRIDE_DATA"}


def test_later_filings_after_as_of_not_used(con, csm):
    before, _ = run(con, csm)
    # a later balance sheet + quarter and a restated 2026-09-30 filing, all filed after as_of
    late = date(2027, 2, 5)
    add_bs(con, "GREY", date(2026, 12, 31), G2_BS, filed=late)
    add_bs(con, "GREY", date(2026, 9, 30), G2_BS, filed=date(2026, 12, 10))
    add_fact(con, "GREY", date(2026, 12, 31), late, "ebit_q", -500 * M, "USD")
    add_fact(con, "GREY", date(2026, 9, 30), date(2026, 12, 10), "ebit_q", -500 * M, "USD")
    after, _ = run(con, csm)
    for c in G_COLUMNS + ["flags"]:
        a, b = row(before, "GREY")[c], row(after, "GREY")[c]
        assert (blank(a) and blank(b)) or a == (approx(b) if isinstance(b, float) else b), c
    assert_g1(row(after, "GREY"))


def test_earlier_as_of_uses_earlier_filing(con, csm):
    df, _ = run(con, csm, as_of=EARLY)  # 2026-09-30 filing and quarter not yet public
    r = row(df, "GREY")
    assert_g1(r, z=Z_EARLY)
    assert r["g_x1"] == approx(0.0)
    assert r["g_x2"] == approx(0.15)
    assert r["g_x3"] == approx(155 / 2000)
    assert r["g_x4"] == approx(800 / 1200)
    assert "EBIT_PERIOD_MISMATCH" not in flagset(r["flags"])


def test_missing_current_assets_not_borrowed(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NOCA")  # 2026-09-30 filing has no current_assets; 2026-06-30 has 600
    assert (r["g_signal"], r["g_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    assert blank(r["g_x1"]) and blank(r["g_z"]) and blank(r["g_zone"])
    assert blank(r["g_distress_veto"])
    assert r["g_x2"] == approx(0.2)  # the selected filing's other items still used
    assert r["g_x3"] == approx(0.09)
    assert r["g_x4"] == approx(800 / 1200)


def test_missing_current_assets_earlier_as_of_uses_complete_filing(con, csm):
    df, _ = run(con, csm, as_of=EARLY)  # the selected filing is now 2026-06-30, which has CA
    r = row(df, "NOCA")
    assert r["g_signal"] == "HOLD"
    assert r["g_x1"] == approx(0.05)


def test_financials_excluded(con, csm):
    df, _ = run(con, csm)
    r = row(df, "FING")
    assert truth(r["eligible"]) is True
    assert (r["g_signal"], r["g_no_signal_reason"]) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    assert r["g_z"] == approx(Z_G1)  # metrics still computed
    assert r["g_zone"] == "grey"
    assert blank(r["g_distress_veto"])


def test_ebit_currency_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "ECCY")
    assert (r["g_signal"], r["g_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    assert "FLOW_CCY_MISMATCH" in flagset(r["flags"])
    assert blank(r["g_x3"]) and blank(r["g_z"])
    assert r["g_x1"] == approx(-0.075)


def test_cfo_currency_mismatch_no_override(con, csm):
    df, _ = run(con, csm)
    r = row(df, "CCCY")  # CFO in CAD, balance sheet USD -> cfo_ttm None
    # Model C also flags FLOW_CCY_MISMATCH for CFO, so the flag alone does not prove G's
    # mapping; the blank g_cfo_to_debt and NO_OVERRIDE_DATA do.
    assert r["g_signal"] == "SELL"
    assert blank(r["g_cfo_to_debt"])
    assert truth(r["g_distress_veto"]) is True
    assert {"FLOW_CCY_MISMATCH", "NO_OVERRIDE_DATA"} <= flagset(r["flags"])
    assert "CASH_RICH_OVERRIDE" not in flagset(r["flags"])


def test_ebit_period_mismatch_flag_not_gate(con, csm):
    df, _ = run(con, csm)
    r = row(df, "GLAG")  # EBIT TTM (180) ends 2026-06-30, balance sheet 2026-09-30
    assert "EBIT_PERIOD_MISMATCH" in flagset(r["flags"])
    assert_g1(r)


def test_labels_and_veto_consistency(con, csm):
    df, _ = run(con, csm)
    for _, r in df.iterrows():
        sig = r["g_signal"]
        assert sig in {"HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}, r["ticker"]
        assert (sig == "NO_SIGNAL") == (not blank(r["g_no_signal_reason"])), r["ticker"]
        if sig in ("HOLD", "SELL"):
            assert truth(r["g_distress_veto"]) is (sig == "SELL"), r["ticker"]
        else:
            assert blank(r["g_distress_veto"]), r["ticker"]


def test_disclaimer_mentions_model_g_no_vote(con, csm, tmp_path):
    df, _ = run(con, csm)
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    first = csv_path.read_text().splitlines()[0]
    assert "altman_z/0.1.0" in first
    assert "Model G is new and unvalidated (not backtested)." in first
    assert "No combined vote (Phase 10)." in first
    assert "ebit_yield_roc/0.1.0" in first
    back = pd.read_csv(csv_path, skiprows=1)
    assert [c for c in back.columns if c.startswith("g_")] == G_COLUMNS
    assert not any("vote" in c.lower() for c in back.columns)
    assert "SCREEN_" not in csv_path.read_text()
