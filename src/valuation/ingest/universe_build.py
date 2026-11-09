"""Build universe CSVs from public constituent lists.

- TSX Composite: holdings of the iShares S&P/TSX Capped Composite ETF (XIC), which tracks the index.
- S&P 500: Wikipedia "List of S&P 500 companies" (GICS sector and sub-industry, CIK).

Output columns match universe/reference.csv plus GICS fields, so `valuation ingest
--universe <file>` works with any of them.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

UA = "Mozilla/5.0 (compatible; trade2-valuation-screener)"
XIC_URL = (
    "https://www.blackrock.com/ca/investors/en/products/239837/"
    "ishares-sptsx-capped-composite-index-etf/1464253357814.ajax"
    "?fileType=csv&fileName=XIC_holdings&dataType=fund"
)
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

COLUMNS = ["ticker", "exchange", "cik", "share_classes", "share_conversion", "gics_sector",
           "gics_sub_industry", "name", "source", "source_as_of", "note"]

TSX_TICKER = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,2})?$")
# Listed share classes with identical economics (or an explicit conversion ratio).
MULTI_CLASS_US = {
    "BRK-B": (2, 1500.0, "class B; EDGAR/Yahoo shares are A-equivalent (1 A = 1500 B)"),
    "BF-B": (2, 1.0, "class B; class A also listed"),
    "GOOG": (2, 1.0, "class C; GOOGL also in index"),
    "GOOGL": (2, 1.0, "class A; GOOG also in index"),
    "FOX": (2, 1.0, "class B; FOXA also in index"),
    "FOXA": (2, 1.0, "class A; FOX also in index"),
    "NWS": (2, 1.0, "class B; NWSA also in index"),
    "NWSA": (2, 1.0, "class A; NWS also in index"),
}


def tsx_to_yahoo(t: str) -> str:
    """'BBD.B' -> 'BBD-B.TO', 'BIP.UN' -> 'BIP-UN.TO', 'RY' -> 'RY.TO'."""
    return t.replace(".", "-") + ".TO"


def parse_xic_csv(text: str) -> tuple[list[dict], str | None]:
    lines = text.lstrip("﻿").splitlines()
    as_of = None
    m = re.match(r'Fund Holdings as of,"([^"]+)"', lines[0]) if lines else None
    if m:
        as_of = pd.to_datetime(m.group(1)).date().isoformat()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Ticker,"))
    rows = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        t = (r.get("Ticker") or "").strip()
        if r.get("Asset Class") != "Equity" or not TSX_TICKER.match(t):
            continue
        if float((r.get("Price") or "0").replace(",", "")) <= 0:  # placeholder lines
            continue
        rows.append({
            "ticker": tsx_to_yahoo(t), "exchange": "TSX", "cik": "", "share_classes": 1,
            "share_conversion": 1, "gics_sector": r.get("Sector", ""), "gics_sub_industry": "",
            "name": r.get("Name", "").title(), "source": "iShares XIC holdings",
            "source_as_of": as_of or "", "note": "",
        })
    return rows, as_of


def parse_sp500_html(html: str, as_of: str) -> list[dict]:
    t = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    rows = []
    for _, r in t.iterrows():
        sym = str(r["Symbol"]).strip().replace(".", "-")
        classes, conv, note = MULTI_CLASS_US.get(sym, (1, 1.0, ""))
        rows.append({
            "ticker": sym, "exchange": "US", "cik": str(int(r["CIK"])).zfill(10),
            "share_classes": classes, "share_conversion": conv,
            "gics_sector": r["GICS Sector"], "gics_sub_industry": r["GICS Sub-Industry"],
            "name": r["Security"], "source": "Wikipedia List of S&P 500 companies",
            "source_as_of": as_of, "note": note,
        })
    return rows


def write_universe(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["ticker"]):
            w.writerow(r)


def validate(rows: list[dict], min_rows: int) -> list[str]:
    problems = []
    tickers = [r["ticker"] for r in rows]
    if len(rows) < min_rows:
        problems.append(f"only {len(rows)} rows (< {min_rows})")
    dupes = sorted({t for t in tickers if tickers.count(t) > 1})
    if dupes:
        problems.append(f"duplicate tickers: {dupes}")
    if any(not r["gics_sector"] for r in rows):
        problems.append("rows without GICS sector")
    return problems


def build(out_dir: str | Path = "universe") -> dict:
    h = {"User-Agent": UA}
    xic = requests.get(XIC_URL, headers=h, timeout=60)
    xic.raise_for_status()
    tsx, _ = parse_xic_csv(xic.text)
    wiki = requests.get(SP500_URL, headers=h, timeout=60)
    wiki.raise_for_status()
    sp = parse_sp500_html(wiki.text, date.today().isoformat())
    problems = {"tsx": validate(tsx, 200), "sp500": validate(sp, 495)}
    if any(r["cik"] == "" for r in sp):
        problems["sp500"].append("rows without CIK")
    write_universe(tsx, Path(out_dir) / "tsx_composite.csv")
    write_universe(sp, Path(out_dir) / "sp500.csv")
    return {"tsx": len(tsx), "sp500": len(sp), "problems": problems}
