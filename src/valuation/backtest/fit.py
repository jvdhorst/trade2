"""Per-company model fit (spec docs/models/per_company_fit.md v0.1.0, `fit/0.1.0`).

For each (company, model) pair, compare a strategy driven by the model's monthly label with simply
holding that stock, over two fixed in-sample windows (select 2014-2019 with a BH-corrected one-sided
t-test, confirm 2020-2023 with a minimum effect). Reads the stored per-date labels (`bt_rows`) and
1-month forward returns (`bt_returns`, horizon 1) written once by `valuation backtest run`; never
computes labels, never touches `bt_runs` or any holdout date (FIT_LOCK_END is a fixed constant).

Screening signals only, not investment advice. CONFIRMED means "worked in both windows", not "will
work"; every result is a survivor-biased upper bound; all models stay unvalidated.
"""

from __future__ import annotations

import json
import math
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import engine as bte
from .engine import add_months, label_versions

# §1 constants ------------------------------------------------------------------------------
FIT_MODEL_VERSION = "fit/0.1.0"
FIT_MARKET = "US"
FIT_HORIZON = 1
FIT_SEL_START, FIT_SEL_END = date(2014, 1, 31), date(2019, 12, 31)
FIT_CONF_START, FIT_CONF_END = date(2020, 1, 31), date(2023, 10, 31)
FIT_LOCK_END = date(2023, 10, 31)
FIT_COST_BP = 10
FIT_COST = FIT_COST_BP / 10_000
FIT_CASH_RATE = 0.0
FIT_INITIAL_POSITION = 1
FIT_Q = 0.10
FIT_MIN_EFFECT = 0.01
FIT_SEL_MIN_MONTHS, FIT_SEL_MIN_ACTIVE, FIT_SEL_MIN_SWITCHES = 36, 6, 2
FIT_CONF_MIN_MONTHS, FIT_CONF_MIN_ACTIVE, FIT_CONF_MIN_SWITCHES = 24, 3, 1
FIT_PRIMARY_VARIANT = "SELL_EXIT"
SELL_EXIT, BUY_ONLY = "SELL_EXIT", "BUY_ONLY"
VARIANTS = (BUY_ONLY, SELL_EXIT)
FIT_MODELS = ("A", "B", "C", "H", "J", "G", "D", "E", "F")
# §3.2: from the model specs, never from data (G cannot emit BUY)
FIT_CAN_EMIT = {k: (frozenset({SELL_EXIT}) if k == "G" else frozenset({SELL_EXIT, BUY_ONLY}))
                for k in FIT_MODELS}
# §3.1: stored label column per model (A: the model label incl. SELL, freeze §3)
LABEL_COLUMNS = {k: f"{k.lower()}_signal" for k in FIT_MODELS}
INSTRUCTION_LABELS = ("BUY", "HOLD", "SELL")
STATUSES = ("CONFIRMED", "NOT_CONFIRMED", "INSUFFICIENT", "NOT_TESTED")

# §7 enforcement 1: the windows never reach the holdout
assert (FIT_SEL_START < FIT_SEL_END < FIT_CONF_START <= FIT_CONF_END
        <= FIT_LOCK_END == date(2023, 10, 31)), "fit windows overlap the holdout"

FIT_PARAMS = {
    "FIT_CAN_EMIT": {k: sorted(v) for k, v in FIT_CAN_EMIT.items()},
    "FIT_CASH_RATE": FIT_CASH_RATE,
    "FIT_CONF_END": FIT_CONF_END.isoformat(),
    "FIT_CONF_MIN_ACTIVE": FIT_CONF_MIN_ACTIVE,
    "FIT_CONF_MIN_MONTHS": FIT_CONF_MIN_MONTHS,
    "FIT_CONF_MIN_SWITCHES": FIT_CONF_MIN_SWITCHES,
    "FIT_CONF_START": FIT_CONF_START.isoformat(),
    "FIT_COST_BP": FIT_COST_BP,
    "FIT_HORIZON": FIT_HORIZON,
    "FIT_INITIAL_POSITION": FIT_INITIAL_POSITION,
    "FIT_LOCK_END": FIT_LOCK_END.isoformat(),
    "FIT_MARKET": FIT_MARKET,
    "FIT_MIN_EFFECT": FIT_MIN_EFFECT,
    "FIT_MODELS": list(FIT_MODELS),
    "FIT_MODEL_VERSION": FIT_MODEL_VERSION,
    "FIT_PRIMARY_VARIANT": FIT_PRIMARY_VARIANT,
    "FIT_Q": FIT_Q,
    "FIT_SEL_END": FIT_SEL_END.isoformat(),
    "FIT_SEL_MIN_ACTIVE": FIT_SEL_MIN_ACTIVE,
    "FIT_SEL_MIN_MONTHS": FIT_SEL_MIN_MONTHS,
    "FIT_SEL_MIN_SWITCHES": FIT_SEL_MIN_SWITCHES,
    "FIT_SEL_START": FIT_SEL_START.isoformat(),
}


def fit_params_hash() -> str:
    """The fit params hash (§1): params_sha256 of the canonical JSON of FIT_PARAMS."""
    from ..models.frozen import params_sha256

    return params_sha256(FIT_PARAMS)


DISCLAIMER = ("Screening signals only, not investment advice. Per-company model fit (fit/0.1.0): "
              "CONFIRMED = worked in both in-sample windows (2014-2019, 2020-2023), not a "
              "forecast; survivor-biased upper bound; all models remain unvalidated.")
HINT = "run `valuation backtest run --market US` first"

FIT_TABLE_COLUMNS = [
    "ticker", "model", "model_version", "variant", "is_primary",
    "sel_n_months", "sel_n_active", "sel_n_switches", "sel_ann_excess", "sel_t", "sel_p", "sel_q",
    "conf_n_months", "conf_n_active", "conf_n_switches", "conf_ann_excess", "conf_t", "conf_p",
    "status", "reason"]
FIT_COLUMNS = ["run_id", "fit_version", "fit_params_hash", "market", *FIT_TABLE_COLUMNS]
INT_COLUMNS = ("sel_n_months", "sel_n_active", "sel_n_switches", "conf_n_months",
               "conf_n_active", "conf_n_switches")

FIT_DDL = [
    """CREATE TABLE IF NOT EXISTS company_model_fit (
        run_id VARCHAR, fit_version VARCHAR, fit_params_hash VARCHAR, market VARCHAR,
        ticker VARCHAR, model VARCHAR, model_version VARCHAR, variant VARCHAR,
        is_primary BOOLEAN,
        sel_n_months INTEGER, sel_n_active INTEGER, sel_n_switches INTEGER,
        sel_ann_excess DOUBLE, sel_t DOUBLE, sel_p DOUBLE, sel_q DOUBLE,
        conf_n_months INTEGER, conf_n_active INTEGER, conf_n_switches INTEGER,
        conf_ann_excess DOUBLE, conf_t DOUBLE, conf_p DOUBLE,
        status VARCHAR, reason VARCHAR,
        PRIMARY KEY (run_id, ticker, model, variant))""",
    """CREATE TABLE IF NOT EXISTS fit_runs (
        run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, market VARCHAR, today DATE,
        git_sha VARCHAR, fit_version VARCHAR, fit_params_hash VARCHAR,
        bt_thresholds_hash VARCHAR, frozen_params_hash VARCHAR, label_versions VARCHAR,
        n_tickers INTEGER, n_family_sell_exit INTEGER, n_family_buy_only INTEGER,
        n_confirmed_sell_exit INTEGER, n_confirmed_buy_only INTEGER)""",
]


class HoldoutLeak(ValueError):
    """A row dated, or exiting, after FIT_LOCK_END reached a per-company metric (§7)."""


class FitPreconditionError(ValueError):
    """The backtest DB lacks what `backtest fit` reads (§10); the message carries the hint."""


@dataclass(frozen=True)
class PairMetrics:
    n_months: int
    n_active: int
    n_switches: int
    ann_excess: float
    t_stat: float
    p_value: float


def _default_can_emit() -> dict:
    return {k: frozenset(v) for k, v in FIT_CAN_EMIT.items()}


@dataclass(frozen=True)
class FitThresholds:
    sel_min_months: int = FIT_SEL_MIN_MONTHS
    sel_min_active: int = FIT_SEL_MIN_ACTIVE
    sel_min_switches: int = FIT_SEL_MIN_SWITCHES
    conf_min_months: int = FIT_CONF_MIN_MONTHS
    conf_min_active: int = FIT_CONF_MIN_ACTIVE
    conf_min_switches: int = FIT_CONF_MIN_SWITCHES
    q: float = FIT_Q
    min_effect: float = FIT_MIN_EFFECT
    can_emit: dict = field(default_factory=_default_can_emit)


# helpers -----------------------------------------------------------------------------------

def _isnull(x) -> bool:
    if x is None:
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _as_date(x):
    if _isnull(x):
        return None
    if isinstance(x, datetime):  # also pandas Timestamp
        return x.date()
    if isinstance(x, np.datetime64):
        return pd.Timestamp(x).date()
    return x


def _dates(s) -> list:
    return [_as_date(x) for x in s]


# §3.3 --------------------------------------------------------------------------------------

def position(label, variant: str) -> int:
    """1 = hold the stock, 0 = cash. Only the trigger label acts (SELL for SELL_EXIT, BUY for
    BUY_ONLY); anything else (HOLD, NO_SIGNAL, NOT_ELIGIBLE, NULL) is the variant's default."""
    if variant == SELL_EXIT:
        return 0 if label == "SELL" else 1
    if variant == BUY_ONLY:
        return 1 if label == "BUY" else 0
    raise ValueError(f"unknown variant {variant!r}")


# §4.3 --------------------------------------------------------------------------------------

def window_of(d: date, t1: date, sel=(FIT_SEL_START, FIT_SEL_END),
              conf=(FIT_CONF_START, FIT_CONF_END)) -> str | None:
    t1 = _as_date(t1)
    if t1 is None:
        return None
    d = _as_date(d)
    if sel[0] <= d <= sel[1] and t1 <= sel[1]:
        return "SEL"
    if conf[0] <= d <= conf[1] and t1 <= conf[1]:
        return "CONF"
    return None


def usable_dates(start: date, end: date) -> list[date]:
    """Month-ends d in [start, end] whose 1-month exit (a session in month d + 2, §4.3) can lie
    on or before `end`: the 70 selection and 44 confirmation dates of the real windows."""
    out, d = [], add_months(start, 0)
    while d <= end:
        x = add_months(d, 2)
        if date(x.year, x.month, 1) <= end:
            out.append(d)
        d = add_months(d, 1)
    return out


# §7 ----------------------------------------------------------------------------------------

def assert_no_holdout(df, lock: date = FIT_LOCK_END, date_col: str = "date",
                      exit_col: str = "t1", ret_col: str = "ret") -> None:
    """Raise HoldoutLeak if any row has date > lock, t1 > lock, or a defined ret without t1."""
    if df is None or len(df) == 0:
        return None
    ds = _dates(df[date_col])
    t1s = _dates(df[exit_col]) if exit_col in df.columns else [None] * len(df)
    rets = df[ret_col].tolist() if ret_col in df.columns else [math.nan] * len(df)
    bad = []
    for i, (d, t1, r) in enumerate(zip(ds, t1s, rets, strict=True)):
        why = None
        if d is not None and d > lock:
            why = f"signal date after {lock}"
        elif t1 is not None and t1 > lock:
            why = f"exit session {t1} after {lock}"
        elif t1 is None and not _isnull(r):
            why = "defined return without an exit session"
        if why:
            bad.append((d, str(df["ticker"].iloc[i]) if "ticker" in df.columns else "", why, t1))
    if bad:
        bad.sort(key=lambda b: (b[0] or date.min, b[1]))
        d, t, why, t1 = bad[0]
        raise HoldoutLeak(f"holdout leak: {len(bad)} row(s); first: date {d} ticker {t or '-'} "
                          f"t1 {t1} ({why}); per-company fit never uses data after "
                          f"{lock}")
    return None


# §4.2 --------------------------------------------------------------------------------------

def strategy_returns(obs: pd.DataFrame, variant: str, cost: float = FIT_COST,
                     cash: float = FIT_CASH_RATE) -> pd.DataFrame:
    """One pair, one window: date, label, ret, pos, switch, cost, r_strat, r_hold, excess."""
    cols = ["date", "label", "ret", "pos", "switch", "cost", "r_strat", "r_hold", "excess"]
    if obs is None or len(obs) == 0:
        return pd.DataFrame(columns=cols)
    x = pd.DataFrame({"date": _dates(obs["date"]), "label": obs["label"].tolist(),
                      "ret": obs["ret"].astype(float).tolist()})
    x = x.sort_values("date", kind="stable").reset_index(drop=True)
    ds = x["date"].tolist()
    pos = [position(lab, variant) for lab in x["label"]]
    sw = []
    for i, d in enumerate(ds):
        prev = pos[i - 1] if i > 0 and ds[i - 1] == add_months(d, -1) else FIT_INITIAL_POSITION
        sw.append(1 if pos[i] != prev else 0)
    ret = x["ret"].to_numpy(dtype=float)
    p = np.array(pos, dtype=int)
    s = np.array(sw, dtype=int)
    c = cost * s
    r_strat = p * ret + (1 - p) * cash - c
    x["pos"] = p
    x["switch"] = s
    x["cost"] = c
    x["r_strat"] = r_strat
    x["r_hold"] = ret
    x["excess"] = r_strat - ret
    return x[cols]


# §5 ----------------------------------------------------------------------------------------

def _metrics_from(sr: pd.DataFrame, variant: str) -> PairMetrics:
    T = len(sr)
    if T == 0:
        return PairMetrics(0, 0, 0, math.nan, math.nan, math.nan)
    e = sr["excess"].to_numpy(dtype=float)
    pos = sr["pos"].to_numpy(dtype=int)
    n_active = int((pos == 0).sum()) if variant == SELL_EXIT else int((pos == 1).sum())
    mean = float(e.mean())
    t = p = math.nan
    if T >= 2 and not np.all(e == e[0]):
        sd = float(e.std(ddof=1))
        if sd > 0:
            t = mean / (sd / math.sqrt(T))
            p = float(stats.t.sf(t, df=T - 1))
    return PairMetrics(T, n_active, int(sr["switch"].sum()), 12.0 * mean, t, p)


def pair_metrics(obs: pd.DataFrame, variant: str, start: date, end: date,
                 cost: float = FIT_COST, lock: date = FIT_LOCK_END) -> PairMetrics:
    assert_no_holdout(obs, lock)  # the whole input, before the window filter (§7.3)
    if obs is None or len(obs) == 0:
        return PairMetrics(0, 0, 0, math.nan, math.nan, math.nan)
    ds = _dates(obs["date"])
    t1 = _dates(obs["t1"]) if "t1" in obs.columns else [None] * len(obs)
    ret = obs["ret"].astype(float).to_numpy()
    keep = [start <= d <= end and t is not None and t <= end and not math.isnan(r)
            for d, t, r in zip(ds, t1, ret, strict=True)]
    sub = obs.loc[np.array(keep, dtype=bool), ["date", "label", "ret"]]
    return _metrics_from(strategy_returns(sub, variant, cost=cost), variant)


# §6 ----------------------------------------------------------------------------------------

def activity_reason(m: PairMetrics, min_months: int, min_active: int,
                    min_switches: int) -> str | None:
    if m.n_months < min_months:
        return "FEW_MONTHS"
    if m.n_active < min_active:
        return "FEW_ACTIVE"
    if m.n_switches < min_switches:
        return "FEW_SWITCHES"
    return None


def bh_qvalues(p: Sequence[float]) -> np.ndarray:
    """Benjamini-Hochberg q-values; NaN entries are excluded from m and stay NaN."""
    arr = np.asarray(list(p), dtype=float)
    out = np.full(arr.shape, np.nan)
    ok = np.flatnonzero(~np.isnan(arr))
    m = len(ok)
    if m == 0:
        return out
    order = ok[np.argsort(arr[ok], kind="stable")]
    raw = m * arr[order] / np.arange(1, m + 1)
    q = np.minimum.accumulate(raw[::-1])[::-1]
    out[order] = np.minimum(1.0, q)
    return out


def _sel_reason(sel: PairMetrics, th: FitThresholds) -> str | None:
    r = activity_reason(sel, th.sel_min_months, th.sel_min_active, th.sel_min_switches)
    if r is not None:
        return r
    if _isnull(sel.p_value):
        return "ZERO_VAR"
    return None


def classify(variant: str, model: str, sel: PairMetrics | None, conf: PairMetrics | None,
             q: float, th: FitThresholds | None = None) -> tuple[str, str]:
    th = th or FitThresholds()
    if variant not in th.can_emit.get(model, ()):
        return "NOT_TESTED", "NO_LABEL_TYPE"
    r = _sel_reason(sel, th)
    if r is not None:
        return "INSUFFICIENT", f"SEL_{r}"
    if _isnull(q) or not q <= th.q:
        return "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT"
    r = activity_reason(conf, th.conf_min_months, th.conf_min_active, th.conf_min_switches)
    if r is not None:
        return "INSUFFICIENT", f"CONF_{r}"
    if conf.ann_excess >= th.min_effect:
        return "CONFIRMED", "OK"
    return "NOT_CONFIRMED", "CONF_BELOW_EFFECT"


# §11 fit_table -----------------------------------------------------------------------------

def _to_long(frame: pd.DataFrame) -> pd.DataFrame:
    """Accept the long form (ticker, model, date, label, ret, t1) or the wide form
    (ticker, date, label_<M> ..., ret, t1). A model gets rows only if its label is present."""
    if "model" in frame.columns and "label" in frame.columns:
        x = frame[["ticker", "model", "date", "label", "ret", "t1"]].copy()
        return x[x["model"].isin(FIT_MODELS)]
    parts = []
    for k in FIT_MODELS:
        c = f"label_{k}"
        if c in frame.columns:
            parts.append(pd.DataFrame({"ticker": frame["ticker"].to_numpy(), "model": k,
                                       "date": frame["date"].to_numpy(),
                                       "label": frame[c].to_numpy(),
                                       "ret": frame["ret"].to_numpy(),
                                       "t1": frame["t1"].to_numpy()}))
    if not parts:
        return pd.DataFrame(columns=["ticker", "model", "date", "label", "ret", "t1"])
    return pd.concat(parts, ignore_index=True)


def fit_table(frame: pd.DataFrame, versions: dict, sel=(FIT_SEL_START, FIT_SEL_END),
              conf=(FIT_CONF_START, FIT_CONF_END), th: FitThresholds | None = None,
              cost: float = FIT_COST, lock: date = FIT_LOCK_END) -> pd.DataFrame:
    """One row per (fitted ticker, model present in `frame`, variant): §8.1 minus the run
    columns. Deterministic; row order of `frame` does not matter."""
    th = th or FitThresholds()
    assert_no_holdout(frame, lock)
    lf = _to_long(frame)
    lf = lf.assign(date=_dates(lf["date"]), t1=_dates(lf["t1"]),
                   ret=lf["ret"].astype(float))
    lf["label"] = [None if _isnull(v) else v for v in lf["label"]]
    lf = lf.sort_values(["ticker", "model", "date"], kind="stable").reset_index(drop=True)
    win = [window_of(d, t, sel, conf) if not math.isnan(r) else None
           for d, t, r in zip(lf["date"], lf["t1"], lf["ret"], strict=True)]
    fitted = sorted(set(lf.loc[[w is not None for w in win], "ticker"]))
    models = [k for k in FIT_MODELS if k in set(lf["model"])]
    groups = {key: g for key, g in lf.groupby(["ticker", "model"], sort=True)}
    empty = lf.iloc[0:0]
    recs = []
    for t in fitted:
        for k in models:
            obs = groups.get((t, k), empty)
            for v in VARIANTS:
                rec = {"ticker": t, "model": k, "model_version": versions.get(k), "variant": v,
                       "is_primary": v == FIT_PRIMARY_VARIANT}
                if v in th.can_emit.get(k, ()):
                    rec["_sel"] = pair_metrics(obs, v, sel[0], sel[1], cost=cost, lock=lock)
                    rec["_conf"] = pair_metrics(obs, v, conf[0], conf[1], cost=cost, lock=lock)
                else:
                    rec["_sel"] = rec["_conf"] = None
                recs.append(rec)
    for v in VARIANTS:  # one global BH family per variant (§6.2)
        fam = [r for r in recs if r["variant"] == v and r["_sel"] is not None
               and _sel_reason(r["_sel"], th) is None]
        q = bh_qvalues([r["_sel"].p_value for r in fam])
        for r, qq in zip(fam, q, strict=True):
            r["sel_q"] = float(qq)
    rows = []
    for r in recs:
        s, c = r.pop("_sel"), r.pop("_conf")
        q = r.pop("sel_q", math.nan)
        status, reason = classify(r["variant"], r["model"], s, c, q, th)
        out = dict(r)
        for pre, pm in (("sel", s), ("conf", c)):
            out[f"{pre}_n_months"] = pm.n_months if pm else None
            out[f"{pre}_n_active"] = pm.n_active if pm else None
            out[f"{pre}_n_switches"] = pm.n_switches if pm else None
            out[f"{pre}_ann_excess"] = pm.ann_excess if pm else math.nan
            out[f"{pre}_t"] = pm.t_stat if pm else math.nan
            out[f"{pre}_p"] = pm.p_value if pm else math.nan
        out["sel_q"] = q
        out["status"], out["reason"] = status, reason
        rows.append(out)
    df = pd.DataFrame(rows, columns=FIT_TABLE_COLUMNS)
    for c in INT_COLUMNS:
        df[c] = df[c].astype("Int64")
    for c in ("sel_ann_excess", "sel_t", "sel_p", "sel_q", "conf_ann_excess", "conf_t",
              "conf_p"):
        df[c] = df[c].astype(float)
    df["is_primary"] = df["is_primary"].astype(bool)
    return df.sort_values(["ticker", "model", "variant"]).reset_index(drop=True)


# DB ----------------------------------------------------------------------------------------

def _table_exists(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?",
                            [name]).fetchone()[0])


def load_fit_frame(con) -> pd.DataFrame:
    """§7.2: stored US labels joined with horizon-1 returns, nothing after FIT_LOCK_END."""
    have = {r[0] for r in con.execute("DESCRIBE bt_rows").fetchall()}
    lab = ", ".join(f"r.{c}" if c in have else f"CAST(NULL AS VARCHAR) AS {c}"
                    for c in LABEL_COLUMNS.values())
    df = con.execute(f"""
        SELECT r.date, r.ticker, {lab}, x.ret, x.t1, x.flag
        FROM bt_rows r JOIN bt_returns x
          ON x.date = r.date AND x.ticker = r.ticker AND x.horizon = {FIT_HORIZON}
        WHERE r.universe = '{FIT_MARKET}' AND r.date <= ? AND x.t1 <= ? AND x.ret IS NOT NULL
        ORDER BY r.date, r.ticker""", [FIT_LOCK_END, FIT_LOCK_END]).df()
    df["date"] = _dates(df["date"])
    df["t1"] = _dates(df["t1"])
    df["date"] = df["date"].astype(object)
    df["t1"] = df["t1"].astype(object)
    for c in LABEL_COLUMNS.values():
        df[c] = [None if _isnull(v) else v for v in df[c]]
    assert_no_holdout(df)
    return df


def check_preconditions(con) -> str | None:
    """§10: None if the DB holds what fit reads, else the refusal message (with the hint)."""
    live = label_versions()
    if not _table_exists(con, "bt_label_versions"):
        return f"bt_label_versions missing (labels not stored for all nine models); {HINT}"
    stored = dict(con.execute("SELECT model, model_version FROM bt_label_versions "
                              "WHERE universe=?", [FIT_MARKET]).fetchall())
    diff = [f"{k}: stored {stored.get(k)}, live {v}" for k, v in live.items()
            if stored.get(k) != v]
    if diff:
        return "stored label versions differ from the live models (" + "; ".join(diff) + \
            f"); {HINT}"
    if not _table_exists(con, "bt_returns") or not con.execute(
            "SELECT count(*) FROM bt_returns WHERE horizon = ?", [FIT_HORIZON]).fetchone()[0]:
        return f"bt_returns has no horizon = 1 rows; {HINT}"
    have = {_as_date(d) for (d,) in con.execute(
        "SELECT DISTINCT date FROM bt_rows WHERE universe=? AND date <= ?",
        [FIT_MARKET, FIT_LOCK_END]).fetchall()}
    need = usable_dates(FIT_SEL_START, FIT_SEL_END) + usable_dates(FIT_CONF_START, FIT_CONF_END)
    missing = [d for d in need if d not in have]
    if missing:
        return (f"bt_rows (US) lacks {len(missing)} usable window date(s), first "
                f"{missing[0]}; {HINT}")
    return None


def to_wide(frame: pd.DataFrame) -> pd.DataFrame:
    """Loader frame (stored a_signal ... f_signal) -> fit_table wide form (label_<M>)."""
    ren = {c: f"label_{k}" for k, c in LABEL_COLUMNS.items()}
    return frame.rename(columns=ren)


def _coverage(con, start: date, end: date) -> dict:
    """Per model: share of eligible member-dates (usable window dates) with BUY/HOLD/SELL."""
    ds = usable_dates(start, end)
    have = {r[0] for r in con.execute("DESCRIBE bt_rows").fetchall()}
    out = {}
    for k, c in LABEL_COLUMNS.items():
        if c not in have or not ds:
            out[k] = (math.nan, 0)
            continue
        n, ok = con.execute(
            f"SELECT count(*), coalesce(sum(({c} IN ('BUY','HOLD','SELL'))::INT), 0) "
            "FROM bt_rows WHERE universe=? AND eligible AND date >= ? AND date <= ? "
            "AND date <= ?", [FIT_MARKET, ds[0], ds[-1], FIT_LOCK_END]).fetchone()
        out[k] = (ok / n if n else math.nan, n)
    return out


def _summary(run_id: str, df: pd.DataFrame, info: dict, cov: dict) -> str:
    sel_d = usable_dates(FIT_SEL_START, FIT_SEL_END)
    conf_d = usable_dates(FIT_CONF_START, FIT_CONF_END)
    L = [f"# Per-company model fit ({FIT_MARKET}, survivor-biased upper bound)", "",
         DISCLAIMER, "",
         "Screening signals only, not investment advice. CONFIRMED means \"worked in both "
         "in-sample windows\", not a forecast. Every result is a survivor-biased upper bound "
         "(removed index members without prices cannot be fitted). Models J, G, D, E, F are "
         "unvalidated (not frozen, not in FROZEN_V2); A, C, H failed the whole-market bar; B is "
         "diagnostic. The holdout (after 2023-10-31) is never used.", "",
         f"Run `{run_id}` ({FIT_MODEL_VERSION}), fit params hash `{fit_params_hash()}`, "
         f"backtest thresholds hash `{info['bt_thresholds_hash']}`, frozen params hash "
         f"`{info['frozen_params_hash']}`.", "",
         f"- Selection window {FIT_SEL_START} .. {FIT_SEL_END}: usable signal dates "
         f"{sel_d[0]} .. {sel_d[-1]} ({len(sel_d)}).",
         f"- Confirmation window {FIT_CONF_START} .. {FIT_CONF_END}: usable signal dates "
         f"{conf_d[0]} .. {conf_d[-1]} ({len(conf_d)}).",
         f"- Lock: no signal date and no exit session after {FIT_LOCK_END}.",
         f"- Cost {FIT_COST_BP} bp per switch; cash return {FIT_CASH_RATE}; BH q <= {FIT_Q}; "
         f"confirmation effect >= {FIT_MIN_EFFECT} a year.",
         f"- Tickers fitted: {info['n_tickers']}. BH family size: SELL_EXIT "
         f"{info['n_family_sell_exit']}, BUY_ONLY {info['n_family_buy_only']}.", "",
         "With BH at q = 0.10, about 10% of the selection passes may be false discoveries "
         "(within this family only).", "",
         "## Status count per model and variant", "",
         "| Model | Variant | " + " | ".join(STATUSES) + " |",
         "|---|---|" + "---|" * len(STATUSES)]
    for k in FIT_MODELS:
        for v in (SELL_EXIT, BUY_ONLY):
            s = df[(df["model"] == k) & (df["variant"] == v)]["status"]
            L.append(f"| {k} | {v} | " + " | ".join(str(int((s == x).sum())) for x in STATUSES)
                     + " |")
    L += ["", "## CONFIRMED SELL_EXIT pairs (primary variant)", ""]
    c = df[(df["status"] == "CONFIRMED") & (df["variant"] == SELL_EXIT)]
    if len(c):
        L += ["| Ticker | Model | sel months/active/switches | sel ann excess | sel p | sel q | "
              "conf months/active/switches | conf ann excess |",
              "|---|---|---|---|---|---|---|---|"]
        for r in c.itertuples(index=False):
            L.append(f"| {r.ticker} | {r.model} | {r.sel_n_months}/{r.sel_n_active}/"
                     f"{r.sel_n_switches} | {r.sel_ann_excess:+.4f} | {r.sel_p:.4g} | "
                     f"{r.sel_q:.4g} | {r.conf_n_months}/{r.conf_n_active}/{r.conf_n_switches} "
                     f"| {r.conf_ann_excess:+.4f} |")
    else:
        L.append("None.")
    L += ["", "## Label coverage (eligible member-dates with BUY/HOLD/SELL, usable dates)", "",
          "| Model | Selection | Confirmation | Note |", "|---|---|---|---|"]
    for k in FIT_MODELS:
        s, n_s = cov["SEL"][k]
        q, n_q = cov["CONF"][k]
        low = any(not math.isnan(x) and x < 0.5 for x in (s, q)) or math.isnan(s) or math.isnan(q)
        fs = "n/a" if math.isnan(s) else f"{s:.1%} of {n_s}"
        fq = "n/a" if math.isnan(q) else f"{q:.1%} of {n_q}"
        L.append(f"| {k} | {fs} | {fq} | {'LOW_LABEL_COVERAGE' if low else ''} |")
    L += ["", "Label coverage below 50% is information only (spec §15): check J-F inputs before "
          "reading their results.", "",
          "## Caveats", "",
          "- Survivorship: fitted companies are survivors with prices; an upper bound.",
          "- Low power per company: at most 70 + 44 monthly observations; SELL streaks cluster, "
          "so p-values are optimistic.",
          "- Absolute, not relative: SELL_EXIT rewards a SELL before any fall, market-wide "
          "falls included.",
          "- Regime dependence: 2014-2019 and 2020-2023 are two regimes, not all.",
          "- Multiple testing remains; choosing the best companies after the fact is not "
          "supported.",
          "- Screening, not advice: a highlight marks historical usefulness, not a trade."]
    return "\n".join(L) + "\n"


def run_fit(con, today: date, out_dir) -> dict:
    """Per-company evaluation from stored rows -> company_model_fit, fit_runs, CSV + summary."""
    from ..engine import _git_sha
    from ..models.frozen import FROZEN_V2, params_sha256

    msg = check_preconditions(con)
    if msg:
        raise FitPreconditionError(msg)
    for ddl in FIT_DDL:
        con.execute(ddl)
    versions = label_versions()
    frame = to_wide(load_fit_frame(con))
    tab = fit_table(frame, versions)
    run_id = uuid.uuid4().hex[:12]
    phash = fit_params_hash()
    out = tab.copy()
    out.insert(0, "market", FIT_MARKET)
    out.insert(0, "fit_params_hash", phash)
    out.insert(0, "fit_version", FIT_MODEL_VERSION)
    out.insert(0, "run_id", run_id)
    out = out[FIT_COLUMNS].sort_values(["ticker", "model", "variant"]).reset_index(drop=True)
    se, bo = out[out["variant"] == SELL_EXIT], out[out["variant"] == BUY_ONLY]
    info = {"run_id": run_id, "market": FIT_MARKET, "today": today,
            "fit_params_hash": phash, "bt_thresholds_hash": bte.thresholds_hash(),
            "frozen_params_hash": params_sha256(FROZEN_V2),
            "label_versions": versions, "n_tickers": int(out["ticker"].nunique()),
            "n_family_sell_exit": int(se["sel_q"].notna().sum()),
            "n_family_buy_only": int(bo["sel_q"].notna().sum()),
            "n_confirmed_sell_exit": int((se["status"] == "CONFIRMED").sum()),
            "n_confirmed_buy_only": int((bo["status"] == "CONFIRMED").sum())}
    con.register("_fit", out)
    con.execute(f"INSERT INTO company_model_fit ({', '.join(FIT_COLUMNS)}) "
                f"SELECT {', '.join(FIT_COLUMNS)} FROM _fit")
    con.unregister("_fit")
    con.execute("INSERT INTO fit_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [run_id, datetime.now(), FIT_MARKET, today, _git_sha(), FIT_MODEL_VERSION,
                 phash, info["bt_thresholds_hash"], info["frozen_params_hash"],
                 json.dumps(versions, sort_keys=True), info["n_tickers"],
                 info["n_family_sell_exit"], info["n_family_buy_only"],
                 info["n_confirmed_sell_exit"], info["n_confirmed_buy_only"]])
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    csv_path = od / "company_model_fit.csv"
    with open(csv_path, "w") as fh:
        fh.write(f"# {DISCLAIMER}\n")
        out.to_csv(fh, index=False, float_format="%.10g", na_rep="")
    copy = od / f"company_model_fit_{run_id}.csv"
    shutil.copyfile(csv_path, copy)
    cov = {"SEL": _coverage(con, FIT_SEL_START, FIT_SEL_END),
           "CONF": _coverage(con, FIT_CONF_START, FIT_CONF_END)}
    md = od / f"company_model_fit_{run_id}.md"
    md.write_text(_summary(run_id, out, info, cov))
    info.update(csv=str(csv_path), csv_copy=str(copy), summary=str(md),
                coverage={w: {k: v[0] for k, v in c.items()} for w, c in cov.items()})
    return info
