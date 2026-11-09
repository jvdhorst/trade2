"""External cross-check: our book_value output vs Yahoo's published bookValue / priceToBook."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd


def _rel(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return a / b - 1


def crosscheck_book_value(con, as_of: date, tolerance: float = 0.05) -> pd.DataFrame:
    run = con.execute(
        "SELECT run_id FROM runs WHERE as_of=? AND model='book_value' "
        "ORDER BY started_at DESC LIMIT 1", [as_of]).fetchone()
    if run is None:
        raise SystemExit(f"no book_value run for {as_of}; run `valuation value` first")
    vals = con.execute(
        "SELECT ticker, price, currency, bvps, pb, flags, inputs_json FROM valuations "
        "WHERE run_id=? ORDER BY ticker", [run[0]]).fetchall()
    rows = []
    for ticker, _price, ccy, bvps, pb, flags, inputs_json in vals:
        meta = json.loads(inputs_json)
        ref = dict(con.execute(
            "SELECT metric, value FROM reference_metrics WHERE ticker=? AND source='yahoo' "
            "AND as_of<=? ORDER BY as_of", [ticker, as_of]).fetchall())
        y_bv, y_pb = ref.get("bookValue"), ref.get("priceToBook")
        conv = meta.get("share_conversion") or 1.0
        if y_bv is not None and conv != 1.0:  # Yahoo quotes BV per reported (e.g. class A) share
            y_bv, y_pb = y_bv / conv, None
        bvps_reporting = bvps / meta["fx_rate"] if meta.get("fx_rate") else None
        d_bv = _rel(bvps, y_bv)
        d_bv_rep = _rel(bvps_reporting, y_bv)
        d_pb = _rel(pb, y_pb)
        bv_ok = d_bv is not None and abs(d_bv) <= tolerance
        rows.append({
            "ticker": ticker, "ccy": ccy, "rep_ccy": meta.get("reporting_currency"),
            "src": meta.get("source"), "period_end": meta.get("period_end"),
            "bvps": bvps, "yahoo_bv": y_bv, "d_bvps": d_bv, "d_bvps_rep_ccy": d_bv_rep,
            "pb": pb, "yahoo_pb": y_pb, "d_pb": d_pb,
            "pass": bool(bv_ok or (d_bv_rep is not None and abs(d_bv_rep) <= tolerance)),
            "flags": flags,
        })
    df = pd.DataFrame(rows)
    for c in ("d_bvps", "d_bvps_rep_ccy", "d_pb"):
        df[c] = df[c].map(lambda x: None if x is None else round(x, 4))
    return df
