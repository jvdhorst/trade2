"""Spec tests for data batch 3 (docs/models/ttm.md v0.3.0 §6, goldens G15-G18).

Written from the spec only (not from the implementation). EDGAR payloads are synthetic
companyfacts responses; Yahoo payloads are synthetic fundamentals-timeseries responses; the
engine tests use an in-memory duckdb store. Modules are imported whole and new names looked up
per test, so missing code fails only the tests that need it.
"""

from __future__ import annotations

from datetime import date

import pytest

from valuation import engine
from valuation.providers import base, sec_edgar, yahoo
from valuation.store import db

REL = 1e-9
CA, CL, RE = "current_assets", "current_liabilities", "retained_earnings"
NEW_ITEMS = (CA, CL, RE)
TOTAL_ASSETS, TOTAL_EQUITY, SHARES = "total_assets", "total_equity", "shares_outstanding"
EDGAR_CONCEPTS = {CA: "AssetsCurrent", CL: "LiabilitiesCurrent",
                  RE: "RetainedEarningsAccumulatedDeficit"}
YAHOO_TYPES = {CA: ("quarterlyCurrentAssets", "Current Assets"),
               CL: ("quarterlyCurrentLiabilities", "Current Liabilities"),
               RE: ("quarterlyRetainedEarnings", "Retained Earnings")}


def approx(x):
    return pytest.approx(x, rel=REL)


def D(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Item-name constants, Yahoo types, EDGAR trim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "value"), [
    ("CURRENT_ASSETS", CA), ("CURRENT_LIABILITIES", CL), ("RETAINED_EARNINGS", RE)])
def test_item_name_constants(name, value):
    assert getattr(base, name) == value


@pytest.mark.parametrize("item", NEW_ITEMS)
def test_new_items_in_items(item):
    assert item in base.ITEMS


def test_existing_items_kept_in_items():
    for it in (TOTAL_EQUITY, SHARES, TOTAL_ASSETS, "total_debt", "cash"):
        assert it in base.ITEMS


@pytest.mark.parametrize("item", NEW_ITEMS)
def test_yahoo_type_in_ts_types(item):
    ytype, line = YAHOO_TYPES[item]
    assert ytype in yahoo.TS_TYPES
    # SPEC-AMBIGUITY: §6 gives the line-item name in parentheses; read as the TS_TYPES value.
    assert yahoo.TS_TYPES[ytype] == line


@pytest.mark.parametrize("item", NEW_ITEMS)
def test_trim_companyfacts_keeps_concept(item):
    concept = EDGAR_CONCEPTS[item]
    raw = {"units": {"USD": [inst(123, end="2026-09-30")]}}
    p = {"cik": 1, "entityName": "Test Co", "facts": {"us-gaap": {
        concept: raw, "SomeUnusedConcept": {"units": {"USD": [inst(1)]}}}, "dei": {}}}
    out = sec_edgar.trim_companyfacts(p)
    assert out["facts"]["us-gaap"][concept] == raw
    assert "SomeUnusedConcept" not in out["facts"]["us-gaap"]


# ---------------------------------------------------------------------------
# EDGAR (parse_companyfacts): G15, G16
# ---------------------------------------------------------------------------

BS_END = "2026-09-30"
PRIOR_YE = "2025-12-31"
FILED = "2026-11-01"


def inst(val, end=BS_END, accn="A", filed=FILED, form="10-Q"):
    return {"val": val, "end": end, "accn": accn, "filed": filed, "form": form}


def edgar_payload(concepts: dict[str, list[dict]]) -> dict:
    return {"cik": 1, "entityName": "Test Co", "facts": {
        "us-gaap": {c: {"units": {"USD": fs}} for c, fs in concepts.items()},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            inst(100, end="2026-10-20")]}}},
    }}


def g15_concepts(**override) -> dict[str, list[dict]]:
    c = {
        "StockholdersEquity": [inst(800), inst(760, end=PRIOR_YE)],
        "Assets": [inst(2000)],
        "AssetsCurrent": [inst(600), inst(580, end=PRIOR_YE)],
        "LiabilitiesCurrent": [inst(500)],
        "RetainedEarningsAccumulatedDeficit": [inst(400)],
    }
    for k, v in override.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    return c


def parse_edgar(concepts):
    return sec_edgar.parse_companyfacts("T", edgar_payload(concepts))


def at_bs(facts):
    """{item: Fact} at the filing's balance-sheet date (one filing in these payloads)."""
    return {f.item: f for f in facts if f.period_end == D(BS_END)}


def test_g15_values():
    got = {k: f.value for k, f in at_bs(parse_edgar(g15_concepts())).items()}
    assert got[CA] == approx(600)  # not the 580 comparative
    assert got[CL] == approx(500)
    assert got[RE] == approx(400)
    assert got[TOTAL_ASSETS] == approx(2000)
    assert got[TOTAL_EQUITY] == approx(800)


def test_g15_fact_fields():
    facts = parse_edgar(g15_concepts())
    new = [f for f in facts if f.item in NEW_ITEMS]
    assert {f.item for f in new} == set(NEW_ITEMS)
    assert len(new) == 3  # one fact each: the comparative date yields no extra fact
    for f in new:
        assert f.ticker == "T"
        assert f.period_end == D(BS_END)
        assert f.filed_date == D(FILED)
        assert f.source == "sec_edgar"
        assert f.filed_estimated is False
        assert f.unit == "USD"


def test_g15_comparative_date_ignored():
    facts = parse_edgar(g15_concepts())
    assert all(f.value != approx(580) for f in facts if f.item == CA)
    assert not [f for f in facts if f.item in NEW_ITEMS and f.period_end == D(PRIOR_YE)]


def test_g16_negative_retained_earnings_and_missing_tags():
    facts = parse_edgar(g15_concepts(
        RetainedEarningsAccumulatedDeficit=[inst(-1250)], AssetsCurrent=None,
        LiabilitiesCurrent=None))
    items = {f.item for f in facts}
    assert CA not in items and CL not in items  # absent, not 0
    got = at_bs(facts)
    assert got[RE].value == approx(-1250)
    assert got[TOTAL_ASSETS].value == approx(2000)
    assert got[TOTAL_EQUITY].value == approx(800)


def test_g16_value_only_at_other_date_absent():
    facts = parse_edgar(g15_concepts(
        RetainedEarningsAccumulatedDeficit=[inst(-1250)], AssetsCurrent=None,
        LiabilitiesCurrent=[inst(500, end=PRIOR_YE)]))
    assert CL not in {f.item for f in facts}
    assert at_bs(facts)[RE].value == approx(-1250)


@pytest.mark.parametrize("missing", NEW_ITEMS)
def test_one_missing_item_others_still_emitted(missing):
    facts = parse_edgar(g15_concepts(**{EDGAR_CONCEPTS[missing]: None}))
    items = {f.item for f in facts}
    assert missing not in items
    assert set(NEW_ITEMS) - {missing} <= items
    assert TOTAL_ASSETS in items


def test_no_derivation_from_assets_minus_noncurrent():
    facts = parse_edgar(g15_concepts(AssetsCurrent=None, AssetsNoncurrent=[inst(1400)]))
    assert CA not in {f.item for f in facts}


def test_negative_current_liabilities_stored_as_is():
    facts = parse_edgar(g15_concepts(LiabilitiesCurrent=[inst(-5)]))
    assert at_bs(facts)[CL].value == approx(-5)


def test_new_items_per_accession():
    # Two filings: each carries its own balance-sheet date and values.
    c = g15_concepts()
    c["StockholdersEquity"].append(inst(780, "2026-06-30", "A0", "2026-08-01"))
    c["AssetsCurrent"].append(inst(550, "2026-06-30", "A0", "2026-08-01"))
    c["RetainedEarningsAccumulatedDeficit"].append(inst(350, "2026-06-30", "A0", "2026-08-01"))
    facts = parse_edgar(c)
    got = {(f.item, f.period_end, f.filed_date): f.value for f in facts if f.item in NEW_ITEMS}
    assert got == {
        (CA, D("2026-06-30"), D("2026-08-01")): approx(550),
        (RE, D("2026-06-30"), D("2026-08-01")): approx(350),
        (CA, D(BS_END), D(FILED)): approx(600),
        (CL, D(BS_END), D(FILED)): approx(500),
        (RE, D(BS_END), D(FILED)): approx(400),
    }


# ---------------------------------------------------------------------------
# Yahoo (timeseries_to_bs + parse_balance_sheet): G17
# ---------------------------------------------------------------------------


def ts_point(as_of, value, ccy="CAD"):
    return {"asOfDate": as_of, "periodType": "3M", "currencyCode": ccy,
            "reportedValue": {"raw": value}}


def yts(types: dict[str, list[tuple[str, float]]], fetched=None, ccy="CAD") -> dict:
    res = [{"meta": {"type": [t]}, t: [ts_point(a, v, ccy) for a, v in pts]}
           for t, pts in types.items()]
    p = {"timeseries": {"result": res, "error": None}}
    if fetched is not None:
        p["_fetched"] = fetched
    return p


def g17_payload() -> dict:
    q3, q2 = "2026-09-30", "2026-06-30"
    p = yts({
        "quarterlyTotalEquityGrossMinorityInterest": [(q3, 800.0), (q2, 800.0)],
        "quarterlyOrdinarySharesNumber": [(q3, 100.0), (q2, 100.0)],
        "quarterlyTotalAssets": [(q3, 2000.0), (q2, 2000.0)],
        "quarterlyCurrentAssets": [(q3, 450.0)],
        "quarterlyCurrentLiabilities": [(q3, 600.0), (q2, 600.0)],
        "quarterlyRetainedEarnings": [(q3, -100.0)],
    }, fetched="2026-11-05")
    for r in p["timeseries"]["result"]:
        if "quarterlyCurrentAssets" in r:
            r["quarterlyCurrentAssets"].append(
                {"asOfDate": q2, "periodType": "3M", "currencyCode": "CAD"})  # no reportedValue
    return p


def g17_facts():
    bs, ccy = yahoo.timeseries_to_bs(g17_payload())
    return yahoo.parse_balance_sheet("X.TO", bs, ccy)


def test_g17_q3_values_and_fields():
    got = {f.item: f for f in g17_facts() if f.period_end == D("2026-09-30")}
    assert got[CA].value == approx(450)
    assert got[CL].value == approx(600)
    assert got[RE].value == approx(-100)
    for it in NEW_ITEMS:
        f = got[it]
        assert f.filed_date == D("2026-11-14")  # +45, not capped at _fetched
        assert f.unit == "CAD"
        assert f.filed_estimated is True
        assert f.source == "yahoo"
        assert f.ticker == "X.TO"


def test_g17_q2_missing_points_absent():
    got = {f.item: f.value for f in g17_facts() if f.period_end == D("2026-06-30")}
    assert got[CL] == approx(600)
    assert RE not in got
    assert CA not in got  # point without reportedValue skipped
    assert got[TOTAL_ASSETS] == approx(2000)


@pytest.mark.parametrize("item", NEW_ITEMS)
def test_yahoo_type_maps_to_item(item):
    ytype, _ = YAHOO_TYPES[item]
    bs, ccy = yahoo.timeseries_to_bs(yts({ytype: [("2026-09-30", 77.0)]}, ccy="USD"))
    facts = [f for f in yahoo.parse_balance_sheet("X", bs, ccy) if f.item in NEW_ITEMS]
    assert [(f.item, f.period_end, f.filed_date, f.value, f.unit) for f in facts] == [
        (item, D("2026-09-30"), D("2026-11-14"), approx(77.0), "USD")]


# ---------------------------------------------------------------------------
# Engine point in time (build_inputs): G18
# ---------------------------------------------------------------------------


def _store(rows_, ticker="T"):
    con = db.connect(":memory:")
    con.execute("INSERT INTO securities (ticker, currency, sector, industry, share_classes, "
                "share_conversion) VALUES (?, 'USD', 'Industrials', 'Machinery', 1, 1.0)", [ticker])
    con.execute("INSERT INTO prices_daily (ticker, date, close, adj_close, volume, source) "
                "VALUES (?, ?, 50.0, 50.0, 1000.0, 'yahoo')", [ticker, D("2026-07-01")])
    for r in rows_:
        con.execute("INSERT INTO fundamentals (ticker, period_end, filed_date, item, value, unit, "
                    "source, filed_estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(r))
    return con


def _filing(pe, filed, src, items: dict[str, float], ticker="T", equity=1000.0, shares=100.0):
    est = src == "yahoo"
    all_items = {}
    if equity is not None:
        all_items[TOTAL_EQUITY] = equity
    if shares is not None:
        all_items[SHARES] = shares
    all_items.update(items)
    return [(ticker, D(pe), D(filed), it, float(v), "shares" if it == SHARES else "USD", src, est)
            for it, v in all_items.items()]


E1 = _filing("2026-06-30", "2026-08-01", "sec_edgar", {CA: 500, CL: 300, RE: 200})
E2 = _filing("2026-09-30", "2026-11-01", "sec_edgar", {CA: 600, CL: 500, RE: 400})
Y3 = _filing("2026-09-30", "2026-11-14", "yahoo", {CA: 610, CL: 510, RE: 410}, equity=1010.0)


def _meta(con, as_of, ticker="T"):
    inputs, skipped = engine.build_inputs(con, D(as_of), {ticker})
    assert ticker not in skipped, skipped
    ((_, meta),) = inputs
    return meta


def _new(meta):
    return tuple(meta[k] for k in NEW_ITEMS)


def test_g18_earlier_filing_before_later_is_public():
    m = _meta(_store(E1 + E2), "2026-10-15")
    assert _new(m) == (approx(500), approx(300), approx(200))
    assert m["period_end"] == "2026-06-30"


def test_g18_later_filing_once_public():
    m = _meta(_store(E1 + E2), "2026-11-09")
    assert _new(m) == (approx(600), approx(500), approx(400))
    assert m["period_end"] == "2026-09-30"


def test_g18_yahoo_invisible_before_estimated_filed_date():
    # Without EDGAR's Q3, Yahoo's Q3 (filed 2026-11-14) is not public on 2026-11-09.
    m = _meta(_store(E1 + Y3), "2026-11-09")
    assert (m["source"], m["period_end"]) == ("sec_edgar", "2026-06-30")
    assert _new(m) == (approx(500), approx(300), approx(200))
    m = _meta(_store(E1 + Y3), "2026-11-20")
    assert (m["source"], m["period_end"]) == ("yahoo", "2026-09-30")
    assert _new(m) == (approx(610), approx(510), approx(410))


def test_g18_yahoo_not_visible_with_edgar():
    m = _meta(_store(E1 + E2 + Y3), "2026-11-09")
    assert m["source"] == "sec_edgar"
    assert _new(m) == (approx(600), approx(500), approx(400))


def test_g18_edgar_wins_period_end_tie():
    m = _meta(_store(E1 + E2 + Y3), "2026-11-20")
    assert (m["source"], m["period_end"]) == ("sec_edgar", "2026-09-30")
    assert _new(m) == (approx(600), approx(500), approx(400))
    assert m[TOTAL_EQUITY] == approx(1000)


def test_g18_missing_re_is_none_never_borrowed():
    e2_no_re = _filing("2026-09-30", "2026-11-01", "sec_edgar", {CA: 600, CL: 500})
    con = _store(E1 + e2_no_re + Y3)
    for as_of in ("2026-11-09", "2026-11-20"):  # before / after Yahoo's Q3 is public
        m = _meta(con, as_of)
        assert (m["source"], m["period_end"]) == ("sec_edgar", "2026-09-30")
        assert m[RE] is None  # not 200 (earlier filing) nor 410 (Yahoo)
        assert (m[CA], m[CL]) == (approx(600), approx(500))


def test_g18_selected_filing_without_new_items_still_selected():
    # Selection needs equity + shares only: adding items never changes the filing.
    e2_bare = _filing("2026-09-30", "2026-11-01", "sec_edgar", {})
    m = _meta(_store(E1 + e2_bare), "2026-11-09")
    assert m["period_end"] == "2026-09-30"
    assert _new(m) == (None, None, None)


def test_g18_new_items_without_equity_do_not_make_a_filing():
    e2_no_eq = _filing("2026-09-30", "2026-11-01", "sec_edgar", {CA: 600, CL: 500, RE: 400},
                       equity=None)
    m = _meta(_store(E1 + e2_no_eq), "2026-11-09")
    assert m["period_end"] == "2026-06-30"
    assert _new(m) == (approx(500), approx(300), approx(200))


def test_g18_end_to_end_from_parsed_edgar():
    # parse_companyfacts -> fundamentals -> build_inputs carries the G15 values.
    facts = parse_edgar(g15_concepts())
    rows_ = [(f.ticker, f.period_end, f.filed_date, f.item, f.value, f.unit, f.source,
              f.filed_estimated) for f in facts]
    m = _meta(_store(rows_), "2026-11-09")
    assert _new(m) == (approx(600), approx(500), approx(400))
    assert m[TOTAL_ASSETS] == approx(2000)
