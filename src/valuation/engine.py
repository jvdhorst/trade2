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
from .providers.base import CASH, EBIT_Q, ITEMS, NI_COMMON_Q, SHARES, TOTAL_DEBT, TOTAL_EQUITY

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


def _filings(con, ticker: str, source: str, need_shares: bool = True) -> list[bv.Filing]:
    rows = con.execute(
        "SELECT period_end, filed_date, item, value, unit, filed_estimated FROM fundamentals "
        f"WHERE ticker=? AND source=? AND item IN ({','.join('?' * len(ITEMS))})",
        [ticker, source, *ITEMS]).fetchall()
    grouped: dict[tuple, dict] = defaultdict(dict)
    for pe, fd, item, value, unit, est in rows:
        if item == SHARES and not (value and value > 0):
            continue  # EDGAR cover pages of some multi-class filers report 0: treat as missing
        d = grouped[(pe, fd)]
        d[item] = value
        if item != SHARES:
            d["_currency"] = unit
        d["_filed_estimated"] = bool(est)
    return [bv.Filing(pe, fd, d) for (pe, fd), d in grouped.items()
            if TOTAL_EQUITY in d and (SHARES in d or not need_shares)]


def split_factor(con, ticker: str, after: date, through: date) -> float:
    """Product of split ratios with split date in (after, through]: puts a per-share figure
    known at `after` on the share basis of `through` (spec docs/models/backtest.md §Splits).
    1.0 when the `splits` table is empty (the live DB)."""
    if through <= after:
        return 1.0
    row = con.execute("SELECT product(ratio) FROM splits WHERE ticker=? AND date>? AND date<=?",
                      [ticker, after, through]).fetchone()
    return float(row[0]) if row and row[0] is not None else 1.0


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
            shares_outstanding=d[SHARES] * conversion
            * split_factor(con, ticker, filing.filed_date, price_row[0]),
            period_end=filing.period_end,
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
    return flow_ttm(con, ticker, NI_COMMON_Q, as_of)


def flow_ttm(con, ticker: str, item: str, as_of: date):
    """TTM of any flow item (spec ttm.md §5.3): per source, latest period_end wins, EDGAR on ties.

    Returns (TTMResult, source, currency)."""
    rows = con.execute(
        "SELECT source, period_end, filed_date, value, unit FROM fundamentals "
        "WHERE ticker=? AND item=? AND filed_date<=?", [ticker, item, as_of]).fetchall()
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
    # Dividends are stored per as-traded share; restate each on the as_of share basis.
    rows = con.execute(
        "SELECT d.ex_date, d.amount / coalesce((SELECT product(s.ratio) FROM splits s "
        "WHERE s.ticker=d.ticker AND s.date>d.ex_date AND s.date<=?), 1) "
        "FROM dividends d WHERE d.ticker=? AND d.ex_date<=?", [as_of, ticker, as_of]).fetchall()

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
                    config: dict, bars_out: dict | None = None
                    ) -> dict[str, elig.EligibilityResult]:
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
    if bars_out is not None:  # reused by Model C (momentum, volatility)
        bars_out.update(bars)
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


# ---------------------------------------------------------------------------------------------
# Phase 7: Model A (residual_income) + Model B (peer_regression, book branch) -> screen_signals
# Specs: docs/models/residual_income.md (Data mapping, Report v2), docs/models/peer_regression.md
# ---------------------------------------------------------------------------------------------

SIGNAL_COLUMNS = [
    "ticker", "market", "sector", "eligible", "elig_fail_reason", "price", "bvps", "pb", "roe0",
    "a_signal", "a_model_signal", "a_no_signal_reason", "a_mos", "a_v_ri", "a_mos_up", "a_mos_down",
    "a_justified_pb", "b_signal", "b_no_signal_reason", "b_implied_upside", "b_resid_pct",
    "b_fitted_pb", "b_fair_value", "c_signal", "c_no_signal_reason", "c_score", "c_pct",
    "c_n_factors", "c_value", "c_quality", "c_momentum", "c_risk",
    "h_signal", "h_no_signal_reason", "h_sy", "h_dy", "h_bby", "h_payout", "h_sy_pct",
    "j_signal", "j_no_signal_reason", "j_ey", "j_roc", "j_ev", "j_rank_ey", "j_rank_roc", "j_pct",
    "flags",
]
C_COLUMNS = ("c_signal", "c_no_signal_reason", "c_score", "c_pct", "c_n_factors", "c_value",
             "c_quality", "c_momentum", "c_risk")
H_COLUMNS = ("h_signal", "h_no_signal_reason", "h_sy", "h_dy", "h_bby", "h_payout", "h_sy_pct")
J_COLUMNS = ("j_signal", "j_no_signal_reason", "j_ey", "j_roc", "j_ev", "j_rank_ey", "j_rank_roc",
             "j_pct")
LABELS = ("BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE")
RF_SERIES = {"TSX": "CA10Y", "US": "US10Y"}


def load_insurer_csm(path) -> dict[str, list[dict]]:
    """universe/insurer_csm.csv -> {ticker: [row, ...]}; csm_after_tax None when blank."""
    import csv
    from pathlib import Path

    out: dict[str, list[dict]] = defaultdict(list)
    if path is None or not Path(path).exists():
        return {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            t = (row.get("ticker") or "").strip()
            if not t:
                continue
            csm = (row.get("csm_after_tax") or "").strip()
            out[t].append({
                "period_end": date.fromisoformat(row["period_end"].strip()),
                "filed_date": date.fromisoformat(row["filed_date"].strip()),
                "csm_after_tax": float(csm) if csm else None,
            })
    return dict(out)


def csm_for(rows: list[dict] | None, period_end: date, as_of: date) -> tuple[bool, float | None]:
    """(csm_relevant, csm_after_tax): the row for exactly this balance-sheet period, public."""
    if rows is None:
        return False, None
    cands = [r for r in rows if r["period_end"] == period_end and r["filed_date"] <= as_of]
    if not cands:
        return True, None
    return True, max(cands, key=lambda r: r["filed_date"])["csm_after_tax"]


def _common_equity(f: bv.Filing) -> float:
    d = f.data
    return d[TOTAL_EQUITY] - (d.get("noncontrolling_interest") or 0.0) - (
        d.get("preferred_equity") or 0.0)


def _all_filings(con, ticker: str, as_of: date) -> dict[str, list[bv.Filing]]:
    # Equity lookups (CE_end at q0, CE_start, ROE history) need equity only, not a share count:
    # multi-class EDGAR filers often lack the cover-page share count (spec: Data mapping).
    return {src: [f for f in _filings(con, ticker, src, need_shares=False) if f.filed_date <= as_of]
            for src in SOURCE_PRIORITY}


def _filing_at(filings: dict[str, list[bv.Filing]], period_end: date) -> bv.Filing | None:
    """Filing with exactly this period_end (EDGAR first; latest filed)."""
    for src in SOURCE_PRIORITY:
        c = [f for f in filings[src] if f.period_end == period_end]
        if c:
            return max(c, key=lambda f: f.filed_date)
    return None


def _filing_year_before(filings: dict[str, list[bv.Filing]], q: date) -> bv.Filing | None:
    """Latest period_end in [q − 380, q − 350] days (EDGAR first, then Yahoo; ties: later filed)."""
    for src in SOURCE_PRIORITY:
        c = [f for f in filings[src] if 350 <= (q - f.period_end).days <= 380]
        if c:
            return max(c, key=lambda f: (f.period_end, f.filed_date))
    return None


def shares_prev_filing(filings: list[bv.Filing], period_end_t: date,
                       as_of: date) -> bv.Filing | None:
    """Model H shares_prev (shareholder_yield.md, Data mapping): from ONE source's filings, the
    latest period_end in [period_end_t − 380, period_end_t − 350] days, filed ≤ as_of, shares > 0;
    equal period_end → later filed. None if none."""
    from .models import shareholder_yield as sy

    lo, hi = sy.PREV_WINDOW_DAYS
    c = [f for f in filings if f.filed_date <= as_of and (f.data.get(SHARES) or 0) > 0
         and lo <= (period_end_t - f.period_end).days <= hi]
    return max(c, key=lambda f: (f.period_end, f.filed_date)) if c else None


def _ni_rows(con, ticker: str, as_of: date) -> dict[str, list]:
    rows = con.execute(
        "SELECT source, period_end, filed_date, value, unit, filed_estimated FROM fundamentals "
        "WHERE ticker=? AND item=? AND filed_date<=?", [ticker, NI_COMMON_Q, as_of]).fetchall()
    out: dict[str, list] = defaultdict(list)
    for src, pe, fd, v, u, est in rows:
        q = _Q(pe, fd, v, u)
        out[src].append((q, bool(est)))
    return out


def _roe_history(quarters: list, filings, as_of: date) -> tuple:
    """Trailing-annual ROE_raw ending 4 and 8 known quarters before q0 (None where incomplete)."""
    from .models import residual_income as ri

    latest = ttm_mod.latest_known(quarters, as_of)
    ends = sorted(latest, reverse=True)
    hist = []
    for k in (4, 8):
        if len(ends) <= k:
            hist.append(None)
            continue
        qk = ends[k]
        res = ttm_mod.ttm([q for q in quarters if q.period_end <= qk], as_of)
        f_end, f_start = _filing_at(filings, qk), _filing_year_before(filings, qk)
        if res.value is None or res.period_end != qk or f_end is None or f_start is None:
            hist.append(None)
            continue
        val, _ = ri.roe_raw(res.value, _common_equity(f_end), _common_equity(f_start))
        hist.append(val)
    return tuple(hist)


def run_signals(con: duckdb.DuckDBPyConnection, as_of: date, tickers: set[str] | None = None,
                liquidity: dict | None = None,
                csm_path="universe/insurer_csm.csv") -> tuple[pd.DataFrame, dict]:
    """Model A + Model B for every security (or `tickers`), one report row each."""
    from .models import composite as cm
    from .models import ebit_yield_roc as jm
    from .models import peer_regression as pr
    from .models import residual_income as ri
    from .models import shareholder_yield as sy

    liquidity = liquidity if liquidity is not None else elig.load_config()
    csm_rows = load_insurer_csm(csm_path)
    inputs, skipped = build_inputs(con, as_of, tickers)
    secs = {t: (c, s) for t, c, s in con.execute(
        "SELECT ticker, currency, coalesce(gics_sector, sector) FROM securities").fetchall()}
    universe = sorted(set(tickers) if tickers is not None else set(secs))
    caps = {i.ticker: i.price * i.shares_outstanding for i, _ in inputs}
    bars: dict[str, list] = {}
    eligibility = run_eligibility(con, as_of, tickers, caps, liquidity, bars_out=bars)

    a_results: dict[str, ri.RIResult] = {}
    details: dict[str, dict] = {}
    h_inputs: dict[str, sy.SYInputs] = {}
    j_inputs: list[jm.JInputs] = []

    def a_elig(e):
        return None if e is None else e.eligible

    for inp, meta in inputs:
        t = inp.ticker
        market = market_of(t, inp.trading_currency) or "US"
        filings = _all_filings(con, t, as_of)
        ce_bv = (inp.total_equity - inp.noncontrolling_interest - inp.preferred_equity)
        res, src, ni_ccy = ni_common_ttm(con, t, as_of)
        q0 = res.period_end
        data_flags: set[str] = set()
        ce_end, rep_ccy = ce_bv, inp.reporting_currency
        ce_start = None
        if q0 is not None:
            if q0 != inp.period_end:
                f = _filing_at(filings, q0)
                if f is not None:
                    ce_end, rep_ccy = _common_equity(f), f.data.get("_currency") or rep_ccy
                else:
                    data_flags.add("ROE_PERIOD_MISMATCH")
            f0 = _filing_year_before(filings, q0)
            ce_start = _common_equity(f0) if f0 is not None else None
        ni_rows = _ni_rows(con, t, as_of)
        ttm_est = bool(src) and any(est for q, est in ni_rows.get(src, [])
                                    if q.period_end in res.quarter_ends)
        hist = ()
        if src and inp.sector in ri.CYCLICAL_SECTORS:
            hist = _roe_history([q for q, _ in ni_rows[src]], filings, as_of)
        csm_relevant, csm = csm_for(csm_rows.get(t), inp.period_end, as_of)
        e = eligibility.get(t)
        dps = dps_ttm(con, t, as_of)
        a = ri.evaluate(ri.RIInputs(
            ticker=t, as_of=as_of, price=inp.price,
            bvps=ce_bv / inp.shares_outstanding * inp.fx_rate, sector=inp.sector,
            eligible=None if e is None else e.eligible,
            rf=risk_free(con, as_of, {"TSX": "CAD", "US": "USD"}[market]),
            ni_common_ttm=res.value, common_equity_end=ce_end, common_equity_start=ce_start,
            shares_outstanding=inp.shares_outstanding, fx_rate=inp.fx_rate,
            dps_ttm=dps, market=market, ttm_flags=res.flags,
            data_flags=frozenset(data_flags),
            filed_estimated=bool(meta.get("filed_estimated")) or ttm_est,
            roe_history=hist, csm_relevant=csm_relevant, csm_after_tax=csm,
            ni_currency=ni_ccy if res.value is not None else None, reporting_currency=rep_ccy,
            ni_quarters=res.quarter_values if res.value is not None else None,
        ))
        a_results[t] = a
        details[t] = {
            "bs_source": meta["source"], "bs_period_end": inp.period_end.isoformat(),
            "bs_filed_date": inp.filed_date.isoformat(), "ttm_source": src,
            "ttm_period_end": q0.isoformat() if q0 else None,
            "ni_common_ttm": res.value, "ni_ccy": ni_ccy, "ce_end": ce_end, "ce_start": ce_start,
            "dps_ttm": dps, "roe_history": list(hist), "csm_after_tax": csm,
            "ni_quarters": list(res.quarter_values) if res.value is not None else None,
        }
        # Model H (shareholder_yield.md, Data mapping): share counts as filed, same source.
        conv = meta.get("share_conversion") or 1.0
        sh_t = meta.get(SHARES)
        sh_t = sh_t * conv if sh_t else None
        f_prev = shares_prev_filing(_filings(con, t, meta["source"]), inp.period_end, as_of)
        sh_prev = f_prev.data[SHARES] * conv if f_prev is not None else None
        sf = split_factor(con, t, f_prev.period_end, inp.period_end) if f_prev else 1.0
        h_inputs[t] = sy.SYInputs(
            ticker=t, market=market, sector=inp.sector, eligible=a_elig(e), price=inp.price,
            dps_ttm=dps, shares_t=sh_t, shares_prev=sh_prev, split_factor=sf,
            ni_common_ttm=None if (ni_ccy and rep_ccy and ni_ccy != rep_ccy) else res.value,
            fx_rate=inp.fx_rate,
            flags=frozenset(a.flags & {"PIT_ESTIMATED", "NO_TTM", "STALE_TTM"}))
        details[t].update({"h_shares_t": sh_t, "h_shares_prev": sh_prev, "h_split_factor": sf,
                           "h_period_end_prev": f_prev.period_end.isoformat() if f_prev
                           else None})
        # Model J (ebit_yield_roc.md, Data mapping): balance sheet = the filing behind bvps.
        # NO_TTM / STALE_TTM keep Model A's NI meaning; a missing EBIT TTM shows as NO_DATA.
        j_flags = set(a.flags) & {"PIT_ESTIMATED", "NO_TTM", "STALE_TTM"}
        ebit, ebit_pe = _flow(con, t, EBIT_Q, as_of, inp.reporting_currency, j_flags)
        if ebit_pe is not None and ebit_pe != inp.period_end:
            j_flags.add("EBIT_PERIOD_MISMATCH")
        j_inputs.append(jm.JInputs(
            ticker=t, market=market, sector=inp.sector, eligible=a_elig(e),
            market_cap=inp.price * inp.shares_outstanding, fx_rate=inp.fx_rate, ebit_ttm=ebit,
            total_debt=meta.get(TOTAL_DEBT), cash=meta.get(CASH),
            total_equity=inp.total_equity, preferred=inp.preferred_equity,
            nci=inp.noncontrolling_interest, flags=frozenset(j_flags)))
        details[t].update({"j_ebit_ttm": ebit, "j_ebit_period_end": ebit_pe.isoformat()
                           if ebit_pe else None, "j_total_debt": meta.get(TOTAL_DEBT),
                           "j_cash": meta.get(CASH)})

    subs = dict(con.execute("SELECT ticker, gics_sub_industry FROM securities").fetchall())
    obs = [pr.PBObs(ticker=t, sector=a.sector, price=a.price, bvps=a.b0, roe=a.roe_raw,
                    eligible=None if eligibility.get(t) is None else eligibility[t].eligible,
                    flags=frozenset(), needs_csm=a.no_signal_reason == "NEEDS_CSM_INPUT",
                    sub_industry=subs.get(t) or None)
           for t, a in a_results.items()]
    b_list, fits = pr.run_peer_regression(obs)
    b_results = {b.ticker: b for b in b_list}
    c_results, c_stats, c_extra = _run_model_c(con, as_of, universe, secs, inputs, a_results,
                                               details, eligibility, bars)

    h_results = {t: sy.compute_sy(i) for t, i in h_inputs.items()}
    h_pct: dict[str, float | None] = {}
    for m in ("TSX", "US"):
        h_pct.update(sy.sy_percentiles({t: h.sy for t, h in h_results.items()
                                        if h.sy is not None and h_inputs[t].market == m
                                        and h_inputs[t].eligible is True}))
    rf_m = {m: risk_free(con, as_of, ccy) for m, ccy in (("TSX", "CAD"), ("US", "USD"))}
    j_results = {r.ticker: r for r in jm.run_ebit_yield_roc(j_inputs, rf_m)}

    run_id = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)", [
        run_id, datetime.now(), as_of, "signals", _git_sha(),
        json.dumps({"models": [ri.MODEL_VERSION, pr.MODEL_VERSION, cm.MODEL_VERSION,
                               sy.MODEL_VERSION, jm.MODEL_VERSION],
                    "composite_stats": {"bounds": {"|".join(k): v
                                                   for k, v in c_stats.bounds.items()},
                                        "sectors": {"|".join(k): v.__dict__
                                                    for k, v in c_stats.sectors.items()}},
                    "liquidity": liquidity, "csm_path": str(csm_path),
                    "skipped": skipped,
                    "fits": {s: f.__dict__ for s, f in fits.items()}}, default=str), "ok"])
    records = []
    for t in universe:
        ccy, sector = secs.get(t, (None, None))
        e = eligibility.get(t)
        eligible = None if e is None else e.eligible
        market = market_of(t, ccy)
        a, b = a_results.get(t), b_results.get(t)
        flags = set(e.flags) if e is not None else set()
        if a is None:
            label = "NO_SIGNAL" if eligible is True else "NOT_ELIGIBLE"
            reason = "NO_DATA" if label == "NO_SIGNAL" else None
            rec = {"ticker": t, "market": market, "sector": sector, "eligible": eligible,
                   "elig_fail_reason": e.elig_fail_reason if e else "",
                   "a_signal": label, "a_model_signal": label, "a_no_signal_reason": reason,
                   "b_signal": label, "b_no_signal_reason": reason}
        else:
            flags |= a.flags | b.flags
            rec = {"ticker": t, "market": a.market, "sector": a.sector, "eligible": eligible,
                   "elig_fail_reason": e.elig_fail_reason if e else "", "price": a.price,
                   "bvps": a.b0, "pb": a.pb, "roe0": a.roe0, "a_signal": a.screen_signal,
                   "a_model_signal": a.signal,
                   "a_no_signal_reason": a.no_signal_reason, "a_mos": a.mos, "a_v_ri": a.v_ri,
                   "a_mos_up": a.mos_up, "a_mos_down": a.mos_down,
                   "a_justified_pb": a.justified_pb, "b_signal": b.signal,
                   "b_no_signal_reason": b.no_signal_reason,
                   "b_implied_upside": b.implied_upside, "b_resid_pct": b.resid_pct,
                   "b_fitted_pb": b.fitted_pb, "b_fair_value": b.fair_value}
        c = c_results.get(t)
        if c is not None:
            flags |= set(c.flags)
            rec.update({"c_signal": c.signal, "c_no_signal_reason": c.no_signal_reason,
                        "c_score": c.score, "c_pct": c.pct, "c_n_factors": c.n_factors,
                        "c_value": c.value, "c_quality": c.quality, "c_momentum": c.momentum,
                        "c_risk": c.risk})
        h = h_results.get(t)
        if h is not None:
            flags |= set(h.flags)
            rec.update({"h_signal": h.signal, "h_no_signal_reason": h.no_signal_reason,
                        "h_sy": h.sy, "h_dy": h.dy, "h_bby": h.bby, "h_payout": h.payout,
                        "h_sy_pct": h_pct.get(t)})
        else:
            label = "NO_SIGNAL" if eligible is True else "NOT_ELIGIBLE"
            rec.update({"h_signal": label,
                        "h_no_signal_reason": "NO_DATA" if label == "NO_SIGNAL" else None})
        j = j_results.get(t)
        if j is not None:
            flags |= set(j.flags)
            rec.update({"j_signal": j.signal, "j_no_signal_reason": j.no_signal_reason,
                        "j_ey": j.ey, "j_roc": j.roc, "j_ev": j.ev, "j_rank_ey": j.rank_ey,
                        "j_rank_roc": j.rank_roc, "j_pct": j.pct})
        else:
            label = "NO_SIGNAL" if eligible is True else "NOT_ELIGIBLE"
            rec.update({"j_signal": label,
                        "j_no_signal_reason": "NO_DATA" if label == "NO_SIGNAL" else None})
        rec["flags"] = ",".join(sorted(flags))
        records.append(rec)
        payload = {**rec, **details.get(t, {})}
        if c is not None:
            payload.update({"c_metrics": c.metrics, "c_winsorized": c.winsorized, "c_z": c.z,
                            "c_mom_pct": c.mom_pct, "c_factor_pct": c.factor_pct,
                            "c_factor_labels": c.factor_labels, **c_extra.get(t, {})})
        if a is not None:
            payload.update({"roe_raw": a.roe_raw, "roe_basis": a.roe_basis, "r": a.r, "b": a.b,
                            "eps_ttm": a.eps_ttm, "v_ri_up": a.v_ri_up,
                            "v_ri_down": a.v_ri_down, "b_residual": b.residual,
                            "b_roe_w": b.roe_w, "b_n_fit": b.n_fit, "b_r2": b.r2,
                            "a_one_off": a.one_off, "a_ni_norm": a.ni_norm,
                            "a_roe_norm": a.roe_norm})
        if h is not None:
            payload.update({"h_eps_ttm": h.eps_ttm, "h_funded": h.funded})
        if j is not None:
            payload.update({"j_ic": j.ic, "j_combined": j.combined, "j_position": j.position,
                            "j_n": j.n})
        con.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?)",
                    [run_id, t, as_of,
                     f"{ri.MODEL_VERSION},{pr.MODEL_VERSION},{cm.MODEL_VERSION},{sy.MODEL_VERSION},"
                     f"{jm.MODEL_VERSION}",
                     rec["a_signal"], rec["b_signal"], json.dumps(payload, default=str)])
    df = pd.DataFrame.from_records(records, columns=SIGNAL_COLUMNS)
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for model, col in (("A", "a_signal"), ("B", "b_signal"), ("C", "c_signal"),
                       ("H", "h_signal"), ("J", "j_signal")):
        counts[model] = {}
        for m in ("TSX", "US"):
            sub = df[df["market"] == m]
            if len(sub):
                counts[model][m] = {lab: int((sub[col] == lab).sum()) for lab in LABELS}
    return df, {"run_id": run_id, "fits": fits, "counts": counts, "skipped": skipped,
                "eligibility": eligibility, "composite_stats": c_stats}


def _flow(con, ticker: str, item: str, as_of: date, rep_ccy: str | None, flags: set[str]):
    """(TTM value, period_end) of a flow item, (None, None) on NO_TTM or a currency that is not
    the balance sheet's (flag FLOW_CCY_MISMATCH). Spec composite.md, Data mapping."""
    res, _src, ccy = flow_ttm(con, ticker, item, as_of)
    if res.value is None:
        return None, None
    if ccy and rep_ccy and ccy != rep_ccy:
        flags.add("FLOW_CCY_MISMATCH")
        return None, None
    return res.value, res.period_end


def _run_model_c(con, as_of, universe, secs, inputs, a_results, details, eligibility, bars):
    """Model C (spec docs/models/composite.md, Data mapping): one CompositeInputs per universe
    ticker; v0.2.0 statistics per market."""
    from .models import composite as cm
    from .providers.base import CAPEX_Q, CFO_Q, GROSS_PROFIT_Q

    by_ticker = {i.ticker: (i, m) for i, m in inputs}
    c_inputs, extra = [], {}
    for t in universe:
        e = eligibility.get(t)
        closes = [b.adj_close for b in bars.get(t, []) if b.adj_close is not None
                  and b.date <= as_of]
        closes = [x for x in closes if x > 0]
        mom, vol = cm.momentum(closes), cm.volatility(closes)
        extra[t] = {"c_mom": mom, "c_vol": vol}
        common = {"ticker": t, "sector": secs.get(t, (None, None))[1],
                  "market": market_of(t, secs.get(t, (None, None))[0]) or "US",
                  "eligible": None if e is None else e.eligible,
                  "history_ok": None if e is None else e.model_c_history_ok,
                  "mom": mom, "vol": vol}
        if t not in by_ticker or t not in a_results:
            c_inputs.append(cm.CompositeInputs(**common))
            continue
        inp, meta = by_ticker[t]
        a = a_results[t]
        common["sector"] = a.sector
        rep = inp.reporting_currency
        flags: set[str] = set(a.flags) & {"NO_TTM", "STALE_TTM", "PIT_ESTIMATED"}
        ni_ccy = details[t].get("ni_ccy")
        ni = details[t]["ni_common_ttm"]
        if ni is not None and ni_ccy and rep and ni_ccy != rep:
            ni = None  # Model A's CCY_MISMATCH
        ni_pe = details[t]["ttm_period_end"]
        cfo, cfo_pe = _flow(con, t, CFO_Q, as_of, rep, flags)
        capex, capex_pe = _flow(con, t, CAPEX_Q, as_of, rep, flags)
        gp, _ = _flow(con, t, GROSS_PROFIT_Q, as_of, rep, flags)
        c_inputs.append(cm.CompositeInputs(
            **common, price=inp.price, market_cap=inp.price * inp.shares_outstanding,
            fx_rate=inp.fx_rate, bvps=a.b0, ni_ttm=ni,
            ni_period_end=date.fromisoformat(ni_pe) if ni_pe else None, roe=a.roe_raw,
            cfo_ttm=cfo, cfo_period_end=cfo_pe, capex_ttm=capex, capex_period_end=capex_pe,
            gross_profit_ttm=gp, total_assets=meta.get("total_assets"),
            total_debt=meta.get("total_debt"), flags=frozenset(flags)))
    results, stats = cm.run_composite(c_inputs)
    return {r.ticker: r for r in results}, stats, extra
