"""Cross-check our BVPS against the issuer's own "book value per common share" disclosure.

Sources are listed in universe/issuer_sources.csv: a URL or local PDF path, the balance-sheet
date, and which value on the label line to take (0 = first, usually the current quarter).
The value read by hand is recorded too, so the check still runs if the PDF is unavailable,
and a parser regression shows up as a mismatch with the recorded value.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from ..providers.base import RawCache

LABEL = re.compile(r"^\s*(?:\d{1,3}\s+)?book value per (?:common )?share\b", re.I)
VALUE = re.compile(r"(?<![\d.])\$?(\d{1,5}\.\d{2})(?![\d])")  # BVPS always has 2 decimals
UA = "Mozilla/5.0 (compatible; trade2-valuation-screener)"


def extract_bvps(lines: list[str], column: int = 0) -> float | None:
    """First line starting with the label; the column-th 2-decimal number after it.

    Bare integers (footnote markers, page refs) are skipped.
    """
    for line in lines:
        m = LABEL.search(line)
        if m:
            values = VALUE.findall(line[m.end():])
            if len(values) > column:
                return float(values[column])
    return None


def pdf_lines(data: bytes) -> list[str]:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    return [ln for p in reader.pages for ln in (p.extract_text() or "").splitlines()]


def _load_source(source: str, cache: RawCache) -> bytes | None:
    if not source.startswith("http"):
        p = Path(source)
        return p.read_bytes() if p.exists() else None
    key = re.sub(r"[^A-Za-z0-9._-]", "_", source.rsplit("/", 1)[-1])
    path = cache.path("issuer", key).with_suffix(".pdf")
    if path.exists():
        return path.read_bytes()
    try:
        r = requests.get(source, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
    except requests.RequestException:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


def issuer_check(con, as_of: date, sources_csv: str | Path = "universe/issuer_sources.csv",
                 tolerance: float = 0.03, cache_root: str | Path = "data/raw") -> pd.DataFrame:
    cache = RawCache(cache_root, as_of)
    run = con.execute(
        "SELECT run_id FROM runs WHERE as_of=? AND model='book_value' "
        "ORDER BY started_at DESC LIMIT 1", [as_of]).fetchone()
    if run is None:
        raise SystemExit(f"no book_value run for {as_of}; run `valuation value` first")
    rows = []
    with open(sources_csv, newline="") as fh:
        for src in csv.DictReader(fh):
            t, pe = src["ticker"], src["period_end"]
            recorded = float(src["recorded_bvps"]) if src.get("recorded_bvps") else None
            data = _load_source(src["source"], cache)
            extracted = extract_bvps(pdf_lines(data), int(src["column"])) if data else None
            issuer = extracted if extracted is not None else recorded
            got = con.execute(
                "SELECT bvps, inputs_json FROM valuations WHERE run_id=? AND ticker=?",
                [run[0], t]).fetchone()
            ours, our_pe, status = None, None, ""
            if got is None:
                status = "not valued"
            else:
                meta = json.loads(got[1])
                our_pe = meta["period_end"]
                ours = got[0] / (meta.get("fx_rate") or 1.0)  # issuer reports in reporting ccy
                if our_pe != pe:
                    status = "period mismatch"
            diff = None if ours is None or issuer is None else ours / issuer - 1
            if not status:
                status = "pass" if diff is not None and abs(diff) <= tolerance else "FAIL"
            rows.append({
                "ticker": t, "period_end": pe, "our_period": our_pe, "our_bvps": ours,
                "issuer_bvps": issuer, "diff": None if diff is None else round(diff, 4),
                "value_from": "pdf" if extracted is not None else "recorded",
                "parser_ok": None if extracted is None or recorded is None
                else abs(extracted - recorded) < 0.005,
                "status": status,
            })
    return pd.DataFrame(rows)
