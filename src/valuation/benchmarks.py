"""Market benchmark yields and returns. Spec: docs/models/benchmarks.md (v0.1.1).

Context for the screening signals, not signals themselves and not investment advice. Prices and
distributions come from the Yahoo chart API (existing provider); everything is point in time
(levels and ex-dates <= as_of).
"""

from __future__ import annotations

import calendar
import csv
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

HORIZONS_M = (1, 3, 6, 12, 36, 60)
SPECIAL_DIST_MULT = 2.0
BENCHMARKS_CSV = Path("universe/benchmarks.csv")
DDL = [
    """CREATE TABLE IF NOT EXISTS benchmarks_daily (
        date DATE, key VARCHAR, close DOUBLE, adj_close DOUBLE, dist_ttm DOUBLE,
        dist_yield DOUBLE, rf_10y DOUBLE, yield_spread DOUBLE, PRIMARY KEY (date, key))""",
    """CREATE TABLE IF NOT EXISTS benchmark_dists (
        key VARCHAR, ex_date DATE, amount DOUBLE, currency VARCHAR, PRIMARY KEY (key, ex_date))""",
]
RF_SERIES = {"US": "US10Y", "TSX": "CA10Y"}


@dataclass(frozen=True)
class Benchmark:
    key: str
    symbol: str
    market: str
    role: str
    note: str = ""


def load_benchmarks(path: str | Path = BENCHMARKS_CSV) -> list[Benchmark]:
    with open(path, newline="") as fh:
        return [Benchmark(r["key"].strip(), r["symbol"].strip(), r["market"].strip(),
                          r["role"].strip(), (r.get("note") or "").strip())
                for r in csv.DictReader(fh) if (r.get("key") or "").strip()]


def primary_return_benchmark(market: str, benchmarks: list[Benchmark] | None = None) -> str:
    for b in benchmarks or load_benchmarks():
        if b.market == market and b.role.startswith("primary_return"):
            return b.key
    raise KeyError(f"no primary return benchmark for {market}")


def _window(dists: Iterable[tuple[date, float]], as_of: date) -> list[float]:
    start = as_of - timedelta(days=365)
    return [a for d, a in dists if start < d <= as_of]


def dist_ttm(dists: Iterable[tuple[date, float]], as_of: date) -> float:
    """Sum of distributions with as_of - 365d < ex_date <= as_of (open on the left)."""
    return float(sum(_window(dists, as_of)))


def dist_yield(dist_ttm_: float | None, close: float | None) -> float | None:
    if dist_ttm_ is None or close is None or close <= 0:
        return None
    return dist_ttm_ / close


def yield_spread(dist_yield_: float | None, rf: float | None) -> float | None:
    if dist_yield_ is None or rf is None:
        return None
    return dist_yield_ - rf


def months_back(d: date, h: int) -> date:
    """Same day of the month h months earlier, clamped to that month's last day."""
    k = d.year * 12 + (d.month - 1) - h
    y, m = k // 12, k % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def level_on(levels: Iterable[tuple[date, float]], d: date) -> float | None:
    """Level of the last date <= d (None if none)."""
    best: tuple[date, float] | None = None
    for dt, lv in levels:
        if dt <= d and lv is not None and (best is None or dt > best[0]):
            best = (dt, lv)
    return None if best is None else best[1]


def total_return(levels: Iterable[tuple[date, float]], as_of: date, months: int) -> float | None:
    levels = list(levels)
    start = months_back(as_of, months)
    l0, l1 = level_on(levels, start), level_on(levels, as_of)
    if l0 is None or l1 is None or l0 <= 0:
        return None
    return l1 / l0 - 1


def annualize(tr: float | None, months: int) -> float | None:
    if tr is None or months < 12 or tr <= -1:
        return None
    return (1 + tr) ** (12 / months) - 1


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def excess(bucket_mean: float | None, benchmark_tr: float | None) -> float | None:
    if _nan(bucket_mean) or _nan(benchmark_tr):
        return None
    return bucket_mean - benchmark_tr


def special_dist(amounts: Iterable[float]) -> bool:
    """True if any payment is more than 2x the median of the *other* payments (>= 2 payments)."""
    a = list(amounts)
    if len(a) < 2:
        return False
    return any(x > SPECIAL_DIST_MULT * statistics.median(a[:i] + a[i + 1:])
               for i, x in enumerate(a))


def benchmark_metrics(levels: Iterable[tuple[date, float, float]],
                      dists: Iterable[tuple[date, float]], as_of: date,
                      rf: float | None = None) -> dict:
    """Header metrics for one benchmark on as_of. levels = (date, close, adj_close) triples."""
    levels = [x for x in levels if x[0] <= as_of]
    dists = [x for x in dists if x[0] <= as_of]
    close = level_on([(d, c) for d, c, _ in levels], as_of)
    adj = [(d, a if a is not None else c) for d, c, a in levels]
    dt = dist_ttm(dists, as_of) if dists else None
    dy = dist_yield(dt, close) if dists else None
    out = {"close": close, "dist_ttm": dt, "dist_yield": dy, "yield_spread": yield_spread(dy, rf),
           "special_dist": special_dist(_window(dists, as_of))}
    for h in (1, 3, 6, 12):
        out[f"tr_{h}m"] = total_return(adj, as_of, h)
    for h, name in ((36, "tr_ann_3y"), (60, "tr_ann_5y")):
        out[name] = annualize(total_return(adj, as_of, h), h)
    return out


# ---------- ingest + report (DB side) ----------

def ensure_schema(con) -> None:
    for ddl in DDL:
        con.execute(ddl)


def ingest_benchmarks(con, yp, start: date, end: date,
                      benchmarks: list[Benchmark] | None = None) -> dict[str, str]:
    """Daily bars + distributions for every benchmark via the chart API (period1/period2, 1d),
    then benchmarks_daily with dist_ttm / dist_yield / rf_10y / yield_spread per date.
    Returns {key: error} for failed symbols."""
    import pandas as pd

    from .providers.yahoo import parse_chart

    ensure_schema(con)
    errors: dict[str, str] = {}
    for b in benchmarks or load_benchmarks():
        try:
            bars, divs = parse_chart(b.symbol, yp.chart_period(b.symbol, start, end))
        except Exception as e:  # noqa: BLE001 - recorded, not fatal
            errors[b.key] = str(e)[:120]
            continue
        bars = [x for x in bars if x.date <= end]
        divs = sorted((d.ex_date, d.amount, d.currency) for d in divs if d.ex_date <= end)
        is_etf = b.symbol[0] != "^"  # indices pay no distributions
        rates = dict(con.execute("SELECT date, rate FROM rates_daily WHERE series=? "
                                 "ORDER BY date", [RF_SERIES.get(b.market, "")]).fetchall())
        rate_dates = sorted(rates)
        rows, j, last_rf = [], 0, None
        for x in sorted(bars, key=lambda x: x.date):
            while j < len(rate_dates) and rate_dates[j] <= x.date:
                last_rf = rates[rate_dates[j]]
                j += 1
            dt = dist_ttm([(d, a) for d, a, _ in divs], x.date) if is_etf else None
            dy = dist_yield(dt, x.close) if is_etf else None
            rows.append((x.date, b.key, x.close, x.adj_close, dt, dy, last_rf,
                         yield_spread(dy, last_rf)))
        cur = con.cursor()
        cur.execute("DELETE FROM benchmarks_daily WHERE key=?", [b.key])
        cur.execute("DELETE FROM benchmark_dists WHERE key=?", [b.key])
        if rows:
            cur.register("_b", pd.DataFrame(rows, columns=[
                "date", "key", "close", "adj_close", "dist_ttm", "dist_yield", "rf_10y",
                "yield_spread"]))
            cur.execute("INSERT INTO benchmarks_daily SELECT * FROM _b").fetchall()
            cur.unregister("_b")
        if divs and is_etf:
            cur.register("_d", pd.DataFrame([(b.key, d, a, c) for d, a, c in divs],
                                            columns=["key", "ex_date", "amount", "currency"])
                         .drop_duplicates(subset=["key", "ex_date"], keep="last"))
            cur.execute("INSERT INTO benchmark_dists SELECT * FROM _d").fetchall()
            cur.unregister("_d")
        cur.close()
    return errors


def benchmark_table(con, as_of: date, benchmarks: list[Benchmark] | None = None):
    """One row per benchmark: key, symbol, market, role + benchmark_metrics on as_of."""
    import pandas as pd

    ensure_schema(con)
    rows = []
    for b in benchmarks or load_benchmarks():
        levels = con.execute("SELECT date, close, adj_close FROM benchmarks_daily WHERE key=? "
                             "AND date<=? ORDER BY date", [b.key, as_of]).fetchall()
        dists = con.execute("SELECT ex_date, amount FROM benchmark_dists WHERE key=? "
                            "AND ex_date<=?", [b.key, as_of]).fetchall()
        rf = con.execute("SELECT rate FROM rates_daily WHERE series=? AND date<=? ORDER BY date "
                         "DESC LIMIT 1", [RF_SERIES.get(b.market, ""), as_of]).fetchone()
        m = benchmark_metrics(levels, dists if b.symbol[0] != "^" else [], as_of,
                              rf[0] if rf else None)
        rows.append({"key": b.key, "symbol": b.symbol, "market": b.market, "role": b.role,
                     "as_of_close_date": levels[-1][0] if levels else None,
                     "rf_10y": rf[0] if rf else None, **m})
    return pd.DataFrame(rows)


def header_lines(table) -> list[str]:
    """Comment lines for the screen_signals header (yields % 2 dp, returns % 1 dp)."""
    def pct(x, dp):
        return "n/a" if _nan(x) else f"{100 * x:.{dp}f}%"

    lines = ["Benchmarks (spec docs/models/benchmarks.md; context, not signals; TSX total return "
             "= XIC adj_close, net of its fee):"]
    for r in table.to_dict("records"):
        y = "" if _nan(r["dist_yield"]) else (
            f" yield {pct(r['dist_yield'], 2)} (vs 10y {pct(r['yield_spread'], 2)})"
            + (" SPECIAL_DIST" if r["special_dist"] else ""))
        lines.append(f"  {r['key']} ({r['symbol']}, {r['role']}): 1m {pct(r['tr_1m'], 1)}, "
                     f"12m {pct(r['tr_12m'], 1)}, 3y ann {pct(r['tr_ann_3y'], 1)}, "
                     f"5y ann {pct(r['tr_ann_5y'], 1)}{y}")
    return lines
