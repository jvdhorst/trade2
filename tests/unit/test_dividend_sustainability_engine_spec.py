"""Spec tests for Model D in the engine and report (docs/models/dividend_sustainability.md
v0.1.0, §Data mapping and look-ahead, §Report and backtest).

Written from the spec only, with the engine API used by the other engine spec tests:
    run_signals(con, as_of, tickers=None, liquidity=None, csm_path=...) -> (DataFrame, info)
    write_signals(df, as_of, out_root=...) -> (csv_path, parquet_path)
The per-ticker details audit record (spec: d_dps_ttm, d_dps_ttm_prev, d_n_ex_dates, d_eps,
d_first_bar, "as H's h_shares_t") is read back from the `signals` table payload of the run.

Fixture: every company has 1e8 shares, common equity 2500 ($M) at 2025-09-30, 2026-06-30 and
2026-09-30 (so Model A's roe_raw = NI TTM / 2500 exactly: CE_end at q0 2026-09-30 and CE_start
at 2025-09-30, 365 days earlier), NI quarters filed 36 days after quarter end, US10Y 0.035.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

REL = 1e-9
M = 1e6
AS_OF = date(2026, 11, 9)
AS_OF_EX = date(2026, 11, 20)  # an ex-date of GE1
FIRST_BAR = date(2024, 11, 1)
SHARES = 1e8
CE = 2500.0  # $M

G_COLUMNS = ["g_signal", "g_no_signal_reason", "g_z", "g_zone", "g_x1", "g_x2", "g_x3", "g_x4",
             "g_cfo_to_debt", "g_distress_veto"]
# dividend_sustainability.md §Report: after the g_* columns, before e_*, f_* and flags
D_COLUMNS = ["d_signal", "d_no_signal_reason", "d_payout", "d_g_sust", "d_g_hist", "d_dy", "d_k",
             "d_spread", "d_v_ddm", "d_mos"]

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

RECENT_BARS = ([date(2026, 10, 26) + timedelta(days=i) for i in range(5)]
               + [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]
               + [date(2026, 11, 9)]
               + [date(2026, 11, 16) + timedelta(days=i) for i in range(5)])
QUARTERS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
            date(2026, 9, 30)]
BS_DATES = (date(2025, 9, 30), date(2026, 6, 30), date(2026, 9, 30))
CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"

# G-E1 dividends (spec): (ex_date, amount)
GE1_DIVS = [(date(2025, 1, 27), 1.54), (date(2025, 4, 24), 1.54), (date(2025, 7, 28), 1.54),
            (date(2025, 10, 27), 1.54), (date(2026, 1, 26), 1.64), (date(2026, 4, 23), 1.64),
            (date(2026, 7, 27), 1.76), (date(2026, 10, 27), 1.76), (date(2026, 11, 20), 1.80)]
# G-E3 as-traded dividends and a 2-for-1 split on 2026-05-01
GE3_DIVS = [(date(2024, 12, 2), 1.0), (date(2025, 3, 3), 1.0), (date(2025, 6, 2), 1.0),
            (date(2025, 9, 2), 1.0), (date(2025, 12, 1), 1.0), (date(2026, 3, 2), 1.0),
            (date(2026, 6, 1), 0.5), (date(2026, 9, 1), 0.5)]
GE3_SPLIT = (date(2026, 5, 1), 2.0)

# Quarterly payers for the end-to-end goldens: prior window (2024-11-09, 2025-11-09], current
# window (2025-11-09, 2026-11-09] at AS_OF.
PRIOR_EX = [date(2024, 12, 2), date(2025, 3, 3), date(2025, 6, 2), date(2025, 9, 2)]
CURR_EX = [date(2025, 12, 1), date(2026, 3, 2), date(2026, 6, 1), date(2026, 9, 1)]


def quarterly(prev_q, curr_q):
    return [(d, prev_q) for d in PRIOR_EX] + [(d, curr_q) for d in CURR_EX]


G1_DIVS = quarterly(0.475, 0.5)  # dps_ttm 2.0, dps_ttm_prev 1.9 (spec G1)
G4_DIVS = quarterly(0.525, 0.5)  # dps_ttm_prev 2.1 (spec G4: DIV_CUT)

# Spec goldens (dividend_sustainability.md §Worked examples), relative tolerance 1e-9
G1 = dict(d_payout=0.5, d_g_sust=0.06, d_g_hist=0.05263157894736836, d_dy=0.044444444444444446,
          d_k=0.10711111111111112, d_spread=0.02211111111111111, d_v_ddm=84.79999999999998,
          d_mos=0.8844444444444441)
G2 = dict(d_payout=0.5, d_g_sust=0.06, d_dy=0.04, d_k=0.10239999999999999,
          d_spread=0.017399999999999985, d_v_ddm=84.79999999999998, d_mos=0.6959999999999997)
G3 = dict(d_payout=1.1111111111111112, d_g_sust=0.0, d_k=0.044444444444444446,
          d_spread=-0.04055555555555556, d_v_ddm=23.52941176470588, d_mos=-0.47712418300653603)


def approx(x):
    return pytest.approx(x, rel=REL, abs=1e-12)


def add_security(con, ticker, sector, currency="USD"):
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [ticker, "TSX" if currency == "CAD" else "NYSE", ticker, currency, currency, sector,
         None, 1, 1.0, sector])


def add_bars(con, ticker, close, first_bar=FIRST_BAR):
    for d in [first_bar] + RECENT_BARS:
        con.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?)",
                    [ticker, d, close, close, 1e6, "test"])


def add_fact(con, ticker, period_end, filed, item, value, unit):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [ticker, period_end, filed, item, value, unit, "sec_edgar", False])


def add_divs(con, ticker, divs):
    for d, amt in divs:
        con.execute("INSERT INTO dividends VALUES (?,?,?,?,?)",
                    [ticker, d, amt, "USD", "yahoo_chart"])


def add_company(con, ticker, *, price, ni_ttm, divs, sector="Consumer Staples",
                first_bar=FIRST_BAR, split=None):
    """1e8 shares, CE 2500 ($M) at every balance sheet, NI TTM `ni_ttm` ($M) in 4 equal
    quarters (roe_raw = ni_ttm / 2500), price bars from `first_bar`."""
    add_security(con, ticker, sector)
    add_bars(con, ticker, price, first_bar)
    for pe in BS_DATES:
        filed = pe + timedelta(days=36)
        add_fact(con, ticker, pe, filed, "shares_outstanding", SHARES, "shares")
        add_fact(con, ticker, pe, filed, "total_equity", CE * M, "USD")
    for q in QUARTERS:
        add_fact(con, ticker, q, q + timedelta(days=36), "ni_common_q", ni_ttm / 4 * M, "USD")
    add_divs(con, ticker, divs)
    if split is not None:
        con.execute("INSERT INTO splits VALUES (?,?,?,?)", [ticker, split[0], split[1], "test"])


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "US10Y", 0.035,
                                                            "test"])
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "CA10Y", 0.03,
                                                            "test"])
    c.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])
    # G-E1: NI 1000 -> eps 10, roe_raw 0.4 (g_raw > 0.06 -> g_sust 0.06), price 100
    add_company(c, "GE1", price=100.0, ni_ttm=1000.0, divs=GE1_DIVS)
    # G-E2: G-E1 with first price bar 2024-12-02 (prior window not covered)
    add_company(c, "GE2", price=100.0, ni_ttm=1000.0, divs=GE1_DIVS, first_bar=date(2024, 12, 2))
    # coverage guard on the current window: first bar after as_of - 358 days
    add_company(c, "GENO", price=100.0, ni_ttm=1000.0, divs=GE1_DIVS,
                first_bar=date(2026, 1, 5))
    # G-E3: split restatement; NI 400, price 45 -> G1 metrics when prev is restated
    add_company(c, "GE3", price=45.0, ni_ttm=400.0, divs=GE3_DIVS, split=GE3_SPLIT)
    # end-to-end goldens
    add_company(c, "DBUY", price=45.0, ni_ttm=400.0, divs=G1_DIVS)                  # G1
    add_company(c, "DHOLD", price=50.0, ni_ttm=400.0, divs=G1_DIVS)                 # G2
    add_company(c, "DSELL", price=45.0, ni_ttm=180.0, divs=G1_DIVS)                 # G3
    add_company(c, "DCUT", price=45.0, ni_ttm=400.0, divs=G4_DIVS)                  # G4
    add_company(c, "DREIT", price=45.0, ni_ttm=400.0, divs=G1_DIVS, sector="Real Estate")
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


def payload(con, info, ticker):
    got = con.execute("SELECT payload_json FROM signals WHERE run_id=? AND ticker=?",
                      [info["run_id"], ticker]).fetchone()
    assert got is not None, ticker
    return json.loads(got[0])


def check(r, **expected):
    for c, want in expected.items():
        if want is None:
            assert blank(r[c]), (c, r[c])
        else:
            assert not blank(r[c]), c
            assert r[c] == approx(want), (c, r[c], want)


# ----------------------------------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------------------------------

def test_d_columns_after_g_contiguous_before_e_f_flags(con, csm):
    df, _ = run(con, csm)
    cols = list(df.columns)
    g = cols.index("g_signal")
    assert cols[g:g + 10] == G_COLUMNS
    d = cols.index("d_signal")
    assert d == g + 10  # right after the g_* block
    assert cols[d:d + 10] == D_COLUMNS
    assert cols[-1] == "flags"
    nxt = cols[d + 10]
    assert nxt in ("e_signal", "f_signal", "flags")
    # order g -> d -> e -> f -> flags
    e = [i for i, c in enumerate(cols) if c.startswith("e_")]
    f = [i for i, c in enumerate(cols) if c.startswith("f_")]
    if e:
        assert min(e) == d + 10
    if f:
        assert min(f) > (max(e) if e else d + 9)
    assert [c for c in cols if c.startswith("d_")] == D_COLUMNS
    # r, eps and roe0 of Model D are not report columns; D has no vote
    assert not any(c in cols for c in ("d_r", "d_eps", "d_roe0", "d_dps_ttm", "d_first_bar"))
    assert not any(c.startswith("d_") and "vote" in c for c in cols)


# ----------------------------------------------------------------------------------------------
# Engine goldens G-E1 .. G-E3
# ----------------------------------------------------------------------------------------------

def test_ge1_ex_date_after_as_of_excluded(con, csm):
    df, info = run(con, csm)
    r = row(df, "GE1")
    p = payload(con, info, "GE1")
    assert p["d_dps_ttm"] == approx(6.8)  # 1.64 + 1.64 + 1.76 + 1.76; 2026-11-20 excluded
    assert p["d_n_ex_dates"] == 4
    assert p["d_dps_ttm_prev"] == approx(6.16)
    assert p["d_eps"] == approx(10.0)
    assert str(p["d_first_bar"])[:10] == "2024-11-01"
    # SPEC-AMBIGUITY: G-E1 states g_hist 0.10389610389610393 for sums taken in ex-date order;
    # the engine may sum in another order, so rel 1e-9 (not bit equality) is asserted.
    check(r, d_g_hist=0.10389610389610393, d_dy=6.8 / 100.0, d_payout=6.8 / 10.0, d_g_sust=0.06)
    # independent: dy 0.068, k = 0.068 * 1.06 + 0.06, spread = k - 0.085 >= 0.02, payout 0.68
    check(r, d_k=0.068 * 1.06 + 0.06, d_spread=0.068 * 1.06 + 0.06 - 0.085)
    assert (r["d_signal"], blank(r["d_no_signal_reason"])) == ("BUY", True)
    assert "G_CAPPED" in flagset(r["flags"])
    assert not flagset(r["flags"]) & {"NO_DIV_HISTORY", "NEW_PAYER", "DIV_CUT", "IRREGULAR_DIV"}


def test_ge1_as_of_on_ex_date_counted(con, csm):
    df, info = run(con, csm, as_of=AS_OF_EX)
    r = row(df, "GE1")
    p = payload(con, info, "GE1")
    assert p["d_dps_ttm"] == approx(8.6)
    assert p["d_n_ex_dates"] == 5
    assert p["d_dps_ttm_prev"] == approx(6.16)
    check(r, d_g_hist=0.39610389610389607, d_dy=0.086, d_payout=0.86)
    # payout 0.86 > 0.75 with spread >= 0.02 -> HOLD, BUY_BLOCKED_PAYOUT
    assert r["d_spread"] >= 0.02
    assert r["d_signal"] == "HOLD"
    assert "BUY_BLOCKED_PAYOUT" in flagset(r["flags"])


def test_ge2_prior_window_not_covered(con, csm):
    df, info = run(con, csm)
    r = row(df, "GE2")
    p = payload(con, info, "GE2")
    assert p["d_dps_ttm_prev"] is None  # 2026-11-09 - 723 = 2024-11-16 < 2024-12-02
    assert p["d_dps_ttm"] == approx(6.8)  # 2026-11-09 - 358 = 2025-11-16 >= first bar
    assert p["d_n_ex_dates"] == 4
    assert str(p["d_first_bar"])[:10] == "2024-12-02"
    assert blank(r["d_g_hist"])
    assert "NO_DIV_HISTORY" in flagset(r["flags"])
    assert "NEW_PAYER" not in flagset(r["flags"])
    assert r["d_signal"] == "BUY"  # NO_DIV_HISTORY never blocks BUY
    check(r, d_dy=0.068, d_payout=0.68)


def test_coverage_guard_current_window(con, csm):
    df, info = run(con, csm)
    r = row(df, "GENO")  # first bar 2026-01-05 > 2026-11-09 - 358 = 2025-11-16
    p = payload(con, info, "GENO")
    assert p["d_dps_ttm"] is None
    assert p["d_n_ex_dates"] == 0
    assert p["d_dps_ttm_prev"] is None
    assert (r["d_signal"], r["d_no_signal_reason"]) == ("NO_SIGNAL", "NO_DATA")
    check(r, d_payout=None, d_dy=None, d_k=None, d_spread=None, d_g_hist=None)
    assert "IRREGULAR_DIV" not in flagset(r["flags"])


def test_ge3_split_restates_prior_dps(con, csm):
    df, info = run(con, csm)
    r = row(df, "GE3")
    p = payload(con, info, "GE3")
    assert p["d_dps_ttm"] == approx(2.0)  # two pre-split 1.00 restated to 0.50
    assert p["d_n_ex_dates"] == 4
    assert p["d_dps_ttm_prev"] == approx(2.0)  # dps_ttm(2025-11-09) 4.0 / split_factor 2.0
    check(r, d_g_hist=0.0)  # not -0.5 (a false cut)
    assert "DIV_CUT" not in flagset(r["flags"])
    assert "BUY_BLOCKED_DIV_CUT" not in flagset(r["flags"])
    # with prev restated the name is G1 (price 45, dps 2.0, eps 4, roe_raw 0.16)
    assert r["d_signal"] == "BUY"
    check(r, **{k: v for k, v in G1.items() if k != "d_g_hist"})


# ----------------------------------------------------------------------------------------------
# End-to-end goldens through the engine
# ----------------------------------------------------------------------------------------------

def test_fixture_roe_raw_from_model_a(con, csm):
    # D takes Model A's roe_raw: NI TTM / avg(CE_end, CE_start) = NI / 2500
    _, info = run(con, csm)
    assert payload(con, info, "DBUY")["roe_raw"] == approx(400.0 / 2500.0)
    assert payload(con, info, "DSELL")["roe_raw"] == approx(180.0 / 2500.0)


def test_g1_buy_end_to_end(con, csm):
    df, info = run(con, csm)
    r = row(df, "DBUY")
    assert (r["d_signal"], blank(r["d_no_signal_reason"])) == ("BUY", True)
    check(r, **G1)
    assert "G_CAPPED" in flagset(r["flags"])  # D flags join flags
    p = payload(con, info, "DBUY")
    assert p["d_dps_ttm"] == approx(2.0)
    assert p["d_dps_ttm_prev"] == approx(1.9)
    assert p["d_n_ex_dates"] == 4
    assert p["d_eps"] == approx(4.0)


def test_g2_hold_end_to_end(con, csm):
    df, _ = run(con, csm)
    r = row(df, "DHOLD")
    assert (r["d_signal"], blank(r["d_no_signal_reason"])) == ("HOLD", True)
    check(r, **G2)
    assert not flagset(r["flags"]) & {"BUY_BLOCKED_PAYOUT", "BUY_BLOCKED_DIV_CUT"}


def test_g3_sell_end_to_end(con, csm):
    df, _ = run(con, csm)
    r = row(df, "DSELL")
    assert (r["d_signal"], blank(r["d_no_signal_reason"])) == ("SELL", True)
    check(r, **G3)
    assert "G_FLOORED" in flagset(r["flags"])
    assert "G_CAPPED" not in flagset(r["flags"])


def test_g4_div_cut_end_to_end(con, csm):
    df, _ = run(con, csm)
    r = row(df, "DCUT")
    assert r["d_signal"] == "HOLD"
    check(r, d_g_hist=-0.04761904761904767, d_spread=0.02211111111111111)
    assert {"G_CAPPED", "DIV_CUT", "BUY_BLOCKED_DIV_CUT"} <= flagset(r["flags"])


def test_real_estate_excluded_metrics_computed(con, csm):
    df, _ = run(con, csm)
    r = row(df, "DREIT")
    assert (r["d_signal"], r["d_no_signal_reason"]) == ("NO_SIGNAL", "EXCLUDED_SECTOR")
    check(r, **G1)


def test_ex_date_after_as_of_never_changes_result(con, csm):
    before, _ = run(con, csm)
    # a large dividend going ex after as_of (declared earlier is irrelevant: ex_date decides)
    add_divs(con, "DBUY", [(date(2026, 11, 12), 5.0)])
    after, info = run(con, csm)
    a, b = row(before, "DBUY"), row(after, "DBUY")
    for c in D_COLUMNS + ["flags"]:
        assert (blank(a[c]) and blank(b[c])) or (
            b[c] == (approx(a[c]) if isinstance(a[c], float) else a[c])), c
    p = payload(con, info, "DBUY")
    assert p["d_n_ex_dates"] == 4
    assert p["d_dps_ttm"] == approx(2.0)


def test_labels_and_reason_consistency(con, csm):
    df, _ = run(con, csm)
    for _, r in df.iterrows():
        sig = r["d_signal"]
        assert sig in {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}, r["ticker"]
        assert (sig == "NO_SIGNAL") == (not blank(r["d_no_signal_reason"])), r["ticker"]


# ----------------------------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------------------------

def test_write_signals_columns_and_disclaimer(con, csm, tmp_path):
    import pyarrow.parquet as pq

    df, _ = run(con, csm)
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    first = csv_path.read_text().splitlines()[0]
    assert "dividend_sustainability/0.1.0" in first
    assert "Model D is new and unvalidated (not backtested)." in first
    assert "No combined vote (Phase 10)." in first
    back = pd.read_csv(csv_path, skiprows=1)
    assert [c for c in back.columns if c.startswith("d_")] == D_COLUMNS
    cols = list(back.columns)
    assert cols.index("d_signal") == cols.index("g_signal") + 10
    assert cols[-1] == "flags"
    assert not any("vote" in c.lower() for c in back.columns)
    assert "SCREEN_" not in csv_path.read_text()
    buy = back[back["ticker"] == "DBUY"].iloc[0]
    assert buy["d_signal"] == "BUY"
    pq_tab = pq.read_table(pq_path)
    assert [c for c in pq_tab.column_names if c.startswith("d_")] == D_COLUMNS
    disc = pq_tab.schema.metadata[b"disclaimer"].decode()
    assert "dividend_sustainability/0.1.0" in disc
    assert "Model D is new and unvalidated (not backtested)." in disc
    pq_df = pq_tab.to_pandas()
    assert pq_df.loc[pq_df["ticker"] == "DBUY", "d_spread"].iloc[0] == approx(
        0.02211111111111111)
