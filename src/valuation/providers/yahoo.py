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


def parse_statement_currency(ts_payload: dict) -> str | None:
    """Latest currencyCode from a fundamentals-timeseries response."""
    for res in ts_payload.get("timeseries", {}).get("result", []) or []:
        for key, vals in res.items():
            if key in ("meta", "timestamp") or not isinstance(vals, list):
                continue
            pts = [v for v in vals if v and v.get("currencyCode")]
            if pts:
                return max(pts, key=lambda v: v.get("asOfDate", ""))["currencyCode"]
    return None


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


def _reset_session() -> None:
    """Drop yfinance's cached cookie/crumb so the next call negotiates a fresh one."""
    try:
        from yfinance.data import YfData

        d = YfData()
        d._crumb = None
        d._cookie = None
    except Exception:  # private API; best effort
        pass


class YahooProvider:
    CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{t}?range=5d&interval=1d"
    TS_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
              "{t}?type=quarterlyStockholdersEquity&period1={p1}&period2={p2}")

    def __init__(self, cache: RawCache, pause: float = 2.0, retries: int = 4,
                 breaker: int = 2):
        self.cache = cache
        self.pause = pause
        self.retries = retries
        self.breaker = breaker  # consecutive profile failures before skipping the endpoint
        self._profile_failures = 0

    def _ticker(self, ticker: str):
        import yfinance as yf

        time.sleep(self.pause)
        return yf.Ticker(ticker)

    def info(self, ticker: str) -> dict:
        """Profile from quoteSummary (needs a crumb); falls back to crumb-free endpoints."""
        raw = self.cache.get(SOURCE, f"{ticker}.info")
        if raw is not None:
            return raw
        if self._profile_failures < self.breaker:
            try:
                raw = self._quote_summary(ticker)
                self._profile_failures = 0
            except ProviderError:
                self._profile_failures += 1
        if raw is None:
            raw = self._fallback_profile(ticker)
        self.cache.put(SOURCE, f"{ticker}.info", raw)
        return raw

    def _quote_summary(self, ticker: str) -> dict:
        # yfinance swallows 401/429 (crumb throttling) and returns an empty dict,
        # so validate and back off. Partial responses (sector missing) also happen:
        # retry, then accept without it.
        best: dict = {}
        for attempt in range(self.retries):
            info = self._ticker(ticker).info or {}
            raw = {k: info.get(k) for k in PROFILE_KEYS}
            if raw.get("currency"):
                best = raw
                if raw.get("sector"):
                    break
            _reset_session()  # a throttled crumb otherwise sticks for the whole process
            time.sleep(5 * 2**attempt)
        if not best:
            raise ProviderError(f"{ticker}: Yahoo profile empty after {self.retries} tries")
        return best

    def _get_json(self, url: str) -> dict:
        import requests

        time.sleep(self.pause / 2)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _fallback_profile(self, ticker: str) -> dict:
        """Trading currency from the chart API, statement currency from the timeseries API.

        Sector/industry and Yahoo's reference metrics are unavailable on this path.
        """
        meta = self._get_json(self.CHART_URL.format(t=ticker))["chart"]["result"][0]["meta"]
        p2 = int(time.time())
        ts = self._get_json(self.TS_URL.format(t=ticker, p1=p2 - 2 * 365 * 86400, p2=p2))
        fin_ccy = parse_statement_currency(ts)
        if not meta.get("currency"):
            raise ProviderError(f"{ticker}: no currency from chart fallback")
        raw = {k: None for k in PROFILE_KEYS}
        raw.update({"symbol": ticker, "currency": meta["currency"],
                    "financialCurrency": fin_ccy or meta["currency"],
                    "longName": meta.get("longName"), "exchange": meta.get("exchangeName"),
                    "quoteType": meta.get("instrumentType"), "_profile_source": "fallback"})
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
