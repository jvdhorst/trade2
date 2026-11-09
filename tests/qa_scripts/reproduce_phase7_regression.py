"""QA P7-4: independent reproduction of the Model B sector regression.

This is the "LINEST-equivalent" spreadsheet check. Standalone script, not a pytest test, and it
does NOT import `valuation`. It reads the latest `signals` run from the DuckDB file (read-only),
rebuilds each sector's fit set from the stored payload per `docs/models/peer_regression.md`,
recomputes the winsor bounds, the Theil-Sen line (own brute-force pairwise implementation with a
pandas cross join), R^2, every residual and resid_pct, and compares them with the stored values.
It also compares slope/intercept with `scipy.stats.theilslopes(y, x, method="joint")`.

Usage:
    uv run python tests/qa_scripts/reproduce_phase7_regression.py data/valuation.duckdb \
        [Financials Energy ...]
"""

from __future__ import annotations

import json
import sys

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

TOL = 1e-6


def load_latest_signals(db_path: str) -> tuple[str, pd.DataFrame, dict]:
    con = duckdb.connect(db_path, read_only=True)
    run_id = con.execute(
        "SELECT run_id FROM runs WHERE model = 'signals' ORDER BY started_at DESC LIMIT 1"
    ).fetchall()[0][0]
    cfg = con.execute("SELECT config_json FROM runs WHERE run_id = ?", [run_id]).fetchall()
    rows = con.execute("SELECT payload_json FROM signals WHERE run_id = ?", [run_id]).fetchall()
    con.close()
    df = pd.DataFrame([json.loads(r[0]) for r in rows])
    return run_id, df, json.loads(cfg[0][0])["fits"]


def theil_sen_pairs(x: pd.Series, y: pd.Series) -> tuple[float, float, int]:
    """Median of pairwise slopes over i < j with x_i != x_j; intercept = median(y - slope*x)."""
    pts = pd.DataFrame({"i": range(len(x)), "x": x.to_numpy(), "y": y.to_numpy()})
    pairs = pts.merge(pts, how="cross", suffixes=("_i", "_j"))
    pairs = pairs[(pairs["i_i"] < pairs["i_j"]) & (pairs["x_i"] != pairs["x_j"])]
    slopes = (pairs["y_j"] - pairs["y_i"]) / (pairs["x_j"] - pairs["x_i"])
    slope = float(np.median(slopes))
    intercept = float(np.median(y.to_numpy() - slope * x.to_numpy()))
    return slope, intercept, len(pairs)


def reproduce_sector(df: pd.DataFrame, fits: dict, sector: str) -> dict:
    sec = df[df["sector"] == sector].copy()
    has_book = sec["bvps"].notna() & (sec["bvps"] > 0)
    not_csm = sec["a_no_signal_reason"] != "NEEDS_CSM_INPUT"
    has_roe = sec["roe_raw"].notna()
    eligible = sec["eligible"].map(lambda v: v is True)  # None and False are both excluded
    sec["in_S"] = eligible & not_csm & has_book & has_roe
    sec["in_F"] = sec["in_S"] & (sec["roe_raw"] >= 0)
    sec["pb_own"] = np.where(has_book, sec["price"] / sec["bvps"], np.nan)

    # Winsor bounds from the fit set, applied to every name with a roe.
    lo, hi = np.percentile(sec.loc[sec["in_F"], "roe_raw"].to_numpy(dtype=float), [5, 95])
    sec["x"] = sec["roe_raw"].clip(lower=lo, upper=hi)
    sec["y"] = np.log(sec["pb_own"])
    fit = sec[sec["in_F"]]

    slope, intercept, n_pairs = theil_sen_pairs(fit["x"], fit["y"])
    yhat = intercept + slope * fit["x"]
    r2 = 1 - ((fit["y"] - yhat) ** 2).sum() / ((fit["y"] - fit["y"].mean()) ** 2).sum()
    sp = stats.theilslopes(fit["y"].to_numpy(), fit["x"].to_numpy(), method="joint")

    # Residuals for every name with book > 0 and a roe (ineligible too); resid_pct only for S.
    has_res = has_book & has_roe & not_csm
    sec["resid_own"] = np.where(has_res, sec["y"] - (intercept + slope * sec["x"]), np.nan)
    s_res = sec.loc[sec["in_S"], "resid_own"].to_numpy()
    n_s = len(s_res)
    sec["pct_own"] = [
        np.sum(s_res < r) / (n_s - 1) if in_s else np.nan
        for r, in_s in zip(sec["resid_own"], sec["in_S"])
    ]

    stored = fits[sector]
    if stored["status"] != "OK":
        # A failed fit has no per-name outputs: stored residual and resid_pct must all be None.
        sec["resid_own"] = np.nan
        sec["pct_own"] = np.nan
    d_res = (sec["resid_own"] - sec["b_residual"]).abs()
    d_pct = (sec["pct_own"] - sec["b_resid_pct"]).abs()
    # A stored value must exist exactly where ours does.
    mismatch = int((sec["resid_own"].isna() != sec["b_residual"].isna()).sum())
    mismatch += int((sec["pct_own"].isna() != sec["b_resid_pct"].isna()).sum())
    return {
        "status": stored["status"],
        "n_fit": (int(sec["in_F"].sum()), stored["n_fit"]),
        "n_scored": (n_s, stored["n_scored"]),
        "n_pairs": n_pairs,
        "lo": (lo, stored["lo"]),
        "hi": (hi, stored["hi"]),
        "slope": (slope, stored["slope"]),
        "intercept": (intercept, stored["intercept"]),
        "r2": (r2, stored["r2"]),
        "scipy_slope": sp.slope,
        "scipy_intercept": sp.intercept,
        "n_residuals": int(sec["resid_own"].notna().sum()),
        "max_d_residual": float(d_res.max()),
        "max_d_resid_pct": float(d_pct.max()),
        "presence_mismatch": mismatch,
    }


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/valuation.duckdb"
    sectors = sys.argv[2:] or ["Financials", "Energy"]
    run_id, df, fits = load_latest_signals(db_path)
    print(f"signals run {run_id}, {len(df)} tickers")
    ok = True
    for sector in sectors:
        res = reproduce_sector(df, fits, sector)
        print(
            f"\n== {sector} (stored status {res['status']}): {res['n_pairs']} slope pairs, "
            f"{res['n_residuals']} residuals =="
        )
        for key in ("n_fit", "n_scored", "lo", "hi", "slope", "intercept", "r2"):
            ours, theirs = res[key]
            diff = abs(ours - theirs)
            ok &= diff <= TOL
            print(f"  {key:10s} ours {ours:.12g}  stored {theirs:.12g}  |diff| {diff:.2e}")
        d_s = abs(res["scipy_slope"] - res["slope"][0])
        d_i = abs(res["scipy_intercept"] - res["intercept"][0])
        ok &= d_s <= TOL and d_i <= TOL
        print(f"  scipy joint: slope {res['scipy_slope']:.12g} (|diff| {d_s:.2e}),")
        print(f"               intercept {res['scipy_intercept']:.12g} (|diff| {d_i:.2e})")
        print(
            f"  max |d residual| {res['max_d_residual']:.2e}, "
            f"max |d resid_pct| {res['max_d_resid_pct']:.2e}, "
            f"presence mismatches {res['presence_mismatch']}"
        )
        ok &= res["presence_mismatch"] == 0
        if res["n_residuals"]:
            ok &= res["max_d_residual"] <= TOL and res["max_d_resid_pct"] <= TOL
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
