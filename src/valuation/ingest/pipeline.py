"""Daily ingestion: universe -> securities, prices, fundamentals, FX, reference metrics."""

from __future__ import annotations

import csv
import logging
from dataclasses import astuple
from datetime import date, datetime
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


def ingest(
    con: duckdb.DuckDBPyConnection,
    universe_csv: str | Path,
    as_of: date,
    cache_root: str | Path = "data/raw",
    yahoo_provider=None,
    edgar_provider=None,
    boc_provider=None,
) -> dict:
    cache = RawCache(cache_root, as_of)
    yp = yahoo_provider or yahoo.YahooProvider(cache)
    ep = edgar_provider or sec_edgar.EdgarProvider(cache)
    bp = boc_provider or boc.BocProvider(cache)

    errors: dict[str, str] = {}
    rows = load_universe(universe_csv)
    for row in rows:
        t = row["ticker"].strip()
        try:
            info = yp.info(t)
            profile = yahoo.parse_profile(info)
            upsert_security(con, t, row, profile)
            for metric, v in yahoo.parse_reference_metrics(info).items():
                con.execute("INSERT OR REPLACE INTO reference_metrics VALUES (?, ?, ?, ?, ?)",
                            [t, as_of, "yahoo", metric, v])
            upsert_prices(con, yahoo.parse_history(t, yp.history(t)))
            upsert_facts(con, yahoo.parse_balance_sheet(
                t, yp.balance_sheet(t), profile["financial_currency"] or profile["currency"]))
            if row.get("cik"):
                upsert_facts(con, sec_edgar.parse_companyfacts(t, ep.companyfacts(row["cik"])))
        except Exception as e:  # keep going; report per-ticker failures
            log.warning("ingest failed for %s: %s", t, e)
            errors[t] = f"{type(e).__name__}: {e}"

    try:
        obs = boc.parse_observations(bp.usdcad(as_of))
        if obs:
            con.executemany("INSERT OR REPLACE INTO fx_daily VALUES (?, 'USDCAD', ?)", obs)
    except Exception as e:
        errors["FX:USDCAD"] = f"{type(e).__name__}: {e}"

    return {"tickers": len(rows), "errors": errors}
