"""Yahoo Finance via yfinance: profile, prices, quarterly balance sheet.

Yahoo gives no filing dates, so filed_date is estimated as period_end + 45 days
(the Canadian interim filing deadline) and marked filed_estimated=True.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .base import (
    CAPEX_Q,
    CASH,
    CFO_Q,
    EBIT_Q,
    GOODWILL,
    GROSS_PROFIT_Q,
    INTANGIBLES,
    NCI,
    NI_COMMON_Q,
    PREFERRED,
    REVENUE_Q,
    SHARES,
    TOTAL_ASSETS,
    TOTAL_DEBT,
    TOTAL_EQUITY,
    Dividend,
    Fact,
    PriceBar,
    RawCache,
)

SOURCE = "yahoo"
CHART_SOURCE = "yahoo_chart"
FILING_LAG_DAYS = 45
ANNUAL_FILING_LAG_DAYS = 90  # fiscal Q4: annual filing deadline (spec docs/models/ttm.md §2)
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
        if total is not None and cse is not None:
            # Everything that is neither common nor NCI: preferreds, LRCNs/AT1, etc.
            # (Derived from total, not "Stockholders Equity", which Yahoo reports as 0
            # for partnerships such as BIP/BEP.)
            pref = total - (mi or 0.0) - cse
        else:
            pref = g.get("Preferred Stock Equity")
        if pref is not None and pref < 0:
            pref = None  # inconsistent components; never let a negative inflate book value
        items = {
            TOTAL_EQUITY: total,
            NCI: mi,
            PREFERRED: pref,
            GOODWILL: g.get("Goodwill"),
            INTANGIBLES: g.get("Other Intangible Assets"),
            SHARES: g.get("Ordinary Shares Number"),
            TOTAL_ASSETS: g.get("Total Assets"),
            TOTAL_DEBT: g.get("Total Debt"),
            CASH: g.get("Cash And Cash Equivalents"),
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


# Fundamentals-timeseries type -> the Yahoo line-item name parse_balance_sheet expects.
TS_TYPES = {
    "quarterlyTotalEquityGrossMinorityInterest": "Total Equity Gross Minority Interest",
    "quarterlyStockholdersEquity": "Stockholders Equity",
    "quarterlyCommonStockEquity": "Common Stock Equity",
    "quarterlyMinorityInterest": "Minority Interest",
    "quarterlyPreferredStockEquity": "Preferred Stock Equity",
    "quarterlyGoodwill": "Goodwill",
    "quarterlyOtherIntangibleAssets": "Other Intangible Assets",
    "quarterlyOrdinarySharesNumber": "Ordinary Shares Number",
    # data batch 2 (spec docs/models/ttm.md §5.2)
    "quarterlyTotalAssets": "Total Assets",
    "quarterlyTotalDebt": "Total Debt",
    "quarterlyCashAndCashEquivalents": "Cash And Cash Equivalents",
}
SPARK_MAX = 20  # Yahoo rejects more symbols per spark call
# Income-statement types (spec docs/models/ttm.md §2); annual only identifies fiscal year ends.
NI_QUARTERLY = "quarterlyNetIncomeCommonStockholders"
NI_ANNUAL = "annualNetIncomeCommonStockholders"
# Data batch 2 flows (spec docs/models/ttm.md §5.1): type -> (item, sign). Capex is reported as a
# negative cash flow and stored as a positive outflow.
FLOW_TYPES = {
    "quarterlyOperatingCashFlow": (CFO_Q, 1.0),
    "quarterlyCapitalExpenditure": (CAPEX_Q, -1.0),
    "quarterlyTotalRevenue": (REVENUE_Q, 1.0),
    "quarterlyGrossProfit": (GROSS_PROFIT_Q, 1.0),
    "quarterlyOperatingIncome": (EBIT_Q, 1.0),
}
TS_FLOW_TYPES = (NI_QUARTERLY, NI_ANNUAL, *FLOW_TYPES)


def _ts_points(payload: dict, type_: str) -> list[dict]:
    pts = []
    for res in payload.get("timeseries", {}).get("result", []) or []:
        for v in res.get(type_) or []:
            if v and v.get("reportedValue", {}).get("raw") is not None:
                pts.append(v)
    return pts


def _quarterly(ticker: str, ts_payload: dict, type_: str, item: str, sign: float) -> list[Fact]:
    """Discrete quarters of one timeseries type; filed date estimated (+90 days for a fiscal Q4,
    else +45), capped at the download date stamped in the payload (`_fetched`). Spec ttm.md §2."""
    fy_ends = {v["asOfDate"] for v in _ts_points(ts_payload, NI_ANNUAL)}
    fetched = ts_payload.get("_fetched")
    observed = date.fromisoformat(fetched) if fetched else None
    facts = []
    for v in _ts_points(ts_payload, type_):
        pe = date.fromisoformat(v["asOfDate"][:10])
        lag = ANNUAL_FILING_LAG_DAYS if v["asOfDate"] in fy_ends else FILING_LAG_DAYS
        filed = pe + timedelta(days=lag)
        if observed is not None:  # it was in a response downloaded that day: public by then
            filed = min(filed, observed)
        facts.append(Fact(ticker, pe, filed, item, sign * float(v["reportedValue"]["raw"]),
                          v.get("currencyCode") or "", SOURCE, True))
    return facts


def parse_quarterly_ni(ticker: str, ts_payload: dict) -> list[Fact]:
    """Discrete-quarter NI to common (spec ttm.md §2)."""
    return _quarterly(ticker, ts_payload, NI_QUARTERLY, NI_COMMON_Q, 1.0)


def parse_quarterly_flows(ticker: str, ts_payload: dict) -> list[Fact]:
    """Data batch 2 flow items (spec ttm.md §5.1); capex sign flipped to a positive outflow."""
    out: list[Fact] = []
    for type_, (item, sign) in FLOW_TYPES.items():
        out += _quarterly(ticker, ts_payload, type_, item, sign)
    return out


def parse_chart(ticker: str, payload: dict) -> tuple[list[PriceBar], list[Dividend]]:
    """v8 chart response -> daily bars (close, adj_close, volume) and dividends."""
    results = (payload or {}).get("chart", {}).get("result") or []
    if not results:
        return [], []
    res = results[0]
    ccy = (res.get("meta") or {}).get("currency")
    ind = res.get("indicators") or {}
    quote = (ind.get("quote") or [{}])[0] or {}
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    adj = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        c = _num(closes[i]) if i < len(closes) else None
        if c is None:
            continue
        bars.append(PriceBar(ticker, datetime.fromtimestamp(ts, tz=UTC).date(), c,
                             _num(adj[i]) if i < len(adj) else None,
                             _num(vols[i]) if i < len(vols) else None, CHART_SOURCE))
    divs = []
    for d in ((res.get("events") or {}).get("dividends") or {}).values():
        amt = _num(d.get("amount"))
        if amt is not None and d.get("date") is not None:
            divs.append(Dividend(ticker, datetime.fromtimestamp(d["date"], tz=UTC).date(), amt,
                                 ccy, CHART_SOURCE))
    return bars, divs


def chart_events_marker(payload: dict) -> str:
    """Latest dividend/split event timestamp in a chart response ('' if none)."""
    res = ((payload or {}).get("chart", {}).get("result") or [{}])[0] or {}
    ev = res.get("events") or {}
    ts = [int(e.get("date") or 0) for kind in ("dividends", "splits")
          for e in (ev.get(kind) or {}).values()]
    return str(max(ts)) if ts else ""


def parse_spark(payload: dict) -> list[PriceBar]:
    """Batch daily closes: {symbol: {timestamp: [...], close: [...]}}."""
    out = []
    for sym, d in (payload or {}).items():
        if not isinstance(d, dict):
            continue
        for ts, c in zip(d.get("timestamp") or [], d.get("close") or [], strict=False):
            if c is None:
                continue
            day = datetime.fromtimestamp(ts, tz=UTC).date()
            out.append(PriceBar(sym, day, float(c), None, None, SOURCE))
    return out


def timeseries_to_bs(payload: dict) -> tuple[dict[str, dict[str, float]], str | None]:
    """Fundamentals-timeseries response -> ({period: {line item: value}}, statement currency)."""
    bs: dict[str, dict[str, float]] = {}
    for res in payload.get("timeseries", {}).get("result", []) or []:
        for key, vals in res.items():
            item = TS_TYPES.get(key)
            if item is None or not isinstance(vals, list):
                continue
            for v in vals:
                if v and v.get("reportedValue", {}).get("raw") is not None:
                    bs.setdefault(v["asOfDate"], {})[item] = float(v["reportedValue"]["raw"])
    return bs, parse_statement_currency(payload)


class RateLimiter:
    """At most `rate` calls per second across threads."""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


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
    TS_ALL_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
                  "timeseries/{t}?type={types}&period1={p1}&period2={p2}")
    HISTORY_URL = ("https://query2.finance.yahoo.com/v8/finance/chart/{t}?range={r}&interval=1d"
                   "&events=div%7Csplit&includeAdjustedClose=true")
    SPARK_URL = ("https://query2.finance.yahoo.com/v8/finance/spark?symbols={s}"
                 "&range={r}&interval=1d")

    def __init__(self, cache: RawCache, rate: float = 4.0, retries: int = 4,
                 breaker: int = 2, pause: float | None = None):
        self.cache = cache
        self.limiter = RateLimiter(rate if pause is None else (1 / pause if pause else 0))
        self.retries = retries
        self.breaker = breaker  # consecutive profile failures before skipping the endpoint
        self._profile_failures = 0

    def _ticker(self, ticker: str):
        import yfinance as yf

        self.limiter.wait()
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

        for attempt in range(self.retries):
            self.limiter.wait()
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.status_code == 429:  # throttled: back off and retry
                time.sleep(5 * 2**attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise ProviderError(f"throttled (429) after {self.retries} tries: {url[:80]}")

    def closes(self, tickers: list[str], range_: str = "1mo") -> list[PriceBar]:
        """Daily closes for many tickers, 20 per call (spark API, no crumb needed)."""
        bars: list[PriceBar] = []
        for i in range(0, len(tickers), SPARK_MAX):
            chunk = tickers[i:i + SPARK_MAX]
            key = "spark_" + hashlib.sha1(",".join(chunk + [range_]).encode()).hexdigest()[:12]
            raw = self.cache.get(SOURCE, key)
            if raw is None:
                raw = self._get_json(self.SPARK_URL.format(s=",".join(chunk), r=range_))
                if isinstance(raw, dict) and "spark" in raw:  # error envelope
                    raise ProviderError(f"spark error: {str(raw)[:200]}")
                self.cache.put(SOURCE, key, raw)
            bars += parse_spark(raw)
        return bars

    def chart(self, ticker: str, range_: str = "5d") -> dict:
        """Daily bars with volume + adjusted close, dividend and split events (one call)."""
        key = f"{ticker}.chart_{range_}"
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            raw = self._get_json(self.HISTORY_URL.format(t=ticker, r=range_))
            if not (raw.get("chart") or {}).get("result"):
                raise ProviderError(f"{ticker}: chart empty: {str(raw)[:120]}")
            self.cache.put(SOURCE, key, raw)
        return raw

    PERIOD_URL = ("https://query2.finance.yahoo.com/v8/finance/chart/{t}?period1={p1}&period2={p2}"
                  "&interval=1d&events=div%7Csplit&includeAdjustedClose=true")

    def chart_period(self, ticker: str, start: date, end: date) -> dict:
        """Daily bars between two dates (backtest history). `range=max` is not used: Yahoo
        down-samples it to monthly bars. A 404 (delisted symbol) raises requests.HTTPError."""
        key = f"{ticker}.chart_{start.isoformat()}_{end.isoformat()}"
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            p1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
            p2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400
            raw = self._get_json(self.PERIOD_URL.format(t=ticker, p1=p1, p2=p2))
            if not (raw.get("chart") or {}).get("result"):
                raise ProviderError(f"{ticker}: chart empty: {str(raw)[:120]}")
            self.cache.put(SOURCE, key, raw)
        return raw

    def fundamentals(self, ticker: str, fallback_currency: str | None) -> list[Fact]:
        """Quarterly balance-sheet + NI facts via fundamentals-timeseries (fields we use only)."""
        key = f"{ticker}.timeseries_v3"  # v3: data batch 2 (ttm.md §5)
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            p2 = int(time.time())
            raw = self._get_json(self.TS_ALL_URL.format(
                t=ticker, types=",".join([*TS_TYPES, *TS_FLOW_TYPES]), p1=p2 - 2 * 365 * 86400,
                p2=p2))
            raw["_fetched"] = date.today().isoformat()  # bounds the estimated filed dates
            self.cache.put(SOURCE, key, raw)
        bs, ccy = timeseries_to_bs(raw)
        if not bs:
            raise ProviderError(f"{ticker}: no fundamentals from timeseries")
        return (parse_balance_sheet(ticker, bs, ccy or fallback_currency or "")
                + parse_quarterly_ni(ticker, raw) + parse_quarterly_flows(ticker, raw))

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
