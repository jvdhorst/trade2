"""Daily ingestion: universe -> securities, prices, fundamentals, FX, reference metrics."""

from __future__ import annotations

import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import astuple
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from ..providers import boc, sec_edgar, yahoo
from ..providers.base import Fact, PriceBar, RawCache

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
    if bars:
        con.executemany(
            "INSERT OR REPLACE INTO prices_daily VALUES (?, ?, ?, ?, ?, ?)",
            [astuple(b) for b in bars],
        )


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


def ingest(
    con: duckdb.DuckDBPyConnection,
    universe_csv: str | Path,
    as_of: date,
    cache_root: str | Path = "data/raw",
    yahoo_provider=None,
    edgar_provider=None,
    boc_provider=None,
    prices_only: bool = False,
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

    errors: dict[str, str] = {}
    stats = {"prices": 0, "profiles": 0, "yahoo_fundamentals": 0, "edgar_filings": 0}
    rows = {r["ticker"].strip(): r for r in load_universe(universe_csv)}
    tickers = list(rows)

    # 1. Prices (batch); per-ticker history only for symbols the batch didn't return.
    try:
        bars = yp.closes(tickers)
    except Exception as e:
        log.warning("batch prices failed: %s", e)
        bars = []
    upsert_prices(con, bars)
    priced = {b.ticker for b in bars}
    for t in tickers:
        if t not in priced:
            try:
                fallback = yahoo.parse_history(t, yp.history(t))
                upsert_prices(con, fallback)
                priced |= {b.ticker for b in fallback}
            except Exception as e:
                errors[t] = f"prices: {type(e).__name__}: {e}"
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
                upsert_facts(con, sec_edgar.parse_companyfacts(t, payload))
                _log_fetch(con, t, "sec_edgar", now, marker)
                stats["edgar_filings"] += 1

    try:
        obs = boc.parse_observations(bp.usdcad(as_of))
        if obs:
            con.executemany("INSERT OR REPLACE INTO fx_daily VALUES (?, 'USDCAD', ?)", obs)
    except Exception as e:
        errors["FX:USDCAD"] = f"{type(e).__name__}: {e}"

    return {"tickers": len(tickers), "errors": errors, "stats": stats}
