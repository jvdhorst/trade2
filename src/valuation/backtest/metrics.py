"""Backtest metrics (pure pandas/numpy). Spec: docs/models/backtest.md §4.

Frames are long; dates are `datetime.date`. `panel`: date, ticker, sector, eligible, label, score.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import rankdata

HORIZONS = (3, 6, 12)
ENTRY_MAX_DAYS = 5
MIN_SECTOR = 5
MIN_IC_NAMES = 20
PASS_YEAR_SHARE, PASS_IC, PASS_T, PASS_HORIZON = 0.60, 0.03, 2.0, 12
BOOK_SECTORS = ("Financials", "Utilities", "Real Estate", "Energy", "Materials")
BASELINE_BUY_PCT, BASELINE_SELL_PCT, BASELINE_MIN = 0.20, 0.80, 5
SIGNALS = ("BUY", "HOLD", "SELL")
NO_ENTRY, INCOMPLETE, EARLY_EXIT, NO_EXIT = "NO_ENTRY", "INCOMPLETE", "EARLY_EXIT", "NO_EXIT"


def _add_months(d: date, n: int) -> date:
    from .engine import add_months

    return add_months(d, n)


def _allowed(df: pd.DataFrame, allowed_dates) -> pd.DataFrame:
    if allowed_dates is None:
        return df
    s = set(allowed_dates)
    return df[df["date"].isin(s)]


# 4.1 ------------------------------------------------------------------------------------------
def forward_returns(prices: pd.DataFrame, dates: Iterable[date],
                    horizons: Sequence[int] = HORIZONS) -> pd.DataFrame:
    """-> date, ticker, horizon, t0, t1, ret, flag (one row per date x ticker x horizon)."""
    cols = ["date", "ticker", "horizon", "t0", "t1", "ret", "flag"]
    dates = sorted(set(dates))
    p = prices[prices["adj_close"].notna() & (prices["adj_close"] > 0)]
    if p.empty:
        return pd.DataFrame(columns=cols)
    p = p.sort_values(["ticker", "date"])
    last_panel = p["date"].max()
    out = []
    for t, g in p.groupby("ticker", sort=True):
        ds = g["date"].tolist()
        dn = np.array(ds, dtype="datetime64[D]")
        px = g["adj_close"].to_numpy(dtype=float)
        for d in dates:
            i0 = int(np.searchsorted(dn, np.datetime64(d, "D"), side="right"))
            no_entry = i0 >= len(ds) or (ds[i0] - d).days > ENTRY_MAX_DAYS
            for h in horizons:
                if no_entry:
                    out.append((d, t, h, None, None, math.nan, NO_ENTRY))
                    continue
                target = _add_months(d, h)
                if target >= last_panel:
                    out.append((d, t, h, ds[i0], None, math.nan, INCOMPLETE))
                    continue
                i1 = int(np.searchsorted(dn, np.datetime64(target, "D"), side="right"))
                if i1 < len(ds):
                    out.append((d, t, h, ds[i0], ds[i1], px[i1] / px[i0] - 1.0, None))
                elif len(ds) - 1 > i0:
                    out.append((d, t, h, ds[i0], ds[-1], px[-1] / px[i0] - 1.0, EARLY_EXIT))
                else:
                    out.append((d, t, h, ds[i0], None, math.nan, NO_EXIT))
    return pd.DataFrame(out, columns=cols)


# 4.2 ------------------------------------------------------------------------------------------
def sector_excess(fwd: pd.DataFrame, panel: pd.DataFrame,
                  min_sector: int = MIN_SECTOR) -> pd.DataFrame:
    """panel x horizon, joined with fwd; + n_sector, sector_median, excess."""
    horizons = sorted(fwd["horizon"].unique()) if len(fwd) else list(HORIZONS)
    base = panel.merge(pd.DataFrame({"horizon": horizons}), how="cross")
    x = base.merge(fwd[["date", "ticker", "horizon", "ret", "flag"]],
                   on=["date", "ticker", "horizon"], how="left")
    valid = (x["eligible"] == True) & x["sector"].notna() & x["ret"].notna()  # noqa: E712
    v = x[valid]
    g = v.groupby(["date", "horizon", "sector"])["ret"]
    stats = pd.DataFrame({"n_sector": g.size(),
                          "sector_median": g.agg(lambda r: float(np.median(r)))}).reset_index()
    stats.loc[stats["n_sector"] < min_sector, "sector_median"] = math.nan
    x = x.merge(stats, on=["date", "horizon", "sector"], how="left")
    x["n_sector"] = x["n_sector"].fillna(0).astype(int)
    x["excess"] = np.where(valid, x["ret"] - x["sector_median"], math.nan)
    return x


# 4.3 ------------------------------------------------------------------------------------------
def spread_by_date(obs: pd.DataFrame, allowed_dates=None) -> pd.DataFrame:
    cols = ["date", "horizon", "n_buy", "n_sell", "buy_excess", "sell_excess", "spread"]
    obs = _allowed(obs, allowed_dates)
    rows = []
    for (d, h), g in obs.groupby(["date", "horizon"], sort=True):
        b = g.loc[(g["label"] == "BUY") & g["excess"].notna(), "excess"]
        s = g.loc[(g["label"] == "SELL") & g["excess"].notna(), "excess"]
        be = float(b.mean()) if len(b) else math.nan
        se = float(s.mean()) if len(s) else math.nan
        rows.append((d, h, len(b), len(s), be, se, be - se if len(b) and len(s) else math.nan))
    return pd.DataFrame(rows, columns=cols)


def yearly_spread(by_date: pd.DataFrame) -> pd.DataFrame:
    x = by_date.dropna(subset=["spread"]).copy()
    if x.empty:
        return pd.DataFrame(columns=["year", "horizon", "n_dates", "spread"])
    x["year"] = [d.year for d in x["date"]]
    g = x.groupby(["year", "horizon"])["spread"]
    return pd.DataFrame({"n_dates": g.size(), "spread": g.mean()}).reset_index()


# 4.4 ------------------------------------------------------------------------------------------
def hit_rates(obs: pd.DataFrame, allowed_dates=None) -> pd.DataFrame:
    obs = _allowed(obs, allowed_dates)
    rows = []
    for h, g in obs.groupby("horizon", sort=True):
        b = g.loc[(g["label"] == "BUY") & g["excess"].notna(), "excess"]
        s = g.loc[(g["label"] == "SELL") & g["excess"].notna(), "excess"]
        rows.append((h, len(b), float((b > 0).mean()) if len(b) else math.nan,
                     len(s), float((s < 0).mean()) if len(s) else math.nan))
    return pd.DataFrame(rows, columns=["horizon", "n_buy", "buy_hit_rate", "n_sell",
                                       "sell_hit_rate"])


# 4.5 ------------------------------------------------------------------------------------------
def spearman(x, y) -> float:
    rx = rankdata(np.asarray(x, dtype=float), method="average")
    ry = rankdata(np.asarray(y, dtype=float), method="average")
    if len(rx) < 2 or np.all(rx == rx[0]) or np.all(ry == ry[0]):
        return math.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def rank_ic(obs: pd.DataFrame, allowed_dates=None, min_names: int = MIN_IC_NAMES) -> pd.DataFrame:
    obs = _allowed(obs, allowed_dates)
    rows = []
    for (d, h), g in obs.groupby(["date", "horizon"], sort=True):
        v = g[(g["eligible"] == True) & g["score"].notna() & g["ret"].notna()]  # noqa: E712
        n = len(v)
        ic = spearman(v["score"], v["ret"]) if n >= min_names else math.nan
        rows.append((d, h, n, ic))
    return pd.DataFrame(rows, columns=["date", "horizon", "n", "ic"])


# 4.6 ------------------------------------------------------------------------------------------
def newey_west_t(x: Sequence[float], lag: int) -> float:
    v = np.asarray([a for a in x if a is not None and not (isinstance(a, float) and math.isnan(a))],
                   dtype=float)
    n = len(v)
    if n <= lag + 1:
        return math.nan
    m = float(v.mean())
    e = v - m
    var = float(e @ e) / n
    for k in range(1, lag + 1):
        var += 2.0 * (1.0 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    if not var > 0:
        return math.nan
    return m / math.sqrt(var / n)


# 4.7 ------------------------------------------------------------------------------------------
def turnover(labels: pd.DataFrame, allowed_dates=None) -> pd.DataFrame:
    cols = ["date", "label_turnover", "buy_turnover", "sell_turnover"]
    labels = _allowed(labels, allowed_dates)
    by = {d: dict(zip(g["ticker"], g["label"], strict=True))
          for d, g in labels.groupby("date", sort=True)}
    ds = sorted(by)
    rows = []
    for prev, cur in zip(ds, ds[1:], strict=False):
        a, b = by[prev], by[cur]
        common = [t for t in b if t in a and a[t] in SIGNALS and b[t] in SIGNALS]
        lab = sum(a[t] != b[t] for t in common) / len(common) if common else math.nan
        res = [cur, lab]
        for s in ("BUY", "SELL"):
            now = {t for t, v in b.items() if v == s}
            before = {t for t, v in a.items() if v == s}
            res.append(len(now - before) / len(now) if now else math.nan)
        rows.append(res)
    return pd.DataFrame(rows, columns=cols)


# 4.8 ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PassResult:
    result: str
    failed_legs: tuple[str, ...]
    incomplete_legs: tuple[str, ...]


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def pass_bar(yearly_spreads_12: Sequence[float], ic_mean: float, ic_t: float,
             holdout_ic_mean: float | None = None,
             holdout_spread: float | None = None) -> PassResult:
    ys = [float(y) for y in yearly_spreads_12 if not _nan(y)]
    failed, incomplete = [], []
    if not ys:
        incomplete.append("SPREAD_YEARS")
    elif not sum(y > 0 for y in ys) / len(ys) >= PASS_YEAR_SHARE:
        failed.append("SPREAD_YEARS")
    if _nan(ic_mean):
        incomplete.append("IC")
    elif not (ic_mean > PASS_IC and not _nan(ic_t) and ic_t > PASS_T):
        failed.append("IC")
    if _nan(holdout_ic_mean) or _nan(holdout_spread) or _nan(ic_mean) or not ys:
        incomplete.append("HOLDOUT")
    elif not (np.sign(holdout_ic_mean) == np.sign(ic_mean)
              and np.sign(holdout_spread) == np.sign(sum(ys) / len(ys))):
        failed.append("HOLDOUT")
    result = "FAIL" if failed else "INCOMPLETE" if incomplete else "PASS"
    return PassResult(result, tuple(failed), tuple(incomplete))


# 4.9 ------------------------------------------------------------------------------------------
def low_pb_baseline(panel_pb: pd.DataFrame, min_sector: int = BASELINE_MIN) -> pd.DataFrame:
    """panel_pb: date, ticker, sector, eligible, price, bvps -> + pb, pct, label, score."""
    x = panel_pb[["date", "ticker", "sector", "eligible", "price", "bvps"]].copy()
    valid = ((x["eligible"] == True) & x["sector"].isin(BOOK_SECTORS)  # noqa: E712
             & x["bvps"].notna() & (x["bvps"] > 0) & x["price"].notna() & (x["price"] > 0))
    x["pb"] = np.where(valid, x["price"] / x["bvps"].where(x["bvps"] > 0), math.nan)
    x["pct"] = math.nan
    x["label"] = np.where(x["eligible"] == True, "NO_SIGNAL", "NOT_ELIGIBLE")  # noqa: E712
    for _, idx in x[valid].groupby(["date", "sector"]).groups.items():
        n = len(idx)
        if n < min_sector:
            continue
        pct = (rankdata(x.loc[idx, "pb"].to_numpy(float), method="average") - 1) / (n - 1)
        x.loc[idx, "pct"] = pct
        x.loc[idx, "label"] = np.where(pct <= BASELINE_BUY_PCT, "BUY",
                                       np.where(pct >= BASELINE_SELL_PCT, "SELL", "HOLD"))
    x["score"] = np.where(x["pct"].notna(), -np.log(x["pb"].where(x["pct"].notna(), 1.0)),
                          math.nan)
    return x[["date", "ticker", "sector", "pb", "pct", "label", "score"]].reset_index(drop=True)


# ---------- benchmark excess (spec docs/models/benchmarks.md, API contract; P8-10) ----------

EXCESS_BUCKETS = ("BUY", "SELL", "ALL")


def benchmark_excess(obs: pd.DataFrame, bench_fwd: pd.DataFrame,
                     allowed_dates=None) -> pd.DataFrame:
    """Per (allowed date, horizon, bucket): equal-weighted mean forward return of the bucket's
    names (non-NaN ret) minus the benchmark's forward return over the same window.
    obs: date, ticker, horizon, label, ret (caller passes eligible names only);
    bench_fwd: date, horizon, ret. ALL = every obs row with a non-NaN ret."""
    cols = ["date", "horizon", "bucket", "n", "bucket_ret", "bench_ret", "excess"]
    o = obs[obs["ret"].notna()]
    dates = sorted(set(obs["date"]) if allowed_dates is None else set(allowed_dates))
    horizons = sorted(set(obs["horizon"]) | set(bench_fwd["horizon"]))
    bench = {(d, h): r for d, h, r in bench_fwd[["date", "horizon", "ret"]].itertuples(index=False)}
    grp = {k: g["ret"].to_numpy() for k, g in o.groupby(["date", "horizon"])}
    lab = {k: g for k, g in o.groupby(["date", "horizon"])}
    rows = []
    for d in dates:
        for h in horizons:
            br = bench.get((d, h), np.nan)
            br = np.nan if br is None else float(br)
            for b in EXCESS_BUCKETS:
                if (d, h) not in grp:
                    vals = np.array([])
                elif b == "ALL":
                    vals = grp[(d, h)]
                else:
                    g = lab[(d, h)]
                    vals = g.loc[g["label"] == b, "ret"].to_numpy()
                n = len(vals)
                mean = float(vals.mean()) if n else np.nan
                ex = mean - br if n and not np.isnan(br) else np.nan
                rows.append([d, h, b, n, mean, br, ex])
    return pd.DataFrame(rows, columns=cols)


def yearly_benchmark_excess(by_date: pd.DataFrame) -> pd.DataFrame:
    x = by_date[by_date["excess"].notna()].copy()
    x["year"] = [d.year for d in x["date"]]
    out = (x.groupby(["year", "horizon", "bucket"])["excess"].agg(["size", "mean"])
           .reset_index().rename(columns={"size": "n_dates", "mean": "excess"}))
    return out[["year", "horizon", "bucket", "n_dates", "excess"]]
