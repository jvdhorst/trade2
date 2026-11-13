"""Write the ranked screen as CSV + Parquet."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CAVEAT = (
    "# book_value model: P/B is meaningful mainly for financials and asset-heavy firms; "
    "for asset-light firms treat it as a sanity metric only. Not investment advice.\n"
)


def write_report(df: pd.DataFrame, as_of, out_root: str | Path = "reports") -> tuple[Path, Path]:
    out = Path(out_root) / str(as_of)
    out.mkdir(parents=True, exist_ok=True)
    csv_path, pq_path = out / "screen_book_value.csv", out / "screen_book_value.parquet"
    with open(csv_path, "w") as fh:
        fh.write(CAVEAT)
        df.to_csv(fh, index=False, float_format="%.6g")
    df.to_parquet(pq_path, index=False)
    return csv_path, pq_path


def write_coverage(cov: pd.DataFrame, as_of, out_root: str | Path = "reports") -> Path:
    out = Path(out_root) / str(as_of)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "coverage_book_value.csv"
    cov.to_csv(path, index=False)
    return path


def coverage_summary(cov: pd.DataFrame) -> list[str]:
    n = len(cov)
    valued = int((cov["status"] == "valued").sum())
    fv = int(cov["has_fair_value"].sum())
    lines = [f"coverage: {valued}/{n} valued ({valued / n:.0%}), {fv} with a peer fair value"]
    reasons = cov.loc[cov["status"] == "skipped", "reason"].value_counts()
    lines += [f"  skipped x{c}: {r}" for r, c in reasons.items()]
    flags = cov["flags"].str.split(",").explode()
    flags = flags[flags.astype(bool)].value_counts()
    lines += [f"  flag {f}: {c}" for f, c in flags.items()]
    return lines


def removal_summary(removal: dict[str, dict]) -> list[str]:
    """Eligibility removal rate per market and rule (spec docs/models/eligibility.md)."""
    from ..models.eligibility import REVIEW_PCT

    lines = []
    for market, r in removal.items():
        rules = ", ".join(f"{k} {v}" for k, v in r["by_rule"].items())
        line = (f"eligibility {market}: {r['n_ineligible']}/{r['n']} not eligible "
                f"({r['pct_ineligible']:.1f}%); by rule: {rules}; "
                f"short history (Model C): {r['short_history']}")
        if r["pct_ineligible"] > REVIEW_PCT[market]:
            line += "  REVIEW THRESHOLDS"
        lines.append(line)
    return lines


SIGNALS_DISCLAIMER = (
    "Screening signals for human review, not investment advice. Models: residual_income/0.2.0, "
    "peer_regression/0.2.0, composite/0.2.0, shareholder_yield/0.1.0, ebit_yield_roc/0.1.0, "
    "altman_z/0.1.0, dividend_sustainability/0.1.0, piotroski/0.1.0, accruals/0.1.0. "
    "as_of {as_of}. No combined vote (Phase 10). Model B is diagnostic only. "
    "Model J is new and unvalidated (not backtested). "
    "Model G is new and unvalidated (not backtested). "
    "Model D is new and unvalidated (not backtested). "
    "Model E is new and unvalidated (not backtested). "
    "Model F is new and unvalidated (not backtested)."
)


def write_signals(df: pd.DataFrame, as_of, out_root: str | Path = "reports",
                  disclaimer: str | None = None,
                  header: list[str] | None = None,
                  fit_header: list[str] | None = None) -> tuple[Path, Path]:
    """Report v2 (spec docs/models/residual_income.md §Report v2): CSV with a comment header line,
    Parquet with the same text under the schema metadata key `disclaimer`."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    text = disclaimer or SIGNALS_DISCLAIMER.format(as_of=as_of)
    out = Path(out_root) / str(as_of)
    out.mkdir(parents=True, exist_ok=True)
    csv_path, pq_path = out / "screen_signals.csv", out / "screen_signals.parquet"
    with open(csv_path, "w") as fh:
        fh.write(f"# {text}\n")
        for line in header or []:  # e.g. the benchmark block (benchmarks.md, Outputs 2)
            fh.write(f"# {line}\n")
        for line in fit_header or []:  # per_company_fit.md §9: lines already carry "# "
            fh.write(f"{line}\n")
        df.to_csv(fh, index=False, float_format="%.6g")
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[b"disclaimer"] = text.encode()
    if header:
        meta[b"benchmarks"] = "\n".join(header).encode()
    if fit_header:
        meta[b"fit"] = "\n".join(fit_header).encode()
    pq.write_table(table.replace_schema_metadata(meta), pq_path)
    return csv_path, pq_path


def signal_summary(counts: dict) -> list[str]:
    lines = []
    for model, by_market in counts.items():
        for market, c in by_market.items():
            lines.append(f"model {model} {market}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    return lines


def write_benchmarks(table: pd.DataFrame, as_of, out_root: str | Path = "reports") -> Path:
    out = Path(out_root) / str(as_of)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "benchmarks.csv"
    with open(path, "w") as fh:
        fh.write("# Benchmark yields and returns (docs/models/benchmarks.md): context for the "
                 "screen, not signals, not investment advice.\n")
        table.to_csv(fh, index=False, float_format="%.6g")
    return path


# ---------- per-company fit (docs/models/per_company_fit.md §9) ----------

FIT_SCREEN_MODELS = ("A", "B", "C", "H", "J", "G", "D", "E", "F")  # = fit.FIT_MODELS
FIT_ROW_FLAG_MODELS = ("A", "C", "H", "J", "G", "D", "E", "F")  # B is diagnostic: never the flag
FIT_STATUSES = ("CONFIRMED", "NOT_CONFIRMED", "INSUFFICIENT", "NOT_TESTED")
FIT_DEFAULT_PATH = "reports/backtest/company_model_fit.csv"
_INSTRUCTION = ("BUY", "HOLD", "SELL")


def fit_screen_columns() -> list[str]:
    ks = [k.lower() for k in FIT_SCREEN_MODELS]
    return ([f"{k}_fit" for k in ks] + [f"{k}_highlight" for k in ks]
            + ["highlight", "highlight_models"])


def load_fit(path) -> pd.DataFrame | None:
    """The fit CSV written by `valuation backtest fit` (first line a `# ` comment), or None
    when it is missing or unreadable (every <m>_fit is then NOT_TESTED)."""
    p = Path(path) if path else None
    if p is None or not p.is_file():
        return None
    try:
        with open(p) as fh:
            first = fh.readline()
        df = pd.read_csv(p, skiprows=1 if first.startswith("#") else 0, dtype=str,
                         keep_default_na=False, na_values=[""])
    except Exception:  # noqa: BLE001 - unreadable -> NOT_TESTED
        return None
    need = {"ticker", "model", "model_version", "variant", "status"}
    return df if need <= set(df.columns) else None


def attach_fit(df: pd.DataFrame, fit: pd.DataFrame | None,
               live_versions: dict) -> tuple[pd.DataFrame, list[str]]:
    """Append <m>_fit, <m>_highlight, highlight, highlight_models after the existing columns.

    <m>_fit = the SELL_EXIT status of (ticker, M) in the latest fit, else NOT_TESTED (no fit,
    no row, non-US screen row, or a stale model_version). <m>_highlight = CONFIRMED and a
    current BUY/HOLD/SELL label (A: a_model_signal). Header lines are returned with "# "."""
    out = df.copy()
    header: list[str] = []
    status: dict[tuple[str, str], str] = {}
    if fit is None or len(fit) == 0:
        header.append("# fit: no company_model_fit results; all <m>_fit = NOT_TESTED")
    else:
        f = fit[fit["variant"] == "SELL_EXIT"]
        run_ids = sorted({str(x) for x in fit["run_id"].dropna()}) if "run_id" in fit else []
        ver = (str(fit["fit_version"].dropna().iloc[0])
               if "fit_version" in fit and fit["fit_version"].notna().any() else "?")
        ph = (str(fit["fit_params_hash"].dropna().iloc[0])[:16]
              if "fit_params_hash" in fit and fit["fit_params_hash"].notna().any() else "?")
        header.append(f"# fit: company_model_fit run {','.join(run_ids) or '?'} ({ver}, params "
                      f"{ph}); CONFIRMED = worked in both in-sample windows (2014-2019, "
                      "2020-2023), not a forecast; survivor-biased")
        stale: dict[str, str] = {}
        for r in f.itertuples(index=False):
            live = live_versions.get(r.model)
            if r.model not in FIT_SCREEN_MODELS:
                continue
            if r.model_version != live:
                stale.setdefault(r.model, str(r.model_version))
                continue
            status[(str(r.ticker), r.model)] = str(r.status)
        for k in FIT_SCREEN_MODELS:
            if k in stale:
                header.append(f"# fit: model {k} fitted on {stale[k]}, live "
                              f"{live_versions.get(k)} -> NOT_TESTED")
    us = (out["market"] == "US").tolist() if "market" in out else [False] * len(out)
    tickers = out["ticker"].astype(str).tolist() if len(out) else []
    hl_cols = {}
    for k in FIT_SCREEN_MODELS:
        lk = k.lower()
        fits = [status.get((t, k), "NOT_TESTED") if is_us else "NOT_TESTED"
                for t, is_us in zip(tickers, us, strict=True)]
        fits = [s if s in FIT_STATUSES else "NOT_TESTED" for s in fits]
        col = "a_model_signal" if k == "A" else f"{lk}_signal"
        labels = out[col].tolist() if col in out else [None] * len(out)
        out[f"{lk}_fit"] = pd.Series(fits, index=out.index, dtype=object)
        hl_cols[k] = [s == "CONFIRMED" and lab in _INSTRUCTION
                      for s, lab in zip(fits, labels, strict=True)]
    for k in FIT_SCREEN_MODELS:
        out[f"{k.lower()}_highlight"] = pd.Series(hl_cols[k], index=out.index, dtype=bool)
    flag_models = [[k for k in FIT_SCREEN_MODELS if k in FIT_ROW_FLAG_MODELS and hl_cols[k][i]]
                   for i in range(len(out))]
    out["highlight"] = pd.Series([bool(x) for x in flag_models], index=out.index, dtype=bool)
    out["highlight_models"] = pd.Series([",".join(x) for x in flag_models], index=out.index,
                                        dtype=object)
    return out, header


def fit_summary(df: pd.DataFrame) -> list[str]:
    if "highlight" not in df:
        return []
    lines = [f"fit: {int(df['highlight'].sum())} rows highlighted (a CONFIRMED model with a "
             "current BUY/HOLD/SELL; B excluded)"]
    for k in FIT_SCREEN_MODELS:
        n = int((df[f"{k.lower()}_fit"] == "CONFIRMED").sum())
        if n:
            lines.append(f"  model {k}: {n} CONFIRMED for the company")
    return lines
