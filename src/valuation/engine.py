"""Assemble model inputs from the DB at an as-of date, run the model, persist results."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections import defaultdict
from datetime import date, datetime

import duckdb
import pandas as pd

from .models import book_value as bv
from .providers.base import ITEMS, SHARES, TOTAL_EQUITY

SOURCE_PRIORITY = ("sec_edgar", "yahoo")  # official filings first


def _fx(con, as_of: date, reporting: str, trading: str) -> float | None:
    """Trading-currency units per 1 reporting-currency unit, latest on/before as_of."""
    if reporting == trading:
        return 1.0
    if {reporting, trading} != {"USD", "CAD"}:
        return None
    row = con.execute(
        "SELECT rate FROM fx_daily WHERE pair='USDCAD' AND date<=? ORDER BY date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    if row is None:
        return None
    return row[0] if reporting == "USD" else 1.0 / row[0]


def _filings(con, ticker: str, source: str) -> list[bv.Filing]:
    rows = con.execute(
        "SELECT period_end, filed_date, item, value, unit, filed_estimated FROM fundamentals "
        "WHERE ticker=? AND source=?", [ticker, source]).fetchall()
    grouped: dict[tuple, dict] = defaultdict(dict)
    for pe, fd, item, value, unit, est in rows:
        d = grouped[(pe, fd)]
        d[item] = value
        if item != SHARES:
            d["_currency"] = unit
        d["_filed_estimated"] = bool(est)
    return [bv.Filing(pe, fd, d) for (pe, fd), d in grouped.items()
            if TOTAL_EQUITY in d and SHARES in d]


def build_inputs(con: duckdb.DuckDBPyConnection, as_of: date):
    inputs, skipped = [], {}
    secs = con.execute(
        "SELECT ticker, currency, coalesce(gics_sector, sector), industry, share_classes, "
        "share_conversion FROM securities ORDER BY ticker"
    ).fetchall()
    for ticker, currency, sector, industry, classes, conversion in secs:
        conversion = conversion or 1.0
        price_row = con.execute(
            "SELECT date, close FROM prices_daily WHERE ticker=? AND date<=? "
            "ORDER BY date DESC LIMIT 1", [ticker, as_of]).fetchone()
        if price_row is None:
            skipped[ticker] = "no price on/before as_of"
            continue
        # Freshest balance sheet wins; on equal period_end prefer official filings.
        filing, source = None, None
        for src in SOURCE_PRIORITY:
            f = bv.select_latest_filing(_filings(con, ticker, src), as_of)
            if f and (filing is None or f.period_end > filing.period_end):
                filing, source = f, src
        if filing is None:
            skipped[ticker] = "no filing with equity + shares public on/before as_of"
            continue
        d = filing.data
        rep_ccy = d.get("_currency") or currency
        fx = _fx(con, as_of, rep_ccy, currency)
        if fx is None:
            skipped[ticker] = f"no FX {rep_ccy}->{currency}"
            continue
        inp = bv.BookValueInputs(
            ticker=ticker, as_of=as_of, price=price_row[1], trading_currency=currency,
            reporting_currency=rep_ccy, total_equity=d[TOTAL_EQUITY],
            shares_outstanding=d[SHARES] * conversion, period_end=filing.period_end,
            filed_date=filing.filed_date,
            noncontrolling_interest=d.get("noncontrolling_interest") or 0.0,
            preferred_equity=d.get("preferred_equity") or 0.0,
            goodwill=d.get("goodwill") or 0.0, intangibles=d.get("intangibles") or 0.0,
            fx_rate=fx, share_classes=classes or 1, sector=sector, industry=industry,
        )
        meta = {
            "source": source, "price_date": price_row[0].isoformat(),
            "filed_estimated": d.get("_filed_estimated", False),
            **{k: d.get(k) for k in ITEMS},
            "period_end": filing.period_end.isoformat(),
            "filed_date": filing.filed_date.isoformat(),
            "reporting_currency": rep_ccy, "fx_rate": fx, "share_conversion": conversion,
        }
        inputs.append((inp, meta))
    return inputs, skipped


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


def run_book_value(con: duckdb.DuckDBPyConnection, as_of: date,
                   min_peers: int = bv.DEFAULT_MIN_PEERS) -> tuple[pd.DataFrame, dict]:
    inputs, skipped = build_inputs(con, as_of)
    meta = {i.ticker: m for i, m in inputs}
    results = [bv.compute_book_value(i) for i, _ in inputs]
    ranked = bv.rank(bv.peer_relative(results, min_peers=min_peers))

    run_id = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, datetime.now(), as_of, "book_value", _git_sha(),
                 json.dumps({"min_peers": min_peers, "skipped": skipped}), "ok"])
    records = []
    for rank_no, v in enumerate(ranked, 1):
        r = v.result
        rec = {
            "rank": rank_no, "ticker": v.ticker, "as_of": as_of, "price": r.price,
            "currency": r.currency, "bvps": r.bvps, "tbvps": r.tbvps, "pb": r.pb,
            "peer_level": v.peer_level, "n_peers": v.n_peers,
            "peer_median_pb": v.peer_median_pb, "fair_value": v.fair_value, "upside": v.upside,
            "pb_percentile": v.pb_percentile, "flags": ",".join(sorted(v.flags)),
            "sector": r.sector, "industry": r.industry,
            "source": meta[v.ticker]["source"], "period_end": meta[v.ticker]["period_end"],
        }
        records.append(rec)
        con.execute(
            "INSERT INTO valuations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, v.ticker, as_of, "book_value", bv.MODEL_VERSION, r.price, r.currency,
             r.bvps, r.tbvps, r.pb, v.peer_level, v.n_peers, v.peer_median_pb, v.fair_value,
             v.upside, v.pb_percentile, rec["flags"], json.dumps(meta[v.ticker])],
        )
    df = pd.DataFrame.from_records(records)
    return df, {"run_id": run_id, "skipped": skipped}
