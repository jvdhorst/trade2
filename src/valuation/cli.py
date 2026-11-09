"""Command line: valuation init | ingest | value | crosscheck."""

from __future__ import annotations

import argparse
import logging
from datetime import date

from .store.db import DEFAULT_DB, connect


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="valuation")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the DuckDB schema")
    pi = sub.add_parser("ingest", help="fetch prices, fundamentals, FX")
    pi.add_argument("--universe", default="universe/reference.csv")
    pi.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    pv = sub.add_parser("value", help="run book_value model and write report")
    pv.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    pv.add_argument("--min-peers", type=int, default=5)
    pv.add_argument("--out", default="reports")
    pc = sub.add_parser("crosscheck", help="compare book_value vs Yahoo reference metrics")
    pc.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    pc.add_argument("--tolerance", type=float, default=0.05)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    con = connect(args.db)
    if args.cmd == "init":
        print(f"schema ready at {args.db}")
    elif args.cmd == "ingest":
        from .ingest.pipeline import ingest

        res = ingest(con, args.universe, args.as_of)
        print(f"ingested {res['tickers']} tickers, {len(res['errors'])} errors")
        for k, v in res["errors"].items():
            print(f"  {k}: {v}")
    elif args.cmd == "value":
        from .engine import run_book_value
        from .report.screen import write_report

        df, info = run_book_value(con, args.as_of, args.min_peers)
        csv_path, pq_path = write_report(df, args.as_of, args.out)
        print(f"run {info['run_id']}: {len(df)} valued -> {csv_path}, {pq_path}")
        for k, v in info["skipped"].items():
            print(f"  skipped {k}: {v}")
    elif args.cmd == "crosscheck":
        from .qa.crosscheck import crosscheck_book_value

        df = crosscheck_book_value(con, args.as_of, args.tolerance)
        print(df.to_string(index=False))
        return 0 if df["pass"].all() else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
