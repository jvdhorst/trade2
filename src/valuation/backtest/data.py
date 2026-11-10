"""Backtest database: point-in-time members, as-traded price history, EDGAR history.

Spec: docs/models/backtest.md (§Data, §Splits, §Survivorship). The backtest uses its own DuckDB
file (`data/backtest.duckdb`: live schema + `index_membership`, `splits`, `bt_ticker_coverage`)
so that the live DB's 2-year price window and refresh policy are never touched.

Price basis: Yahoo's chart `close` and dividends are split-adjusted to the download date. The
backtest stores them **as traded** (close x F(d), volume / F(d), dividend x F(ex_date), with
F(d) = product of the ratios of splits dated after d), so that price x shares-as-filed is a real
market cap on every date. `adj_close` (split + dividend adjusted) is kept as delivered: it is only
used in ratios (total returns).
"""

from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import requests

from ..providers import sec_edgar
from ..providers.base import Dividend, PriceBar
from ..providers.yahoo import ProviderError, parse_chart
from . import membership as mem

PRICE_START = date(2012, 6, 1)  # >= 273 sessions before the first rebalance (E6) + slack
EXTRA_LAG_DAYS = 30  # estimated (Yahoo/TSX) filed dates get this extra lag in backtests

COVERAGE_DDL = """CREATE TABLE IF NOT EXISTS bt_ticker_coverage (
    ticker VARCHAR PRIMARY KEY, market VARCHAR, member_source VARCHAR, has_price BOOLEAN,
    price_reason VARCHAR, first_price DATE, last_price DATE, cik VARCHAR, cik_source VARCHAR,
    sector VARCHAR, sector_source VARCHAR, n_facts INTEGER)"""


# ---------- splits / as-traded basis (pure) ----------

def parse_splits(payload: dict) -> list[tuple[date, float]]:
    res = ((payload or {}).get("chart", {}).get("result") or [{}])[0] or {}
    out = []
    for s in ((res.get("events") or {}).get("splits") or {}).values():
        num, den = s.get("numerator"), s.get("denominator")
        if num and den and s.get("date") is not None:
            out.append((datetime.fromtimestamp(s["date"], tz=UTC).date(), float(num) / float(den)))
    return sorted(out)


def later_split_factor(splits: list[tuple[date, float]], d: date) -> float:
    f = 1.0
    for sd, r in splits:
        if sd > d:
            f *= r
    return f


def as_traded(bars: list[PriceBar], divs: list[Dividend], splits: list[tuple[date, float]]
              ) -> tuple[list[PriceBar], list[Dividend]]:
    """Undo Yahoo's split adjustment of close/volume/dividends (adj_close unchanged)."""
    out_b = []
    for b in bars:
        f = later_split_factor(splits, b.date)
        out_b.append(PriceBar(b.ticker, b.date, b.close * f, b.adj_close,
                              None if b.volume is None else b.volume / f, b.source))
    out_d = [Dividend(d.ticker, d.ex_date, d.amount * later_split_factor(splits, d.ex_date),
                      d.currency, d.source) for d in divs]
    return out_b, out_d


def check_symbol(bars: list[PriceBar], intervals: list[mem.Interval]) -> str | None:
    """None if the price series plausibly belongs to the index member; else a reason code.

    A symbol Yahoo now uses for another company typically has no bars inside the old membership
    window (e.g. reused after a delisting)."""
    if not bars:
        return "NO_PRICE"
    dates = [b.date for b in bars]
    for iv in intervals:
        lo, hi = max(iv.start, PRICE_START), iv.end or date.max
        inside = [d for d in dates if lo <= d <= hi]
        if not inside:
            return "NO_BARS_IN_MEMBERSHIP"
        if (inside[0] - lo).days > 31:
            return "STARTS_AFTER_MEMBERSHIP"
    return None


# ---------- SIC -> GICS sector (coarse, removed names only) ----------

def _r(spec: str, sector: str) -> list[tuple[int, int, str]]:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        out.append((int(lo), int(hi or lo), sector))
    return out


# Spec §6: rows top to bottom, left cell before right; first match wins.
_SIC_RANGES: list[tuple[int, int, str]] = [
    *_r("1200-1399, 2900-2999", "Energy"), *_r("1000-1199, 1400-1499", "Materials"),
    *_r("2830-2836, 3841-3851, 5120-5129, 8000-8099", "Health Care"),
    *_r("2840-2844, 2000-2199, 5400-5499, 5140-5149, 5912", "Consumer Staples"),
    *_r("2700-2799, 4800-4899, 7810-7849", "Communication Services"),
    *_r("4900-4949", "Utilities"), *_r("6798, 6500-6599", "Real Estate"),
    *_r("6000-6799", "Financials"),
    *_r("3570-3579, 3600-3629, 3640-3699, 3800-3840, 3852-3899, 7370-7379",
        "Information Technology"),
    *_r("3711-3716, 3751, 3630-3639", "Consumer Discretionary"),
    *_r("2400-2499, 2600-2699, 2800-2899, 3000-3099, 3200-3399", "Materials"),
    *_r("2200-2399, 2500-2599, 3100-3199, 3900-3999, 5200-5999, 7000-7099, 7900-7999",
        "Consumer Discretionary"),
    *_r("1500-1799, 3400-3569, 3580-3599, 3700-3799, 4000-4799, 4950-4999, 5000-5199, "
        "7380-7389, 8700-8799, 7000-8999", "Industrials"),
]


def sic_to_sector(sic: int | None) -> str | None:
    if sic is None:
        return None
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    for lo, hi, sector in _SIC_RANGES:
        if lo <= n <= hi:
            return sector
    return None


# ---------- CIK by company name (EDGAR frames; removed names only) ----------

_STOP = {"inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "the", "holdings",
         "holding", "group", "incorporated", "de", "nv", "sa", "lp", "llc", "class", "a", "b",
         "new", "and", "cos", "companies", "intl", "international"}


def norm_name(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"/[A-Za-z ]+/?$", " ", name.lower())  # "CELGENE CORP /DE/"
    s = s.replace("&", " and ")
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _STOP]
    return " ".join(toks)


def name_index(frames: list[dict]) -> dict[str, set[str]]:
    """normalised entityName -> {10-digit CIK} from EDGAR frames responses."""
    idx: dict[str, set[str]] = {}
    for payload in frames:
        for d in payload.get("data", []):
            idx.setdefault(norm_name(d.get("entityName")), set()).add(str(d["cik"]).zfill(10))
    idx.pop("", None)
    return idx


def match_cik(name: str | None, idx: dict[str, set[str]]) -> str | None:
    """Unique exact match on the normalised name, else a unique prefix match; else None."""
    n = norm_name(name)
    if not n:
        return None
    exact = idx.get(n)
    if exact and len(exact) == 1:
        return next(iter(exact))
    if exact:
        return None
    pref = {c for k, ciks in idx.items() if k.startswith(n + " ") or n.startswith(k + " ")
            for c in ciks}
    return next(iter(pref)) if len(pref) == 1 else None


# ---------- build ----------

@dataclass
class Member:
    ticker: str
    market: str  # "US" | "TSX"
    name: str | None
    cik: str | None
    sector: str | None
    sub_industry: str | None
    share_classes: int
    share_conversion: float
    source: str  # current_universe | removed


def read_universe(path) -> dict[str, dict]:
    with open(path, newline="") as fh:
        return {r["ticker"]: r for r in csv.DictReader(fh)}


def bulk_insert(con, table: str, df, key: list[str]) -> int:
    """Bulk insert (DuckDB executemany is ~ms per row); duplicates on `key` keep the last row."""
    if df is None or len(df) == 0:
        return 0
    df = df.drop_duplicates(subset=key, keep="last")
    con.register("_bulk", df)
    con.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM _bulk")
    con.unregister("_bulk")
    return len(df)


def store_prices(con, results: dict[str, tuple]) -> None:
    """results: {ticker: (bars, divs, splits)} -> prices_daily, dividends, splits (replaced)."""
    import pandas as pd

    tickers = list(results)
    if not tickers:
        return
    for t in ("prices_daily", "dividends", "splits"):
        con.execute(f"DELETE FROM {t} WHERE ticker IN ({','.join('?' * len(tickers))})", tickers)
    bars = [(b.ticker, b.date, b.close, b.adj_close, b.volume, b.source)
            for bs, _, _ in results.values() for b in bs]
    divs = [(d.ticker, d.ex_date, d.amount, d.currency, d.source)
            for _, ds, _ in results.values() for d in ds]
    spl = [(t, sd, r, "yahoo_chart") for t, (_, _, ss) in results.items() for sd, r in ss]
    bulk_insert(con, "prices_daily", pd.DataFrame(bars, columns=[
        "ticker", "date", "close", "adj_close", "volume", "source"]), ["ticker", "date"])
    bulk_insert(con, "dividends", pd.DataFrame(divs, columns=[
        "ticker", "ex_date", "amount", "currency", "source"]), ["ticker", "ex_date", "source"])
    bulk_insert(con, "splits", pd.DataFrame(spl, columns=["ticker", "date", "ratio", "source"]),
                ["ticker", "date"])


def store_facts(con, facts) -> int:
    import pandas as pd

    df = pd.DataFrame([(f.ticker, f.period_end, f.filed_date, f.item, f.value, f.unit, f.source,
                        f.filed_estimated) for f in facts],
                      columns=["ticker", "period_end", "filed_date", "item", "value", "unit",
                               "source", "filed_estimated"])
    return bulk_insert(con, "fundamentals", df,
                       ["ticker", "period_end", "filed_date", "item", "source"])


def fetch_prices(yp, tickers: list[str], end: date, workers: int = 4) -> dict[str, tuple]:
    """{ticker: (bars, divs, splits, error)} on the as-traded basis; shared rate limiter."""
    def work(t):
        try:
            raw = yp.chart_period(t, PRICE_START, end)
        except (requests.HTTPError, ProviderError) as e:
            return t, ([], [], [], str(e)[:120])
        bars, divs = parse_chart(t, raw)
        splits = parse_splits(raw)
        bars, divs = as_traded(bars, divs, splits)
        return t, (bars, divs, splits, None)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(work, tickers))


def lag_estimated_filings(con, days: int = EXTRA_LAG_DAYS) -> int:
    """Backtest look-ahead guard for estimated filed dates (Yahoo/TSX): + `days`."""
    n = con.execute("SELECT count(*) FROM fundamentals WHERE filed_estimated").fetchone()[0]
    con.execute(f"UPDATE fundamentals SET filed_date = filed_date + INTERVAL {int(days)} DAY "
                "WHERE filed_estimated")
    return n


def edgar_facts(ep, ticker: str, cik: str):
    raw = ep.companyfacts(cik)
    return (sec_edgar.parse_companyfacts(ticker, raw)
            + sec_edgar.parse_quarterly_ni(ticker, raw))


def frames_for_names(ep, years=range(2012, 2026)) -> list[dict]:
    out = []
    for y in years:
        key = f"frame_{sec_edgar.EQUITY_PARENT}_CY{y}Q4I"
        raw = ep.cache.get(sec_edgar.SOURCE, key)
        if raw is None:
            try:
                raw = ep._get(sec_edgar.FRAMES_URL.format(concept=sec_edgar.EQUITY_PARENT,
                                                          frame=f"CY{y}Q4I"))
            except requests.HTTPError:
                continue
            ep.cache.put(sec_edgar.SOURCE, key, raw)
        out.append(raw)
    return out


def submissions(ep, cik: str) -> dict:
    key = f"submissions_CIK{cik}"
    raw = ep.cache.get(sec_edgar.SOURCE, key)
    if raw is None:
        full = ep._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        raw = {k: full.get(k) for k in ("cik", "name", "sic", "sicDescription", "tickers",
                                        "formerNames")}
        ep.cache.put(sec_edgar.SOURCE, key, raw)
    return raw


def first_last(bars) -> tuple[date | None, date | None]:
    return (bars[0].date, bars[-1].date) if bars else (None, None)


def week_before(d: date) -> date:
    return d - timedelta(days=7)
