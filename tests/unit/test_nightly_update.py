"""Nightly update scripts (docs/ops/nightly_update.md): offline tests.

The bash script runs against a throwaway repo layout in tmp_path with a stub `uv` that records
its arguments, so nothing is fetched. Behaviour tests need bash on Linux; the static checks run
everywhere.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "scripts" / "nightly_update.sh"
PS1 = ROOT / "scripts" / "nightly_update.ps1"

linux_bash = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("bash") is None,
    reason="bash script behaviour is tested on Linux",
)

STUB_UV = """#!/usr/bin/env bash
# Stub uv: record args; fail when the args match $STUB_FAIL_MATCH, at most $STUB_FAIL_TIMES times.
if [ "$1" = "--version" ]; then echo "uv 0.0-stub"; exit 0; fi
echo "$*" >> "$STUB_CALLS"
if [ -n "${STUB_FAIL_MATCH:-}" ] && [[ "$*" == *"$STUB_FAIL_MATCH"* ]]; then
  n=$(cat "$STUB_COUNT" 2>/dev/null || echo 0)
  if [ "$n" -lt "${STUB_FAIL_TIMES:-99}" ]; then
    echo $((n + 1)) > "$STUB_COUNT"
    echo "HTTP Error 429: Too Many Requests" >&2
    exit 3
  fi
fi
echo "stub ok: $*"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal repo layout: scripts/nightly_update.sh + universe files + a stub uv."""
    (tmp_path / "scripts").mkdir()
    shutil.copy2(SH, tmp_path / "scripts" / "nightly_update.sh")
    (tmp_path / "universe").mkdir()
    for u in ("tsx_composite.csv", "sp500.csv"):
        (tmp_path / "universe" / u).write_text("ticker\nX\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(STUB_UV)
    uv.chmod(0o755)
    return tmp_path


def run(repo: Path, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{repo / 'bin'}:/usr/bin:/bin",
        "HOME": str(repo / "home"),  # keeps the real ~/.local/bin/uv off the fallback PATH
        "STUB_CALLS": str(repo / "calls.txt"),
        "STUB_COUNT": str(repo / "count.txt"),
        "TRADE2_RETRY_DELAY": "0",
    }
    env.update(env_extra)
    return subprocess.run(["bash", str(repo / "scripts" / "nightly_update.sh"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def calls(repo: Path) -> list[str]:
    p = repo / "calls.txt"
    return p.read_text().splitlines() if p.exists() else []


def today_log(repo: Path) -> Path:
    return repo / "logs" / f"nightly_{date.today().isoformat()}.log"


# --- static checks (any OS) -------------------------------------------------------------------

def _code_lines(path: Path, comment: str) -> list[str]:
    text = re.sub(r"<#.*?#>", "", path.read_text(), flags=re.S)  # PowerShell block comments
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith(comment)]


@pytest.mark.parametrize("path,comment", [(SH, "#"), (PS1, "#")])
def test_scripts_never_run_backtest_or_hardcode_secrets(path: Path, comment: str):
    code = "\n".join(_code_lines(path, comment))
    assert "backtest" not in code.lower()
    assert "--holdout" not in code
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", code), "no hard-coded addresses"
    assert "VALUATION_SEC_USER_AGENT=" not in code and "$env:VALUATION_SEC_USER_AGENT =" not in code


def test_scripts_cover_both_universes_and_value():
    for path in (SH, PS1):
        text = path.read_text()
        assert "universe/tsx_composite.csv" in text and "universe/sp500.csv" in text
        assert "'value'" in text or '"value"' in text or "value --out reports" in text


# --- bash behaviour (Linux) -------------------------------------------------------------------

@linux_bash
def test_dry_run_prints_steps_without_side_effects(repo: Path):
    r = run(repo, "--dry-run")
    assert r.returncode == 0, r.stderr
    steps = [ln for ln in r.stdout.splitlines() if ln.startswith("step ")]
    assert steps == [
        "step 1: uv run valuation ingest --universe universe/tsx_composite.csv",
        "step 2: uv run valuation ingest --universe universe/sp500.csv",
        "step 3: uv run valuation value --out reports",
    ]
    assert f"reports/{date.today().isoformat()}/" in r.stdout
    assert "deep refresh: no" in r.stdout
    assert calls(repo) == []
    assert not (repo / "logs").exists()


@linux_bash
def test_deep_refresh_by_flag_and_by_weekday(repo: Path):
    r = run(repo, "--dry-run", "--deep")
    assert r.stdout.count("--refresh-history") == 2
    dow = str(date.today().isoweekday())
    other = str(date.today().isoweekday() % 7 + 1)
    on_day = run(repo, "--dry-run", TRADE2_DEEP_REFRESH_DAY=dow).stdout
    off_day = run(repo, "--dry-run", TRADE2_DEEP_REFRESH_DAY=other).stdout
    assert on_day.count("--refresh-history") == 2
    assert "--refresh-history" not in off_day


@linux_bash
def test_full_run_order_log_and_lock_release(repo: Path):
    r = run(repo, VALUATION_SEC_USER_AGENT="test-agent")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls(repo) == [
        "run valuation ingest --universe universe/tsx_composite.csv",
        "run valuation ingest --universe universe/sp500.csv",
        "run valuation value --out reports",
    ]
    log = today_log(repo).read_text()
    assert "nightly update OK" in log and "stub ok: run valuation value" in log
    assert "VALUATION_SEC_USER_AGENT not set" not in log
    assert not (repo / "logs" / ".nightly.lock").exists()


@linux_bash
def test_lock_held_by_live_process_skips(repo: Path):
    lock = repo / "logs" / ".nightly.lock"
    lock.mkdir(parents=True)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (lock / "pid").write_text(str(holder.pid))
        r = run(repo)
        assert r.returncode == 0
        assert "SKIP: another nightly update is running" in r.stdout
        assert calls(repo) == []
        assert lock.exists(), "the other run's lock must be left alone"
    finally:
        holder.kill()
        holder.wait()


@linux_bash
def test_stale_lock_is_taken_over(repo: Path):
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = repo / "logs" / ".nightly.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(dead.pid))
    r = run(repo)
    assert r.returncode == 0, r.stdout
    assert "removing stale lock" in r.stdout
    assert len(calls(repo)) == 3
    assert not lock.exists()


@linux_bash
def test_failed_step_is_retried_once(repo: Path):
    r = run(repo, STUB_FAIL_MATCH="sp500", STUB_FAIL_TIMES="1")
    assert r.returncode == 0, r.stdout
    assert calls(repo).count("run valuation ingest --universe universe/sp500.csv") == 2
    assert "retrying in 0s" in today_log(repo).read_text()


@linux_bash
def test_persistent_failure_exits_nonzero_skips_value_and_notifies(repo: Path):
    out = repo / "notified.txt"
    r = run(repo, STUB_FAIL_MATCH="tsx_composite",
            TRADE2_NOTIFY_CMD=f'{{ echo "$TRADE2_STATUS|$TRADE2_FAILED_STEPS"; cat; }} > {out}')
    assert r.returncode == 1
    c = calls(repo)
    assert c.count("run valuation ingest --universe universe/tsx_composite.csv") == 2
    assert "run valuation ingest --universe universe/sp500.csv" in c, "other market still ingested"
    assert not any("value" in x for x in c)
    note = out.read_text()
    assert note.startswith("1|ingest tsx_composite, value (skipped)")
    assert "429" in note, "log tail on stdin"


@linux_bash
def test_missing_uv_fails_clearly(repo: Path):
    (repo / "bin" / "uv").unlink()
    r = run(repo)
    assert r.returncode == 127
    assert "uv not found" in r.stdout
    assert run(repo, "--dry-run").returncode == 127


@linux_bash
def test_old_logs_are_pruned(repo: Path):
    logs = repo / "logs"
    logs.mkdir()
    old, recent = logs / "nightly_2000-01-01.log", logs / "nightly_2000-01-02.log"
    old.write_text("old")
    recent.write_text("recent")
    t = time.time()
    os.utime(old, (t - 40 * 86400, t - 40 * 86400))
    os.utime(recent, (t - 5 * 86400, t - 5 * 86400))
    assert run(repo).returncode == 0
    assert not old.exists() and recent.exists()
