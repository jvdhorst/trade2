"""Spec tests for the one-shot holdout guard (docs/models/revision_v2_freeze.md §3, §4).

Agreed interface (module `valuation.backtest.holdout`):
    class HoldoutRefused(Exception)
    stated_sha256(freeze_path) -> str            # the **SHA-256 = `...`** value in the doc
    check_holdout_guard(freeze_path, result_path, con=None) -> str   # hash or HoldoutRefused
Refusal causes: (a) params_sha256(FROZEN_V2) != stated hash; (b) the doc's JSON block does
not hash to its stated value; (c) result_path exists; (d) `bt_runs` already has a
kind='holdout' row whose model_version contains one of the four frozen model versions.
Only fixtures and tmp files are used: never real holdout data or a real backtest DB.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from valuation.backtest.holdout import HoldoutRefused, check_holdout_guard, stated_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
FREEZE_DOC = REPO_ROOT / "docs" / "models" / "revision_v2_freeze.md"
FROZEN_SHA = "6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e"
OTHER_SHA = "0" * 64
VERSIONS = ["residual_income/0.2.0", "peer_regression/0.2.0", "composite/0.2.0",
            "shareholder_yield/0.1.0"]

# bt_runs columns as in the backtest engine schema
BT_RUNS_DDL = """CREATE TABLE bt_runs (
    run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, kind VARCHAR, market VARCHAR,
    thresholds_hash VARCHAR, git_sha VARCHAR, today DATE, n_dates INTEGER,
    model_version VARCHAR)"""


def bt_con(rows=()):
    con = duckdb.connect(":memory:")
    con.execute(BT_RUNS_DDL)
    for i, (kind, mv) in enumerate(rows):
        con.execute("INSERT INTO bt_runs (run_id, kind, market, model_version) VALUES (?,?,?,?)",
                    [f"r{i}", kind, "US", mv])
    return con


def doc_copy(tmp_path, old=None, new=None) -> Path:
    text = FREEZE_DOC.read_text(encoding="utf-8")
    if old is not None:
        assert old in text
        text = text.replace(old, new)
    p = tmp_path / "freeze.md"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# stated_sha256 and the passing case
# ---------------------------------------------------------------------------


def test_stated_sha256_real_doc():
    assert stated_sha256(FREEZE_DOC) == FROZEN_SHA


def test_stated_sha256_altered_copy(tmp_path):
    p = doc_copy(tmp_path, FROZEN_SHA, OTHER_SHA)
    assert stated_sha256(p) == OTHER_SHA


def test_guard_passes(tmp_path):
    con = bt_con()
    assert check_holdout_guard(FREEZE_DOC, tmp_path / "holdout_v2.md", con) == FROZEN_SHA


def test_guard_passes_without_con(tmp_path):
    assert check_holdout_guard(FREEZE_DOC, tmp_path / "holdout_v2.md") == FROZEN_SHA


@pytest.mark.parametrize("rows", [
    [("in_sample", "residual_income/0.2.0;composite/0.2.0")],  # in-sample rows never block
    [("holdout", "residual_income/0.1.0;peer_regression/0.1.0")],  # older versions: not frozen
    [("holdout", "composite/0.1.0")],
])
def test_guard_passes_with_unrelated_rows(tmp_path, rows):
    assert check_holdout_guard(FREEZE_DOC, tmp_path / "res.md", bt_con(rows)) == FROZEN_SHA


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_refuses_when_stated_hash_differs(tmp_path):  # (a) and (b)
    p = doc_copy(tmp_path, FROZEN_SHA, OTHER_SHA)
    with pytest.raises(HoldoutRefused):
        check_holdout_guard(p, tmp_path / "res.md", bt_con())


def test_refuses_when_doc_json_does_not_hash_to_stated(tmp_path):  # (b)
    # Change a value inside the JSON block, keep the stated hash.
    p = doc_copy(tmp_path, '"ERP":0.05', '"ERP":0.06')
    with pytest.raises(HoldoutRefused):
        check_holdout_guard(p, tmp_path / "res.md", bt_con())


def test_refuses_when_doc_has_no_json_or_hash(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises((HoldoutRefused, ValueError)):
        check_holdout_guard(p, tmp_path / "res.md", bt_con())


def test_refuses_when_result_exists(tmp_path):  # (c)
    res = tmp_path / "holdout_v2.md"
    res.write_text("already run\n")
    with pytest.raises(HoldoutRefused):
        check_holdout_guard(FREEZE_DOC, res, bt_con())


@pytest.mark.parametrize("version", VERSIONS)
def test_refuses_when_holdout_row_exists(tmp_path, version):  # (d)
    con = bt_con([("in_sample", version), ("holdout", f"x;{version};y")])
    with pytest.raises(HoldoutRefused):
        check_holdout_guard(FREEZE_DOC, tmp_path / "res.md", con)


def test_refuses_exact_model_version_match(tmp_path):
    con = bt_con([("holdout", "shareholder_yield/0.1.0")])
    with pytest.raises(HoldoutRefused):
        check_holdout_guard(FREEZE_DOC, tmp_path / "res.md", con)


def test_holdout_refused_is_exception():
    assert issubclass(HoldoutRefused, Exception)


# ---------------------------------------------------------------------------
# CLI: refused before any computation
# ---------------------------------------------------------------------------


@pytest.fixture
def no_compute(monkeypatch):
    import valuation.backtest.engine as bte
    import valuation.backtest.runner as btr

    def boom(*a, **k):
        raise AssertionError("computation started although the holdout guard refused")

    monkeypatch.setattr(bte, "run_backtest", boom)
    monkeypatch.setattr(btr, "compute_returns", boom)


def _main(argv):
    from valuation.cli import main

    return main(argv)


def test_cli_run_refused_on_altered_hash(tmp_path, no_compute):
    doc = doc_copy(tmp_path, FROZEN_SHA, OTHER_SHA)
    res = tmp_path / "holdout_v2.md"
    rc = _main(["backtest", "run", "--holdout", "--freeze", str(doc), "--result", str(res),
                "--bt-db", str(tmp_path / "bt.duckdb")])
    assert rc != 0
    assert not res.exists()


def test_cli_run_refused_when_result_exists(tmp_path, no_compute):
    res = tmp_path / "holdout_v2.md"
    res.write_text("already run\n")
    rc = _main(["backtest", "run", "--holdout", "--freeze", str(FREEZE_DOC), "--result",
                str(res), "--bt-db", str(tmp_path / "bt.duckdb")])
    assert rc != 0
    assert res.read_text() == "already run\n"


def test_cli_run_refused_when_holdout_row_exists(tmp_path, no_compute):
    db = tmp_path / "bt.duckdb"
    con = duckdb.connect(str(db))
    con.execute(BT_RUNS_DDL)
    con.execute("INSERT INTO bt_runs (run_id, kind, market, model_version) VALUES (?,?,?,?)",
                ["r0", "holdout", "US", ";".join(VERSIONS)])
    con.close()
    res = tmp_path / "holdout_v2.md"
    rc = _main(["backtest", "run", "--holdout", "--freeze", str(FREEZE_DOC), "--result",
                str(res), "--bt-db", str(db)])
    assert rc != 0
    assert not res.exists()


def test_cli_report_holdout_without_freeze_refused(tmp_path, no_compute):
    rc = _main(["backtest", "report", "--holdout", "--bt-db", str(tmp_path / "bt.duckdb")])
    assert rc != 0
