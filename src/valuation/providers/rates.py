"""Risk-free rates: Canada 10y (Bank of Canada Valet) and US 10y (FRED DGS10).

Stored in `rates_daily` as decimal fractions (5.12% -> 0.0512), one row per (date, series).
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, timedelta

import requests

from .base import RawCache
from .boc import URL as BOC_URL
from .boc import parse_observations

SOURCE = "rates"
CA10Y, US10Y = "CA10Y", "US10Y"
BOC_SERIES = "BD.CDN.10YR.DQ.YLD"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd={start}"


def parse_boc_yield(payload: dict) -> list[tuple[date, str, float, str]]:
    return [(d, CA10Y, v / 100.0, "boc") for d, v in parse_observations(payload, BOC_SERIES)]


def parse_fred_csv(text: str) -> list[tuple[date, str, float, str]]:
    """fredgraph.csv: header `observation_date,DGS10` (older files: `DATE`); '.' or '' = missing."""
    out = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        return out
    for row in reader:
        if len(row) < 2 or row[1].strip() in ("", "."):
            continue
        try:
            out.append((date.fromisoformat(row[0].strip()), US10Y, float(row[1]) / 100.0, "fred"))
        except ValueError:
            continue
    return out


class RatesProvider:
    def __init__(self, cache: RawCache):
        self.cache = cache

    def boc_10y(self, end: date, days: int = 30) -> dict:
        key = f"{BOC_SERIES}_{end.isoformat()}_{days}"
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            params = {"start_date": (end - timedelta(days=days)).isoformat(),
                      "end_date": end.isoformat()}
            r = requests.get(BOC_URL.format(series=BOC_SERIES), params=params, timeout=30)
            r.raise_for_status()
            raw = r.json()
            self.cache.put(SOURCE, key, raw)
        return raw

    def fred_10y(self, end: date, days: int = 30) -> str:
        key = f"DGS10_{end.isoformat()}_{days}"
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            url = FRED_URL.format(start=(end - timedelta(days=days)).isoformat())
            raw = None
            for attempt in range(3):  # FRED is slow on long ranges (first load: back to 2013)
                try:
                    r = requests.get(url, headers={"User-Agent": "trade2-valuation-screener"},
                                     timeout=120)
                    r.raise_for_status()
                    raw = r.text
                    break
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == 2:
                        raise
                    time.sleep(5 * 2**attempt)
            self.cache.put(SOURCE, key, raw)
        return raw
