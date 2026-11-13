"""Two-mode harness tests (docs/models/per_company_fit.md v0.1.0 §2, §10, §13 "Reproduction";
docs/models/backtest.md v0.2.0 unchanged).

Part 1 (whole-market regression) builds a small deterministic fixture backtest DB (labels and
prices for models A/B/C/H, forward returns h = 3/6/12) and runs the EXISTING whole-market
pipeline on it: the public metric functions (`report.model_panel`, `metrics.sector_excess`,
`spread_by_date`, `yearly_spread`, `hit_rates`, `rank_ic`, `newey_west_t`) and the report entry
point (`valuation backtest report`). The expected numbers below were computed on `main` code
before the two-mode change (commit 977adcb) and are frozen as literals: the refactor must
reproduce them exactly (§2.2). These tests pass now.

Part 2 covers the §2.1 / §2.2 / §10 additions (h = 1 rows ignored by the report, J/G/D/E/F
columns in bt_rows, bt_label_versions, part stamp, `--extra-models`, the `fit` action). Those
fail until SW implements them.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import shutil
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from valuation.backtest import data as btd
from valuation.backtest import engine as bte
from valuation.backtest import metrics as m
from valuation.backtest import report as rep
from valuation.backtest import runner
from valuation.store import db

REPO_ROOT = Path(__file__).resolve().parents[2]
TOL = 1e-9
TODAY = "2026-11-10"
SIG = ["BUY", "HOLD", "SELL", "NO_SIGNAL"]
N_TICKERS, N_DATES = 30, 30
NEW_ROW_TYPES = {"j_signal": "VARCHAR", "j_pct": "DOUBLE", "g_signal": "VARCHAR", "g_z": "DOUBLE",
                 "d_signal": "VARCHAR", "d_mos": "DOUBLE", "e_signal": "VARCHAR",
                 "e_f_score": "DOUBLE", "f_signal": "VARCHAR", "f_acc_pct": "DOUBLE"}
EXTRA = ("J", "G", "D", "E", "F")
FROZEN_MODELS = ("A", "B", "C", "H")


def close(a, b, tol=TOL):
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return a is None or pd.isna(a)
    return a is not None and not pd.isna(a) and abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
# Fixture DB (deterministic: numpy legacy RandomState with fixed seeds)
# ---------------------------------------------------------------------------


def month_ends(first: date, n: int) -> list[date]:
    out, d = [], first
    for _ in range(n):
        out.append(d)
        d = bte.add_months(d, 1)
    return out


DATES = month_ends(date(2014, 1, 31), N_DATES)  # 2014-01-31 .. 2016-06-30


def build_db(path, *, h1: bool = False, new_cols: bool = False):
    """The pre-change stored rows. h1 / new_cols add what a post-change `backtest run` stores
    on top (h = 1 returns, J/G/D/E/F columns) without touching anything else."""
    rng = np.random.RandomState(20261113)
    con = db.connect(path)
    for ddl in [*bte.BT_DDL, btd.COVERAGE_DDL, runner.UNIVERSE_DDL, runner.RETURNS_DDL]:
        con.execute(ddl)
    tickers = [f"T{i:02d}" for i in range(N_TICKERS)]
    sectors = {t: ("Industrials" if i < N_TICKERS // 2 else "Information Technology")
               for i, t in enumerate(tickers)}
    for t in tickers:
        con.execute("INSERT INTO securities (ticker, exchange, name, currency, gics_sector, "
                    "gics_sub_industry) VALUES (?,?,?,?,?,?)",
                    [t, "US", t, "USD", sectors[t], "Sub " + sectors[t][:3]])
    sess, d = [], date(2014, 1, 1)
    while d <= date(2017, 9, 29):
        if d.weekday() < 5:
            sess.append(d)
        d += timedelta(days=1)
    px = []
    for t in tickers:
        lr = rng.normal(0.0003, 0.015, len(sess))
        lvl = np.round(50 * np.exp(np.cumsum(lr)), 4)
        px += [(t, s, float(v), float(v), 1e6, "fixture") for s, v in zip(sess, lvl)]
    pdf = pd.DataFrame(px, columns=["ticker", "date", "close", "adj_close", "volume", "source"])
    con.register("_p", pdf)
    con.execute("INSERT INTO prices_daily SELECT * FROM _p")
    con.unregister("_p")
    recs = []
    for d in DATES:
        for t in tickers:
            elig = rng.rand() < 0.9
            labs = ([SIG[k] for k in rng.choice(4, 4, p=[.3, .35, .25, .1])] if elig
                    else ["NOT_ELIGIBLE"] * 4)
            cl = [SIG[k] for k in rng.choice(3, 4)] if elig else [None] * 4
            v = np.round(rng.normal(0, 1, 12), 6)
            flags = ",".join(f for f, p in (("ROE_CLIPPED", .2), ("NEG_ROE_UNFITTED", .15))
                             if rng.rand() < p)
            recs.append(dict(
                universe="US", date=d, ticker=t, market="US", sector=sectors[t],
                eligible=bool(elig), elig_fail_reason=None if elig else "E2",
                price=float(20 + 10 * abs(v[0])), bvps=float(5 + abs(v[1])),
                pb=float((20 + 10 * abs(v[0])) / (5 + abs(v[1]))), roe0=float(v[2] / 10),
                roe_raw=float(v[2] / 10), a_signal=labs[0], a_reason=None, a_mos=float(v[3]),
                b_signal=labs[1], b_reason=None, b_residual=float(v[4]), b_resid_pct=float(v[5]),
                b_upside=float(v[6]), b_n_fit=12, b_r2=0.5, flags=flags, c_signal=labs[2],
                c_reason=None, c_score=float(v[7]), c_value=float(v[8]), c_quality=float(v[9]),
                c_momentum=float(v[10]), c_risk=float(v[11]), c_v_label=cl[0], c_q_label=cl[1],
                c_m_label=cl[2], c_r_label=cl[3], h_signal=labs[3], h_score=float(v[3] + v[4]),
                h_dy=float(abs(v[5]) / 50), h_bby=float(v[6] / 50)))
    rows = pd.DataFrame(recs)
    if new_cols:  # separate RNG: the pre-change columns are bit-identical
        rng2 = np.random.RandomState(4242)
        for col, typ in NEW_ROW_TYPES.items():
            con.execute(f"ALTER TABLE bt_rows ADD COLUMN IF NOT EXISTS {col} {typ}")
        for k in ("j", "g", "d", "e", "f"):
            pool = ["HOLD", "SELL", "NO_SIGNAL"] if k == "g" else SIG
            rows[f"{k}_signal"] = [pool[i] for i in rng2.choice(len(pool), len(rows))]
        rows["j_pct"] = np.round(rng2.uniform(0, 1, len(rows)), 6)
        rows["g_z"] = np.round(rng2.normal(2, 1, len(rows)), 6)
        rows["d_mos"] = np.round(rng2.normal(0, 0.3, len(rows)), 6)
        rows["e_f_score"] = rng2.randint(0, 10, len(rows)).astype(float)
        rows["f_acc_pct"] = np.round(rng2.uniform(0, 1, len(rows)), 6)
    con.register("_r", rows)
    con.execute(f"INSERT INTO bt_rows ({', '.join(rows.columns)}) SELECT * FROM _r")
    con.unregister("_r")
    con.executemany("INSERT INTO bt_universe VALUES (?,?,?,?)",
                    [["US", d, t, True] for d in DATES for t in tickers])
    con.executemany("INSERT INTO bt_ticker_coverage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [[t, "US", "current", True, None, sess[0], sess[-1], None, None, sectors[t],
                      "universe", 0] for t in tickers])
    prices = pdf[["ticker", "date", "adj_close"]]
    fr = m.forward_returns(prices, DATES, horizons=(3, 6, 12))
    if h1:
        fr = pd.concat([m.forward_returns(prices, DATES, horizons=(1,)), fr], ignore_index=True)
    con.register("_f", fr)
    con.execute("INSERT INTO bt_returns SELECT date, ticker, horizon, t0, t1, ret, flag FROM _f")
    con.unregister("_f")
    return con


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    p = tmp_path_factory.mktemp("two_mode") / "base.duckdb"
    build_db(p).close()
    return p


def copy_db(src: Path, tmp_path: Path, name: str) -> Path:
    dst = tmp_path / name
    shutil.copy(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Canonical forms of the report tables (run_id dropped; floats rounded to 1e-9)
# ---------------------------------------------------------------------------


def _v(x):
    if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA:
        return "NULL"
    if isinstance(x, float | np.floating):
        r = round(float(x), 9)
        return repr(0.0 if r == 0 else r)
    if hasattr(x, "isoformat"):
        return x.isoformat()[:10]
    return str(x)


def canon(con, table: str, cols: list[str], where: str = "") -> list[str]:
    rows = con.execute(f"SELECT {', '.join(cols)} FROM {table} {where}").fetchall()
    return sorted("|".join(_v(x) for x in r) for r in rows)


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


METRIC_COLS = ["model", "scope", "horizon", "metric", "key", "value"]
DIAG_COLS = ["question", "model", "bucket", "horizon", "n", "mean_excess", "hit_rate", "mean_ic",
             "n_ic_dates", "mean_spread", "n_spread_dates"]
SIGNAL_COLS = ["date", "ticker", "model", "label", "score", "sector", "eligible", "pb", "flags"]
WHERE_FROZEN = "WHERE model IN ('A','B','C','H','A_BOOK','BASELINE_PB','C_V','C_Q','C_M','C_R'," \
               "'H_BBY','H_DY')"


def run_report(path: Path, out: Path, monkeypatch, extra=()):
    from valuation.cli import main

    monkeypatch.chdir(REPO_ROOT)  # universe/benchmarks.csv is read relative to the repo
    return main(["backtest", "report", "--bt-db", str(path), "--today", TODAY, "--out", str(out),
                 *extra])


def report_tables(path: Path) -> dict:
    con = duckdb.connect(str(path))
    run = con.execute("SELECT run_id FROM bt_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0]
    w = f"WHERE run_id = '{run}'"
    out = {"metrics": canon(con, "bt_metrics", METRIC_COLS, w),
           "diagnostics": canon(con, "bt_diagnostics", DIAG_COLS, w),
           "signals": canon(con, "bt_signals", SIGNAL_COLS, w),
           "metrics_frozen_models": canon(con, "bt_metrics", METRIC_COLS,
                                          w + " AND " + WHERE_FROZEN[6:]),
           "signals_frozen_models": canon(con, "bt_signals", SIGNAL_COLS,
                                          w + " AND " + WHERE_FROZEN[6:])}
    out["raw_metrics"] = con.execute(
        f"SELECT model, scope, horizon, metric, key, value FROM bt_metrics {w}").df()
    out["raw_signals"] = con.execute(f"SELECT * FROM bt_signals {w}").df()
    con.close()
    return out


@pytest.fixture(scope="module")
def base_report(base_db, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        d = tmp_path_factory.mktemp("rep_base")
        p = copy_db(base_db, d, "bt.duckdb")
        assert run_report(p, d / "out", mp) == 0
        return report_tables(p)
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# Frozen literals (computed on main before the change; see module docstring)
# ---------------------------------------------------------------------------

# report entry point: sha256 of the canonical tables, and their row counts
FROZEN_DIGEST = {
    "metrics": ("455e79317bb210561210b545b3a745bd586025d89ba7e8487637edb2b9f83c75", 408),
    "diagnostics": ("408ec6fb74e5ff83082dd3850e04a86afc8e907a879f0c444e6c03a9ec33b705", 36),
    "signals": ("83794be03abe88309aefd132b0a165acf4749df807a28c8236c6ae50b9c10f73", 10800),
}
# bt_metrics in-sample values, models A, B, C, H (key '' rows): (model, horizon, metric) -> value
FROZEN_METRICS = {
    ("A", 3, "buy_hit_rate"): 0.4980392156862745,
    ("A", 3, "mean_ic"): 0.012211611255089513,
    ("A", 3, "mean_spread"): -0.004561271021706971,
    ("A", 3, "n_buy"): 255.0,
    ("A", 3, "n_ic_dates"): 30.0,
    ("A", 3, "n_sell"): 181.0,
    ("A", 3, "nw_t"): 0.46383647213657075,
    ("A", 3, "sell_hit_rate"): 0.5248618784530387,
    ("A", 6, "buy_hit_rate"): 0.48627450980392156,
    ("A", 6, "mean_ic"): 0.02872958120784208,
    ("A", 6, "mean_spread"): 0.0072834094657453,
    ("A", 6, "n_buy"): 255.0,
    ("A", 6, "n_ic_dates"): 30.0,
    ("A", 6, "n_sell"): 181.0,
    ("A", 6, "nw_t"): 0.8863565462755542,
    ("A", 6, "sell_hit_rate"): 0.4972375690607735,
    ("A", 12, "buy_hit_rate"): 0.4980392156862745,
    ("A", 12, "mean_ic"): 0.03147697712915105,
    ("A", 12, "mean_spread"): 0.03263441140202541,
    ("A", 12, "n_buy"): 255.0,
    ("A", 12, "n_ic_dates"): 30.0,
    ("A", 12, "n_sell"): 181.0,
    ("A", 12, "nw_t"): 1.6890596249952525,
    ("A", 12, "sell_hit_rate"): 0.48066298342541436,
    ("B", 3, "buy_hit_rate"): 0.4959016393442623,
    ("B", 3, "mean_ic"): 0.03025609715312232,
    ("B", 3, "mean_spread"): -0.01127766772228415,
    ("B", 3, "n_buy"): 244.0,
    ("B", 3, "n_ic_dates"): 30.0,
    ("B", 3, "n_sell"): 209.0,
    ("B", 3, "nw_t"): 0.7189650498500028,
    ("B", 3, "sell_hit_rate"): 0.4880382775119617,
    ("B", 6, "buy_hit_rate"): 0.4959016393442623,
    ("B", 6, "mean_ic"): 0.01493989238153998,
    ("B", 6, "mean_spread"): 0.009214847800056011,
    ("B", 6, "n_buy"): 244.0,
    ("B", 6, "n_ic_dates"): 30.0,
    ("B", 6, "n_sell"): 209.0,
    ("B", 6, "nw_t"): 0.3840934399515912,
    ("B", 6, "sell_hit_rate"): 0.46411483253588515,
    ("B", 12, "buy_hit_rate"): 0.4918032786885246,
    ("B", 12, "mean_ic"): 0.004086182315015269,
    ("B", 12, "mean_spread"): 0.029612598264458334,
    ("B", 12, "n_buy"): 244.0,
    ("B", 12, "n_ic_dates"): 30.0,
    ("B", 12, "n_sell"): 209.0,
    ("B", 12, "nw_t"): 0.17978544761038345,
    ("B", 12, "sell_hit_rate"): 0.5119617224880383,
    ("C", 3, "buy_hit_rate"): 0.4860557768924303,
    ("C", 3, "mean_ic"): 0.03852841372610623,
    ("C", 3, "mean_spread"): -0.010539401049173955,
    ("C", 3, "n_buy"): 251.0,
    ("C", 3, "n_ic_dates"): 28.0,
    ("C", 3, "n_sell"): 192.0,
    ("C", 3, "nw_t"): 0.9266098348967852,
    ("C", 3, "sell_hit_rate"): 0.4635416666666667,
    ("C", 6, "buy_hit_rate"): 0.4900398406374502,
    ("C", 6, "mean_ic"): 0.0035154918340769004,
    ("C", 6, "mean_spread"): -0.027218628334436636,
    ("C", 6, "n_buy"): 251.0,
    ("C", 6, "n_ic_dates"): 28.0,
    ("C", 6, "n_sell"): 192.0,
    ("C", 6, "nw_t"): 0.0930399581131001,
    ("C", 6, "sell_hit_rate"): 0.390625,
    ("C", 12, "buy_hit_rate"): 0.46613545816733065,
    ("C", 12, "mean_ic"): 0.000311046060473971,
    ("C", 12, "mean_spread"): -0.0823630602894006,
    ("C", 12, "n_buy"): 251.0,
    ("C", 12, "n_ic_dates"): 28.0,
    ("C", 12, "n_sell"): 192.0,
    ("C", 12, "nw_t"): 0.012057749513846357,
    ("C", 12, "sell_hit_rate"): 0.4375,
    ("H", 3, "buy_hit_rate"): 0.4721030042918455,
    ("H", 3, "mean_ic"): -0.007266464614290702,
    ("H", 3, "mean_spread"): -0.007365246063855074,
    ("H", 3, "n_buy"): 233.0,
    ("H", 3, "n_ic_dates"): 30.0,
    ("H", 3, "n_sell"): 208.0,
    ("H", 3, "nw_t"): -0.20811921698669034,
    ("H", 3, "sell_hit_rate"): 0.46634615384615385,
    ("H", 6, "buy_hit_rate"): 0.5064377682403434,
    ("H", 6, "mean_ic"): 0.020467046640959666,
    ("H", 6, "mean_spread"): 0.013065103311534402,
    ("H", 6, "n_buy"): 233.0,
    ("H", 6, "n_ic_dates"): 30.0,
    ("H", 6, "n_sell"): 208.0,
    ("H", 6, "nw_t"): 0.4727504178046596,
    ("H", 6, "sell_hit_rate"): 0.5,
    ("H", 12, "buy_hit_rate"): 0.5150214592274678,
    ("H", 12, "mean_ic"): 0.011797212449386368,
    ("H", 12, "mean_spread"): -0.0014219897583736666,
    ("H", 12, "n_buy"): 233.0,
    ("H", 12, "n_ic_dates"): 30.0,
    ("H", 12, "n_sell"): 208.0,
    ("H", 12, "nw_t"): 0.4729558620944707,
    ("H", 12, "sell_hit_rate"): 0.4855769230769231,
}
# yearly spreads (model, horizon, year) -> value
FROZEN_YEARLY = {
    ("A", 3, "2014"): 0.003721764476406339,
    ("A", 3, "2015"): -0.008452252978850957,
    ("A", 3, "2016"): -0.013345378103645625,
    ("A", 6, "2014"): 0.035996421846619685,
    ("A", 6, "2015"): -0.016131300281238506,
    ("A", 6, "2016"): -0.0033131958020358545,
    ("A", 12, "2014"): 0.07061852730023488,
    ("A", 12, "2015"): -0.016633144587571858,
    ("A", 12, "2016"): 0.055201291584801006,
    ("B", 3, "2014"): -0.009999468236991526,
    ("B", 3, "2015"): -0.0060444222072289664,
    ("B", 3, "2016"): -0.02430055772297977,
    ("B", 6, "2014"): 0.013684209166597072,
    ("B", 6, "2015"): 0.03591448156886675,
    ("B", 6, "2016"): -0.05312314247064759,
    ("B", 12, "2014"): 0.025457427327882757,
    ("B", 12, "2015"): 0.05560595258982618,
    ("B", 12, "2016"): -0.014063768513126199,
    ("C", 3, "2014"): -0.015492256826389543,
    ("C", 3, "2015"): -0.014549428453348022,
    ("C", 3, "2016"): 0.007386365313605372,
    ("C", 6, "2014"): -0.04986961636488673,
    ("C", 6, "2015"): -0.025532732341848646,
    ("C", 6, "2016"): 0.014711555741287533,
    ("C", 12, "2014"): -0.15049202316685775,
    ("C", 12, "2015"): -0.07826085137079146,
    ("C", 12, "2016"): 0.045690447628295355,
    ("H", 3, "2014"): -0.0059832690616751704,
    ("H", 3, "2015"): -0.013189136458445173,
    ("H", 3, "2016"): 0.0015185807209653248,
    ("H", 6, "2014"): 0.009465263999200004,
    ("H", 6, "2015"): -0.0020613169241151933,
    ("H", 6, "2016"): 0.050517622407502384,
    ("H", 12, "2014"): -6.880042566252224e-05,
    ("H", 12, "2015"): -0.03017060841298441,
    ("H", 12, "2016"): 0.05336886888542553,
}
# pass_result keys per model
FROZEN_PASS = {
    "A": "FAIL|failed=IC|incomplete=HOLDOUT",
    "B": "FAIL|failed=IC|incomplete=HOLDOUT",
    "C": "FAIL|failed=SPREAD_YEARS,IC|incomplete=HOLDOUT",
    "H": "FAIL|failed=SPREAD_YEARS,IC|incomplete=HOLDOUT",
}
# public metric functions on the fixture: (model, horizon, quantity) -> value
FROZEN_FUNCS = {
    ('A', 0, 'label_turnover'): 0.6612534220639806,
    ('A', 12, 'buy_excess_sum'): 1.0587798632847014,
    ('A', 12, 'buy_hit_rate'): 0.4980392156862745,
    ('A', 12, 'excess_sum'): 12.721565205526941,
    ('A', 12, 'ic_mean'): 0.03147697712915105,
    ('A', 12, 'n_ic'): 30,
    ('A', 12, 'n_spread'): 30,
    ('A', 12, 'nw_t'): 1.6890596249952525,
    ('A', 12, 'sell_hit_rate'): 0.48066298342541436,
    ('A', 12, 'spread_sum'): 0.9790323420607622,
    ('A', 12, 'yearly_sum'): 0.10918667429746404,
    ('A', 3, 'buy_excess_sum'): -0.014223617886118953,
    ('A', 3, 'buy_hit_rate'): 0.4980392156862745,
    ('A', 3, 'excess_sum'): 1.4456391391564585,
    ('A', 3, 'ic_mean'): 0.012211611255089513,
    ('A', 3, 'n_ic'): 30,
    ('A', 3, 'n_spread'): 30,
    ('A', 3, 'nw_t'): 0.46383647213657075,
    ('A', 3, 'sell_hit_rate'): 0.5248618784530387,
    ('A', 3, 'spread_sum'): -0.13683813065120912,
    ('A', 3, 'yearly_sum'): -0.018075866606090243,
    ('A', 6, 'buy_excess_sum'): 0.2392181266118646,
    ('A', 6, 'buy_hit_rate'): 0.48627450980392156,
    ('A', 6, 'excess_sum'): 7.346502731397612,
    ('A', 6, 'ic_mean'): 0.02872958120784208,
    ('A', 6, 'n_ic'): 30,
    ('A', 6, 'n_spread'): 30,
    ('A', 6, 'nw_t'): 0.8863565462755542,
    ('A', 6, 'sell_hit_rate'): 0.4972375690607735,
    ('A', 6, 'spread_sum'): 0.21850228397235902,
    ('A', 6, 'yearly_sum'): 0.016551925763345323,
    ('B', 0, 'label_turnover'): 0.6268498138924381,
    ('B', 12, 'buy_excess_sum'): 1.1031294941158283,
    ('B', 12, 'buy_hit_rate'): 0.4918032786885246,
    ('B', 12, 'excess_sum'): 12.721565205526941,
    ('B', 12, 'ic_mean'): 0.004086182315015269,
    ('B', 12, 'n_ic'): 30,
    ('B', 12, 'n_spread'): 30,
    ('B', 12, 'nw_t'): 0.17978544761038345,
    ('B', 12, 'sell_hit_rate'): 0.5119617224880383,
    ('B', 12, 'spread_sum'): 0.88837794793375,
    ('B', 12, 'yearly_sum'): 0.06699961140458274,
    ('B', 3, 'buy_excess_sum'): 0.1494153246121502,
    ('B', 3, 'buy_hit_rate'): 0.4959016393442623,
    ('B', 3, 'excess_sum'): 1.4456391391564585,
    ('B', 3, 'ic_mean'): 0.03025609715312232,
    ('B', 3, 'n_ic'): 30,
    ('B', 3, 'n_spread'): 30,
    ('B', 3, 'nw_t'): 0.7189650498500028,
    ('B', 3, 'sell_hit_rate'): 0.4880382775119617,
    ('B', 3, 'spread_sum'): -0.33833003166852454,
    ('B', 3, 'yearly_sum'): -0.04034444816720026,
    ('B', 6, 'buy_excess_sum'): 0.6368102983687302,
    ('B', 6, 'buy_hit_rate'): 0.4959016393442623,
    ('B', 6, 'excess_sum'): 7.346502731397612,
    ('B', 6, 'ic_mean'): 0.01493989238153998,
    ('B', 6, 'n_ic'): 30,
    ('B', 6, 'n_spread'): 30,
    ('B', 6, 'nw_t'): 0.3840934399515912,
    ('B', 6, 'sell_hit_rate'): 0.46411483253588515,
    ('B', 6, 'spread_sum'): 0.2764454340016803,
    ('B', 6, 'yearly_sum'): -0.003524451735183766,
    ('C', 0, 'label_turnover'): 0.6528400386381226,
    ('C', 12, 'buy_excess_sum'): -0.26931428750973385,
    ('C', 12, 'buy_hit_rate'): 0.46613545816733065,
    ('C', 12, 'excess_sum'): 12.721565205526941,
    ('C', 12, 'ic_mean'): 0.000311046060473971,
    ('C', 12, 'n_ic'): 28,
    ('C', 12, 'n_spread'): 30,
    ('C', 12, 'nw_t'): 0.012057749513846357,
    ('C', 12, 'sell_hit_rate'): 0.4375,
    ('C', 12, 'spread_sum'): -2.470891808682018,
    ('C', 12, 'yearly_sum'): -0.18306242690935384,
    ('C', 3, 'buy_excess_sum'): -0.02794418653340272,
    ('C', 3, 'buy_hit_rate'): 0.4860557768924303,
    ('C', 3, 'excess_sum'): 1.4456391391564585,
    ('C', 3, 'ic_mean'): 0.03852841372610623,
    ('C', 3, 'n_ic'): 28,
    ('C', 3, 'n_spread'): 30,
    ('C', 3, 'nw_t'): 0.9266098348967852,
    ('C', 3, 'sell_hit_rate'): 0.4635416666666667,
    ('C', 3, 'spread_sum'): -0.3161820314752187,
    ('C', 3, 'yearly_sum'): -0.022655319966132192,
    ('C', 6, 'buy_excess_sum'): 0.09393843912638389,
    ('C', 6, 'buy_hit_rate'): 0.4900398406374502,
    ('C', 6, 'excess_sum'): 7.346502731397612,
    ('C', 6, 'ic_mean'): 0.0035154918340769004,
    ('C', 6, 'n_ic'): 28,
    ('C', 6, 'n_spread'): 30,
    ('C', 6, 'nw_t'): 0.0930399581131001,
    ('C', 6, 'sell_hit_rate'): 0.390625,
    ('C', 6, 'spread_sum'): -0.8165588500330991,
    ('C', 6, 'yearly_sum'): -0.06069079296544784,
    ('H', 0, 'label_turnover'): 0.6724533612598096,
    ('H', 12, 'buy_excess_sum'): 0.5485723722205561,
    ('H', 12, 'buy_hit_rate'): 0.5150214592274678,
    ('H', 12, 'excess_sum'): 12.721565205526941,
    ('H', 12, 'ic_mean'): 0.011797212449386368,
    ('H', 12, 'n_ic'): 30,
    ('H', 12, 'n_spread'): 30,
    ('H', 12, 'nw_t'): 0.4729558620944707,
    ('H', 12, 'sell_hit_rate'): 0.4855769230769231,
    ('H', 12, 'spread_sum'): -0.042659692751209996,
    ('H', 12, 'yearly_sum'): 0.0231294600467786,
    ('H', 3, 'buy_excess_sum'): -0.12275206482819287,
    ('H', 3, 'buy_hit_rate'): 0.4721030042918455,
    ('H', 3, 'excess_sum'): 1.4456391391564585,
    ('H', 3, 'ic_mean'): -0.007266464614290702,
    ('H', 3, 'n_ic'): 30,
    ('H', 3, 'n_spread'): 30,
    ('H', 3, 'nw_t'): -0.20811921698669034,
    ('H', 3, 'sell_hit_rate'): 0.46634615384615385,
    ('H', 3, 'spread_sum'): -0.2209573819156522,
    ('H', 3, 'yearly_sum'): -0.01765382479915502,
    ('H', 6, 'buy_excess_sum'): 0.39972084000277613,
    ('H', 6, 'buy_hit_rate'): 0.5064377682403434,
    ('H', 6, 'excess_sum'): 7.346502731397612,
    ('H', 6, 'ic_mean'): 0.020467046640959666,
    ('H', 6, 'n_ic'): 30,
    ('H', 6, 'n_spread'): 30,
    ('H', 6, 'nw_t'): 0.4727504178046596,
    ('H', 6, 'sell_hit_rate'): 0.5,
    ('H', 6, 'spread_sum'): 0.39195309934603206,
    ('H', 6, 'yearly_sum'): 0.0579215694825872,
}


# ---------------------------------------------------------------------------
# Part 1: whole-market regression (passes on current code)
# ---------------------------------------------------------------------------


def test_fixture_shape(base_db):
    con = duckdb.connect(str(base_db))
    assert con.execute("SELECT count(*) FROM bt_rows").fetchone()[0] == N_TICKERS * N_DATES
    assert {h for (h,) in con.execute("SELECT DISTINCT horizon FROM bt_returns").fetchall()} == {
        3, 6, 12}
    con.close()


@pytest.mark.parametrize("table", ["metrics", "diagnostics", "signals"])
def test_report_tables_reproduce_frozen_digest(base_report, table):
    exp_digest, exp_n = FROZEN_DIGEST[table]
    lines = base_report[table]
    assert len(lines) == exp_n
    assert digest(lines) == exp_digest


def test_report_metrics_frozen_values(base_report):
    df = base_report["raw_metrics"]
    got = {(r.model, int(r.horizon), r.metric): r.value
           for r in df[(df["key"] == "") & df["model"].isin(FROZEN_MODELS)
                       & (df["scope"] == "in_sample") & df["horizon"].notna()].itertuples()}
    assert len(FROZEN_METRICS) > 0
    for k, v in FROZEN_METRICS.items():
        assert k in got, k
        assert close(got[k], v), (k, got[k], v)


def test_report_yearly_frozen_values(base_report):
    df = base_report["raw_metrics"]
    y = df[(df["metric"] == "yearly_spread") & df["model"].isin(FROZEN_MODELS)]
    got = {(r.model, int(r.horizon), r.key): r.value for r in y.itertuples()}
    assert set(got) == set(FROZEN_YEARLY)
    for k, v in FROZEN_YEARLY.items():
        assert close(got[k], v), (k, got[k], v)


def test_report_pass_results_frozen(base_report):
    df = base_report["raw_metrics"]
    p = df[df["metric"] == "pass_result"]
    got = {r.model: r.key for r in p.itertuples()}
    for k, v in FROZEN_PASS.items():
        assert got[k] == v, k


def load_panel_obs(path: Path, model: str):
    con = duckdb.connect(str(path))
    rows = rep.load_rows(con, "US")
    fwd = rep.load_returns(con)
    con.close()
    fwd = fwd[fwd["horizon"].isin(m.HORIZONS)]  # the §2.2 filter, applied here explicitly
    panel = rep.model_panel(rows, model)
    return panel, m.sector_excess(fwd, panel)


def func_values(path: Path) -> dict:
    out = {}
    for model in FROZEN_MODELS:
        panel, obs = load_panel_obs(path, model)
        bd = m.spread_by_date(obs)
        yr = m.yearly_spread(bd)
        hr = m.hit_rates(obs)
        ic = m.rank_ic(obs)
        to = m.turnover(panel[["date", "ticker", "label"]])
        for h in m.HORIZONS:
            ich = ic[(ic["horizon"] == h) & ic["ic"].notna()].sort_values("date")
            bdh = bd[(bd["horizon"] == h)].sort_values("date")
            out[(model, h, "spread_sum")] = float(bdh["spread"].sum(skipna=True))
            out[(model, h, "n_spread")] = int(bdh["spread"].notna().sum())
            out[(model, h, "buy_excess_sum")] = float(bdh["buy_excess"].sum(skipna=True))
            out[(model, h, "ic_mean")] = float(ich["ic"].mean())
            out[(model, h, "nw_t")] = float(m.newey_west_t(ich["ic"].tolist(), h - 1))
            out[(model, h, "n_ic")] = len(ich)
            r = hr[hr["horizon"] == h].iloc[0]
            out[(model, h, "buy_hit_rate")] = float(r["buy_hit_rate"])
            out[(model, h, "sell_hit_rate")] = float(r["sell_hit_rate"])
            out[(model, h, "yearly_sum")] = float(yr[yr["horizon"] == h]["spread"].sum())
            out[(model, h, "excess_sum")] = float(obs[obs["horizon"] == h]["excess"].sum())
        out[(model, 0, "label_turnover")] = float(to["label_turnover"].mean())
    return out


def test_public_metric_functions_frozen(base_db):
    got = func_values(base_db)
    assert len(FROZEN_FUNCS) > 0
    assert set(got) == set(FROZEN_FUNCS)
    for k, v in FROZEN_FUNCS.items():
        assert close(got[k], v), (k, got[k], v)


# ---------------------------------------------------------------------------
# Part 2: §2.2 h = 1 rows and new columns never change the whole-market report
# ---------------------------------------------------------------------------


def test_load_returns_reads_horizons_3_6_12_only(tmp_path):
    con = build_db(tmp_path / "h1.duckdb", h1=True)
    assert 1 in {h for (h,) in con.execute("SELECT DISTINCT horizon FROM bt_returns").fetchall()}
    fwd = rep.load_returns(con)
    con.close()
    assert set(fwd["horizon"]) == set(m.HORIZONS) == {3, 6, 12}


def test_metrics_horizons_unchanged():
    assert m.HORIZONS == (3, 6, 12)


def test_report_with_h1_rows_and_new_columns_is_row_identical(base_report, tmp_path,
                                                             monkeypatch):
    p = tmp_path / "post.duckdb"
    build_db(p, h1=True, new_cols=True).close()
    assert run_report(p, tmp_path / "out", monkeypatch) == 0
    got = report_tables(p)
    for t in ("metrics", "diagnostics", "signals"):
        assert got[t] == base_report[t], t
        assert digest(got[t]) == FROZEN_DIGEST[t][0], t


def test_report_with_h1_rows_only_is_row_identical(base_report, tmp_path, monkeypatch):
    p = tmp_path / "h1only.duckdb"
    build_db(p, h1=True).close()
    assert run_report(p, tmp_path / "out", monkeypatch) == 0
    got = report_tables(p)
    for t in ("metrics", "diagnostics", "signals"):
        assert got[t] == base_report[t], t


def test_default_report_adds_no_model():
    assert set(rep.MODELS) == {"A", "B", "A_BOOK", "BASELINE_PB", "C", "C_V", "C_Q", "C_M",
                               "C_R", "H", "H_BBY", "H_DY"}


# ---------------------------------------------------------------------------
# Part 2: `report --extra-models` (§2.2)
# ---------------------------------------------------------------------------


def run_cli(args, monkeypatch, capsys):
    from valuation.cli import main

    monkeypatch.chdir(REPO_ROOT)
    try:
        rc = main(args)
    except SystemExit as e:
        out = capsys.readouterr()
        pytest.fail(f"argparse rejected {args}: exit {e.code}: {out.err[-300:]}")
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_extra_models_adds_diagnostic_rows_only(base_report, tmp_path, monkeypatch, capsys):
    p = tmp_path / "x.duckdb"
    build_db(p, h1=True, new_cols=True).close()
    rc, _ = run_cli(["backtest", "report", "--bt-db", str(p), "--today", TODAY, "--out",
                     str(tmp_path / "out"), "--extra-models"], monkeypatch, capsys)
    assert rc == 0
    got = report_tables(p)
    # the frozen and existing diagnostic models are untouched
    assert got["metrics_frozen_models"] == base_report["metrics_frozen_models"]
    assert got["signals_frozen_models"] == base_report["signals_frozen_models"]
    # SPEC-QUESTION: §2.2 says --extra-models "adds diagnostic rows J, G, D, E, F" without naming
    # the tables; read as rows in bt_metrics and bt_signals like every other report model.
    models = set(got["raw_metrics"]["model"])
    assert set(EXTRA) <= models
    sig = got["raw_signals"]
    con = duckdb.connect(str(p))
    rows = con.execute("SELECT date, ticker, j_signal, j_pct, g_signal, g_z, d_signal, d_mos, "
                       "e_signal, e_f_score, f_signal, f_acc_pct FROM bt_rows").df()
    con.close()
    rows["date"] = [d.date() if hasattr(d, "date") else d for d in rows["date"]]
    score = {"J": lambda r: -r.j_pct, "G": lambda r: r.g_z, "D": lambda r: r.d_mos,
             "E": lambda r: r.e_f_score, "F": lambda r: -r.f_acc_pct}
    for k in EXTRA:
        s = sig[sig["model"] == k].copy()
        s["date"] = [d.date() if hasattr(d, "date") else d for d in s["date"]]
        mm = s.merge(rows, on=["date", "ticker"])
        assert len(mm) == len(rows)
        lab = mm[f"{k.lower()}_signal"]
        assert (mm["label"] == lab).all(), k
        for r in mm.itertuples():
            exp = score[k](r) if r.label in ("BUY", "HOLD", "SELL") else math.nan
            assert close(r.score, exp), (k, r.ticker, r.date)
    md = sorted((tmp_path / "out").glob("backtest_US_*.md"))[-1].read_text()
    assert "unvalidated" in md.lower()


def test_extra_models_refused_with_holdout(base_db, tmp_path, monkeypatch, capsys):
    # SPEC-QUESTION: `report --holdout` is already refused by the existing CLI (the holdout runs
    # and reports in one shot via `run`), so this only checks that the combination is accepted
    # by the parser and refused with exit 2 before anything is written.
    p = copy_db(base_db, tmp_path, "bt.duckdb")
    rc, text = run_cli(["backtest", "report", "--bt-db", str(p), "--today", TODAY, "--out",
                        str(tmp_path / "out"), "--extra-models", "--holdout"], monkeypatch,
                       capsys)
    assert rc == 2
    assert "REFUSED" in text
    con = duckdb.connect(str(p))
    assert con.execute("SELECT count(*) FROM bt_runs").fetchone()[0] == 0
    con.close()
    assert not (REPO_ROOT / "docs" / "qa" / "holdout_v2.md").exists()


# ---------------------------------------------------------------------------
# Part 2: CLI actions and backward-compatible flags (§10)
# ---------------------------------------------------------------------------


def backtest_help(capsys) -> str:
    from valuation.cli import main

    with pytest.raises(SystemExit) as ei:
        main(["backtest", "--help"])
    assert ei.value.code == 0
    return capsys.readouterr().out


def test_cli_backtest_actions_include_fit(capsys):
    text = backtest_help(capsys)
    import re

    mt = re.search(r"\{([a-z,]+)\}", text)
    assert mt, text
    assert set(mt.group(1).split(",")) == {"build", "run", "report", "benchmarks", "fit"}


def test_cli_backtest_existing_flags_kept(capsys):
    text = backtest_help(capsys)
    for flag in ("--bt-db", "--market", "--workers", "--today", "--holdout", "--freeze",
                 "--result", "--out", "--extra-models"):
        assert flag in text, flag


def test_cli_existing_flags_defaults_unchanged(monkeypatch):
    # the parser is built inside main(); capture the parsed namespace via backtest_cmd
    import valuation.backtest.cli_cmd as cc

    seen = {}
    monkeypatch.setattr(cc, "backtest_cmd", lambda args: seen.update(vars(args)) or 0)
    from valuation.cli import main

    assert main(["backtest", "report"]) == 0
    assert seen["bt_db"] == "data/backtest.duckdb"
    assert seen["universe"] == "US"
    assert seen["workers"] == 4
    assert seen["holdout"] is False
    assert seen["freeze"] is None
    assert seen["result"] == "docs/qa/holdout_v2.md"
    assert seen["out"] == "reports/backtest"


def test_cli_fit_action_parses(monkeypatch):
    import valuation.backtest.cli_cmd as cc

    seen = {}
    monkeypatch.setattr(cc, "backtest_cmd", lambda args: seen.update(vars(args)) or 0)
    from valuation.cli import main

    assert main(["backtest", "fit", "--market", "US", "--bt-db", "x.duckdb", "--today",
                 "2026-11-10", "--out", "o"]) == 0
    assert seen["action"] == "fit"
    assert seen["today"] == date(2026, 11, 10)


def test_cli_report_extra_models_flag_parses(monkeypatch):
    import valuation.backtest.cli_cmd as cc

    seen = {}
    monkeypatch.setattr(cc, "backtest_cmd", lambda args: seen.update(vars(args)) or 0)
    from valuation.cli import main

    assert main(["backtest", "report"]) == 0
    assert seen.get("extra_models") is False  # opt-in, off by default
    assert main(["backtest", "report", "--extra-models"]) == 0
    assert seen["extra_models"] is True


# ---------------------------------------------------------------------------
# Part 2: `backtest run` additions (§2.1): stamp, new columns, bt_label_versions, h = 1
# ---------------------------------------------------------------------------


def live_versions():
    from fit_db_fixture import live_versions as lv

    return lv()


def test_engine_model_version_bumped():
    from valuation.models.frozen import FROZEN_VERSIONS

    assert bte.MODEL_VERSION == "backtest/0.3.0"
    rv = bte.run_model_version()
    for v in FROZEN_VERSIONS:  # holdout guard check (d) unaffected
        assert v in rv


def test_compute_returns_writes_h1_rows(tmp_path):
    con = build_db(tmp_path / "cr.duckdb")
    n = runner.compute_returns(con, DATES)
    got = con.execute("SELECT date, ticker, horizon, t0, t1, ret, flag FROM bt_returns").df()
    prices = con.execute("SELECT ticker, date, adj_close FROM prices_daily").df()
    con.close()
    assert n == len(got)
    assert set(got["horizon"]) == {1, 3, 6, 12}
    prices["date"] = [d.date() if hasattr(d, "date") else d for d in prices["date"]]
    exp = m.forward_returns(prices, DATES, horizons=(1, 3, 6, 12))
    got["date"] = [d.date() if hasattr(d, "date") else d for d in got["date"]]
    a = got.sort_values(["date", "ticker", "horizon"]).reset_index(drop=True)
    b = exp.sort_values(["date", "ticker", "horizon"]).reset_index(drop=True)
    assert len(a) == len(b)
    assert np.allclose(a["ret"].to_numpy(dtype=float), b["ret"].to_numpy(dtype=float),
                       equal_nan=True, atol=1e-12)


# --- run_backtest with a fake run_signals (no real signal computation) ---

OLD_BT_ROWS_DDL = """CREATE TABLE IF NOT EXISTS bt_rows (
        universe VARCHAR, date DATE, ticker VARCHAR, market VARCHAR, sector VARCHAR,
        eligible BOOLEAN, elig_fail_reason VARCHAR, price DOUBLE, bvps DOUBLE, pb DOUBLE,
        roe0 DOUBLE, roe_raw DOUBLE, a_signal VARCHAR, a_reason VARCHAR, a_mos DOUBLE,
        b_signal VARCHAR, b_reason VARCHAR, b_residual DOUBLE, b_resid_pct DOUBLE,
        b_upside DOUBLE, b_n_fit INTEGER, b_r2 DOUBLE, flags VARCHAR,
        c_signal VARCHAR, c_reason VARCHAR, c_score DOUBLE, c_value DOUBLE, c_quality DOUBLE,
        c_momentum DOUBLE, c_risk DOUBLE, c_v_label VARCHAR, c_q_label VARCHAR,
        c_m_label VARCHAR, c_r_label VARCHAR,
        h_signal VARCHAR, h_score DOUBLE, h_dy DOUBLE, h_bby DOUBLE,
        PRIMARY KEY (universe, date, ticker))"""  # bt_rows as of backtest/0.2.0
RUN_DATE = date(2015, 6, 30)
FAKE = {"AAA": dict(a_signal="HOLD", a_model_signal="SELL", b_signal="BUY", c_signal="HOLD",
                    h_signal="SELL", j_signal="BUY", j_pct=0.1, g_signal="SELL", g_z=1.2,
                    d_signal="HOLD", d_mos=0.05, e_signal="BUY", e_f_score=8.0,
                    f_signal="SELL", f_acc_pct=0.9),
        "BBB": dict(a_signal="BUY", a_model_signal="BUY", b_signal="SELL", c_signal="SELL",
                    h_signal="HOLD", j_signal="NO_SIGNAL", j_pct=None, g_signal="HOLD",
                    g_z=3.4, d_signal="NOT_ELIGIBLE", d_mos=None, e_signal="SELL",
                    e_f_score=1.0, f_signal="BUY", f_acc_pct=0.05)}


class SyncExecutor:
    submitted: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *args, **kw):
        SyncExecutor.submitted.append(args)
        f = concurrent.futures.Future()
        try:
            f.set_result(fn(*args, **kw))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f


def fake_run_signals(con, d, tickers=None, liquidity=None, csm_path=None, **kw):
    from valuation.engine import SIGNAL_COLUMNS

    rows = []
    for t in sorted(tickers or []):
        r = {c: None for c in SIGNAL_COLUMNS}
        for c in SIGNAL_COLUMNS:
            if c.endswith("_signal"):
                r[c] = "NO_SIGNAL"
            elif c.endswith("_reason"):
                r[c] = None
        r.update(ticker=t, market="US", sector="Industrials", eligible=True, flags="",
                 price=10.0, bvps=5.0, pb=2.0, roe0=0.1, a_mos=0.2, c_score=0.3, h_sy=0.04,
                 h_dy=0.02, h_bby=0.02)
        r.update(FAKE[t])
        rows.append(r)
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS), {"run_id": "fake"}


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    import valuation.engine as live

    db_path = tmp_path / "bt.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(OLD_BT_ROWS_DDL)  # an older table: new columns must be added by ALTER
    con.close()
    view = duckdb.connect()
    view.execute("CREATE TABLE signals (run_id VARCHAR, ticker VARCHAR, payload_json VARCHAR)")
    view.execute("CREATE TABLE runs (run_id VARCHAR)")
    monkeypatch.setitem(bte._CON, str(db_path), view)
    monkeypatch.setattr(live, "run_signals", fake_run_signals)
    monkeypatch.setattr(bte, "ProcessPoolExecutor", SyncExecutor)
    SyncExecutor.submitted = []
    return db_path


def new_stamp() -> dict:
    from valuation.models.frozen import FROZEN_V2, FROZEN_VERSIONS, params_sha256

    return {"version": ",".join(["backtest/0.3.0", *FROZEN_VERSIONS]),
            "params_hash": params_sha256(FROZEN_V2), "label_versions": live_versions()}


def part_path(db_path: Path, d: date) -> Path:
    return db_path.parent / "backtest_parts" / "US" / f"{d.isoformat()}.json"


def run_bt(db_path):
    return bte.run_backtest(str(db_path), "US", {RUN_DATE: ["AAA", "BBB"]}, workers=1,
                            log=lambda *a: None)


def stored(db_path) -> dict:
    con = duckdb.connect(str(db_path))
    df = con.execute("SELECT * FROM bt_rows WHERE universe='US'").df()
    con.close()
    return {r["ticker"]: r for r in df.to_dict("records")}


def test_run_backtest_stores_abch_labels_unchanged(fake_run):  # passes now (regression)
    run_bt(fake_run)
    s = stored(fake_run)
    assert set(s) == {"AAA", "BBB"}
    assert s["AAA"]["a_signal"] == "SELL"  # the model label a_model_signal (freeze §3)
    assert s["BBB"]["a_signal"] == "BUY"
    assert (s["AAA"]["b_signal"], s["AAA"]["c_signal"], s["AAA"]["h_signal"]) == (
        "BUY", "HOLD", "SELL")
    assert (s["BBB"]["b_signal"], s["BBB"]["c_signal"], s["BBB"]["h_signal"]) == (
        "SELL", "SELL", "HOLD")


def test_run_backtest_stores_new_label_columns(fake_run):  # §2.1.1
    run_bt(fake_run)
    con = duckdb.connect(str(fake_run))
    types = {r[0]: r[1] for r in con.execute("DESCRIBE bt_rows").fetchall()}
    con.close()
    for c, t in NEW_ROW_TYPES.items():
        assert types.get(c) == t, c
    s = stored(fake_run)
    for t, vals in FAKE.items():
        for c in NEW_ROW_TYPES:
            exp = vals[c]
            got = s[t][c]
            if exp is None:
                assert got is None or pd.isna(got), (t, c)
            elif isinstance(exp, float):
                assert close(got, exp), (t, c)
            else:
                assert got == exp, (t, c)


def test_run_backtest_writes_label_versions(fake_run):  # §2.1.3
    run_bt(fake_run)
    con = duckdb.connect(str(fake_run))
    cols = [r[0] for r in con.execute("DESCRIBE bt_label_versions").fetchall()]
    got = dict(con.execute("SELECT model, model_version FROM bt_label_versions "
                           "WHERE universe='US'").fetchall())
    con.close()
    assert cols == ["universe", "model", "model_version"]
    assert got == live_versions()
    assert len(got) == 9


def test_run_backtest_part_stamp_has_label_versions(fake_run):  # §2.1.2
    run_bt(fake_run)
    head = json.loads(part_path(fake_run, RUN_DATE).read_text())
    for k, v in new_stamp().items():
        assert head.get(k) == v, k


def write_part(db_path: Path, stamp: dict):
    p = part_path(db_path, RUN_DATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**stamp, "date": RUN_DATE.isoformat(), "seconds": 0.0,
                             "n_members": 0, "rows": []}))


def test_part_with_matching_stamp_is_reused(fake_run):
    write_part(fake_run, new_stamp())
    res = run_bt(fake_run)
    assert SyncExecutor.submitted == []
    assert res["ran"] == 0


@pytest.mark.parametrize("change", ["no_label_versions", "stale_label_version", "old_version"])
def test_part_with_old_stamp_is_rerun(fake_run, change):
    st = new_stamp()
    if change == "no_label_versions":
        st.pop("label_versions")
        st["version"] = bte.run_model_version()  # even with the current version string
    elif change == "stale_label_version":
        st["label_versions"] = {**st["label_versions"], "G": "altman_z/0.0.9"}
    else:
        from valuation.models.frozen import FROZEN_VERSIONS

        st["version"] = ",".join(["backtest/0.2.0", *FROZEN_VERSIONS])
    write_part(fake_run, st)
    res = run_bt(fake_run)
    assert res["ran"] == 1
    assert len(SyncExecutor.submitted) == 1


def test_report_applies_in_sample_exit_lock(base_db, tmp_path, monkeypatch):
    """backtest.md §2 in-sample exit lock: the US report locks stock and benchmark returns at the
    last in-sample date."""
    seen = []
    real = rep.lock_in_sample

    def spy(fwd, in_end):
        seen.append(in_end)
        return real(fwd, in_end)

    monkeypatch.setattr(rep, "lock_in_sample", spy)
    p = copy_db(base_db, tmp_path, "lock.duckdb")
    assert run_report(p, tmp_path / "out", monkeypatch) == 0
    ins = bte.in_sample_dates(bte.rebalance_dates(bte.BT_START, date.fromisoformat(TODAY)))
    assert seen and all(d == ins[-1] for d in seen)
