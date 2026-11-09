"""Daily ingestion: universe -> securities, prices, fundamentals, FX, reference metrics."""

from __future__ import annotations

import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import astuple
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from ..providers import boc, rates, sec_edgar, yahoo
from ..providers.base import Dividend, Fact, PriceBar, RawCache

log = logging.getLogger(__name__)


def load_universe(path: str | Path) -> list[dict]:
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("ticker")]


def upsert_facts(con: duckdb.DuckDBPyConnection, facts: list[Fact]) -> None:
    if facts:
        con.executemany(
            "INSERT OR REPLACE INTO fundamentals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [astuple(f) for f in facts],
        )


def upsert_prices(con: duckdb.DuckDBPyConnection, bars: list[PriceBar]) -> None:
    """Upsert bars. A spark bar (no volume/adj_close) never clears what a chart bar stored."""
    if bars:
        con.executemany(
            "INSERT INTO prices_daily VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ticker, date) DO UPDATE "
            "SET close = excluded.close, "
            "adj_close = coalesce(excluded.adj_close, prices_daily.adj_close), "
            "volume = coalesce(excluded.volume, prices_daily.volume), "
            "source = CASE WHEN excluded.volume IS NULL AND prices_daily.volume IS NOT NULL "
            "THEN prices_daily.source ELSE excluded.source END",
            [astuple(b) for b in bars],
        )


def upsert_dividends(con: duckdb.DuckDBPyConnection, divs: list[Dividend]) -> None:
    if divs:
        con.executemany("INSERT OR REPLACE INTO dividends VALUES (?, ?, ?, ?, ?)",
                        [astuple(d) for d in divs])


def upsert_security(con, ticker: str, row: dict, profile: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO securities (ticker, exchange, name, currency, financial_currency,"
        " sector, industry, cik, share_classes, share_conversion, updated_at, gics_sector,"
        " gics_sub_industry) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [ticker, row.get("exchange") or profile.get("exchange"), profile.get("name"),
         profile.get("currency"), profile.get("financial_currency"), profile.get("sector"),
         profile.get("industry"), row.get("cik") or None, int(row.get("share_classes") or 1),
         float(row.get("share_conversion") or 1), datetime.now(),
         row.get("gics_sector") or None, row.get("gics_sub_industry") or None],
    )


def update_universe_fields(con, ticker: str, row: dict) -> None:
    """Universe-file fields change without a profile refresh (GICS, share classes, CIK)."""
    con.execute(
        "UPDATE securities SET cik=?, share_classes=?, share_conversion=?, gics_sector=?, "
        "gics_sub_industry=? WHERE ticker=?",
        [row.get("cik") or None, int(row.get("share_classes") or 1),
         float(row.get("share_conversion") or 1), row.get("gics_sector") or None,
         row.get("gics_sub_industry") or None, ticker],
    )


# Refresh policy: prices daily; fundamentals when due; profiles monthly.
FUNDAMENTALS_MAX_AGE = timedelta(days=7)
NEW_QUARTER_DUE = timedelta(days=91 + 45)  # next quarter end + filing lag
PROFILE_MAX_AGE = timedelta(days=30)
# Price history (chart API): one full backfill, then 5 days a day. 2y, not 13mo: a 13-month range
# returns ~272 sessions, one short of eligibility rule E6 (273).
HISTORY_BACKFILL_RANGE = "2y"
HISTORY_DAILY_RANGE = "5d"
HISTORY_MAX_GAP = timedelta(days=7)  # last volume bar older than this -> backfill again
RATES_BACKFILL_DAYS = 13 * 366  # first load: back to 2013 for the backtest
RATES_DAILY_DAYS = 30


def _fetch_log(con) -> dict[tuple[str, str], tuple[datetime, str | None]]:
    rows = con.execute("SELECT ticker, source, fetched_at, marker FROM fetch_log").fetchall()
    return {(t, s): (f, m) for t, s, f, m in rows}


def _log_fetch(con, ticker: str, source: str, now: datetime, marker: str | None = None) -> None:
    con.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?, ?)",
                [ticker, source, now, marker])


def yahoo_fundamentals_due(last_fetch: datetime | None, latest_period: date | None,
                           now: datetime, force: bool = False) -> bool:
    if force or last_fetch is None or latest_period is None:
        return True
    if now - last_fetch >= FUNDAMENTALS_MAX_AGE:
        return True
    # A new quarter is probably out: re-check daily until it shows up.
    return now.date() - latest_period >= NEW_QUARTER_DUE and now - last_fetch >= timedelta(days=1)


def history_needs_backfill(last_full: datetime | None, last_volume_date: date | None,
                           as_of: date, force: bool = False) -> bool:
    """Full chart refetch: never backfilled, or a gap the 5-day window can't cover."""
    return (force or last_full is None or last_volume_date is None
            or as_of - last_volume_date > HISTORY_MAX_GAP)


def events_changed(new_marker: str, stored_marker: str | None) -> bool:
    """A dividend/split newer than the last one seen rebases adj_close: refetch the full range."""
    return bool(new_marker) and (not stored_marker or int(new_marker) > int(stored_marker))


def ingest_history(con, yp, tickers: list[str], as_of: date, now: datetime, workers: int,
                   errors: dict[str, str], force: bool = False) -> dict:
    """Daily close/adj_close/volume + dividends from the chart API (provider's rate limiter)."""
    flog = _fetch_log(con)
    last_vol = dict(con.execute(
        "SELECT ticker, max(date) FROM prices_daily WHERE volume IS NOT NULL GROUP BY 1"
    ).fetchall())

    def work(t: str):
        last_full, marker = flog.get((t, "yahoo_chart"), (None, None))
        try:
            full = history_needs_backfill(last_full, last_vol.get(t), as_of, force)
            raw = yp.chart(t, HISTORY_BACKFILL_RANGE if full else HISTORY_DAILY_RANGE)
            new_marker = yahoo.chart_events_marker(raw)
            if not full and events_changed(new_marker, marker):
                raw, full = yp.chart(t, HISTORY_BACKFILL_RANGE), True
                new_marker = yahoo.chart_events_marker(raw) or new_marker
            bars, divs = yahoo.parse_chart(t, raw)
            return t, full, new_marker or marker, bars, divs, None
        except Exception as e:
            return t, False, marker, [], [], f"history: {type(e).__name__}: {e}"

    st: dict = {"history": 0, "backfills": 0, "dividends": 0, "priced": set()}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for t, full, marker, bars, divs, err in pool.map(work, tickers):
            if err:
                errors[t] = f"{errors[t]}; {err}" if t in errors else err
                continue
            bars = [b for b in bars if b.date <= as_of]
            upsert_prices(con, bars)
            upsert_dividends(con, [d for d in divs if d.ex_date <= as_of])
            if full:  # fetched_at of the yahoo_chart row = time of the last full backfill
                _log_fetch(con, t, "yahoo_chart", now, marker)
                st["backfills"] += 1
            elif marker != flog.get((t, "yahoo_chart"), (None, None))[1]:
                con.execute("UPDATE fetch_log SET marker=? WHERE ticker=? AND source='yahoo_chart'",
                            [marker, t])
            if bars:
                st["history"] += 1
                st["priced"].add(t)
            st["dividends"] += len(divs)
    return st


def ingest_rates(con, rp, as_of: date, errors: dict[str, str]) -> int:
    have = dict(con.execute("SELECT series, max(date) FROM rates_daily GROUP BY 1").fetchall())
    n = 0
    for series, fetch, parse in ((rates.CA10Y, rp.boc_10y, rates.parse_boc_yield),
                                 (rates.US10Y, rp.fred_10y, rates.parse_fred_csv)):
        days = RATES_DAILY_DAYS if have.get(series) else RATES_BACKFILL_DAYS
        try:
            rows = [r for r in parse(fetch(as_of, days)) if r[0] <= as_of]
        except Exception as e:
            errors[f"RATES:{series}"] = f"{type(e).__name__}: {e}"
            continue
        if rows:
            con.executemany("INSERT OR REPLACE INTO rates_daily VALUES (?, ?, ?, ?)", rows)
        n += len(rows)
    return n


def ingest(
    con: duckdb.DuckDBPyConnection,
    universe_csv: str | Path,
    as_of: date,
    cache_root: str | Path = "data/raw",
    yahoo_provider=None,
    edgar_provider=None,
    boc_provider=None,
    rates_provider=None,
    prices_only: bool = False,
    history: bool = True,
    refresh_history: bool = False,
    refresh_fundamentals: bool = False,
    workers: int = 4,
    now: datetime | None = None,
) -> dict:
    """Incremental ingest.

    1. Prices for all tickers via batch calls.
    2. Profiles (new or older than 30 days) and fundamentals (when due), fetched in
       parallel threads; all DB writes happen on this thread.
    3. US filings: companyfacts only for CIKs whose latest accession (from EDGAR frames) is new.
    """
    now = now or datetime.now()
    cache = RawCache(cache_root, as_of)
    yp = yahoo_provider or yahoo.YahooProvider(cache)
    ep = edgar_provider or sec_edgar.EdgarProvider(cache)
    bp = boc_provider or boc.BocProvider(cache)
    rp = rates_provider or rates.RatesProvider(cache)

    errors: dict[str, str] = {}
    stats = {"prices": 0, "profiles": 0, "yahoo_fundamentals": 0, "edgar_filings": 0,
             "history": 0, "backfills": 0, "dividends": 0, "rates": 0}
    rows = {r["ticker"].strip(): r for r in load_universe(universe_csv)}
    tickers = list(rows)

    # 1. Prices (batch); per-ticker history only for symbols the batch didn't return.
    try:
        bars = yp.closes(tickers)
    except Exception as e:
        log.warning("batch prices failed: %s", e)
        bars = []
    bars = [b for b in bars if b.date <= as_of]  # never store bars after as_of
    upsert_prices(con, bars)
    priced = {b.ticker for b in bars}
    for t in tickers:
        if t not in priced:
            try:
                fallback = [b for b in yahoo.parse_history(t, yp.history(t)) if b.date <= as_of]
                upsert_prices(con, fallback)
                priced |= {b.ticker for b in fallback}
            except Exception as e:
                errors[t] = f"prices: {type(e).__name__}: {e}"
    if history:  # 1b. daily volume + adj_close + dividends (chart API, per ticker)
        hist = ingest_history(con, yp, tickers, as_of, now, workers, errors, refresh_history)
        priced |= hist.pop("priced")
        stats.update(hist)
    stats["prices"] = len(priced)

    known = dict(con.execute("SELECT ticker, updated_at FROM securities").fetchall())
    flog = _fetch_log(con)
    if not prices_only or any(t not in known for t in tickers):
        latest_pe = dict(con.execute(
            "SELECT ticker, max(period_end) FROM fundamentals WHERE source='yahoo' GROUP BY 1"
        ).fetchall())

        def plan(t: str) -> tuple[bool, bool]:
            new = t not in known
            if prices_only:
                return new, new
            need_profile = new or now - known[t] >= PROFILE_MAX_AGE
            last = flog.get((t, "yahoo"), (None, None))[0]
            need_fund = yahoo_fundamentals_due(last, latest_pe.get(t), now, refresh_fundamentals)
            return need_profile, need_fund

        def work(t: str):
            need_profile, need_fund = plan(t)
            out = {"ticker": t, "info": None, "facts": None, "error": None}
            try:
                if need_profile:
                    out["info"] = yp.info(t)
                if need_fund:
                    ccy = None
                    if out["info"]:
                        ccy = out["info"].get("financialCurrency") or out["info"].get("currency")
                    out["facts"] = yp.fundamentals(t, ccy)
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            return out

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(work, tickers))

        for res in results:  # single-threaded DB writes
            t, row = res["ticker"], rows[res["ticker"]]
            if res["info"] is not None:
                info = res["info"]
                upsert_security(con, t, row, yahoo.parse_profile(info))
                for metric, v in yahoo.parse_reference_metrics(info).items():
                    con.execute("INSERT OR REPLACE INTO reference_metrics VALUES (?, ?, ?, ?, ?)",
                                [t, as_of, "yahoo", metric, v])
                stats["profiles"] += 1
            elif t in known:
                update_universe_fields(con, t, row)
            if res["facts"] is not None:
                upsert_facts(con, res["facts"])
                _log_fetch(con, t, "yahoo", now)
                stats["yahoo_fundamentals"] += 1
            if res["error"]:
                errors[t] = res["error"]

    # 3. EDGAR: refetch companyfacts only when a new accession appeared.
    us = {t: r["cik"].zfill(10) for t, r in rows.items() if r.get("cik")}
    if us and not prices_only:
        try:
            latest_accn = ep.latest_accessions(as_of)
        except Exception as e:
            log.warning("EDGAR frames failed, fetching all: %s", e)
            latest_accn = {}
        due = []
        for t, cik in us.items():
            accn = latest_accn.get(cik)
            prev = flog.get((t, "sec_edgar"), (None, None))[1]
            if refresh_fundamentals or prev is None or (accn is not None and accn != prev):
                due.append((t, cik, accn or prev or "unknown"))

        def fetch_edgar(item):
            t, cik, marker = item
            try:
                return t, marker, ep.companyfacts(cik), None
            except Exception as e:
                return t, marker, None, f"edgar: {type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for t, marker, payload, err in pool.map(fetch_edgar, due):
                if err:
                    errors[t] = err
                    continue
                upsert_facts(con, sec_edgar.parse_companyfacts(t, payload)
                             + sec_edgar.parse_quarterly_ni(t, payload))
                _log_fetch(con, t, "sec_edgar", now, marker)
                stats["edgar_filings"] += 1

    try:
        obs = boc.parse_observations(bp.usdcad(as_of))
        if obs:
            con.executemany("INSERT OR REPLACE INTO fx_daily VALUES (?, 'USDCAD', ?)", obs)
    except Exception as e:
        errors["FX:USDCAD"] = f"{type(e).__name__}: {e}"

    stats["rates"] = ingest_rates(con, rp, as_of, errors)
    return {"tickers": len(tickers), "errors": errors, "stats": stats}
