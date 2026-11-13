"""Backtest results: per-model metrics, pass/fail, Phase 7 diagnostics, survivorship.

Spec: docs/models/backtest.md §4, §5, §7. Input: the per-date rows (`bt_rows`, written by the
runner), `bt_returns`, `bt_universe`, `bt_ticker_coverage`. Output tables (spec §7): `bt_signals`
(long, per model), `bt_metrics`, `bt_diagnostics`, `bt_coverage`, plus a `bt_runs` row per run.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from unittest import mock

import numpy as np
import pandas as pd

from . import engine as bte
from . import metrics as m

MODELS = {"A": "Model A (residual income, model label incl. SELL)",
          "B": "Model B (P/B ~ ROE), DIAGNOSTIC ONLY: pass line printed, never a verdict",
          "A_BOOK": "Model A restricted to book sectors",
          "BASELINE_PB": "Baseline: low P/B buckets (book sectors)",
          "C": "Model C (value-quality-momentum-risk composite)",
          "C_V": "Model C factor V (value), diagnostic",
          "C_Q": "Model C factor Q (quality), diagnostic",
          "C_M": "Model C factor M (momentum 12-1), diagnostic",
          "C_R": "Model C factor R (low volatility), diagnostic",
          "H": "Model H (shareholder yield)",
          "H_BBY": "Model H part BBY (net buyback yield), diagnostic: IC only",
          "H_DY": "Model H part DY (dividend yield), diagnostic: IC only"}
DIAGNOSTIC_MODELS = frozenset({"B", "A_BOOK", "BASELINE_PB", "C_V", "C_Q", "C_M", "C_R",
                               "H_BBY", "H_DY"})
# per_company_fit.md §2.2: opt-in (`report --extra-models`) diagnostic rows for the unvalidated
# models (not frozen, not in FROZEN_V2). Never a verdict; never computed on holdout dates.
EXTRA_MODELS = {"J": "Model J (EBIT yield + ROC), UNVALIDATED, diagnostic, never a verdict",
                "G": "Model G (Altman Z), UNVALIDATED, diagnostic, never a verdict",
                "D": "Model D (dividend sustainability), UNVALIDATED, diagnostic, never a verdict",
                "E": "Model E (Piotroski F-score), UNVALIDATED, diagnostic, never a verdict",
                "F": "Model F (accruals), UNVALIDATED, diagnostic, never a verdict"}
# label column, score column, score sign (score where the label is BUY/HOLD/SELL)
EXTRA_SCORES = {"J": ("j_signal", "j_pct", -1.0), "G": ("g_signal", "g_z", 1.0),
                "D": ("d_signal", "d_mos", 1.0), "E": ("e_signal", "e_f_score", 1.0),
                "F": ("f_signal", "f_acc_pct", -1.0)}
# Model H parts (freeze §3): IC only, so every scored name gets the neutral label HOLD
H_PARTS = {"H_BBY": "h_bby", "H_DY": "h_dy"}
# Model C diagnostic factors (composite.md, Data mapping / Backtest): label column, score column.
C_FACTORS = {"C_V": ("c_v_label", "c_value"), "C_Q": ("c_q_label", "c_quality"),
             "C_M": ("c_m_label", "c_momentum"), "C_R": ("c_r_label", "c_risk")}
Q1_PB_SPLIT = 2.9
Q4_MIN_SUBIND = 12
PRICE_LOOKBACK_DAYS = 10

OUT_DDL = [
    """CREATE TABLE IF NOT EXISTS bt_signals (run_id VARCHAR, date DATE, ticker VARCHAR,
        model VARCHAR, label VARCHAR, score DOUBLE, sector VARCHAR, eligible BOOLEAN, pb DOUBLE,
        flags VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS bt_metrics (run_id VARCHAR, model VARCHAR, scope VARCHAR,
        horizon INTEGER, metric VARCHAR, key VARCHAR, value DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS bt_diagnostics (run_id VARCHAR, question VARCHAR, model VARCHAR,
        bucket VARCHAR, horizon INTEGER, n INTEGER, mean_excess DOUBLE, hit_rate DOUBLE,
        mean_ic DOUBLE, n_ic_dates INTEGER, mean_spread DOUBLE, n_spread_dates INTEGER)""",
    """CREATE TABLE IF NOT EXISTS bt_coverage (run_id VARCHAR, date DATE, n_members INTEGER,
        n_with_price INTEGER, missing_price INTEGER, missing_fundamentals INTEGER)""",
]


def _d(x):
    """Timestamp / datetime -> date (a pandas Timestamp is also a `date` instance)."""
    return x.date() if isinstance(x, datetime) else x


def _f(v) -> float:
    return math.nan if v is None else float(v)


# ---------- panels ----------

def load_rows(con, market: str) -> pd.DataFrame:
    rows = bte.load_signals(con, market)
    rows["date"] = [_d(x) for x in rows["date"]]
    sub = dict(con.execute("SELECT ticker, gics_sub_industry FROM securities").fetchall())
    rows["sub_industry"] = rows["ticker"].map(sub)
    rows["flags"] = rows["flags"].fillna("")
    rows["eligible"] = rows["eligible"].fillna(False).astype(bool)
    if market == "TSX":
        rows = rows[rows["market"] == "TSX"].copy()
    return rows.reset_index(drop=True)


def model_panel(rows: pd.DataFrame, model: str) -> pd.DataFrame:
    keep = ["date", "ticker", "sector", "eligible", "pb", "flags", "sub_industry", "price",
            "bvps", "roe_raw", "a_reason"]
    x = rows[keep].copy()
    if model in ("A", "A_BOOK"):
        x["label"] = rows["a_signal"].to_numpy()
        x["score"] = np.where(rows["a_signal"].isin(m.SIGNALS), rows["a_mos"], np.nan)
        if model == "A_BOOK":
            book = rows["sector"].isin(m.BOOK_SECTORS).to_numpy()
            x["label"] = np.where(book, x["label"], "NO_SIGNAL")
            x["score"] = np.where(book, x["score"], np.nan)
    elif model == "B":
        x["label"] = rows["b_signal"].to_numpy()
        x["score"] = np.where(rows["b_signal"].isin(m.SIGNALS) & rows["b_residual"].notna(),
                              -rows["b_residual"], np.nan)
    elif model == "BASELINE_PB":
        pb_in = rows[["date", "ticker", "sector", "eligible", "price", "bvps"]].copy()
        # unadjusted book only: CSM-adjusted insurers (TSX) have no unadjusted bvps here
        pb_in.loc[rows["flags"].str.contains("CSM_ADJUSTED"), "bvps"] = np.nan
        base = m.low_pb_baseline(pb_in)
        x = x.drop(columns=["pb"]).merge(base[["date", "ticker", "pb", "label", "score"]],
                                         on=["date", "ticker"], how="left")
    elif model == "C":
        x["label"] = rows["c_signal"].to_numpy()
        x["score"] = np.where(rows["c_signal"].isin(m.SIGNALS), rows["c_score"], np.nan)
    elif model in C_FACTORS:
        lab, sc = C_FACTORS[model]
        x["label"] = rows[lab].fillna("NO_SIGNAL").to_numpy()
        x["score"] = np.where(rows[lab].notna(), rows[sc], np.nan)
    elif model == "H":
        x["label"] = rows["h_signal"].fillna("NO_SIGNAL").to_numpy()
        x["score"] = np.where(rows["h_signal"].isin(m.SIGNALS), rows["h_score"], np.nan)
    elif model in EXTRA_SCORES:
        lab, sc, sign = EXTRA_SCORES[model]
        labels = rows[lab] if lab in rows.columns else pd.Series(None, index=rows.index,
                                                                   dtype=object)
        score = (rows[sc].astype(float) if sc in rows.columns
                 else pd.Series(np.nan, index=rows.index))
        x["label"] = labels.fillna("NO_SIGNAL").to_numpy()
        x["score"] = np.where(labels.isin(m.SIGNALS), sign * score, np.nan)
    elif model in H_PARTS:
        ok = rows["h_signal"].isin(m.SIGNALS) & rows[H_PARTS[model]].notna()
        x["label"] = np.where(ok, "HOLD", "NO_SIGNAL")
        x["score"] = np.where(ok, rows[H_PARTS[model]], np.nan)
    else:
        raise ValueError(model)
    return x


def load_returns(con) -> pd.DataFrame:
    """Whole-market horizons only (per_company_fit.md §2.2): the h = 1 rows written for the
    per-company fit must never reach sector_excess, which takes horizons from the data."""
    hs = ", ".join(str(h) for h in m.HORIZONS)
    r = con.execute("SELECT date, ticker, horizon, ret, flag, t1 FROM bt_returns "
                    f"WHERE horizon IN ({hs})").df()
    r["date"] = [_d(x) for x in r["date"]]
    r["t1"] = [None if pd.isna(x) else _d(x) for x in r["t1"]]
    return r


def lock_in_sample(fwd: pd.DataFrame, in_end: date) -> pd.DataFrame:
    """backtest.md §2 in-sample exit lock: a row dated on or before `in_end` keeps its return
    only if its exit session t1 is on or before `in_end`; otherwise ret NaN, flag INCOMPLETE.
    Rows dated after `in_end` are unchanged. Returns a copy."""
    out = fwd.copy()
    if out.empty:
        return out
    t1 = out["t1"] if "t1" in out else pd.Series([None] * len(out), index=out.index)
    late = [d <= in_end and (x is None or pd.isna(x) or _d(x) > in_end)
            for d, x in zip(out["date"], t1, strict=True)]
    mask = pd.Series(late, index=out.index)
    out["ret"] = out["ret"].astype(float)
    out.loc[mask, "ret"] = float("nan")
    out["flag"] = pd.Series([m.INCOMPLETE if lk else f for lk, f in zip(late, out["flag"],
                                                                         strict=True)],
                            index=out.index, dtype=object)
    return out


# ---------- metrics per model ----------

def _scope_metrics(obs: pd.DataFrame, panel: pd.DataFrame, dates: list[date]) -> dict:
    ds = set(dates)
    bd = m.spread_by_date(obs, ds)
    yearly = m.yearly_spread(bd)
    hits = m.hit_rates(obs, ds)
    ic = m.rank_ic(obs, ds)
    out = {"by_date": bd, "yearly": yearly, "hits": hits, "ic": ic, "h": {}}
    for h in m.HORIZONS:
        ich = ic[(ic["horizon"] == h) & ic["ic"].notna()].sort_values("date")
        bdh = bd[(bd["horizon"] == h) & bd["spread"].notna()]
        hr = hits[hits["horizon"] == h]
        b = obs[(obs["horizon"] == h) & obs["date"].isin(ds)]
        out["h"][h] = {
            "ic_mean": float(ich["ic"].mean()) if len(ich) else math.nan,
            "ic_t": m.newey_west_t(ich["ic"].tolist(), h - 1), "n_ic": len(ich),
            "mean_spread": float(bdh["spread"].mean()) if len(bdh) else math.nan,
            "n_spread": len(bdh),
            "buy_hit": float(hr["buy_hit_rate"].iloc[0]) if len(hr) else math.nan,
            "sell_hit": float(hr["sell_hit_rate"].iloc[0]) if len(hr) else math.nan,
            "n_buy": int(hr["n_buy"].iloc[0]) if len(hr) else 0,
            "n_sell": int(hr["n_sell"].iloc[0]) if len(hr) else 0,
            "buy_excess": float(b.loc[(b["label"] == "BUY"), "excess"].mean()),
            "sell_excess": float(b.loc[(b["label"] == "SELL"), "excess"].mean()),
            "early_exit": int((b["label"].isin(("BUY", "SELL"))
                               & (b["flag"] == m.EARLY_EXIT)).sum()),
        }
    to = m.turnover(panel[["date", "ticker", "label"]], ds)
    out["turnover"] = {k: float(to[k].mean()) if len(to) else math.nan
                       for k in ("label_turnover", "buy_turnover", "sell_turnover")}
    cnt = (panel[panel["date"].isin(ds)].groupby(["date", "label"]).size()
           .unstack(fill_value=0))
    cnt["year"] = [d.year for d in cnt.index]
    out["counts_by_year"] = cnt.groupby("year").mean().round(1)
    return out


def model_metrics(rows: pd.DataFrame, fwd: pd.DataFrame, model: str, ins: list[date],
                  ho: list[date] | None) -> dict:
    panel = model_panel(rows, model)
    obs = m.sector_excess(fwd, panel)
    res = {"model": model, "panel": panel, "obs": obs, "in": _scope_metrics(obs, panel, ins)}
    if ho:
        res["ho"] = _scope_metrics(obs, panel, ho)
    y = res["in"]["yearly"]
    y12 = y[y["horizon"] == m.PASS_HORIZON]["spread"].tolist()
    h12 = res["in"]["h"][12]
    ho12 = res["ho"]["h"][12] if ho else {}
    res["pass"] = m.pass_bar(y12, h12["ic_mean"], h12["ic_t"], ho12.get("ic_mean"),
                             ho12.get("mean_spread"))
    return res


def metric_rows(run_id: str, res: dict) -> list[list]:
    out = []
    for scope in ("in", "ho"):
        if scope not in res:
            continue
        sc = {"in": "in_sample", "ho": "holdout"}[scope]
        s = res[scope]
        for h, v in s["h"].items():
            for k, name in (("ic_mean", "mean_ic"), ("ic_t", "nw_t"), ("n_ic", "n_ic_dates"),
                            ("mean_spread", "mean_spread"), ("buy_hit", "buy_hit_rate"),
                            ("sell_hit", "sell_hit_rate"), ("n_buy", "n_buy"),
                            ("n_sell", "n_sell")):
                out.append([run_id, res["model"], sc, h, name, "", _f(v[k])])
        for _, r in s["yearly"].iterrows():
            out.append([run_id, res["model"], sc, int(r["horizon"]), "yearly_spread",
                        str(int(r["year"])), float(r["spread"])])
        for k, v in s["turnover"].items():
            out.append([run_id, res["model"], sc, None, k, "", _f(v)])
    p = res["pass"]
    out.append([run_id, res["model"], "in_sample", 12, "pass_result",
                f"{p.result}|failed={','.join(p.failed_legs)}|"
                f"incomplete={','.join(p.incomplete_legs)}",
                {"PASS": 1.0, "FAIL": 0.0}.get(p.result, math.nan)])
    return out


# ---------- diagnostics ----------

def _bucket(obs: pd.DataFrame, mask, side: str, dates) -> dict:
    s = obs[mask & obs["date"].isin(set(dates)) & obs["excess"].notna()]
    hit = (s["excess"] > 0) if side == "BUY" else (s["excess"] < 0)
    return {"n": len(s), "mean_excess": float(s["excess"].mean()) if len(s) else math.nan,
            "median_excess": float(s["excess"].median()) if len(s) else math.nan,
            "hit_rate": float(hit.mean()) if len(s) else math.nan,
            "mean_ret": float(s["ret"].mean()) if len(s) else math.nan,
            "mean_sector_median": float(s["sector_median"].mean()) if len(s) else math.nan}


def diagnostics(a_obs: pd.DataFrame, b_obs: pd.DataFrame, rows: pd.DataFrame,
                dates: list[date]) -> list[dict]:
    out = []
    for h in m.HORIZONS:
        a = a_obs[a_obs["horizon"] == h]
        b = b_obs[b_obs["horizon"] == h]
        clipped = a["flags"].str.contains("ROE_CLIPPED")
        neg = b["flags"].str.contains("NEG_ROE_UNFITTED")
        specs = [
            ("Q1", "A", "SELL_ALL", a, a["label"] == "SELL", "SELL"),
            ("Q1", "A", "PB_GT_2_9", a, (a["label"] == "SELL") & (a["pb"] > Q1_PB_SPLIT), "SELL"),
            ("Q1", "A", "PB_LE_2_9", a, (a["label"] == "SELL") & (a["pb"] <= Q1_PB_SPLIT),
             "SELL"),
            ("Q1", "A", "HOLD_REF", a, a["label"] == "HOLD", "SELL"),
            ("Q2", "A", "CLIPPED", a, (a["label"] == "BUY") & clipped, "BUY"),
            ("Q2", "A", "NOT_CLIPPED", a, (a["label"] == "BUY") & ~clipped, "BUY"),
            ("Q3", "B", "NEG_ROE_UNFITTED", b, (b["label"] == "BUY") & neg, "BUY"),
            ("Q3", "B", "ROE_NONNEG", b, (b["label"] == "BUY") & ~neg, "BUY"),
        ]
        for q, model, bucket, obs, mask, side in specs:
            out.append({"question": q, "model": model, "bucket": bucket, "horizon": h,
                        **_bucket(obs, mask, side, dates)})
    out += financials_split(b_obs, rows, dates)
    return out


Q4_EXCLUDED = ("Transaction & Payment Processing Services", "Financial Exchanges & Data")


def _refit(day: pd.DataFrame, mode: str = "split") -> pd.DataFrame:
    """Model B for Financials only, grouped by GICS sub-industry where a sub-industry has
    >= Q4_MIN_SUBIND names in the fit set; the rest pools as 'Financials-other' (spec §7 Q4)."""
    from ..models import peer_regression as pr

    f = day[(day["sector"] == "Financials") & (day["price"] > 0)]
    obs = [pr.PBObs(ticker=r.ticker, sector="Financials", price=float(r.price),
                    bvps=None if pd.isna(r.bvps) else float(r.bvps),
                    roe=None if pd.isna(r.roe_raw) else float(r.roe_raw),
                    eligible=bool(r.eligible), needs_csm=r.a_reason == "NEEDS_CSM_INPUT")
           for r in f.itertuples()]
    if not obs:
        return pd.DataFrame(columns=["ticker", "label", "score", "group"])
    sub = dict(zip(f["ticker"], f["sub_industry"], strict=True))
    if mode == "ex_pay_data":  # P7-14: payments/exchanges/data names left out of the fit set
        obs = [o for o in obs if sub.get(o.ticker) not in Q4_EXCLUDED]
        groups = {o.ticker: "Financials ex payments/exchanges" for o in obs}
        with mock.patch.object(pr, "BOOK_SECTORS", frozenset(groups.values())):
            res, _ = pr.run_peer_regression(
                [pr.PBObs(**{**o.__dict__, "sector": groups[o.ticker]}) for o in obs])
        return pd.DataFrame([{"ticker": r.ticker, "label": r.signal,
                              "score": -r.residual if r.residual is not None
                              and r.signal in m.SIGNALS else math.nan,
                              "group": groups[r.ticker]} for r in res])
    n_fit: dict = {}
    for o in obs:
        if pr._in_fit(o) and sub.get(o.ticker):
            n_fit[sub[o.ticker]] = n_fit.get(sub[o.ticker], 0) + 1
    groups = {}
    for o in obs:
        si = sub.get(o.ticker)
        groups[o.ticker] = (f"Financials|{si}" if si and n_fit.get(si, 0) >= Q4_MIN_SUBIND
                            else "Financials-other")
    obs = [pr.PBObs(**{**o.__dict__, "sector": groups[o.ticker]}) for o in obs]
    with mock.patch.object(pr, "BOOK_SECTORS", frozenset(groups.values())):
        res, _ = pr.run_peer_regression(obs)
    return pd.DataFrame([{"ticker": r.ticker, "label": r.signal,
                          "score": -r.residual if r.residual is not None
                          and r.signal in m.SIGNALS else math.nan,
                          "group": groups[r.ticker]} for r in res])


def financials_split(b_obs: pd.DataFrame, rows: pd.DataFrame, dates: list[date]) -> list[dict]:
    out = []
    ds = set(dates)
    base = b_obs[(b_obs["sector"] == "Financials") & b_obs["date"].isin(ds)]
    day_rows = rows[(rows["sector"] == "Financials") & rows["date"].isin(ds)]
    variants = {"B": base}
    groups = []
    for bucket, mode in (("B_FIN_SPLIT", "split"), ("B_FIN_EX_PAY_DATA", "ex_pay_data")):
        refit = []
        for d, day in day_rows.groupby("date"):
            rf = _refit(day, mode)
            rf["date"] = d
            refit.append(rf)
        refit = (pd.concat(refit) if refit else
                 pd.DataFrame(columns=["ticker", "label", "score", "group", "date"]))
        v = base.drop(columns=["label", "score"]).merge(
            refit[["date", "ticker", "label", "score", "group"]], on=["date", "ticker"],
            how="left")
        v["label"] = v["label"].fillna("NO_SIGNAL")
        if mode == "split":
            groups = sorted(v["group"].dropna().unique().tolist())
        else:  # same subset as B for comparability: the excluded names get no label
            v = v[~v["sub_industry"].isin(Q4_EXCLUDED)]
        variants[bucket] = v
    variants["B_EX_PAY_DATA_SUBSET"] = base[~base["sub_industry"].isin(Q4_EXCLUDED)]
    for bucket, obs in variants.items():
        bd = m.spread_by_date(obs, ds)
        ic = m.rank_ic(obs, ds)
        hits = m.hit_rates(obs, ds)
        for h in m.HORIZONS:
            ich = ic[(ic["horizon"] == h) & ic["ic"].notna()]
            bdh = bd[(bd["horizon"] == h) & bd["spread"].notna()]
            hr = hits[hits["horizon"] == h]
            out.append({
                "question": "Q4", "model": "B", "bucket": bucket, "horizon": h,
                "n": int(hr["n_buy"].iloc[0] + hr["n_sell"].iloc[0]) if len(hr) else 0,
                "mean_excess": math.nan,
                "hit_rate": float(hr["buy_hit_rate"].iloc[0]) if len(hr) else math.nan,
                "sell_hit_rate": float(hr["sell_hit_rate"].iloc[0]) if len(hr) else math.nan,
                "n_buy": int(hr["n_buy"].iloc[0]) if len(hr) else 0,
                "n_sell": int(hr["n_sell"].iloc[0]) if len(hr) else 0,
                "mean_ic": float(ich["ic"].mean()) if len(ich) else math.nan,
                "ic_t": m.newey_west_t(ich["ic"].tolist(), h - 1), "n_ic_dates": len(ich),
                "mean_spread": float(bdh["spread"].mean()) if len(bdh) else math.nan,
                "n_spread_dates": len(bdh),
                "groups": groups if bucket == "B_FIN_SPLIT" else ["Financials"]})
    return out


# ---------- survivorship ----------

def coverage(con, market: str) -> pd.DataFrame:
    """Per date: members, with price, missing_price (spec §5 incl. the 10-day bar check),
    missing_fundamentals (priced members without any fundamentals row)."""
    return con.execute(f"""
        WITH u AS (SELECT date, ticker, has_price FROM bt_universe WHERE universe = ?),
        bar AS (SELECT u.date, u.ticker, u.has_price AND EXISTS (
                  SELECT 1 FROM prices_daily p WHERE p.ticker = u.ticker
                  AND p.date > u.date - INTERVAL {PRICE_LOOKBACK_DAYS} DAY AND p.date <= u.date)
                  AS priced,
                EXISTS (SELECT 1 FROM fundamentals f WHERE f.ticker = u.ticker) AS has_facts
                FROM u)
        SELECT date, count(*) AS n_members, sum(priced::INT) AS n_with_price,
               sum((NOT priced)::INT) AS missing_price,
               sum((priced AND NOT has_facts)::INT) AS missing_fundamentals
        FROM bar GROUP BY date ORDER BY date""", [market]).df()


def survivorship(con, market: str, cov: pd.DataFrame) -> dict:
    tc = con.execute("SELECT ticker, member_source, has_price, price_reason "
                     "FROM bt_ticker_coverage").df()
    u = con.execute("SELECT DISTINCT ticker FROM bt_universe WHERE universe=?", [market]).df()
    tc = tc[tc["ticker"].isin(set(u["ticker"]))]
    removed = tc[tc["member_source"] == "removed"]
    total = int(cov["n_members"].sum()) if len(cov) else 0
    miss = int(cov["missing_price"].sum()) if len(cov) else 0
    return {"member_dates": total, "member_dates_missing_price": miss,
            "share_missing": miss / total if total else math.nan,
            "distinct_members": len(tc), "removed_names": len(removed),
            "removed_lacking_prices": int((~removed["has_price"].astype(bool)).sum()),
            "current_lacking_prices": int(
                (~tc[tc["member_source"] == "current"]["has_price"].astype(bool)).sum()),
            "reasons": removed.loc[~removed["has_price"].astype(bool), "price_reason"]
            .fillna("?").str.split(":").str[0].value_counts().to_dict(),
            "missing_fundamentals_member_dates": int(cov["missing_fundamentals"].sum())
            if len(cov) else 0}


# ---------- persistence ----------

def persist(con, run_id: str, results: dict, diag: list[dict], cov: pd.DataFrame) -> None:
    for ddl in OUT_DDL:
        con.execute(ddl)
    for model, res in results.items():
        p = res["panel"]
        df = pd.DataFrame({"run_id": run_id, "date": p["date"], "ticker": p["ticker"],
                           "model": model, "label": p["label"], "score": p["score"],
                           "sector": p["sector"], "eligible": p["eligible"], "pb": p["pb"],
                           "flags": p["flags"]})
        con.register("_df", df)
        con.execute("INSERT INTO bt_signals SELECT * FROM _df")
        con.unregister("_df")
        con.executemany("INSERT INTO bt_metrics VALUES (?,?,?,?,?,?,?)",
                        metric_rows(run_id, res))
    con.executemany("INSERT INTO bt_diagnostics VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        [run_id, d["question"], d["model"], d["bucket"], d["horizon"], d["n"],
         _f(d.get("mean_excess")), _f(d.get("hit_rate")), _f(d.get("mean_ic")),
         d.get("n_ic_dates"), _f(d.get("mean_spread")), d.get("n_spread_dates")] for d in diag])
    c = cov.assign(run_id=run_id)[["run_id", "date", "n_members", "n_with_price",
                                   "missing_price", "missing_fundamentals"]]
    con.register("_c", c)
    con.execute("INSERT INTO bt_coverage SELECT * FROM _c")
    con.unregister("_c")


# ---------- markdown ----------

def fmt(v, pct=False, nd=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v * 100:+.2f}%" if pct else f"{v:.{nd}f}"


def render_model(res: dict, label: str) -> str:
    p = res["pass"]
    legs = []
    if p.failed_legs:
        legs.append("failed: " + ", ".join(p.failed_legs))
    if p.incomplete_legs:
        legs.append("incomplete: " + ", ".join(p.incomplete_legs))
    L = [f"### {label}: **{p.result}**" + (f" ({'; '.join(legs)})" if legs else ""), "",
         "| Scope | Horizon | Mean IC | NW t (lag) | IC dates | Mean spread | spread dates | "
         "BUY hit | BUY obs | SELL hit | SELL obs | BUY excess | SELL excess |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for scope in ("in", "ho"):
        if scope not in res:
            continue
        for h, v in res[scope]["h"].items():
            L.append(f"| {'in-sample' if scope == 'in' else 'holdout'} | {h}m | "
                     f"{fmt(v['ic_mean'], nd=4)} | {fmt(v['ic_t'], nd=2)} ({h - 1}) | "
                     f"{v['n_ic']} | {fmt(v['mean_spread'], True)} | {v['n_spread']} | "
                     f"{fmt(v['buy_hit'], True)} | {v['n_buy']} | {fmt(v['sell_hit'], True)} | "
                     f"{v['n_sell']} | {fmt(v['buy_excess'], True)} | "
                     f"{fmt(v['sell_excess'], True)} |")
    L += ["", "Per-year sector-neutral BUY − SELL spread (in-sample; mean of monthly spreads, "
          "equal-weighted):", "", "| Year | 3m | 6m | 12m | dates (12m) |",
          "|---|---|---|---|---|"]
    y = res["in"]["yearly"]
    for yr in sorted(y["year"].unique()):
        cells = []
        for h in m.HORIZONS:
            r = y[(y["year"] == yr) & (y["horizon"] == h)]
            cells.append(fmt(float(r["spread"].iloc[0]), True) if len(r) else "n/a")
        r12 = y[(y["year"] == yr) & (y["horizon"] == 12)]
        n12 = int(r12["n_dates"].iloc[0]) if len(r12) else 0
        L.append(f"| {yr} | {' | '.join(cells)} | {n12} |")
    y12 = y[y["horizon"] == 12]["spread"]
    L.append(f"| **years > 0 (12m)** | | | **{int((y12 > 0).sum())}/{len(y12)}** | |")
    t = res["in"]["turnover"]
    L += ["", f"Turnover (monthly mean, in-sample): label {fmt(t['label_turnover'], True)}, "
          f"BUY set {fmt(t['buy_turnover'], True)}, SELL set {fmt(t['sell_turnover'], True)}.",
          "", "Signal counts (mean per month-end, in-sample, by year):", ""]
    c = res["in"]["counts_by_year"]
    cols = [k for k in ("BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE") if k in c.columns]
    L += ["| Year | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for yr, row in c.iterrows():
        L.append(f"| {yr} | " + " | ".join(f"{row[k]:.1f}" for k in cols) + " |")
    if "ho" in res:
        c = res["ho"]["counts_by_year"]
        L += ["", "Holdout signal counts (mean per month-end): " + ", ".join(
            f"{k} {c[k].mean():.1f}" for k in cols if k in c.columns)]
    return "\n".join(L)


def render_diag(diag: list[dict]) -> str:
    L = []
    titles = {"Q1": "Model A SELL bucket vs its sector median",
              "Q2": "Model A BUYs with / without ROE_CLIPPED",
              "Q3": "Model B BUYs on loss-makers (NEG_ROE_UNFITTED)"}
    for q, title in titles.items():
        L += [f"#### {q}: {title}", "", "| Horizon | Bucket | n obs | mean excess | "
              "median excess | hit vs sector | mean return | mean sector median |",
              "|---|---|---|---|---|---|---|---|"]
        for d in diag:
            if d["question"] == q:
                L.append(f"| {d['horizon']}m | {d['bucket']} | {d['n']} | "
                         f"{fmt(d['mean_excess'], True)} | {fmt(d['median_excess'], True)} | "
                         f"{fmt(d['hit_rate'], True)} | {fmt(d['mean_ret'], True)} | "
                         f"{fmt(d['mean_sector_median'], True)} |")
        L.append("")
    L += ["#### Q4: Financials, Model B as specified vs split by GICS sub-industry (diagnostic)",
          "", "| Horizon | Variant | Mean IC | NW t | IC dates | mean spread | spread dates | "
          "BUY hit | SELL hit | BUY obs | SELL obs |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    groups = []
    for d in diag:
        if d["question"] == "Q4":
            L.append(f"| {d['horizon']}m | {d['bucket']} | {fmt(d['mean_ic'], nd=4)} | "
                     f"{fmt(d['ic_t'], nd=2)} | {d['n_ic_dates']} | "
                     f"{fmt(d['mean_spread'], True)} | {d['n_spread_dates']} | "
                     f"{fmt(d['hit_rate'], True)} | {fmt(d['sell_hit_rate'], True)} | "
                     f"{d['n_buy']} | {d['n_sell']} |")
            if d["bucket"] == "B_FIN_SPLIT":
                groups = d["groups"]
    if groups:
        L += ["", "Split groups that occurred: " + "; ".join(groups)]
    return "\n".join(L)


# ---------- benchmark excess (benchmarks.md, API contract; P8-10) ----------

def benchmark_fwd(con, key: str, dates: list[date]) -> pd.DataFrame:
    """The benchmark's forward return per (date, horizon), same t0/t1 rule as the stocks."""
    try:
        px = con.execute("SELECT key AS ticker, date, adj_close FROM benchmarks_daily "
                         "WHERE key=?", [key]).df()
    except Exception:  # noqa: BLE001 - table absent: run `valuation backtest benchmarks`
        return pd.DataFrame(columns=["date", "horizon", "ret"])
    if px.empty:
        return pd.DataFrame(columns=["date", "horizon", "ret"])
    px["date"] = [_d(x) for x in px["date"]]
    fr = m.forward_returns(px, dates)
    return fr[["date", "horizon", "ret", "t1"]]


def benchmark_excess_tables(results: dict, bench: pd.DataFrame, dates: list[date],
                            models=("A", "B", "C", "H")) -> dict[str, pd.DataFrame]:
    out = {}
    for model in models:
        if model not in results:
            continue
        obs = results[model]["obs"]
        obs = obs[obs["eligible"]][["date", "ticker", "horizon", "label", "ret"]]
        out[model] = m.benchmark_excess(obs, bench, set(dates))
    return out


def render_benchmark_excess(tables: dict[str, pd.DataFrame], key: str) -> str:
    L = [f"Benchmark `{key}`; excess = equal-weighted mean forward return of the bucket minus the "
         "benchmark's forward return over the same window (local currency). Reported alongside "
         "the pass bar, not a pass criterion. ALL = every eligible name.", "",
         "| Model | Bucket | 3m mean excess | 6m | 12m | 12m dates | 12m years > 0 |",
         "|---|---|---|---|---|---|---|"]
    for model, bd in tables.items():
        y = m.yearly_benchmark_excess(bd)
        for b in m.EXCESS_BUCKETS:
            cells = []
            for h in m.HORIZONS:
                v = bd[(bd["horizon"] == h) & (bd["bucket"] == b)]["excess"].dropna()
                cells.append(fmt(float(v.mean()) if len(v) else math.nan, True))
            v12 = bd[(bd["horizon"] == 12) & (bd["bucket"] == b)]["excess"].dropna()
            y12 = y[(y["horizon"] == 12) & (y["bucket"] == b)]["excess"]
            L.append(f"| {model} | {b} | {' | '.join(cells)} | {len(v12)} | "
                     f"{int((y12 > 0).sum())}/{len(y12)} |")
    L += ["", "Per-year 12m excess of the BUY bucket:", "",
          "| Year | " + " | ".join(tables) + " |", "|---" * (len(tables) + 1) + "|"]
    ys = {k: m.yearly_benchmark_excess(v) for k, v in tables.items()}
    years = sorted({int(r) for y in ys.values() for r in y["year"]})
    for yr in years:
        cells = []
        for k in tables:
            r = ys[k][(ys[k]["year"] == yr) & (ys[k]["horizon"] == 12) & (ys[k]["bucket"] == "BUY")]
            cells.append(fmt(float(r["excess"].iloc[0]), True) if len(r) else "n/a")
        L.append(f"| {yr} | " + " | ".join(cells) + " |")
    return "\n".join(L)
