"""Spec tests for quarterly NI, TTM and DPS_TTM (docs/models/ttm.md, v0.1.1).

Written from the spec only (not from the implementation). EDGAR payloads are
synthetic companyfacts responses shaped like the real API.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from valuation.providers import yahoo
from valuation.providers.base import Dividend, Fact
from valuation.providers.sec_edgar import parse_quarterly_ni
from valuation.ttm import TTMResult, dps_ttm, ttm

REL = 1e-9
A = "NetIncomeLossAvailableToCommonStockholdersBasic"
N = "NetIncomeLoss"
P1 = "PreferredStockDividendsAndOtherAdjustments"
P2 = "PreferredStockDividendsIncomeStatementImpact"
P3 = "DividendsPreferredStock"


def approx(x):
    return pytest.approx(x, rel=REL)


def D(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def fact(start, end, val, filed, form=None, accn=None, include_start=True):
    s, e = D(start) if start else None, D(end)
    if form is None:
        days = (e - s).days if s else 0
        form = "10-K" if days > 300 else "10-Q"
    f = {
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "accn": accn or f"0000000000-{filed.replace('-', '')}",
        "fy": e.year,
        "fp": "FY" if form.startswith("10-K") else "Q",
    }
    if include_start and start is not None:
        f["start"] = start
    return f


def payload(concepts: dict[str, list[dict]]) -> dict:
    return {
        "cik": 1,
        "entityName": "Test Co",
        "facts": {"us-gaap": {c: {"units": {"USD": fs}} for c, fs in concepts.items()}},
    }


def rows(facts):
    """Sorted [(period_end, filed_date, value)] for comparison against approx values."""
    return sorted((f.period_end, f.filed_date, f.value) for f in facts)


def mk(pe, filed, value):
    return Fact("T", D(pe), D(filed), "ni_common_q", value, "USD", "sec_edgar", False)


# ---------------------------------------------------------------------------
# G1: Q4 from FY - 9M (AAPL)
# ---------------------------------------------------------------------------

G1_FACTS = [
    fact("2025-09-28", "2025-12-27", 42097, "2026-01-30"),
    fact("2025-09-28", "2026-03-28", 71675, "2026-05-01"),
    fact("2025-12-28", "2026-03-28", 29578, "2026-05-01"),
    fact("2025-09-28", "2026-06-27", 101464, "2026-07-31"),
    fact("2026-03-29", "2026-06-27", 29789, "2026-07-31"),
    fact("2025-09-28", "2026-09-26", 132175, "2026-11-03"),
]


def g1_quarters():
    return parse_quarterly_ni("AAPL", payload({N: [dict(f) for f in G1_FACTS]}))


def test_g1_quarters():
    q = g1_quarters()
    assert rows(q) == [
        (D("2025-12-27"), D("2026-01-30"), approx(42097)),
        (D("2026-03-28"), D("2026-05-01"), approx(29578)),
        (D("2026-06-27"), D("2026-07-31"), approx(29789)),
        (D("2026-09-26"), D("2026-11-03"), approx(132175 - 101464)),
    ]
    assert len(q) == 4


def test_g1_ttm_after_10k():
    r = ttm(g1_quarters(), D("2026-11-09"))
    assert isinstance(r, TTMResult)
    assert r.value == approx(42097 + 29578 + 29789 + (132175 - 101464))
    assert r.value == approx(132175)
    assert set(r.flags) == set()


def test_g1_ttm_before_10k_is_no_ttm():
    r = ttm(g1_quarters(), D("2026-11-02"))
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


# ---------------------------------------------------------------------------
# G2: chain 1 preferred, Q4 derivation (JPM)
# ---------------------------------------------------------------------------

G2_PERIODS = [
    ("2025-01-01", "2025-09-30", "2025-11-04"),
    ("2025-07-01", "2025-09-30", "2025-11-04"),
    ("2025-01-01", "2025-12-31", "2026-02-13"),
    ("2026-01-01", "2026-03-31", "2026-05-01"),
    ("2026-04-01", "2026-06-30", "2026-08-06"),
    ("2026-07-01", "2026-09-30", "2026-11-03"),
]
G2_A = [42991, 14043, 55681, 16148, 20752, 14936]
G2_N = [44023, 14393, 57048, 16494, 21155, 15305]


def g2_quarters():
    a = [fact(s, e, v, f) for (s, e, f), v in zip(G2_PERIODS, G2_A)]
    n = [fact(s, e, v, f) for (s, e, f), v in zip(G2_PERIODS, G2_N)]
    return parse_quarterly_ni("JPM", payload({A: a, N: n}))


def test_g2_quarters_use_chain_1():
    assert rows(g2_quarters()) == [
        (D("2025-09-30"), D("2025-11-04"), approx(14043)),
        (D("2025-12-31"), D("2026-02-13"), approx(55681 - 42991)),
        (D("2026-03-31"), D("2026-05-01"), approx(16148)),
        (D("2026-06-30"), D("2026-08-06"), approx(20752)),
        (D("2026-09-30"), D("2026-11-03"), approx(14936)),
    ]


def test_g2_q4_is_not_chain_2():
    q4 = [f for f in g2_quarters() if f.period_end == D("2025-12-31")]
    assert len(q4) == 1
    assert q4[0].value == approx(12690)
    assert q4[0].value != approx(57048 - 44023)


def test_g2_ttm_and_result_fields():
    r = ttm(g2_quarters(), D("2026-11-09"))
    assert r.value == approx(12690 + 16148 + 20752 + 14936)
    assert r.value == approx(64526)
    assert r.period_end == D("2026-09-30")
    assert r.filed_date == D("2026-11-03")
    assert tuple(r.quarter_ends) == (
        D("2026-09-30"), D("2026-06-30"), D("2026-03-31"), D("2025-12-31"))
    assert set(r.flags) == set()


def test_g2_ttm_before_q3_2026_public():
    r = ttm(g2_quarters(), D("2026-11-02"))
    assert r.value == approx(14043 + 12690 + 16148 + 20752)
    assert r.value == approx(63633)
    assert r.period_end == D("2026-06-30")
    assert r.filed_date == D("2026-08-06")
    assert tuple(r.quarter_ends) == (
        D("2026-06-30"), D("2026-03-31"), D("2025-12-31"), D("2025-09-30"))


# ---------------------------------------------------------------------------
# G3: chain 2 with preferred dividends
# ---------------------------------------------------------------------------


def test_g3_chain_2_with_preferred():
    n = [
        fact("2026-01-01", "2026-03-31", 100, "2026-05-01"),
        fact("2026-04-01", "2026-06-30", 120, "2026-08-01"),
        fact("2026-07-01", "2026-09-30", 90, "2026-11-01"),
    ]
    p = [
        fact("2026-01-01", "2026-03-31", 10, "2026-05-01"),
        fact("2026-04-01", "2026-06-30", 10, "2026-08-01"),
    ]
    q = parse_quarterly_ni("T", payload({N: n, P1: p}))
    assert rows(q) == [
        (D("2026-03-31"), D("2026-05-01"), approx(90)),
        (D("2026-06-30"), D("2026-08-01"), approx(110)),
        (D("2026-09-30"), D("2026-11-01"), approx(90)),
    ]


def test_preferred_concept_fallback_order():
    n = [
        fact("2026-01-01", "2026-03-31", 100, "2026-05-01"),
        fact("2026-04-01", "2026-06-30", 100, "2026-08-01"),
    ]
    # Q1: P1 and P2 present -> P1 (first present). Q2: only P3 -> P3.
    q = parse_quarterly_ni("T", payload({
        N: n,
        P1: [fact("2026-01-01", "2026-03-31", 10, "2026-05-01")],
        P2: [fact("2026-01-01", "2026-03-31", 30, "2026-05-01")],
        P3: [fact("2026-04-01", "2026-06-30", 7, "2026-08-01")],
    }))
    assert rows(q) == [
        (D("2026-03-31"), D("2026-05-01"), approx(90)),
        (D("2026-06-30"), D("2026-08-01"), approx(93)),
    ]


# ---------------------------------------------------------------------------
# G4: FY minus three quarters
# ---------------------------------------------------------------------------


def test_g4_fy_minus_three_quarters():
    n = [
        fact("2025-01-01", "2025-03-31", 10, "2025-05-01"),
        fact("2025-04-01", "2025-06-30", 20, "2025-08-01"),
        fact("2025-07-01", "2025-09-30", 30, "2025-11-01"),
        fact("2025-01-01", "2025-12-31", 100, "2026-02-15"),
    ]
    q = parse_quarterly_ni("T", payload({N: n}))
    assert rows(q) == [
        (D("2025-03-31"), D("2025-05-01"), approx(10)),
        (D("2025-06-30"), D("2025-08-01"), approx(20)),
        (D("2025-09-30"), D("2025-11-01"), approx(30)),
        (D("2025-12-31"), D("2026-02-15"), approx(100 - 60)),
    ]


# ---------------------------------------------------------------------------
# G5: restatement
# ---------------------------------------------------------------------------


def g5_quarters():
    n = [
        fact("2025-04-01", "2025-06-30", 10, "2025-08-01"),
        fact("2025-07-01", "2025-09-30", 20, "2025-11-01"),
        fact("2025-10-01", "2025-12-31", 30, "2026-02-15"),
        fact("2026-01-01", "2026-03-31", 50, "2026-05-01"),
        fact("2026-01-01", "2026-03-31", 45, "2026-08-01"),
        fact("2026-01-01", "2026-03-31", 45, "2026-11-01"),  # repeat: no new row
    ]
    return parse_quarterly_ni("T", payload({N: n}))


def test_g5_restatement_rows():
    q1 = [f for f in g5_quarters() if f.period_end == D("2026-03-31")]
    assert rows(q1) == [
        (D("2026-03-31"), D("2026-05-01"), approx(50)),
        (D("2026-03-31"), D("2026-08-01"), approx(45)),
    ]
    assert len(q1) == 2


def test_g5_ttm_point_in_time():
    q = g5_quarters()
    r = ttm(q, D("2026-06-01"))
    assert r.value == approx(10 + 20 + 30 + 50)
    assert r.filed_date == D("2026-05-01")
    r = ttm(q, D("2026-09-01"))
    assert r.value == approx(10 + 20 + 30 + 45)
    assert r.filed_date == D("2026-08-01")


# ---------------------------------------------------------------------------
# G6: gap -> NO_TTM
# ---------------------------------------------------------------------------


def test_g6_gap_no_ttm():
    q = [
        mk("2025-03-31", "2025-05-01", 1),
        mk("2025-06-30", "2025-08-01", 2),
        mk("2025-12-31", "2026-02-15", 3),
        mk("2026-03-31", "2026-05-01", 4),
    ]
    r = ttm(q, D("2026-11-09"))
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


# ---------------------------------------------------------------------------
# G7: DPS_TTM
# ---------------------------------------------------------------------------

G7 = [
    ("2025-10-27", 1.54),
    ("2026-01-26", 1.64),
    ("2026-04-23", 1.64),
    ("2026-07-27", 1.76),
    ("2026-10-27", 1.76),
    ("2026-11-20", 1.80),
]


def divs(items):
    return [Dividend(ticker="TD.TO", ex_date=D(x), amount=a, currency="CAD",
                     source="yahoo_chart") for x, a in items]


def test_g7_dps_ttm():
    assert dps_ttm(divs(G7), D("2026-11-09")) == approx(1.64 + 1.64 + 1.76 + 1.76)
    assert dps_ttm(divs(G7), D("2026-10-26")) == approx(1.54 + 1.64 + 1.64 + 1.76)


def test_dps_ttm_window_boundaries():
    as_of = D("2026-11-09")
    edge_out = as_of - timedelta(days=365)
    items = [(edge_out.isoformat(), 1.0), (as_of.isoformat(), 2.0),
             ((as_of + timedelta(days=1)).isoformat(), 4.0)]
    assert dps_ttm(divs(items), as_of) == approx(2.0)


def test_dps_ttm_none_is_zero():
    assert dps_ttm([], D("2026-11-09")) == 0.0
    assert dps_ttm(divs([("2020-01-01", 1.0)]), D("2026-11-09")) == 0.0


# ---------------------------------------------------------------------------
# EDGAR parser: filtering and emitted fields
# ---------------------------------------------------------------------------


def test_edgar_emitted_fact_fields():
    for f in g2_quarters():
        assert isinstance(f, Fact)
        assert f.ticker == "JPM"
        assert f.item == "ni_common_q"
        assert f.source == "sec_edgar"
        assert f.unit == "USD"
        assert f.filed_estimated is False


def test_edgar_ignores_facts_without_start():
    n = [
        fact("2026-01-01", "2026-03-31", 100, "2026-05-01"),
        fact("2026-04-01", "2026-06-30", 999, "2026-08-01", include_start=False),
    ]
    q = parse_quarterly_ni("T", payload({N: n}))
    assert rows(q) == [(D("2026-03-31"), D("2026-05-01"), approx(100))]


def test_edgar_ignores_excluded_forms():
    n = [
        fact("2026-01-01", "2026-03-31", 100, "2026-05-01"),
        fact("2026-04-01", "2026-06-30", 999, "2026-08-01", form="8-K"),
        fact("2026-01-01", "2026-03-31", 777, "2026-09-01", form="8-K"),  # no restatement
    ]
    q = parse_quarterly_ni("T", payload({N: n}))
    assert rows(q) == [(D("2026-03-31"), D("2026-05-01"), approx(100))]


@pytest.mark.parametrize("form", ["10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"])
def test_edgar_accepted_forms(form):
    n = [fact("2026-01-01", "2026-03-31", 100, "2026-05-01", form=form)]
    q = parse_quarterly_ni("T", payload({N: n}))
    assert rows(q) == [(D("2026-03-31"), D("2026-05-01"), approx(100))]


def test_edgar_same_filed_tie_uses_larger_accn():
    n = [
        fact("2026-01-01", "2026-03-31", 100, "2026-05-01", accn="0000000000-26-000001"),
        fact("2026-01-01", "2026-03-31", 105, "2026-05-01", accn="0000000000-26-000002"),
    ]
    q = parse_quarterly_ni("T", payload({N: n}))
    assert rows(q) == [(D("2026-03-31"), D("2026-05-01"), approx(105))]


def test_edgar_empty_payload():
    assert parse_quarterly_ni("T", payload({})) == []


# ---------------------------------------------------------------------------
# TTM selection
# ---------------------------------------------------------------------------


def four_quarters():
    return [
        mk("2024-06-30", "2024-08-01", 1),
        mk("2024-09-30", "2024-11-01", 2),
        mk("2024-12-31", "2025-02-15", 3),
        mk("2025-03-31", "2025-05-01", 4),
    ]


def test_ttm_stale_boundary():
    # 2025-03-31 + 456 days = 2026-06-30.
    r = ttm(four_quarters(), D("2026-06-30"))
    assert r.value == approx(10)
    assert set(r.flags) == set()
    r = ttm(four_quarters(), D("2026-07-01"))
    assert r.value == approx(10)
    assert set(r.flags) == {"STALE_TTM"}


def test_ttm_fewer_than_four_quarters():
    r = ttm(four_quarters()[1:], D("2026-01-01"))
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


def test_ttm_empty():
    r = ttm([], D("2026-01-01"))
    assert r.value is None
    assert r.period_end is None
    assert r.filed_date is None
    assert tuple(r.quarter_ends) == ()
    assert set(r.flags) == {"NO_TTM"}


def test_ttm_ignores_unfiled_and_uses_latest_known_row():
    q = four_quarters() + [
        mk("2025-03-31", "2025-08-01", 40),  # restatement of the latest quarter
        mk("2025-06-30", "2025-08-15", 1000),  # not yet public at as_of
    ]
    r = ttm(q, D("2025-08-10"))
    assert r.value == approx(1 + 2 + 3 + 40)
    assert r.period_end == D("2025-03-31")
    assert r.filed_date == D("2025-08-01")
    assert tuple(r.quarter_ends) == (
        D("2025-03-31"), D("2024-12-31"), D("2024-09-30"), D("2024-06-30"))


def test_ttm_uses_only_four_latest_quarters():
    q = [mk("2024-03-31", "2024-05-01", 100)] + four_quarters()
    r = ttm(q, D("2025-06-01"))
    assert r.value == approx(10)
    assert r.filed_date == D("2025-05-01")


def test_ttm_spacing_over_100_days_is_gap():
    q = [
        mk("2024-06-30", "2024-08-01", 1),
        mk("2024-09-30", "2024-11-01", 2),
        mk("2024-12-31", "2025-02-15", 3),
        mk("2025-04-11", "2025-05-15", 4),  # 101 days after 2024-12-31
    ]
    r = ttm(q, D("2025-06-01"))
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


# ---------------------------------------------------------------------------
# Yahoo: quarterly NI from fundamentals-timeseries
# ---------------------------------------------------------------------------


def ts_point(as_of, value, period, ccy="CAD"):
    return {"asOfDate": as_of, "periodType": period, "currencyCode": ccy,
            "reportedValue": {"raw": value}}


YAHOO_TS = {"timeseries": {"result": [
    {
        "meta": {"type": ["quarterlyNetIncomeCommonStockholders"]},
        "timestamp": [1761868800, 1769817600, 1777507200, 1785456000],
        "quarterlyNetIncomeCommonStockholders": [
            ts_point("2025-10-31", 5000000000.0, "3M"),
            ts_point("2026-01-31", 5100000000.0, "3M"),
            ts_point("2026-04-30", 5200000000.0, "3M"),
            ts_point("2026-07-31", 5879000000.0, "3M"),
        ],
    },
    {
        "meta": {"type": ["annualNetIncomeCommonStockholders"]},
        "timestamp": [1761868800],
        "annualNetIncomeCommonStockholders": [
            ts_point("2025-10-31", 19868000000.0, "12M"),
        ],
    },
], "error": None}}


def test_yahoo_quarterly_ni():
    facts = yahoo.parse_quarterly_ni("RY.TO", YAHOO_TS)
    assert rows(facts) == [
        (D("2025-10-31"), D("2025-10-31") + timedelta(days=90), approx(5000000000.0)),
        (D("2026-01-31"), D("2026-01-31") + timedelta(days=45), approx(5100000000.0)),
        (D("2026-04-30"), D("2026-04-30") + timedelta(days=45), approx(5200000000.0)),
        (D("2026-07-31"), D("2026-07-31") + timedelta(days=45), approx(5879000000.0)),
    ]
    assert len(facts) == 4
    for f in facts:
        assert isinstance(f, Fact)
        assert f.ticker == "RY.TO"
        assert f.item == "ni_common_q"
        assert f.unit == "CAD"
        assert f.filed_estimated is True


def test_yahoo_quarterly_ni_explicit_dates():
    by_pe = {f.period_end: f.filed_date for f in yahoo.parse_quarterly_ni("RY.TO", YAHOO_TS)}
    assert by_pe[D("2025-10-31")] == D("2026-01-29")
    assert by_pe[D("2026-07-31")] == D("2026-09-14")


def test_yahoo_annual_points_not_emitted_as_quarters():
    facts = yahoo.parse_quarterly_ni("RY.TO", YAHOO_TS)
    assert all(f.value != approx(19868000000.0) for f in facts)


def test_yahoo_ttm_from_parsed_quarters():
    facts = yahoo.parse_quarterly_ni("RY.TO", YAHOO_TS)
    r = ttm(facts, D("2026-11-09"))
    assert r.value == approx(5000000000.0 + 5100000000.0 + 5200000000.0 + 5879000000.0)
    assert r.period_end == D("2026-07-31")
    assert r.filed_date == D("2026-09-14")
    r = ttm(facts, D("2026-09-13"))  # Q3 not yet (estimated) public
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


def yahoo_ts(quarters, annuals, fetched=None, ccy="CAD"):
    """Fundamentals-timeseries payload; quarters/annuals: [(asOfDate, value)]."""
    p = {"timeseries": {"result": [
        {
            "meta": {"type": ["quarterlyNetIncomeCommonStockholders"]},
            "quarterlyNetIncomeCommonStockholders": [
                ts_point(x, v, "3M", ccy) for x, v in quarters],
        },
        {
            "meta": {"type": ["annualNetIncomeCommonStockholders"]},
            "annualNetIncomeCommonStockholders": [
                ts_point(x, v, "12M", ccy) for x, v in annuals],
        },
    ], "error": None}}
    if fetched is not None:
        p["_fetched"] = fetched
    return p


def filed_by_pe(facts):
    return {f.period_end: f.filed_date for f in facts}


def test_yahoo_source_and_skips_points_without_raw():
    q = YAHOO_TS["timeseries"]["result"][0]["quarterlyNetIncomeCommonStockholders"]
    payload = yahoo_ts([], [("2025-10-31", 19868000000.0)])
    payload["timeseries"]["result"][0]["quarterlyNetIncomeCommonStockholders"] = [
        dict(q[0]),
        {"asOfDate": "2026-01-31", "periodType": "3M", "currencyCode": "CAD",
         "reportedValue": {}},
        {"asOfDate": "2026-04-30", "periodType": "3M", "currencyCode": "CAD"},
    ]
    facts = yahoo.parse_quarterly_ni("RY.TO", payload)
    assert [f.period_end for f in facts] == [D("2025-10-31")]
    assert all(f.source == "yahoo" for f in yahoo.parse_quarterly_ni("RY.TO", YAHOO_TS))


# --- v0.1.1: filed_date = min(estimate, _fetched) ---------------------------

YQ = [("2026-01-31", 5100000000.0), ("2026-04-30", 5200000000.0),
      ("2026-07-31", 5879000000.0)]
YA = [("2025-10-31", 19868000000.0)]


def test_yahoo_fetched_before_estimate_caps_filed_date():
    # 2026-07-31 + 45 = 2026-09-14 > 2026-08-20 -> capped; the earlier estimates are
    # before 2026-08-20 and stay.
    facts = yahoo.parse_quarterly_ni("RY.TO", yahoo_ts(YQ, YA, fetched="2026-08-20"))
    assert filed_by_pe(facts) == {
        D("2026-01-31"): D("2026-03-17"),
        D("2026-04-30"): D("2026-06-14"),
        D("2026-07-31"): D("2026-08-20"),
    }
    assert all(f.filed_estimated is True for f in facts)


def test_yahoo_fetched_after_estimate_leaves_it():
    facts = yahoo.parse_quarterly_ni("RY.TO", yahoo_ts(YQ, YA, fetched="2026-11-09"))
    assert filed_by_pe(facts) == {
        D("2026-01-31"): D("2026-01-31") + timedelta(days=45),
        D("2026-04-30"): D("2026-04-30") + timedelta(days=45),
        D("2026-07-31"): D("2026-07-31") + timedelta(days=45),
    }


def test_yahoo_fetched_equal_to_estimate():
    facts = yahoo.parse_quarterly_ni("RY.TO", yahoo_ts(YQ, YA, fetched="2026-09-14"))
    assert filed_by_pe(facts)[D("2026-07-31")] == D("2026-09-14")


def test_yahoo_fetched_caps_fiscal_q4_estimate():
    # Q4 = annual asOfDate: estimate 2025-10-31 + 90 = 2026-01-29 > 2025-12-15.
    payload = yahoo_ts([("2025-10-31", 5000000000.0)], YA, fetched="2025-12-15")
    facts = yahoo.parse_quarterly_ni("RY.TO", payload)
    assert filed_by_pe(facts) == {D("2025-10-31"): D("2025-12-15")}
    assert facts[0].filed_estimated is True
    payload = yahoo_ts([("2025-10-31", 5000000000.0)], YA, fetched="2026-03-01")
    assert filed_by_pe(yahoo.parse_quarterly_ni("RY.TO", payload)) == {
        D("2025-10-31"): D("2026-01-29")}


def test_yahoo_without_fetched_uses_plain_estimate():
    payload = yahoo_ts(YQ, YA)
    assert "_fetched" not in payload
    assert filed_by_pe(yahoo.parse_quarterly_ni("RY.TO", payload)) == {
        D("2026-01-31"): D("2026-03-17"),
        D("2026-04-30"): D("2026-06-14"),
        D("2026-07-31"): D("2026-09-14"),
    }


def test_yahoo_ttm_respects_capped_filed_date():
    quarters = [("2025-07-31", 4900000000.0), ("2025-10-31", 5000000000.0)] + YQ
    facts = yahoo.parse_quarterly_ni("RY.TO", yahoo_ts(quarters, YA, fetched="2026-08-20"))
    r = ttm(facts, D("2026-08-19"))  # before _fetched: the capped Q3 2026 is not visible
    assert r.period_end == D("2026-04-30")
    assert r.value == approx(4900000000.0 + 5000000000.0 + 5100000000.0 + 5200000000.0)
    assert r.filed_date == D("2026-06-14")
    r = ttm(facts, D("2026-08-20"))  # on _fetched it is
    assert r.period_end == D("2026-07-31")
    assert r.value == approx(5000000000.0 + 5100000000.0 + 5200000000.0 + 5879000000.0)
    assert r.filed_date == D("2026-08-20")


# ---------------------------------------------------------------------------
# Yahoo: v8 chart -> bars and dividends
# ---------------------------------------------------------------------------


def ts(y, m, d, hh=14, mm=30):
    return int(datetime(y, m, d, hh, mm, tzinfo=UTC).timestamp())


CHART = {"chart": {"result": [{
    "meta": {"currency": "CAD", "symbol": "TD.TO", "exchangeName": "TOR",
             "instrumentType": "EQUITY", "timezone": "EST",
             "exchangeTimezoneName": "America/Toronto"},
    "timestamp": [ts(2026, 10, 26), ts(2026, 10, 27), ts(2026, 10, 28), ts(2026, 10, 29)],
    "indicators": {
        "quote": [{
            "open": [100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 101.5, None, 103.5],
            "volume": [1000000, 1100000, 1200000, 1300000],
        }],
        "adjclose": [{"adjclose": [99.5, 100.5, None, 103.5]}],
    },
    "events": {"dividends": {
        str(ts(2026, 10, 27)): {"amount": 1.76, "date": ts(2026, 10, 27)},
        str(ts(2026, 7, 27)): {"amount": 1.76, "date": ts(2026, 7, 27)},
    }},
}], "error": None}}


def test_parse_chart_bars():
    bars, _ = yahoo.parse_chart("TD.TO", CHART)
    got = sorted((b.date, b.close, b.adj_close, b.volume) for b in bars)
    assert got == [
        (D("2026-10-26"), approx(100.5), approx(99.5), approx(1000000)),
        (D("2026-10-27"), approx(101.5), approx(100.5), approx(1100000)),
        (D("2026-10-29"), approx(103.5), approx(103.5), approx(1300000)),
    ]
    for b in bars:
        assert b.ticker == "TD.TO"
        assert b.source == "yahoo_chart"


def test_parse_chart_uses_utc_date():
    payload = {"chart": {"result": [{
        "meta": {"currency": "USD"},
        # 2026-10-27 23:30 UTC is still Oct 27 in UTC (Oct 28 in e.g. Asia).
        "timestamp": [ts(2026, 10, 27, 23, 30)],
        "indicators": {"quote": [{"close": [10.0], "volume": [5]}],
                       "adjclose": [{"adjclose": [10.0]}]},
    }], "error": None}}
    bars, dividends = yahoo.parse_chart("X", payload)
    assert [b.date for b in bars] == [D("2026-10-27")]
    assert list(dividends) == []


def test_parse_chart_dividends():
    _, dividends = yahoo.parse_chart("TD.TO", CHART)
    got = sorted((v.ex_date, v.amount) for v in dividends)
    assert got == [(D("2026-07-27"), approx(1.76)), (D("2026-10-27"), approx(1.76))]
    for v in dividends:
        assert isinstance(v, Dividend)
        assert v.ticker == "TD.TO"
        assert v.currency == "CAD"
        assert isinstance(v.source, str) and v.source


def test_parse_chart_dividends_feed_dps_ttm():
    _, dividends = yahoo.parse_chart("TD.TO", CHART)
    assert dps_ttm(list(dividends), D("2026-11-09")) == approx(1.76 + 1.76)
    assert dps_ttm(list(dividends), D("2026-10-26")) == approx(1.76)
