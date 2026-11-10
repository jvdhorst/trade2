"""Spec tests for backtest splits and point-in-time signals (docs/models/backtest.md §3, API
contract, property expectations "look-ahead").

Written from the spec only (API contract):
    from valuation.backtest.engine import signals_on
    from valuation.backtest.data import parse_splits, as_traded
    from valuation.backtest.membership import Interval
    from valuation.engine import split_factor, dps_ttm, build_inputs   # live engine, split-aware

The backtest modules are imported inside the tests, so the live-engine split tests run on their own.
The look-ahead tests use the live eligibility config (config/liquidity.yaml, US: MDTV >= 25M,
market cap >= 5B, price >= 5, <= 5 stale sessions, 273 sessions of history), since `signals_on`
takes no liquidity argument.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from valuation.engine import SIGNAL_COLUMNS, build_inputs, dps_ttm, run_signals, split_factor
from valuation.providers.base import Dividend, PriceBar
from valuation.store.db import connect

TOL = 1e-9


def _ts(d: date, hour: int = 13, minute: int = 30) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC).timestamp())


# ---------------------------------------------------------------------------
# parse_splits / as_traded
# ---------------------------------------------------------------------------


def chart_payload(splits: dict) -> dict:
    return {"chart": {"result": [{
        "meta": {"currency": "USD", "symbol": "X"},
        "timestamp": [_ts(date(2020, 8, 28)), _ts(date(2020, 8, 31))],
        "indicators": {"quote": [{"close": [499.23, 129.04], "volume": [46907479, 225702700]}],
                       "adjclose": [{"adjclose": [122.0, 126.5]}]},
        "events": {"dividends": {str(_ts(date(2020, 8, 7))): {
            "amount": 0.205, "date": _ts(date(2020, 8, 7))}},
            "splits": splits},
    }], "error": None}}


def test_parse_splits_sorted_ratio_and_utc_date():
    from valuation.backtest.data import parse_splits

    t1 = _ts(date(2024, 6, 10))           # NVDA 10-for-1
    t2 = _ts(date(2020, 8, 31))           # AAPL 4-for-1
    t3 = _ts(date(2022, 1, 3), 0, 30)     # reverse split 1-for-10, early UTC hour
    payload = chart_payload({
        str(t1): {"date": t1, "numerator": 10, "denominator": 1, "splitRatio": "10:1"},
        str(t3): {"date": t3, "numerator": 1, "denominator": 10, "splitRatio": "1:10"},
        str(t2): {"date": t2, "numerator": 4, "denominator": 1, "splitRatio": "4:1"},
    })
    got = [(d, r) for d, r in parse_splits(payload)]
    assert [d for d, _ in got] == [date(2020, 8, 31), date(2022, 1, 3), date(2024, 6, 10)]
    assert [r for _, r in got] == pytest.approx([4.0, 0.1, 10.0], abs=TOL)


def test_parse_splits_none():
    from valuation.backtest.data import parse_splits

    assert list(parse_splits(chart_payload({}))) == []
    p = chart_payload({})
    del p["chart"]["result"][0]["events"]["splits"]
    assert list(parse_splits(p)) == []


def test_as_traded_spec_example():
    from valuation.backtest.data import as_traded

    src = "yahoo_chart"
    bars = [PriceBar("X", date(2020, 8, 28), 125.0, 122.0, 400e6, src),
            PriceBar("X", date(2020, 8, 31), 129.0, 126.5, 225e6, src)]
    divs = [Dividend("X", date(2020, 8, 7), 0.205, "USD", src)]
    out_bars, out_divs = as_traded(bars, divs, [(date(2020, 8, 31), 4.0)])
    b = {x.date: x for x in out_bars}
    assert b[date(2020, 8, 28)].close == pytest.approx(500.0, abs=TOL)
    assert b[date(2020, 8, 28)].volume == pytest.approx(100e6, abs=1e-3)
    assert b[date(2020, 8, 28)].adj_close == 122.0           # unchanged
    # a bar on the split date is already on the new basis (F counts splits dated AFTER t)
    assert b[date(2020, 8, 31)].close == pytest.approx(129.0, abs=TOL)
    assert b[date(2020, 8, 31)].volume == pytest.approx(225e6, abs=1e-3)
    assert b[date(2020, 8, 31)].adj_close == 126.5
    assert len(out_divs) == 1
    assert out_divs[0].ex_date == date(2020, 8, 7)
    assert out_divs[0].amount == pytest.approx(0.82, abs=TOL)


def test_as_traded_multiple_splits_product():
    from valuation.backtest.data import as_traded

    src = "yahoo_chart"
    bars = [PriceBar("X", date(2020, 8, 28), 12.5, 12.0, 4e8, src),
            PriceBar("X", date(2021, 1, 4), 13.0, 12.9, 1e8, src),
            PriceBar("X", date(2024, 6, 10), 121.0, 121.0, 3e8, src)]
    divs = [Dividend("X", date(2020, 8, 7), 0.0205, "USD", src),
            Dividend("X", date(2024, 6, 11), 0.01, "USD", src)]
    splits = [(date(2020, 8, 31), 4.0), (date(2024, 6, 10), 10.0)]
    out_bars, out_divs = as_traded(bars, divs, splits)
    b = {x.date: x for x in out_bars}
    assert b[date(2020, 8, 28)].close == pytest.approx(500.0, abs=TOL)   # × 40
    assert b[date(2020, 8, 28)].volume == pytest.approx(1e7, abs=1e-3)   # / 40
    assert b[date(2021, 1, 4)].close == pytest.approx(130.0, abs=TOL)    # × 10
    assert b[date(2024, 6, 10)].close == pytest.approx(121.0, abs=TOL)   # × 1
    assert all(b[k].adj_close == x.adj_close for k, x in zip(b, bars, strict=True))
    d = {x.ex_date: x for x in out_divs}
    assert d[date(2020, 8, 7)].amount == pytest.approx(0.82, abs=TOL)
    assert d[date(2024, 6, 11)].amount == pytest.approx(0.01, abs=TOL)


def test_as_traded_no_splits_identity():
    from valuation.backtest.data import as_traded

    bars = [PriceBar("X", date(2020, 8, 28), 125.0, 122.0, 4e8, "s")]
    divs = [Dividend("X", date(2020, 8, 7), 0.205, "USD", "s")]
    ob, od = as_traded(bars, divs, [])
    assert [(x.date, x.close, x.adj_close, x.volume) for x in ob] == [
        (date(2020, 8, 28), 125.0, 122.0, 4e8)]
    assert [(x.ex_date, x.amount) for x in od] == [(date(2020, 8, 7), 0.205)]


# ---------------------------------------------------------------------------
# Live engine, split-aware: split_factor, dps_ttm, build_inputs (spec §3 example)
# ---------------------------------------------------------------------------

SPLIT = date(2020, 8, 31)


def add_split(con, ticker, d, ratio):
    con.execute("INSERT INTO splits VALUES (?,?,?,?)", [ticker, d, ratio, "test"])


@pytest.fixture
def split_db():
    con = connect(":memory:")
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ["X", "NASDAQ", "X", "USD", "USD", "Information Technology", None, 1, 1.0,
         "Information Technology"])
    for d, c in [(date(2020, 8, 28), 500.0), (SPLIT, 129.0), (date(2020, 9, 30), 115.0)]:
        con.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?)",
                    ["X", d, c, c, 1e8, "test"])
    for item, value, unit in [("total_equity", 7.2e10, "USD"),
                              ("shares_outstanding", 1.0e9, "shares")]:
        con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                    ["X", date(2020, 6, 27), date(2020, 7, 30), item, value, unit,
                     "sec_edgar", False])
    con.execute("INSERT INTO dividends VALUES (?,?,?,?,?)",
                ["X", date(2020, 8, 7), 0.82, "USD", "test"])  # as traded
    add_split(con, "X", SPLIT, 4.0)
    add_split(con, "OTHER", date(2020, 8, 15), 3.0)
    return con


def test_split_factor(split_db):
    con = split_db
    assert split_factor(con, "X", date(2020, 7, 30), date(2020, 8, 31)) == 4.0
    assert split_factor(con, "X", date(2020, 7, 30), date(2020, 8, 30)) == 1.0
    assert split_factor(con, "X", date(2020, 8, 31), date(2020, 9, 30)) == 1.0   # after exclusive
    assert split_factor(con, "X", date(2020, 8, 30), date(2020, 8, 31)) == 4.0   # through incl.
    assert split_factor(con, "NOSPLIT", date(2000, 1, 1), date(2030, 1, 1)) == 1.0
    add_split(con, "X", date(2024, 6, 10), 10.0)
    assert split_factor(con, "X", date(2020, 1, 1), date(2025, 1, 1)) == pytest.approx(40.0)
    assert split_factor(con, "X", date(2021, 1, 1), date(2025, 1, 1)) == pytest.approx(10.0)


def test_split_factor_empty_table():
    con = connect(":memory:")
    assert split_factor(con, "X", date(2020, 1, 1), date(2021, 1, 1)) == 1.0


def test_dps_ttm_divides_by_later_splits(split_db):
    con = split_db
    assert dps_ttm(con, "X", date(2020, 8, 30)) == pytest.approx(0.82, abs=TOL)
    assert dps_ttm(con, "X", date(2020, 8, 31)) == pytest.approx(0.205, abs=TOL)
    assert dps_ttm(con, "X", date(2020, 9, 30)) == pytest.approx(0.205, abs=TOL)  # 0.82 / 4


def test_dps_ttm_ex_on_split_date_not_divided(split_db):
    con = split_db
    con.execute("INSERT INTO dividends VALUES (?,?,?,?,?)", ["X", SPLIT, 0.205, "USD", "test"])
    # ex 2020-08-31 is already on the new basis: split date not in (ex_date, d]
    assert dps_ttm(con, "X", date(2020, 9, 30)) == pytest.approx(0.205 + 0.205, abs=TOL)


def test_dps_ttm_unchanged_without_splits(split_db):
    con = split_db
    con.execute("DELETE FROM splits")
    assert dps_ttm(con, "X", date(2020, 9, 30)) == pytest.approx(0.82, abs=TOL)


def _inp(con, d):
    inputs, skipped = build_inputs(con, d, {"X"})
    assert not skipped, skipped
    assert len(inputs) == 1
    return inputs[0][0]


def test_build_inputs_shares_split_adjusted(split_db):
    con = split_db
    i = _inp(con, date(2020, 8, 30))
    assert i.shares_outstanding == pytest.approx(1.0e9)
    assert i.price == 500.0
    assert i.price * i.shares_outstanding == pytest.approx(5.0e11)     # market cap
    i = _inp(con, date(2020, 8, 31))
    assert i.shares_outstanding == pytest.approx(4.0e9)
    assert i.price == 129.0
    i = _inp(con, date(2020, 9, 30))
    assert i.shares_outstanding == pytest.approx(4.0e9)


def test_build_inputs_split_after_d_changes_nothing(split_db):
    con = split_db
    before = _inp(con, date(2020, 8, 30))
    add_split(con, "X", date(2020, 9, 15), 2.0)
    after = _inp(con, date(2020, 8, 30))
    assert after.shares_outstanding == before.shares_outstanding
    assert _inp(con, date(2020, 9, 30)).shares_outstanding == pytest.approx(8.0e9)


def test_build_inputs_unchanged_without_splits(split_db):
    con = split_db
    con.execute("DELETE FROM splits")
    assert _inp(con, date(2020, 9, 30)).shares_outstanding == pytest.approx(1.0e9)


# ---------------------------------------------------------------------------
# signals_on: point-in-time, look-ahead
# ---------------------------------------------------------------------------

D = date(2020, 6, 30)
BAR_START, BAR_END = date(2019, 3, 1), date(2020, 9, 30)
FIN = [f"F{i:02d}" for i in range(1, 14)]          # 13 names: Model B can fit (MIN_FIT 12)
QEND = [date(2018, 12, 31), date(2019, 3, 31), date(2019, 6, 30), date(2019, 9, 30),
        date(2019, 12, 31), date(2020, 3, 31), date(2020, 6, 30)]
START = date(2013, 1, 1)


def bdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bulk(con, table, rows, cols):
    df = pd.DataFrame(rows, columns=cols)
    con.register("_tmp_rows", df)
    con.execute(f"INSERT INTO {table} SELECT * FROM _tmp_rows")
    con.unregister("_tmp_rows")


def add_sec(con, t, sector="Financials"):
    con.execute(
        "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, sector, "
        "industry, share_classes, share_conversion, gics_sector) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [t, "NYSE", t, "USD", "USD", sector, None, 1, 1.0, sector])


def add_bars(con, t, price, start=BAR_START, end=BAR_END, volume=2.0e6):
    rows = [(t, d, price * (1 + 0.001 * (k % 7)), price * (1 + 0.001 * (k % 7)), volume, "test")
            for k, d in enumerate(bdays(start, end))]
    _bulk(con, "prices_daily", rows, ["ticker", "date", "close", "adj_close", "volume", "source"])


def fact(con, t, pe, filed, item, value, unit, source="sec_edgar", est=False):
    con.execute("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?)",
                [t, pe, filed, item, value, unit, source, est])


def add_name(con, t, i):
    """A valid US Financials name: price 20 + 2.5i, 1e9 shares, equity (20 + 2i)e9, ROE 4% + 1% i
    (MDTV >= 40M, market cap >= 20B)."""
    add_sec(con, t)
    add_bars(con, t, 20.0 + 2.5 * i)
    eq = (20.0 + 2 * i) * 1e9
    roe = 0.04 + 0.01 * i
    for q in QEND:
        filed = q + timedelta(days=40)
        fact(con, t, q, filed, "total_equity", eq, "USD")
        fact(con, t, q, filed, "shares_outstanding", 1.0e9, "shares")
        fact(con, t, q, filed, "ni_common_q", eq * roe / 4, "USD")


def base_db():
    con = connect(":memory:")
    for i, t in enumerate(FIN):
        add_name(con, t, i)
    for t, i in (("OLD", 3), ("NEW", 5)):
        add_name(con, t, i)
    add_name(con, "NOPX", 4)                      # member, but bars only after D
    con.execute("DELETE FROM prices_daily WHERE ticker='NOPX' AND date<=?", [D])
    add_bars(con, "NOSEC", 50.0)                  # member with bars, no securities row
    for m in range(1, 13):
        for y in (2019, 2020):
            con.execute("INSERT INTO rates_daily VALUES (?,?,?,?)",
                        [date(y, m, 1), "US10Y", 0.015 + 0.001 * m, "test"])
    return con


def intervals():
    from valuation.backtest.membership import Interval

    out = [Interval(t, START, None) for t in FIN]
    out += [Interval("OLD", START, date(2020, 5, 31)),     # removed before D
            Interval("NEW", date(2020, 7, 15), None),      # added after D
            Interval("NOPX", START, None), Interval("NOSEC", START, None),
            Interval("GONE", START, date(2015, 1, 1))]
    return out


def sig(con, d=D):
    from valuation.backtest.engine import signals_on

    return signals_on(con, d, intervals())


def assert_same(a: pd.DataFrame, b: pd.DataFrame):
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def row(df, t):
    sub = df[df["ticker"] == t]
    assert len(sub) == 1
    return sub.iloc[0]


@pytest.fixture(scope="module")
def base_frame():
    return sig(base_db())


def test_signals_on_columns_and_order(base_frame):
    df = base_frame
    assert list(df.columns) == list(SIGNAL_COLUMNS)
    assert list(df["ticker"]) == sorted(df["ticker"])


def test_synthetic_universe_is_valid(base_frame):
    # sanity of the fixture: every name passes the live eligibility filter and Model A runs
    df = base_frame
    assert df["eligible"].all(), df[["ticker", "elig_fail_reason"]]
    assert set(df["a_signal"]) <= {"BUY", "HOLD", "SELL"}, df[["ticker", "a_no_signal_reason"]]
    assert df["a_mos"].notna().all()


def test_membership_on_d(base_frame):
    # (c) members on D with a securities row and >= 1 bar <= D; not today's membership
    assert list(base_frame["ticker"]) == FIN
    con = base_db()
    early = sig(con, date(2020, 5, 29))
    assert "OLD" in set(early["ticker"]) and "NEW" not in set(early["ticker"])
    late = sig(con, date(2020, 7, 15))
    assert "NEW" in set(late["ticker"]) and "OLD" not in set(late["ticker"])
    assert "NOPX" in set(late["ticker"])                 # its first bar is now <= d
    for df in (early, late):
        assert "NOSEC" not in set(df["ticker"]) and "GONE" not in set(df["ticker"])


def test_signals_on_equals_live_run_signals(base_frame):
    con = base_db()
    live, _ = run_signals(con, D, tickers=set(FIN), csm_path=None)
    live = live.sort_values("ticker").reset_index(drop=True)
    assert_same(base_frame, live)


def test_later_filed_restatement_ignored(base_frame):
    # (a) restated equity / NI for the 2020-03-31 quarter filed after D: no change on D
    con = base_db()
    q, filed = date(2020, 3, 31), date(2020, 7, 5)
    fact(con, "F01", q, filed, "total_equity", 35e9, "USD")
    fact(con, "F01", q, filed, "shares_outstanding", 1.0e9, "shares")
    fact(con, "F01", q, filed, "ni_common_q", 2.0e9, "USD")
    assert_same(sig(con), base_frame)


def test_restatement_filed_on_or_before_d_is_used(base_frame):
    con = base_db()
    q = date(2020, 3, 31)
    fact(con, "F01", q, D, "total_equity", 35e9, "USD")         # filed exactly on D
    fact(con, "F01", q, D, "shares_outstanding", 1.0e9, "shares")
    df = sig(con)
    assert row(df, "F01")["bvps"] == pytest.approx(35.0, rel=1e-12)
    assert row(base_frame, "F01")["bvps"] == pytest.approx(20.0, rel=1e-12)


def test_ni_restatement_timing(base_frame):
    q = date(2020, 3, 31)
    con = base_db()
    # v0.2.0: restated value 3.0e9 -> 0.5e9. 3.0e9 is a dominant quarter (> 50% of TTM NI), so
    # Model A's one-off rule (residual_income 0.2.0 R2) normalises roe0 back to the base 0.05.
    fact(con, "F02", q, date(2020, 7, 1), "ni_common_q", 0.5e9, "USD")
    assert_same(sig(con), base_frame)
    con = base_db()
    fact(con, "F02", q, date(2020, 6, 20), "ni_common_q", 0.5e9, "USD")
    df = sig(con)
    assert row(df, "F02")["roe0"] != pytest.approx(row(base_frame, "F02")["roe0"], rel=1e-9)


def test_prices_after_d_ignored(base_frame):
    # (b) change every bar after D and add new ones: D's rows identical
    con = base_db()
    con.execute("UPDATE prices_daily SET close = close * 3, adj_close = adj_close * 3, "
                "volume = 0 WHERE date > ?", [D])
    add_bars(con, "F01", 999.0, start=date(2020, 10, 1), end=date(2020, 12, 31))
    add_bars(con, "OLD", 1.0, start=date(2020, 10, 1), end=date(2020, 10, 30))
    assert_same(sig(con), base_frame)


def test_price_on_d_is_used(base_frame):
    con = base_db()
    con.execute("UPDATE prices_daily SET close = close * 1.5 WHERE ticker='F03' AND date = ?", [D])
    assert row(sig(con), "F03")["price"] == pytest.approx(row(base_frame, "F03")["price"] * 1.5)


def test_rates_and_splits_after_d_ignored(base_frame):
    con = base_db()
    con.execute("UPDATE rates_daily SET rate = 0.09 WHERE date > ?", [D])
    con.execute("INSERT INTO rates_daily VALUES (?,?,?,?)", [D + timedelta(days=2), "US10Y",
                                                             0.11, "test"])
    add_split(con, "F01", date(2020, 7, 15), 2.0)
    add_split(con, "F04", date(2020, 9, 1), 4.0)
    assert_same(sig(con), base_frame)
    # control: the latest rate on/before D (2020-06-01) does matter
    con.execute("UPDATE rates_daily SET rate = 0.05 WHERE date = ?", [date(2020, 6, 1)])
    assert row(sig(con), "F01")["a_mos"] != pytest.approx(row(base_frame, "F01")["a_mos"])


def test_estimated_fact_invisible_before_stored_filed_date():
    # (d) a Yahoo (filed_estimated) balance sheet stored pre-lagged with filed_date f: invisible
    # on f - 1, visible on f. signals_on applies no extra lag itself (API contract).
    f = date(2020, 8, 20)
    pe = date(2020, 7, 31)        # newer than the latest EDGAR balance sheet, so it wins
    con0, con1 = base_db(), base_db()
    fact(con1, "F05", pe, f, "total_equity", 60e9, "USD", source="yahoo", est=True)
    fact(con1, "F05", pe, f, "shares_outstanding", 1.0e9, "shares", source="yahoo", est=True)
    before0, before1 = sig(con0, f - timedelta(days=1)), sig(con1, f - timedelta(days=1))
    assert_same(before1, before0)
    on0, on1 = sig(con0, f), sig(con1, f)
    assert row(on1, "F05")["bvps"] == pytest.approx(60.0, rel=1e-12)
    assert row(on0, "F05")["bvps"] != pytest.approx(60.0, rel=1e-6)
