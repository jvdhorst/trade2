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
