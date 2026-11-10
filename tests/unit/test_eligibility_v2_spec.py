"""Spec tests for eligibility v0.1.1 (docs/models/eligibility.md; revision round v2, P9-6).

`config/liquidity.yaml` has `min_history_sessions: 274` in both markets, so a name with
273 sessions fails E6 (model_c_history_ok False, SHORT_HISTORY, still eligible) and one with
274 passes. Written from the spec only.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from valuation.models.eligibility import Bar, EligibilityInputs, evaluate, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config" / "liquidity.yaml"
AS_OF = date(2026, 11, 10)


def test_repo_config_274_both_markets():
    cfg = load_config(CONFIG)
    assert cfg["tsx"]["min_history_sessions"] == 274
    assert cfg["us"]["min_history_sessions"] == 274


def weekdays_ending(as_of: date, n: int) -> list[date]:
    out, d = [], as_of
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def run(market: str, n: int):
    cfg = load_config(CONFIG)
    dates = weekdays_ending(AS_OF, n)
    bars = [Bar(d, 100.0, 100.0, 1_000_000) for d in dates]  # TV 1e8 > both min_mdtv
    inp = EligibilityInputs(ticker="TST", as_of=AS_OF, market=market, index_member=True,
                            bars=bars, market_cap=1e11, market_sessions=None)
    return evaluate(inp, cfg)


@pytest.mark.parametrize("market", ["TSX", "US"])
def test_273_sessions_fail_e6(market):
    r = run(market, 273)
    assert r.history_sessions == 273
    assert r.model_c_history_ok is False
    assert "SHORT_HISTORY" in r.flags
    assert r.eligible is True  # E6 never makes a name ineligible
    assert "E6" not in r.fail_rules


@pytest.mark.parametrize("market", ["TSX", "US"])
def test_274_sessions_pass_e6(market):
    r = run(market, 274)
    assert r.history_sessions == 274
    assert r.model_c_history_ok is True
    assert "SHORT_HISTORY" not in r.flags
    assert r.eligible is True
    assert r.fail_rules == ()
