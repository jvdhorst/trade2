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
