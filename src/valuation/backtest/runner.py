"""Backtest orchestration: build the backtest DB, run the dates, compute forward returns.

Spec: docs/models/backtest.md. CLI: `valuation backtest build|run|report`.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from ..providers.base import RawCache
from ..store import db
from . import data as btd
from . import engine as bte
from . import membership as mem
from . import metrics as m

log = logging.getLogger(__name__)
BT_DB = Path("data/backtest.duckdb")
# The changes table moved from the constituents page to its own article on 2026-08-11.
WIKI_CHANGES = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
WIKI_CURRENT = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UA = "trade2-valuation-screener/0.1 (research backtest; low volume)"

UNIVERSE_DDL = """CREATE TABLE IF NOT EXISTS bt_universe (
    universe VARCHAR, date DATE, ticker VARCHAR, has_price BOOLEAN,
    PRIMARY KEY (universe, date, ticker))"""
RETURNS_DDL = """CREATE TABLE IF NOT EXISTS bt_returns (
    date DATE, ticker VARCHAR, horizon INTEGER, t0 DATE, t1 DATE, ret DOUBLE, flag VARCHAR,
    PRIMARY KEY (date, ticker, horizon))"""


def fetch_wiki(cache: RawCache, url: str, key: str) -> str:
    import requests

    p = cache.path("wikipedia", key).with_suffix(".html")
    if p.exists():
        return p.read_text()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(r.text)
    return r.text


def parse_current_rows(html: str) -> pd.DataFrame:
    import io

    df = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    sym = next(c for c in df.columns if "symbol" in str(c).lower())
    sec = next(c for c in df.columns if "security" in str(c).lower())
    added = next((c for c in df.columns if "added" in str(c).lower()), None)

    def dd(x):
        try:
            return pd.to_datetime(str(x)[:10]).date()
        except (ValueError, TypeError):
            return None

    return pd.DataFrame({"ticker": [mem.norm_ticker(x) for x in df[sym]],
                         "name": df[sec].astype(str),
                         "date_added": [dd(x) for x in df[added]] if added is not None
                         else None})


def build(bt_path: Path, changes_html: str, current_html: str, sp_csv: str,
          tsx_csv: str, today: date, workers: int = 4, cache: RawCache | None = None) -> dict:
    """Create/refresh the backtest DB. Returns a stats dict (also logged)."""
    from ..providers.sec_edgar import EdgarProvider
    from ..providers.yahoo import YahooProvider

    t0 = time.time()
    cache = cache or RawCache()
    yp, ep = YahooProvider(cache), EdgarProvider(cache)
    changes = mem.parse_changes(changes_html)
    live = parse_current_rows(current_html)
    intervals = mem.build_intervals(list(live["ticker"]), changes)
    names = {c.removed_ticker: c.removed_name for c in changes if c.removed_ticker}
    names.update({c.added_ticker: c.added_name for c in changes if c.added_ticker})
    names.update(dict(zip(live["ticker"], live["name"], strict=True)))

    con = db.connect(bt_path)
    for ddl in [*bte.BT_DDL, btd.COVERAGE_DDL, UNIVERSE_DDL, RETURNS_DDL]:
        con.execute(ddl)
    mem.store(con, intervals, names, today,
              f"{mem.SOURCE} + wikipedia:Historical_components_of_the_S&P_500")
    _rates_fx(con, cache, today)

    sp = btd.read_universe(sp_csv)
    tsx = btd.read_universe(tsx_csv)
    by_ticker: dict[str, list[mem.Interval]] = {}
    for iv in intervals:
        by_ticker.setdefault(iv.ticker, []).append(iv)
    us = sorted(by_ticker)
    log.info("membership: %d intervals, %d distinct US tickers since 2013", len(intervals), len(us))

    prices = btd.fetch_prices(yp, us + sorted(tsx), today, workers)
    cov, keep, iv_rows = {}, {}, []
    for t in us + sorted(tsx):
        bars, divs, splits, err = prices[t]
        reason = ("FETCH_ERROR: " + err) if err else None
        if not err and not bars:
            reason = "NO_PRICE"
        if not err and t in by_ticker:
            validity = btd.interval_validity(bars, by_ticker[t], today)
            iv_rows += [[t, iv.start, iv.end, ok] for iv, ok in validity]
            if not any(ok for _, ok in validity):
                reason = reason or "NO_BARS_IN_MEMBERSHIP"
        elif t in by_ticker:
            iv_rows += [[t, iv.start, iv.end, False] for iv in by_ticker[t]]
        if reason:
            bars, divs, splits = [], [], []
        keep[t] = (bars, divs, splits)
        fl = btd.first_last(bars)
        cov[t] = {"has_price": bool(bars), "price_reason": reason, "first": fl[0], "last": fl[1]}
    btd.store_prices(con, keep)
    con.execute(btd.INTERVAL_DDL)
    con.execute("DELETE FROM bt_interval_coverage")
    con.executemany("INSERT INTO bt_interval_coverage VALUES (?,?,?,?)", iv_rows)

    # Securities: current members from the universe files; removed US names via EDGAR names/SIC.
    frames_idx = None
    con.execute("DELETE FROM securities")
    for t in us + sorted(tsx):
        row = sp.get(t) or tsx.get(t)
        market = "TSX" if t in tsx and t not in by_ticker else "US"
        cik, cik_src, sector, sector_src, sub = None, None, None, None, None
        if row:
            cik = (row.get("cik") or "").zfill(10) if row.get("cik") else None
            cik_src = "universe" if cik else None
            sector, sector_src = row.get("gics_sector") or None, "universe"
            sub = row.get("gics_sub_industry") or None
        elif cov[t]["has_price"]:
            if frames_idx is None:
                frames_idx = btd.name_index(btd.frames_for_names(ep))
            cik = btd.match_cik(names.get(t), frames_idx)
            cik_src = "edgar_name_match" if cik else None
            if cik:
                try:
                    sub_raw = btd.submissions(ep, cik)
                    if btd.ticker_matches(sub_raw, t):
                        sector = btd.sic_to_sector(sub_raw.get("sic"))
                        sector_src = "SECTOR_FROM_SIC" if sector else "NO_SECTOR_SIC"
                    else:
                        log.info("CIK %s (%s) does not list %s: dropped", cik,
                                 sub_raw.get("name"), t)
                        cik, cik_src = None, "NAME_MATCH_TICKER_MISMATCH"
                except Exception as e:  # noqa: BLE001 - recorded, not fatal
                    log.warning("submissions %s %s: %s", t, cik, e)
        currency = "CAD" if market == "TSX" else "USD"
        con.execute(
            "INSERT INTO securities (ticker, exchange, name, currency, financial_currency, "
            "sector, industry, cik, share_classes, share_conversion, updated_at, gics_sector, "
            "gics_sub_industry) VALUES (?,?,?,?,?,?,?,?,?,?,now(),?,?)",
            [t, market, names.get(t) or (row or {}).get("name"), currency, currency, sector, None,
             cik, int((row or {}).get("share_classes") or 1),
             float((row or {}).get("share_conversion") or 1.0), sector, sub])
        cov[t].update({"market": market, "cik": cik, "cik_source": cik_src, "sector": sector,
                       "sector_source": sector_src,
                       "member_source": "current" if row else "removed"})

    # Fundamentals: EDGAR history for US names with prices; TSX Yahoo facts from the live DB.
    con.execute("DELETE FROM fundamentals")
    todo = [(t, cov[t]["cik"]) for t in us if cov[t]["has_price"] and cov[t]["cik"]]

    def work(item):
        t, cik = item
        try:
            return t, btd.edgar_facts(ep, t, cik), None
        except Exception as e:  # noqa: BLE001
            return t, [], str(e)[:120]

    n_err, all_facts = 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for t, facts, err in ex.map(work, todo):
            if err:
                n_err += 1
                log.warning("EDGAR %s: %s", t, err)
            all_facts += facts
            cov[t]["n_facts"] = len(facts)
    btd.store_facts(con, all_facts)
    tsx_list = sorted(tsx)
    tsx_facts = []
    for t in tsx_list:  # Yahoo fundamentals-timeseries (same cached call as the live ingest)
        if not cov[t]["has_price"]:
            continue
        try:
            tsx_facts += yp.fundamentals(t, "CAD")
        except Exception as e:  # noqa: BLE001 - recorded, not fatal
            log.warning("TSX fundamentals %s: %s", t, e)
    btd.store_facts(con, tsx_facts)
    n_lag = btd.lag_estimated_filings(con)

    con.execute("DELETE FROM bt_ticker_coverage")
    con.executemany("INSERT INTO bt_ticker_coverage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        [t, c.get("market"), c.get("member_source"), c["has_price"], c["price_reason"],
         c["first"], c["last"], c.get("cik"), c.get("cik_source"), c.get("sector"),
         c.get("sector_source"), c.get("n_facts")] for t, c in cov.items()])
    con.close()
    stats = {"intervals": len(intervals), "us_tickers": len(us), "tsx_tickers": len(tsx_list),
             "us_with_price": sum(cov[t]["has_price"] for t in us),
             "removed_with_price": sum(cov[t]["has_price"] for t in us if t not in sp),
             "removed_total": sum(1 for t in us if t not in sp),
             "edgar_errors": n_err, "estimated_facts_lagged": n_lag,
             "seconds": round(time.time() - t0, 1)}
    log.info("backtest DB built: %s", stats)
    return stats


def _rates_fx(con, cache: RawCache, today: date) -> None:
    """10y yields and USD/CAD back to 2012 through the live providers (cached)."""
    import pandas as pd

    from ..providers import boc, rates

    days = (today - date(2012, 6, 1)).days
    rp, bp = rates.RatesProvider(cache), boc.BocProvider(cache)
    rows = (rates.parse_boc_yield(rp.boc_10y(today, days))
            + rates.parse_fred_csv(rp.fred_10y(today, days)))
    btd.bulk_insert(con, "rates_daily", pd.DataFrame(rows, columns=[
        "date", "series", "rate", "source"]), ["date", "series"])
    fx = [(d, "USDCAD", r) for d, r in boc.parse_observations(bp.usdcad(today, days))]
    btd.bulk_insert(con, "fx_daily", pd.DataFrame(fx, columns=["date", "pair", "rate"]),
                    ["date", "pair"])


def members_by_date(con, universe: str, dates: list[date]) -> dict[date, list[str]]:
    """US: point-in-time S&P 500 members with prices and a security row. TSX: current
    constituents (survivor-biased). Records every member-date in bt_universe."""
    cov = {t: hp for t, hp in con.execute(
        "SELECT ticker, has_price FROM bt_ticker_coverage").fetchall()}
    con.execute(UNIVERSE_DDL)
    con.execute("DELETE FROM bt_universe WHERE universe=?", [universe])
    out, rows = {}, []
    if universe == "US":
        valid = [mem.Interval(t, s, e) for t, s, e in con.execute(
            "SELECT ticker, start_date, end_date FROM bt_interval_coverage WHERE valid").fetchall()]
        intervals = mem.load(con)
        for d in dates:
            ms = sorted(mem.members_on(intervals, d))
            ok = mem.members_on(valid, d)
            rows += [[universe, d, t, bool(cov.get(t)) and t in ok] for t in ms]
            out[d] = [t for t in ms if cov.get(t) and t in ok]
    else:
        tsx = [t for t, mk in con.execute(
            "SELECT ticker, market FROM bt_ticker_coverage").fetchall() if mk == "TSX"]
        for d in dates:
            rows += [[universe, d, t, bool(cov.get(t))] for t in tsx]
            out[d] = [t for t in tsx if cov.get(t)]
    con.executemany("INSERT INTO bt_universe VALUES (?,?,?,?)", rows)
    return out


def compute_returns(con, dates: list[date]) -> int:
    con.execute("DROP TABLE IF EXISTS bt_returns")
    con.execute(RETURNS_DDL)
    prices = con.execute("SELECT ticker, date, adj_close FROM prices_daily").df()
    prices["date"] = [d.date() if hasattr(d, "date") else d for d in prices["date"]]
    fr = m.forward_returns(prices, dates)
    con.execute("DELETE FROM bt_returns")
    con.register("_fr", fr)
    con.execute("INSERT INTO bt_returns SELECT date, ticker, horizon, t0, t1, ret, flag FROM _fr")
    con.unregister("_fr")
    return len(fr)


def connect_bt(path: Path = BT_DB) -> duckdb.DuckDBPyConnection:
    return db.connect(path)
