"""Backtest runner: month-end rebalance -> eligibility -> Model A -> Model B, point in time.

Spec: docs/models/backtest.md. Each date runs the live `valuation.engine.run_signals` unchanged on a
connection that sees only the backtest DB, with prices dated <= d and facts filed <= d (the live
engine's own point-in-time selection). Deterministic (no randomness, sorted inputs) and resumable
(dates already in `bt_rows` for a universe are skipped).
"""

from __future__ import annotations

import calendar
import hashlib
import json
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime

import duckdb
import pandas as pd

from .data import sic_to_sector  # noqa: F401  (public API, spec §6)
from .membership import members_on

MODEL_VERSION = "backtest/0.1.0"
BT_START = REBALANCE_START = date(2014, 1, 1)
HOLDOUT_MONTHS = 36
SOURCE_TABLES = ("securities", "prices_daily", "fundamentals", "dividends", "splits",
                 "rates_daily", "fx_daily")

BT_DDL = [
    """CREATE TABLE IF NOT EXISTS bt_rows (
        universe VARCHAR, date DATE, ticker VARCHAR, market VARCHAR, sector VARCHAR,
        eligible BOOLEAN, elig_fail_reason VARCHAR, price DOUBLE, bvps DOUBLE, pb DOUBLE,
        roe0 DOUBLE, roe_raw DOUBLE, a_signal VARCHAR, a_reason VARCHAR, a_mos DOUBLE,
        b_signal VARCHAR, b_reason VARCHAR, b_residual DOUBLE, b_resid_pct DOUBLE,
        b_upside DOUBLE, b_n_fit INTEGER, b_r2 DOUBLE, flags VARCHAR,
        PRIMARY KEY (universe, date, ticker))""",
    """CREATE TABLE IF NOT EXISTS bt_dates (
        universe VARCHAR, date DATE, n_members INTEGER, n_with_price INTEGER, seconds DOUBLE,
        PRIMARY KEY (universe, date))""",
    """CREATE TABLE IF NOT EXISTS bt_runs (
        run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, kind VARCHAR, market VARCHAR,
        thresholds_hash VARCHAR, git_sha VARCHAR, today DATE, n_dates INTEGER,
        model_version VARCHAR)""",
]


def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, n: int) -> date:
    """Last calendar day of the month n months after d's month."""
    k = d.year * 12 + (d.month - 1) + n
    return month_end(k // 12, k % 12 + 1)


def rebalance_dates(start: date = BT_START, today: date | None = None) -> list[date]:
    """Calendar month-ends from start's month to the last month-end strictly before today."""
    today = today or date.today()
    out, d = [], month_end(start.year, start.month)
    while d < today:
        out.append(d)
        d = add_months(d, 1)
    return out


def holdout_dates(dates: list[date], n: int = HOLDOUT_MONTHS) -> list[date]:
    """The last min(n, len(dates)) dates (locked unless a run is an explicit holdout run)."""
    return sorted(dates)[-n:] if n > 0 else []


def in_sample_dates(dates: list[date], n: int = HOLDOUT_MONTHS) -> list[date]:
    ho = set(holdout_dates(dates, n))
    return [d for d in sorted(dates) if d not in ho]


def _consts(module) -> dict:
    out = {}
    for k, v in vars(module).items():
        if not k.isupper() or k.startswith("_") or isinstance(v, bool | str):
            continue
        if isinstance(v, frozenset | set | tuple | list):
            out[k] = sorted(v) if isinstance(v, frozenset | set) else list(v)
        elif isinstance(v, int | float):
            out[k] = v
    return out


def thresholds_hash(liquidity: dict | None = None) -> str:
    """sha256 of the frozen model constants + liquidity config (spec §2)."""
    from ..models import eligibility as elig
    from ..models import peer_regression as pr
    from ..models import residual_income as ri

    obj = {"residual_income": _consts(ri), "peer_regression": _consts(pr),
           "liquidity": liquidity if liquidity is not None else elig.load_config()}
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def log_run(con, kind: str, market: str, today: date, n_dates: int) -> str:
    from ..engine import _git_sha

    for ddl in BT_DDL:
        con.execute(ddl)
    run_id = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO bt_runs VALUES (?,?,?,?,?,?,?,?,?)",
                [run_id, datetime.now(), kind, market, thresholds_hash(), _git_sha(), today,
                 n_dates, MODEL_VERSION])
    return run_id


# ---------- per-date worker ----------

def _view_con(db_path: str) -> duckdb.DuckDBPyConnection:
    """In-memory connection whose source tables are read-only views of the backtest DB, so that
    run_signals' bookkeeping inserts (runs, signals) never touch the backtest file."""
    from ..store import db

    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path}' AS src (READ_ONLY)")
    for t in SOURCE_TABLES:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM src.{t}")
    for stmt in db.SCHEMA:
        if not any(f"EXISTS {t} " in stmt or f"EXISTS {t}(" in stmt for t in SOURCE_TABLES):
            con.execute(stmt)
    return con


def signals_on(con, d: date, intervals, csm_path=None) -> pd.DataFrame:
    """Spec §3 for one date: members on d with a securities row and a bar dated <= d, run through
    the live run_signals (prices <= d, facts filed <= d). Returns its report frame by ticker."""
    from ..engine import run_signals

    members = members_on(intervals, d)
    have = {t for (t,) in con.execute(
        "SELECT DISTINCT s.ticker FROM securities s JOIN prices_daily p USING (ticker) "
        "WHERE p.date <= ?", [d]).fetchall()}
    universe = sorted(members & have)
    if not universe:
        from ..engine import SIGNAL_COLUMNS
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    df, _ = run_signals(con, d, tickers=set(universe), csm_path=csm_path)
    return df.sort_values("ticker").reset_index(drop=True)


_CON: dict[str, duckdb.DuckDBPyConnection] = {}


def run_one(db_path: str, d: date, tickers: list[str], csm_path=None) -> tuple[date, list, float]:
    from ..engine import run_signals

    con = _CON.get(db_path)
    if con is None:
        con = _CON[db_path] = _view_con(db_path)
    t0 = time.time()
    df, info = run_signals(con, d, tickers=set(tickers), csm_path=csm_path)
    payload = {t: json.loads(p) for t, p in con.execute(
        "SELECT ticker, payload_json FROM signals WHERE run_id=?", [info["run_id"]]).fetchall()}
    con.execute("DELETE FROM signals")
    con.execute("DELETE FROM runs")
    rows = []
    for r in df.to_dict("records"):
        p = payload.get(r["ticker"], {})
        rows.append([
            r["ticker"], r["market"], r["sector"], r["eligible"], r["elig_fail_reason"],
            r.get("price"), r.get("bvps"), r.get("pb"), r.get("roe0"), p.get("roe_raw"),
            r["a_signal"], r["a_no_signal_reason"], r.get("a_mos"), r["b_signal"],
            r["b_no_signal_reason"], p.get("b_residual"), r.get("b_resid_pct"),
            r.get("b_implied_upside"), p.get("b_n_fit"), p.get("b_r2"), r["flags"]])
    return d, rows, time.time() - t0


def _nan_none(v):
    return None if isinstance(v, float) and v != v else v


def _part_dir(db_path: str, universe: str):
    from pathlib import Path

    d = Path(db_path).parent / "backtest_parts" / universe
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_backtest(db_path: str, universe: str, members_by_date: dict[date, list[str]],
                 workers: int = 4, csm_path=None, log=print) -> dict:
    """Run every date not yet done for `universe`, then load all dates into bt_rows.

    Workers read the DB through a read-only attach, so finished dates go to one JSON file each
    (`data/backtest_parts/<universe>/<date>.json`: resumable) and are loaded into the DB after
    the pool has shut down."""
    parts = _part_dir(db_path, universe)
    todo = sorted(d for d in members_by_date if not (parts / f"{d.isoformat()}.json").exists())
    log(f"[{universe}] {len(todo)} dates to run ({len(members_by_date) - len(todo)} cached)")
    t_start = time.time()
    if todo:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_one, db_path, d, sorted(members_by_date[d]), csm_path): d
                    for d in todo}
            for f in futs:
                d, rows, secs = f.result()
                payload = {"date": d.isoformat(), "seconds": secs,
                           "n_members": len(members_by_date[d]),
                           "rows": [[_nan_none(v) for v in r] for r in rows]}
                tmp = parts / f"{d.isoformat()}.json.tmp"
                tmp.write_text(json.dumps(payload, default=str))
                tmp.rename(parts / f"{d.isoformat()}.json")
                log(f"[{universe}] {d}: {len(rows)} names, {secs:.1f}s")
    ran = time.time() - t_start
    con = duckdb.connect(db_path)
    for ddl in BT_DDL:
        con.execute(ddl)
    con.execute("DELETE FROM bt_rows WHERE universe=?", [universe])
    con.execute("DELETE FROM bt_dates WHERE universe=?", [universe])
    recs, dates = [], []
    for d in sorted(members_by_date):
        p = parts / f"{d.isoformat()}.json"
        payload = json.loads(p.read_text())
        recs += [[universe, d, *r] for r in payload["rows"]]
        dates.append([universe, d, payload["n_members"], len(payload["rows"]),
                      payload["seconds"]])
    cols = ["universe", "date", "ticker", "market", "sector", "eligible", "elig_fail_reason",
            "price", "bvps", "pb", "roe0", "roe_raw", "a_signal", "a_reason", "a_mos",
            "b_signal", "b_reason", "b_residual", "b_resid_pct", "b_upside", "b_n_fit", "b_r2",
            "flags"]
    df = pd.DataFrame(recs, columns=cols)
    con.register("_df", df)
    con.execute("INSERT INTO bt_rows SELECT * FROM _df")
    con.unregister("_df")
    con.executemany("INSERT INTO bt_dates VALUES (?,?,?,?,?)", dates)
    con.close()
    return {"ran": len(todo), "seconds": ran,
            "cpu_seconds": sum(r[4] for r in dates)}


def load_signals(con, universe: str) -> pd.DataFrame:
    return con.execute("SELECT * FROM bt_rows WHERE universe=? ORDER BY date, ticker",
                       [universe]).df()
