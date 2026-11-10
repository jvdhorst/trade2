"""Spec tests for data batch 2 (docs/models/ttm.md v0.2.0 §5.1-§5.3, goldens G8-G14).

Written from the spec only (not from the implementation). EDGAR payloads are synthetic
companyfacts responses; Yahoo payloads are synthetic fundamentals-timeseries responses.
Modules are imported whole and new functions looked up per test, so a missing function
fails only the tests that need it.
"""

from __future__ import annotations

from datetime import date

import pytest

from valuation import engine, ttm
from valuation.providers import base, sec_edgar, yahoo
from valuation.providers.base import Fact
from valuation.store import db

REL = 1e-9
CFO_Q, CAPEX_Q, REVENUE_Q = "cfo_q", "capex_q", "revenue_q"
GP_Q, EBIT_Q = "gross_profit_q", "ebit_q"
TOTAL_ASSETS, TOTAL_DEBT, CASH = "total_assets", "total_debt", "cash"

CFO_CHAIN = ["NetCashProvidedByUsedInOperatingActivities",
             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX_CHAIN = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
REVENUE_CHAIN = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                 "RevenuesNetOfInterestExpense"]
GP_CHAIN = ["GrossProfit"]
EBIT_CHAIN = ["OperatingIncomeLoss"]
COST_CONCEPTS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
CHAINS = {CFO_Q: CFO_CHAIN, CAPEX_Q: CAPEX_CHAIN, REVENUE_Q: REVENUE_CHAIN, GP_Q: GP_CHAIN,
          EBIT_Q: EBIT_CHAIN}


def approx(x):
    return pytest.approx(x, rel=REL)


def D(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def fact(start, end, val, filed, form=None, accn=None):
    """A duration fact (companyfacts shape)."""
    if form is None:
        form = "10-K" if (D(end) - D(start)).days > 300 else "10-Q"
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form,
            "accn": accn or f"0000000000-{filed.replace('-', '')}", "fy": D(end).year,
            "fp": "FY" if form.startswith("10-K") else "Q"}


def payload(concepts: dict[str, list[dict]]) -> dict:
    return {"cik": 1, "entityName": "Test Co",
            "facts": {"us-gaap": {c: {"units": {"USD": fs}} for c, fs in concepts.items()}}}


def flows(concepts: dict[str, list[dict]], ticker="T"):
    return sec_edgar.parse_quarterly_flows(ticker, payload(concepts))


def rows(facts, item):
    """Sorted [(period_end, filed_date, value)] of one item."""
    return sorted((f.period_end, f.filed_date, f.value) for f in facts if f.item == item)


def values(facts, item):
    return {f.period_end: f.value for f in facts if f.item == item}


Q3 = ("2026-07-01", "2026-09-30")  # a direct fiscal quarter used by the one-quarter tests
Q3_FILED = "2026-11-01"


def one_q(val):
    return [fact(*Q3, val, Q3_FILED)]


# ---------------------------------------------------------------------------
# Item-name constants
# ---------------------------------------------------------------------------


def test_flow_item_constants():
    assert (base.CFO_Q, base.CAPEX_Q, base.REVENUE_Q, base.GROSS_PROFIT_Q, base.EBIT_Q) == (
        CFO_Q, CAPEX_Q, REVENUE_Q, GP_Q, EBIT_Q)
    assert set(base.FLOW_ITEMS) == {base.NI_COMMON_Q, CFO_Q, CAPEX_Q, REVENUE_Q, GP_Q, EBIT_Q}
    assert len(base.FLOW_ITEMS) == 6


def test_balance_sheet_item_constants_in_items():
    assert (base.TOTAL_ASSETS, base.TOTAL_DEBT, base.CASH) == (TOTAL_ASSETS, TOTAL_DEBT, CASH)
    for it in (TOTAL_ASSETS, TOTAL_DEBT, CASH):
        assert it in base.ITEMS


# ---------------------------------------------------------------------------
# G8: CFO from year-to-date facts only
# ---------------------------------------------------------------------------

G8 = [
    fact("2025-01-01", "2025-09-30", 280, "2025-11-01"),
    fact("2025-01-01", "2025-12-31", 400, "2026-02-15"),
    fact("2026-01-01", "2026-03-31", 100, "2026-05-01"),
    fact("2026-01-01", "2026-06-30", 250, "2026-08-01"),
    fact("2026-01-01", "2026-09-30", 330, "2026-11-01"),
]


def g8():
    return flows({CFO_CHAIN[0]: [dict(f) for f in G8]})


def test_g8_cfo_quarters_and_filed_dates():
    assert rows(g8(), CFO_Q) == [
        (D("2025-12-31"), D("2026-02-15"), approx(120)),  # derived Q4 gets the 10-K date
        (D("2026-03-31"), D("2026-05-01"), approx(100)),
        (D("2026-06-30"), D("2026-08-01"), approx(150)),
        (D("2026-09-30"), D("2026-11-01"), approx(80)),
    ]


def test_g8_nine_months_alone_yields_no_quarter():
    q = flows({CFO_CHAIN[0]: [dict(G8[0])]})
    assert rows(q, CFO_Q) == []


def test_g8_ttm():
    r = ttm.ttm([f for f in g8() if f.item == CFO_Q], D("2026-11-09"))
    assert r.value == approx(450)
    assert r.period_end == D("2026-09-30")
    assert r.filed_date == D("2026-11-01")
    assert set(r.flags) == set()


def test_g8_ttm_three_quarters_known_is_no_ttm():
    r = ttm.ttm([f for f in g8() if f.item == CFO_Q], D("2026-10-01"))
    assert r.value is None
    assert set(r.flags) == {"NO_TTM"}


def test_edgar_flow_fact_fields():
    for f in g8():
        assert isinstance(f, Fact)
        assert f.ticker == "T"
        assert f.item == CFO_Q
        assert f.unit == "USD"
        assert f.source == "sec_edgar"
        assert f.filed_estimated is False


def test_edgar_flows_exclude_ni_common_q():
    q = flows({"NetIncomeLoss": one_q(50), CFO_CHAIN[0]: one_q(70)})
    assert {f.item for f in q} == {CFO_Q}


def test_missing_flow_item_absent_not_zero():
    q = flows({CFO_CHAIN[0]: one_q(70)})
    items = {f.item for f in q}
    for it in (CAPEX_Q, REVENUE_Q, GP_Q, EBIT_Q):
        assert it not in items


def test_edgar_flows_empty_payload():
    assert flows({}) == []


# ---------------------------------------------------------------------------
# Emission rule (per item): restatement adds a row, a repeat adds none
# ---------------------------------------------------------------------------


def test_restated_flow_gets_second_row_and_repeat_adds_none():
    facts = [
        fact("2026-01-01", "2026-03-31", 50, "2026-05-01"),
        fact("2026-01-01", "2026-03-31", 45, "2026-08-01"),
        fact("2026-01-01", "2026-03-31", 45, "2026-11-01"),
    ]
    q = flows({EBIT_CHAIN[0]: facts})
    assert rows(q, EBIT_Q) == [
        (D("2026-03-31"), D("2026-05-01"), approx(50)),
        (D("2026-03-31"), D("2026-08-01"), approx(45)),
    ]


def test_emission_is_per_item():
    # The same value in two items is two rows, one per item.
    q = flows({EBIT_CHAIN[0]: one_q(40), REVENUE_CHAIN[0]: one_q(40)})
    assert rows(q, EBIT_Q) == [(D("2026-09-30"), D(Q3_FILED), approx(40))]
    assert rows(q, REVENUE_Q) == [(D("2026-09-30"), D(Q3_FILED), approx(40))]


# ---------------------------------------------------------------------------
# Chain priority and fallback, one test per chain member
# ---------------------------------------------------------------------------

MEMBERS = [(item, i, c) for item, chain in CHAINS.items() for i, c in enumerate(chain)]
PAIRS = [(item, chain[i], chain[i + 1]) for item, chain in CHAINS.items()
         for i in range(len(chain) - 1)]


@pytest.mark.parametrize(("item", "idx", "concept"), MEMBERS,
                         ids=[f"{m[0]}-{m[2]}" for m in MEMBERS])
def test_chain_member_alone_fills_item(item, idx, concept):
    q = flows({concept: one_q(123)})
    assert rows(q, item) == [(D("2026-09-30"), D(Q3_FILED), approx(123))]


@pytest.mark.parametrize(("item", "better", "worse"), PAIRS,
                         ids=[f"{p[0]}-{p[1]}" for p in PAIRS])
def test_chain_priority(item, better, worse):
    q = flows({better: one_q(100), worse: one_q(90)})
    assert rows(q, item) == [(D("2026-09-30"), D(Q3_FILED), approx(100))]


@pytest.mark.parametrize("item", [CFO_Q, CAPEX_Q, REVENUE_Q])
def test_per_quarter_fallback(item):
    c1, c2 = CHAINS[item][0], CHAINS[item][1]
    q = flows({c1: [fact("2026-04-01", "2026-06-30", 10, "2026-08-01")],
               c2: [fact("2026-04-01", "2026-06-30", 11, "2026-08-01"), fact(*Q3, 20, Q3_FILED)]})
    assert values(q, item) == {D("2026-06-30"): approx(10), D("2026-09-30"): approx(20)}


def test_chains_never_mixed_inside_a_derivation():
    # 9M only in chain 1, FY only in chain 2: neither chain alone can derive Q4.
    q = flows({CFO_CHAIN[0]: [fact("2025-01-01", "2025-09-30", 280, "2025-11-01")],
               CFO_CHAIN[1]: [fact("2025-01-01", "2025-12-31", 400, "2026-02-15")]})
    assert rows(q, CFO_Q) == []


def test_cfo_chain_2_from_year_to_date():
    q = flows({CFO_CHAIN[1]: [dict(f) for f in G8]})
    assert values(q, CFO_Q) == {D("2025-12-31"): approx(120), D("2026-03-31"): approx(100),
                                D("2026-06-30"): approx(150), D("2026-09-30"): approx(80)}


# ---------------------------------------------------------------------------
# G9: revenue chain fallback and priority
# ---------------------------------------------------------------------------


def g9():
    # Facts of the same period filed on the same day (see note in the test).
    return flows({
        "Revenues": [fact("2025-07-01", "2025-09-30", 500, "2025-11-01"),
                     fact("2025-10-01", "2025-12-31", 510, "2026-02-15")],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            fact("2025-07-01", "2025-09-30", 480, "2025-11-01"),
            fact("2026-01-01", "2026-03-31", 520, "2026-05-01"),
            fact("2026-04-01", "2026-06-30", 530, "2026-08-01")],
    })


def test_g9_revenue_quarters():
    # SPEC-AMBIGUITY: same-day filings for 2025-09-30. If chain 2 were filed first, the emission
    # rule would give a first row 480 and a second 500; the golden's "all filed by 2026-08-01"
    # does not fix this.
    assert rows(g9(), REVENUE_Q) == [
        (D("2025-09-30"), D("2025-11-01"), approx(500)),
        (D("2025-12-31"), D("2026-02-15"), approx(510)),
        (D("2026-03-31"), D("2026-05-01"), approx(520)),
        (D("2026-06-30"), D("2026-08-01"), approx(530)),
    ]


def test_g9_ttm():
    r = ttm.ttm([f for f in g9() if f.item == REVENUE_Q], D("2026-08-15"))
    assert r.value == approx(2060)
    assert r.period_end == D("2026-06-30")


# ---------------------------------------------------------------------------
# G10: gross profit chain 2 (REV - COST)
# ---------------------------------------------------------------------------


def test_g10_gross_profit():
    q = flows({
        "Revenues": [fact("2026-01-01", "2026-03-31", 900, "2026-05-01"),
                     fact("2026-04-01", "2026-06-30", 1000, "2026-08-01"),
                     fact("2026-07-01", "2026-09-30", 1100, "2026-11-01")],
        "CostOfRevenue": [fact("2026-04-01", "2026-06-30", 600, "2026-08-01"),
                          fact("2026-07-01", "2026-09-30", 700, "2026-11-01")],
        "GrossProfit": [fact("2026-07-01", "2026-09-30", 450, "2026-11-01")],
    })
    assert rows(q, GP_Q) == [
        (D("2026-06-30"), D("2026-08-01"), approx(400)),
        (D("2026-09-30"), D("2026-11-01"), approx(450)),  # chain 1, not 1100 - 700
    ]  # 2026-03-31: revenue but no cost and no GrossProfit -> no row


def test_g10_cost_of_goods_and_services_sold():
    q = flows({"Revenues": [fact("2026-04-01", "2026-06-30", 1000, "2026-08-01")],
               "CostOfGoodsAndServicesSold": [fact("2026-04-01", "2026-06-30", 520, "2026-08-01")]})
    assert values(q, GP_Q) == {D("2026-06-30"): approx(480)}


@pytest.mark.parametrize("cost", COST_CONCEPTS)
def test_gross_profit_each_cost_concept(cost):
    q = flows({"Revenues": one_q(1000), cost: one_q(650)})
    assert values(q, GP_Q) == {D("2026-09-30"): approx(350)}


@pytest.mark.parametrize(("better", "worse"), [(COST_CONCEPTS[0], COST_CONCEPTS[1]),
                                               (COST_CONCEPTS[1], COST_CONCEPTS[2])])
def test_gross_profit_cost_priority(better, worse):
    q = flows({"Revenues": one_q(1000), better: one_q(600), worse: one_q(700)})
    assert values(q, GP_Q) == {D("2026-09-30"): approx(400)}


@pytest.mark.parametrize("rev", REVENUE_CHAIN)
def test_gross_profit_uses_any_revenue_concept(rev):
    q = flows({rev: one_q(1000), "CostOfRevenue": one_q(600)})
    assert values(q, GP_Q) == {D("2026-09-30"): approx(400)}


def test_gross_profit_rev_is_first_revenue_concept_present():
    q = flows({"Revenues": one_q(1000), REVENUE_CHAIN[1]: one_q(990), "CostOfRevenue": one_q(600)})
    assert values(q, GP_Q) == {D("2026-09-30"): approx(400)}


def test_gross_profit_no_cost_no_row():
    q = flows({"Revenues": one_q(1000)})
    assert rows(q, GP_Q) == []
    assert values(q, REVENUE_Q) == {D("2026-09-30"): approx(1000)}


def test_gross_profit_cost_without_revenue_no_row():
    q = flows({"CostOfRevenue": one_q(600)})
    assert rows(q, GP_Q) == []


# ---------------------------------------------------------------------------
# G11: capex sign (EDGAR)
# ---------------------------------------------------------------------------


def test_g11_edgar_capex_positive_as_reported():
    q = flows({CAPEX_CHAIN[0]: one_q(30)})
    assert values(q, CAPEX_Q) == {D("2026-09-30"): approx(30)}


def test_g11_edgar_capex_chain_2():
    q = flows({CAPEX_CHAIN[1]: one_q(12)})
    assert values(q, CAPEX_Q) == {D("2026-09-30"): approx(12)}


# ---------------------------------------------------------------------------
# EDGAR balance sheet (parse_companyfacts): G12 total debt, G13 cash and assets
# ---------------------------------------------------------------------------

BS_END = "2026-09-30"


def inst(val, end=BS_END, accn="A1", filed="2026-11-01", form="10-Q"):
    return {"val": val, "end": end, "accn": accn, "filed": filed, "form": form}


def bs_payload(concepts: dict[str, float], extra: dict[str, list[dict]] | None = None) -> dict:
    """One accession A1 with StockholdersEquity at BS_END (sets the balance-sheet date)."""
    us = {"StockholdersEquity": {"units": {"USD": [inst(1000)]}}}
    for c, v in concepts.items():
        us[c] = {"units": {"USD": [inst(v)]}}
    for c, fs in (extra or {}).items():
        us.setdefault(c, {"units": {"USD": []}})["units"]["USD"].extend(fs)
    return {"cik": 1, "entityName": "Test Co", "facts": {
        "us-gaap": us,
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            inst(100, end="2026-10-20")]}}},
    }}


def bs_items(concepts, extra=None):
    facts = sec_edgar.parse_companyfacts("T", bs_payload(concepts, extra))
    return {f.item: f.value for f in facts if f.period_end == D(BS_END)}


G12 = [
    ({"DebtLongtermAndShorttermCombinedAmount": 900, "LongTermDebt": 800}, 900, "D1"),
    ({"LongTermDebtNoncurrent": 700, "DebtCurrent": 150, "LongTermDebt": 800}, 850, "D2"),
    ({"LongTermDebt": 800, "ShortTermBorrowings": 50, "CommercialPaper": 30}, 850, "D3-stb"),
    ({"LongTermDebt": 800, "CommercialPaper": 30}, 830, "D3-cp"),
    ({"LongTermDebt": 800}, 800, "D3-st0"),
    ({"LongTermDebtNoncurrent": 700, "LongTermDebtCurrent": 100, "CommercialPaper": 50}, 850, "D4"),
    ({"LongTermDebtNoncurrent": 700}, 700, "D4-bare"),
    ({"LongTermDebtAndCapitalLeaseObligations": 600,
      "LongTermDebtAndCapitalLeaseObligationsCurrent": 40}, 640, "D5"),
    ({"ShortTermBorrowings": 50}, None, "absent"),
]


@pytest.mark.parametrize(("concepts", "expected"), [g[:2] for g in G12], ids=[g[2] for g in G12])
def test_g12_total_debt(concepts, expected):
    it = bs_items(concepts)
    if expected is None:
        assert TOTAL_DEBT not in it
    else:
        assert it[TOTAL_DEBT] == approx(expected)


def test_total_debt_d5_with_short_term_and_no_current():
    it = bs_items({"LongTermDebtAndCapitalLeaseObligations": 600, "CommercialPaper": 25})
    assert it[TOTAL_DEBT] == approx(625)


def test_total_debt_d2_ignores_short_term():
    # DebtCurrent already holds short-term borrowings.
    it = bs_items({"LongTermDebtNoncurrent": 700, "DebtCurrent": 150, "ShortTermBorrowings": 50})
    assert it[TOTAL_DEBT] == approx(850)


def test_total_debt_uses_balance_sheet_date_not_comparative():
    it = bs_items({"LongTermDebt": 800},
                  extra={"LongTermDebt": [inst(999, end="2025-12-31")]})  # comparative column
    assert it[TOTAL_DEBT] == approx(800)


def test_total_debt_fact_fields():
    facts = sec_edgar.parse_companyfacts("T", bs_payload({"LongTermDebt": 800}))
    (f,) = [x for x in facts if x.item == TOTAL_DEBT]
    assert (f.ticker, f.period_end, f.filed_date) == ("T", D(BS_END), D("2026-11-01"))
    assert f.source == "sec_edgar" and f.filed_estimated is False


def test_balance_sheet_items_per_accession():
    # Two filings: each carries its own balance-sheet date and values.
    p = bs_payload({"Assets": 5000})
    us = p["facts"]["us-gaap"]
    us["StockholdersEquity"]["units"]["USD"].append(inst(1100, "2026-06-30", "A0", "2026-08-01"))
    us["Assets"]["units"]["USD"].append(inst(4800, "2026-06-30", "A0", "2026-08-01"))
    facts = sec_edgar.parse_companyfacts("T", p)
    got = {(f.period_end, f.filed_date): f.value for f in facts if f.item == TOTAL_ASSETS}
    assert got == {(D("2026-06-30"), D("2026-08-01")): approx(4800),
                   (D(BS_END), D("2026-11-01")): approx(5000)}


CASH1, CASH2, CASH3 = ("CashAndCashEquivalentsAtCarryingValue",
                       "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash")


@pytest.mark.parametrize(("concepts", "expected"), [
    ({CASH1: 70, CASH2: 75}, 70),
    ({CASH2: 75}, 75),
    ({CASH3: 60}, 60),
    ({CASH2: 75, CASH3: 60}, 75),
    ({CASH1: 70, CASH3: 60}, 70),
], ids=["1-over-2", "2-only", "3-only", "2-over-3", "1-over-3"])
def test_g13_cash_chain(concepts, expected):
    assert bs_items(concepts)[CASH] == approx(expected)


def test_g13_total_assets():
    assert bs_items({"Assets": 5000})[TOTAL_ASSETS] == approx(5000)


def test_g13_missing_balance_sheet_items_absent_not_zero():
    it = bs_items({})
    for item in (TOTAL_ASSETS, TOTAL_DEBT, CASH):
        assert item not in it


# ---------------------------------------------------------------------------
# Yahoo flows (parse_quarterly_flows) and balance sheet: G11, G14
# ---------------------------------------------------------------------------


def ts_point(as_of, value, period="3M", ccy="CAD"):
    return {"asOfDate": as_of, "periodType": period, "currencyCode": ccy,
            "reportedValue": {"raw": value}}


def yts(types: dict[str, list[tuple[str, float]]], fetched=None, ccy="CAD") -> dict:
    """Fundamentals-timeseries payload; types: {type: [(asOfDate, value)]}."""
    res = []
    for t, pts in types.items():
        period = "12M" if t.startswith("annual") else "3M"
        res.append({"meta": {"type": [t]}, t: [ts_point(a, v, period, ccy) for a, v in pts]})
    p = {"timeseries": {"result": res, "error": None}}
    if fetched is not None:
        p["_fetched"] = fetched
    return p


ANNUAL = {"annualNetIncomeCommonStockholders": [("2025-12-31", 999.0)]}

G14 = yts({
    **ANNUAL,
    "quarterlyOperatingCashFlow": [("2025-12-31", 210.0), ("2026-09-30", 190.0)],
    "quarterlyTotalRevenue": [("2026-09-30", 1200.0)],
    "quarterlyTotalAssets": [("2026-09-30", 9000.0)],
    "quarterlyTotalDebt": [("2026-09-30", 2500.0)],
    "quarterlyCashAndCashEquivalents": [("2026-09-30", 300.0)],
}, fetched="2026-11-05")


def test_g14_yahoo_flows():
    facts = yahoo.parse_quarterly_flows("X.TO", G14)
    assert rows(facts, CFO_Q) == [
        (D("2025-12-31"), D("2026-03-31"), approx(210)),  # +90, fiscal year end
        (D("2026-09-30"), D("2026-11-05"), approx(190)),  # +45 = 2026-11-14, capped at _fetched
    ]
    assert rows(facts, REVENUE_Q) == [(D("2026-09-30"), D("2026-11-05"), approx(1200))]
    for f in facts:
        assert f.unit == "CAD"
        assert f.filed_estimated is True
        assert f.source == "yahoo"
        assert f.ticker == "X.TO"
    assert {f.item for f in facts} == {CFO_Q, REVENUE_Q}  # balance-sheet types are not flows


def test_g14_yahoo_balance_sheet_not_capped():
    # SPEC-AMBIGUITY: G14 has no equity types; the new items are expected without them.
    bs, ccy = yahoo.timeseries_to_bs(G14)
    facts = yahoo.parse_balance_sheet("X.TO", bs, ccy)
    got = {f.item: f for f in facts if f.item in (TOTAL_ASSETS, TOTAL_DEBT, CASH)}
    assert {k: f.value for k, f in got.items()} == {
        TOTAL_ASSETS: approx(9000), TOTAL_DEBT: approx(2500), CASH: approx(300)}
    for f in got.values():
        assert f.period_end == D("2026-09-30")
        assert f.filed_date == D("2026-11-14")
        assert f.unit == "CAD"
        assert f.filed_estimated is True


def test_yahoo_balance_sheet_missing_item_absent():
    p = yts({"quarterlyTotalAssets": [("2026-09-30", 9000.0)]}, ccy="USD")
    bs, ccy = yahoo.timeseries_to_bs(p)
    items = {f.item for f in yahoo.parse_balance_sheet("X", bs, ccy)}
    assert TOTAL_ASSETS in items
    assert TOTAL_DEBT not in items and CASH not in items


YAHOO_TYPES = [
    ("quarterlyOperatingCashFlow", CFO_Q, 190.0, 190.0),
    ("quarterlyCapitalExpenditure", CAPEX_Q, -30.0, 30.0),
    ("quarterlyTotalRevenue", REVENUE_Q, 1200.0, 1200.0),
    ("quarterlyGrossProfit", GP_Q, 400.0, 400.0),
    ("quarterlyOperatingIncome", EBIT_Q, 150.0, 150.0),
]


@pytest.mark.parametrize(("ytype", "item", "reported", "stored"), YAHOO_TYPES,
                         ids=[t[1] for t in YAHOO_TYPES])
def test_yahoo_flow_type_mapping(ytype, item, reported, stored):
    facts = yahoo.parse_quarterly_flows("X", yts({**ANNUAL, ytype: [("2026-09-30", reported)]}))
    assert rows(facts, item) == [(D("2026-09-30"), D("2026-11-14"), approx(stored))]


@pytest.mark.parametrize(("reported", "stored"), [(-30.0, 30.0), (5.0, -5.0)])
def test_g11_yahoo_capex_sign_flipped(reported, stored):
    p = yts({**ANNUAL, "quarterlyCapitalExpenditure": [("2026-09-30", reported)]})
    assert values(yahoo.parse_quarterly_flows("X", p), CAPEX_Q) == {D("2026-09-30"): approx(stored)}


def test_yahoo_flows_exclude_ni_common_q():
    # SPEC-AMBIGUITY: §5.1 says Yahoo parse_quarterly_flows works "likewise" to EDGAR's (all flow
    # items except ni_common_q); read as: no ni_common_q facts from parse_quarterly_flows.
    p = yts({**ANNUAL, "quarterlyNetIncomeCommonStockholders": [("2026-09-30", 50.0)],
             "quarterlyOperatingCashFlow": [("2026-09-30", 70.0)]})
    assert {f.item for f in yahoo.parse_quarterly_flows("X", p)} == {CFO_Q}


def test_yahoo_flows_skip_points_without_raw():
    p = yts({**ANNUAL, "quarterlyOperatingCashFlow": [("2025-12-31", 210.0)]})
    pts = p["timeseries"]["result"][1]["quarterlyOperatingCashFlow"]
    pts.append({"asOfDate": "2026-03-31", "periodType": "3M", "currencyCode": "CAD",
                "reportedValue": {}})
    pts.append({"asOfDate": "2026-06-30", "periodType": "3M", "currencyCode": "CAD"})
    facts = yahoo.parse_quarterly_flows("X", p)
    assert [f.period_end for f in facts] == [D("2025-12-31")]


def test_yahoo_flows_unit_is_currency_code():
    p = yts({**ANNUAL, "quarterlyOperatingCashFlow": [("2026-09-30", 70.0)]}, ccy="USD")
    assert [f.unit for f in yahoo.parse_quarterly_flows("X", p)] == ["USD"]


def test_yahoo_flows_without_fetched_plain_estimate():
    ocf = [("2025-12-31", 210.0), ("2026-09-30", 190.0)]
    p = yts({**ANNUAL, "quarterlyOperatingCashFlow": ocf})
    assert {f.period_end: f.filed_date for f in yahoo.parse_quarterly_flows("X", p)} == {
        D("2025-12-31"): D("2026-03-31"), D("2026-09-30"): D("2026-11-14")}


# ---------------------------------------------------------------------------
# §5.3 engine.flow_ttm: per-source TTM, latest period_end wins, EDGAR on a tie
# ---------------------------------------------------------------------------

EDGAR_Q = ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
LATER_Q = ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]


def _con(rows_):
    con = db.connect(":memory:")
    for r in rows_:
        con.execute("INSERT INTO fundamentals (ticker, period_end, filed_date, item, value, unit, "
                    "source, filed_estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(r))
    return con


def _q(ends, vals, src, item=CFO_Q, unit="USD", filed=None, ticker="T"):
    est = src == "yahoo"
    return [(ticker, D(e), filed or date.fromordinal(D(e).toordinal() + 40), item, float(v), unit,
             src, est) for e, v in zip(ends, vals, strict=True)]


def test_flow_ttm_latest_period_end_wins():
    con = _con(_q(EDGAR_Q, [1, 2, 3, 4], "sec_edgar")
               + _q(LATER_Q, [10, 20, 30, 40], "yahoo", unit="CAD")
               + _q(LATER_Q, [100, 100, 100, 100], "sec_edgar", item=REVENUE_Q))  # other item
    res, src, ccy = engine.flow_ttm(con, "T", CFO_Q, D("2026-12-31"))
    assert isinstance(res, ttm.TTMResult)
    assert res.value == approx(100)
    assert res.period_end == D("2026-09-30")
    assert (src, ccy) == ("yahoo", "CAD")


def test_flow_ttm_edgar_wins_tie():
    con = _con(_q(LATER_Q, [1, 2, 3, 4], "sec_edgar")
               + _q(LATER_Q, [10, 20, 30, 40], "yahoo", unit="CAD"))
    res, src, ccy = engine.flow_ttm(con, "T", CFO_Q, D("2026-12-31"))
    assert res.value == approx(10)
    assert (src, ccy) == ("sec_edgar", "USD")


def test_flow_ttm_ignores_fact_filed_after_as_of():
    late = _q(["2026-09-30"], [4], "sec_edgar", filed=D("2026-11-20"))
    con = _con(_q(EDGAR_Q, [1, 2, 3, 4], "sec_edgar") + late
               + _q(LATER_Q[:3], [10, 20, 30], "yahoo", unit="CAD")
               + _q(["2026-09-30"], [40], "yahoo", unit="CAD", filed=D("2026-11-05")))
    res, src, _ = engine.flow_ttm(con, "T", CFO_Q, D("2026-11-10"))
    # EDGAR's 2026-09-30 quarter is not yet public: EDGAR TTM ends 2026-06-30, Yahoo's ends later.
    assert (res.value, res.period_end, src) == (approx(100), D("2026-09-30"), "yahoo")
    res, src, _ = engine.flow_ttm(con, "T", CFO_Q, D("2026-11-20"))
    assert (res.value, res.period_end, src) == (approx(2 + 3 + 4 + 4), D("2026-09-30"), "sec_edgar")


def test_flow_ttm_no_data_is_no_ttm():
    con = _con(_q(EDGAR_Q[:3], [1, 2, 3], "sec_edgar"))
    res, _, _ = engine.flow_ttm(con, "T", CFO_Q, D("2026-12-31"))
    assert res.value is None
    assert "NO_TTM" in set(res.flags)
