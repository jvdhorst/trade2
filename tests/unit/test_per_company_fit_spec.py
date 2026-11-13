"""Spec tests for the per-company model fit (docs/models/per_company_fit.md v0.1.0, `fit/0.1.0`).

Written from the spec only (§11 API contract), before the implementation exists:
    from valuation.backtest.fit import (FIT_MODEL_VERSION, FIT_PARAMS, fit_params_hash,
        FitThresholds, PairMetrics, HoldoutLeak, LABEL_COLUMNS, position, window_of,
        assert_no_holdout, strategy_returns, pair_metrics, activity_reason, bh_qvalues,
        classify, fit_table, load_fit_frame, run_fit)
Covers §1 constants and the params hash, §3.3 positions, §4 returns/costs/windows, §5 pair
metrics, §6 luck guard (activity, BH, classify), §8 output schema, §12 goldens G1-G4, G7 and the
§13 properties. The holdout guard (§7, G5) is in test_fit_holdout_guard_spec.py, the screen (§9,
G6) in test_screen_fit_spec.py.

`valuation.backtest.fit` is imported inside each test (via `fitmod()`), so every test fails on
its own until the module exists. Golden tolerance 1e-9 absolute (§12).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from valuation.backtest import engine as bte
from valuation.backtest import metrics as m

TOL = 1e-9
NAN = float("nan")
SELL_EXIT, BUY_ONLY = "SELL_EXIT", "BUY_ONLY"
FIT_HASH = "4d0a9e579c7d41a703ba4f2b0fdfd08f4883cb8315cb9b82e4c172313cd4b926"
FIT_PARAMS_JSON = (
    '{"FIT_CAN_EMIT":{"A":["BUY_ONLY","SELL_EXIT"],"B":["BUY_ONLY","SELL_EXIT"],'
    '"C":["BUY_ONLY","SELL_EXIT"],"D":["BUY_ONLY","SELL_EXIT"],"E":["BUY_ONLY","SELL_EXIT"],'
    '"F":["BUY_ONLY","SELL_EXIT"],"G":["SELL_EXIT"],"H":["BUY_ONLY","SELL_EXIT"],'
    '"J":["BUY_ONLY","SELL_EXIT"]},"FIT_CASH_RATE":0.0,"FIT_CONF_END":"2023-10-31",'
    '"FIT_CONF_MIN_ACTIVE":3,"FIT_CONF_MIN_MONTHS":24,"FIT_CONF_MIN_SWITCHES":1,'
    '"FIT_CONF_START":"2020-01-31","FIT_COST_BP":10,"FIT_HORIZON":1,"FIT_INITIAL_POSITION":1,'
    '"FIT_LOCK_END":"2023-10-31","FIT_MARKET":"US","FIT_MIN_EFFECT":0.01,'
    '"FIT_MODELS":["A","B","C","H","J","G","D","E","F"],"FIT_MODEL_VERSION":"fit/0.1.0",'
    '"FIT_PRIMARY_VARIANT":"SELL_EXIT","FIT_Q":0.1,"FIT_SEL_END":"2019-12-31",'
    '"FIT_SEL_MIN_ACTIVE":6,"FIT_SEL_MIN_MONTHS":36,"FIT_SEL_MIN_SWITCHES":2,'
    '"FIT_SEL_START":"2014-01-31"}')
MODELS = ("A", "B", "C", "H", "J", "G", "D", "E", "F")
# §8.1 column order
FIT_COLUMNS = [
    "run_id", "fit_version", "fit_params_hash", "market", "ticker", "model", "model_version",
    "variant", "is_primary",
    "sel_n_months", "sel_n_active", "sel_n_switches", "sel_ann_excess", "sel_t", "sel_p", "sel_q",
    "conf_n_months", "conf_n_active", "conf_n_switches", "conf_ann_excess", "conf_t", "conf_p",
    "status", "reason"]
FIT_TABLE_COLUMNS = FIT_COLUMNS[4:]  # §11: §8.1 minus run_id, fit_version, fit_params_hash, market
METRIC_COLUMNS = [c for c in FIT_COLUMNS if c.startswith(("sel_", "conf_"))]
FIT_RUNS_COLUMNS = [
    "run_id", "started_at", "market", "today", "git_sha", "fit_version", "fit_params_hash",
    "bt_thresholds_hash", "frozen_params_hash", "label_versions", "n_tickers",
    "n_family_sell_exit", "n_family_buy_only", "n_confirmed_sell_exit", "n_confirmed_buy_only"]


def fitmod():
    import valuation.backtest.fit as F

    return F


def close(a, b, tol=TOL):
    if b is None or pd.isna(b):
        return a is None or pd.isna(a)
    return a is not None and not pd.isna(a) and abs(float(a) - float(b)) <= tol


def isnull(x):
    return x is None or pd.isna(x)


def params_hash_value(F):
    # SPEC-QUESTION: §11 imports `fit_params_hash` without saying whether it is a constant or a
    # function (§8.1 calls it "§1 hash"). Accept either.
    h = F.fit_params_hash
    return h() if callable(h) else h


# ---------------------------------------------------------------------------
# G1 fixture (§12)
# ---------------------------------------------------------------------------

D = [date(2015, 1, 31), date(2015, 2, 28), date(2015, 3, 31), date(2015, 4, 30),
     date(2015, 5, 31), date(2015, 6, 30), date(2015, 7, 31), date(2015, 8, 31),
     date(2015, 9, 30), date(2015, 10, 31), date(2015, 11, 30), date(2015, 12, 31),
     date(2016, 1, 31), date(2016, 2, 29)]
T1 = [date(2015, 3, 2), date(2015, 4, 1), date(2015, 5, 1), date(2015, 6, 1), date(2015, 7, 1),
      date(2015, 8, 3), date(2015, 9, 1), date(2015, 10, 1), date(2015, 11, 2), date(2015, 12, 1),
      date(2016, 1, 1), date(2016, 2, 1), date(2016, 3, 1), date(2016, 4, 1)]
G1_SEL = (date(2015, 1, 31), date(2015, 10, 31))
G1_CONF = (date(2015, 11, 30), date(2016, 4, 30))
G1_LOCK = date(2016, 4, 30)
RET = {
    "AAA": [.02, -.06, .03, -.08, -.05, .04, .01, -.07, .02, -.01, -.05, .03, -.04, .02],
    "BBB": [.01, -.07, .02, -.09, .03, .02, -.06, .01, .01, .00, .04, .02, .05, .01],
    "CCC": [.03, .02, -.01, NAN, .01, -.02, .02, .01, .00, .01, .02, -.03, .01, .02],
    "DDD": [.01, .02, .03, -.02, .01, .02, .01, .02, .01, .01, .01, .02, -.01, .00],
    "EEE": [.01, -.05, .02, -.06, -.04, .03, .02, -.05, .01, .02, -.03, .02, .01, .03],
}
_AB = {"NS": "NO_SIGNAL", "NE": "NOT_ELIGIBLE", "-": None}
LAB = {
    ("AAA", "E"): "HOLD SELL BUY SELL SELL NS HOLD SELL SELL HOLD SELL HOLD SELL NE",
    ("BBB", "E"): "BUY SELL HOLD SELL HOLD BUY SELL HOLD HOLD HOLD SELL HOLD SELL HOLD",
    ("CCC", "E"): "HOLD SELL SELL HOLD SELL BUY HOLD SELL HOLD HOLD HOLD SELL HOLD HOLD",
    ("DDD", "E"): "HOLD HOLD HOLD SELL HOLD - BUY HOLD HOLD HOLD HOLD SELL HOLD HOLD",
    ("EEE", "E"): "HOLD SELL HOLD SELL SELL HOLD HOLD SELL SELL SELL HOLD HOLD HOLD BUY",
    ("AAA", "G"): "HOLD SELL SELL HOLD HOLD HOLD SELL HOLD HOLD HOLD SELL HOLD HOLD HOLD",
}


def labels(t, model):
    return [_AB.get(x, x) for x in LAB[(t, model)].split()]


def g1_th(F):
    return F.FitThresholds(sel_min_months=6, sel_min_active=2, sel_min_switches=2,
                           conf_min_months=3, conf_min_active=1, conf_min_switches=1, q=0.10,
                           min_effect=0.01,
                           can_emit={"E": {"SELL_EXIT", "BUY_ONLY"}, "G": {"SELL_EXIT"}})


def t1_of(t, i):
    # a NO_ENTRY row (ret NaN) has no exit session (forward_returns gives t1 None)
    return None if math.isnan(RET[t][i]) else T1[i]


def pair_obs(t, model, idx=range(14)):
    lab = labels(t, model)
    return pd.DataFrame({"date": [D[i] for i in idx], "label": [lab[i] for i in idx],
                         "ret": [RET[t][i] for i in idx], "t1": [t1_of(t, i) for i in idx]})


def g1_frame():
    """Long frame `ticker, model, date, label, ret, t1` (§11 accepts the long form)."""
    rows = []
    for (t, model), _ in LAB.items():
        lab = labels(t, model)
        for i in range(14):
            rows.append({"ticker": t, "model": model, "date": D[i], "label": lab[i],
                         "ret": RET[t][i], "t1": t1_of(t, i)})
    return pd.DataFrame(rows)


def versions():
    from fit_db_fixture import live_versions

    return live_versions()


# (sel), (conf), in_family, q, status, reason -- G1d table, verbatim
G1D = {
    ("AAA", "E", SELL_EXIT): ((8, 4, 5, 0.3825, 2.534568010738418, 0.019485995463755224),
                              (4, 2, 4, 0.258, 1.6350085663531526, 0.10028073422494856),
                              0.04871498865938806, "CONFIRMED", "OK"),
    ("BBB", "E", SELL_EXIT): ((8, 3, 6, 0.321, 1.9590086492171759, 0.0454762232812197),
                              (4, 2, 4, -0.282, -1.7871023864790274, 0.9140561144549066),
                              0.0757937054686995, "NOT_CONFIRMED", "CONF_BELOW_EFFECT"),
    ("CCC", "E", SELL_EXIT): ((7, 4, 4, -0.05828571428571429, -1.2653489768601378,
                               0.8736682012212719),
                              (4, 1, 2, 0.084, 0.9540527858909348, 0.2052204690499026),
                              0.8736682012212719, "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT"),
    ("DDD", "E", SELL_EXIT): ((8, 1, 2, 0.027, 0.9390442180993814, 0.18948566197027977),
                              (4, 1, 2, -0.066, -1.0634101379502299, 0.8171977706580784),
                              None, "INSUFFICIENT", "SEL_FEW_ACTIVE"),
    ("EEE", "E", SELL_EXIT): ((8, 4, 5, 0.2925, 2.546247426916576, 0.019156456560723584),
                              (4, 0, 0, 0.0, NAN, NAN),
                              0.04871498865938806, "INSUFFICIENT", "CONF_FEW_ACTIVE"),
    ("AAA", "G", SELL_EXIT): ((8, 3, 4, 0.024, 0.2238067144620166, 0.4146496683124279),
                              (4, 1, 2, 0.144, 0.9727953421229352, 0.20120247341636044),
                              0.5183120853905349, "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT"),
    ("AAA", "G", BUY_ONLY): (None, None, None, "NOT_TESTED", "NO_LABEL_TYPE"),
    ("BBB", "E", BUY_ONLY): ((8, 2, 3, 0.2355, 1.2128551108555325, 0.1322612485566378),
                             (4, 0, 1, -0.363, -3.279867562423663, 0.9767847609795925),
                             0.1322612485566378, "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT"),
    ("AAA", "E", BUY_ONLY): ((8, 1, 3, 0.2805, 1.4276710835930253, 0.09822001251629679),
                             (4, 0, 1, 0.117, 0.48148148148148157, 0.33155645150122326),
                             None, "INSUFFICIENT", "SEL_FEW_ACTIVE"),
    ("CCC", "E", BUY_ONLY): ((7, 1, 4, -0.144, -2.327950780989512, 0.9705972904469337),
                             (4, 0, 1, -0.063, -0.4371522885184593, 0.654221833935601),
                             None, "INSUFFICIENT", "SEL_FEW_ACTIVE"),
    ("DDD", "E", BUY_ONLY): ((8, 1, 3, -0.1395, -2.1212415119074643, 0.9642049332697483),
                             (4, 0, 1, -0.063, -0.8047217485013146, 0.760076937296911),
                             None, "INSUFFICIENT", "SEL_FEW_ACTIVE"),
    ("EEE", "E", BUY_ONLY): ((8, 0, 1, 0.1785, 1.0994374318721514, 0.1539726199500789),
                             (4, 1, 2, -0.006, -0.04729837698404029, 0.5173760044626572),
                             None, "INSUFFICIENT", "SEL_FEW_ACTIVE"),
}


def assert_metrics(pm, exp):
    n_months, n_active, n_switches, ann, t, p = exp
    assert pm.n_months == n_months
    assert pm.n_active == n_active
    assert pm.n_switches == n_switches
    assert close(pm.ann_excess, ann), (pm.ann_excess, ann)
    assert close(pm.t_stat, t), (pm.t_stat, t)
    assert close(pm.p_value, p), (pm.p_value, p)


# ---------------------------------------------------------------------------
# §1 constants and the fit params hash
# ---------------------------------------------------------------------------


def test_constants_values():
    F = fitmod()
    assert F.FIT_MODEL_VERSION == "fit/0.1.0"
    assert F.FIT_MARKET == "US"
    assert F.FIT_HORIZON == 1
    assert (F.FIT_SEL_START, F.FIT_SEL_END) == (date(2014, 1, 31), date(2019, 12, 31))
    assert (F.FIT_CONF_START, F.FIT_CONF_END) == (date(2020, 1, 31), date(2023, 10, 31))
    assert F.FIT_LOCK_END == date(2023, 10, 31)
    assert F.FIT_COST_BP == 10
    assert F.FIT_COST == pytest.approx(0.001, abs=1e-15)
    assert F.FIT_CASH_RATE == 0.0
    assert F.FIT_INITIAL_POSITION == 1
    assert F.FIT_Q == 0.10
    assert F.FIT_MIN_EFFECT == 0.01
    assert (F.FIT_SEL_MIN_MONTHS, F.FIT_SEL_MIN_ACTIVE, F.FIT_SEL_MIN_SWITCHES) == (36, 6, 2)
    assert (F.FIT_CONF_MIN_MONTHS, F.FIT_CONF_MIN_ACTIVE, F.FIT_CONF_MIN_SWITCHES) == (24, 3, 1)
    assert F.FIT_PRIMARY_VARIANT == "SELL_EXIT"
    assert tuple(F.FIT_MODELS) == MODELS


def test_can_emit_from_specs():  # §3.2
    F = fitmod()
    assert set(F.FIT_CAN_EMIT) == set(MODELS)
    for k in MODELS:
        exp = {"SELL_EXIT"} if k == "G" else {"SELL_EXIT", "BUY_ONLY"}
        assert set(F.FIT_CAN_EMIT[k]) == exp, k


def test_label_columns():  # §3.1 / §11
    F = fitmod()
    assert dict(F.LABEL_COLUMNS) == {"A": "a_signal", "B": "b_signal", "C": "c_signal",
                                     "H": "h_signal", "J": "j_signal", "G": "g_signal",
                                     "D": "d_signal", "E": "e_signal", "F": "f_signal"}


def test_fit_params_canonical_json_and_hash():
    F = fitmod()
    from valuation.models.frozen import canonical_json, params_sha256

    assert canonical_json(F.FIT_PARAMS) == FIT_PARAMS_JSON
    assert params_sha256(F.FIT_PARAMS) == FIT_HASH
    assert params_hash_value(F) == FIT_HASH


def test_window_constants_ordering():  # §7 enforcement 1 (module import assertion)
    F = fitmod()
    assert (F.FIT_SEL_START < F.FIT_SEL_END < F.FIT_CONF_START <= F.FIT_CONF_END
            <= F.FIT_LOCK_END == date(2023, 10, 31))


def test_fit_thresholds_defaults():  # §6.4
    F = fitmod()
    th = F.FitThresholds()
    assert (th.sel_min_months, th.sel_min_active, th.sel_min_switches) == (36, 6, 2)
    assert (th.conf_min_months, th.conf_min_active, th.conf_min_switches) == (24, 3, 1)
    assert th.q == 0.10 and th.min_effect == 0.01
    assert {k: set(v) for k, v in th.can_emit.items()} == {
        k: set(v) for k, v in F.FIT_CAN_EMIT.items()}
    assert dataclasses.is_dataclass(th)
    with pytest.raises(dataclasses.FrozenInstanceError):
        th.q = 0.2


def test_pair_metrics_dataclass():  # §5
    F = fitmod()
    names = [f.name for f in dataclasses.fields(F.PairMetrics)]
    assert names == ["n_months", "n_active", "n_switches", "ann_excess", "t_stat", "p_value"]
    pm = F.PairMetrics(1, 0, 0, 0.0, NAN, NAN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pm.n_months = 2


def test_holdout_leak_is_value_error():
    F = fitmod()
    assert issubclass(F.HoldoutLeak, ValueError)


# ---------------------------------------------------------------------------
# §3.3 position (G3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,sell_exit,buy_only", [
    ("BUY", 1, 1), ("HOLD", 1, 0), ("SELL", 0, 0), ("NO_SIGNAL", 1, 0),
    ("NOT_ELIGIBLE", 1, 0), (None, 1, 0), ("SOMETHING_ELSE", 1, 0), ("", 1, 0),
])
def test_g3_position(label, sell_exit, buy_only):
    F = fitmod()
    assert F.position(label, SELL_EXIT) == sell_exit
    assert F.position(label, BUY_ONLY) == buy_only
    assert isinstance(F.position(label, SELL_EXIT), int | np.integer)


def test_position_nan_label_is_missing():  # §3.1: NULL label treated as missing
    F = fitmod()
    assert F.position(NAN, SELL_EXIT) == 1
    assert F.position(NAN, BUY_ONLY) == 0


# ---------------------------------------------------------------------------
# §4.3 windows (G1 membership, G4)
# ---------------------------------------------------------------------------


def test_g1_window_membership_parameterised():
    F = fitmod()
    got = [F.window_of(D[i], T1[i], sel=G1_SEL, conf=G1_CONF) for i in range(14)]
    assert got == ["SEL"] * 8 + [None, None] + ["CONF"] * 4


@pytest.mark.parametrize("d,t1,exp", [
    (date(2019, 10, 31), date(2019, 12, 2), "SEL"),
    (date(2019, 11, 30), date(2020, 1, 2), None),
    (date(2019, 11, 30), date(2020, 1, 1), None),
    (date(2019, 12, 31), date(2020, 2, 3), None),
    (date(2023, 8, 31), date(2023, 10, 2), "CONF"),
    (date(2023, 9, 30), date(2023, 11, 1), None),
    (date(2023, 10, 31), date(2023, 12, 1), None),
    (date(2014, 1, 31), date(2014, 3, 3), "SEL"),
    (date(2020, 1, 31), date(2020, 3, 2), "CONF"),
    (date(2013, 12, 31), date(2014, 2, 3), None),  # before the selection start
    # EARLY_EXIT row: counts when its t1 is inside the window
    (date(2019, 11, 30), date(2019, 12, 13), "SEL"),
    (date(2019, 12, 31), date(2019, 12, 31), "SEL"),  # t1 == window end, inclusive
])
def test_g4_window_of_real_bounds(d, t1, exp):
    F = fitmod()
    assert F.window_of(d, t1) == exp


def _weekday_prices(a, b, ticker="X", seed=3):
    from fit_db_fixture import weekdays

    sess = weekdays(a, b)
    rng = np.random.RandomState(seed)
    px = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, len(sess))))
    return pd.DataFrame({"ticker": ticker, "date": sess, "adj_close": px})


def test_g4_real_window_boundaries():
    F = fitmod()
    dates = bte.rebalance_dates(bte.BT_START, date(2026, 11, 10))
    assert len(dates) == 154
    ins = bte.in_sample_dates(dates)
    assert len(ins) == 118 and ins[-1] == date(2023, 10, 31)
    prices = _weekday_prices(date(2013, 12, 2), date(2026, 11, 9))
    fr = m.forward_returns(prices, ins, horizons=(F.FIT_HORIZON,)).sort_values("date")
    fr["w"] = [F.window_of(d, t1) for d, t1 in zip(fr["date"], fr["t1"])]
    sel = fr[fr["w"] == "SEL"]
    conf = fr[fr["w"] == "CONF"]
    assert len(sel) == 70 and len(conf) == 44
    assert sel["date"].iloc[0] == date(2014, 1, 31) and sel["date"].iloc[-1] == date(2019, 10, 31)
    assert (sel["t0"].iloc[0], sel["t1"].iloc[0]) == (date(2014, 2, 3), date(2014, 3, 3))
    assert (sel["t0"].iloc[-1], sel["t1"].iloc[-1]) == (date(2019, 11, 1), date(2019, 12, 2))
    assert conf["date"].iloc[0] == date(2020, 1, 31)
    assert conf["date"].iloc[-1] == date(2023, 8, 31)
    assert (conf["t0"].iloc[0], conf["t1"].iloc[0]) == (date(2020, 2, 3), date(2020, 3, 2))
    assert conf["t1"].iloc[-1] == date(2023, 10, 2)
    unused = fr[fr["w"].isna()]
    assert list(unused["date"]) == [date(2019, 11, 30), date(2019, 12, 31), date(2023, 9, 30),
                                    date(2023, 10, 31)]
    exp_t1 = {date(2019, 11, 30): date(2020, 1, 1), date(2019, 12, 31): date(2020, 2, 3),
              date(2023, 9, 30): date(2023, 11, 1), date(2023, 10, 31): date(2023, 12, 1)}
    assert dict(zip(unused["date"], unused["t1"])) == exp_t1


def test_g4_pair_metrics_default_windows_count_70_and_44():
    F = fitmod()
    ins = bte.in_sample_dates(bte.rebalance_dates(bte.BT_START, date(2026, 11, 10)))
    prices = _weekday_prices(date(2013, 12, 2), date(2026, 11, 9))
    fr = m.forward_returns(prices, ins, horizons=(1,))
    fr = fr[fr["t1"].notna() & (fr["t1"] <= F.FIT_LOCK_END)]  # as the loader (§7.2)
    lab = ["SELL" if i % 5 == 0 else ("HOLD" if i % 3 else "BUY") for i in range(len(fr))]
    obs = pd.DataFrame({"date": fr["date"].tolist(), "label": lab, "ret": fr["ret"].tolist(),
                        "t1": fr["t1"].tolist()})
    s = F.pair_metrics(obs, SELL_EXIT, F.FIT_SEL_START, F.FIT_SEL_END)
    c = F.pair_metrics(obs, SELL_EXIT, F.FIT_CONF_START, F.FIT_CONF_END)
    assert s.n_months == 70 and c.n_months == 44


def test_g7_chaining_of_one_month_returns():  # uses the unchanged forward_returns (§4.1)
    from fit_db_fixture import weekdays

    sess = weekdays(date(2015, 2, 2), date(2015, 5, 29))
    px = pd.DataFrame({"ticker": "X", "date": sess,
                       "adj_close": [100 + 0.37 * i + (i % 7) for i in range(len(sess))]})
    ds = [date(2015, 1, 31), date(2015, 2, 28), date(2015, 3, 31)]
    fr = m.forward_returns(px, ds, horizons=(1,)).set_index("date")
    assert fr.loc[ds[0], "t0"] == date(2015, 2, 2)
    assert fr.loc[ds[0], "t1"] == fr.loc[ds[1], "t0"] == date(2015, 3, 2)
    assert fr.loc[ds[1], "t1"] == fr.loc[ds[2], "t0"] == date(2015, 4, 1)
    adj = dict(zip(px["date"], px["adj_close"]))
    prod = float(np.prod(1 + fr["ret"].to_numpy()))
    assert close(prod, adj[date(2015, 5, 1)] / adj[date(2015, 2, 2)])


# ---------------------------------------------------------------------------
# §4.2 strategy_returns (G1a, G1b, G1c), costs, cash
# ---------------------------------------------------------------------------

SR_COLUMNS = ["date", "label", "ret", "pos", "switch", "cost", "r_strat", "r_hold", "excess"]


def test_g1a_strategy_returns_aaa_e_sell_exit():
    F = fitmod()
    obs = pair_obs("AAA", "E", range(8))[["date", "label", "ret"]]
    out = F.strategy_returns(obs.iloc[::-1].reset_index(drop=True), SELL_EXIT)  # any order
    assert list(out.columns) == SR_COLUMNS
    assert list(out["date"]) == D[:8]
    assert list(out["pos"]) == [1, 0, 1, 0, 0, 1, 1, 0]  # d6 NO_SIGNAL -> 1, not carried
    assert list(out["switch"]) == [0, 1, 1, 1, 0, 1, 0, 1]
    for a, b in zip(out["r_strat"], [.02, -.001, .029, -.001, 0, .039, .01, -.001]):
        assert close(a, b)
    for a, b in zip(out["excess"], [0, .059, -.001, .079, .05, -.001, 0, .069]):
        assert close(a, b)
    for a, b in zip(out["r_hold"], RET["AAA"][:8]):
        assert close(a, b)
    # SPEC-QUESTION: §11 lists a `cost` column without defining it; read as FIT_COST * switch.
    for a, s in zip(out["cost"], out["switch"]):
        assert close(a, 0.001 * s)
    # SPEC-QUESTION: §12 G1a writes "ann_excess 0.3825 (= 12 · 0.254/8)", but the listed e values
    # sum to 0.255 and 12 · 0.255 / 8 = 0.3825 (12 · 0.254 / 8 = 0.381). The e column and 0.3825
    # agree; the parenthetical 0.254 is a typo. Tested: sum 0.255.
    assert close(sum(out["excess"]), 0.255)


def test_g1b_gap_rule_ccc_e_sell_exit():
    F = fitmod()
    idx = [0, 1, 2, 4, 5, 6, 7]  # d4 has ret NaN (NO_ENTRY) and is dropped
    obs = pair_obs("CCC", "E", idx)[["date", "label", "ret"]]
    out = F.strategy_returns(obs, SELL_EXIT)
    assert list(out["pos"]) == [1, 0, 0, 0, 1, 1, 0]
    assert list(out["switch"]) == [0, 1, 0, 1, 1, 0, 1]  # d5: prev = 1 after the gap
    for a, b in zip(out["excess"], [0, -.021, .01, -.011, -.001, 0, -.011]):
        assert close(a, b)


def test_g1b_pair_metrics_drops_nan_and_applies_gap():
    F = fitmod()
    pm = F.pair_metrics(pair_obs("CCC", "E"), SELL_EXIT, *G1_SEL, lock=G1_LOCK)
    assert_metrics(pm, (7, 4, 4, -0.05828571428571429, -1.2653489768601378,
                        0.8736682012212719))


def test_g1c_buy_only_bbb_e():
    F = fitmod()
    obs = pair_obs("BBB", "E", range(8))[["date", "label", "ret"]]
    out = F.strategy_returns(obs, BUY_ONLY)
    assert list(out["pos"]) == [1, 0, 0, 0, 0, 1, 0, 0]  # HOLD -> cash
    assert list(out["switch"]) == [0, 1, 0, 0, 0, 1, 1, 0]
    for a, b in zip(out["excess"], [0, .069, -.02, .09, -.03, -.001, .059, -.01]):
        assert close(a, b)
    pm = F.pair_metrics(pair_obs("BBB", "E"), BUY_ONLY, *G1_SEL, lock=G1_LOCK)
    assert_metrics(pm, (8, 2, 3, 0.2355, 1.2128551108555325, 0.1322612485566378))


def test_initial_state_sell_exit_window_starting_in_sell_pays_one_switch():  # §4.5
    F = fitmod()
    obs = pd.DataFrame({"date": [D[0], D[1]], "label": ["SELL", "SELL"], "ret": [-.02, .01]})
    out = F.strategy_returns(obs, SELL_EXIT)
    assert list(out["switch"]) == [1, 0]
    assert close(out["r_strat"].iloc[0], -0.001) and close(out["r_strat"].iloc[1], 0.0)
    assert close(out["excess"].iloc[0], 0.02 - 0.001)


def test_initial_state_buy_only_window_starting_in_cash_pays_one_switch():  # §4.5
    F = fitmod()
    obs = pd.DataFrame({"date": [D[0], D[1]], "label": ["HOLD", "BUY"], "ret": [.03, .02]})
    out = F.strategy_returns(obs, BUY_ONLY)
    assert list(out["pos"]) == [0, 1]
    assert list(out["switch"]) == [1, 1]
    assert close(out["excess"].iloc[0], -0.03 - 0.001)
    assert close(out["excess"].iloc[1], -0.001)


def test_gap_after_cash_month_resets_to_invested():  # §4.2: prev = 1 after any gap
    F = fitmod()
    obs = pd.DataFrame({"date": [D[0], D[2]], "label": ["SELL", "HOLD"], "ret": [.01, .02]})
    out = F.strategy_returns(obs, SELL_EXIT)
    # d3 follows a gap (d2 missing): prev = 1, pos 1 -> no switch (a carried 0 would charge one)
    assert list(out["switch"]) == [1, 0]
    assert close(out["excess"].iloc[1], 0.0)


def test_transaction_cost_parameter():
    F = fitmod()
    obs = pair_obs("AAA", "E", range(8))[["date", "label", "ret"]]
    out = F.strategy_returns(obs, SELL_EXIT, cost=0.0025)
    for r, p, s, ret in zip(out["r_strat"], out["pos"], out["switch"], out["ret"]):
        assert close(r, p * ret - 0.0025 * s)


def test_cash_rate_parameter():  # §4.2 r_strat = pos·ret + (1 − pos)·cash − cost·switch
    F = fitmod()
    obs = pair_obs("AAA", "E", range(8))[["date", "label", "ret"]]
    out = F.strategy_returns(obs, SELL_EXIT, cost=0.001, cash=0.002)
    exp = [p * r + (1 - p) * 0.002 - 0.001 * s
           for p, r, s in zip([1, 0, 1, 0, 0, 1, 1, 0], RET["AAA"][:8], [0, 1, 1, 1, 0, 1, 0, 1])]
    for a, b in zip(out["r_strat"], exp):
        assert close(a, b)
    assert close(out["r_strat"].iloc[1], 0.001)  # d2: cash 0.002 − cost 0.001
    for a, b, h in zip(out["excess"], out["r_strat"], out["r_hold"]):
        assert close(a, b - h)


def test_default_cash_is_zero():
    F = fitmod()
    obs = pair_obs("AAA", "E", range(8))[["date", "label", "ret"]]
    a = F.strategy_returns(obs, SELL_EXIT)
    b = F.strategy_returns(obs, SELL_EXIT, cost=F.FIT_COST, cash=0.0)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


# ---------------------------------------------------------------------------
# §5 pair_metrics (G1a and G1d, every pair, both windows)
# ---------------------------------------------------------------------------


def test_g1a_pair_metrics():
    F = fitmod()
    pm = F.pair_metrics(pair_obs("AAA", "E"), SELL_EXIT, *G1_SEL, cost=0.001, lock=G1_LOCK)
    assert isinstance(pm, F.PairMetrics)
    assert_metrics(pm, (8, 4, 5, 0.3825, 2.534568010738418, 0.019485995463755224))


@pytest.mark.parametrize("key", [k for k, v in G1D.items() if v[0] is not None])
def test_g1d_pair_metrics_both_windows(key):
    F = fitmod()
    t, model, variant = key
    sel, conf = G1D[key][0], G1D[key][1]
    obs = pair_obs(t, model)
    assert_metrics(F.pair_metrics(obs, variant, *G1_SEL, lock=G1_LOCK), sel)
    assert_metrics(F.pair_metrics(obs, variant, *G1_CONF, lock=G1_LOCK), conf)


def test_p_value_equals_scipy_one_sided_ttest():
    F = fitmod()
    obs = pair_obs("BBB", "E")
    out = F.strategy_returns(obs[obs["date"] <= D[7]][["date", "label", "ret"]], SELL_EXIT)
    ref = stats.ttest_1samp(out["excess"].to_numpy(), 0, alternative="greater")
    pm = F.pair_metrics(obs, SELL_EXIT, *G1_SEL, lock=G1_LOCK)
    assert close(pm.t_stat, float(ref.statistic)) and close(pm.p_value, float(ref.pvalue))


def test_zero_variance_gives_nan_t_and_p():  # EEE conf: e ≡ 0
    F = fitmod()
    pm = F.pair_metrics(pair_obs("EEE", "E"), SELL_EXIT, *G1_CONF, lock=G1_LOCK)
    assert (pm.n_months, pm.n_active, pm.n_switches) == (4, 0, 0)
    assert pm.ann_excess == 0.0 and math.isnan(pm.t_stat) and math.isnan(pm.p_value)


def test_single_month_gives_nan_t_and_empty_window_nan_ann():
    F = fitmod()
    obs = pair_obs("AAA", "E")
    # window [d2, 2015-04-01]: d2 (t1 2015-04-01) is the only kept month
    pm = F.pair_metrics(obs, SELL_EXIT, D[1], date(2015, 4, 1), lock=G1_LOCK)
    assert pm.n_months == 1 and close(pm.ann_excess, 12 * 0.059)
    assert math.isnan(pm.t_stat) and math.isnan(pm.p_value)
    empty = F.pair_metrics(obs, SELL_EXIT, date(2014, 1, 31), date(2014, 6, 30), lock=G1_LOCK)
    assert empty.n_months == 0 and empty.n_active == 0 and empty.n_switches == 0
    assert math.isnan(empty.ann_excess) and math.isnan(empty.t_stat)


def test_pair_metrics_drops_rows_exiting_after_window_end():  # §5 step 2: t1 ≤ end
    F = fitmod()
    obs = pair_obs("AAA", "E")
    # end = 2015-10-31 keeps d1-d8; d9 (2015-09-30, t1 2015-11-02) is dropped by the t1 rule
    pm = F.pair_metrics(obs, SELL_EXIT, date(2015, 1, 31), date(2015, 10, 31), lock=G1_LOCK)
    assert pm.n_months == 8
    pm2 = F.pair_metrics(obs, SELL_EXIT, date(2015, 1, 31), date(2015, 11, 2), lock=G1_LOCK)
    assert pm2.n_months == 9


# ---------------------------------------------------------------------------
# §13 properties on the pure functions
# ---------------------------------------------------------------------------


def test_hold_identity_never_sell():
    F = fitmod()
    obs = pair_obs("DDD", "E")
    obs["label"] = ["HOLD", "BUY", None, "NO_SIGNAL", "NOT_ELIGIBLE"] * 2 + ["HOLD"] * 4
    out = F.strategy_returns(obs[["date", "label", "ret"]].dropna(subset=["ret"]), SELL_EXIT)
    assert (out["excess"] == 0).all() and (out["switch"] == 0).all()
    pm = F.pair_metrics(obs, SELL_EXIT, *G1_SEL, lock=G1_LOCK)
    assert pm.n_active == 0 and pm.ann_excess == 0.0
    assert F.activity_reason(pm, 6, 2, 2) == "FEW_ACTIVE"


def test_hold_identity_buy_only_all_buy():
    F = fitmod()
    obs = pair_obs("AAA", "E")
    obs["label"] = "BUY"
    pm = F.pair_metrics(obs, BUY_ONLY, *G1_SEL, lock=G1_LOCK)
    assert pm.n_switches == 0 and pm.ann_excess == 0.0 and pm.n_active == 8


@pytest.mark.parametrize("key", [k for k, v in G1D.items() if v[0] is not None])
def test_cost_monotonicity(key):
    F = fitmod()
    t, model, variant = key
    obs = pair_obs(t, model)
    anns = [F.pair_metrics(obs, variant, *G1_SEL, cost=c, lock=G1_LOCK).ann_excess
            for c in (0.0, 0.0005, 0.001, 0.002, 0.01)]
    assert all(b <= a + 1e-15 for a, b in zip(anns, anns[1:])), anns


# ---------------------------------------------------------------------------
# §6.1 activity minimums
# ---------------------------------------------------------------------------


def pm_(F, n_months=40, n_active=8, n_switches=3, ann=0.05, t=2.0, p=0.03):
    return F.PairMetrics(n_months, n_active, n_switches, ann, t, p)


def test_activity_reason_order_and_boundaries():
    F = fitmod()
    assert F.activity_reason(pm_(F, 36, 6, 2), 36, 6, 2) is None  # equal = enough
    assert F.activity_reason(pm_(F, 35, 6, 2), 36, 6, 2) == "FEW_MONTHS"
    assert F.activity_reason(pm_(F, 36, 5, 2), 36, 6, 2) == "FEW_ACTIVE"
    assert F.activity_reason(pm_(F, 36, 6, 1), 36, 6, 2) == "FEW_SWITCHES"
    assert F.activity_reason(pm_(F, 10, 0, 0), 36, 6, 2) == "FEW_MONTHS"  # first check wins
    assert F.activity_reason(pm_(F, 36, 0, 0), 36, 6, 2) == "FEW_ACTIVE"
    assert F.activity_reason(pm_(F, 24, 3, 1), 24, 3, 1) is None
    assert F.activity_reason(pm_(F, 23, 3, 1), 24, 3, 1) == "FEW_MONTHS"
    assert F.activity_reason(pm_(F, 24, 2, 1), 24, 3, 1) == "FEW_ACTIVE"
    assert F.activity_reason(pm_(F, 24, 3, 0), 24, 3, 1) == "FEW_SWITCHES"


# ---------------------------------------------------------------------------
# §6.2 BH q-values (G1e, G2)
# ---------------------------------------------------------------------------


def assert_q(got, exp):
    got = np.asarray(got, dtype=float)
    assert got.shape == (len(exp),)
    for a, b in zip(got, exp):
        assert close(a, b), (got, exp)


def test_g2_bh_qvalues():
    F = fitmod()
    assert_q(F.bh_qvalues([0.01, 0.04, 0.03, NAN, 0.5]),
             [0.04, 0.0533333333333333, 0.0533333333333333, NAN, 0.5])
    assert_q(F.bh_qvalues([0.02, 0.02, 0.2]), [0.03, 0.03, 0.2])
    assert_q(F.bh_qvalues([0.3]), [0.3])
    out = F.bh_qvalues([])
    assert isinstance(out, np.ndarray) and len(out) == 0


def test_g1e_bh_sell_exit_family():
    F = fitmod()
    p = [0.019485995463755224, 0.0454762232812197, 0.8736682012212719, 0.019156456560723584,
         0.4146496683124279]  # AAA·E, BBB, CCC, EEE, AAA·G (input order)
    assert_q(F.bh_qvalues(p), [0.04871498865938806, 0.0757937054686995, 0.8736682012212719,
                               0.04871498865938806, 0.5183120853905349])


def test_bh_returns_ndarray_and_handles_all_nan():
    F = fitmod()
    out = F.bh_qvalues([NAN, NAN])
    assert isinstance(out, np.ndarray) and np.isnan(out).all() and len(out) == 2
    assert isinstance(F.bh_qvalues([0.1, 0.2]), np.ndarray)


def test_bh_capped_at_one_and_nan_not_counted_in_m():
    F = fitmod()
    assert_q(F.bh_qvalues([0.9, 0.95, NAN]), [0.95, 0.95, NAN])  # m = 2 (NaN excluded)
    assert_q(F.bh_qvalues([0.8, NAN, NAN, NAN]), [0.8, NAN, NAN, NAN])
    assert_q(F.bh_qvalues([0.6, 0.7]), [0.7, 0.7])
    assert_q(F.bh_qvalues([0.9, 0.99, 0.999]), [0.999, 0.999, 0.999])
    assert (F.bh_qvalues([0.5, 0.9, 0.99, 0.6]) <= 1).all()


def test_bh_ties_get_equal_q():
    F = fitmod()
    q = F.bh_qvalues([0.04, 0.01, 0.04, 0.04])
    assert_q(q, [0.04, 0.04, 0.04, 0.04])  # 4·.01/1 = .04; 4·.04/4 = .04
    q2 = F.bh_qvalues([0.03, 0.03, 0.03, 0.5])
    assert_q(q2, [0.04, 0.04, 0.04, 0.5])


def test_bh_properties_random():
    F = fitmod()
    rng = np.random.RandomState(0)
    for _ in range(25):
        n = rng.randint(1, 40)
        p = rng.uniform(0, 1, n) ** 2
        p[rng.rand(n) < 0.2] = NAN
        q = F.bh_qvalues(p)
        ok = ~np.isnan(p)
        assert (np.isnan(q) == ~ok).all()
        assert (q[ok] >= p[ok] - 1e-15).all() and (q[ok] <= 1).all()
        order = np.argsort(p[ok], kind="stable")
        qs = q[ok][order]
        assert (np.diff(qs) >= -1e-15).all()  # monotone in p
        perm = rng.permutation(n)
        assert np.allclose(F.bh_qvalues(p[perm]), q[perm], equal_nan=True, atol=1e-15)


# ---------------------------------------------------------------------------
# §6.3-6.4 classify
# ---------------------------------------------------------------------------


def conf_(F, n_months=30, n_active=5, n_switches=2, ann=0.02):
    return F.PairMetrics(n_months, n_active, n_switches, ann, 1.0, 0.2)


def test_classify_rule_order():
    F = fitmod()
    th = F.FitThresholds()
    good_sel, good_conf = pm_(F), conf_(F)
    assert F.classify(SELL_EXIT, "A", good_sel, good_conf, 0.05, th) == ("CONFIRMED", "OK")
    # rule 1 wins over everything
    assert F.classify(BUY_ONLY, "G", good_sel, good_conf, 0.05, th) == (
        "NOT_TESTED", "NO_LABEL_TYPE")
    # rule 2 (selection activity) before rule 3 and 4
    assert F.classify(SELL_EXIT, "A", pm_(F, n_months=35), conf_(F, n_months=1), 0.5, th) == (
        "INSUFFICIENT", "SEL_FEW_MONTHS")
    assert F.classify(SELL_EXIT, "A", pm_(F, n_active=5), good_conf, NAN, th) == (
        "INSUFFICIENT", "SEL_FEW_ACTIVE")
    assert F.classify(SELL_EXIT, "A", pm_(F, n_switches=1), good_conf, NAN, th) == (
        "INSUFFICIENT", "SEL_FEW_SWITCHES")
    assert F.classify(SELL_EXIT, "A", pm_(F, t=NAN, p=NAN), good_conf, NAN, th) == (
        "INSUFFICIENT", "SEL_ZERO_VAR")
    # rule 3 before rule 4
    assert F.classify(SELL_EXIT, "A", good_sel, conf_(F, n_months=1), 0.11, th) == (
        "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT")
    # rule 4
    assert F.classify(SELL_EXIT, "A", good_sel, conf_(F, n_months=23), 0.05, th) == (
        "INSUFFICIENT", "CONF_FEW_MONTHS")
    assert F.classify(SELL_EXIT, "A", good_sel, conf_(F, n_active=2), 0.05, th) == (
        "INSUFFICIENT", "CONF_FEW_ACTIVE")
    assert F.classify(SELL_EXIT, "A", good_sel, conf_(F, n_switches=0), 0.05, th) == (
        "INSUFFICIENT", "CONF_FEW_SWITCHES")
    # rule 6
    assert F.classify(SELL_EXIT, "A", good_sel, conf_(F, ann=0.005), 0.05, th) == (
        "NOT_CONFIRMED", "CONF_BELOW_EFFECT")


def test_classify_q_boundary_inclusive():
    F = fitmod()
    th = F.FitThresholds()
    assert F.classify(SELL_EXIT, "C", pm_(F), conf_(F), 0.10, th)[0] == "CONFIRMED"
    assert F.classify(SELL_EXIT, "C", pm_(F), conf_(F), 0.1000000001, th) == (
        "NOT_CONFIRMED", "SEL_NOT_SIGNIFICANT")


def test_classify_effect_threshold_inclusive():
    F = fitmod()
    th = F.FitThresholds()
    assert F.classify(SELL_EXIT, "H", pm_(F), conf_(F, ann=0.01), 0.05, th) == ("CONFIRMED", "OK")
    assert F.classify(SELL_EXIT, "H", pm_(F), conf_(F, ann=0.0099999999), 0.05, th) == (
        "NOT_CONFIRMED", "CONF_BELOW_EFFECT")
    assert F.classify(SELL_EXIT, "H", pm_(F), conf_(F, ann=-0.2), 0.05, th) == (
        "NOT_CONFIRMED", "CONF_BELOW_EFFECT")


def test_classify_conf_p_not_used():  # §6.3: confirmation p-values reported, not used
    F = fitmod()
    th = F.FitThresholds()
    c = F.PairMetrics(30, 5, 2, 0.02, NAN, NAN)
    assert F.classify(SELL_EXIT, "J", pm_(F), c, 0.05, th) == ("CONFIRMED", "OK")
    c2 = F.PairMetrics(30, 5, 2, 0.02, -1.0, 0.99)
    assert F.classify(SELL_EXIT, "J", pm_(F), c2, 0.05, th) == ("CONFIRMED", "OK")


@pytest.mark.parametrize("model", [k for k in MODELS if k != "G"])
def test_classify_buy_only_tested_for_all_but_g(model):
    F = fitmod()
    assert F.classify(BUY_ONLY, model, pm_(F), conf_(F), 0.05)[0] == "CONFIRMED"


@pytest.mark.parametrize("model", MODELS)
def test_classify_sell_exit_tested_for_all_nine(model):
    F = fitmod()
    assert F.classify(SELL_EXIT, model, pm_(F), conf_(F), 0.05)[0] != "NOT_TESTED"


def test_classify_default_thresholds_are_spec_constants():
    F = fitmod()
    # 36 months, 6 active, 2 switches is the minimum for selection under the defaults
    assert F.classify(SELL_EXIT, "A", pm_(F, 36, 6, 2), conf_(F, 24, 3, 1), 0.05) == (
        "CONFIRMED", "OK")
    assert F.classify(SELL_EXIT, "A", pm_(F, 36, 6, 2), conf_(F, 24, 3, 1, ann=0.01), 0.05)[0] \
        == "CONFIRMED"


# ---------------------------------------------------------------------------
# §11 fit_table on the G1 fixture (G1d, G1e, family membership, NOT_TESTED)
# ---------------------------------------------------------------------------


def run_g1_table(F, frame=None):
    out = F.fit_table(g1_frame() if frame is None else frame, versions(), sel=G1_SEL,
                      conf=G1_CONF, th=g1_th(F), cost=0.001, lock=G1_LOCK)
    return out


def g1_rows(out):
    sub = out[out["model"].isin(["E", "G"])].copy()
    return {(r.ticker, r.model, r.variant): r for r in sub.itertuples(index=False)}


def test_g1d_fit_table_columns():
    F = fitmod()
    out = run_g1_table(F)
    assert list(out.columns) == FIT_TABLE_COLUMNS


def test_g1d_fit_table_all_rows():
    F = fitmod()
    rows = g1_rows(run_g1_table(F))
    # SPEC-QUESTION: §8.1 says each fitted ticker gets 18 rows (9 models x 2), but G1 only has
    # E and G labels and a can_emit with E and G only; which other models fit_table emits for a
    # partial frame is not stated, so only the E and G rows are checked here.
    for key, (sel, conf, q, status, reason) in G1D.items():
        assert key in rows, key
        r = rows[key]
        assert (r.status, r.reason) == (status, reason), key
        assert bool(r.is_primary) == (key[2] == SELL_EXIT)
        assert r.model_version == versions()[key[1]]
        if sel is None:
            for c in METRIC_COLUMNS:
                assert isnull(getattr(r, c)), (key, c)
            continue
        got_sel = (r.sel_n_months, r.sel_n_active, r.sel_n_switches, r.sel_ann_excess, r.sel_t,
                   r.sel_p)
        got_conf = (r.conf_n_months, r.conf_n_active, r.conf_n_switches, r.conf_ann_excess,
                    r.conf_t, r.conf_p)
        for a, b in zip(got_sel, sel):
            assert close(a, b), (key, "sel", got_sel, sel)
        for a, b in zip(got_conf, conf):
            assert close(a, b), (key, "conf", got_conf, conf)
        assert close(r.sel_q, q), (key, r.sel_q, q)


def test_g1_rows_absent_for_unlisted_tickers():
    F = fitmod()
    out = run_g1_table(F)
    assert set(out["ticker"]) == {"AAA", "BBB", "CCC", "DDD", "EEE"}
    assert not out.duplicated(["ticker", "model", "variant"]).any()


def test_g1_buy_only_family_is_separate():  # BBB·E BUY_ONLY alone: m = 1, q = p
    F = fitmod()
    rows = g1_rows(run_g1_table(F))
    fam = [k for k, r in rows.items() if k[2] == BUY_ONLY and not isnull(r.sel_q)]
    assert fam == [("BBB", "E", BUY_ONLY)]
    assert close(rows[("BBB", "E", BUY_ONLY)].sel_q, 0.1322612485566378)


def test_g1_consistency_properties():  # §13
    F = fitmod()
    out = run_g1_table(F)
    sub = out[out["model"].isin(["E", "G"])]
    for r in sub.itertuples(index=False):
        if r.status == "CONFIRMED":
            assert r.sel_q <= 0.10 and r.sel_ann_excess > 0 and r.conf_ann_excess >= 0.01
            assert r.sel_n_months >= 6 and r.sel_n_active >= 2 and r.sel_n_switches >= 2
            assert r.conf_n_months >= 3 and r.conf_n_active >= 1 and r.conf_n_switches >= 1
        if not isnull(r.sel_q):  # passing selection implies a positive effect
            if r.sel_q <= 0.10:
                assert r.sel_ann_excess > 0
        assert (r.status == "NOT_TESTED") == (r.model == "G" and r.variant == BUY_ONLY)


def test_g1_fit_table_permutation_and_determinism():
    F = fitmod()
    a = run_g1_table(F)
    b = run_g1_table(F, g1_frame().sample(frac=1.0, random_state=4).reset_index(drop=True))
    c = run_g1_table(F)
    key = ["ticker", "model", "variant"]
    norm = [x.sort_values(key).reset_index(drop=True) for x in (a, b, c)]
    pd.testing.assert_frame_equal(norm[0], norm[1])
    pd.testing.assert_frame_equal(norm[0], norm[2])


def test_g1_label_change_is_local():  # §13: only that pair's W metrics and the family q change
    F = fitmod()
    base = g1_rows(run_g1_table(F))
    fr = g1_frame()
    mask = (fr["ticker"] == "AAA") & (fr["model"] == "E") & (fr["date"] == D[6])
    assert fr.loc[mask, "label"].tolist() == ["HOLD"]
    fr.loc[mask, "label"] = "SELL"
    new = g1_rows(run_g1_table(F, fr))
    sel_cols = [c for c in METRIC_COLUMNS if c.startswith("sel_") and c != "sel_q"]
    conf_cols = [c for c in METRIC_COLUMNS if c.startswith("conf_")]
    changed = new[("AAA", "E", SELL_EXIT)]
    assert any(not close(getattr(changed, c), getattr(base[("AAA", "E", SELL_EXIT)], c))
               for c in sel_cols)
    for k in base:
        cols = conf_cols if k[:2] == ("AAA", "E") else sel_cols + conf_cols
        for c in cols:
            assert close(getattr(new[k], c), getattr(base[k], c)), (k, c)


def test_fit_table_wide_frame_equals_long_frame():
    # SPEC-QUESTION: §11 says the wide form is `ticker, date, label_A … label_F` but the loader
    # (§7.2) selects `<label columns>` (= a_signal … f_signal per LABEL_COLUMNS). Literal §11
    # reading used here: columns label_E and label_G; other models absent.
    F = fitmod()
    long = g1_frame()
    wide = long.pivot_table(index=["ticker", "date"], columns="model", values="label",
                            aggfunc="first", dropna=False).reset_index()
    wide.columns = ["ticker", "date"] + [f"label_{c}" for c in wide.columns[2:]]
    rets = long.drop_duplicates(["ticker", "date"])[["ticker", "date", "ret", "t1"]]
    wide = wide.merge(rets, on=["ticker", "date"])
    # pivot drops the None label of DDD d6 into NaN: missing either way (§3.1)
    a = g1_rows(run_g1_table(F))
    b = g1_rows(run_g1_table(F, wide))
    for k in G1D:
        for c in METRIC_COLUMNS:
            assert close(getattr(a[k], c), getattr(b[k], c)), (k, c)
        assert (a[k].status, a[k].reason) == (b[k].status, b[k].reason), k


# ---------------------------------------------------------------------------
# §8 outputs: run_fit on the fixture DB (company_model_fit, fit_runs, CSV, MD)
# ---------------------------------------------------------------------------


@pytest.fixture
def fit_env(tmp_path):
    from fit_db_fixture import build_fit_db

    return build_fit_db(tmp_path / "bt.duckdb"), tmp_path / "out"


def do_fit(env):
    """Run the fit inside the test body (so a missing module fails the test, not the fixture)."""
    from fit_db_fixture import TODAY

    F = fitmod()
    con, out_dir = env
    res = F.run_fit(con, TODAY, out_dir)
    return F, con, out_dir, res


def latest(con):
    run_id = con.execute("SELECT run_id FROM fit_runs ORDER BY started_at DESC LIMIT 1"
                         ).fetchone()[0]
    return run_id, con.execute("SELECT * FROM company_model_fit WHERE run_id=?", [run_id]).df()


def test_company_model_fit_columns_and_types(fit_env):
    F, con, _, _ = do_fit(fit_env)
    desc = con.execute("DESCRIBE company_model_fit").fetchall()
    assert [r[0] for r in desc] == FIT_COLUMNS
    types = {r[0]: r[1] for r in desc}
    for c in ("run_id", "fit_version", "fit_params_hash", "market", "ticker", "model",
              "model_version", "variant", "status", "reason"):
        assert types[c] == "VARCHAR", c
    assert types["is_primary"] == "BOOLEAN"
    for c in ("sel_n_months", "sel_n_active", "sel_n_switches", "conf_n_months",
              "conf_n_active", "conf_n_switches"):
        assert types[c] == "INTEGER", c
    for c in ("sel_ann_excess", "sel_t", "sel_p", "sel_q", "conf_ann_excess", "conf_t",
              "conf_p"):
        assert types[c] == "DOUBLE", c


def test_company_model_fit_rows(fit_env):
    F, con, _, _ = do_fit(fit_env)
    run_id, df = latest(con)
    assert len(run_id) == 12 and all(ch in "0123456789abcdef" for ch in run_id)
    assert set(df["ticker"]) == {"AAA", "BBB", "CCC"}
    assert len(df) == 3 * 18
    assert not df.duplicated(["run_id", "ticker", "model", "variant"]).any()
    for _t, g in df.groupby("ticker"):
        assert sorted(zip(g["model"], g["variant"])) == sorted(
            (k, v) for k in MODELS for v in (BUY_ONLY, SELL_EXIT))
    assert (df["fit_version"] == "fit/0.1.0").all()
    assert (df["fit_params_hash"] == FIT_HASH).all()
    assert (df["market"] == "US").all()
    lv = versions()
    assert (df["model_version"] == df["model"].map(lv)).all()
    assert (df["is_primary"] == (df["variant"] == SELL_EXIT)).all()
    assert set(df["status"]) <= {"CONFIRMED", "NOT_CONFIRMED", "INSUFFICIENT", "NOT_TESTED"}
    nt = df["status"] == "NOT_TESTED"
    assert (nt == ((df["model"] == "G") & (df["variant"] == BUY_ONLY))).all()
    assert (df.loc[nt, "reason"] == "NO_LABEL_TYPE").all()
    assert df.loc[nt, METRIC_COLUMNS].isna().all().all()
    # metrics computed for every tested pair whatever its status (§8.1)
    tested = df[~nt]
    assert tested[["sel_n_months", "conf_n_months", "sel_ann_excess", "conf_ann_excess"]
                  ].notna().all().all()
    # real windows: every fixture ticker has all 70 / 44 usable months
    assert (tested["sel_n_months"] == 70).all() and (tested["conf_n_months"] == 44).all()


def test_company_model_fit_consistency(fit_env):  # §13 consistency on real windows
    F, con, _, _ = do_fit(fit_env)
    _, df = latest(con)
    for r in df.itertuples(index=False):
        if r.status == "CONFIRMED":
            assert r.sel_q <= 0.10 and r.sel_ann_excess > 0 and r.conf_ann_excess >= 0.01
            assert r.sel_n_months >= 36 and r.sel_n_active >= 6 and r.sel_n_switches >= 2
            assert r.conf_n_months >= 24 and r.conf_n_active >= 3 and r.conf_n_switches >= 1
        in_family = (r.status != "NOT_TESTED" and not str(r.reason).startswith("SEL_FEW")
                     and r.reason != "SEL_ZERO_VAR")
        assert (not isnull(r.sel_q)) == in_family, r
    # the clairvoyant AAA·E fixture pair must be CONFIRMED (sanity of the fixture + pipeline)
    aaa = df[(df["ticker"] == "AAA") & (df["model"] == "E") & (df["variant"] == SELL_EXIT)]
    assert aaa["status"].tolist() == ["CONFIRMED"]


def test_family_q_matches_bh_over_family(fit_env):  # §6.2 one global family per variant
    F, con, _, _ = do_fit(fit_env)
    _, df = latest(con)
    for variant in (SELL_EXIT, BUY_ONLY):
        fam = df[(df["variant"] == variant) & df["sel_q"].notna()]
        exp = F.bh_qvalues(fam["sel_p"].tolist())
        assert np.allclose(fam["sel_q"].to_numpy(), exp, atol=TOL)


def test_fit_runs_row(fit_env):
    F, con, _, res = do_fit(fit_env)
    assert isinstance(res, dict)
    cols = [r[0] for r in con.execute("DESCRIBE fit_runs").fetchall()]
    assert cols == FIT_RUNS_COLUMNS
    run_id, df = latest(con)
    r = con.execute("SELECT * FROM fit_runs WHERE run_id=?", [run_id]).df().iloc[0]
    from fit_db_fixture import TODAY

    from valuation.models.frozen import FROZEN_V2, params_sha256

    assert r["market"] == "US" and r["fit_version"] == "fit/0.1.0"
    assert pd.Timestamp(r["today"]).date() == TODAY
    assert r["fit_params_hash"] == FIT_HASH
    assert r["bt_thresholds_hash"] == bte.thresholds_hash()
    assert r["frozen_params_hash"] == params_sha256(FROZEN_V2)
    assert json.loads(r["label_versions"]) == versions()
    assert r["n_tickers"] == 3
    se, bo = df[df["variant"] == SELL_EXIT], df[df["variant"] == BUY_ONLY]
    assert r["n_family_sell_exit"] == int(se["sel_q"].notna().sum())
    assert r["n_family_buy_only"] == int(bo["sel_q"].notna().sum())
    assert r["n_confirmed_sell_exit"] == int((se["status"] == "CONFIRMED").sum())
    assert r["n_confirmed_buy_only"] == int((bo["status"] == "CONFIRMED").sum())


def read_fit_csv(path):
    lines = path.read_text().splitlines()
    assert lines[0].startswith("# ")
    rows = list(csv.reader(lines[1:]))
    return lines, rows[0], rows[1:]


def test_csv_schema_order_and_format(fit_env):
    F, con, out_dir, _ = do_fit(fit_env)
    run_id, df = latest(con)
    path = out_dir / "company_model_fit.csv"
    lines, header, rows = read_fit_csv(path)
    assert header == FIT_COLUMNS
    assert len(rows) == len(df) == 54
    keys = [(r[4], r[5], r[7]) for r in rows]
    assert keys == sorted(keys)  # (ticker, model, variant), BUY_ONLY before SELL_EXIT
    assert all(r[0] == run_id for r in rows)
    by_key = {(r.ticker, r.model, r.variant): r for r in df.itertuples(index=False)}
    i_ann = FIT_COLUMNS.index("sel_ann_excess")
    i_q = FIT_COLUMNS.index("sel_q")
    for r in rows:
        d = by_key[(r[4], r[5], r[7])]
        if r[FIT_COLUMNS.index("status")] == "NOT_TESTED":
            for c in METRIC_COLUMNS:
                assert r[FIT_COLUMNS.index(c)] == "", c  # NULL as an empty field
        else:
            assert r[i_ann] == format(d.sel_ann_excess, ".10g")  # float_format "%.10g"
        if isnull(d.sel_q):
            assert r[i_q] == ""
    copy = out_dir / f"company_model_fit_{run_id}.csv"
    assert copy.exists()
    assert copy.read_text() == path.read_text()
    assert (out_dir / f"company_model_fit_{run_id}.md").exists()


def test_csv_holds_latest_run_only(fit_env):
    F, con, out_dir, _ = do_fit(fit_env)
    first, _ = latest(con)
    from fit_db_fixture import TODAY

    F.run_fit(con, TODAY, out_dir)
    second, df = latest(con)
    assert first != second
    _, _, rows = read_fit_csv(out_dir / "company_model_fit.csv")
    assert {r[0] for r in rows} == {second} and len(rows) == 54
    assert (out_dir / f"company_model_fit_{first}.csv").exists()
    assert (out_dir / f"company_model_fit_{second}.csv").exists()
    # appended per run: both runs remain in the table
    n = con.execute("SELECT count(DISTINCT run_id), count(*) FROM company_model_fit").fetchone()
    assert n == (2, 108)


def test_two_fits_identical_except_run_id(fit_env):  # §13 determinism
    F, con, out_dir, _ = do_fit(fit_env)
    first, a = latest(con)
    from fit_db_fixture import TODAY

    F.run_fit(con, TODAY, out_dir)
    _, b = latest(con)
    key = ["ticker", "model", "variant"]
    a = a.drop(columns=["run_id"]).sort_values(key).reset_index(drop=True)
    b = b.drop(columns=["run_id"]).sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_summary_md_content(fit_env):
    F, con, out_dir, _ = do_fit(fit_env)
    run_id, _ = latest(con)
    md = (out_dir / f"company_model_fit_{run_id}.md").read_text()
    assert run_id in md
    assert FIT_HASH in md or FIT_HASH[:16] in md
    assert "survivor" in md.lower()
    assert "not investment advice" in md.lower() or "not a forecast" in md.lower()
    assert "10%" in md  # BH false-discovery line (§8.3)
    for s in ("2014-01-31", "2019-10-31", "2020-01-31", "2023-08-31"):
        assert s in md, s  # window bounds and usable dates
