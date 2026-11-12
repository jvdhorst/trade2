"""Spec tests for Model E in the engine and report (docs/models/piotroski.md v0.1.0,
§Data mapping and look-ahead, §Report and backtest).

Written from the spec only, with the engine API used by test_altman_z_engine_spec.py:
    run_signals(con, as_of, tickers=None, liquidity=None, csm_path=...) -> (DataFrame, info)
    write_signals(df, as_of, out_root=...) -> (csv_path, parquet_path)
The store is EDGAR-like: balance-sheet filings per (period_end, filed_date) at t (2026-09-30),
t-4q (2025-09-30) and t-8q (2024-09-30), and nine discrete quarters (2024-09-30 .. 2026-09-30) of
`ni_common_q`, `cfo_q`, `gross_profit_q`, `revenue_q`, each filed 36 days after quarter end.
Amounts are the spec's E1 golden in $M x 1e6 (current TTM = last four quarters, prior TTM = the
four quarters ending 2025-09-30), so the engine must reproduce E1: F 8 of 9, "111111110", BUY.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from valuation.engine import run_signals
from valuation.report.screen import write_signals
from valuation.store.db import connect

M = 1e6
AS_OF = date(2026, 11, 30)
LATE = date(2026, 12, 10)  # after as_of
# piotroski.md §Report: e_* after g_* and d_*, before f_* and flags
E_COLUMNS = ["e_signal", "e_no_signal_reason", "e_f_score", "e_n_scored", "e_signals"]
LABELS = {"BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"}
FB = "PRIOR_TA_FALLBACK"

_SECTION = dict(min_mdtv=1, min_market_cap=0, min_price=0, mdtv_window=1,
                max_zero_volume_days=100, max_stale_sessions=100000, min_history_sessions=1)
LIQ = {"tsx": dict(_SECTION), "us": dict(_SECTION)}

BAR_DATES = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)] + \
            [date(2026, 11, 23) + timedelta(days=i) for i in range(5)]
# Nine quarter ends, gaps 90-92 days (ttm.md §3 needs 80-100)
QUARTERS = [date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30),
            date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
            date(2026, 9, 30)]
T, T4, T8 = date(2026, 9, 30), date(2025, 9, 30), date(2024, 9, 30)  # 365 / 730 days back
CSM_HEADER = "ticker,period_end,filed_date,csm_after_tax,adjusted_bvps_reported,source,note\n"

# Per-quarter flows ($M): index 0 is the extra 2024-09-30 quarter, 1-4 the prior TTM
# (NI 100, GP 400, revenue 1150), 5-8 the current TTM (NI 120, CFO 150, GP 420, revenue 1200).
NI_Q = (25, 25, 25, 25, 25, 30, 30, 30, 30)
CFO_Q = (37.5,) * 9
GP_Q = (100, 100, 100, 100, 100, 105, 105, 105, 105)
REV_Q = (287.5,) * 5 + (300,) * 4

BS_T = dict(total_assets=1000, total_debt=300, current_assets=400, current_liabilities=200,
            total_equity=500)
BS_4Q = dict(total_assets=950, total_debt=310, current_assets=380, current_liabilities=200,
             total_equity=480)
BS_8Q = dict(total_assets=900, total_debt=320, current_assets=360, current_liabilities=200,
             total_equity=450)


def add_security(con, ticker, sector):
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [ticker, "NYSE", ticker, "USD", "USD", sector, None, 1, 1.0, sector])


def add_bars(con, ticker, close=20.0, volume=1e6):
    for d in BAR_DATES:
        con.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?)",
                    [ticker, d, close, close, volume, "test"])


def add_fact(con, ticker, period_end, filed, item, value, unit, source="sec_edgar",
             estimated=False):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [ticker, period_end, filed, item, value, unit, source, estimated])


def add_bs(con, ticker, period_end, items, shares, *, filed=None, ccy="USD",
           source="sec_edgar"):
    """One balance-sheet filing: the share count plus the given items ($M)."""
    filed = filed or period_end + timedelta(days=36)
    est = source == "yahoo"
    if shares is not None:
        add_fact(con, ticker, period_end, filed, "shares_outstanding", shares, "shares", source,
                 est)
    for item, v in items.items():
        add_fact(con, ticker, period_end, filed, item, v * M, ccy, source, est)


def add_flow(con, ticker, item, values, *, ccy="USD", quarters=QUARTERS):
    for q, v in zip(quarters, values):
        add_fact(con, ticker, q, q + timedelta(days=36), item, v * M, ccy)


def add_company(con, ticker, sector="Industrials", *, n_quarters=9, ni_quarters=None,
                bs_4q=True, bs_8q=True, filed_8q=None, ccy_4q="USD", ccy_8q="USD",
                gp_ccy="USD", shares_4q=101e6):
    add_security(con, ticker, sector)
    add_bars(con, ticker)
    add_bs(con, ticker, T, BS_T, 100e6)
    if bs_4q:
        add_bs(con, ticker, T4, BS_4Q, shares_4q, ccy=ccy_4q)
    if bs_8q:
        add_bs(con, ticker, T8, BS_8Q, 102e6, filed=filed_8q, ccy=ccy_8q)
    qs = QUARTERS[-n_quarters:]
    add_flow(con, ticker, "ni_common_q", NI_Q[-n_quarters:], quarters=ni_quarters or qs)
    add_flow(con, ticker, "cfo_q", CFO_Q[-n_quarters:], quarters=qs)
    add_flow(con, ticker, "gross_profit_q", GP_Q[-n_quarters:], quarters=qs, ccy=gp_ccy)
    add_flow(con, ticker, "revenue_q", REV_Q[-n_quarters:], quarters=qs)


@pytest.fixture
def con():
    c = connect(":memory:")
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "US10Y", 0.035,
                                                            "test"])
    c.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [date(2026, 10, 30), "CA10Y", 0.03,
                                                            "test"])
    c.execute("INSERT INTO fx_daily VALUES (?,?,?)", [date(2026, 10, 30), "USDCAD", 1.40])
    add_company(c, "GOLD")                                       # E1 -> BUY
    add_company(c, "NO8Q", bs_8q=False)                          # E2 fallback -> F 9
    add_company(c, "LATE8", filed_8q=LATE)                       # t-8q filed after as_of
    add_company(c, "SHORT", n_quarters=5, bs_4q=False, bs_8q=False)  # TSX-like history
    add_company(c, "FIN", sector="Financials")
    # E_PERIOD_MISMATCH: NI quarters end 2026-06-30 (8 quarters), balance sheet 2026-09-30
    add_company(c, "NILAG", ni_quarters=QUARTERS[:8])
    add_company(c, "GPCCY", gp_ccy="CAD")                        # current GP TTM in CAD
    add_company(c, "P4CCY", ccy_4q="CAD")                        # t-4q filing in CAD
    add_company(c, "P8CCY", ccy_8q="CAD")                        # t-8q filing in CAD
    add_company(c, "SPLT", shares_4q=50.5e6)                     # 2:1 split between t-4q, t
    c.execute("INSERT INTO splits VALUES (?,?,?,?)", ["SPLT", date(2026, 3, 2), 2.0, "test"])
    add_company(c, "NOSPLT", shares_4q=50.5e6)                   # same, no split
    # same-source rule: t-4q balance sheet only from Yahoo, t and t-8q from EDGAR
    add_company(c, "YSRC", bs_4q=False)
    add_bs(c, "YSRC", T4, BS_4Q, 101e6, source="yahoo")
    # restatements filed <= as_of are used
    add_company(c, "RSTM")
    add_bs(c, "RSTM", T4, {**BS_4Q, "total_debt": 285}, 101e6, filed=date(2026, 1, 15))
    add_fact(c, "RSTM", T4, date(2026, 10, 1), "revenue_q", 250 * M, "USD")
    # nothing but equity + shares at t: every input None
    add_security(c, "EMPTY", "Industrials")
    add_bars(c, "EMPTY")
    add_bs(c, "EMPTY", T, dict(total_equity=500), 100e6)
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


def check(r, signals, f, n, signal, reason=None):
    assert r["e_signals"] == signals
    if f is None:
        assert blank(r["e_f_score"])
    else:
        assert r["e_f_score"] == f
    assert r["e_n_scored"] == n
    assert r["e_signal"] == signal
    if reason is None:
        assert blank(r["e_no_signal_reason"])
    else:
        assert r["e_no_signal_reason"] == reason


def test_e_columns_order_g_d_e_f_flags(con, csm):
    df, _ = run(con, csm)
    cols = list(df.columns)
    i = cols.index("e_signal")
    assert cols[i:i + 5] == E_COLUMNS  # present, contiguous, spec order
    assert [c for c in cols if c.startswith("e_")] == E_COLUMNS
    g_idx = [k for k, c in enumerate(cols) if c.startswith("g_")]
    d_idx = [k for k, c in enumerate(cols) if c.startswith("d_")]
    f_idx = [k for k, c in enumerate(cols) if c.startswith("f_")]
    assert g_idx and max(g_idx) < i
    assert not d_idx or max(d_idx) < i
    assert "f_signal" in cols and min(f_idx) == i + 5  # f_* block right after e_*
    assert cols[-1] == "flags" and cols.index("flags") > max(f_idx)
    assert not any("vote" in c for c in cols if c.startswith("e_"))


def test_e1_golden_through_engine(con, csm):
    df, _ = run(con, csm)
    r = row(df, "GOLD")
    assert r["eligible"] in (True, "True") or bool(r["eligible"]) is True
    check(r, "111111110", 8, 9, "BUY")
    assert not flagset(r["flags"]) & {FB, "E_PERIOD_MISMATCH", "PRIOR_CCY_MISMATCH",
                                      "ANNUAL_PRIOR", "F_INCOMPLETE"}


def test_missing_t8q_filing_fallback(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NO8Q")  # E2: ta_8q None -> ta_4q
    check(r, "111111111", 9, 9, "BUY")
    assert FB in flagset(r["flags"])


def test_t8q_filed_after_as_of_not_used(con, csm):
    df, _ = run(con, csm)
    r = row(df, "LATE8")
    check(r, "111111111", 9, 9, "BUY")
    assert FB in flagset(r["flags"])


def test_short_history_incomplete(con, csm):
    df, _ = run(con, csm)
    r = row(df, "SHORT")  # 5 quarters, no t-4q / t-8q filing
    check(r, "-1-1-----", 2, 2, "NO_SIGNAL", "F_INCOMPLETE")
    assert FB not in flagset(r["flags"])  # signals 3 and 9 unscored
    assert "F_INCOMPLETE" not in flagset(r["flags"])  # reason only


def test_financials_excluded_metrics_output(con, csm):
    df, _ = run(con, csm)
    r = row(df, "FIN")
    check(r, "111111110", 8, 9, "NO_SIGNAL", "EXCLUDED_SECTOR")


def test_flow_period_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "NILAG")  # NI TTM ends 2026-06-30 != t: ni_ttm (and its prior) None
    check(r, "-1--11110", 5, 6, "NO_SIGNAL", "F_INCOMPLETE")
    assert "E_PERIOD_MISMATCH" in flagset(r["flags"])
    assert "E_PERIOD_MISMATCH" not in flagset(row(df, "GOLD")["flags"])


def test_flow_currency_mismatch(con, csm):
    df, _ = run(con, csm)
    r = row(df, "GPCCY")  # gp_ttm None (and no prior) -> signal 8 unscored, E3 pattern
    check(r, "1111111-0", 7, 8, "BUY")
    assert "FLOW_CCY_MISMATCH" in flagset(r["flags"])


def test_prior_4q_currency_mismatch_shares_kept(con, csm):
    df, _ = run(con, csm)
    r = row(df, "P4CCY")  # ta/debt/ca/cl at t-4q None; shares_4q kept -> signal 7 scored
    check(r, "-1-1--11-", 4, 4, "NO_SIGNAL", "F_INCOMPLETE")
    assert "PRIOR_CCY_MISMATCH" in flagset(r["flags"])
    assert FB not in flagset(r["flags"])


def test_prior_8q_currency_mismatch(con, csm):
    # SPEC-AMBIGUITY: a t-8q filing in another currency gives ta_8q None (Data mapping), and
    # §Formulas substitutes ta_4q for any None ta_8q, so the fallback applies and is flagged
    # (E2 result). The spec's "missing only" wording could be read as excluding this case.
    df, _ = run(con, csm)
    r = row(df, "P8CCY")
    check(r, "111111111", 9, 9, "BUY")
    assert {"PRIOR_CCY_MISMATCH", FB} <= flagset(r["flags"])


def test_split_factor_puts_shares_4q_on_t_basis(con, csm):
    df, _ = run(con, csm)
    check(row(df, "SPLT"), "111111110", 8, 9, "BUY")  # 50.5e6 x 2 = 101e6
    check(row(df, "NOSPLT"), "111111010", 7, 9, "HOLD")  # 100e6 * 200 > 50.5e6 * 201


def test_same_source_prior_filing(con, csm):
    df, _ = run(con, csm)
    r = row(df, "YSRC")  # EDGAR t; the Yahoo t-4q filing is not used -> t-4q inputs None
    check(r, "-1-1---1-", 3, 3, "NO_SIGNAL", "F_INCOMPLETE")
    assert FB not in flagset(r["flags"])


def test_restatements_filed_before_as_of_used(con, csm):
    df, _ = run(con, csm)
    r = row(df, "RSTM")
    # t-4q filing restated (debt 285 -> lev tie, signal 5 = 0); 2025-09-30 revenue restated to
    # 250 -> rev_ttm_prior 1112.5: gm_prior 0.3596 > 0.35 (signal 8 = 0), ato_prior
    # 1112.5/900 = 1.236 < 1.263 (signal 9 = 1)
    check(r, "111101101", 7, 9, "HOLD")


def test_everything_missing_blank_f_score(con, csm):
    df, _ = run(con, csm)
    r = row(df, "EMPTY")
    check(r, "---------", None, 0, "NO_SIGNAL", "F_INCOMPLETE")


def test_filings_and_quarters_after_as_of_not_used(con, csm):
    before, _ = run(con, csm)
    late2 = date(2027, 2, 5)
    # restated current and q_prev quarters, a new quarter, a new balance sheet, restated t-4q
    # and t-8q filings, all filed after as_of
    add_fact(con, "GOLD", T, LATE, "ni_common_q", -500 * M, "USD")
    add_fact(con, "GOLD", T4, LATE, "revenue_q", 10 * M, "USD")
    add_fact(con, "GOLD", T4, LATE, "ni_common_q", 500 * M, "USD")
    add_fact(con, "GOLD", date(2026, 12, 31), late2, "ni_common_q", -500 * M, "USD")
    add_bs(con, "GOLD", date(2026, 12, 31), dict(BS_T, total_assets=5000), 300e6, filed=late2)
    add_bs(con, "GOLD", T4, dict(BS_4Q, total_assets=5000, total_debt=4000), 500e6, filed=LATE)
    add_bs(con, "GOLD", T8, dict(BS_8Q, total_assets=1), 500e6, filed=LATE)
    after, _ = run(con, csm)
    for c in E_COLUMNS + ["flags"]:
        a, b = row(before, "GOLD")[c], row(after, "GOLD")[c]
        assert (blank(a) and blank(b)) or a == b, c
    check(row(after, "GOLD"), "111111110", 8, 9, "BUY")


def test_labels_consistency(con, csm):
    df, _ = run(con, csm)
    for _, r in df.iterrows():
        sig = r["e_signal"]
        assert sig in LABELS, r["ticker"]
        assert (sig == "NO_SIGNAL") == (not blank(r["e_no_signal_reason"])), r["ticker"]
        s = r["e_signals"]
        assert isinstance(s, str) and len(s) == 9 and set(s) <= {"0", "1", "-"}, r["ticker"]
        assert r["e_n_scored"] == 9 - s.count("-"), r["ticker"]
        if r["e_n_scored"] > 0:
            assert r["e_f_score"] == s.count("1"), r["ticker"]
        else:
            assert blank(r["e_f_score"]), r["ticker"]
        assert "ANNUAL_PRIOR" not in flagset(r["flags"]), r["ticker"]


def test_write_signals_columns_and_disclaimer(con, csm, tmp_path):
    df, _ = run(con, csm)
    csv_path, pq_path = sorted(write_signals(df, AS_OF, out_root=tmp_path),
                               key=lambda p: str(p).endswith(".parquet"))
    first = csv_path.read_text().splitlines()[0]
    assert "piotroski/0.1.0" in first
    assert "Model E is new and unvalidated (not backtested)." in first
    back = pd.read_csv(csv_path, skiprows=1, dtype={"e_signals": str, "e_signal": str})
    assert [c for c in back.columns if c.startswith("e_")] == E_COLUMNS
    assert list(back.columns) == list(df.columns)
    g = back[back["ticker"] == "GOLD"].iloc[0]
    assert (g["e_signal"], g["e_signals"]) == ("BUY", "111111110")
    assert float(g["e_f_score"]) == 8 and float(g["e_n_scored"]) == 9
    s = back[back["ticker"] == "SHORT"].iloc[0]
    assert s["e_signals"] == "-1-1-----" and s["e_no_signal_reason"] == "F_INCOMPLETE"
    e = back[back["ticker"] == "EMPTY"].iloc[0]
    assert blank(e["e_f_score"]) and e["e_signals"] == "---------"  # empty when None
    raw = csv_path.read_text()
    assert "SCREEN_" not in raw
    assert not any("vote" in c.lower() for c in back.columns)

    import pyarrow.parquet as pq

    table = pq.read_table(pq_path)
    meta = table.schema.metadata[b"disclaimer"].decode()
    assert "piotroski/0.1.0" in meta
    assert "Model E is new and unvalidated (not backtested)." in meta
    pdf = table.to_pandas()
    assert [c for c in pdf.columns if c.startswith("e_")] == E_COLUMNS
    gp = pdf[pdf["ticker"] == "GOLD"].iloc[0]
    assert gp["e_signals"] == "111111110"  # stored as a string, not a number
    assert pdf[pdf["ticker"] == "SHORT"].iloc[0]["e_signals"] == "-1-1-----"
