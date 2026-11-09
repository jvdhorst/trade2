"""Yahoo Finance via yfinance: profile, prices, quarterly balance sheet.

Yahoo gives no filing dates, so filed_date is estimated as period_end + 45 days
(the Canadian interim filing deadline) and marked filed_estimated=True.
"""

from __future__ import annotations

import math
import time
from datetime import date, timedelta
from typing import Any

from .base import (
    GOODWILL,
    INTANGIBLES,
    NCI,
    PREFERRED,
    SHARES,
    TOTAL_EQUITY,
    Fact,
    PriceBar,
    RawCache,
)

SOURCE = "yahoo"
FILING_LAG_DAYS = 45
PROFILE_KEYS = (
    "symbol", "shortName", "longName", "currency", "financialCurrency", "exchange", "sector",
    "industry", "quoteType", "sharesOutstanding", "marketCap", "bookValue", "priceToBook",
    "regularMarketPrice", "previousClose",
)


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


# ---------- parse (pure) ----------

def parse_profile(info: dict) -> dict:
    return {
        "name": info.get("longName") or info.get("shortName"),
        "currency": info.get("currency"),
        "financial_currency": info.get("financialCurrency") or info.get("currency"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "exchange": info.get("exchange"),
    }


def parse_reference_metrics(info: dict) -> dict[str, float]:
    """External reference values used by QA cross-checks."""
    out = {}
    for k in ("bookValue", "priceToBook", "sharesOutstanding", "marketCap", "regularMarketPrice"):
        v = _num(info.get(k))
        if v is not None:
            out[k] = v
    return out


def parse_balance_sheet(ticker: str, bs: dict[str, dict[str, Any]], currency: str) -> list[Fact]:
    """bs: {period_end_iso: {yahoo_line_item: value}} (quarterly)."""
    facts: list[Fact] = []
    for period, row in bs.items():
        pe = date.fromisoformat(period[:10])
        filed = pe + timedelta(days=FILING_LAG_DAYS)
        g = {k: _num(v) for k, v in row.items()}
        se, cse = g.get("Stockholders Equity"), g.get("Common Stock Equity")
        mi = g.get("Minority Interest")
        total = g.get("Total Equity Gross Minority Interest")
        if total is None and se is not None:
            total = se + (mi or 0.0)
        if se is not None and cse is not None:
            pref = se - cse  # preferreds + other equity instruments (LRCNs etc.)
        else:
            pref = g.get("Preferred Stock Equity")
        items = {
            TOTAL_EQUITY: total,
            NCI: mi,
            PREFERRED: pref,
            GOODWILL: g.get("Goodwill"),
            INTANGIBLES: g.get("Other Intangible Assets"),
            SHARES: g.get("Ordinary Shares Number"),
        }
        for item, v in items.items():
            if v is not None:
                unit = "shares" if item == SHARES else currency
                facts.append(Fact(ticker, pe, filed, item, v, unit, SOURCE, True))
    return facts


def parse_history(ticker: str, rows: list[dict]) -> list[PriceBar]:
    out = []
    for r in rows:
        c = _num(r.get("Close"))
        if c is None:
            continue
        out.append(
            PriceBar(ticker, date.fromisoformat(str(r["Date"])[:10]), c,
                     _num(r.get("Adj Close")), _num(r.get("Volume")), SOURCE)
        )
    return out


# ---------- fetch (network, cached) ----------

class ProviderError(RuntimeError):
    pass


class YahooProvider:
    def __init__(self, cache: RawCache, pause: float = 2.0, retries: int = 4):
        self.cache = cache
        self.pause = pause
        self.retries = retries

    def _ticker(self, ticker: str):
        import yfinance as yf

        time.sleep(self.pause)
        return yf.Ticker(ticker)

    def info(self, ticker: str) -> dict:
        raw = self.cache.get(SOURCE, f"{ticker}.info")
        if raw is None:
            # yfinance swallows 401/429 (crumb throttling) and returns an empty dict,
            # so validate and back off; never cache an empty profile.
            # Partial responses (sector missing) also happen: retry, then accept without it.
            best: dict = {}
            for attempt in range(self.retries):
                info = self._ticker(ticker).info or {}
                raw = {k: info.get(k) for k in PROFILE_KEYS}
                if raw.get("currency"):
                    best = raw
                    if raw.get("sector"):
                        break
                time.sleep(5 * 2**attempt)
            if not best:
                raise ProviderError(f"{ticker}: Yahoo profile empty after {self.retries} tries")
            raw = best
            self.cache.put(SOURCE, f"{ticker}.info", raw)
        return raw

    def balance_sheet(self, ticker: str) -> dict:
        raw = self.cache.get(SOURCE, f"{ticker}.balance_sheet_q")
        if raw is None:
            df = self._ticker(ticker).quarterly_balance_sheet
            raw = {} if df is None or df.empty else {
                col.date().isoformat(): {idx: _num(df.at[idx, col]) for idx in df.index}
                for col in df.columns
            }
            if not raw:
                raise ProviderError(f"{ticker}: Yahoo balance sheet empty")
            self.cache.put(SOURCE, f"{ticker}.balance_sheet_q", raw)
        return raw

    def history(self, ticker: str, days: int = 30) -> list[dict]:
        raw = self.cache.get(SOURCE, f"{ticker}.history")
        if raw is None:
            df = self._ticker(ticker).history(period=f"{days}d", auto_adjust=False)
            raw = [] if df is None or df.empty else [
                {"Date": idx.date().isoformat(), "Close": _num(r.get("Close")),
                 "Adj Close": _num(r.get("Adj Close")), "Volume": _num(r.get("Volume"))}
                for idx, r in df.iterrows()
            ]
            if not raw:
                raise ProviderError(f"{ticker}: Yahoo price history empty")
            self.cache.put(SOURCE, f"{ticker}.history", raw)
        return raw
