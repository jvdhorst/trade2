"""Spec tests for the eligibility filter (docs/models/eligibility.md, v0.1.0).

Written from the spec only (not from the implementation). Golden examples use a
config built in this file from the spec's defaults plus the per-example
overrides, so they do not depend on the repo's config/liquidity.yaml.
"""

from __future__ import annotations

import copy
import random
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from valuation.models.eligibility import (
    MODEL_VERSION,
    Bar,
    EligibilityInputs,
    EligibilityResult,
    evaluate,
    load_config,
    removal_report,
)

REL = 1e-9
REPO_ROOT = Path(__file__).resolve().parents[2]

KEYS = (
    "min_mdtv",
    "min_market_cap",
    "min_price",
    "mdtv_window",
    "max_zero_volume_days",
    "max_stale_sessions",
    "min_history_sessions",
)

DEFAULTS = {
    "tsx": {
        "min_mdtv": 5_000_000,
        "min_market_cap": 1_000_000_000,
        "min_price": 2,
        "mdtv_window": 63,
        "max_zero_volume_days": 3,
        "max_stale_sessions": 5,
        "min_history_sessions": 273,
    },
    "us": {
        "min_mdtv": 25_000_000,
        "min_market_cap": 5_000_000_000,
        "min_price": 5,
        "mdtv_window": 63,
        "max_zero_volume_days": 3,
        "max_stale_sessions": 5,
        "min_history_sessions": 273,
    },
}

AS_OF = date(2026, 3, 13)  # Friday
BASE_DATES = [date(2026, 3, d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]
FEB_MAR_DATES = [date(2026, 2, d) for d in (23, 24, 25, 26, 27)] + [
    date(2026, 3, d) for d in (2, 3, 4, 5, 6)
]


def approx(x):
    return pytest.approx(x, rel=REL)


def d(day: int, month: int = 3) -> date:
    return date(2026, month, day)


def make_config(**section_overrides) -> dict:
    """Example config: mdtv_window 5 and min_history_sessions 6 in both sections.

    section_overrides: e.g. us={"mdtv_window": 4}.
    """
    cfg = copy.deepcopy(DEFAULTS)
    for sec in ("tsx", "us"):
        cfg[sec]["mdtv_window"] = 5
        cfg[sec]["min_history_sessions"] = 6
    for sec, over in section_overrides.items():
        cfg[sec].update(over)
    return cfg


def bars_from(dates, closes, volumes, adj=None) -> list[Bar]:
    """Bars with adj_close = close unless adj is given."""
    n = len(dates)
    closes = closes if isinstance(closes, list) else [closes] * n
    volumes = volumes if isinstance(volumes, list) else [volumes] * n
    adj = closes if adj is None else adj
    return [Bar(dt, c, a, v) for dt, c, a, v in zip(dates, closes, adj, volumes)]


def run(market, bars, market_cap, *, index_member=True, as_of=AS_OF, sessions=None,
        config=None, ticker="TST") -> EligibilityResult:
    inputs = EligibilityInputs(
        ticker=ticker,
        as_of=as_of,
        market=market,
        index_member=index_member,
        bars=bars,
        market_cap=market_cap,
        market_sessions=sessions,
    )
    return evaluate(inputs, config if config is not None else make_config())


# ---------------------------------------------------------------------------
# Example inputs
# ---------------------------------------------------------------------------

EX1_VOLUMES = [300_000] * 5 + [300_000, 250_000, 400_000, 260_000, 350_000]


def ex1_bars(**kw) -> list[Bar]:
    return bars_from(BASE_DATES, 100, list(EX1_VOLUMES), **kw)


def ex1(**kw) -> EligibilityResult:
    kw.setdefault("bars", ex1_bars())
    bars = kw.pop("bars")
    return run("US", bars, 6e9, **kw)


def ex2_bars() -> list[Bar]:
    closes = [10] * 5 + [10, 11, 12, 13, 14]
    vols = [400_000] * 5 + [400_000, 500_000, 300_000, 450_000, 340_000]
    return bars_from(BASE_DATES, closes, vols)


def ex2() -> EligibilityResult:
    return run("TSX", ex2_bars(), 2e9)


def ex5() -> EligibilityResult:
    vols = [1_000_000] * 5 + [0, 0, 0, 0, 1_000_000]
    return run("TSX", bars_from(BASE_DATES, 20, vols), 2e9)


def ex7() -> EligibilityResult:
    return run("TSX", bars_from(BASE_DATES, 1.50, 4_000_000), None)


def ex9() -> EligibilityResult:
    return ex1(bars=ex1_bars()[5:])


def assert_ex1(r: EligibilityResult):
    assert r.eligible is True
    assert r.fail_rules == ()
    assert r.elig_fail_reason == ""
    assert r.mdtv == approx(100 * 300000)
    assert r.price == approx(100)
    assert r.zero_volume_days == 0
    assert r.stale_sessions == 0
    assert r.history_sessions == 10
    assert r.model_c_history_ok is True
    assert r.flags == frozenset()


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------


def test_model_version():
    assert MODEL_VERSION == "eligibility/0.1.0"


def test_bar_is_namedtuple_with_fields():
    b = Bar(d(2), 1.0, 1.0, 10.0)
    assert isinstance(b, tuple)
    assert b._fields == ("date", "close", "adj_close", "volume")


def test_result_echoes_inputs_and_is_frozen():
    r = ex1(ticker="BRK-B")
    assert r.ticker == "BRK-B"
    assert r.as_of == AS_OF
    assert r.market == "US"
    assert r.market_cap == approx(6e9)
    assert isinstance(r.flags, frozenset)
    assert isinstance(r.fail_rules, tuple)
    with pytest.raises(Exception):  # noqa: B017  (frozen dataclass: FrozenInstanceError)
        r.eligible = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Golden examples
# ---------------------------------------------------------------------------


def test_ex1_us_pass():
    assert_ex1(ex1())


def test_ex2_tsx_e2_only():
    r = ex2()
    assert r.mdtv == approx(14 * 340000)
    assert r.price == approx(14)
    assert r.eligible is False
    assert r.fail_rules == ("E2",)
    assert r.elig_fail_reason == "E2"
    assert r.flags == frozenset()


def test_ex3_even_count_median():
    cfg = make_config(us={"mdtv_window": 4})
    vols = [600_000] * 6 + [600_000, 400_000, 500_000, 700_000]
    r = run("US", bars_from(BASE_DATES, 50, vols), 6e9, config=cfg)
    assert r.mdtv == approx((50 * 500000 + 50 * 600000) / 2)
    assert r.eligible is True
    assert r.fail_rules == ()


def test_ex4_boundary_equality():
    cfg = make_config(us={"mdtv_window": 7})
    vols = [5_000_000 if dt not in (d(5), d(6), d(9)) else 0 for dt in BASE_DATES]
    r = run("US", bars_from(BASE_DATES, 5, vols), 5_000_000_000, config=cfg)
    assert r.mdtv == approx(5 * 5000000)
    assert r.price == approx(5)
    assert r.zero_volume_days == 3
    assert r.stale_sessions == 0
    assert r.eligible is True
    assert r.fail_rules == ()


def test_ex4b_staleness_boundary():
    r = run("US", bars_from(FEB_MAR_DATES, 100, 300_000), 6e9)
    assert r.stale_sessions == 5
    assert r.eligible is True
    assert r.fail_rules == ()


def test_ex5_zero_volume_days_e2_and_e5():
    r = ex5()
    assert r.mdtv == approx(0)
    assert r.zero_volume_days == 4
    assert r.fail_rules == ("E2", "E5")
    assert r.elig_fail_reason == "E2;E5"
    assert r.eligible is False


def ex6_bars() -> list[Bar]:
    return bars_from(FEB_MAR_DATES[:-1], 100, 300_000)  # Feb 23-27, Mar 2-5


def test_ex6_stale_quote_e5():
    r = run("US", ex6_bars(), 6e9)
    assert r.stale_sessions == 6
    assert r.fail_rules == ("E5",)
    assert r.elig_fail_reason == "E5"
    assert r.eligible is False
    assert r.mdtv == approx(100 * 300000)
    assert r.history_sessions == 9


def test_ex6_with_market_sessions():
    sessions = [d(5), d(6), d(10), d(11), d(12), d(13)]  # Mar 9 closed
    r = run("US", ex6_bars(), 6e9, sessions=sessions)
    assert r.stale_sessions == 5
    assert r.eligible is True
    assert r.fail_rules == ()


def test_ex6_sessions_after_as_of_not_counted():
    sessions = [d(5), d(6), d(10), d(11), d(12), d(13), d(16), d(17)]
    r = run("US", ex6_bars(), 6e9, sessions=sessions)
    assert r.stale_sessions == 5
    assert r.eligible is True


def test_ex7_price_and_missing_cap():
    r = ex7()
    assert r.mdtv == approx(1.5 * 4000000)
    assert r.price == approx(1.5)
    assert r.market_cap is None
    assert r.fail_rules == ("E3", "E4")
    assert r.elig_fail_reason == "E3;E4"
    assert r.eligible is False


def test_ex8_future_bar_ignored():
    bars = ex1_bars() + [Bar(d(16), 1, 1, 0)]
    r = ex1(bars=bars)
    assert_ex1(r)
    assert r == ex1()


def test_ex8_as_of_mar_12():
    r = ex1(as_of=d(12))
    assert r.mdtv == approx(30_000_000)
    assert r.stale_sessions == 0
    assert r.history_sessions == 9
    assert r.price == approx(100)
    assert r.eligible is True
    assert r.fail_rules == ()


def test_ex9_short_history_stays_eligible():
    r = ex9()
    assert r.history_sessions == 5
    assert r.eligible is True
    assert r.fail_rules == ()
    assert r.model_c_history_ok is False
    assert r.flags == frozenset({"SHORT_HISTORY"})


def test_ex9_null_adj_close_not_counted():
    adj = [None] * 5 + [100] * 5
    r = ex1(bars=ex1_bars(adj=adj))
    assert r.history_sessions == 5
    assert r.eligible is True
    assert r.fail_rules == ()
    assert r.model_c_history_ok is False
    assert r.flags == frozenset({"SHORT_HISTORY"})


def test_ex10_not_index_member():
    r = ex1(index_member=False)
    assert r.fail_rules == ("E1",)
    assert r.elig_fail_reason == "E1"
    assert r.eligible is False


def test_ex11_missing_volume():
    vols = list(EX1_VOLUMES)
    vols[BASE_DATES.index(d(11))] = None
    r = ex1(bars=bars_from(BASE_DATES, 100, vols))
    assert r.mdtv is None
    assert r.flags == frozenset({"INSUFFICIENT_VOLUME_HISTORY"})
    assert r.zero_volume_days == 0
    assert r.fail_rules == ("E2",)
    assert r.elig_fail_reason == "E2"
    assert r.eligible is False


def test_ex12_no_bars():
    r = run("TSX", [], 2e9)
    assert r.mdtv is None
    assert r.price is None
    assert r.stale_sessions is None
    assert r.zero_volume_days == 0
    assert r.history_sessions == 0
    assert r.fail_rules == ("E2", "E4", "E5")
    assert r.elig_fail_reason == "E2;E4;E5"
    assert r.flags == frozenset({"SHORT_HISTORY", "INSUFFICIENT_VOLUME_HISTORY"})
    assert r.model_c_history_ok is False
    assert r.eligible is False


def test_only_future_bars_behave_like_no_bars():
    bars = bars_from([d(16), d(17)], 100, 300_000)
    r = run("TSX", bars, 2e9)
    assert r.mdtv is None
    assert r.price is None
    assert r.stale_sessions is None
    assert r.history_sessions == 0
    assert r.fail_rules == ("E2", "E4", "E5")


# ---------------------------------------------------------------------------
# Market section selection
# ---------------------------------------------------------------------------


def test_market_selects_section():
    # Ex 2 bars under US thresholds: mdtv 4.76M < 25M and cap 2e9 < 5e9.
    r = run("US", ex2_bars(), 2e9)
    assert r.fail_rules == ("E2", "E3")
    # Ex 1 bars under TSX thresholds: everything passes.
    r = run("TSX", ex1_bars(), 6e9)
    assert r.eligible is True


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_unsorted_bars_accepted():
    bars = ex1_bars()
    random.Random(42).shuffle(bars)
    assert bars != ex1_bars()
    assert_ex1(ex1(bars=bars))


def test_reversed_bars_accepted():
    assert_ex1(ex1(bars=list(reversed(ex1_bars()))))


def test_plain_objects_accepted_as_bars():
    bars = [
        SimpleNamespace(date=b.date, close=b.close, adj_close=b.adj_close, volume=b.volume)
        for b in ex1_bars()
    ]
    assert_ex1(ex1(bars=bars))


@pytest.mark.parametrize("market", ["NYSE", "CA", "tsx", "us", ""])
def test_bad_market_raises(market):
    with pytest.raises(ValueError):
        run(market, ex1_bars(), 6e9)


def test_duplicate_dates_raise():
    bars = ex1_bars() + [Bar(d(10), 101, 101, 1000)]
    with pytest.raises(ValueError):
        ex1(bars=bars)


# ---------------------------------------------------------------------------
# Removal-rate report
# ---------------------------------------------------------------------------


def test_removal_report_example():
    rep = removal_report([ex1(), ex2(), ex5(), ex7(), ex9()])
    assert rep["n"] == 5
    assert rep["n_ineligible"] == 3
    assert rep["pct_ineligible"] == approx(60.0)
    assert rep["by_rule"] == {"E1": 0, "E2": 2, "E3": 1, "E4": 1, "E5": 1}
    assert rep["short_history"] == 1


def test_removal_report_empty():
    rep = removal_report([])
    assert rep["n"] == 0
    assert rep["n_ineligible"] == 0
    assert rep["pct_ineligible"] == 0.0
    assert rep["by_rule"] == {"E1": 0, "E2": 0, "E3": 0, "E4": 0, "E5": 0}
    assert rep["short_history"] == 0


def test_removal_report_counts_short_history_even_if_ineligible():
    r12 = run("TSX", [], 2e9)  # ineligible and short history
    rep = removal_report([r12, ex9()])
    assert rep["short_history"] == 2
    assert rep["by_rule"] == {"E1": 0, "E2": 1, "E3": 0, "E4": 1, "E5": 1}


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def write_yaml(path: Path, cfg: dict) -> Path:
    lines = []
    for sec, vals in cfg.items():
        lines.append(f"{sec}:")
        for k, v in vals.items():
            lines.append(f"  {k}: {v}")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_load_config_valid(tmp_path):
    p = write_yaml(tmp_path / "liq.yaml", DEFAULTS)
    cfg = load_config(p)
    for sec in ("tsx", "us"):
        for k in KEYS:
            assert cfg[sec][k] == DEFAULTS[sec][k]


def test_load_config_accepts_str_path(tmp_path):
    p = write_yaml(tmp_path / "liq.yaml", DEFAULTS)
    assert load_config(str(p))["us"]["min_mdtv"] == 25_000_000


@pytest.mark.parametrize("section", ["tsx", "us"])
def test_load_config_missing_section(tmp_path, section):
    cfg = copy.deepcopy(DEFAULTS)
    del cfg[section]
    p = write_yaml(tmp_path / "liq.yaml", cfg)
    with pytest.raises(ValueError):
        load_config(p)


@pytest.mark.parametrize("section", ["tsx", "us"])
@pytest.mark.parametrize("key", KEYS)
def test_load_config_missing_key(tmp_path, section, key):
    cfg = copy.deepcopy(DEFAULTS)
    del cfg[section][key]
    p = write_yaml(tmp_path / "liq.yaml", cfg)
    with pytest.raises(ValueError):
        load_config(p)


def test_repo_config_has_spec_defaults():
    cfg = load_config(REPO_ROOT / "config" / "liquidity.yaml")
    for sec in ("tsx", "us"):
        for k in KEYS:
            assert cfg[sec][k] == DEFAULTS[sec][k], (sec, k)


def test_golden_example_with_loaded_config(tmp_path):
    p = write_yaml(tmp_path / "liq.yaml", make_config())
    assert_ex1(ex1(config=load_config(p)))
