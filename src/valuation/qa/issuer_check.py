"""Cross-check our numbers against the issuer's own disclosures in its supplementary pack.

Sources are listed in universe/issuer_sources.csv, one row per (ticker, metric):
- `metric=bvps` (default): "book value per (common) share"; `column` = which value on the label
  line to take (0 = first, usually the current quarter). Compared with book_value's BVPS, ±3%.
- `metric=roe`: the line matched by the `label` regex (reported ROE, in %); `column` = how many
  quarterly values to average from the start of the line (4 = the trailing four quarters, matching
  our trailing-annual ROE). Compared with Model A's ROE_raw, ±1.5pp
  (spec docs/models/residual_income.md, External check).
The value read by hand is recorded too (`recorded_bvps`; for ROE the mean, as a decimal), so the
check still runs if the PDF is unavailable, and a parser regression shows up as a mismatch.
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


PCT = re.compile(r"(?<![\d.])(\()?(\d{1,3}\.\d{1,2})\)?")  # 17.9 / (1.0); bare ints are footnotes


def extract_roe(lines: list[str], label: str, columns: int = 4) -> list[float] | None:
    """First line whose start matches `label`; its first `columns` 1–2-decimal values as decimals.

    Values in parentheses are negative. Footnote markers (bare integers) are skipped.
    """
    rx = re.compile(r"^\s*(?:\d{1,3}\s+)?(?:" + label + ")", re.I)
    for line in lines:
        m = rx.search(line)
        if m:
            vals = [(-1 if neg else 1) * float(v) / 100 for neg, v in PCT.findall(line[m.end():])]
            if len(vals) >= columns:
                return vals[:columns]
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


def _latest_run(con, as_of: date, model: str):
    row = con.execute("SELECT run_id FROM runs WHERE as_of=? AND model=? "
                      "ORDER BY started_at DESC LIMIT 1", [as_of, model]).fetchone()
    return row[0] if row else None


def issuer_check(con, as_of: date, sources_csv: str | Path = "universe/issuer_sources.csv",
                 tolerance: float = 0.03, cache_root: str | Path = "data/raw",
                 roe_tolerance: float = 0.015) -> pd.DataFrame:
    cache = RawCache(cache_root, as_of)
    run = _latest_run(con, as_of, "book_value")
    if run is None:
        raise SystemExit(f"no book_value run for {as_of}; run `valuation value` first")
    sig_run = _latest_run(con, as_of, "signals")
    rows = []
    with open(sources_csv, newline="") as fh:
        for src in csv.DictReader(fh):
            metric = (src.get("metric") or "bvps").strip() or "bvps"
            data = _load_source(src["source"], cache)
            lines = pdf_lines(data) if data else None
            if metric == "roe":
                rows.append(_check_roe(con, sig_run, src, lines, roe_tolerance))
            else:
                rows.append(_check_bvps(con, run, src, lines, tolerance))
    return pd.DataFrame(rows)


def _check_bvps(con, run: str, src: dict, lines: list[str] | None, tolerance: float) -> dict:
    t, pe = src["ticker"], src["period_end"]
    recorded = float(src["recorded_bvps"]) if src.get("recorded_bvps") else None
    extracted = extract_bvps(lines, int(src["column"])) if lines else None
    issuer = extracted if extracted is not None else recorded
    got = con.execute("SELECT bvps, inputs_json FROM valuations WHERE run_id=? AND ticker=?",
                      [run, t]).fetchone()
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
    return {
        "ticker": t, "metric": "bvps", "period_end": pe, "our_period": our_pe, "ours": ours,
        "issuer": issuer, "diff": None if diff is None else round(diff, 4),
        "value_from": "pdf" if extracted is not None else "recorded",
        "parser_ok": None if extracted is None or recorded is None
        else abs(extracted - recorded) < 0.005,
        "status": status,
    }


def _check_roe(con, sig_run: str | None, src: dict, lines: list[str] | None,
               tolerance: float) -> dict:
    """Issuer ROE = mean of its `column` latest quarterly reported ROEs; ours = Model A ROE_raw.

    The difference is in percentage points (decimal: 0.015 = 1.5pp)."""
    t, pe = src["ticker"], src["period_end"]
    n = int(src["column"])
    recorded = float(src["recorded_bvps"]) if src.get("recorded_bvps") else None
    vals = extract_roe(lines, src["label"], n) if lines and src.get("label") else None
    extracted = sum(vals) / len(vals) if vals else None
    issuer = extracted if extracted is not None else recorded
    ours, our_pe, status = None, None, ""
    got = None if sig_run is None else con.execute(
        "SELECT payload_json FROM signals WHERE run_id=? AND ticker=?", [sig_run, t]).fetchone()
    if got is None:
        status = "not valued" if sig_run else "no signals run"
    else:
        p = json.loads(got[0])
        ours, our_pe = p.get("roe_raw"), p.get("ttm_period_end")
        if ours is None:
            status = "no ROE"
        elif our_pe != pe:
            status = "period mismatch"
    diff = None if ours is None or issuer is None else ours - issuer
    if not status:
        status = "pass" if abs(diff) <= tolerance else "FAIL"
    return {
        "ticker": t, "metric": "roe", "period_end": pe, "our_period": our_pe, "ours": ours,
        "issuer": issuer, "diff": None if diff is None else round(diff, 4),
        "value_from": "pdf" if extracted is not None else "recorded",
        "parser_ok": None if extracted is None or recorded is None
        else abs(extracted - recorded) < 0.0005,
        "status": status,
    }
