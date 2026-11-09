"""Eligibility ("well-traded") filter. Spec: docs/models/eligibility.md (v0.1.0)."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import yaml

MODEL_VERSION = "eligibility/0.1.0"
DEFAULT_CONFIG = Path("config/liquidity.yaml")  # relative to cwd; falls back to the repo copy
_REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "liquidity.yaml"
MARKETS = {"TSX": "tsx", "US": "us"}
KEYS = ("min_mdtv", "min_market_cap", "min_price", "mdtv_window", "max_zero_volume_days",
        "max_stale_sessions", "min_history_sessions")
RULES = ("E1", "E2", "E3", "E4", "E5")
# A removal rate above 1.5x the upper expectation prompts a PM review (never automatic tuning).
REVIEW_PCT = {"US": 15.0, "TSX": 37.5}

SHORT_HISTORY = "SHORT_HISTORY"
INSUFFICIENT_VOLUME_HISTORY = "INSUFFICIENT_VOLUME_HISTORY"


class Bar(NamedTuple):
    date: date
    close: float | None
    adj_close: float | None
    volume: float | None


@dataclass
class EligibilityInputs:
    ticker: str
    as_of: date
    market: str
    index_member: bool
    bars: Sequence[Any]
    market_cap: float | None
    market_sessions: Iterable[date] | None = None


@dataclass(frozen=True)
class EligibilityResult:
    ticker: str
    as_of: date
    market: str
    eligible: bool
    fail_rules: tuple[str, ...]
    elig_fail_reason: str
    mdtv: float | None
    zero_volume_days: int
    stale_sessions: int | None
    history_sessions: int
    price: float | None
    market_cap: float | None
    model_c_history_ok: bool
    flags: frozenset[str]


def validate_config(cfg: Any) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("liquidity config must be a mapping")
    for section in MARKETS.values():
        sec = cfg.get(section)
        if not isinstance(sec, dict):
            raise ValueError(f"liquidity config: missing section '{section}'")
        missing = [k for k in KEYS if k not in sec]
        if missing:
            raise ValueError(f"liquidity config [{section}]: missing {', '.join(missing)}")
        # PyYAML reads e.g. "1e9" as a string: refuse it rather than compare float to str.
        bad = [k for k in KEYS if isinstance(sec[k], bool) or not isinstance(sec[k], int | float)]
        if bad:
            raise ValueError(f"liquidity config [{section}]: not a number: {', '.join(bad)}")
    return cfg


def load_config(path: str | Path | None = None) -> dict:
    if path is None:
        path = DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else _REPO_CONFIG
    with open(path) as fh:
        return validate_config(yaml.safe_load(fh))


def _business_days(after: date, through: date) -> int:
    n, d = 0, after + timedelta(days=1)
    while d <= through:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def evaluate(inp: EligibilityInputs, config: dict) -> EligibilityResult:
    if inp.market not in MARKETS:
        raise ValueError(f"{inp.ticker}: unknown market {inp.market!r}")
    th = config[MARKETS[inp.market]]
    dates = [b.date for b in inp.bars]
    if len(dates) != len(set(dates)):
        raise ValueError(f"{inp.ticker}: duplicate bar dates")
    # No look-ahead: bars after as_of are dropped first.
    bars = sorted((b for b in inp.bars if b.date <= inp.as_of), key=lambda b: b.date)

    window_n = int(th["mdtv_window"])
    window = bars[-window_n:] if window_n > 0 else []
    tv = [b.close * b.volume for b in window if b.close is not None and b.volume is not None]
    flags: set[str] = set()
    if len(tv) >= window_n and tv:
        mdtv = statistics.median(tv)
    else:
        mdtv = None
        flags.add(INSUFFICIENT_VOLUME_HISTORY)
    zero_days = sum(1 for b in window if b.volume is not None and b.volume == 0)

    last = next((b for b in reversed(bars) if b.close is not None), None)
    price = last.close if last else None
    if last is None:
        stale = None
    elif inp.market_sessions is None:
        stale = _business_days(last.date, inp.as_of)
    else:
        stale = sum(1 for d in set(inp.market_sessions) if last.date < d <= inp.as_of)
    history = sum(1 for b in bars if b.adj_close is not None)

    passed = {
        "E1": inp.index_member is True,
        "E2": mdtv is not None and mdtv >= th["min_mdtv"],
        "E3": inp.market_cap is not None and inp.market_cap >= th["min_market_cap"],
        "E4": price is not None and price >= th["min_price"],
        "E5": (stale is not None and zero_days <= th["max_zero_volume_days"]
               and stale <= th["max_stale_sessions"]),
    }
    e6 = history >= th["min_history_sessions"]
    if not e6:
        flags.add(SHORT_HISTORY)
    fail = tuple(r for r in RULES if not passed[r])
    return EligibilityResult(
        ticker=inp.ticker, as_of=inp.as_of, market=inp.market, eligible=not fail,
        fail_rules=fail, elig_fail_reason=";".join(fail), mdtv=mdtv, zero_volume_days=zero_days,
        stale_sessions=stale, history_sessions=history, price=price, market_cap=inp.market_cap,
        model_c_history_ok=e6, flags=frozenset(flags),
    )


def removal_report(results: Sequence[EligibilityResult]) -> dict:
    n = len(results)
    n_inel = sum(1 for r in results if not r.eligible)
    return {
        "n": n,
        "n_ineligible": n_inel,
        "pct_ineligible": 100.0 * n_inel / n if n else 0.0,
        "by_rule": {rule: sum(1 for r in results if rule in r.fail_rules) for rule in RULES},
        "short_history": sum(1 for r in results if not r.model_c_history_ok),
    }
