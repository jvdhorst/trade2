"""Shared fixture DB for the per-company fit spec tests (docs/models/per_company_fit.md v0.1.0).

Not a test module (no `test_` prefix). Builds a small synthetic backtest DuckDB with only what the
spec says `backtest fit` reads: `bt_rows` (universe US, the nine label columns), `bt_returns`
(horizon 1, plus 3/6/12 as a real run would write), `bt_label_versions`, and `prices_daily`.
Fixtures only: no network, no real backtest DB.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from valuation.backtest import engine as bte
from valuation.backtest import metrics as m
from valuation.backtest import runner
from valuation.store import db

TODAY = date(2026, 11, 10)
LOCK = date(2023, 10, 31)
MODELS = ("A", "B", "C", "H", "J", "G", "D", "E", "F")
LABEL_COLUMNS = {k: f"{k.lower()}_signal" for k in MODELS}
# §2.1: new bt_rows columns (name -> type)
NEW_ROW_TYPES = {"j_signal": "VARCHAR", "j_pct": "DOUBLE", "g_signal": "VARCHAR", "g_z": "DOUBLE",
                 "d_signal": "VARCHAR", "d_mos": "DOUBLE", "e_signal": "VARCHAR",
                 "e_f_score": "DOUBLE", "f_signal": "VARCHAR", "f_acc_pct": "DOUBLE"}
LABEL_VERSIONS_DDL = """CREATE TABLE IF NOT EXISTS bt_label_versions (universe VARCHAR,
    model VARCHAR, model_version VARCHAR, PRIMARY KEY (universe, model))"""
TICKERS = ("AAA", "BBB", "CCC")


def live_versions() -> dict[str, str]:
    from valuation.models import (
        accruals,
        altman_z,
        composite,
        dividend_sustainability,
        ebit_yield_roc,
        peer_regression,
        piotroski,
        residual_income,
        shareholder_yield,
    )

    mods = {"A": residual_income, "B": peer_regression, "C": composite, "H": shareholder_yield,
            "J": ebit_yield_roc, "G": altman_z, "D": dividend_sustainability, "E": piotroski,
            "F": accruals}
    return {k: v.MODEL_VERSION for k, v in mods.items()}


def weekdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def all_dates() -> list[date]:
    return bte.rebalance_dates(bte.BT_START, TODAY)  # 154 month-ends 2014-01-31 .. 2026-10-31


def in_sample() -> list[date]:
    return bte.in_sample_dates(all_dates())  # 118, .. 2023-10-31


def holdout() -> list[date]:
    return bte.holdout_dates(all_dates())  # 36, 2023-11-30 .. 2026-10-31


def make_prices(perturb: bool = False) -> pd.DataFrame:
    sess = weekdays(date(2013, 12, 2), date(2026, 11, 9))
    rng = np.random.RandomState(11)
    rows = []
    for t in (*TICKERS, "ZZZ"):
        lr = rng.normal(0.0004, 0.018, len(sess))
        lvl = 40.0 * np.exp(np.cumsum(lr))
        if perturb:  # change every price after the lock
            k = np.random.RandomState(99).uniform(0.5, 1.5, len(sess))
            lvl = np.where(np.array([s > LOCK for s in sess]), lvl * k, lvl)
        rows += [(t, s, float(v)) for s, v in zip(sess, np.round(lvl, 6))]
    return pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])


def _labels(prices: pd.DataFrame, dates: list[date], seed: int) -> list[dict]:
    """Deterministic labels. AAA·E is 'clairvoyant' (SELL before a fall) so that some pairs pass;
    the rest are random. G never says BUY (§3.1)."""
    rng = np.random.RandomState(seed)
    h1 = m.forward_returns(prices, dates, horizons=(1,)).set_index(["date", "ticker"])["ret"]
    pool = ["BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE", None]
    recs = []
    for d in dates:
        for t in TICKERS:
            r = {"universe": "US", "date": d, "ticker": t, "market": "US",
                 "sector": "Industrials", "eligible": True}
            for k in MODELS:
                lab = pool[rng.choice(6, p=[.25, .35, .2, .1, .05, .05])]
                if k == "G" and lab == "BUY":
                    lab = "HOLD"
                if t == "AAA" and k == "E":
                    ret = h1.get((d, t), np.nan)
                    lab = "SELL" if (ret == ret and ret < -0.01) else "HOLD"
                r[LABEL_COLUMNS[k]] = lab
            recs.append(r)
    return recs


def build_fit_db(path, *, with_holdout: bool = False, perturb: bool = False,
                 label_versions: dict | None | bool = True, h1: bool = True,
                 drop_date: date | None = None):
    """Create the fixture backtest DB at `path` and return an open connection.

    with_holdout: also store bt_rows / bt_returns rows for the 36 holdout month-ends (as a
    later holdout run would) plus a ticker ZZZ that only has holdout rows.
    perturb: every price after 2023-10-31 is changed (returns recomputed from those prices).
    """
    con = db.connect(path)
    for ddl in [*bte.BT_DDL, runner.RETURNS_DDL]:
        con.execute(ddl)
    for col, typ in NEW_ROW_TYPES.items():
        con.execute(f"ALTER TABLE bt_rows ADD COLUMN IF NOT EXISTS {col} {typ}")
    prices = make_prices(perturb)
    pdf = prices.assign(close=prices["adj_close"], volume=1e6, source="fixture")[
        ["ticker", "date", "close", "adj_close", "volume", "source"]]
    con.register("_p", pdf)
    con.execute("INSERT INTO prices_daily SELECT * FROM _p")
    con.unregister("_p")
    base = make_prices(False)  # labels never depend on the perturbation
    dates = in_sample()
    recs = _labels(base, dates, seed=5)
    if with_holdout:
        ho = holdout()
        hrecs = _labels(prices, ho, seed=77)
        hrecs += [{"universe": "US", "date": d, "ticker": "ZZZ", "market": "US",
                   "sector": "Industrials", "eligible": True,
                   **{LABEL_COLUMNS[k]: ("SELL" if k != "G" else "HOLD") for k in MODELS}}
                  for d in ho]
        recs += hrecs
        dates = dates + ho
    if drop_date is not None:
        recs = [r for r in recs if r["date"] != drop_date]
    rows = pd.DataFrame(recs)
    con.register("_r", rows)
    con.execute(f"INSERT INTO bt_rows ({', '.join(rows.columns)}) SELECT * FROM _r")
    con.unregister("_r")
    hs = (1, 3, 6, 12) if h1 else (3, 6, 12)
    tick = prices if with_holdout else prices[prices["ticker"] != "ZZZ"]
    fr = m.forward_returns(tick, dates, horizons=hs)
    con.register("_f", fr)
    con.execute("INSERT INTO bt_returns SELECT date, ticker, horizon, t0, t1, ret, flag FROM _f")
    con.unregister("_f")
    if label_versions is not False and label_versions is not None:
        con.execute(LABEL_VERSIONS_DDL)
        lv = live_versions() if label_versions is True else label_versions
        con.executemany("INSERT INTO bt_label_versions VALUES (?,?,?)",
                        [["US", k, v] for k, v in lv.items()])
    return con
