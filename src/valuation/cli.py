"""Command line: valuation init | ingest | value | crosscheck."""

from __future__ import annotations

import argparse
import logging
import time
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
    pi.add_argument("--prices-only", action="store_true", help="daily mode: batch prices + FX")
    pi.add_argument("--refresh-fundamentals", action="store_true",
                    help="refetch fundamentals for every ticker, ignoring the refresh policy")
    pi.add_argument("--workers", type=int, default=4)
    pi.add_argument("--no-history", action="store_true",
                    help="skip the per-ticker chart call (volume, adj_close, dividends)")
    pi.add_argument("--refresh-history", action="store_true",
                    help="refetch the full price history for every ticker")
    pv = sub.add_parser("value", help="run book_value + Models A/B and write reports")
    pv.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    pv.add_argument("--min-peers", type=int, default=5)
    pv.add_argument("--out", default="reports")
    pv.add_argument("--universe", default=None,
                    help="restrict the run to tickers in this universe CSV (default: all ingested)")
    pv.add_argument("--liquidity", default=None,
                    help="eligibility thresholds YAML (default: config/liquidity.yaml)")
    pv.add_argument("--csm", default="universe/insurer_csm.csv",
                    help="manual insurer CSM input (Model A/B, spec residual_income.md)")
    pv.add_argument("--no-signals", action="store_true",
                    help="skip Model A/B and screen_signals (book_value report only)")
    pu = sub.add_parser("universe", help="rebuild TSX Composite + S&P 500 lists")
    pu.add_argument("--out", default="universe")
    pc = sub.add_parser("crosscheck", help="compare book_value vs Yahoo reference metrics")
    pc.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    pc.add_argument("--tolerance", type=float, default=None)
    pc.add_argument("--issuer", action="store_true",
                    help="compare against issuer BVPS and ROE (universe/issuer_sources.csv)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    con = connect(args.db)
    if args.cmd == "init":
        print(f"schema ready at {args.db}")
    elif args.cmd == "ingest":
        from .ingest.pipeline import ingest

        t0 = time.monotonic()
        res = ingest(con, args.universe, args.as_of, prices_only=args.prices_only,
                     refresh_fundamentals=args.refresh_fundamentals, workers=args.workers,
                     history=not args.no_history, refresh_history=args.refresh_history)
        st = res["stats"]
        print(f"ingested {res['tickers']} tickers ({st['prices']} priced, "
              f"{st['history']} with volume history ({st['backfills']} full backfills), "
              f"{st['dividends']} dividend rows, {st['profiles']} profiles, "
              f"{st['yahoo_fundamentals']} Yahoo + {st['edgar_filings']} EDGAR fundamentals "
              f"refreshed, {st['rates']} rate rows), {len(res['errors'])} errors "
              f"in {time.monotonic() - t0:.0f}s")
        for k, v in res["errors"].items():
            print(f"  {k}: {v}")
    elif args.cmd == "value":
        from .engine import run_book_value
        from .ingest.pipeline import load_universe
        from .models.eligibility import load_config
        from .report.screen import (
            coverage_summary,
            removal_summary,
            write_coverage,
            write_report,
        )

        tickers = ({r["ticker"].strip() for r in load_universe(args.universe)}
                   if args.universe else None)
        df, info = run_book_value(con, args.as_of, args.min_peers, tickers,
                                  liquidity=load_config(args.liquidity))
        csv_path, pq_path = write_report(df, args.as_of, args.out)
        cov_path = write_coverage(info["coverage"], args.as_of, args.out)
        print(f"run {info['run_id']}: {len(df)} valued -> {csv_path}, {pq_path}, {cov_path}")
        for line in coverage_summary(info["coverage"]):
            print(line)
        for line in removal_summary(info["removal"]):
            print(line)
        for k, v in sorted(info["skipped"].items()):
            print(f"  skipped {k}: {v}")
        if not args.no_signals:
            from .engine import run_signals
            from .report.screen import signal_summary, write_signals

            sdf, sinfo = run_signals(con, args.as_of, tickers,
                                     liquidity=load_config(args.liquidity), csm_path=args.csm)
            s_csv, s_pq = write_signals(sdf, args.as_of, args.out)
            print(f"signals run {sinfo['run_id']}: {len(sdf)} rows -> {s_csv}, {s_pq}")
            for s, f in sorted(sinfo["fits"].items()):
                r2 = "n/a" if f.r2 is None else f"{f.r2:.3f}"
                print(f"  model B fit {s}: {f.status}, n_fit {f.n_fit}, R2 {r2}")
            for line in signal_summary(sinfo["counts"]):
                print(line)
    elif args.cmd == "universe":
        from .ingest.universe_build import build

        res = build(args.out)
        print(f"TSX Composite: {res['tsx']} rows, S&P 500: {res['sp500']} rows")
        bad = {k: v for k, v in res["problems"].items() if v}
        for k, v in bad.items():
            print(f"  {k}: {'; '.join(v)}")
        return 1 if bad else 0
    elif args.cmd == "crosscheck":
        if args.issuer:
            from .qa.issuer_check import issuer_check

            df = issuer_check(con, args.as_of, tolerance=args.tolerance or 0.03)
            print(df.to_string(index=False))
            return 0 if (df["status"] == "pass").all() else 1
        from .qa.crosscheck import crosscheck_book_value

        df = crosscheck_book_value(con, args.as_of, args.tolerance or 0.05)
        print(df.to_string(index=False))
        return 0 if df["pass"].all() else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
