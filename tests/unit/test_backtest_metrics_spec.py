"""Spec tests for the backtest calendar and metrics (docs/models/backtest.md §2, §4, §6, §8 C1, R1,
N1, B1, P1, and the property expectations).

Written from the spec only (API contract):
    from valuation.backtest.metrics import (forward_returns, sector_excess, spread_by_date,
        yearly_spread, hit_rates, rank_ic, newey_west_t, turnover, pass_bar, PassResult,
        low_pb_baseline)
    from valuation.backtest.engine import rebalance_dates, holdout_dates, sic_to_sector,
        thresholds_hash
"""

from __future__ import annotations

import math
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from valuation.backtest.engine import holdout_dates, rebalance_dates, sic_to_sector, thresholds_hash
from valuation.backtest.metrics import (
    PassResult,
    forward_returns,
    hit_rates,
    low_pb_baseline,
    newey_west_t,
    pass_bar,
    rank_ic,
    sector_excess,
    spread_by_date,
    turnover,
    yearly_spread,
)

TOL = 1e-9
NAN = float("nan")


def close(a, b, tol=TOL):
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return a is None or pd.isna(a)
    return a is not None and not pd.isna(a) and abs(float(a) - float(b)) <= tol


def isnan(x):
    return x is None or pd.isna(x)


# ---------------------------------------------------------------------------
# §2 calendar (C1) and holdout lock
# ---------------------------------------------------------------------------


def test_c1_rebalance_dates():
    assert rebalance_dates(date(2014, 1, 1), date(2014, 4, 30)) == [
        date(2014, 1, 31), date(2014, 2, 28), date(2014, 3, 31)]
    assert rebalance_dates(date(2014, 1, 1), date(2014, 5, 1)) == [
        date(2014, 1, 31), date(2014, 2, 28), date(2014, 3, 31), date(2014, 4, 30)]


def test_c1_holdout_of_short_list_is_all():
    dates = rebalance_dates(date(2014, 1, 1), date(2014, 11, 1))
    assert len(dates) == 10
    assert holdout_dates(dates, n=36) == dates
    assert holdout_dates(dates) == dates  # default HOLDOUT_N = 36


def test_calendar_today_2026_11_10():
    today = date(2026, 11, 10)
    dates = rebalance_dates(date(2014, 1, 1), today)
    assert len(dates) == 154
    assert dates[0] == date(2014, 1, 31) and dates[-1] == date(2026, 10, 31)
    assert dates == sorted(dates)
    assert date(2016, 2, 29) in dates and date(2015, 2, 28) in dates  # calendar month-ends
    ho = holdout_dates(dates)
    ins = [d for d in dates if d not in set(ho)]
    assert len(ho) == 36 and len(ins) == 118
    assert ho[0] == date(2023, 11, 30) and ho[-1] == date(2026, 10, 31)
    assert ins[-1] == date(2023, 10, 31)
    assert ho == dates[-36:]


def test_rebalance_dates_default_start_and_strictly_before_today():
    assert rebalance_dates(today=date(2026, 11, 10)) == rebalance_dates(
        date(2014, 1, 1), date(2026, 11, 10))
    # a month-end equal to today is excluded; start itself counts when it is a month-end
    assert rebalance_dates(date(2014, 1, 31), date(2014, 3, 31)) == [
        date(2014, 1, 31), date(2014, 2, 28)]


# ---------------------------------------------------------------------------
# R1 panel
# ---------------------------------------------------------------------------

SESS = [date(2023, 12, 1), date(2024, 1, 2), date(2024, 2, 1), date(2024, 3, 1),
        date(2024, 4, 1), date(2024, 5, 1)]
PRICES = {
    "F1": [100, 102, 104, 110, 108, 115],
    "F2": [50, 49, 51, 48, 50, 52],
    "F3": [20, 21, 22, 23, 22, 24],
    "F4": [80, 82, 78, 76, 79, 81],
    "F5": [30, 31, 29, 27],  # no bars after 2024-03-01
    "E1": [40, 42, 41, 45, 43, 46],
    "E2": [60, 58, 61, 63, 62, 66],
    "E3": [25, 24, 26, 27, 25, 28],
    "E5": [70, 71, 69, 72, 74, 73],
}
E4_BARS = [(date(2024, 1, 9), 10.0), (date(2024, 2, 1), 11.0), (date(2024, 3, 1), 12.0),
           (date(2024, 4, 1), 11.5), (date(2024, 5, 1), 12.5)]
D1, D2, D3, D4 = date(2023, 11, 30), date(2023, 12, 31), date(2024, 1, 31), date(2024, 2, 29)
DATES = [D1, D2, D3, D4]
TICKERS = ["F1", "F2", "F3", "F4", "F5", "E1", "E2", "E3", "E4", "E5"]
LABEL_CODES = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD", "NE": "NOT_ELIGIBLE",
               "NS": "NO_SIGNAL"}
TABLE = {
    D1: "BUY .40|SELL -.30|HOLD .05|HOLD .10|SELL -.25|BUY .30|SELL -.22|NE .50|HOLD 0|NE nan",
    D2: "BUY .35|HOLD 0|BUY .30|SELL -.28|HOLD 0|BUY .27|HOLD .10|SELL -.21|NS nan|HOLD .05",
    D3: "HOLD .15|SELL -.24|BUY .33|SELL -.30|HOLD .02|BUY .26|HOLD -.05|HOLD .12|BUY .29"
        "|SELL -.35",
    D4: "BUY .3|SELL -.3|BUY .3|SELL -.3|HOLD 0|BUY .3|HOLD 0|HOLD 0|BUY .3|SELL -.3",
}


def r1_prices() -> pd.DataFrame:
    rows = [(t, SESS[i], float(p)) for t, ps in PRICES.items() for i, p in enumerate(ps)]
    rows += [("E4", d, p) for d, p in E4_BARS]
    return pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])


def r1_panel() -> pd.DataFrame:
    rows = []
    for d, line in TABLE.items():
        for t, cell in zip(TICKERS, line.split("|"), strict=True):
            code, score = cell.split()
            label = LABEL_CODES[code]
            rows.append({"date": d, "ticker": t,
                         "sector": "Financials" if t.startswith("F") else "Energy",
                         "eligible": label != "NOT_ELIGIBLE", "label": label,
                         "score": float(score)})
    return pd.DataFrame(rows, columns=["date", "ticker", "sector", "eligible", "label", "score"])


@pytest.fixture(scope="module")
def fwd():
    return forward_returns(r1_prices(), DATES, horizons=(3,))


@pytest.fixture(scope="module")
def obs(fwd):
    return sector_excess(fwd, r1_panel(), min_sector=3)


def cell(df, d, t, col, horizon=3):
    sub = df[(df["date"] == d) & (df["ticker"] == t) & (df["horizon"] == horizon)]
    assert len(sub) == 1, (d, t, len(sub))
    return sub.iloc[0][col]


RET = {  # (ret, flag) per date and ticker
    D1: {"F1": 0.1, "F2": -0.04, "F3": 0.15, "F4": -0.05, "F5": -0.1, "E1": 0.125, "E2": 0.05,
         "E3": 0.08, "E4": NAN, "E5": 0.028571428571},
    D2: {"F1": 0.058823529412, "F2": 0.020408163265, "F3": 0.047619047619,
         "F4": -0.036585365854, "F5": -0.129032258065, "E1": 0.023809523810,
         "E2": 0.068965517241, "E3": 0.041666666667, "E4": NAN, "E5": 0.042253521127},
    D3: {"F1": 0.105769230769, "F2": 0.019607843137, "F3": 0.090909090909,
         "F4": 0.038461538462, "F5": -0.068965517241, "E1": 0.121951219512,
         "E2": 0.081967213115, "E3": 0.076923076923, "E4": 0.136363636364,
         "E5": 0.057971014493},
}
FLAG = {(D1, "E4"): "NO_ENTRY", (D2, "E4"): "NO_ENTRY", (D2, "F5"): "EARLY_EXIT",
        (D3, "F5"): "EARLY_EXIT"}
EXCESS = {
    D1: {"F1": 0.14, "F2": 0.0, "F3": 0.19, "F4": -0.01, "F5": -0.06, "E1": NAN, "E2": NAN,
         "E3": NAN, "E4": NAN, "E5": NAN},
    D2: {"F1": 0.038415366146, "F2": 0.0, "F3": 0.027210884354, "F4": -0.056993529119,
         "F5": -0.149440421330, "E1": -0.018150570087, "E2": 0.027005423345,
         "E3": -0.000293427230, "E4": NAN, "E5": 0.000293427230},
    D3: {"F1": 0.067307692308, "F2": -0.018853695324, "F3": 0.052447552448, "F4": 0.0,
         "F5": -0.107427055703, "E1": 0.039984006397, "E2": 0.0, "E3": -0.005044136192,
         "E4": 0.054396423249, "E5": -0.023996198622},
}
# The spec table prints 12 decimals: compare those cells at 1e-11 (rounding), exact ones at 1e-9.
TABLE_TOL = 1e-11 + 1e-12


# --- 4.1 forward_returns -------------------------------------------------------------------


def test_forward_returns_shape_and_columns(fwd):
    assert list(fwd.columns) == ["date", "ticker", "horizon", "t0", "t1", "ret", "flag"]
    assert len(fwd) == len(DATES) * len(TICKERS)
    assert set(fwd["horizon"]) == {3}
    assert all(isinstance(d, date) for d in fwd["date"])


@pytest.mark.parametrize("d", [D1, D2, D3])
@pytest.mark.parametrize("t", TICKERS)
def test_r1_forward_return(fwd, d, t):
    want = RET[d][t]
    got = cell(fwd, d, t, "ret")
    if math.isnan(want):
        assert isnan(got)
    else:
        assert abs(got - want) <= TABLE_TOL
    flag = FLAG.get((d, t))
    got_flag = cell(fwd, d, t, "flag")
    if flag is None:
        assert isnan(got_flag)
    else:
        assert got_flag == flag


def test_r1_forward_return_exact_values(fwd):
    assert close(cell(fwd, D2, "F5", "ret"), 27 / 31 - 1)
    assert close(cell(fwd, D3, "F5", "ret"), 27 / 29 - 1)
    assert close(cell(fwd, D3, "E4", "ret"), 12.5 / 11 - 1)
    assert close(cell(fwd, D1, "F1", "ret"), 0.1)


def test_r1_t0_t1(fwd):
    assert cell(fwd, D1, "F1", "t0") == date(2023, 12, 1)
    assert cell(fwd, D1, "F1", "t1") == date(2024, 3, 1)   # target 2024-02-29
    assert cell(fwd, D2, "F1", "t0") == date(2024, 1, 2)
    assert cell(fwd, D2, "F1", "t1") == date(2024, 4, 1)   # target 2024-03-31
    assert cell(fwd, D3, "F1", "t1") == date(2024, 5, 1)   # target 2024-04-30
    # F5 EARLY_EXIT: t1 = its last session
    assert cell(fwd, D2, "F5", "t0") == date(2024, 1, 2)
    assert cell(fwd, D2, "F5", "t1") == date(2024, 3, 1)
    assert cell(fwd, D3, "E4", "t0") == date(2024, 2, 1)


def test_r1_d4_all_incomplete(fwd):
    sub = fwd[fwd["date"] == D4]
    assert len(sub) == len(TICKERS)
    assert (sub["flag"] == "INCOMPLETE").all()   # incl. F5 (not EARLY_EXIT)
    assert sub["ret"].isna().all()


def _px(rows):
    return pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])


def test_no_exit():
    # X: one session after d only (t0 == last session); Y extends last_panel
    prices = _px([("X", date(2024, 1, 2), 10.0),
                  ("Y", date(2024, 1, 2), 5.0), ("Y", date(2024, 6, 3), 6.0)])
    out = forward_returns(prices, [date(2023, 12, 31)], horizons=(3,))
    x = out[out["ticker"] == "X"].iloc[0]
    assert x["flag"] == "NO_EXIT" and isnan(x["ret"])
    y = out[out["ticker"] == "Y"].iloc[0]
    assert isnan(y["flag"]) and close(y["ret"], 0.2)
    assert y["t1"] == date(2024, 6, 3)


def test_entry_max_days_boundary():
    d = date(2024, 1, 31)
    prices = _px([("OK", date(2024, 2, 5), 10.0), ("OK", date(2024, 5, 1), 11.0),
                  ("LATE", date(2024, 2, 6), 10.0), ("LATE", date(2024, 5, 1), 12.0),
                  ("Z", date(2024, 2, 1), 1.0), ("Z", date(2024, 6, 3), 1.0)])
    out = forward_returns(prices, [d], horizons=(3,)).set_index("ticker")
    assert isnan(out.loc["OK", "flag"]) and close(out.loc["OK", "ret"], 0.1)   # 5 days: ok
    assert out.loc["LATE", "flag"] == "NO_ENTRY" and isnan(out.loc["LATE", "ret"])  # 6 days


def test_no_session_after_d_is_no_entry():
    prices = _px([("A", date(2024, 1, 2), 10.0), ("B", date(2024, 1, 2), 1.0),
                  ("B", date(2024, 9, 3), 1.0)])
    out = forward_returns(prices, [date(2024, 1, 31)], horizons=(3,)).set_index("ticker")
    assert out.loc["A", "flag"] == "NO_ENTRY" and isnan(out.loc["A", "ret"])


def test_target_equal_last_panel_is_incomplete():
    # d 2024-01-31, h 3 -> target 2024-04-30 == last_panel -> INCOMPLETE (D2: >=, not >)
    prices = _px([("A", date(2024, 2, 1), 10.0), ("A", date(2024, 4, 30), 11.0)])
    out = forward_returns(prices, [date(2024, 1, 31)], horizons=(3,))
    assert out.iloc[0]["flag"] == "INCOMPLETE" and isnan(out.iloc[0]["ret"])


def test_target_is_calendar_month_end_leap_year():
    # d 2023-11-30, h 3 -> target 2024-02-29 (leap): a session on 02-29 is not after target
    prices = _px([("A", date(2023, 12, 1), 10.0), ("A", date(2024, 2, 29), 20.0),
                  ("A", date(2024, 3, 1), 12.0), ("A", date(2024, 6, 3), 13.0)])
    out = forward_returns(prices, [date(2023, 11, 30)], horizons=(3,))
    assert out.iloc[0]["t1"] == date(2024, 3, 1)
    assert close(out.iloc[0]["ret"], 0.2)


def test_target_from_month_end_2014_01_31():
    # 2014-01-31, h=3 -> target 2014-04-30; a session on 2014-04-30 is not after target
    prices = _px([("A", date(2014, 2, 3), 10.0), ("A", date(2014, 4, 30), 50.0),
                  ("A", date(2014, 5, 1), 11.0), ("A", date(2014, 12, 31), 1.0)])
    out = forward_returns(prices, [date(2014, 1, 31)], horizons=(3,))
    assert out.iloc[0]["t1"] == date(2014, 5, 1) and close(out.iloc[0]["ret"], 0.1)


def test_non_positive_or_null_adj_close_is_not_a_session():
    prices = _px([("A", date(2024, 2, 1), 0.0),          # not a session
                  ("A", date(2024, 2, 2), 10.0),          # t0
                  ("A", date(2024, 5, 1), None),          # not a session
                  ("A", date(2024, 5, 2), -1.0),          # not a session
                  ("A", date(2024, 5, 3), 12.0),          # t1
                  ("B", date(2024, 2, 1), 1.0), ("B", date(2024, 9, 3), 1.0)])
    out = forward_returns(prices, [date(2024, 1, 31)], horizons=(3,)).set_index("ticker")
    assert out.loc["A", "t0"] == date(2024, 2, 2)
    assert out.loc["A", "t1"] == date(2024, 5, 3)
    assert close(out.loc["A", "ret"], 0.2)


def test_default_horizons_and_multiple_horizons():
    prices = r1_prices()
    out = forward_returns(prices, [D1])
    assert sorted(set(out["horizon"])) == [3, 6, 12]
    assert len(out) == 3 * len(TICKERS)
    # h = 6 and 12 from 2023-11-30: targets 2024-05-31 / 2024-11-30 >= last_panel
    sub = out[(out["horizon"] > 3) & (out["ticker"] != "E4")]
    assert (sub["flag"] == "INCOMPLETE").all()


# --- 4.2 sector_excess ---------------------------------------------------------------------


@pytest.mark.parametrize("d", [D1, D2, D3])
@pytest.mark.parametrize("t", TICKERS)
def test_r1_excess(obs, d, t):
    want = EXCESS[d][t]
    got = cell(obs, d, t, "excess")
    if math.isnan(want):
        assert isnan(got)
    else:
        assert abs(got - want) <= TABLE_TOL


def test_r1_sector_medians(obs):
    def med(d, sector):
        sub = obs[(obs["date"] == d) & (obs["sector"] == sector) & obs["eligible"]
                  & obs["ret"].notna()]
        vals = set(sub["sector_median"].dropna().round(15))
        ns = set(sub["n_sector"])
        return vals, ns
    v, n = med(D1, "Financials")
    assert n == {5} and len(v) == 1 and close(v.pop(), -0.04)
    v, n = med(D1, "Energy")
    assert n == {2} and v == set()  # n 2 < 3 -> NaN
    v, n = med(D2, "Financials")
    assert n == {5} and close(v.pop(), 1 / 49)
    v, n = med(D2, "Energy")
    assert n == {4} and close(v.pop(), 0.041960093897, 1e-11)
    assert close(cell(obs, D2, "E2", "sector_median"), (1 / 24 + 3 / 71) / 2)
    v, n = med(D3, "Financials")
    assert close(v.pop(), 1 / 26)
    v, n = med(D3, "Energy")
    assert n == {5} and close(v.pop(), 5 / 61)


def test_r1_excess_exact_values(obs):
    assert close(cell(obs, D2, "F1", "excess"), 1 / 17 - 1 / 49)
    assert close(cell(obs, D3, "E4", "excess"), 12.5 / 11 - 1 - 5 / 61)
    assert cell(obs, D3, "F4", "excess") == pytest.approx(0.0, abs=1e-15)


def test_sector_excess_columns(obs):
    for c in ("date", "ticker", "sector", "eligible", "label", "score", "horizon", "ret",
              "flag", "n_sector", "sector_median", "excess"):
        assert c in obs.columns
    assert len(obs) == len(DATES) * len(TICKERS)


def test_sector_excess_ineligible_has_no_excess_but_ret(obs):
    assert close(cell(obs, D1, "E3", "ret"), 0.08)
    assert isnan(cell(obs, D1, "E3", "excess"))
    assert isnan(cell(obs, D1, "E5", "excess"))


def test_sector_excess_none_sector_excluded(fwd):
    panel = r1_panel()
    panel.loc[(panel["date"] == D1) & (panel["ticker"] == "F1"), "sector"] = None
    out = sector_excess(fwd, panel, min_sector=3)
    assert isnan(cell(out, D1, "F1", "excess"))
    # Financials d1 without F1: [-0.04, 0.15, -0.05, -0.1] -> median -0.045, n 4
    assert close(cell(out, D1, "F2", "sector_median"), -0.045)
    assert cell(out, D1, "F2", "n_sector") == 4


def test_sector_excess_default_min_sector_5(fwd):
    out = sector_excess(fwd, r1_panel())  # MIN_SECTOR = 5
    assert close(cell(out, D1, "F1", "excess"), 0.14)      # Financials n 5
    assert isnan(cell(out, D2, "E1", "excess"))            # Energy n 4 < 5


# --- 4.3 spread_by_date / yearly_spread ----------------------------------------------------


def test_r1_spread_by_date(obs):
    sp = spread_by_date(obs)
    assert list(sp.columns) == ["date", "horizon", "n_buy", "n_sell", "buy_excess",
                                "sell_excess", "spread"]
    sp = sp.set_index("date")
    assert len(sp) == 4
    r = sp.loc[D1]
    assert (r["n_buy"], r["n_sell"]) == (1, 2)
    assert close(r["buy_excess"], 0.14) and close(r["sell_excess"], -0.03)
    assert close(r["spread"], 0.17)
    r = sp.loc[D2]
    assert (r["n_buy"], r["n_sell"]) == (3, 2)
    assert close(r["buy_excess"], 0.015825226804, 1e-11)
    assert close(r["sell_excess"], -0.028643478175, 1e-11)
    assert close(r["spread"], 0.044468704979, 1e-11)
    r = sp.loc[D3]
    assert (r["n_buy"], r["n_sell"]) == (3, 3)
    assert close(r["buy_excess"], 0.048942660698, 1e-11)
    assert close(r["sell_excess"], -0.014283297982, 1e-11)
    assert close(r["spread"], 0.063225958680, 1e-11)
    r = sp.loc[D4]
    assert (r["n_buy"], r["n_sell"]) == (0, 0)
    assert isnan(r["spread"])


def test_r1_yearly_spread(obs):
    ys = yearly_spread(spread_by_date(obs))
    assert list(ys.columns) == ["year", "horizon", "n_dates", "spread"]
    ys = ys.set_index("year")
    assert sorted(ys.index) == [2023, 2024]
    assert ys.loc[2023, "n_dates"] == 2
    assert close(ys.loc[2023, "spread"], 0.107234352489, 1e-11)
    assert ys.loc[2024, "n_dates"] == 1           # d4 not counted
    assert close(ys.loc[2024, "spread"], 0.063225958680, 1e-11)


def test_yearly_spread_omits_years_without_spread(obs):
    ys = yearly_spread(spread_by_date(obs, allowed_dates=[D4]))
    assert len(ys) == 0


def test_spread_needs_both_sides(obs):
    o = obs.copy()
    o.loc[(o["date"] == D1) & (o["label"] == "SELL"), "label"] = "HOLD"
    r = spread_by_date(o).set_index("date").loc[D1]
    assert r["n_buy"] == 1 and r["n_sell"] == 0 and isnan(r["spread"])


# --- 4.4 hit_rates -------------------------------------------------------------------------


def test_r1_hit_rates(obs):
    hr = hit_rates(obs)
    assert list(hr.columns) == ["horizon", "n_buy", "buy_hit_rate", "n_sell", "sell_hit_rate"]
    r = hr.set_index("horizon").loc[3]
    assert r["n_buy"] == 7 and close(r["buy_hit_rate"], 6 / 7)
    assert r["n_sell"] == 7 and close(r["sell_hit_rate"], 5 / 7)


def test_hit_rates_empty_is_nan(obs):
    hr = hit_rates(obs, allowed_dates=[D4])
    r = hr.set_index("horizon").loc[3]
    assert r["n_buy"] == 0 and isnan(r["buy_hit_rate"])
    assert r["n_sell"] == 0 and isnan(r["sell_hit_rate"])


# --- 4.5 rank_ic ---------------------------------------------------------------------------


def test_r1_rank_ic(obs):
    ic = rank_ic(obs, min_names=3)
    assert list(ic.columns) == ["date", "horizon", "n", "ic"]
    ic = ic.set_index("date")
    assert ic.loc[D1, "n"] == 7 and ic.loc[D1, "ic"] == pytest.approx(0.5, abs=1e-12)
    assert ic.loc[D2, "n"] == 9 and close(ic.loc[D2, "ic"], 0.6861984751796929)
    assert ic.loc[D3, "n"] == 10 and close(ic.loc[D3, "ic"], 11 / 15)
    assert ic.loc[D4, "n"] == 0 and isnan(ic.loc[D4, "ic"])
    assert close(ic["ic"].dropna().mean(), 0.6398439361710088)


def test_r1_rank_ic_min_names_8(obs):
    ic = rank_ic(obs, min_names=8).set_index("date")
    assert ic.loc[D1, "n"] == 7 and isnan(ic.loc[D1, "ic"])
    assert close(ic["ic"].dropna().mean(), 0.7097659042565131)


def test_rank_ic_default_min_names_20(obs):
    ic = rank_ic(obs)
    assert ic["ic"].isna().all()


def test_rank_ic_matches_spearman(obs):
    from scipy.stats import spearmanr
    sub = obs[(obs["date"] == D2) & obs["eligible"] & obs["score"].notna() & obs["ret"].notna()]
    rho = spearmanr(sub["score"], sub["ret"]).statistic
    got = rank_ic(obs, min_names=3).set_index("date").loc[D2, "ic"]
    assert close(got, rho)


def test_rank_ic_constant_scores_nan(obs):
    o = obs.copy()
    o.loc[o["date"] == D3, "score"] = 0.1
    assert isnan(rank_ic(o, min_names=3).set_index("date").loc[D3, "ic"])


# --- 4.6 newey_west_t (N1) -----------------------------------------------------------------

N1 = [0.05, 0.02, -0.01, 0.04, 0.06, 0.03, -0.02, 0.05]


def test_n1_lag2():
    assert close(newey_west_t(N1, 2), 4.763139720814412)
    assert close(newey_west_t(N1, 2), 0.0275 * math.sqrt(30000))


def test_n1_lag0_is_plain_t_population_sd():
    assert close(newey_west_t(N1, 0), 2.852096383629523)
    x = np.array(N1)
    assert close(newey_west_t(N1, 0), x.mean() / (x.std(ddof=0) / math.sqrt(len(x))))


def test_n1_lag6():
    assert close(newey_west_t(N1, 6), 7.545937746270388)


def test_n1_t_le_l_plus_1_nan():
    assert isnan(newey_west_t(N1, 7))     # T = 8 = L + 1
    assert isnan(newey_west_t(N1, 8))
    assert isnan(newey_west_t([], 0))


def test_n1_zero_variance_nan():
    assert isnan(newey_west_t([0.02] * 5, 2))


def test_n1_nan_dropped_not_zero_filled():
    with_nan = [0.05, NAN, 0.02, -0.01, 0.04, NAN, 0.06, 0.03, -0.02, 0.05]
    assert close(newey_west_t(with_nan, 2), newey_west_t(N1, 2))
    assert close(newey_west_t(np.array(with_nan), 2), 4.763139720814412)
    assert close(newey_west_t(pd.Series(with_nan), 2), 4.763139720814412)


def test_n1_nan_dropped_then_too_short():
    assert isnan(newey_west_t([0.01, NAN, 0.03, NAN], 1))   # T 2 <= L + 1


# --- 4.7 turnover --------------------------------------------------------------------------


def r1_labels(dates=(D1, D2, D3)):
    p = r1_panel()
    return p[p["date"].isin(dates)][["date", "ticker", "label"]].reset_index(drop=True)


def test_r1_turnover():
    to = turnover(r1_labels())
    for c in ("date", "label_turnover", "buy_turnover", "sell_turnover"):
        assert c in to.columns
    to = to.set_index("date")
    assert sorted(to.index) == [D2, D3]
    assert close(to.loc[D2, "label_turnover"], 5 / 7)
    assert close(to.loc[D2, "buy_turnover"], 1 / 3)
    assert close(to.loc[D2, "sell_turnover"], 1.0)
    assert close(to.loc[D3, "label_turnover"], 4 / 9)
    assert close(to.loc[D3, "buy_turnover"], 1 / 3)
    assert close(to.loc[D3, "sell_turnover"], 2 / 3)
    assert close(to["label_turnover"].dropna().mean(), 73 / 126)
    assert close(to["buy_turnover"].dropna().mean(), 1 / 3)
    assert close(to["sell_turnover"].dropna().mean(), 5 / 6)


def test_turnover_pairs_consecutive_within_allowed():
    to = turnover(r1_labels(), allowed_dates=[D1, D3]).set_index("date")
    assert list(to.index) == [D3]
    # both-labelled d1 & d3: F1..F5, E1, E2, E4 (8); changed F1, F3, F4, F5, E2, E4
    assert close(to.loc[D3, "label_turnover"], 6 / 8)
    assert close(to.loc[D3, "buy_turnover"], 2 / 3)    # F3, E4 new
    assert close(to.loc[D3, "sell_turnover"], 2 / 3)   # F4, E5 new
    filtered = turnover(r1_labels((D1, D3)))
    pd.testing.assert_frame_equal(
        to.reset_index().sort_values("date").reset_index(drop=True),
        filtered.sort_values("date").reset_index(drop=True), check_dtype=False)


def test_turnover_empty_sets_nan():
    lab = pd.DataFrame([(D1, "A", "HOLD"), (D2, "A", "NO_SIGNAL"), (D1, "B", "SELL"),
                        (D2, "B", "NOT_ELIGIBLE")], columns=["date", "ticker", "label"])
    r = turnover(lab).set_index("date").loc[D2]
    assert isnan(r["label_turnover"]) and isnan(r["buy_turnover"]) and isnan(r["sell_turnover"])


# --- allowed_dates and permutation properties ----------------------------------------------


@pytest.mark.parametrize("subset", [[D2, D3], [D1], [D1, D3, D4]])
def test_allowed_dates_equals_filtered_input(obs, subset):
    filt = obs[obs["date"].isin(subset)].reset_index(drop=True)

    def norm(df, keys):
        return df.sort_values(keys).reset_index(drop=True)

    pd.testing.assert_frame_equal(norm(spread_by_date(obs, allowed_dates=subset), ["date"]),
                                  norm(spread_by_date(filt), ["date"]), check_dtype=False)
    pd.testing.assert_frame_equal(norm(hit_rates(obs, allowed_dates=subset), ["horizon"]),
                                  norm(hit_rates(filt), ["horizon"]), check_dtype=False)
    pd.testing.assert_frame_equal(
        norm(rank_ic(obs, allowed_dates=subset, min_names=3), ["date"]),
        norm(rank_ic(filt, min_names=3), ["date"]), check_dtype=False)
    sp = spread_by_date(obs, allowed_dates=subset)
    assert sorted(sp["date"]) == sorted(subset)


def test_permutation_invariance():
    rng = np.random.default_rng(20261110)
    prices, panel = r1_prices(), r1_panel()
    prices_p = prices.iloc[rng.permutation(len(prices))].reset_index(drop=True)
    panel_p = panel.iloc[rng.permutation(len(panel))].reset_index(drop=True)
    dates_p = [D3, D1, D4, D2]

    def srt(df, keys):
        return df.sort_values(keys).reset_index(drop=True)

    f0 = forward_returns(prices, DATES, horizons=(3,))
    f1 = forward_returns(prices_p, dates_p, horizons=(3,))
    pd.testing.assert_frame_equal(srt(f0, ["date", "ticker", "horizon"]),
                                  srt(f1, ["date", "ticker", "horizon"]))
    f1p = f1.iloc[rng.permutation(len(f1))].reset_index(drop=True)
    o0 = sector_excess(f0, panel, min_sector=3)
    o1 = sector_excess(f1p, panel_p, min_sector=3)
    keys = ["date", "ticker", "horizon"]
    pd.testing.assert_frame_equal(srt(o0, keys), srt(o1[o0.columns], keys))
    o1p = o1.iloc[rng.permutation(len(o1))].reset_index(drop=True)
    pd.testing.assert_frame_equal(srt(spread_by_date(o0), ["date"]),
                                  srt(spread_by_date(o1p), ["date"]))
    pd.testing.assert_frame_equal(srt(hit_rates(o0), ["horizon"]),
                                  srt(hit_rates(o1p), ["horizon"]))
    pd.testing.assert_frame_equal(srt(rank_ic(o0, min_names=3), ["date"]),
                                  srt(rank_ic(o1p, min_names=3), ["date"]))
    lab = r1_labels()
    lab_p = lab.iloc[rng.permutation(len(lab))].reset_index(drop=True)
    pd.testing.assert_frame_equal(srt(turnover(lab), ["date"]), srt(turnover(lab_p), ["date"]))
    pb = b1_panel()
    pb_p = pb.iloc[rng.permutation(len(pb))].reset_index(drop=True)
    pd.testing.assert_frame_equal(srt(low_pb_baseline(pb), ["date", "ticker"]),
                                  srt(low_pb_baseline(pb_p), ["date", "ticker"]))


# ---------------------------------------------------------------------------
# 4.8 pass_bar (P1)
# ---------------------------------------------------------------------------

BASE = dict(yearly_spreads_12=[0.02, -0.01, 0.03, 0.0, 0.01, NAN], ic_mean=0.045, ic_t=2.4,
            holdout_ic_mean=0.01, holdout_spread=0.005)


def pb_(**kw):
    args = {**BASE, **kw}
    return pass_bar(args.pop("yearly_spreads_12"), args.pop("ic_mean"), args.pop("ic_t"),
                    **args)


def test_p1_base_pass():
    r = pb_()
    assert isinstance(r, PassResult)
    assert r.result == "PASS" and r.failed_legs == () and r.incomplete_legs == ()


@pytest.mark.parametrize("kw, result, failed, incomplete", [
    ({"ic_t": 2.0}, "FAIL", ("IC",), ()),
    ({"ic_mean": 0.03}, "FAIL", ("IC",), ()),
    ({"ic_t": NAN}, "FAIL", ("IC",), ()),                       # NaN t fails
    ({"holdout_ic_mean": None, "holdout_spread": None}, "INCOMPLETE", (), ("HOLDOUT",)),
    ({"holdout_ic_mean": None}, "INCOMPLETE", (), ("HOLDOUT",)),
    ({"holdout_spread": None}, "INCOMPLETE", (), ("HOLDOUT",)),
    ({"holdout_ic_mean": NAN}, "INCOMPLETE", (), ("HOLDOUT",)),
    ({"holdout_ic_mean": 0.0}, "FAIL", ("HOLDOUT",), ()),
    ({"holdout_spread": -0.001}, "FAIL", ("HOLDOUT",), ()),
    ({"yearly_spreads_12": [-0.01, 0.02], "holdout_ic_mean": None, "holdout_spread": None},
     "FAIL", ("SPREAD_YEARS",), ("HOLDOUT",)),
    ({"yearly_spreads_12": [NAN, NAN, NAN]}, "INCOMPLETE", (), ("SPREAD_YEARS", "HOLDOUT")),
    ({"yearly_spreads_12": []}, "INCOMPLETE", (), ("SPREAD_YEARS", "HOLDOUT")),
    ({"ic_mean": NAN}, "INCOMPLETE", (), ("IC", "HOLDOUT")),
    ({"ic_mean": NAN, "ic_t": NAN}, "INCOMPLETE", (), ("IC", "HOLDOUT")),
    # failed computable leg -> FAIL even with an incomplete leg (D3), legs in table order
    ({"ic_t": 1.0, "holdout_ic_mean": None}, "FAIL", ("IC",), ("HOLDOUT",)),
    ({"yearly_spreads_12": [-0.01, 0.02, -0.03], "ic_t": 1.0},
     "FAIL", ("SPREAD_YEARS", "IC", "HOLDOUT"), ()),
])
def test_p1_cases(kw, result, failed, incomplete):
    r = pb_(**kw)
    assert r.result == result
    assert tuple(r.failed_legs) == failed
    assert tuple(r.incomplete_legs) == incomplete


def test_p1_zero_year_is_not_positive():
    # [0.02, 0.0, 0.0] -> share 1/3 < 0.6 (0 is not > 0); mean 0.0067 > 0 matches holdout
    r = pb_(yearly_spreads_12=[0.02, 0.0, 0.0])
    assert r.result == "FAIL" and r.failed_legs == ("SPREAD_YEARS",)


def test_p1_holdout_zero_matches_only_zero():
    # yearly mean exactly 0.0 (sign 0, share 3/4): holdout_spread 0.0 matches, 0.001 does not
    r = pb_(yearly_spreads_12=[0.25, 0.25, 0.5, -1.0], holdout_spread=0.0)
    assert r.result == "PASS" and "HOLDOUT" not in r.failed_legs
    r = pb_(yearly_spreads_12=[0.25, 0.25, 0.5, -1.0], holdout_spread=0.001)
    assert "HOLDOUT" in r.failed_legs


def test_p1_holdout_ic_negative_fails():
    r = pb_(holdout_ic_mean=-0.02)
    assert r.result == "FAIL" and r.failed_legs == ("HOLDOUT",)


# ---------------------------------------------------------------------------
# 4.9 low_pb_baseline (B1)
# ---------------------------------------------------------------------------

BD = date(2024, 1, 31)


def b1_panel():
    rows = [("B1", 10, 10, True), ("B2", 20, 20, True), ("B3", 32, 20, True),
            ("B4", 24, 16, True), ("B5", 50, 20, True), ("B6", 12, 10, True),
            ("B7", 20, -5, True), ("B8", 5, 10, False)]
    out = [{"date": BD, "ticker": t, "sector": "Financials", "eligible": e, "price": float(p),
            "bvps": float(b)} for t, p, b, e in rows]
    for i, (p, b) in enumerate([(10, 10), (20, 10), (30, 10), (40, 10)]):
        out.append({"date": BD, "ticker": f"N{i}", "sector": "Energy", "eligible": True,
                    "price": float(p), "bvps": float(b)})
    return pd.DataFrame(out, columns=["date", "ticker", "sector", "eligible", "price", "bvps"])


def test_b1_baseline():
    out = low_pb_baseline(b1_panel())
    assert list(out.columns) == ["date", "ticker", "sector", "pb", "pct", "label", "score"]
    out = out.set_index("ticker")
    exp = {"B1": (1.0, 0.1, "BUY", 0.0), "B2": (1.0, 0.1, "BUY", 0.0),
           "B3": (1.6, 0.8, "SELL", -0.47000362924573563),
           "B4": (1.5, 0.6, "HOLD", -0.4054651081081644),
           "B5": (2.5, 1.0, "SELL", -0.9162907318741551),
           "B6": (1.2, 0.4, "HOLD", -0.1823215567939546)}
    for t, (pb, pct, label, score) in exp.items():
        r = out.loc[t]
        assert close(r["pb"], pb) and close(r["pct"], pct), t
        assert r["label"] == label, t
        assert close(r["score"], score), t
    assert out.loc["B7", "label"] == "NO_SIGNAL"
    assert isnan(out.loc["B7", "pct"]) and isnan(out.loc["B7", "score"])
    assert out.loc["B8", "label"] == "NOT_ELIGIBLE"
    assert isnan(out.loc["B8", "pct"]) and isnan(out.loc["B8", "score"])
    for t in ("N0", "N1", "N2", "N3"):  # Energy group of 4 < 5
        assert out.loc[t, "label"] == "NO_SIGNAL"
        assert isnan(out.loc[t, "pct"]) and isnan(out.loc[t, "score"])


def test_baseline_non_book_sector_no_signal():
    p = b1_panel()
    p["sector"] = "Information Technology"
    out = low_pb_baseline(p).set_index("ticker")
    assert (out.drop(index="B8")["label"] == "NO_SIGNAL").all()
    assert out.loc["B8", "label"] == "NOT_ELIGIBLE"
    assert out["score"].isna().all()


def test_baseline_min_sector_param():
    out = low_pb_baseline(b1_panel(), min_sector=4).set_index("ticker")
    # Energy n 4 >= 4: pb 1, 2, 3, 4 -> pct 0, 1/3, 2/3, 1
    assert out.loc["N0", "label"] == "BUY" and out.loc["N3", "label"] == "SELL"
    assert out.loc["N1", "label"] == "HOLD" and close(out.loc["N1", "pct"], 1 / 3)
    assert close(out.loc["N3", "score"], -math.log(4.0))


# ---------------------------------------------------------------------------
# §6 SIC -> GICS sector, thresholds_hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sic, sector", [
    (1311, "Energy"), (2834, "Health Care"), (2911, "Energy"),
    (3674, "Information Technology"), (3714, "Consumer Discretionary"), (4911, "Utilities"),
    (6022, "Financials"), (6798, "Real Estate"), (5411, "Consumer Staples"),
    (5331, "Consumer Discretionary"), (7372, "Information Technology"), (4512, "Industrials"),
    (9995, None),
])
def test_sic_to_sector_spec_checks(sic, sector):
    assert sic_to_sector(sic) == sector


@pytest.mark.parametrize("sic, sector", [
    (None, None),
    (100, None),                       # agriculture: not in the table
    (6799, "Financials"),              # 6500-6599 / 6798 do not match, 6000-6799 does
    (6512, "Real Estate"),
    (7011, "Consumer Discretionary"),  # 7000-7099 (right cell of row 5) before 7000-8999
    (8062, "Health Care"),             # 8000-8099 before Industrials 7000-8999
    (8711, "Industrials"),
    (2851, "Materials"),
    (2844, "Consumer Staples"),        # 2840-2844 before Materials 2800-2899
    (5912, "Consumer Staples"),        # before Consumer Discretionary 5200-5999
    (5122, "Health Care"),             # before Industrials 5000-5199
    (3845, "Health Care"),             # 3841-3851 before IT 3800-3840/3852-3899
    (3812, "Information Technology"),
    (4813, "Communication Services"),
    (3751, "Consumer Discretionary"),
])
def test_sic_to_sector_table_order(sic, sector):
    assert sic_to_sector(sic) == sector


def test_thresholds_hash_stable_hex():
    # Spec does not give a signature; the hash is over the live constants and liquidity.yaml,
    # so the no-argument call is the literal reading.
    h1, h2 = thresholds_hash(), thresholds_hash()
    assert isinstance(h1, str) and re.fullmatch(r"[0-9a-f]{64}", h1)
    assert h1 == h2
