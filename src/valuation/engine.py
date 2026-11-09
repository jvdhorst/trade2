"""Assemble model inputs from the DB at an as-of date, run the model, persist results."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections import defaultdict
from datetime import date, datetime

import duckdb
import pandas as pd

from . import ttm as ttm_mod
from .models import book_value as bv
from .models import eligibility as elig
from .providers.base import ITEMS, NI_COMMON_Q, SHARES, TOTAL_EQUITY

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
        f"WHERE ticker=? AND source=? AND item IN ({','.join('?' * len(ITEMS))})",
        [ticker, source, *ITEMS]).fetchall()
    grouped: dict[tuple, dict] = defaultdict(dict)
    for pe, fd, item, value, unit, est in rows:
        d = grouped[(pe, fd)]
        d[item] = value
        if item != SHARES:
            d["_currency"] = unit
        d["_filed_estimated"] = bool(est)
    return [bv.Filing(pe, fd, d) for (pe, fd), d in grouped.items()
            if TOTAL_EQUITY in d and SHARES in d]


def build_inputs(con: duckdb.DuckDBPyConnection, as_of: date, tickers: set[str] | None = None):
    inputs, skipped = [], {}
    secs = con.execute(
        "SELECT ticker, currency, coalesce(gics_sector, sector), industry, share_classes, "
        "share_conversion FROM securities ORDER BY ticker"
    ).fetchall()
    for ticker, currency, sector, industry, classes, conversion in secs:
        if tickers is not None and ticker not in tickers:
            continue
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


class _Q:  # a stored quarter row, as ttm.ttm expects
    __slots__ = ("period_end", "filed_date", "value", "unit")

    def __init__(self, period_end, filed_date, value, unit):
        self.period_end, self.filed_date, self.value, self.unit = (
            period_end, filed_date, value, unit)


def ni_common_ttm(con, ticker: str, as_of: date):
    """TTM NI to common: per source, then the latest quarter end wins (EDGAR on ties).

    Returns (TTMResult, source, currency)."""
    rows = con.execute(
        "SELECT source, period_end, filed_date, value, unit FROM fundamentals "
        "WHERE ticker=? AND item=? AND filed_date<=?", [ticker, NI_COMMON_Q, as_of]).fetchall()
    best: tuple[ttm_mod.TTMResult, str | None, str | None] = (
        ttm_mod.TTMResult(None, flags=frozenset({ttm_mod.NO_TTM})), None, None)
    for src in SOURCE_PRIORITY:
        qs = [_Q(pe, fd, v, u) for s_, pe, fd, v, u in rows if s_ == src]
        res = ttm_mod.ttm(qs, as_of)
        if res.value is not None and (best[0].period_end is None
                                      or res.period_end > best[0].period_end):
            best = (res, src, next((q.unit for q in qs if q.period_end == res.period_end), None))
    return best


def dps_ttm(con, ticker: str, as_of: date) -> float:
    rows = con.execute("SELECT ex_date, amount FROM dividends WHERE ticker=? AND ex_date<=?",
                       [ticker, as_of]).fetchall()

    class _D:
        __slots__ = ("ex_date", "amount")

        def __init__(self, ex_date, amount):
            self.ex_date, self.amount = ex_date, amount

    return ttm_mod.dps_ttm([_D(d, a) for d, a in rows], as_of)


def risk_free(con, as_of: date, currency: str | None) -> float | None:
    """Local 10-year government yield (decimal) on/before as_of: CAD -> CA10Y, USD -> US10Y."""
    series = {"CAD": "CA10Y", "USD": "US10Y"}.get(currency or "")
    if series is None:
        return None
    row = con.execute("SELECT rate FROM rates_daily WHERE series=? AND date<=? "
                      "ORDER BY date DESC LIMIT 1", [series, as_of]).fetchone()
    return row[0] if row else None


def market_of(ticker: str, currency: str | None) -> str | None:
    if currency == "CAD" or ticker.endswith(".TO"):
        return "TSX"
    if currency == "USD":
        return "US"
    return None


SESSION_QUORUM = 0.5  # a market session = a date on which >= half the market's names have a bar


def run_eligibility(con, as_of: date, tickers: set[str] | None, market_caps: dict[str, float],
                    config: dict) -> dict[str, elig.EligibilityResult]:
    """Evaluate the filter for every security (valued or not). Universe file = index membership."""
    secs = con.execute("SELECT ticker, currency FROM securities").fetchall()
    markets = {t: market_of(t, c) for t, c in secs if tickers is None or t in tickers}
    lookback = max(int(config[k]["min_history_sessions"]) for k in ("tsx", "us")) * 2 + 60
    bars: dict[str, list[elig.Bar]] = {t: [] for t in markets}
    for t, d, c, a, v in con.execute(
            "SELECT ticker, date, close, adj_close, volume FROM prices_daily "
            "WHERE date<=? AND date>?::DATE - ?::INTEGER ORDER BY ticker, date",
            [as_of, as_of, lookback]).fetchall():
        if t in bars:
            bars[t].append(elig.Bar(d, c, a, v))
    sessions: dict[str, list[date]] = {}
    for m in ("TSX", "US"):
        names = [t for t, mk in markets.items() if mk == m]
        counts: dict[date, int] = defaultdict(int)
        for t in names:
            for b in bars[t]:
                counts[b.date] += 1
        sessions[m] = [d for d, n in counts.items() if n >= SESSION_QUORUM * len(names)]
    out = {}
    for t, m in markets.items():
        if m is None:
            continue
        out[t] = elig.evaluate(elig.EligibilityInputs(
            ticker=t, as_of=as_of, market=m, index_member=True, bars=bars[t],
            market_cap=market_caps.get(t), market_sessions=sessions[m] or None), config)
    if tickers is not None:  # universe members never ingested: no data at all
        for t in tickers - set(markets):
            m = market_of(t, None) or "US"
            out[t] = elig.evaluate(elig.EligibilityInputs(t, as_of, m, True, [], None), config)
    return out


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


def run_book_value(con: duckdb.DuckDBPyConnection, as_of: date,
                   min_peers: int = bv.DEFAULT_MIN_PEERS,
                   tickers: set[str] | None = None,
                   liquidity: dict | None = None) -> tuple[pd.DataFrame, dict]:
    liquidity = liquidity if liquidity is not None else elig.load_config()
    inputs, skipped = build_inputs(con, as_of, tickers)
    if tickers is not None:  # universe members never ingested
        known = {r[0] for r in con.execute("SELECT ticker FROM securities").fetchall()}
        skipped.update({t: "not ingested" for t in tickers - known})
    meta = {i.ticker: m for i, m in inputs}
    caps = {i.ticker: i.price * i.shares_outstanding for i, _ in inputs}
    eligibility = run_eligibility(con, as_of, tickers, caps, liquidity)
    extra = {}
    for i, _ in inputs:
        res, src, ccy = ni_common_ttm(con, i.ticker, as_of)
        extra[i.ticker] = {
            "ni_common_ttm": res.value, "ni_ttm_period_end": res.period_end, "ni_ttm_source": src,
            "ni_ttm_currency": ccy, "ttm_flags": ",".join(sorted(res.flags)),
            "dps_ttm": dps_ttm(con, i.ticker, as_of),
            "rf": risk_free(con, as_of, i.trading_currency),
        }
    results = [bv.compute_book_value(i) for i, _ in inputs]
    ranked = bv.rank(bv.peer_relative(results, min_peers=min_peers))

    run_id = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, datetime.now(), as_of, "book_value", _git_sha(),
                 json.dumps({"min_peers": min_peers, "skipped": skipped, "liquidity": liquidity,
                             "eligibility_version": elig.MODEL_VERSION}), "ok"])
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
            **_elig_cols(eligibility.get(v.ticker)), **extra[v.ticker],
        }
        records.append(rec)
        con.execute(
            "INSERT INTO valuations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, v.ticker, as_of, "book_value", bv.MODEL_VERSION, r.price, r.currency,
             r.bvps, r.tbvps, r.pb, v.peer_level, v.n_peers, v.peer_median_pb, v.fair_value,
             v.upside, v.pb_percentile, rec["flags"],
             json.dumps({**meta[v.ticker], **extra[v.ticker]}, default=str)],
        )
    df = pd.DataFrame.from_records(records)
    reports = {m: elig.removal_report([r for r in eligibility.values() if r.market == m])
               for m in ("TSX", "US") if any(r.market == m for r in eligibility.values())}
    return df, {"run_id": run_id, "skipped": skipped, "eligibility": eligibility,
                "removal": reports,
                "coverage": coverage(df, skipped, {t: m for t, m in meta.items()}, eligibility)}


def _elig_cols(r: elig.EligibilityResult | None) -> dict:
    if r is None:
        return {"eligible": None, "elig_fail_reason": "", "mdtv": None, "elig_flags": ""}
    return {"eligible": r.eligible, "elig_fail_reason": r.elig_fail_reason, "mdtv": r.mdtv,
            "elig_flags": ",".join(sorted(r.flags))}


def coverage(df: pd.DataFrame, skipped: dict[str, str], meta: dict[str, dict],
             eligibility: dict[str, elig.EligibilityResult] | None = None) -> pd.DataFrame:
    """One row per security: valued or skipped (with reason), source, data quality, eligibility."""
    eligibility = eligibility or {}
    rows = []
    for _, r in df.iterrows():
        m = meta[r["ticker"]]
        rows.append({
            "ticker": r["ticker"], "status": "valued", "reason": "", "source": m["source"],
            "period_end": m["period_end"], "filed_estimated": m["filed_estimated"],
            "flags": r["flags"], "peer_level": r["peer_level"], "n_peers": r["n_peers"],
            "has_fair_value": pd.notna(r["fair_value"]),
            "ttm_flags": r.get("ttm_flags", ""), **_elig_cols(eligibility.get(r["ticker"])),
        })
    for t, reason in skipped.items():
        rows.append({"ticker": t, "status": "skipped", "reason": reason, "source": None,
                     "period_end": None, "filed_estimated": None, "flags": "",
                     "peer_level": None, "n_peers": 0, "has_fair_value": False,
                     "ttm_flags": "", **_elig_cols(eligibility.get(t))})
    return pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
