"""In-sample exit lock (docs/models/backtest.md §2, 2026-11-13): an in-sample observation never
uses a forward return whose exit session lies after the last in-sample date."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

from valuation.backtest import report as rep

IN_END = date(2023, 10, 31)


def _fwd():
    return pd.DataFrame([
        # date, ticker, horizon, t1, ret, flag
        (date(2022, 9, 30), "AAA", 12, date(2023, 10, 2), 0.10, None),   # exits inside: kept
        (date(2022, 10, 31), "AAA", 12, date(2023, 11, 1), 0.20, None),  # exits 1 day after: locked
        (date(2023, 6, 30), "AAA", 3, date(2023, 10, 2), 0.03, None),    # kept
        (date(2023, 9, 29), "AAA", 3, date(2024, 1, 2), 0.04, None),     # locked
        (date(2023, 10, 31), "AAA", 12, None, math.nan, "INCOMPLETE"),   # no exit: stays NaN
        (date(2023, 11, 30), "AAA", 3, date(2024, 3, 1), 0.05, None),    # holdout row: untouched
    ], columns=["date", "ticker", "horizon", "t1", "ret", "flag"])


def test_lock_in_sample_masks_exits_after_in_end():
    out = rep.lock_in_sample(_fwd(), IN_END)
    assert out["ret"].tolist()[0] == 0.10
    assert math.isnan(out["ret"].tolist()[1]) and out["flag"].tolist()[1] == "INCOMPLETE"
    assert out["ret"].tolist()[2] == 0.03
    assert math.isnan(out["ret"].tolist()[3]) and out["flag"].tolist()[3] == "INCOMPLETE"
    assert math.isnan(out["ret"].tolist()[4]) and out["flag"].tolist()[4] == "INCOMPLETE"
    assert out["ret"].tolist()[5] == 0.05 and pd.isna(out["flag"].tolist()[5])


def test_lock_in_sample_keeps_rows_and_does_not_mutate_input():
    f = _fwd()
    out = rep.lock_in_sample(f, IN_END)
    assert len(out) == len(f)
    assert f["ret"].tolist()[1] == 0.20


def test_load_returns_carries_t1():
    import duckdb

    con = duckdb.connect()
    con.execute("CREATE TABLE bt_returns (date DATE, ticker VARCHAR, horizon INTEGER, t0 DATE, "
                "t1 DATE, ret DOUBLE, flag VARCHAR)")
    con.execute("INSERT INTO bt_returns VALUES ('2022-10-31','AAA',12,'2022-11-01','2023-11-01',"
                "0.2,NULL), ('2022-10-31','AAA',1,'2022-11-01','2022-12-01',0.01,NULL)")
    r = rep.load_returns(con)
    assert list(r["horizon"]) == [12]
    assert r["t1"].tolist()[0] == date(2023, 11, 1)
