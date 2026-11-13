"""`valuation backtest build|run|report|benchmarks|fit` (specs docs/models/backtest.md,
docs/models/per_company_fit.md)."""

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


def _params_hash() -> str:
    from ..models.frozen import FROZEN_V2, params_sha256

    return params_sha256(FROZEN_V2)


def _write_holdout_result(path: Path, run_id: str, params_hash: str, results: dict,
                          report_path: Path) -> None:
    """docs/qa/holdout_v2.md (freeze §4.4); its existence blocks any further holdout run."""
    from ..engine import _git_sha
    from . import report as rep

    L = ["# Holdout v2 result (one-shot run)", "", DISCLAIMER, "",
         f"Run `{run_id}`, git `{_git_sha()}`, params SHA-256 `{params_hash}`, thresholds hash "
         f"`{bte.thresholds_hash()}`. Full report: `{report_path}`.", "",
         "| Model | Verdict | Failed legs | Incomplete legs |", "|---|---|---|---|"]
    for model in ("A", "C", "H", "B"):
        p = results[model]["pass"]
        verdict = (f"{p.result} (diagnostic, not counted)" if model in rep.DIAGNOSTIC_MODELS
                   else p.result)
        L.append(f"| {model} | {verdict} | {', '.join(p.failed_legs) or '-'} | "
                 f"{', '.join(p.incomplete_legs) or '-'} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n")
    print(f"-> {path}")


def fit_cmd(args) -> int:
    """`valuation backtest fit` (per_company_fit.md §7.4, §10): per-company evaluation from the
    stored rows. Never computes labels, never writes bt_runs, never reads holdout dates."""
    from . import fit as F

    if getattr(args, "holdout", False):
        print("REFUSED: `backtest fit` never runs on the holdout (per_company_fit.md §7); "
              "drop --holdout")
        return 2
    if args.universe != F.FIT_MARKET:
        print(f"REFUSED: `backtest fit` is {F.FIT_MARKET} only (TSX history too short)")
        return 2
    ins = bte.in_sample_dates(bte.rebalance_dates(bte.BT_START, args.today))
    if not ins or ins[-1] < F.FIT_LOCK_END:
        print(f"REFUSED: with --today {args.today} the in-sample period ends "
              f"{ins[-1] if ins else '-'}, before {F.FIT_LOCK_END}: {F.FIT_LOCK_END} would be a "
              "holdout date (use --today on or after 2026-11-01)")
        return 2
    bt_path = Path(args.bt_db)
    if not bt_path.exists():
        print(f"REFUSED: backtest DB {bt_path} not found; {F.HINT}")
        return 2
    con = runner.connect_bt(bt_path)
    try:
        msg = F.check_preconditions(con)
        if msg:
            print(f"REFUSED: {msg}")
            return 2
        res = F.run_fit(con, args.today, Path(args.out))
    finally:
        con.close()
    print(f"fit run {res['run_id']} ({F.FIT_MODEL_VERSION}, params {res['fit_params_hash'][:16]})"
          f": {res['n_tickers']} tickers; BH family SELL_EXIT {res['n_family_sell_exit']}, "
          f"BUY_ONLY {res['n_family_buy_only']}; CONFIRMED SELL_EXIT "
          f"{res['n_confirmed_sell_exit']}, BUY_ONLY {res['n_confirmed_buy_only']}")
    low = sorted(k for k, v in res["coverage"]["SEL"].items()
                 if not (v >= 0.5) or not (res["coverage"]["CONF"][k] >= 0.5))
    if low:
        print("LOW_LABEL_COVERAGE (<50% of eligible member-dates labelled): " + ", ".join(low))
    print(f"-> {res['csv']}\n-> {res['summary']}")
    return 0


def backtest_cmd(args) -> int:
    if args.action == "fit":
        return fit_cmd(args)
    if getattr(args, "extra_models", False) and getattr(args, "holdout", False):
        print("REFUSED: --extra-models with --holdout: the unvalidated models J, G, D, E, F are "
              "never computed on holdout dates (per_company_fit.md §2.2)")
        return 2
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

    holdout = bool(getattr(args, "holdout", False))
    params_hash = None
    if holdout:  # one-shot guard BEFORE any computation (revision_v2_freeze.md §3-§4)
        from .holdout import HoldoutRefused, check_holdout_guard

        if args.universe != "US":
            print("REFUSED: the holdout run is US only (revision_v2_freeze.md §4)")
            return 2
        if not getattr(args, "freeze", None):
            print("REFUSED: --holdout needs --freeze <freeze doc> (one-shot guarded run)")
            return 2
        con = runner.connect_bt(bt_path)
        try:
            params_hash = check_holdout_guard(args.freeze, args.result, con)
        except HoldoutRefused as e:
            con.close()
            print(f"REFUSED: {e}")
            return 2
        if args.action == "report":
            print("REFUSED: use `backtest run --holdout --freeze ...`: the holdout runs and "
                  "reports in one shot")
            con.close()
            return 2
    else:
        con = runner.connect_bt(bt_path)
    all_dates = bte.rebalance_dates(bte.BT_START, args.today)
    # Without --holdout the US run never touches the last HOLDOUT_N month-ends: no signals,
    # no forward returns, no metrics for them.
    dates = (all_dates if holdout or args.universe == "TSX"
             else bte.in_sample_dates(all_dates))
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
        if not holdout:
            return 0
        con = runner.connect_bt(bt_path)

    # report
    ins = bte.in_sample_dates(all_dates)
    ho = bte.holdout_dates(all_dates) if holdout else None
    if args.universe == "TSX":  # TSX: short recent history; reported pooled, never in the bar
        ins, ho = sorted(set(dates)), None
    kind = "holdout" if ho else "in_sample"
    run_id = bte.log_run(con, kind, args.universe, args.today, len(dates))
    rows = rep.load_rows(con, args.universe)
    fwd = rep.load_returns(con)
    if not ho and args.universe == "US":  # in-sample report: holdout dates never loaded
        keep = set(ins)
        rows = rows[rows["date"].isin(keep)].reset_index(drop=True)
        fwd = fwd[fwd["date"].isin(keep)].reset_index(drop=True)
    if args.universe == "US" and ins:  # backtest.md §2: in-sample exits never pass in_end
        fwd = rep.lock_in_sample(fwd, ins[-1])
    have = sorted(set(rows["date"]))
    ins = [d for d in ins if d in set(have)]
    ho = [d for d in ho if d in set(have)] if ho else None
    label = ("survivor-biased upper bound" if args.universe == "US"
             else "current constituents, survivor-biased")
    results = {}
    models = dict(rep.MODELS)
    if getattr(args, "extra_models", False):
        models.update(rep.EXTRA_MODELS)
    for model, name in models.items():
        res = rep.model_metrics(rows, fwd, model, ins, ho)
        results[model] = res
        h12 = res["in"]["h"][12]
        tag = (" [unvalidated, diagnostic, never a verdict]" if model in rep.EXTRA_MODELS
               else " [diagnostic, not a verdict]" if model in rep.DIAGNOSTIC_MODELS else "")
        print(f"{name}{tag}: {res['pass'].result} failed={res['pass'].failed_legs} "
              f"incomplete={res['pass'].incomplete_legs} IC12 {h12['ic_mean']:.4f} "
              f"t {h12['ic_t']:.2f} spread12 {h12['mean_spread']:.4f}")
    diag = rep.diagnostics(results["A"]["obs"], results["B"]["obs"], rows, ins)
    cov = rep.coverage(con, args.universe)
    sv = rep.survivorship(con, args.universe, cov)
    rep.persist(con, run_id, results, diag, cov)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    parts = [f"# Backtest results ({args.universe}, {label})", "", DISCLAIMER, "",
             f"Run `{run_id}` ({kind}), thresholds hash `{bte.thresholds_hash()[:16]}`, "
             f"params hash (freeze §2) `{_params_hash()[:16]}`. "
             f"In-sample dates: {ins[0] if ins else '-'} .. {ins[-1] if ins else '-'} "
             f"({len(ins)}); holdout: "
             + (f"{ho[0]} .. {ho[-1]} ({len(ho)})" if ho else "locked (not computed)"), ""]
    if getattr(args, "extra_models", False):
        parts += ["Models J, G, D, E, F (--extra-models) are unvalidated (not frozen, not in "
                  "FROZEN_V2), in-sample diagnostics only, never a verdict; their whole-market "
                  "hypotheses are not pre-registered.", ""]
    for model, name in models.items():
        parts += [rep.render_model(results[model], name), ""]
    from ..benchmarks import primary_return_benchmark

    bkey = primary_return_benchmark(args.universe)
    bench = rep.benchmark_fwd(con, bkey, dates)
    if args.universe == "US" and ins and not bench.empty:
        bench = rep.lock_in_sample(bench.assign(flag=None), ins[-1])
    bex = rep.benchmark_excess_tables(results, bench, ins)
    parts += ["## Excess return vs benchmark (in-sample)", "",
              rep.render_benchmark_excess(bex, bkey), ""]
    if ho:
        parts += ["## Excess return vs benchmark (holdout)", "",
                  rep.render_benchmark_excess(rep.benchmark_excess_tables(results, bench, ho),
                                              bkey), ""]
    parts += ["## Phase 7 diagnostics (in-sample)", "", rep.render_diag(diag), "",
              "## Survivorship", "", "```", json.dumps(sv, indent=1, default=str), "```"]
    path = out / f"backtest_{args.universe}_{run_id}.md"
    path.write_text("\n".join(parts) + "\n")
    if ho:
        _write_holdout_result(Path(args.result), run_id, params_hash, results, path)
    print(json.dumps(sv, default=str))
    print(f"-> {path}")
    return 0
