"""`valuation backtest build|run|report` (spec docs/models/backtest.md)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..providers.base import RawCache
from . import engine as bte
from . import report as rep
from . import runner

DISCLAIMER = ("Screening-signal backtest for model validation only; not investment advice. "
              "Results are a survivor-biased upper bound where removed index members lack prices.")


def backtest_cmd(args) -> int:
    bt_path = Path(args.bt_db)
    if args.action == "build":
        cache = RawCache()
        changes_html = runner.fetch_wiki(cache, runner.WIKI_CHANGES, "sp500_changes")
        cur_html = runner.fetch_wiki(cache, runner.WIKI_CURRENT, "sp500_current")
        stats = runner.build(bt_path, changes_html, cur_html, "universe/sp500.csv",
                             "universe/tsx_composite.csv", args.today, workers=args.workers,
                             cache=cache)
        print(json.dumps(stats, indent=1, default=str))
        return 0

    if args.action == "benchmarks":  # benchmark series for the excess-return column (P8-10)
        from datetime import date

        from ..benchmarks import ingest_benchmarks
        from ..providers.yahoo import YahooProvider

        con = runner.connect_bt(bt_path)
        errs = ingest_benchmarks(con, YahooProvider(RawCache()), date(2012, 6, 1), args.today)
        n = con.execute("SELECT key, count(*), min(date), max(date) FROM benchmarks_daily "
                        "GROUP BY 1 ORDER BY 1").fetchall()
        print("\n".join(f"{k}: {c} bars {a} .. {b}" for k, c, a, b in n), errs or "")
        return 0

    con = runner.connect_bt(bt_path)
    dates = bte.rebalance_dates(bte.BT_START, args.today)
    if args.action == "run":
        t0 = time.time()
        members = runner.members_by_date(con, args.universe, dates)
        if args.universe == "TSX":  # TSX facts exist only for the last few years
            first = con.execute("SELECT min(filed_date) FROM fundamentals f "
                                "JOIN bt_ticker_coverage c "
                                "USING (ticker) WHERE c.market='TSX'").fetchone()[0]
            members = {d: t for d, t in members.items() if first is not None and d >= first}
            # Model B pools TSX and US names, as the live run does.
            us = runner.members_by_date(con, "US", list(members), record=False)
            members = {d: sorted(set(t) | set(us[d])) for d, t in members.items()}
        n_ret = runner.compute_returns(con, dates)
        con.close()
        res = bte.run_backtest(str(bt_path), args.universe, members, workers=args.workers,
                               csm_path="universe/insurer_csm.csv"
                               if args.universe == "TSX" else None)
        print(f"[{args.universe}] ran {res['ran']} dates in {res['seconds']:.0f}s; "
              f"{n_ret} forward-return rows; total {time.time() - t0:.0f}s")
        return 0

    # report
    ins = bte.in_sample_dates(dates)
    ho = bte.holdout_dates(dates) if args.holdout else None
    if args.universe == "TSX":  # TSX: short recent history; reported pooled, never in the bar
        ins, ho = sorted(set(dates)), None
    kind = "holdout" if ho else "in_sample"
    run_id = bte.log_run(con, kind, args.universe, args.today, len(dates))
    rows = rep.load_rows(con, args.universe)
    fwd = rep.load_returns(con)
    have = sorted(set(rows["date"]))
    ins = [d for d in ins if d in set(have)]
    ho = [d for d in ho if d in set(have)] if ho else None
    label = ("survivor-biased upper bound" if args.universe == "US"
             else "current constituents, survivor-biased")
    results = {}
    for model, name in rep.MODELS.items():
        res = rep.model_metrics(rows, fwd, model, ins, ho)
        results[model] = res
        h12 = res["in"]["h"][12]
        print(f"{name}: {res['pass'].result} failed={res['pass'].failed_legs} "
              f"incomplete={res['pass'].incomplete_legs} IC12 {h12['ic_mean']:.4f} "
              f"t {h12['ic_t']:.2f} spread12 {h12['mean_spread']:.4f}")
    diag = rep.diagnostics(results["A"]["obs"], results["B"]["obs"], rows, ins)
    cov = rep.coverage(con, args.universe)
    sv = rep.survivorship(con, args.universe, cov)
    rep.persist(con, run_id, results, diag, cov)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    parts = [f"# Backtest results ({args.universe}, {label})", "", DISCLAIMER, "",
             f"Run `{run_id}` ({kind}), thresholds hash `{bte.thresholds_hash()[:16]}`. "
             f"In-sample dates: {ins[0] if ins else '-'} .. {ins[-1] if ins else '-'} "
             f"({len(ins)}); holdout: "
             + (f"{ho[0]} .. {ho[-1]} ({len(ho)})" if ho else "locked (not computed)"), ""]
    for model, name in rep.MODELS.items():
        parts += [rep.render_model(results[model], name), ""]
    parts += ["## Phase 7 diagnostics (in-sample)", "", rep.render_diag(diag), "",
              "## Survivorship", "", "```", json.dumps(sv, indent=1, default=str), "```"]
    path = out / f"backtest_{args.universe}_{run_id}.md"
    path.write_text("\n".join(parts) + "\n")
    print(json.dumps(sv, default=str))
    print(f"-> {path}")
    return 0
