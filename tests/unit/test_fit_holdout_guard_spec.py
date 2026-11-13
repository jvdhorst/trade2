"""Spec tests for the per-company fit holdout lock (docs/models/per_company_fit.md v0.1.0 §7, G5,
§10 fit preconditions, §13 no-look-ahead).

Written from the spec only:
    from valuation.backtest.fit import (assert_no_holdout, HoldoutLeak, pair_metrics,
        load_fit_frame, run_fit, FIT_LOCK_END)
    valuation.cli.main(["backtest", "fit", ...])   # §7.4, §10
Fixture DBs only (tests/unit/fit_db_fixture.py); never the real backtest DB or holdout data.
`valuation.backtest.fit` is imported inside each test so each fails on its own until it exists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FREEZE_DOC = REPO_ROOT / "docs" / "models" / "revision_v2_freeze.md"
LOCK = date(2023, 10, 31)
NAN = float("nan")
HINT = "valuation backtest run --market US"


def fitmod():
    import valuation.backtest.fit as F

    return F


def frame(rows):
    """rows: (date, ret, t1[, ticker])"""
    return pd.DataFrame([{"date": r[0], "ret": r[1], "t1": r[2],
                          "ticker": r[3] if len(r) > 3 else "AAA", "label": "HOLD"}
                         for r in rows], columns=["date", "ticker", "label", "ret", "t1"])


# ---------------------------------------------------------------------------
# G5 assert_no_holdout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", [
    (date(2023, 8, 31), .01, date(2023, 10, 2)),
    (date(2023, 9, 15), .01, date(2023, 10, 31)),  # boundary: t1 == lock passes
    (date(2023, 10, 31), NAN, None),               # signal date == lock, no return: passes
    (date(2014, 1, 31), .02, date(2014, 3, 3)),
])
def test_g5_passes(row):
    F = fitmod()
    assert F.assert_no_holdout(frame([row])) is None
    assert F.assert_no_holdout(frame([row]), lock=LOCK) is None


@pytest.mark.parametrize("row", [
    (date(2023, 9, 30), .01, date(2023, 11, 1)),   # exit after the lock
    (date(2023, 11, 30), NAN, None),               # signal date after the lock
    (date(2023, 8, 31), .01, None),                # defined return without an exit date
    (date(2023, 11, 30), .01, date(2024, 1, 2)),
    (date(2026, 10, 31), NAN, None),
])
def test_g5_raises(row):
    F = fitmod()
    with pytest.raises(F.HoldoutLeak):
        F.assert_no_holdout(frame([row]))
    with pytest.raises(ValueError):  # HoldoutLeak(ValueError)
        F.assert_no_holdout(frame([row]), lock=LOCK)


def test_g5_empty_passes():
    F = fitmod()
    assert F.assert_no_holdout(frame([])) is None


def test_g5_raises_when_one_row_of_many_leaks():
    F = fitmod()
    ok = [(date(2015, m, 28), .01, date(2015, m + 2, 2)) for m in range(1, 9)]
    with pytest.raises(F.HoldoutLeak):
        F.assert_no_holdout(frame(ok + [(date(2023, 9, 30), .01, date(2023, 11, 1))]))


def test_g5_message_names_first_offending_row():  # sorted by date, ticker
    F = fitmod()
    df = frame([(date(2024, 5, 31), NAN, None, "ZZZ"), (date(2023, 11, 30), NAN, None, "MMM"),
                (date(2023, 11, 30), NAN, None, "BBB"), (date(2015, 1, 31), .01,
                                                         date(2015, 3, 2), "AAA")])
    with pytest.raises(F.HoldoutLeak) as ei:
        F.assert_no_holdout(df)
    msg = str(ei.value)
    assert "BBB" in msg and "2023-11-30" in msg
    assert "ZZZ" not in msg


def test_assert_no_holdout_custom_columns_and_lock():
    F = fitmod()
    df = pd.DataFrame({"d": [date(2016, 5, 31)], "x": [None], "r": [NAN]})
    with pytest.raises(F.HoldoutLeak):
        F.assert_no_holdout(df, lock=date(2016, 4, 30), date_col="d", exit_col="x", ret_col="r")
    assert F.assert_no_holdout(df, lock=date(2016, 5, 31), date_col="d", exit_col="x",
                               ret_col="r") is None


def test_lock_constant_is_fixed():  # §7: does not move with --today
    F = fitmod()
    assert F.FIT_LOCK_END == date(2023, 10, 31)
    # no "today" input exists for the guard: the default lock is the constant
    with pytest.raises(F.HoldoutLeak):
        F.assert_no_holdout(frame([(date(2023, 11, 30), NAN, None)]))


# ---------------------------------------------------------------------------
# pair_metrics enforces the guard on its whole input (§5 step 1, §7.3)
# ---------------------------------------------------------------------------


def pm_obs(extra=()):
    base = [(date(2015, 1, 31), .02, date(2015, 3, 2)), (date(2015, 2, 28), -.06,
                                                          date(2015, 4, 1)),
            (date(2015, 3, 31), .03, date(2015, 5, 1))]
    df = frame(list(base) + list(extra))
    df["label"] = ["HOLD", "SELL", "BUY"] + ["SELL"] * len(extra)
    return df


def test_pair_metrics_raises_on_holdout_row_outside_window():
    F = fitmod()
    obs = pm_obs([(date(2023, 11, 30), .05, date(2024, 1, 2))])
    with pytest.raises(F.HoldoutLeak):
        F.pair_metrics(obs, "SELL_EXIT", date(2015, 1, 31), date(2015, 12, 31))


def test_pair_metrics_raises_on_late_exit_row_even_if_window_drops_it():
    F = fitmod()
    obs = pm_obs([(date(2023, 9, 30), .05, date(2023, 11, 1))])
    with pytest.raises(F.HoldoutLeak):
        F.pair_metrics(obs, "SELL_EXIT", date(2015, 1, 31), date(2015, 12, 31))


def test_pair_metrics_raises_on_return_without_exit():
    F = fitmod()
    obs = pm_obs([(date(2016, 3, 31), .05, None)])
    with pytest.raises(F.HoldoutLeak):
        F.pair_metrics(obs, "BUY_ONLY", date(2015, 1, 31), date(2015, 12, 31))


def test_pair_metrics_uses_lock_parameter():
    F = fitmod()
    obs = pm_obs([(date(2016, 5, 31), .01, date(2016, 7, 1))])
    with pytest.raises(F.HoldoutLeak):
        F.pair_metrics(obs, "SELL_EXIT", date(2015, 1, 31), date(2015, 12, 31),
                       lock=date(2016, 4, 30))
    pm = F.pair_metrics(obs, "SELL_EXIT", date(2015, 1, 31), date(2015, 12, 31))
    assert pm.n_months == 3


def test_pair_metrics_calls_assert_no_holdout(monkeypatch):
    F = fitmod()
    calls = []
    orig = F.assert_no_holdout

    def spy(df, *a, **k):
        calls.append(len(df))
        return orig(df, *a, **k)

    monkeypatch.setattr(F, "assert_no_holdout", spy)
    obs = pm_obs([(date(2019, 3, 31), .01, date(2019, 5, 1))])
    F.pair_metrics(obs, "SELL_EXIT", date(2015, 1, 31), date(2015, 12, 31))
    assert calls and calls[0] == 4  # the whole input, before the window filter


# ---------------------------------------------------------------------------
# Loader (§7.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def ho_db(tmp_path):
    from fit_db_fixture import build_fit_db

    return build_fit_db(tmp_path / "bt_ho.duckdb", with_holdout=True, perturb=True)


def test_loader_never_loads_holdout(ho_db):
    F = fitmod()
    # the fixture really holds holdout rows
    assert ho_db.execute("SELECT max(date) FROM bt_rows").fetchone()[0] == date(2026, 10, 31)
    df = F.load_fit_frame(ho_db)
    assert len(df) > 0
    assert max(df["date"]) <= LOCK
    assert df["t1"].notna().all() and max(df["t1"]) <= LOCK
    assert df["ret"].notna().all()
    assert "ZZZ" not in set(df["ticker"])  # holdout-only ticker
    assert max(df["date"]) == date(2023, 8, 31)  # 2023-09-30 exits 2023-11-01 > lock
    for c in ("date", "ticker", "a_signal", "b_signal", "c_signal", "h_signal", "j_signal",
              "g_signal", "d_signal", "e_signal", "f_signal", "ret", "t1", "flag"):
        assert c in df.columns, c
    assert all(type(d) is date for d in df["date"])  # §11: datetime.date
    assert all(type(d) is date for d in df["t1"])


def test_loader_reads_horizon_1_only(ho_db):
    F = fitmod()
    df = F.load_fit_frame(ho_db)
    exp = ho_db.execute("""SELECT r.date, r.ticker, x.ret FROM bt_rows r JOIN bt_returns x
        ON x.date = r.date AND x.ticker = r.ticker AND x.horizon = 1
        WHERE r.universe = 'US' AND r.date <= DATE '2023-10-31' AND x.t1 <= DATE '2023-10-31'
        AND x.ret IS NOT NULL""").df()
    assert len(df) == len(exp)
    got = df.set_index(["date", "ticker"])["ret"].sort_index()
    exp["date"] = [d.date() if hasattr(d, "date") else d for d in exp["date"]]
    ref = exp.set_index(["date", "ticker"])["ret"].sort_index()
    assert (abs(got.to_numpy() - ref.to_numpy()) < 1e-12).all()


def test_loader_calls_assert_no_holdout(ho_db, monkeypatch):
    F = fitmod()
    calls = []
    orig = F.assert_no_holdout

    def spy(df, *a, **k):
        calls.append(len(df))
        return orig(df, *a, **k)

    monkeypatch.setattr(F, "assert_no_holdout", spy)
    df = F.load_fit_frame(ho_db)
    assert calls and len(df) in calls


# ---------------------------------------------------------------------------
# Leak / perturbation test (§7 QA list, §13)
# ---------------------------------------------------------------------------


def fit_rows(con, run_id=None):
    if run_id is None:
        run_id = con.execute("SELECT run_id FROM fit_runs ORDER BY started_at DESC LIMIT 1"
                             ).fetchone()[0]
    df = con.execute("SELECT * FROM company_model_fit WHERE run_id=?", [run_id]).df()
    return df.drop(columns=["run_id"]).sort_values(["ticker", "model", "variant"]).reset_index(
        drop=True)


def test_leak_post_lock_rows_and_prices_change_nothing(tmp_path):
    from fit_db_fixture import TODAY, build_fit_db

    F = fitmod()
    clean = build_fit_db(tmp_path / "clean.duckdb")
    leaky = build_fit_db(tmp_path / "leaky.duckdb", with_holdout=True, perturb=True)
    # the perturbation really changed post-lock data
    q = "SELECT ret FROM bt_returns WHERE date = DATE '2023-09-30' AND ticker='AAA' AND horizon=1"
    assert clean.execute(q).fetchone()[0] != leaky.execute(q).fetchone()[0]
    F.run_fit(clean, TODAY, tmp_path / "out_clean")
    F.run_fit(leaky, TODAY, tmp_path / "out_leaky")
    a, b = fit_rows(clean), fit_rows(leaky)
    assert len(a) == 54
    pd.testing.assert_frame_equal(a, b)


def test_fit_output_independent_of_today(tmp_path):  # FIT_LOCK_END fixed, not --today
    from fit_db_fixture import build_fit_db

    F = fitmod()
    con = build_fit_db(tmp_path / "bt.duckdb", with_holdout=True)
    F.run_fit(con, date(2026, 11, 10), tmp_path / "o1")
    a = fit_rows(con)
    F.run_fit(con, date(2027, 6, 15), tmp_path / "o2")  # holdout split moves; lock does not
    b = fit_rows(con)
    pd.testing.assert_frame_equal(a, b)


def test_fit_never_writes_bt_runs_and_holdout_guard_still_passes(tmp_path):
    from fit_db_fixture import TODAY, build_fit_db

    from valuation.backtest.holdout import check_holdout_guard

    F = fitmod()
    con = build_fit_db(tmp_path / "bt.duckdb")
    before = con.execute("SELECT count(*) FROM bt_runs").fetchone()[0]
    F.run_fit(con, TODAY, tmp_path / "out")
    assert con.execute("SELECT count(*) FROM bt_runs").fetchone()[0] == before == 0
    result = tmp_path / "holdout_v2.md"
    assert check_holdout_guard(FREEZE_DOC, result, con)
    assert not result.exists()


# ---------------------------------------------------------------------------
# CLI refusals and preconditions (§7.4, §10)
# ---------------------------------------------------------------------------


def cli(args, monkeypatch):
    from valuation.cli import main

    monkeypatch.chdir(REPO_ROOT)
    return main(args)


def run_cli(args, monkeypatch, capsys):
    """Return (exit code, output). An argparse rejection (SystemExit) is a test failure: the
    refusal must come from the fit command, not from an unknown action or flag."""
    try:
        rc = cli(args, monkeypatch)
    except SystemExit as e:  # pragma: no cover - failure path
        out = capsys.readouterr()
        pytest.fail(f"argparse rejected {args}: exit {e.code}: {out.err[-300:]}")
    out = capsys.readouterr()
    return rc, out.out + out.err


@pytest.fixture
def good_db(tmp_path):
    from fit_db_fixture import build_fit_db

    path = tmp_path / "bt.duckdb"
    build_fit_db(path).close()
    return path


def assert_nothing_written(path: Path, out: Path):
    con = duckdb.connect(str(path))
    tabs = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables"
                                      ).fetchall()}
    if "company_model_fit" in tabs:
        assert con.execute("SELECT count(*) FROM company_model_fit").fetchone()[0] == 0
    if "fit_runs" in tabs:
        assert con.execute("SELECT count(*) FROM fit_runs").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM bt_runs").fetchone()[0] == 0
    con.close()
    assert not (out / "company_model_fit.csv").exists()


@pytest.mark.parametrize("extra", [
    ["--holdout"],
    ["--holdout", "--freeze", str(FREEZE_DOC)],
    ["--market", "TSX"],
    ["--today", "2026-10-31"],   # in-sample would end 2023-09-30 < lock
    ["--today", "2025-01-15"],
])
def test_cli_fit_refusals(good_db, tmp_path, monkeypatch, capsys, extra):
    out = tmp_path / "out"
    args = ["backtest", "fit", "--bt-db", str(good_db), "--out", str(out)]
    if "--today" not in extra:
        args += ["--today", "2026-11-10"]
    rc, text = run_cli(args + extra, monkeypatch, capsys)
    assert rc == 2
    assert "REFUSED" in text
    assert_nothing_written(good_db, out)
    assert not (REPO_ROOT / "docs" / "qa" / "holdout_v2.md").exists()


@pytest.mark.parametrize("today", ["2026-11-10", "2026-11-01"])
def test_cli_fit_runs_on_good_db(good_db, tmp_path, monkeypatch, capsys, today):
    out = tmp_path / "out"
    rc, _ = run_cli(["backtest", "fit", "--bt-db", str(good_db), "--today", today,
                     "--out", str(out)], monkeypatch, capsys)
    assert rc == 0
    con = duckdb.connect(str(good_db))
    assert con.execute("SELECT count(*) FROM company_model_fit").fetchone()[0] == 54
    assert con.execute("SELECT count(*) FROM fit_runs").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM bt_runs").fetchone()[0] == 0
    con.close()
    assert (out / "company_model_fit.csv").exists()


def test_cli_fit_default_market_is_us(good_db, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    rc, _ = run_cli(["backtest", "fit", "--bt-db", str(good_db), "--market", "US",
                     "--today", "2026-11-10", "--out", str(out)], monkeypatch, capsys)
    assert rc == 0


@pytest.mark.parametrize("kind", ["no_label_versions", "stale_label_version", "no_h1",
                                  "missing_sel_date", "missing_conf_date"])
def test_cli_fit_preconditions(tmp_path, monkeypatch, capsys, kind):
    from fit_db_fixture import build_fit_db, live_versions

    path = tmp_path / "bt.duckdb"
    kw = {}
    if kind == "no_label_versions":
        kw["label_versions"] = False
    elif kind == "stale_label_version":
        kw["label_versions"] = {**live_versions(), "G": "altman_z/0.0.9"}
    elif kind == "no_h1":
        kw["h1"] = False
    elif kind == "missing_sel_date":
        kw["drop_date"] = date(2016, 6, 30)
    else:
        kw["drop_date"] = date(2023, 8, 31)  # last usable confirmation date
    build_fit_db(path, **kw).close()
    out = tmp_path / "out"
    rc, text = run_cli(["backtest", "fit", "--bt-db", str(path), "--today", "2026-11-10",
                        "--out", str(out)], monkeypatch, capsys)
    assert rc == 2
    assert HINT in text
    assert_nothing_written(path, out)


def test_cli_embargo_date_missing_is_not_a_precondition(tmp_path, monkeypatch, capsys):
    # §10: bt_rows must hold the 70 + 44 usable dates; an unused embargo month is not required
    from fit_db_fixture import build_fit_db

    path = tmp_path / "bt.duckdb"
    build_fit_db(path, drop_date=date(2019, 11, 30)).close()
    rc, _ = run_cli(["backtest", "fit", "--bt-db", str(path), "--today", "2026-11-10",
                     "--out", str(tmp_path / "out")], monkeypatch, capsys)
    assert rc == 0
