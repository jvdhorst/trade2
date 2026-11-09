"""SW regression tests for run_signals' equity alignment (found in P7-4 QA validation)."""

from datetime import date, timedelta

from test_signals_report_spec import (  # noqa: E402  (shared DB fixture helpers)
    AS_OF,
    LIQ,
    add_bank,
    add_fact,
    add_rates,
    flagset,
    row,
)

from valuation.engine import run_signals
from valuation.store.db import connect


def test_ce_end_from_filing_without_share_count():
    """Multi-class EDGAR filers lack a cover-page share count at some quarter ends. CE_end at q0
    needs equity only (spec Data mapping), so no ROE_PERIOD_MISMATCH and the q0 equity is used."""
    con = connect(":memory:")
    add_rates(con)
    add_bank(con, "BANK")
    # move the q0 (2026-09-30) balance sheet to equity-only: delete its share count
    con.execute("DELETE FROM fundamentals WHERE ticker='BANK' AND item='shares_outstanding' "
                "AND period_end=?", [date(2026, 9, 30)])
    df, _ = run_signals(con, AS_OF, liquidity=LIQ, csm_path=None)
    r = row(df, "BANK")
    assert "ROE_PERIOD_MISMATCH" not in flagset(r["flags"])
    # bvps comes from the latest filing with shares (2026-06-30: 98e9 / 2e9); ROE uses q0 equity
    assert r["bvps"] == 49.0
    assert abs(r["roe0"] - 13.0 / 96.0) < 1e-12


def test_ce_end_missing_at_q0_flags_period_mismatch():
    con = connect(":memory:")
    add_rates(con)
    add_bank(con, "BANK")
    con.execute("DELETE FROM fundamentals WHERE ticker='BANK' AND period_end=? "
                "AND item IN ('total_equity','shares_outstanding')", [date(2026, 9, 30)])
    add_fact(con, "BANK", date(2026, 9, 30), date(2026, 9, 30) + timedelta(days=36),
             "noncontrolling_interest", 0.0, "USD")
    df, _ = run_signals(con, AS_OF, liquidity=LIQ, csm_path=None)
    r = row(df, "BANK")
    assert "ROE_PERIOD_MISMATCH" in flagset(r["flags"])
    assert abs(r["roe0"] - 13.0 / ((98e9 + 92e9) / 2 / 1e9)) < 1e-12
